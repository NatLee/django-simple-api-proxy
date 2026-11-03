"""JSON mode: the proxy as it worked in 1.x, kept as a compatibility layer.

Why it is kept: 2.0 made pass-through mode the default, which changes what clients
get (the API server's status codes, any content type, no ``username``). With
``PROXY_PASS_THROUGH = False``, or ``pass_through = False`` on a subclass, a project
upgrades and keeps the 1.x answers until it is ready to switch.

How it fits in: ``JSONMode`` sits on top of ``PassThroughView``. In the few steps
where the two modes differ it checks ``pass_through`` and either does what 1.x did
or hands the step down to pass-through mode, which knows nothing about this module.
``APIProxy`` (views.py) is ``JSONMode`` under its public name.

When it can go: in a major release, once projects no longer set
``PROXY_PASS_THROUGH = False``. Removing it means deleting this module, the JSON-mode
checks in checks.py (W002-W005) and the 1.x tests in tests/test_api_proxy.py, and
basing ``APIProxy`` on ``PassThroughView``.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import requests
from django.http import HttpResponse, JsonResponse
from loguru import logger
from rest_framework.request import Request

from django_simple_api_proxy import conf
from django_simple_api_proxy.pass_through import (
    ERROR_REPLY,
    PassThroughView,
    _content_type,
    _failure_reason,
)

#: Sent as ``username`` when the request has no authenticated user.
ANONYMOUS_USERNAME = "#anonymous"

#: The methods only JSON mode calls. A subclass in pass-through mode that overrides
#: one of them gets warning W005, because the override does nothing there.
JSON_MODE_HOOKS = ("update_payload", "preprocess_request_body", "response")

_JSON_TYPE_NAMES = {list: "an array", str: "a string", int: "a number", float: "a number",
                    bool: "true or false", type(None): "null"}


class ProxyError(ValueError):
    """A request JSON mode cannot forward; the message says why, for the log."""


def _field_names(body: Any) -> str:
    """Describe a decoded JSON body for the log without any of its values."""
    if isinstance(body, dict):
        return str(list(body))
    return "(not a JSON object)"


class JSONMode(PassThroughView):
    """JSON in, JSON out, as in 1.x, when ``pass_through`` is off."""

    #: True (the default since 2.0) passes everything through; False brings back
    #: the JSON mode of 1.x.
    pass_through: bool = conf.pass_through_enabled()

    # -- Hooks only JSON mode calls (JSON_MODE_HOOKS) --------------------------

    def update_payload(self, request: Request, params: Any) -> dict[str, Any]:
        """Set ``username`` in ``params`` and return ``params``.

        The value is the name of the user Django REST framework authenticated, or
        ``#anonymous``. A ``username`` sent by the client is replaced.
        """
        if not isinstance(params, dict):
            kind = _JSON_TYPE_NAMES.get(type(params), type(params).__name__)
            raise ProxyError(f"the JSON body must be an object ({{...}}), not {kind}")
        user = request.user
        username = getattr(user, "username", None)
        if username is None and hasattr(user, "get_username"):
            username = user.get_username()  # a custom user model without `username`
        username = username or ANONYMOUS_USERNAME
        logger.debug("Username: {}", username)
        params.update({"username": username})
        return params

    def preprocess_request_body(self, request: Request) -> tuple[str, dict[str, Any]]:
        """Return the API server URL and the JSON body to send for ``request``.

        An empty body counts as ``{}``; ``username`` is added by ``update_payload``.
        """
        try:
            params = json.loads(request.body) if request.body else {}
        except ValueError as exc:
            raise ProxyError(
                f"the request body is not JSON (Content-Type: {request.content_type or 'none'}); "
                "JSON mode only forwards JSON, set PROXY_PASS_THROUGH = True to forward any body"
            ) from exc
        # Field names only: the values may be passwords or tokens.
        logger.debug("JSON body fields: {}", _field_names(params))
        path = self.get_proxy_path(request)
        params = self.update_payload(request, params)
        return path, params

    # Django REST framework stores the finished reply in `self.response` after the
    # handler returns, which only hides this method once it is no longer needed.
    def response(self, resp: Any) -> JsonResponse:  # type: ignore[override]
        """Build the reply for the client from the API server's decoded JSON."""
        return JsonResponse(resp, safe=False, json_dumps_params={"ensure_ascii": False})

    # -- Steps where the modes differ ------------------------------------------
    # Each one does what 1.x did when pass_through is off and hands the step to
    # pass-through mode otherwise. super(PassThroughView, self) is Django REST
    # framework's own behaviour, which 1.x used for these steps.

    def head(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        """JSON mode answers HEAD like GET, as Django does."""
        if self.pass_through:
            return super().head(request, *args, **kwargs)
        return self.get(request, *args, **kwargs)

    def options(  # type: ignore[override]
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        """JSON mode lets Django REST framework answer OPTIONS."""
        if self.pass_through:
            return super().options(request, *args, **kwargs)
        return super(PassThroughView, self).options(request, *args, **kwargs)

    def parse_path(self, request: Request) -> str:
        """JSON mode: the first match of ``proxy_route_path`` (a regular expression) is
        replaced with ``proxy_target_path`` and a trailing slash is dropped, so
        ``/my_test_route/items/7/`` becomes ``/get/items/7``.
        """
        if self.pass_through:
            return super().parse_path(request)
        path = urlparse(request.get_full_path()).path.rstrip("/")
        return re.sub(self.proxy_route_path, self.proxy_target_path, path, count=1)

    def get_proxy_path(self, request: Request) -> str:
        """JSON mode puts ``target_api_url`` and the path together as they are."""
        if self.pass_through:
            return super().get_proxy_path(request)
        url = f"{self.target_api_url}{self.parse_path(request)}"
        logger.debug("Upstream URL: {}", url)
        return url

    def perform_authentication(self, request: Request) -> None:
        if self.pass_through:
            return super().perform_authentication(request)
        return super(PassThroughView, self).perform_authentication(request)

    def perform_content_negotiation(self, request: Request, force: bool = False) -> Any:
        if self.pass_through:
            return super().perform_content_negotiation(request, force)
        return super(PassThroughView, self).perform_content_negotiation(request, force)

    def finalize_response(self, request: Request, response: Any, *args: Any, **kwargs: Any) -> Any:
        if self.pass_through:
            return super().finalize_response(request, response, *args, **kwargs)
        return super(PassThroughView, self).finalize_response(request, response, *args, **kwargs)

    def _forward(self, request: Request, method: str) -> HttpResponse:
        """JSON mode: if anything fails on the way (a body that is not a JSON object,
        an API server that cannot be reached, a reply that is not JSON), the error is
        logged and the client gets ``{"status": "error"}`` with HTTP 200.
        """
        if self.pass_through:
            return super()._forward(request, method)
        logger.debug("Proxy {} {}", method, request.path)
        try:
            reply = self._json_from(self._send_upstream(request, method))
        except Exception as exc:  # Whatever went wrong, the client gets the same answer.
            reason = str(exc) if isinstance(exc, ProxyError) else _failure_reason(exc)
            logger.exception('Could not proxy {} {}: {}. Replying {{"status": "error"}}',
                             method, request.path, reason)
            reply = ERROR_REPLY.copy()
        return self.response(reply)

    # -- Internals ------------------------------------------------------------

    def _send_upstream(self, request: Request, method: str) -> requests.Response:
        """GET sends the query string on; the other methods send the JSON body on."""
        if method == "GET":
            params = dict(request.GET)
            url = self.get_proxy_path(request)
            params = self.update_payload(request, params)
            return self.send_request(method, url, params=params)
        url, body = self.preprocess_request_body(request)
        return self.send_request(method, url, json=body)

    @staticmethod
    def _json_from(upstream: Any) -> Any:
        try:
            return upstream.json()
        except ValueError as exc:
            status = getattr(upstream, "status_code", "?")
            kind = _content_type(upstream) or "no Content-Type"
            raise ProxyError(
                f"the API server answered HTTP {status} ({kind}), which is not JSON; "
                "set PROXY_PASS_THROUGH = True to pass any reply on"
            ) from exc
