# Django Simple API Proxy

[![Test](https://github.com/NatLee/django-simple-api-proxy/actions/workflows/test.yml/badge.svg)](https://github.com/NatLee/django-simple-api-proxy/actions/workflows/test.yml)[![Release](https://github.com/NatLee/django-simple-api-proxy/actions/workflows/release.yml/badge.svg)](https://github.com/NatLee/django-simple-api-proxy/actions/workflows/release.yml)

This is a simple tool for proxying any APIs easily on your Django server.

You can use it as middleware to make a layer for user authorization or something.

## Installation

```bash
pip install django-simple-api-proxy
```

Check it in [Pypi](https://pypi.org/project/django-simple-api-proxy/).

## Quick Start

1. Add `rest_framework` and `django_simple_api_proxy` to your `INSTALLED_APPS` in `settings.py` like this:

```py
INSTALLED_APPS = [
...
'rest_framework',
'django_simple_api_proxy',
]
```

The proxy view is built on [Django REST framework](https://www.django-rest-framework.org/), which is installed with this package. Without `rest_framework` in `INSTALLED_APPS`, the error pages it shows to browsers (for example "403 Forbidden" from a permission class) fail with `TemplateDoesNotExist` and become "500 Internal Server Error".

2. Add APP settings to your `settings.py` like this:

```py
TARGET_API_URL = 'https://httpbin.org'
PROXY_ROUTE_PATH = 'my_test_route'
PROXY_TARGET_PATH = 'get'
```

- `TARGET_API_URL`: the API server to forward requests to, without a trailing slash.
- `PROXY_ROUTE_PATH`: the path on your Django server that is proxied.
- `PROXY_TARGET_PATH`: the path on the API server that `PROXY_ROUTE_PATH` is mapped to.

Write both paths without a leading or trailing slash. With `PROXY_ROUTE_PATH = 'my_test_route/'`, the proxy builds wrong URLs (`/my_test_route/123` is sent to `/get123`).

3. Include the `django_simple_api_proxy` URL settings in your project `urls.py` like this:

```py
from django.conf import settings
from django.urls import include, path
urlpatterns += [
    path(settings.PROXY_ROUTE_PATH, include('django_simple_api_proxy.urls'))
]
```

4. Test on your server.

```bash
python manage.py runserver
```

Here's an example you success proxy an API by visit the following URLs.

- http://127.0.0.1:8000/my_test_route/
- http://127.0.0.1:8000/my_test_route

And the result will be as below.

```log
[06/Sep/2022 01:26:04] "GET /my_test_route/ HTTP/1.1" 200 314
2022-09-06 01:26:06.338 | DEBUG    | django_simple_api_proxy.views:get:73 - ----- Proxy GET
2022-09-06 01:26:06.339 | DEBUG    | django_simple_api_proxy.views:get_proxy_path:37 - URL: /get
2022-09-06 01:26:06.340 | DEBUG    | django_simple_api_proxy.views:update_payload:49 - Username: #anonymous
```

```json
{
  "args": { "username": "#anonymous" },
  "headers": {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Host": "httpbin.org",
    "User-Agent": "python-requests/2.27.1",
    "X-Amzn-Trace-Id": "Root=1-6316360c-2308560261599de4071127ac"
  },
  "origin": "xxx.xxx.xxx.xxx",
  "url": "https://httpbin.org/get?username=%23anonymous"
}
```

But when you visit `http://127.0.0.1:8000/my_test_route/123`, you'll get error.

Cause this URL is not found on target API server.

So, this proxy server will return this for you.

```json
{ "status": "error" }
```

## How requests are forwarded

- `GET`: the query string is forwarded as query parameters.
- `POST`, `PUT`, `PATCH` and `DELETE`: the request body must be JSON (or empty) and is forwarded as a JSON body. Query strings are not forwarded for these methods.
- The proxy adds `username` to the query parameters (`GET`) or the JSON body (other methods): the logged-in user's username, or `#anonymous`. A `username` sent by the client is replaced.
- The JSON returned by the API server is passed back as is. If the API server cannot be reached or does not return JSON, the proxy answers `{"status": "error"}` with HTTP status 200.

## Usage

After the quick start, you may want to change some methods with your API server like making an authorization.

You can do it with inheriting the `APIProxy` class.

Here is an example. `JWTAuthentication` comes from a separate package: `pip install djangorestframework-simplejwt`.

```py

from loguru import logger

from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated

from django_simple_api_proxy.views import APIProxy

class MyAPIProxy(APIProxy):
    # give custom authentication
    authentication_classes = [SessionAuthentication, JWTAuthentication]

    # give custom permission
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        """Get."""
        # your new `GET` logic here

        logger.debug("----- Proxy GET Heyyaya")
        response = {"status": "default error!!"}
        try:
            params = dict(request.GET)
            path = self.get_proxy_path(request)
            params = self.update_payload(request, params)
            middle_resp_ = self.send_request("GET", path, params=params)
            response = middle_resp_.json()
        except Exception as e:
            print('yooooo error occurs!!')
            print(e)
        return self.response(response)

```

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

## Supported versions

Python 3.8 or later and Django 4.0 or later. CI tests every Django release from 4.0 to 6.1, each on the oldest and the newest Python it supports.

## Development

```bash
pip install -e ".[test]"
pytest tests

pip install tox
tox -e py               # the Python on PATH with the newest Django
tox -e py312-django42   # one combination; `tox -l` lists them all
```

## More

There is an example project you can check in [./example](https://github.com/NatLee/django-simple-api-proxy/tree/main/example/api_proxy_example).
