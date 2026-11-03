"""System checks: ``manage.py check`` and ``runserver`` warn about proxy setups that misbehave.

W001-W004 are about settings that build wrong URLs; only the settings are checked,
not a subclass that sets its own ``target_api_url`` and paths. W005 is about
subclasses whose JSON-mode overrides stopped doing anything when pass-through mode
became the default in 2.0.

They are warnings, so they never stop a project from starting. Silence one with
``SILENCED_SYSTEM_CHECKS = ["django_simple_api_proxy.W003"]``.
"""
from __future__ import annotations

from typing import Any, Iterator

from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured

from django_simple_api_proxy.conf import pass_through_enabled

_ID = "django_simple_api_proxy."

#: The schemes requests can send to.
URL_SCHEMES = ("http://", "https://")


def check_settings(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    """Warn about proxy settings that send requests to the wrong place."""
    warnings: list[checks.CheckMessage] = []
    url = getattr(settings, "TARGET_API_URL", None)
    route = getattr(settings, "PROXY_ROUTE_PATH", None)
    target = getattr(settings, "PROXY_TARGET_PATH", None)
    # Pass-through mode trims the slashes itself, so only the scheme matters there.
    exact_paths = not pass_through_enabled()

    if isinstance(url, str) and not url.startswith(URL_SCHEMES):
        warnings.append(checks.Warning(
            "TARGET_API_URL does not start with http:// or https://, "
            "so no request can reach it.",
            hint="Write the full address, e.g. TARGET_API_URL = 'https://httpbin.org'.",
            id=_ID + "W001",
        ))
    if exact_paths and isinstance(url, str) and url.endswith("/"):
        warnings.append(checks.Warning(
            "TARGET_API_URL ends with '/', so the proxy builds URLs with '//', "
            "e.g. https://httpbin.org//get.",
            hint="Drop the trailing slash.",
            id=_ID + "W002",
        ))
    if exact_paths and isinstance(route, str) and route != route.strip("/"):
        warnings.append(checks.Warning(
            "PROXY_ROUTE_PATH starts or ends with '/', so the proxy builds wrong URLs: "
            "with 'my_test_route/', /my_test_route/abc goes to /getabc.",
            hint=f"Write it without slashes: PROXY_ROUTE_PATH = {route.strip('/')!r}.",
            id=_ID + "W003",
        ))
    if exact_paths and isinstance(target, str) and (not target or target != target.strip("/")):
        warnings.append(checks.Warning(
            "PROXY_TARGET_PATH is empty or starts or ends with '/', so the proxy "
            "builds URLs with '//' or without the slash between path parts.",
            hint="Write it without slashes, e.g. PROXY_TARGET_PATH = 'get'. To proxy "
                 "the whole API server, use pass-through mode (the default since 2.0), "
                 "where '' works.",
            id=_ID + "W004",
        ))
    return warnings


def _subclasses(cls: Any) -> Iterator[Any]:  # APIProxy, imported late (it needs the settings)
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


def check_json_mode_hooks(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    """Warn about subclasses that override JSON-mode methods but run in pass-through mode."""
    try:
        from django_simple_api_proxy.json_mode import JSON_MODE_HOOKS
        from django_simple_api_proxy.views import APIProxy
    except ImproperlyConfigured:
        return []  # a setting is missing, and Django reports that already
    try:
        from django.urls import get_resolver

        get_resolver().url_patterns  # imports the project's URLs and the proxies they use
    except Exception:
        pass  # Django's own URL checks report broken URLs
    warnings: list[checks.CheckMessage] = []
    for cls in sorted(set(_subclasses(APIProxy)), key=lambda c: (c.__module__, c.__qualname__)):
        overridden = [name for name in JSON_MODE_HOOKS
                      if getattr(cls, name) is not getattr(APIProxy, name)]
        if cls.pass_through and overridden:
            methods = ", ".join(f"{name}()" for name in overridden)
            warnings.append(checks.Warning(
                f"{cls.__module__}.{cls.__qualname__} overrides {methods}, which only JSON "
                "mode calls, but it runs in pass-through mode (the default since 2.0), so "
                "the override does nothing.",
                hint="To keep the 1.x behaviour, set pass_through = False on it (or "
                     "PROXY_PASS_THROUGH = False in settings.py). To pass everything "
                     "through, move the change to upstream_headers() or relay_response().",
                obj=cls,
                id=_ID + "W005",
            ))
    return warnings
