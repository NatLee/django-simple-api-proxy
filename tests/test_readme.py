"""The code blocks in README.md work as written."""
import re
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
PY_BLOCKS = re.findall(r"```py\n(.*?)```", README, re.S)


def readme_block(marker):
    """The first ```py block of the README that contains `marker`."""
    return next(block for block in PY_BLOCKS if marker in block)


def run_readme_block(marker, **namespace):
    namespace.setdefault("__name__", "readme_example")
    exec(compile(readme_block(marker), "README.md", "exec"), namespace)
    return namespace


class QuickStartTests(SimpleTestCase):

    def test_installed_apps_block_lists_rest_framework(self):
        block = readme_block("INSTALLED_APPS")
        self.assertIn("'rest_framework'", block)
        self.assertIn("'django_simple_api_proxy'", block)

    def test_urls_block_runs_on_its_own(self):
        namespace = run_readme_block("include('django_simple_api_proxy.urls')", urlpatterns=[])

        self.assertEqual([str(p.pattern) for p in namespace["urlpatterns"]], ["my_test_route"])


@mock.patch("django_simple_api_proxy.views.requests.request")
class UsageExampleTests(SimpleTestCase):
    """The custom `MyAPIProxy` from the "Usage" section."""

    def setUp(self):
        self.view = run_readme_block("class MyAPIProxy(APIProxy)")["MyAPIProxy"].as_view()
        self.factory = APIRequestFactory()

    def test_logged_in_user_is_proxied(self, upstream):
        upstream.return_value = mock.Mock(**{"json.return_value": {"from": "upstream"}})
        request = self.factory.get("/my_test_route/?q=1")
        force_authenticate(request, user=User(username="alice"))

        resp = self.view(request, resource="/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'{"from": "upstream"}')
        sent = upstream.call_args.kwargs
        self.assertEqual(sent["url"], "https://httpbin.org/get")
        self.assertEqual(sent["params"], {"q": ["1"], "username": "alice"})

    def test_anonymous_user_gets_403(self, upstream):
        request = self.factory.get("/my_test_route/", HTTP_ACCEPT="application/json")

        resp = self.view(request, resource="/")
        resp.render()

        self.assertEqual(resp.status_code, 403)
        upstream.assert_not_called()

    def test_anonymous_browser_gets_403_page_not_500(self, upstream):
        # Needs 'rest_framework' in INSTALLED_APPS, as the quick start now says.
        request = self.factory.get("/my_test_route/", HTTP_ACCEPT="text/html")

        resp = self.view(request, resource="/")
        resp.render()

        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"Authentication credentials were not provided.", resp.content)
