from django.conf import settings
from django.urls import include, path

# Same as step 3 of the README quick start.
urlpatterns = [
    path(settings.PROXY_ROUTE_PATH, include('django_simple_api_proxy.urls')),
]
