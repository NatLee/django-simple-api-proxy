from django.urls import path

from django_simple_api_proxy.views import APIProxy


class PassThroughProxy(APIProxy):
    """The proxy in pass-through mode, for the whole API server.

    `target_api_url` is set by the tests to the local upstream they start.
    """
    pass_through = True
    proxy_route_path = "my_test_route"
    proxy_target_path = ""


# The same shape as the README's URLs: the route, and everything that starts with it.
urlpatterns = [
    path("my_test_route", PassThroughProxy.as_view(), {"resource": ""}),
    path("my_test_route<path:resource>", PassThroughProxy.as_view()),
]
