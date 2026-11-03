"""Proxy behaviour, checked through the URL setup from the README.

The upstream API is replaced by a fake `requests.request`, so no network is needed.
"""
import json
import warnings
from unittest import mock

import requests
from django.contrib.auth.models import User
from django.test import RequestFactory, SimpleTestCase
from loguru import logger
from rest_framework.test import APIRequestFactory, force_authenticate

from django_simple_api_proxy.views import APIProxy

UPSTREAM = "https://httpbin.org"


def upstream_reply(payload):
    """A fake upstream response whose body is `payload` as JSON."""
    reply = mock.Mock(status_code=200)
    reply.json.return_value = payload
    return reply


def upstream_reply_not_json():
    """A fake upstream response whose body is not JSON (e.g. an HTML 404 page)."""
    reply = mock.Mock(status_code=404)
    reply.json.side_effect = ValueError("Expecting value")
    return reply


@mock.patch("django_simple_api_proxy.views.requests.request")
class ProxyGetTests(SimpleTestCase):

    def test_route_with_trailing_slash_goes_to_target_path(self, upstream):
        upstream.return_value = upstream_reply({"from": "upstream"})

        resp = self.client.get("/my_test_route/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"from": "upstream"})
        sent = upstream.call_args.kwargs
        self.assertEqual(sent["method"], "GET")
        self.assertEqual(sent["url"], UPSTREAM + "/get")
        self.assertEqual(sent["params"], {"username": "#anonymous"})

    def test_route_without_trailing_slash_goes_to_target_path(self, upstream):
        upstream.return_value = upstream_reply({})

        resp = self.client.get("/my_test_route")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(upstream.call_args.kwargs["url"], UPSTREAM + "/get")

    def test_sub_path_and_query_are_forwarded(self, upstream):
        upstream.return_value = upstream_reply({})

        self.client.get("/my_test_route/items/123/?a=1&a=2")

        sent = upstream.call_args.kwargs
        self.assertEqual(sent["url"], UPSTREAM + "/get/items/123")
        self.assertEqual(sent["params"], {"a": ["1", "2"], "username": "#anonymous"})

    def test_client_cannot_choose_the_username(self, upstream):
        upstream.return_value = upstream_reply({})

        self.client.get("/my_test_route/?username=admin")

        self.assertEqual(upstream.call_args.kwargs["params"]["username"], "#anonymous")

    def test_upstream_list_and_non_ascii_are_returned_as_is(self, upstream):
        upstream.return_value = upstream_reply([{"name": "測試"}])

        resp = self.client.get("/my_test_route/")

        self.assertEqual(resp.json(), [{"name": "測試"}])
        self.assertIn("測試", resp.content.decode("utf-8"))

    def test_upstream_not_json_returns_error_status(self, upstream):
        upstream.return_value = upstream_reply_not_json()

        resp = self.client.get("/my_test_route/123")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "error"})

    def test_upstream_unreachable_returns_error_status(self, upstream):
        upstream.side_effect = requests.ConnectionError("no route to host")

        resp = self.client.get("/my_test_route/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "error"})

    def test_upstream_request_has_a_timeout_and_checks_tls(self, upstream):
        upstream.return_value = upstream_reply({})

        self.client.get("/my_test_route/")

        sent = upstream.call_args.kwargs
        self.assertEqual(sent["timeout"], 180)
        self.assertIs(sent["verify"], True)
        self.assertIsNone(sent["data"])
        self.assertIsNone(sent["json"])

    def test_client_headers_are_not_forwarded(self, upstream):
        upstream.return_value = upstream_reply({})

        self.client.get("/my_test_route/", HTTP_AUTHORIZATION="Bearer secret",
                        HTTP_COOKIE="sessionid=abc")

        self.assertNotIn("headers", upstream.call_args.kwargs)
        self.assertNotIn("cookies", upstream.call_args.kwargs)

    def test_head_is_forwarded_as_get(self, upstream):
        upstream.return_value = upstream_reply({"from": "upstream"})

        resp = self.client.head("/my_test_route/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(upstream.call_args.kwargs["method"], "GET")

    def test_stock_view_sends_anonymous_even_for_logged_in_users(self, upstream):
        # The stock view has no authentication classes, so Django REST framework
        # never looks at the user Django's AuthenticationMiddleware found.
        upstream.return_value = upstream_reply({})
        request = RequestFactory().get("/my_test_route/")
        request.user = User(username="alice")

        APIProxy.as_view()(request, resource="")

        self.assertEqual(upstream.call_args.kwargs["params"], {"username": "#anonymous"})


@mock.patch("django_simple_api_proxy.views.requests.request")
class OtherMethodTests(SimpleTestCase):

    def test_options_is_answered_without_calling_upstream(self, upstream):
        resp = self.client.options("/my_test_route/")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Allow"], "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS")
        # Django REST framework shows the class docstring here.
        self.assertEqual(resp.json()["description"], "API proxy")
        upstream.assert_not_called()

    def test_other_methods_are_not_allowed(self, upstream):
        resp = self.client.generic("TRACE", "/my_test_route/")

        self.assertEqual(resp.status_code, 405)
        upstream.assert_not_called()


@mock.patch("django_simple_api_proxy.views.requests.request")
class ProxyBodyMethodTests(SimpleTestCase):

    def send(self, method, path, body=None, **extra):
        data = json.dumps(body) if body is not None else ""
        return getattr(self.client, method.lower())(
            path, data=data, content_type="application/json", **extra
        )

    def test_json_body_is_forwarded_with_username(self, upstream):
        for method in ["POST", "PUT", "PATCH", "DELETE"]:
            with self.subTest(method=method):
                upstream.reset_mock()
                upstream.return_value = upstream_reply({"method": method})

                resp = self.send(method, "/my_test_route/things", {"hello": "world"})

                self.assertEqual(resp.json(), {"method": method})
                sent = upstream.call_args.kwargs
                self.assertEqual(sent["method"], method)
                self.assertEqual(sent["url"], UPSTREAM + "/get/things")
                self.assertEqual(sent["json"], {"hello": "world", "username": "#anonymous"})

    def test_empty_body_sends_only_username(self, upstream):
        upstream.return_value = upstream_reply({})

        self.send("POST", "/my_test_route/")

        self.assertEqual(upstream.call_args.kwargs["json"], {"username": "#anonymous"})

    def test_query_string_is_not_forwarded(self, upstream):
        upstream.return_value = upstream_reply({})

        self.send("DELETE", "/my_test_route/things?id=3", {"hello": "world"})

        sent = upstream.call_args.kwargs
        self.assertEqual(sent["url"], UPSTREAM + "/get/things")
        self.assertIsNone(sent["params"])

    def test_body_that_is_not_json_returns_error_status(self, upstream):
        resp = self.client.post("/my_test_route/", data="a=1",
                                content_type="application/x-www-form-urlencoded")

        self.assertEqual(resp.json(), {"status": "error"})
        upstream.assert_not_called()

    def test_json_body_that_is_not_an_object_returns_error_status(self, upstream):
        resp = self.send("POST", "/my_test_route/", [1, 2])

        self.assertEqual(resp.json(), {"status": "error"})
        upstream.assert_not_called()

    def test_logged_in_user_name_is_sent_in_json_body(self, upstream):
        upstream.return_value = upstream_reply({})
        request = APIRequestFactory().post("/my_test_route/", {"q": 1}, format="json")
        force_authenticate(request, user=User(username="alice"))

        APIProxy.as_view()(request, resource="")

        self.assertEqual(upstream.call_args.kwargs["json"], {"q": 1, "username": "alice"})


@mock.patch("django_simple_api_proxy.views.requests.request")
class SubclassTests(SimpleTestCase):
    """What the README says a subclass can change."""

    def test_overridden_hooks_are_used_for_every_method(self, upstream):
        class Customised(APIProxy):
            def update_payload(self, request, params):
                params = super().update_payload(request, params)
                params["extra"] = "yes"
                return params

            def response(self, resp):
                return super().response({"wrapped": resp})

        factory = APIRequestFactory()
        for method in ["GET", "POST", "PUT", "PATCH", "DELETE"]:
            with self.subTest(method=method):
                upstream.reset_mock()
                upstream.return_value = upstream_reply({"method": method})
                build = getattr(factory, method.lower())
                if method == "GET":
                    request = build("/my_test_route/")
                else:
                    request = build("/my_test_route/", {}, format="json")

                resp = Customised.as_view()(request, resource="")

                self.assertEqual(json.loads(resp.content), {"wrapped": {"method": method}})
                sent = upstream.call_args.kwargs
                forwarded = sent["params"] if method == "GET" else sent["json"]
                self.assertEqual(forwarded, {"username": "#anonymous", "extra": "yes"})

    def test_class_attributes_point_a_subclass_at_another_api(self, upstream):
        class OtherAPI(APIProxy):
            target_api_url = "https://api.example.com"
            proxy_route_path = "other"
            proxy_target_path = "v1"

        upstream.return_value = upstream_reply({})
        request = APIRequestFactory().get("/other/items/7")

        OtherAPI.as_view()(request, resource="/items/7")

        self.assertEqual(upstream.call_args.kwargs["url"], "https://api.example.com/v1/items/7")


@mock.patch("django_simple_api_proxy.views.requests.request")
class LoggingTests(SimpleTestCase):

    def capture(self, level):
        messages = []
        sink = logger.add(messages.append, level=level, format="{message}")
        self.addCleanup(logger.remove, sink)
        return messages

    def test_failures_are_logged_as_errors_with_the_traceback(self, upstream):
        upstream.side_effect = requests.ConnectionError("no route to host")
        errors = self.capture("ERROR")

        self.client.get("/my_test_route/")

        self.assertEqual(len(errors), 1)
        record = errors[0].record
        self.assertEqual(record["level"].name, "ERROR")
        self.assertIs(record["exception"].type, requests.ConnectionError)
        self.assertEqual(record["message"],
                         'Could not proxy GET /my_test_route/, replying {"status": "error"}')

    def test_debug_log_says_what_was_forwarded_where(self, upstream):
        upstream.return_value = upstream_reply({})
        messages = self.capture("DEBUG")

        self.client.post("/my_test_route/things", data='{"a": 1}',
                         content_type="application/json")

        self.assertEqual([m.record["message"] for m in messages], [
            "Proxy POST /my_test_route/things",
            "JSON body fields: ['a']",
            "Upstream URL: https://httpbin.org/get/things",
            "Username: #anonymous",
        ])

    def test_body_values_never_reach_the_log(self, upstream):
        upstream.return_value = upstream_reply({})
        messages = self.capture("DEBUG")
        body = {"password": "hunter2", "profile": {"token": "s3cret-token"}}

        for method in ["POST", "PUT", "PATCH", "DELETE"]:
            self.client.generic(method, "/my_test_route/", json.dumps(body),
                                content_type="application/json")

        log = "\n".join(messages)
        self.assertEqual(log.count("JSON body fields: ['password', 'profile']"), 4)
        self.assertNotIn("hunter2", log)
        self.assertNotIn("s3cret-token", log)

    def test_body_that_is_not_an_object_is_not_logged(self, upstream):
        messages = self.capture("DEBUG")

        self.client.post("/my_test_route/", data='["hunter2"]', content_type="application/json")

        # The proxy's own lines only: this request fails, and the traceback
        # is up to the handler (see the diagnose test below).
        lines = [m.record["message"] for m in messages]
        self.assertIn("JSON body fields: (not a JSON object)", lines)
        self.assertNotIn("hunter2", "\n".join(lines))

    def test_failed_request_log_has_no_body_values_without_diagnose(self, upstream):
        # loguru's default handler prints variable values in tracebacks
        # (diagnose=True); the README tells users to turn that off.
        upstream.side_effect = requests.ConnectionError("no route to host")
        output = []
        sink = logger.add(output.append, level="DEBUG", diagnose=False, backtrace=False,
                          format="{level} {message}\n{exception}")
        self.addCleanup(logger.remove, sink)

        self.client.post("/my_test_route/", data='{"password": "hunter2"}',
                         content_type="application/json")

        log = "".join(output)
        self.assertIn("ERROR Could not proxy POST /my_test_route/", log)
        self.assertIn("Traceback", log)
        self.assertIn("ConnectionError: no route to host", log)
        self.assertNotIn("hunter2", log)

    def test_debug_log_can_be_turned_off(self, upstream):
        upstream.return_value = upstream_reply({})
        messages = self.capture("DEBUG")

        self.client.get("/my_test_route/")
        self.assertTrue(messages)
        self.assertTrue(all(m.record["name"].startswith("django_simple_api_proxy")
                            for m in messages))

        messages.clear()
        logger.disable("django_simple_api_proxy")
        self.addCleanup(logger.enable, "django_simple_api_proxy")
        self.client.get("/my_test_route/")
        self.assertEqual(messages, [])


@mock.patch("django_simple_api_proxy.views.requests.request")
class OpenDecisionTests(SimpleTestCase):
    """Current behaviour the owner has not decided to change yet.

    The README lists these under "Known limitations". If one is changed on purpose,
    change its test with it. (The third one, query strings dropped for POST, PUT,
    PATCH and DELETE, is `ProxyBodyMethodTests.test_query_string_is_not_forwarded`.)
    """

    def test_upstream_status_code_is_not_passed_on(self, upstream):
        upstream.return_value = upstream_reply({"detail": "Not found"})
        upstream.return_value.status_code = 404

        resp = self.client.get("/my_test_route/missing")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"detail": "Not found"})

    def test_route_path_with_trailing_slash_builds_wrong_url(self, upstream):
        class TrailingSlash(APIProxy):
            proxy_route_path = "my_test_route/"

        request = RequestFactory().get("/my_test_route/abc")

        self.assertEqual(TrailingSlash().get_proxy_path(request), UPSTREAM + "/getabc")


class ParsePathTests(SimpleTestCase):

    def test_no_deprecation_warning_on_python_3_13_and_later(self):
        # Python 3.13 deprecated passing re.sub()'s count positionally; the proxy
        # did that on every request.
        request = RequestFactory().get("/my_test_route/abc")

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            self.assertEqual(APIProxy().parse_path(request), "/get/abc")


class UpdatePayloadTests(SimpleTestCase):

    def test_logged_in_user_name_is_sent(self):
        request = mock.Mock()
        request.user.username = "alice"

        self.assertEqual(APIProxy().update_payload(request, {"q": 1}),
                         {"q": 1, "username": "alice"})

    def test_user_model_without_username_field_sends_its_username(self):
        # Before 1.1, a custom user model with USERNAME_FIELD = "email" and no
        # `username` made every logged-in request end in {"status": "error"}.
        class EmailUser:
            is_authenticated = True

            def get_username(self):
                return "alice@example.com"

        request = mock.Mock(user=EmailUser())

        self.assertEqual(APIProxy().update_payload(request, {}),
                         {"username": "alice@example.com"})

    @mock.patch("django_simple_api_proxy.views.requests.request")
    def test_user_model_without_username_field_is_proxied(self, upstream):
        class EmailUser:
            is_authenticated = True
            is_active = True

            def get_username(self):
                return "alice@example.com"

        upstream.return_value = upstream_reply({"ok": True})
        request = APIRequestFactory().get("/my_test_route/")
        force_authenticate(request, user=EmailUser())

        resp = APIProxy.as_view()(request, resource="")

        self.assertEqual(json.loads(resp.content), {"ok": True})
        self.assertEqual(upstream.call_args.kwargs["params"], {"username": "alice@example.com"})
