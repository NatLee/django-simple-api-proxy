"""The code blocks in README.md work as written."""
import re
import sys
import textwrap
import types
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.test import SimpleTestCase, override_settings
from django.urls.resolvers import RegexPattern, URLResolver
from loguru import logger
from rest_framework.test import APIRequestFactory, force_authenticate

from django_simple_api_proxy.views import APIProxy
from tests.upstream import Upstream, echoed

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
# Blocks inside the quick start list are indented; dedent them before running.
PY_BLOCKS = [textwrap.dedent(block) for block in re.findall(r"```py\n(.*?)```", README, re.S)]


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

    def test_settings_block_is_what_the_tests_use(self):
        namespace = run_readme_block("TARGET_API_URL")

        for name in ["TARGET_API_URL", "PROXY_ROUTE_PATH", "PROXY_TARGET_PATH"]:
            self.assertEqual(namespace[name], getattr(settings, name))

    def test_urls_block_runs_on_its_own(self):
        namespace = run_readme_block("include('django_simple_api_proxy.urls')", urlpatterns=[])

        self.assertEqual([str(p.pattern) for p in namespace["urlpatterns"]], ["my_test_route"])


def readme_proxy_class():
    return run_readme_block("class MyAPIProxy(APIProxy)")["MyAPIProxy"]


@mock.patch("django_simple_api_proxy.views.requests.request")
class UsageExampleTests(SimpleTestCase):
    """The custom `MyAPIProxy` from the "Customising" section."""

    def setUp(self):
        self.view = readme_proxy_class().as_view()
        self.factory = APIRequestFactory()

    def test_logged_in_user_is_proxied(self, upstream):
        upstream.return_value = mock.Mock(**{"json.return_value": {"from": "upstream"}})
        request = self.factory.get("/my_test_route/?q=1")
        force_authenticate(request, user=User(username="alice", email="alice@example.com"))

        resp = self.view(request, resource="/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'{"from": "upstream"}')
        sent = upstream.call_args.kwargs
        self.assertEqual(sent["url"], "https://httpbin.org/get")
        self.assertEqual(sent["params"],
                         {"q": ["1"], "username": "alice", "email": "alice@example.com"})

    def test_anonymous_user_gets_403(self, upstream):
        request = self.factory.get("/my_test_route/", HTTP_ACCEPT="application/json")

        resp = self.view(request, resource="/")
        resp.render()

        self.assertEqual(resp.status_code, 403)
        upstream.assert_not_called()

    def test_anonymous_browser_gets_403_page_not_500(self, upstream):
        # Needs 'rest_framework' in INSTALLED_APPS, as the quick start says.
        request = self.factory.get("/my_test_route/", HTTP_ACCEPT="text/html")

        resp = self.view(request, resource="/")
        resp.render()

        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"Authentication credentials were not provided.", resp.content)


class CustomRoutingTests(SimpleTestCase):
    """The `urls.py` block that routes `PROXY_ROUTE_PATH` to `MyAPIProxy`."""

    def test_route_and_paths_below_it_reach_the_subclass(self):
        proxy_class = readme_proxy_class()
        package = types.ModuleType("readme_project")
        package.__path__ = []
        views = types.ModuleType("readme_project.views")
        views.MyAPIProxy = proxy_class
        modules = {"readme_project": package, "readme_project.views": views}

        with mock.patch.dict(sys.modules, modules):
            namespace = run_readme_block(
                "MyAPIProxy.as_view()", __name__="readme_project.urls",
                __package__="readme_project", urlpatterns=[],
            )

        resolver = URLResolver(RegexPattern(r"^/"), namespace["urlpatterns"])
        for path, resource in [("/my_test_route", ""), ("/my_test_route/items/7", "/items/7")]:
            with self.subTest(path=path):
                match = resolver.resolve(path)
                self.assertIs(match.func.view_class, proxy_class)
                self.assertEqual(match.kwargs, {"resource": resource})


@mock.patch("django_simple_api_proxy.views.requests.request")
class LoggingBlockTests(SimpleTestCase):

    def test_logging_block_turns_the_proxy_log_off(self, upstream):
        def restore():
            logger.enable("django_simple_api_proxy")
            logger.remove()
            logger.add(sys.stderr)  # loguru's default handler
        self.addCleanup(restore)
        upstream.return_value = mock.Mock(**{"json.return_value": {}})

        run_readme_block('logger.disable("django_simple_api_proxy")')
        messages = []
        logger.add(messages.append, level="DEBUG")
        self.client.get("/my_test_route/")

        self.assertEqual(messages, [])


class PassThroughTests(SimpleTestCase):
    """The "Pass everything through" settings, the mapping table and `KeyedProxy`."""

    def test_settings_block_turns_pass_through_on_for_the_whole_server(self):
        namespace = run_readme_block("PROXY_PASS_THROUGH = True")

        self.assertIs(namespace["PROXY_PASS_THROUGH"], True)
        self.assertEqual(namespace["PROXY_TARGET_PATH"], "")

    @mock.patch("django_simple_api_proxy.views.requests.request")
    def test_path_table_rows(self, upstream):
        class PassThrough(APIProxy):
            pass_through = True

        upstream.return_value = mock.Mock(status_code=200, content=b"", reason="OK",
                                          headers={}, raw=None)
        factory = APIRequestFactory()
        for path, url in [
            ("/my_test_route", "https://httpbin.org/get"),
            ("/my_test_route/", "https://httpbin.org/get/"),
            ("/my_test_route/items/42/?a=1", "https://httpbin.org/get/items/42/?a=1"),
        ]:
            with self.subTest(path=path):
                PassThrough.as_view()(factory.get(path))
                self.assertEqual(upstream.call_args.kwargs["url"], url)
        self.assertIn("| `GET /my_test_route/items/42/?a=1` | "
                      "`https://httpbin.org/get/items/42?a=1&username=%23anonymous` | "
                      "`https://httpbin.org/get/items/42/?a=1` |", README)

    def test_keyed_proxy_adds_the_key_and_keeps_cookies_home(self):
        upstream = Upstream.start()
        self.addCleanup(upstream.stop)
        keyed = run_readme_block("class KeyedProxy(APIProxy)")["KeyedProxy"]
        request = APIRequestFactory().get("/keyed/echo", HTTP_COOKIE="sessionid=abc",
                                          HTTP_X_HELLO="world")

        with mock.patch.object(keyed, "target_api_url", upstream.url),                 override_settings(UPSTREAM_API_KEY="demo-key"):
            resp = keyed.as_view()(request)

        headers = echoed(resp)[2]
        self.assertEqual(headers["authorization"], ["Bearer demo-key"])
        self.assertEqual(headers["x-hello"], ["world"])
        self.assertNotIn("cookie", headers)


class ImageTests(SimpleTestCase):

    def test_hero_has_a_light_and_a_dark_version(self):
        self.assertIn('<source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/NatLee/django-simple-api-proxy/main/docs/images/hero-dark.webp">', README)
        self.assertIn('<img src="https://raw.githubusercontent.com/NatLee/django-simple-api-proxy/main/docs/images/hero-light.webp"', README)

    def test_images_from_this_repo_exist(self):
        # Absolute URLs, so the images also show on PyPI.
        prefix = "https://raw.githubusercontent.com/NatLee/django-simple-api-proxy/main/"
        paths = re.findall(re.escape(prefix) + r"""([^)\s"']+)""", README)

        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())
