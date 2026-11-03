from django.urls import path

from django_simple_api_proxy import views

# The project's urls.py mounts these under the route, as in the README:
#     path(settings.PROXY_ROUTE_PATH, include("django_simple_api_proxy.urls"))
# so the route itself and everything below it reach the proxy.
urlpatterns = [
    path("", views.APIProxy.as_view(), {"resource": ""}),
    path("<path:resource>", views.APIProxy.as_view()),
]
