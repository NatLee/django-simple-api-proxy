"""Proxy behaviour, checked through the URL setup from the README.

The upstream API is replaced by a fake `requests.request`, so no network is needed.
"""
import json
import warnings
from unittest import mock

import requests
from django.test import RequestFactory, SimpleTestCase

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
