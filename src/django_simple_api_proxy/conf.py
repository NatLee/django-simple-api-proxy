"""The proxy's settings, and an error that says what to add when one is missing."""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

#: The settings every project needs, with an example and what each one is for.
REQUIRED_SETTINGS = {
    "TARGET_API_URL": ("'https://httpbin.org'", "the server requests are sent on to"),
    "PROXY_ROUTE_PATH": ("'my_test_route'", "the route on your site that is proxied"),
    "PROXY_TARGET_PATH": ("'get'", "the path on that server the route maps to"),
}


def require_settings() -> None:
    """Raise ``ImproperlyConfigured`` naming every missing setting, with lines to copy."""
    missing = [name for name in REQUIRED_SETTINGS if not hasattr(settings, name)]
    if not missing:
        return
    lines = "\n".join(
        f"    {name} = {example}  # {purpose}"
        for name, (example, purpose) in REQUIRED_SETTINGS.items()
        if name in missing
    )
    raise ImproperlyConfigured(
        f"django-simple-api-proxy needs {', '.join(missing)} in your settings. "
        f"Add to settings.py, for example:\n{lines}"
    )

