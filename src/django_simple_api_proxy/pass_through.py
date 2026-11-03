"""Pass-through mode: one route of your site passed through to another server.

``PassThroughView`` sends a request for ``/<PROXY_ROUTE_PATH>/<rest>`` to
``<TARGET_API_URL>/<PROXY_TARGET_PATH>/<rest>`` as the client sent it (method,
path, query string, headers, body) and relays the reply as the server gave it
(status, headers, cookies, body). Redirects go back to the client.

This module knows nothing about the JSON mode of 1.x: ``APIProxy`` (views.py) is
this view with the JSON mode (json_mode.py) layered on top.
"""
from __future__ import annotations

import re
from http.cookies import SimpleCookie
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.utils.encoding import escape_uri_path
from loguru import logger
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from django_simple_api_proxy import conf
from django_simple_api_proxy.headers import (
    request_headers_to_forward,
    response_headers_to_relay,
)

conf.require_settings()

#: What the client gets when a request cannot be proxied (both modes).
ERROR_REPLY = {"status": "error"}


def _failure_reason(exc: Exception) -> str:
    """One line for the log about why the API server gave no answer."""
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


class RelayedResponse(HttpResponse):
    """The API server's reply on its way to the client.

    Like Django's own redirect responses it has ``url``, the ``Location`` it points
    to, so Django's test client can follow a relayed redirect.
    """

    @property
    def url(self) -> str | None:
        return self.get("Location")


class PassThroughView(APIView):
    """Pass requests through to the API server and relay its replies as they are."""

    # Anyone can use the stock proxy. Subclasses set these to require a login.
    authentication_classes = []
    permission_classes = []

    # Read once, when this module is imported. Subclasses can set their own.
    target_api_url: str = settings.TARGET_API_URL
    proxy_route_path: str = settings.PROXY_ROUTE_PATH
    proxy_target_path: str = settings.PROXY_TARGET_PATH

    # -- HTTP methods: each one goes through as itself --------------------------

    def get(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        return self._forward(request, "GET")

    def post(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        return self._forward(request, "POST")

    def put(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        return self._forward(request, "PUT")

    def patch(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        return self._forward(request, "PATCH")

    def delete(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        return self._forward(request, "DELETE")

    def head(self, request: Request, *args: Any, **kwargs: Any) -> HttpResponse:
        return self._forward(request, "HEAD")

    # Django REST framework answers OPTIONS with a Response of its own; this relays
    # the API server's reply instead.
    def options(  # type: ignore[override]
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponse:
        return self._forward(request, "OPTIONS")

    # -- Steps that subclasses can override -----------------------------------

    def parse_path(self, request: Request) -> str:
        """Return the path on the API server for ``request``.

        Everything after the route is kept as it is, trailing slash included, so
        ``/my_test_route/items/7/`` becomes ``/get/items/7/``. Slashes around the
        two settings don't matter.
        """
        _, rest = self._split_route(request)
        target = self.proxy_target_path.strip("/")
        if target:
            return f"/{target}{rest}"
        return rest or "/"

    def get_proxy_path(self, request: Request) -> str:
        """Return the API server URL for ``request``, without the query string."""
        url = f"{self.target_api_url.rstrip('/')}{self.parse_path(request)}"
        logger.debug("Upstream URL: {}", url)
        return url

    def upstream_headers(self, request: Request) -> dict[str, str]:
        """Return the headers to send to the API server.

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
        allow_redirects: bool | None = None,
    ) -> requests.Response:
        """Send the request to the API server and return its reply.

        Pass-through mode puts the query string in ``url`` and passes the raw body
        as ``data``, the result of ``upstream_headers()`` as ``headers`` and
        ``allow_redirects=False``, so an override has to accept those
        (``**kwargs`` will do).
        """
        kwargs: dict[str, Any] = dict(method=method, url=url, params=params, data=data,
                                      json=json, timeout=timeout, verify=verify)
        # JSON mode passes neither, and calls requests as it always has (following
        # redirects).
        if headers is not None:
            kwargs["headers"] = dict(headers)
        if allow_redirects is not None:
            kwargs["allow_redirects"] = allow_redirects
        return requests.request(**kwargs)

    def relay_response(self, upstream: requests.Response) -> HttpResponse:
        """Build the reply for the client from the API server's reply.

        The status code, headers, cookies and body come back as they are, except
        hop-by-hop headers and those that no longer fit after requests decoded the
        body (``Content-Encoding``, ``Content-Length``). A ``Location`` header goes
        through ``rewrite_location()``.
        """
        reply = RelayedResponse(upstream.content, status=upstream.status_code,
                                reason=upstream.reason or None)
        del reply["Content-Type"]  # only the API server's, if it sent one
        head = self.request.method == "HEAD"
        for name, value in response_headers_to_relay(upstream.headers, head=head).items():
            reply[name] = value
        location = reply.get("Location")
        if location:
            reply["Location"] = self.rewrite_location(self.request, location)
        for cookie in _set_cookie_headers(upstream):
            _add_cookie(reply, cookie)
        return reply

    def rewrite_location(self, request: Request, location: str) -> str:
        """Point a ``Location`` header of the API server at your site.

        * An address on the API server under ``proxy_target_path`` goes through the
          proxy route: with the quick start settings, ``https://httpbin.org/get/next``
          and ``/get/next`` both become ``/my_test_route/next``.
        * A relative one (``next``, ``../list``, ``?page=2``) stays as it is: the
          client resolves it against the proxy route, which is right already.
        * An address on another host stays as it is. One on the API server outside
          ``proxy_target_path`` (``/login`` above) becomes absolute, so it still
          leads to the API server.
        """
        if not (location.startswith("/") or urlsplit(location).scheme):
            return location
        base = urlsplit(self.target_api_url.rstrip("/"))
        target = self.proxy_target_path.strip("/")
        base_path = base.path.rstrip("/") + (f"/{target}" if target else "")
        there = urlsplit(urljoin(f"{base.scheme}://{base.netloc}/", location))
        same_server = (there.scheme.lower(), there.netloc.lower()) == (
            base.scheme.lower(), base.netloc.lower())
        under_target = (not base_path or there.path == base_path
                        or there.path.startswith(base_path + "/"))
        if same_server and under_target:
            try:
                route, _ = self._split_route(request)
            except Http404:  # a parse_path() override sent something else here
                return location
            path = route + there.path[len(base_path):]
            return urlunsplit(("", "", path or "/", there.query, there.fragment))
        if location.startswith("/") and not location.startswith("//"):
            return urlunsplit(there)  # on the API server, outside the proxied path
        return location

    # -- Django REST framework hooks ------------------------------------------

    def perform_authentication(self, request: Request) -> None:
        # Read the body before authentication can: SessionAuthentication's CSRF
        # check parses forms, and after that a multipart body can't be read again.
        # (Not earlier: Django REST framework's error handling needs the content
        # negotiation that runs first, e.g. for a body over DATA_UPLOAD_MAX_MEMORY_SIZE.)
        _ = request.body
        super().perform_authentication(request)

    def perform_content_negotiation(self, request: Request, force: bool = False) -> Any:
        # The API server decides what it answers with, so an Accept header Django
        # REST framework can't render is not a 406.
        return super().perform_content_negotiation(request, force=True)

    def finalize_response(self, request: Request, response: Any, *args: Any, **kwargs: Any) -> Any:
        # Django REST framework would set its own Allow header on every reply and add
        # Accept to Vary. The replies the proxy builds (the API server's, or a 502/504)
        # keep their headers; Django REST framework's own (a 403, say) get its headers.
        if isinstance(response, Response):
            return super().finalize_response(request, response, *args, **kwargs)
        return response

    # -- Internals ------------------------------------------------------------

    def _forward(self, request: Request, method: str) -> HttpResponse:
        """Send ``request`` to the API server as ``method`` and relay the reply."""
        logger.debug("Proxy {} {}", method, request.path)
        url = self.get_proxy_path(request)
        query = request.META.get("QUERY_STRING", "")
        if query:
            url = f"{url}?{query}"
        body = request.body
        headers = self.upstream_headers(request)
        logger.debug("Forwarding headers {} and {} bytes of body", sorted(headers), len(body))
        try:
            # Redirects go back to the client (see rewrite_location()), cookies and all.
            upstream = self.send_request(method, url, data=body or None, headers=headers,
                                         allow_redirects=False)
        except requests.RequestException as exc:
            status = 504 if isinstance(exc, requests.Timeout) else 502
            logger.exception('Could not proxy {} {}: {}. Replying {} {{"status": "error"}}',
                             method, request.path, _failure_reason(exc), status)
            return JsonResponse(ERROR_REPLY, status=status)
        logger.debug("API server answered {} ({}, {} bytes)", upstream.status_code,
                     _content_type(upstream) or "no Content-Type", len(upstream.content))
        return self.relay_response(upstream)

    def _split_route(self, request: Request) -> tuple[str, str]:
        """Split the request path into the proxy route on your site and the rest."""
        path = escape_uri_path(request.path)
        route = self.proxy_route_path.strip("/")
        if not route:
            return "", path
        match = re.search("/" + re.escape(route) + "(?=/|$)", path)
        if match is None:  # e.g. /my_test_routeXYZ, which the README's URLs also send here
            raise Http404(f"{request.path} is not under /{route}/")
        return path[:match.end()], path[match.end():]


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
