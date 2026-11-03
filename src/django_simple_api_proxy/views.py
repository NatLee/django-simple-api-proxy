"""Forward the requests under one route of a Django site to another API server.

``APIProxy`` takes a request for ``/<PROXY_ROUTE_PATH>/<rest>``, sends it to
``<TARGET_API_URL>/<PROXY_TARGET_PATH>/<rest>`` and answers with the JSON the API
server returns. Subclass it to require a login or to change what is sent.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.http import JsonResponse
from loguru import logger
from rest_framework.request import Request
from rest_framework.views import APIView

#: Sent as ``username`` when the request has no authenticated user.
ANONYMOUS_USERNAME = "#anonymous"


def _field_names(body: Any) -> str:
    """Describe a decoded JSON body for the log without any of its values."""
    if isinstance(body, dict):
        return str(list(body))
    return "(not a JSON object)"


class APIProxy(APIView):
    """API proxy"""
    # Django REST framework shows the docstring above in OPTIONS responses and on
    # its browsable pages, so it stays as it has always been.

    # Anyone can use the stock proxy. Subclasses set these to require a login.
    authentication_classes = []
    permission_classes = []

    # Read once, when this module is imported. Subclasses can set their own.
    target_api_url: str = settings.TARGET_API_URL
    proxy_route_path: str = settings.PROXY_ROUTE_PATH
    proxy_target_path: str = settings.PROXY_TARGET_PATH

    # -- HTTP methods ---------------------------------------------------------
    # Django answers HEAD with get(); Django REST framework answers OPTIONS itself.

    def get(self, request: Request, *args: Any, **kwargs: Any) -> JsonResponse:
        """Forward a GET request; the query string goes along as query parameters."""
        return self._forward(request, "GET")

    def post(self, request: Request, *args: Any, **kwargs: Any) -> JsonResponse:
        """Forward a POST request with its JSON body."""
        return self._forward(request, "POST")

    def put(self, request: Request, *args: Any, **kwargs: Any) -> JsonResponse:
        """Forward a PUT request with its JSON body."""
        return self._forward(request, "PUT")

    def patch(self, request: Request, *args: Any, **kwargs: Any) -> JsonResponse:
        """Forward a PATCH request with its JSON body."""
        return self._forward(request, "PATCH")

    def delete(self, request: Request, *args: Any, **kwargs: Any) -> JsonResponse:
        """Forward a DELETE request with its JSON body."""
        return self._forward(request, "DELETE")

    # -- Steps that subclasses can override -----------------------------------

    def parse_path(self, request: Request) -> str:
        """Return the path on the API server for ``request``.

        The first match of ``proxy_route_path`` (a regular expression) is replaced
        with ``proxy_target_path`` and a trailing slash is dropped, so
        ``/my_test_route/items/7/`` becomes ``/get/items/7``.
        """
        path = urlparse(request.get_full_path()).path.rstrip("/")
        return re.sub(self.proxy_route_path, self.proxy_target_path, path, count=1)

    def get_proxy_path(self, request: Request) -> str:
        """Return the API server URL for ``request``, without the query string."""
        url = f"{self.target_api_url}{self.parse_path(request)}"
        logger.debug("Upstream URL: {}", url)
        return url

    def update_payload(self, request: Request, params: dict[str, Any]) -> dict[str, Any]:
        """Set ``username`` in ``params`` and return ``params``.

        The value is the name of the user Django REST framework authenticated, or
        ``#anonymous``. A ``username`` sent by the client is replaced.
        """
        # A custom user model without `username` ends in {"status": "error"}.
        username = request.user.username or ANONYMOUS_USERNAME  # type: ignore[union-attr]
        logger.debug("Username: {}", username)
        params.update({"username": username})
        return params

    def preprocess_request_body(self, request: Request) -> tuple[str, dict[str, Any]]:
        """Return the API server URL and the JSON body to send for ``request``.

        An empty body counts as ``{}``; ``username`` is added by ``update_payload``.
        """
        params = json.loads(request.body) if request.body else {}
        # Field names only: the values may be passwords or tokens.
        logger.debug("JSON body fields: {}", _field_names(params))
        path = self.get_proxy_path(request)
        params = self.update_payload(request, params)
        return path, params

    def send_request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        data: Any = None,
        json: Any = None,
        timeout: float | tuple[float, float] = 180,
        verify: bool | str = True,
    ) -> requests.Response:
        """Send the request to the API server and return its reply."""
        return requests.request(
            method=method,
            url=url,
            params=params,
            data=data,
            json=json,
            timeout=timeout,
            verify=verify,
        )

    # Django REST framework stores the finished reply in `self.response` after the
    # handler returns, which only hides this method once it is no longer needed.
    def response(self, resp: Any) -> JsonResponse:  # type: ignore[override]
        """Build the reply for the client from the API server's decoded JSON."""
        return JsonResponse(resp, safe=False, json_dumps_params={"ensure_ascii": False})

    # -- Internals ------------------------------------------------------------

    def _forward(self, request: Request, method: str) -> JsonResponse:
        """Send ``request`` to the API server as ``method`` and relay the JSON reply.

        If anything fails on the way (a body that is not a JSON object, an API
        server that cannot be reached, a reply that is not JSON), the error is
        logged and the client gets ``{"status": "error"}`` with HTTP status 200.
        """
        logger.debug("Proxy {} {}", method, request.path)
        try:
            reply = self._send_upstream(request, method).json()
        except Exception:  # Whatever went wrong, the client gets the same answer.
            logger.exception('Could not proxy {} {}, replying {{"status": "error"}}',
                             method, request.path)
            reply = {"status": "error"}
        return self.response(reply)

    def _send_upstream(self, request: Request, method: str) -> requests.Response:
        """GET sends the query string on; the other methods send the JSON body on."""
        if method == "GET":
            params = dict(request.GET)
            url = self.get_proxy_path(request)
            params = self.update_payload(request, params)
            return self.send_request(method, url, params=params)
        url, body = self.preprocess_request_body(request)
        return self.send_request(method, url, json=body)
