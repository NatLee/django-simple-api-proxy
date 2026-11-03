# Django Simple API Proxy

[![Test](https://github.com/NatLee/django-simple-api-proxy/actions/workflows/test.yml/badge.svg)](https://github.com/NatLee/django-simple-api-proxy/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/django-simple-api-proxy)](https://pypi.org/project/django-simple-api-proxy/)
[![Python 3.8–3.14](https://img.shields.io/badge/python-3.8%20%E2%80%93%203.14-3776ab)](#supported-versions)
[![Django 4.0–6.1](https://img.shields.io/badge/django-4.0%20%E2%80%93%206.1-0c4b33)](#supported-versions)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/NatLee/django-simple-api-proxy/blob/main/LICENSE)

Put another HTTP API behind a route on your Django site. Requests to `/my_test_route/…` are sent on to the API server you configure, with the route swapped for the API's path and the caller's username attached, and the JSON the API answers with goes straight back to the client. The proxy is a [Django REST framework](https://www.django-rest-framework.org/) view, so putting your site's login in front of the API takes a couple of lines, and anything else is one overridden method away.

![A client calls GET /my_test_route/?q=django on your Django site. The proxy checks the login if you asked it to, maps my_test_route to get and adds username, then calls GET /get?q=django&username=%23anonymous on https://httpbin.org. The JSON reply goes back to the client with HTTP 200; if anything fails, the client gets 200 with {"status": "error"}.](https://raw.githubusercontent.com/NatLee/django-simple-api-proxy/main/docs/images/request-flow.png)

## Installation

```bash
pip install django-simple-api-proxy
```

This also installs Django REST framework, [requests](https://requests.readthedocs.io/) and [loguru](https://github.com/Delgan/loguru).

## Quick start

1. Add Django REST framework and the proxy to `INSTALLED_APPS` in `settings.py`:

   ```py
   INSTALLED_APPS = [
       ...,
       'rest_framework',
       'django_simple_api_proxy',
   ]
   ```

   Don't skip `rest_framework`: without it, the error pages Django REST framework shows to browsers (for example a 403 from a permission class) fail and turn into a 500.

2. Tell the proxy where to send requests, also in `settings.py`:

   ```py
   TARGET_API_URL = 'https://httpbin.org'
   PROXY_ROUTE_PATH = 'my_test_route'
   PROXY_TARGET_PATH = 'get'
   ```

   Now `/my_test_route/…` on your site maps to `https://httpbin.org/get/…`.

3. Route the requests to the proxy in your project's `urls.py`:

   ```py
   from django.conf import settings
   from django.urls import include, path

   urlpatterns += [
       path(settings.PROXY_ROUTE_PATH, include('django_simple_api_proxy.urls')),
   ]
   ```

4. Start the server and send a request:

   ```bash
   python manage.py runserver
   curl "http://127.0.0.1:8000/my_test_route/?q=django"
   ```

   httpbin answers with what it received. Formatted, and without `headers` and `origin`:

   ```json
   {
     "args": {"q": "django", "username": "#anonymous"},
     "method": "GET",
     "url": "https://httpbin.org/get?q=django&username=%23anonymous"
   }
   ```

   A path the API server doesn't know gets the proxy's error answer:

   ```bash
   curl "http://127.0.0.1:8000/my_test_route/nope"
   # {"status": "error"}
   ```

To try `POST`, `PUT`, `PATCH` and `DELETE`, set `PROXY_TARGET_PATH = 'anything'`; httpbin's `/anything` echoes every method:

```bash
curl -X POST http://127.0.0.1:8000/my_test_route/ \
     -H 'Content-Type: application/json' -d '{"hello": "world"}'
# {..., "method": "POST", "json": {"hello": "world", "username": "#anonymous"}, ...}
```

## Settings

| Setting | Example | What it is |
|---|---|---|
| `TARGET_API_URL` | `'https://httpbin.org'` | The API server, without a trailing slash. |
| `PROXY_ROUTE_PATH` | `'my_test_route'` | The route on your site that is proxied. |
| `PROXY_TARGET_PATH` | `'get'` | The path on the API server that the route maps to. |

All three are required. The proxy reads them when Django loads your URLs, so a missing one stops `manage.py check` and `runserver` with `AttributeError: 'Settings' object has no attribute 'TARGET_API_URL'`.

Write both paths without slashes at either end, and don't leave `PROXY_TARGET_PATH` empty: either one builds wrong URLs (see [Known limitations](#known-limitations)). With the settings above, the paths map like this:

| Request to your site | Request to the API server |
|---|---|
| `/my_test_route` or `/my_test_route/` | `https://httpbin.org/get` |
| `GET /my_test_route/items/42/?a=1` | `https://httpbin.org/get/items/42?a=1&username=%23anonymous` |

## Customising

Subclass `APIProxy`. This one only lets logged-in users through and also sends their e-mail address:

```py
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_simple_api_proxy.views import APIProxy


class MyAPIProxy(APIProxy):
    # Anyone not logged in gets a 403, and nothing is forwarded.
    authentication_classes = [SessionAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def update_payload(self, request, params):
        params = super().update_payload(request, params)  # adds "username"
        params["email"] = request.user.email
        return params
```

`JWTAuthentication` comes from a separate package: `pip install djangorestframework-simplejwt`. With `SessionAuthentication`, Django REST framework also checks the CSRF token on `POST`, `PUT`, `PATCH` and `DELETE` from logged-in browsers.

Then route `PROXY_ROUTE_PATH` to your class in `urls.py`, instead of including `django_simple_api_proxy.urls`:

```py
from django.conf import settings
from django.urls import path

from .views import MyAPIProxy  # wherever you defined it

urlpatterns += [
    path(settings.PROXY_ROUTE_PATH, MyAPIProxy.as_view(), {"resource": ""}),
    path(settings.PROXY_ROUTE_PATH + "<path:resource>", MyAPIProxy.as_view()),
]
```

What else a subclass can change:

| Override | By default | Override it to |
|---|---|---|
| `authentication_classes`, `permission_classes` | `[]`: no login, anyone can use the proxy | require a login, as above |
| `target_api_url`, `proxy_route_path`, `proxy_target_path` | the three settings | proxy a second API from another subclass |
| `update_payload(request, params)` | sets `username` | add or drop what is sent |
| `send_request(method, url, params=None, data=None, json=None, timeout=180, verify=True)` | calls `requests.request` | add headers, change the timeout, reuse a session |
| `response(resp)` | returns the JSON as is, non-ASCII text unescaped | reshape the reply |
| `get`, `post`, `put`, `patch`, `delete` | forward the request | replace one method completely |

## How requests are forwarded

| The client sends | The API server gets |
|---|---|
| `GET` | `GET` with the query string as query parameters, plus `username` |
| `POST`, `PUT`, `PATCH`, `DELETE` | the same method with the JSON body, plus `username`. The body must be a JSON object or empty. |
| `HEAD` | a `GET`; the client gets the headers only |
| `OPTIONS` | nothing: Django REST framework answers it |
| anything else | nothing: the client gets `405 Method Not Allowed` |

- **Path**: the route is replaced with `PROXY_TARGET_PATH`, the rest of the path is kept, and a trailing slash is dropped.
- **`username`**: the user Django REST framework authenticated, or `#anonymous`. The stock `APIProxy` has no authentication classes, so it sends `#anonymous` even for users logged in to your site; [subclass it](#customising) to send real names. A `username` sent by the client is replaced.
- **Headers**: the client's headers, cookies included, stay on your site. The API server only gets the default headers of requests.
- **Reply**: the API server's JSON goes back as is, with HTTP 200. Its status code and headers are not passed on.
- **Errors**: if the body is not a JSON object, the API server can't be reached or doesn't answer within 180 seconds, or its reply is not JSON, the client gets `{"status": "error"}` with HTTP 200, and the error is logged with its traceback.
- **TLS**: the API server's certificate is checked.
- **Logs**: each request is logged at `DEBUG` level. Of a JSON body, only the field names are logged, never the values. See [Logging](#logging).

### Known limitations

This is how the proxy behaves today; plan around it:

1. Errors and the API server's own status codes (404, 201, …) all reach the client as HTTP 200.
2. A `PROXY_ROUTE_PATH` with a trailing slash, or an empty `PROXY_TARGET_PATH`, builds wrong URLs: with `'my_test_route/'`, `/my_test_route/abc` goes to `/getabc`. `PROXY_ROUTE_PATH` is also used as a regular expression, and `/my_test_routeabc` reaches the proxy too.
3. `POST`, `PUT`, `PATCH` and `DELETE` drop the query string, and form-encoded bodies get `{"status": "error"}`.

## Logging

The proxy logs through loguru: every request at `DEBUG` level, failures at `ERROR` level with the traceback. Request bodies are logged by field name only, never by value. A `POST` with `{"user": "nat", "password": "hunter2"}` logs this:

```log
2026-11-03 12:56:05.547 | DEBUG    | django_simple_api_proxy.views:_forward:147 - Proxy POST /my_test_route/
2026-11-03 12:56:05.547 | DEBUG    | django_simple_api_proxy.views:preprocess_request_body:106 - JSON body fields: ['user', 'password']
2026-11-03 12:56:05.548 | DEBUG    | django_simple_api_proxy.views:get_proxy_path:84 - Upstream URL: https://httpbin.org/anything
2026-11-03 12:56:05.548 | DEBUG    | django_simple_api_proxy.views:update_payload:95 - Username: #anonymous
```

Unless you configure it, loguru prints all of this to stderr. Its default handler also prints the values of local variables in tracebacks (`diagnose=True`), so when a request fails, its body can still show up in the traceback. If bodies may hold passwords or tokens, turn the proxy's log off, or keep only `INFO` and up without the variable values:

```py
import sys

from loguru import logger

logger.disable("django_simple_api_proxy")  # no log from the proxy at all

# or
logger.remove()
logger.add(sys.stderr, level="INFO", diagnose=False)
```

## Supported versions

| | Versions |
|---|---|
| Python | 3.8 – 3.14 |
| Django | 4.0 – 6.1 |
| Django REST framework | the release pip picks for your Django |

CI tests every Django release from 4.0 to 6.1, each on the oldest and the newest Python it supports: 17 combinations.

## Development

```bash
git clone https://github.com/NatLee/django-simple-api-proxy.git
cd django-simple-api-proxy
pip install -e ".[test]"
pytest tests
```

The tests don't touch the network: the API server is replaced by a fake `requests.request`. `tests/test_readme.py` runs the Python blocks of this README, so keep them working when you edit it.

To run the CI combinations locally:

```bash
pip install tox
tox -e py               # the Python on PATH with the newest Django it can install
tox -e py312-django42   # one combination; `tox -l` lists them all
```

There is also a small Django project to try things in: [`example/api_proxy_example`](https://github.com/NatLee/django-simple-api-proxy/tree/main/example/api_proxy_example).

```bash
pip install -e .
cd example/api_proxy_example
python manage.py runserver
```

The diagram at the top is drawn in [`docs/images/request-flow.html`](https://github.com/NatLee/django-simple-api-proxy/blob/main/docs/images/request-flow.html); the command to render it is in the file.

## License

[MIT](https://github.com/NatLee/django-simple-api-proxy/blob/main/LICENSE)
