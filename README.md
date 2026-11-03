<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/NatLee/django-simple-api-proxy/main/docs/images/hero-dark.webp">
    <img src="https://raw.githubusercontent.com/NatLee/django-simple-api-proxy/main/docs/images/hero-light.webp" width="880" alt="A client sends GET /my_test_route/items/7 to your Django site. APIProxy on your site maps /my_test_route/ to /get/ and sends GET /get/items/7 on to the target site, https://httpbin.org. The reply comes back the same way. Pass-through mode, the default since 2.0, passes the method, path, query, headers, any body, and the reply's status, headers, cookies, body and redirects through as they are. JSON mode, PROXY_PASS_THROUGH = False, is the 1.x behaviour: JSON in, JSON out, adds the caller's username and always answers HTTP 200.">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/NatLee/django-simple-api-proxy/actions/workflows/test.yml"><img src="https://github.com/NatLee/django-simple-api-proxy/actions/workflows/test.yml/badge.svg" alt="Test"></a>
  <a href="https://pypi.org/project/django-simple-api-proxy/"><img src="https://img.shields.io/pypi/v/django-simple-api-proxy" alt="PyPI"></a>
  <a href="#supported-versions"><img src="https://img.shields.io/pypi/pyversions/django-simple-api-proxy" alt="Python"></a>
  <a href="#supported-versions"><img src="https://img.shields.io/pypi/frameworkversions/django/django-simple-api-proxy?label=django" alt="Django"></a>
  <a href="https://github.com/NatLee/django-simple-api-proxy/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License: MIT"></a>
</p>

Django Simple API Proxy turns one route of your Django site into a door to another server. A request that comes in at `/my_test_route/…` goes out to the server you point it at, and the answer comes back the same way, so your browser code, apps and users only ever talk to your site. Handy when the other server has no CORS headers, needs an API key you don't want in the browser, or should only be reachable for people logged in to your site. The proxy is a [Django REST framework](https://www.django-rest-framework.org/) view, so putting your login in front of it takes two lines.

Everything goes through as it is: the method, path, query string, headers and body (JSON, forms, files, anything) go to the other server, and its status code, headers, cookies and body come back. Redirects come back too, pointed at your route, so the client keeps going through your site.

The JSON-in, JSON-out behaviour of 1.x is still there, one setting away: see [JSON mode](#json-mode).

## Upgrading to 2.0

2.0 makes pass-through the default. If you upgrade from 1.x and set nothing, your clients get the other server's answers as they are:

| | 1.x | 2.0 |
|---|---|---|
| Status code | always 200 | the other server's; 502 or 504 if it can't be reached in time |
| Failures | `{"status": "error"}` | the other server's own error reply |
| Reply | JSON only | any content type, with headers and cookies |
| `username` | added to the query string or JSON body | not added |
| Headers, forms, files, query strings on `POST` | dropped or refused | passed on |
| Trailing slash | dropped | kept |
| Redirects | followed on your server | handed to the client |

You are affected if your clients look for `{"status": "error"}` or count on HTTP 200, if the other server reads `username`, or if you subclass `APIProxy` and override `update_payload()`, `preprocess_request_body()` or `response()`. Those three only run in JSON mode, and `manage.py check` points out subclasses that still override them (`django_simple_api_proxy.W005`).

To keep the 1.x behaviour, add one line to `settings.py`:

```py
PROXY_PASS_THROUGH = False
```

or set `pass_through = False` on your subclass. Everything else (settings, URLs, method names) works as in 1.x.

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

2. Say where requests go, also in `settings.py`:

   ```py
   TARGET_API_URL = 'https://httpbin.org'
   PROXY_ROUTE_PATH = 'my_test_route'
   PROXY_TARGET_PATH = 'get'
   ```

   Now `/my_test_route/…` on your site maps to `https://httpbin.org/get/…`.

3. Send that route to the proxy in your project's `urls.py`:

   ```py
   from django.conf import settings
   from django.urls import include, path

   urlpatterns += [
       path(settings.PROXY_ROUTE_PATH, include('django_simple_api_proxy.urls')),
   ]
   ```

4. Start the server and send your first proxied request:

   ```bash
   python manage.py runserver
   curl "http://127.0.0.1:8000/my_test_route?q=django"
   ```

   httpbin answers with what it received (formatted, and without `headers` and `origin`):

   ```json
   {
     "args": {"q": "django"},
     "url": "https://httpbin.org/get?q=django"
   }
   ```

   A path the other server doesn't know gets its own 404:

   ```bash
   curl -i "http://127.0.0.1:8000/my_test_route/nope"
   # HTTP/1.1 404 Not Found
   # content-type: text/html
   # ...
   ```

   The same goes for `/my_test_route/` with a slash: it maps to `/get/`, which httpbin doesn't have. The proxy keeps your trailing slash, because the other server may care.

## What goes through

Point the route at the whole server with `PROXY_TARGET_PATH = ''`; the examples below use httpbin that way.

Status codes, reason phrases, content types and headers come back as they are. Here httpbin's teapot, with its own `418 Unknown` (your site's `Date`, `Server` and security headers left out):

```bash
curl -i http://127.0.0.1:8000/my_test_route/status/418
```

```http
HTTP/1.1 418 Unknown
access-control-allow-credentials: true
access-control-allow-origin: *
access-control-expose-headers: Retry-After, RateLimit, RateLimit-Policy
x-content-type-options: nosniff
x-more-info: http://tools.ietf.org/html/rfc2324
Content-Length: 135


    -=[ teapot ]=-

       _...._
     .'  _ _ `.
    | ."` ^ `". _,
    \_;`"---"`|//
      |       ;/
      \_     _/
        `"""`
```

Any method, query string, header and body goes the other way. httpbin shows what it got (trimmed):

```bash
printf 'hello\n' > hello.txt
curl "http://127.0.0.1:8000/my_test_route/anything/items?a=1&a=2" -X PUT \
     -H "X-Hello: world" -F "note=hi" -F "file=@hello.txt"
```

```json
{
  "args": {"a": ["1", "2"]},
  "files": {"file": "hello\n"},
  "form": {"note": "hi"},
  "headers": {
    "Content-Type": "multipart/form-data; boundary=------------------------O3Q6yIpVNGhVzhwQ2ShfTu",
    "X-Hello": "world",
    ...
  },
  "method": "PUT",
  "url": "https://httpbin.org/anything/items?a=1&a=2"
}
```

Binary replies and cookies come back too:

```bash
curl -s -o duck.png -w '%{http_code} %{content_type} %{size_download} bytes\n' \
     http://127.0.0.1:8000/my_test_route/image/png
# 200 image/png 8090 bytes

curl -i "http://127.0.0.1:8000/my_test_route/response-headers?X-Powered-By=httpbin&Set-Cookie=flavour%3Doatmeal"
# HTTP/1.1 200 OK
# content-type: application/json
# x-powered-by: httpbin
# Set-Cookie: flavour=oatmeal
# ...
```

Redirects go back to the client. A `Location` on the other server is rewritten to go through your route, and the cookies on the redirect come along, so a client that follows it stays on your site:

```bash
curl -i "http://127.0.0.1:8000/my_test_route/cookies/set?flavour=oatmeal"
# HTTP/1.1 302 Found
# Location: /my_test_route/cookies        (httpbin said /cookies)
# Set-Cookie: flavour=oatmeal; Path=/
# ...

curl -L -c jar.txt -b jar.txt "http://127.0.0.1:8000/my_test_route/cookies/set?flavour=oatmeal"
# {"cookies": {"flavour": "oatmeal"}}
```

## Settings

| Setting | Example | What it is |
|---|---|---|
| `TARGET_API_URL` | `'https://httpbin.org'` | The server requests go to, without a trailing slash. Required. |
| `PROXY_ROUTE_PATH` | `'my_test_route'` | The route on your site that is proxied. Required. |
| `PROXY_TARGET_PATH` | `'get'` | The path on that server the route maps to; `''` for the whole server. Required. |
| `PROXY_PASS_THROUGH` | `False` | `True` (the default since 2.0) passes everything through; `False` is the [JSON mode](#json-mode) of 1.x. Optional. |

The proxy reads them when Django loads your URLs. If `TARGET_API_URL` or `PROXY_TARGET_PATH` is missing, `manage.py check` and `runserver` stop and tell you what to add (a missing `PROXY_ROUTE_PATH` already stops them at the line of your `urls.py` that uses it):

```
django.core.exceptions.ImproperlyConfigured: django-simple-api-proxy needs TARGET_API_URL in your settings. Add to settings.py, for example:
    TARGET_API_URL = 'https://httpbin.org'  # the server requests are sent on to
```

They also warn, without stopping, about settings that can't work, such as a `TARGET_API_URL` without `https://`:

```
System check identified some issues:

WARNINGS:
?: (django_simple_api_proxy.W001) TARGET_API_URL does not start with http:// or https://, so no request can reach it.
	HINT: Write the full address, e.g. TARGET_API_URL = 'https://httpbin.org'.

System check identified 1 issue (0 silenced).
```

In JSON mode they also warn about slashes around the two paths, which build wrong URLs there (see [JSON mode](#json-mode)); pass-through mode trims those slashes itself. To silence a warning, add its id to `SILENCED_SYSTEM_CHECKS`.

With the quick start settings, paths map like this:

| Request to your site | Pass-through mode sends | JSON mode sends |
|---|---|---|
| `/my_test_route` or `/my_test_route/` | `https://httpbin.org/get` or `…/get/` | `https://httpbin.org/get` |
| `GET /my_test_route/items/42/?a=1` | `https://httpbin.org/get/items/42/?a=1` | `https://httpbin.org/get/items/42?a=1&username=%23anonymous` |

## Customising

Subclass `APIProxy`. This one only lets logged-in users through, keeps your site's own credentials on your site, and adds an API key on the server, so the key never reaches the browser:

```py
from django.conf import settings
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_simple_api_proxy.views import APIProxy


class MyAPIProxy(APIProxy):
    # Anyone not logged in gets a 403, and nothing is forwarded.
    authentication_classes = [SessionAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def upstream_headers(self, request):
        headers = super().upstream_headers(request)
        headers.pop("Cookie", None)  # your session cookie stays here
        headers.pop("Authorization", None)  # and so does your JWT
        headers["X-Api-Key"] = settings.UPSTREAM_API_KEY
        headers["X-User"] = request.user.get_username()
        return headers
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

A logged-in request with a cookie and a JWT, to httpbin's `/headers` (with `PROXY_TARGET_PATH = ''` and `UPSTREAM_API_KEY = 'demo-key'`):

```bash
curl -H "Authorization: Bearer $TOKEN" -H "Cookie: sessionid=abc" \
     http://127.0.0.1:8000/my_test_route/headers
# {"headers": {"X-Api-Key": "demo-key", "X-User": "nat", ...}}   no Authorization, no Cookie
```

**Why drop `Cookie` and `Authorization`?** Pass-through mode sends the client's headers on as they are, cookies and `Authorization` included. That is what makes logins on the other server work through your site. But if your site uses them for its own login and the other server isn't yours, they would reach a third party, so drop them in `upstream_headers()` as above.

What a subclass can change:

| Override | By default | Override it to |
|---|---|---|
| `authentication_classes`, `permission_classes` | `[]`: no login, anyone can use the proxy | require a login, as above |
| `target_api_url`, `proxy_route_path`, `proxy_target_path` | the three settings | proxy a second server from another subclass |
| `pass_through` | `PROXY_PASS_THROUGH` (`True`) | switch the mode for one subclass |
| `upstream_headers(request)` | the client's headers | add an API key, drop cookies |
| `relay_response(upstream)` | copies status, headers, cookies and body | change the reply |
| `rewrite_location(request, location)` | points `Location` at your route, see below | map redirects differently |
| `send_request(method, url, params=None, data=None, json=None, timeout=180, verify=True, headers=None, allow_redirects=None)` | calls `requests.request` | change the timeout, reuse a session |
| `get`, `post`, `put`, `patch`, `delete`, `head`, `options` | forward the request | replace one method completely |
| `update_payload(request, params)`, `response(resp)` | JSON mode only, see [JSON mode](#json-mode) | |

If you override `send_request`, accept `**kwargs`: pass-through mode also passes `headers` and `allow_redirects=False` through it.

## How requests are forwarded

| | Pass-through mode (default) | JSON mode (`PROXY_PASS_THROUGH = False`) |
|---|---|---|
| Methods | `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`, each as itself | `GET`, `POST`, `PUT`, `PATCH`, `DELETE`. `HEAD` goes as `GET`; Django REST framework answers `OPTIONS` |
| Path | the route swapped, the rest kept exactly, trailing slash too | the route swapped for `PROXY_TARGET_PATH`, trailing slash dropped |
| Query string | every method, exactly as sent | `GET` only, plus `username` |
| Request headers | the client's, see below | none: requests' defaults |
| Request body | any body, byte for byte | a JSON object, plus `username` |
| Reply status | the other server's | always 200 |
| Reply headers | the other server's, see below | none from the other server: `Content-Type: application/json` |
| Reply body | byte for byte, decompressed | the JSON, encoded again |
| Redirects | handed to the client, `Location` pointed at your route | followed on your server |
| On failure | 502 (can't reach it) or 504 (no answer in time) with `{"status": "error"}` | 200 with `{"status": "error"}` |

Both modes:

- **Login**: the proxy is a Django REST framework view, so `authentication_classes` and `permission_classes` work in both modes, and a refused request is never forwarded.
- **TLS and timeout**: the other server's certificate is checked, and it has 180 seconds to answer. Override `send_request` to change either.
- **Other methods** (`TRACE`, …) get `405 Method Not Allowed` and are not forwarded.
- **Logs**: each request is logged at `DEBUG` level, failures at `ERROR` level with the reason and the traceback. The proxy's own log lines never hold bodies or header values. See [Logging](#logging).

Pass-through mode:

- **Request headers**: all the client sends, cookies and `Authorization` included, except the ones that only describe one connection (`Connection` and the headers it names, `Keep-Alive`, `Proxy-Authorization`, `TE`, `Trailer`, `Transfer-Encoding`, `Upgrade`) and the ones requests sets itself (`Host`, `Content-Length`, `Accept-Encoding`, `Expect`). The proxy adds nothing of its own (no `username`, no `X-Forwarded-For`); requests only fills in `User-Agent` and `Accept` when the client sent none.
- **Reply headers**: all the other server sends, every `Set-Cookie` included, except the per-connection ones, `Content-Encoding` and `Content-Length` (requests has already decompressed the body), and `Date` and `Server` (your web server sets its own).
- **Redirects** (301, 302, 303, 307, 308) and other replies with a `Location` (such as 201) come back with it rewritten:
  - an address on the other server under `PROXY_TARGET_PATH` goes through your route: with the quick start settings, `https://httpbin.org/get/next` and `/get/next` both become `/my_test_route/next`;
  - a relative one (`next`, `../list`, `?page=2`) stays as it is, because it already resolves against your route;
  - an address on another host stays as it is, and one on the other server outside `PROXY_TARGET_PATH` becomes absolute, so it still leads there.
- **Bodies are read into memory**, in both directions. Django refuses request bodies over [`DATA_UPLOAD_MAX_MEMORY_SIZE`](https://docs.djangoproject.com/en/stable/ref/settings/#data-upload-max-memory-size) (2.5 MB by default) with a 400; raise it for bigger uploads. Streams (server-sent events, WebSockets) don't go through.
- **Bodies and cookies are not rewritten**: links in HTML still point at the other server, and a cookie's `Path` and `Domain` stay as the other server set them.

## JSON mode

`PROXY_PASS_THROUGH = False` (or `pass_through = False` on a subclass) brings back the 1.x proxy: JSON in, JSON out. With the quick start settings:

```bash
curl "http://127.0.0.1:8000/my_test_route/?q=django"
# {"args": {"q": "django", "username": "#anonymous"}, ..., "url": "https://httpbin.org/get?q=django&username=%23anonymous"}

curl "http://127.0.0.1:8000/my_test_route/nope"
# {"status": "error"}
```

and the server log says why: `Could not proxy GET /my_test_route/nope: the API server answered HTTP 404 (text/html), which is not JSON; set PROXY_PASS_THROUGH = True to pass any reply on.`

- **`username`** goes along with every request: the user Django REST framework authenticated, or `#anonymous`. The stock `APIProxy` has no authentication classes, so it sends `#anonymous` even for users logged in to your site; give your subclass authentication classes to send real names. A `username` sent by the client is replaced. User models without a `username` field send what `get_username()` returns.
- **Errors**: if the body is not a JSON object, the other server can't be reached or doesn't answer in time, or its reply is not JSON, the client gets `{"status": "error"}` with HTTP 200, and the log says which it was.
- **Redirects** are followed on your server, so the client gets the final answer.

A JSON-mode subclass changes what is sent through `update_payload()`. This one also sends the user's e-mail address:

```py
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_simple_api_proxy.views import APIProxy


class MyJSONProxy(APIProxy):
    pass_through = False  # JSON mode, as in 1.x
    authentication_classes = [SessionAuthentication, JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def update_payload(self, request, params):
        params = super().update_payload(request, params)  # adds "username"
        params["email"] = request.user.email
        return params
```

`response(resp)` reshapes the decoded JSON before it goes back (by default it returns it as is, non-ASCII text unescaped).

### Known limitations

These are why pass-through became the default; JSON mode keeps them for compatibility:

1. Errors and the other server's own status codes (404, 201, …) all reach the client as HTTP 200.
2. A `PROXY_ROUTE_PATH` with a trailing slash, or an empty `PROXY_TARGET_PATH`, builds wrong URLs: with `'my_test_route/'`, `/my_test_route/abc` goes to `/getabc`. `PROXY_ROUTE_PATH` is also used as a regular expression, and `/my_test_routeabc` reaches the proxy too.
3. `POST`, `PUT`, `PATCH` and `DELETE` drop the query string, and form-encoded bodies get `{"status": "error"}`.

## Logging

The proxy logs through loguru: every request at `DEBUG` level, failures at `ERROR` level with the reason and the traceback. Request bodies are logged by size or field name only, never by value, and headers by name only. The `PUT` from [What goes through](#what-goes-through) logs this:

```log
2026-11-03 16:39:45.063 | DEBUG    | django_simple_api_proxy.pass_through:_forward:258 - Proxy PUT /my_test_route/anything/items
2026-11-03 16:39:45.064 | DEBUG    | django_simple_api_proxy.pass_through:get_proxy_path:135 - Upstream URL: https://httpbin.org/anything/items
2026-11-03 16:39:45.064 | DEBUG    | django_simple_api_proxy.pass_through:_forward:265 - Forwarding headers ['Accept', 'Content-Type', 'User-Agent', 'X-Hello'] and 306 bytes of body
2026-11-03 16:39:45.714 | DEBUG    | django_simple_api_proxy.pass_through:_forward:275 - API server answered 200 (application/json, 557 bytes)
```

and a `POST` of `{"user": "nat", "password": "hunter2"}` in JSON mode this:

```log
2026-11-03 16:40:00.380 | DEBUG    | django_simple_api_proxy.json_mode:_forward:171 - Proxy POST /my_test_route/
2026-11-03 16:40:00.383 | DEBUG    | django_simple_api_proxy.json_mode:preprocess_request_body:101 - JSON body fields: ['user', 'password']
2026-11-03 16:40:00.383 | DEBUG    | django_simple_api_proxy.json_mode:get_proxy_path:146 - Upstream URL: https://httpbin.org/anything
2026-11-03 16:40:00.383 | DEBUG    | django_simple_api_proxy.json_mode:update_payload:84 - Username: #anonymous
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

CI tests every Django release from 4.0 to 6.1, each on the oldest and the newest Python it supports: 17 combinations. The package ships type hints (`py.typed`).

## Development

```bash
git clone https://github.com/NatLee/django-simple-api-proxy.git
cd django-simple-api-proxy
pip install -e ".[test]"
pytest tests
```

The tests don't need the internet. Pass-through mode is tested against a real HTTP server the tests start on `127.0.0.1` (`tests/upstream.py`); JSON mode against a fake `requests.request`, with the test settings pinned to `PROXY_PASS_THROUGH = False` so the 1.x tests keep running unchanged. `tests/test_readme.py` runs the Python blocks of this README, so keep them working when you edit it.

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

The picture at the top is drawn in [`docs/images/src/hero.html`](https://github.com/NatLee/django-simple-api-proxy/blob/main/docs/images/src/hero.html); the commands to render it are in the file.

## License

[MIT](https://github.com/NatLee/django-simple-api-proxy/blob/main/LICENSE)
