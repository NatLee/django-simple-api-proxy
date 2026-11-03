"""System checks: ``manage.py check`` and ``runserver`` warn about settings that build wrong URLs.

They are warnings, so they never stop a project from starting. Silence one with
``SILENCED_SYSTEM_CHECKS = ["django_simple_api_proxy.W003"]``. Only the settings are
checked; a subclass that sets its own ``target_api_url`` and paths is not.
"""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core import checks

_ID = "django_simple_api_proxy."


def check_settings(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    """Warn about proxy settings that send requests to the wrong place."""
    warnings: list[checks.CheckMessage] = []
    url = getattr(settings, "TARGET_API_URL", None)
    route = getattr(settings, "PROXY_ROUTE_PATH", None)
    target = getattr(settings, "PROXY_TARGET_PATH", None)

    if isinstance(url, str) and not url.startswith(("http://", "https://")):
        warnings.append(checks.Warning(
            "TARGET_API_URL does not start with http:// or https://, "
            "so no request can reach it.",
            hint="Write the full address, e.g. TARGET_API_URL = 'https://httpbin.org'.",
            id=_ID + "W001",
        ))
    if isinstance(url, str) and url.endswith("/"):
        warnings.append(checks.Warning(
            "TARGET_API_URL ends with '/', so the proxy builds URLs with '//', "
            "e.g. https://httpbin.org//get.",
            hint="Drop the trailing slash.",
            id=_ID + "W002",
        ))
    if isinstance(route, str) and route != route.strip("/"):
        warnings.append(checks.Warning(
            "PROXY_ROUTE_PATH starts or ends with '/', so the proxy builds wrong URLs: "
            "with 'my_test_route/', /my_test_route/abc goes to /getabc.",
            hint=f"Write it without slashes: PROXY_ROUTE_PATH = {route.strip('/')!r}.",
            id=_ID + "W003",
        ))
    if isinstance(target, str) and (not target or target != target.strip("/")):
        warnings.append(checks.Warning(
            "PROXY_TARGET_PATH is empty or starts or ends with '/', so the proxy "
            "builds URLs with '//' or without the slash between path parts.",
            hint="Write it without slashes, e.g. PROXY_TARGET_PATH = 'get'.",
            id=_ID + "W004",
        ))
    return warnings
