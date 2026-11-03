"""Forward the requests under one route of a Django site to another server.

``APIProxy`` takes a request for ``/<PROXY_ROUTE_PATH>/<rest>``, sends it to
``<TARGET_API_URL>/<PROXY_TARGET_PATH>/<rest>`` and answers with what that server
returns. It has two modes:

* JSON mode, the default: JSON in, JSON out. It adds ``username`` to what it sends
  and always answers with HTTP 200.
* Pass-through mode (``PROXY_PASS_THROUGH = True`` or ``pass_through = True`` on a
  subclass): the method, path, query string, headers and body go through as they
  are, and the status code, headers and body of the reply come back as they are.

Subclass it to require a login or to change what is sent.
"""
from __future__ import annotations

import json
import re
from http.cookies import SimpleCookie
from typing import Any, Mapping
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.utils.encoding import escape_uri_path
from loguru import logger
from rest_framework.request import Request
from rest_framework.views import APIView

from django_simple_api_proxy import conf
from django_simple_api_proxy.headers import (
    request_headers_to_forward,
    response_headers_to_relay,
)

conf.require_settings()

#: Sent as ``username`` when the request has no authenticated user.
ANONYMOUS_USERNAME = "#anonymous"

#: What the client gets when a request cannot be proxied (both modes).
ERROR_REPLY = {"status": "error"}

def _field_names(body: Any) -> str:
    """Describe a decoded JSON body for the log without any of its values."""
    if isinstance(body, dict):
        return str(list(body))
    return "(not a JSON object)"


def _failure_reason(exc: Exception) -> str:
    """One line for the log about why a request could not be proxied."""
    if isinstance(exc, requests.Timeout):
        return "the API server did not answer in time"
    if isinstance(exc, requests.ConnectionError):
        return "could not connect to the API server"
    if isinstance(exc, requests.RequestException):
        return f"the request to the API server failed ({type(exc).__name__})"
    return f"{type(exc).__name__}: {exc}"


def _content_type(response: Any) -> str | None:
    headers = getattr(response, "headers", None)
    return headers.get("Content-Type") if isinstance(headers, Mapping) else None


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
    #: Pass everything through instead of JSON in, JSON out. See the module docstring.
    pass_through: bool = conf.pass_through_enabled()

    # -- HTTP methods ---------------------------------------------------------
    # Django REST framework answers OPTIONS itself, except in pass-through mode.

    def get(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        """Forward a GET request; the query string goes along as query parameters."""
        return self._forward(request, "GET")

    def post(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        """Forward a POST request with its JSON body."""
        return self._forward(request, "POST")

    def put(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        """Forward a PUT request with its JSON body."""
        return self._forward(request, "PUT")

    def patch(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        """Forward a PATCH request with its JSON body."""
        return self._forward(request, "PATCH")

    def delete(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        """Forward a DELETE request with its JSON body."""
        return self._forward(request, "DELETE")

    def head(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        """Forward HEAD in pass-through mode; otherwise answer it like GET, as Django does."""
        if self.pass_through:
            return self._forward(request, "HEAD")
        return self.get(request, *args, **kwargs)

    # Django REST framework answers OPTIONS with a Response; pass-through mode relays the
    # API server's reply instead.
    def options(  # type: ignore[override]
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        """Forward OPTIONS in pass-through mode; otherwise Django REST framework answers."""
        if self.pass_through:
            return self._forward(request, "OPTIONS")
        return super().options(request, *args, **kwargs)

    # -- Steps that subclasses can override -----------------------------------

    def parse_path(self, request: Request) -> str:
        """Return the path on the API server for ``request``.

        JSON mode: the first match of ``proxy_route_path`` (a regular expression) is
        replaced with ``proxy_target_path`` and a trailing slash is dropped, so
        ``/my_test_route/items/7/`` becomes ``/get/items/7``.

        Pass-through mode: everything after the route is kept as it is, trailing
        slash included, so ``/my_test_route/items/7/`` becomes ``/get/items/7/``.
        Slashes around the two settings don't matter.
        """
        if self.pass_through:
            return self._map_path_exactly(request)
        path = urlparse(request.get_full_path()).path.rstrip("/")
        return re.sub(self.proxy_route_path, self.proxy_target_path, path, count=1)

    def get_proxy_path(self, request: Request) -> str:
        """Return the API server URL for ``request``, without the query string."""
        base = self.target_api_url.rstrip("/") if self.pass_through else self.target_api_url
        url = f"{base}{self.parse_path(request)}"
        logger.debug("Upstream URL: {}", url)
        return url

    def update_payload(self, request: Request, params: dict[str, Any]) -> dict[str, Any]:
        """Set ``username`` in ``params`` and return ``params`` (JSON mode).

        The value is the name of the user Django REST framework authenticated, or
        ``#anonymous``. A ``username`` sent by the client is replaced.
        """
        user = request.user
        username = getattr(user, "username", None)
        if username is None and hasattr(user, "get_username"):
            username = user.get_username()  # a custom user model without `username`
        username = username or ANONYMOUS_USERNAME
        logger.debug("Username: {}", username)
        params.update({"username": username})
        return params

    def preprocess_request_body(self, request: Request) -> tuple[str, dict[str, Any]]:
        """Return the API server URL and the JSON body to send for ``request`` (JSON mode).

        An empty body counts as ``{}``; ``username`` is added by ``update_payload``.
        """
        params = json.loads(request.body) if request.body else {}
        # Field names only: the values may be passwords or tokens.
        logger.debug("JSON body fields: {}", _field_names(params))
        path = self.get_proxy_path(request)
        params = self.update_payload(request, params)
        return path, params

    def upstream_headers(self, request: Request) -> dict[str, str]:
        """Return the headers to send to the API server (pass-through mode).

        These are the client's headers, cookies and ``Authorization`` included,
        without the hop-by-hop ones and those requests sets itself (``Host``,
        ``Content-Length``, ``Accept-Encoding``). Override it to add an API key or
        to keep the client's cookies on your site.
        """
        return request_headers_to_forward(request.headers, has_body=bool(request.body))

    def send_request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        data: Any = None,
        json: Any = None,
        timeout: float | tuple[float, float] = 180,
        verify: bool | str = True,
        headers: Mapping[str, str] | None = None,
    ) -> requests.Response:
        """Send the request to the API server and return its reply.

        In pass-through mode ``url`` carries the query string, ``data`` the raw
        body and ``headers`` the result of ``upstream_headers()``, so an override
        has to accept ``headers``.
        """
        kwargs: dict[str, Any] = dict(method=method, url=url, params=params, data=data,
                                      json=json, timeout=timeout, verify=verify)
        if headers is not None:  # JSON mode sends none, and calls requests as it always has
            kwargs["headers"] = dict(headers)
        return requests.request(**kwargs)

    # Django REST framework stores the finished reply in `self.response` after the
    # handler returns, which only hides this method once it is no longer needed.
    def response(self, resp: Any) -> JsonResponse:  # type: ignore[override]
        """Build the reply for the client from the API server's decoded JSON (JSON mode)."""
        return JsonResponse(resp, safe=False, json_dumps_params={"ensure_ascii": False})

    def relay_response(self, upstream: requests.Response) -> HttpResponse:
        """Build the reply for the client from the API server's reply (pass-through mode).

        The status code, headers, cookies and body come back as they are, except
        hop-by-hop headers and those that no longer fit after requests decoded the
        body (``Content-Encoding``, ``Content-Length``).
        """
        reply = HttpResponse(upstream.content, status=upstream.status_code,
                             reason=upstream.reason or None)
        del reply["Content-Type"]  # only the API server's, if it sent one
        head = self.request.method == "HEAD"
        for name, value in response_headers_to_relay(upstream.headers, head=head).items():
            reply[name] = value
        for cookie in _set_cookie_headers(upstream):
            _add_cookie(reply, cookie)
        return reply

    # -- Django REST framework hooks ------------------------------------------

    def perform_authentication(self, request: Request) -> None:
        if self.pass_through:
            # Read the body before authentication can: SessionAuthentication's CSRF
            # check parses forms, and after that a multipart body can't be read again.
            # (Not earlier: Django REST framework's error handling needs the content
            # negotiation that runs first, e.g. for a body over DATA_UPLOAD_MAX_MEMORY_SIZE.)
            _ = request.body
        super().perform_authentication(request)

    def perform_content_negotiation(self, request: Request, force: bool = False) -> Any:
        # In pass-through mode the API server decides what it answers with, so an
        # Accept header Django REST framework can't render is not a 406.
        return super().perform_content_negotiation(request, force=force or self.pass_through)

    # -- Internals ------------------------------------------------------------

    def _forward(self, request: Request, method: str) -> HttpResponse:
        """Send ``request`` to the API server as ``method`` and relay the reply.

        In JSON mode, if anything fails on the way (a body that is not a JSON
        object, an API server that cannot be reached, a reply that is not JSON), the
        error is logged and the client gets ``{"status": "error"}`` with HTTP 200.
        """
        logger.debug("Proxy {} {}", method, request.path)
        if self.pass_through:
            return self._pass_through(request, method)
        try:
            reply = self._send_upstream(request, method).json()
        except Exception:  # Whatever went wrong, the client gets the same answer.
            logger.exception('Could not proxy {} {}, replying {{"status": "error"}}',
                             method, request.path)
            reply = ERROR_REPLY.copy()
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

    def _pass_through(self, request: Request, method: str) -> HttpResponse:
        # Django REST framework would replace the API server's Allow header and add
        # Accept to its Vary header.
        self.headers = {}
        url = self.get_proxy_path(request)
        query = request.META.get("QUERY_STRING", "")
        if query:
            url = f"{url}?{query}"
        body = request.body
        headers = self.upstream_headers(request)
        logger.debug("Forwarding headers {} and {} bytes of body", sorted(headers), len(body))
        try:
            upstream = self.send_request(method, url, data=body or None, headers=headers)
        except requests.RequestException as exc:
            status = 504 if isinstance(exc, requests.Timeout) else 502
            logger.exception('Could not proxy {} {}: {}. Replying {} {{"status": "error"}}',
                             method, request.path, _failure_reason(exc), status)
            return JsonResponse(ERROR_REPLY, status=status)
        logger.debug("API server answered {} ({}, {} bytes)", upstream.status_code,
                     _content_type(upstream) or "no Content-Type", len(upstream.content))
        return self.relay_response(upstream)

    def _map_path_exactly(self, request: Request) -> str:
        path = escape_uri_path(request.path)
        route = self.proxy_route_path.strip("/")
        if route:
            match = re.search("/" + re.escape(route) + "(?=/|$)", path)
            if match is None:  # e.g. /my_test_routeXYZ, which the README's URLs also send here
                raise Http404(f"{request.path} is not under /{route}/")
            path = path[match.end():]
        target = self.proxy_target_path.strip("/")
        if target:
            return f"/{target}{path}"
        return path or "/"


def _set_cookie_headers(upstream: requests.Response) -> list[str]:
    """Every ``Set-Cookie`` header of the reply; requests joins them into one."""
    raw_headers = getattr(getattr(upstream, "raw", None), "headers", None)
    getlist = getattr(raw_headers, "getlist", None)
    if callable(getlist):
        return list(getlist("Set-Cookie"))
    value = upstream.headers.get("Set-Cookie")
    return [value] if value else []


def _add_cookie(reply: HttpResponse, header: str) -> None:
    name = header.split("=", 1)[0].strip()
    jar = SimpleCookie()
    jar.load(header)
    if name in jar:
        reply.cookies[name] = jar[name]
    else:
        logger.debug("Dropped a Set-Cookie header Python can't parse (cookie {!r})", name)
