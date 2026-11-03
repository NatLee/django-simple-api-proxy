"""Forward the requests under one route of a Django site to another server.

``APIProxy`` takes a request for ``/<PROXY_ROUTE_PATH>/<rest>``, sends it to
``<TARGET_API_URL>/<PROXY_TARGET_PATH>/<rest>`` and answers with what that server
returns. It has two modes:

* Pass-through mode, the default since 2.0 (pass_through.py): the method, path,
  query string, headers and body go through as they are, and the status code,
  headers and body of the reply come back as they are. Redirects go back to the
  client.
* JSON mode, the behaviour of 1.x (json_mode.py; ``PROXY_PASS_THROUGH = False`` or
  ``pass_through = False`` on a subclass): JSON in, JSON out. It adds ``username``
  to what it sends and always answers with HTTP 200.

Subclass it to require a login or to change what is sent.
"""
from __future__ import annotations

# Projects and tests patch `django_simple_api_proxy.views.requests.request` to fake
# the API server, as the README's tests do; that has to keep working.
import requests  # noqa: F401

from django_simple_api_proxy.json_mode import (  # noqa: F401  importable from here since 1.x/2.0
    ANONYMOUS_USERNAME,
    JSONMode,
    ProxyError,
)
from django_simple_api_proxy.pass_through import ERROR_REPLY  # noqa: F401


class APIProxy(JSONMode):
    """API proxy"""
    # Django REST framework shows the docstring above in OPTIONS responses (JSON
    # mode) and on its browsable pages, so it stays as it has always been.
