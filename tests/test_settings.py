"""What a developer sees when the settings are missing or would build wrong URLs."""
import importlib

from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from django_simple_api_proxy import conf, views
from django_simple_api_proxy.checks import check_settings


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
        # Before 1.1 this was AttributeError: 'Settings' object has no attribute ...
        with override_settings():
            del settings.TARGET_API_URL

            with self.assertRaisesMessage(ImproperlyConfigured, "TARGET_API_URL = 'https://"):
                importlib.reload(views)

    def test_nothing_is_raised_when_everything_is_set(self):
        conf.require_settings()

    def test_pass_through_setting_is_read(self):
        self.assertIs(conf.pass_through_enabled(), False)
        with override_settings(PROXY_PASS_THROUGH=True):
            self.assertIs(conf.pass_through_enabled(), True)


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
