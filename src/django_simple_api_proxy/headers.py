"""Which headers pass-through mode sends on, in each direction.

A proxy never passes on hop-by-hop headers: they describe one connection (client to
your site, or your site to the API server), not the message (RFC 9110, section 7.6.1).
A few more are worked out again by requests or by your web server.
"""
from __future__ import annotations

from typing import Iterable, Mapping

#: Hop-by-hop headers, plus the legacy ``Proxy-Connection`` and ``Trailers``.
HOP_BY_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "trailers", "transfer-encoding", "upgrade",
})

#: Request headers requests sets itself for the API server. ``Accept-Encoding`` is
#: requests' own so that it can always decode the reply; your web server has
#: already answered ``Expect: 100-continue``.
NOT_FORWARDED_REQUEST_HEADERS = frozenset({"host", "content-length", "accept-encoding", "expect"})

#: The Content-Type that Django's runserver (Python's wsgiref) fills in for requests
#: that have none. On a request without a body it came from the development server,
#: not from the client, so it stays behind.
RUNSERVER_DEFAULT_CONTENT_TYPE = "text/plain"

#: Response headers that no longer fit once requests has read and decoded the reply,
#: or that your web server sets itself (nginx hides ``Date`` and ``Server`` too).
#: ``Set-Cookie`` goes through separately, one cookie at a time.
NOT_RELAYED_RESPONSE_HEADERS = frozenset({
    "content-encoding", "content-length", "date", "server", "set-cookie",
})


def _connection_tokens(headers: Mapping[str, str]) -> set[str]:
    """Header names listed in ``Connection``; those are hop-by-hop too."""
    tokens: set[str] = set()
    for name, value in headers.items():
        if name.lower() == "connection":
            tokens.update(t.strip().lower() for t in value.split(",") if t.strip())
    return tokens


def _without(headers: Mapping[str, str], names: Iterable[str]) -> dict[str, str]:
    skip = set(names) | HOP_BY_HOP_HEADERS | _connection_tokens(headers)
    return {name: value for name, value in headers.items() if name.lower() not in skip}


def request_headers_to_forward(headers: Mapping[str, str], has_body: bool) -> dict[str, str]:
    """The client's headers that go on to the API server.

    Without a body, the Content-Type runserver makes up (RUNSERVER_DEFAULT_CONTENT_TYPE)
    stays behind.
    """
    forwarded = _without(headers, NOT_FORWARDED_REQUEST_HEADERS)
    if not has_body:
        forwarded = {
            name: value for name, value in forwarded.items()
            if not (name.lower() == "content-type" and value == RUNSERVER_DEFAULT_CONTENT_TYPE)
        }
    return forwarded


def response_headers_to_relay(headers: Mapping[str, str], head: bool = False) -> dict[str, str]:
    """The API server's headers that go back to the client.

    A reply to ``HEAD`` has no body to decode, so its ``Content-Length`` stays when
    the API server did not compress the reply.
    """
    relayed = _without(headers, NOT_RELAYED_RESPONSE_HEADERS)
    if head and not any(name.lower() == "content-encoding" for name in headers):
        relayed.update({name: value for name, value in headers.items()
                        if name.lower() == "content-length"})
    return relayed
