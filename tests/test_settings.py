"""What a developer sees when the settings are missing or would build wrong URLs."""
import json
import subprocess
import sys
import textwrap

from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from django_simple_api_proxy import conf
from django_simple_api_proxy.checks import check_json_mode_hooks, check_settings
from django_simple_api_proxy.views import APIProxy


class MissingSettingTests(SimpleTestCase):

    def test_missing_settings_are_named_with_a_line_to_copy(self):
        with override_settings():
            del settings.TARGET_API_URL
            del settings.PROXY_TARGET_PATH

            with self.assertRaises(ImproperlyConfigured) as raised:
                conf.require_settings()

        self.assertEqual(str(raised.exception), (
            "django-simple-api-proxy needs TARGET_API_URL, PROXY_TARGET_PATH in your settings. "
            "Add to settings.py, for example:\n"
            "    TARGET_API_URL = 'https://httpbin.org'  # the server requests are sent on to\n"
            "    PROXY_TARGET_PATH = 'get'  # the path on that server the route maps to"
        ))

    def test_importing_the_view_without_settings_says_what_to_add(self):
        # Before 2.0 this was AttributeError: 'Settings' object has no attribute ...
        result = run_fresh_project(without="TARGET_API_URL")

        self.assertEqual(result["error"], "ImproperlyConfigured")
        self.assertIn("TARGET_API_URL = 'https://httpbin.org'", result["message"])

    def test_nothing_is_raised_when_everything_is_set(self):
        conf.require_settings()

    def test_pass_through_setting_is_read(self):
        self.assertIs(conf.pass_through_enabled(), False)
        with override_settings(PROXY_PASS_THROUGH=True):
            self.assertIs(conf.pass_through_enabled(), True)


# A project set up like the README's quick start, in a process of its own: importing
# the view reads the settings once, and the test settings pin the 1.x JSON mode.
# The proxy settings come in as JSON on the command line. The API server is a
# stand-in for requests.request that answers 404 in plain text.
QUICK_START = {"TARGET_API_URL": "https://httpbin.org", "PROXY_ROUTE_PATH": "my_test_route",
               "PROXY_TARGET_PATH": "get"}
FRESH_PROJECT = textwrap.dedent("""
    import json, sys, types
    from unittest import mock

    import django
    from django.conf import settings

    settings.configure(
        DEBUG=True, SECRET_KEY="x", ALLOWED_HOSTS=["testserver"], ROOT_URLCONF="project_urls",
        INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes",
                        "rest_framework", "django_simple_api_proxy"],
        **json.loads(sys.argv[1]),
    )
    django.setup()
    try:
        from django_simple_api_proxy.views import APIProxy
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        sys.exit()

    from django.conf import settings
    from django.test import Client
    from django.urls import include, path

    urls = types.ModuleType("project_urls")
    urls.urlpatterns = [path(settings.PROXY_ROUTE_PATH, include("django_simple_api_proxy.urls"))]
    sys.modules["project_urls"] = urls

    reply = mock.Mock(status_code=404, reason="Not Found", content=b"nope",
                      headers={"Content-Type": "text/plain"}, raw=None)
    with mock.patch("requests.request", return_value=reply) as api:
        resp = Client().get("/my_test_route/items/7/?a=1")
    print(json.dumps({
        "pass_through": APIProxy.pass_through,
        "status": resp.status_code, "body": resp.content.decode(),
        "url": api.call_args.kwargs["url"],
        "allow_redirects": api.call_args.kwargs["allow_redirects"],
    }))
""")


def run_fresh_project(without=None):
    """Run FRESH_PROJECT with the quick start settings, minus `without`; return its JSON."""
    proxy_settings = {name: value for name, value in QUICK_START.items() if name != without}
    result = subprocess.run([sys.executable, "-c", FRESH_PROJECT, json.dumps(proxy_settings)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise AssertionError(result.stderr[-2000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


class DefaultModeTests(SimpleTestCase):
    """Since 2.0, pass-through mode is what you get without PROXY_PASS_THROUGH."""

    def test_pass_through_is_on_without_the_setting(self):
        with override_settings():
            del settings.PROXY_PASS_THROUGH

            self.assertIs(conf.pass_through_enabled(), True)

    def test_false_brings_back_json_mode(self):
        with override_settings(PROXY_PASS_THROUGH=False):
            self.assertIs(conf.pass_through_enabled(), False)

    def test_fresh_project_passes_requests_through(self):
        self.assertEqual(run_fresh_project(), {
            "pass_through": True,
            "status": 404, "body": "nope",
            "url": "https://httpbin.org/get/items/7/?a=1",
            "allow_redirects": False,
        })


class JSONModeHookCheckTests(SimpleTestCase):
    """W005: overrides that stopped doing anything when pass-through became the default."""

    def warned(self):
        return {m.obj: m for m in check_json_mode_hooks() if m.id == "django_simple_api_proxy.W005"}

    def test_json_mode_override_in_pass_through_mode_is_flagged(self):
        class OldStyle(APIProxy):
            pass_through = True

            def update_payload(self, request, params):
                return params

            def response(self, resp):
                return super().response(resp)

        class Child(OldStyle):
            pass

        warned = self.warned()

        self.assertIn(OldStyle, warned)
        self.assertIn(Child, warned)  # it inherits the overrides
        self.assertIsInstance(warned[OldStyle], checks.Warning)
        self.assertIn("overrides update_payload(), response(), which only JSON mode calls",
                      warned[OldStyle].msg)
        self.assertIn("set pass_through = False on it", warned[OldStyle].hint)

    def test_json_mode_subclasses_and_pass_through_hooks_are_fine(self):
        class OptedOut(APIProxy):
            pass_through = False

            def update_payload(self, request, params):
                return params

        class Modern(APIProxy):
            pass_through = True

            def upstream_headers(self, request):
                return {}

        warned = self.warned()

        self.assertNotIn(OptedOut, warned)
        self.assertNotIn(Modern, warned)

    def test_check_is_registered(self):
        class OldStyle(APIProxy):
            pass_through = True

            def preprocess_request_body(self, request):
                return super().preprocess_request_body(request)

        flagged = [m.obj for m in checks.run_checks() if m.id == "django_simple_api_proxy.W005"]

        self.assertIn(OldStyle, flagged)


class SystemCheckTests(SimpleTestCase):

    def ids(self):
        return [message.id for message in check_settings()]

    def test_readme_settings_pass(self):
        self.assertEqual(self.ids(), [])

    def test_check_is_registered(self):
        ids = [message.id for message in checks.run_checks()]
        self.assertNotIn("django_simple_api_proxy.W001", ids)
        with override_settings(TARGET_API_URL="httpbin.org"):
            ids = [message.id for message in checks.run_checks()]
        self.assertIn("django_simple_api_proxy.W001", ids)

    def test_warnings_are_warnings_not_errors(self):
        with override_settings(TARGET_API_URL="httpbin.org/", PROXY_ROUTE_PATH="/r/",
                               PROXY_TARGET_PATH=""):
            messages = check_settings()

        self.assertTrue(messages)
        self.assertTrue(all(isinstance(m, checks.Warning) for m in messages))

    def test_each_setting_problem(self):
        cases = [
            ({"TARGET_API_URL": "httpbin.org"}, ["django_simple_api_proxy.W001"]),
            ({"TARGET_API_URL": "https://httpbin.org/"}, ["django_simple_api_proxy.W002"]),
            ({"PROXY_ROUTE_PATH": "my_test_route/"}, ["django_simple_api_proxy.W003"]),
            ({"PROXY_ROUTE_PATH": "/my_test_route"}, ["django_simple_api_proxy.W003"]),
            ({"PROXY_TARGET_PATH": ""}, ["django_simple_api_proxy.W004"]),
            ({"PROXY_TARGET_PATH": "get/"}, ["django_simple_api_proxy.W004"]),
        ]
        for overrides, expected in cases:
            with self.subTest(**overrides), override_settings(**overrides):
                self.assertEqual(self.ids(), expected)

    def test_pass_through_mode_only_needs_the_scheme(self):
        relaxed = {"TARGET_API_URL": "https://httpbin.org/", "PROXY_ROUTE_PATH": "/r/",
                   "PROXY_TARGET_PATH": ""}
        with override_settings(PROXY_PASS_THROUGH=True, **relaxed):
            self.assertEqual(self.ids(), [])
        with override_settings(PROXY_PASS_THROUGH=True, TARGET_API_URL="httpbin.org"):
            self.assertEqual(self.ids(), ["django_simple_api_proxy.W001"])

    def test_hint_shows_the_fixed_value(self):
        with override_settings(PROXY_ROUTE_PATH="/my_test_route/"):
            (message,) = check_settings()

        self.assertEqual(message.hint, "Write it without slashes: PROXY_ROUTE_PATH = 'my_test_route'.")
