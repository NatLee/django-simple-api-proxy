"""Pass-through mode, checked end to end against a real HTTP server on 127.0.0.1.

Requests go through Django's test client, the proxy, requests and a socket to
`tests.upstream`, and the reply comes all the way back. No internet needed.
"""
import gzip
import json
from unittest import mock
from urllib.parse import quote, urlencode

import requests
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from loguru import logger
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.test import APIRequestFactory

from django_simple_api_proxy.views import APIProxy
from tests.upstream import GZIPPED_TEXT, PNG, Upstream, echoed
from tests.urls_pass_through import PassThroughProxy


class UpstreamTestCase(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.upstream = Upstream.start()
        cls.addClassCleanup(cls.upstream.stop)
        patcher = mock.patch.object(PassThroughProxy, "target_api_url", cls.upstream.url)
        patcher.start()
        cls.addClassCleanup(patcher.stop)


@override_settings(ROOT_URLCONF="tests.urls_pass_through")
class MethodTests(UpstreamTestCase):

    def test_every_method_goes_through_as_itself(self):
        for method in ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]:
            with self.subTest(method=method):
                resp = self.client.generic(method, "/my_test_route/echo")

                self.assertEqual(resp.status_code, 200)
                self.assertEqual(echoed(resp)[0], method)

    def test_head_goes_through_as_head(self):
        resp = self.client.head("/my_test_route/status/200")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Upstream"], "yes")
        self.assertEqual(resp.content, b"")
        # No body to decode, so the API server's length stays.
        self.assertEqual(resp["Content-Length"], str(len(b"upstream says 200")))

    def test_trace_is_not_allowed(self):
        resp = self.client.generic("TRACE", "/my_test_route/echo")

        self.assertEqual(resp.status_code, 405)


@override_settings(ROOT_URLCONF="tests.urls_pass_through")
class PathAndQueryTests(UpstreamTestCase):

    def test_query_string_goes_through_exactly(self):
        query = "a=1&a=2&b=x%20y&c&q=%E6%B8%AC"
        for method in ["GET", "POST", "DELETE"]:
            with self.subTest(method=method):
                resp = self.client.generic(method, "/my_test_route/echo?" + query)

                self.assertEqual(echoed(resp)[1], "/echo?" + query)

    def test_nothing_is_added_to_the_query_or_body(self):
        method, path, headers, body = echoed(self.client.get("/my_test_route/echo"))

        self.assertEqual(path, "/echo")
        self.assertEqual(body, b"")

    def test_path_is_kept_with_its_trailing_slash(self):
        for path, upstream_path in [
            ("/my_test_route", "/"),
            ("/my_test_route/", "/"),
            ("/my_test_route/echo/", "/echo/"),
            ("/my_test_route/echo/items/7", "/echo/items/7"),
            ("/my_test_route/echo/a%20b", "/echo/a%20b"),
        ]:
            with self.subTest(path=path):
                self.assertEqual(echoed(self.client.get(path))[1], upstream_path)

    def test_target_path_and_slashes_in_the_settings(self):
        class UnderEcho(PassThroughProxy):
            proxy_route_path = "/my_test_route/"
            proxy_target_path = "/echo/"
            target_api_url = self.upstream.url + "/"

        view = UnderEcho.as_view()
        factory = APIRequestFactory()
        for path, upstream_path in [
            ("/my_test_route", "/echo"),
            ("/my_test_route/", "/echo/"),
            ("/my_test_route/items/7/", "/echo/items/7/"),
        ]:
            with self.subTest(path=path):
                self.assertEqual(echoed(view(factory.get(path)))[1], upstream_path)

    def test_route_is_matched_as_text_not_as_a_pattern(self):
        class Dotted(PassThroughProxy):
            proxy_route_path = "api.v1"

        view = Dotted.as_view()
        factory = APIRequestFactory()

        self.assertEqual(echoed(view(factory.get("/api.v1/echo")))[1], "/echo")
        self.assertEqual(view(factory.get("/apixv1/echo")).status_code, 404)

    def test_paths_that_only_start_like_the_route_are_not_forwarded(self):
        with mock.patch("django_simple_api_proxy.views.requests.request") as upstream:
            resp = self.client.get("/my_test_routeXYZ")

        self.assertEqual(resp.status_code, 404)
        upstream.assert_not_called()


@override_settings(ROOT_URLCONF="tests.urls_pass_through")
class RequestHeaderTests(UpstreamTestCase):

    def test_end_to_end_headers_go_through(self):
        resp = self.client.get(
            "/my_test_route/echo",
            HTTP_X_HELLO="world", HTTP_AUTHORIZATION="Bearer t0ken",
            HTTP_COOKIE="a=1", HTTP_ACCEPT="application/xml", HTTP_USER_AGENT="my-app/1.0",
        )

        headers = echoed(resp)[2]
        self.assertEqual(headers["x-hello"], ["world"])
        self.assertEqual(headers["authorization"], ["Bearer t0ken"])
        self.assertEqual(headers["cookie"], ["a=1"])
        self.assertEqual(headers["user-agent"], ["my-app/1.0"])
        # Django REST framework can't render XML; the API server decides, not a 406.
        self.assertEqual(headers["accept"], ["application/xml"])

    def test_hop_by_hop_headers_stay_behind(self):
        resp = self.client.get(
            "/my_test_route/echo",
            HTTP_CONNECTION="X-Hop-Secret", HTTP_X_HOP_SECRET="1", HTTP_KEEP_ALIVE="300",
            HTTP_PROXY_AUTHORIZATION="Basic cHJveHk6cGFzcw==", HTTP_TE="trailers",
            HTTP_UPGRADE="websocket", HTTP_EXPECT="100-continue",
        )

        headers = echoed(resp)[2]
        for name in ["x-hop-secret", "keep-alive", "proxy-authorization", "te", "upgrade",
                     "expect"]:
            self.assertNotIn(name, headers)
        # requests' own connection, not the client's.
        self.assertEqual(headers["connection"], ["keep-alive"])

    def test_host_and_accept_encoding_are_requests_own(self):
        resp = self.client.get("/my_test_route/echo", HTTP_ACCEPT_ENCODING="br;q=1, identity")

        headers = echoed(resp)[2]
        self.assertEqual(headers["host"], [self.upstream.url.split("//")[1]])
        self.assertNotEqual(headers["accept-encoding"], ["br;q=1, identity"])

    def test_runserver_content_type_on_bodyless_requests_is_dropped(self):
        # runserver sets CONTENT_TYPE=text/plain on requests that have none.
        resp = self.client.get("/my_test_route/echo", CONTENT_TYPE="text/plain")

        self.assertNotIn("content-type", echoed(resp)[2])

    def test_override_adds_a_header_and_drops_cookies(self):
        class WithKey(PassThroughProxy):
            def upstream_headers(self, request):
                headers = super().upstream_headers(request)
                headers.pop("Cookie", None)
                headers["X-Api-Key"] = "server-side-secret"
                return headers

        request = APIRequestFactory().get("/my_test_route/echo", HTTP_COOKIE="sessionid=abc")

        headers = echoed(WithKey.as_view()(request))[2]
        self.assertEqual(headers["x-api-key"], ["server-side-secret"])
        self.assertNotIn("cookie", headers)


@override_settings(ROOT_URLCONF="tests.urls_pass_through")
class RequestBodyTests(UpstreamTestCase):

    def assertBodyArrives(self, resp, body, content_type):
        method, path, headers, received = echoed(resp)
        self.assertEqual(received, body)
        self.assertEqual(headers["content-type"], [content_type])
        self.assertEqual(headers["content-length"], [str(len(body))])

    def test_json_body_goes_through_byte_for_byte(self):
        body = b'{"b": 2,  "a": [1, 2], "username": "the client\'s"}'

        resp = self.client.post("/my_test_route/echo", data=body, content_type="application/json")

        self.assertBodyArrives(resp, body, "application/json")

    def test_json_array_goes_through(self):
        resp = self.client.put("/my_test_route/echo", data=b"[1, 2]", content_type="application/json")

        self.assertBodyArrives(resp, b"[1, 2]", "application/json")

    def test_form_body_goes_through(self):
        body = urlencode([("name", "nat"), ("tags", "a"), ("tags", "b")]).encode()

        resp = self.client.post("/my_test_route/echo", data=body,
                                content_type="application/x-www-form-urlencoded")

        self.assertBodyArrives(resp, body, "application/x-www-form-urlencoded")

    def test_file_upload_goes_through(self):
        upload = SimpleUploadedFile("logo.png", PNG, content_type="image/png")

        resp = self.client.post("/my_test_route/echo", data={"file": upload, "title": "logo"})

        method, path, headers, received = echoed(resp)
        self.assertTrue(headers["content-type"][0].startswith("multipart/form-data; boundary="))
        self.assertIn(PNG, received)
        self.assertIn(b'name="title"', received)

    def test_file_upload_survives_authentication_that_reads_the_form(self):
        # SessionAuthentication's CSRF check reads request.POST; after that a
        # multipart body can only be forwarded if the proxy read it first.
        class ReadsTheForm(BaseAuthentication):
            def authenticate(self, request):
                request.POST
                return None

        class Guarded(PassThroughProxy):
            authentication_classes = [ReadsTheForm]

        upload = SimpleUploadedFile("logo.png", PNG, content_type="image/png")
        request = APIRequestFactory().post("/my_test_route/echo", {"file": upload},
                                           format="multipart")

        resp = Guarded.as_view()(request)

        self.assertEqual(resp.status_code, 200)
        self.assertIn(PNG, echoed(resp)[3])

    def test_body_over_the_upload_limit_is_a_400(self):
        # Django refuses it while the proxy reads the body; before this was fixed,
        # DEBUG mode turned that into a 500.
        with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=10),                 mock.patch("django_simple_api_proxy.views.requests.request") as upstream:
            resp = self.client.post("/my_test_route/echo", data=b"x" * 11,
                                    content_type="application/octet-stream")

        self.assertEqual(resp.status_code, 400)
        upstream.assert_not_called()

    def test_binary_body_goes_through(self):
        body = bytes(range(256))

        resp = self.client.patch("/my_test_route/echo", data=body,
                                 content_type="application/octet-stream")

        self.assertBodyArrives(resp, body, "application/octet-stream")


@override_settings(ROOT_URLCONF="tests.urls_pass_through")
class ResponseTests(UpstreamTestCase):

    def test_status_codes_come_back(self):
        for status in [201, 202, 400, 401, 403, 404, 409, 418, 422, 429, 500, 503]:
            with self.subTest(status=status):
                resp = self.client.get(f"/my_test_route/status/{status}")

                self.assertEqual(resp.status_code, status)
                self.assertEqual(resp.content, f"upstream says {status}".encode())

    def test_no_content_comes_back_without_a_content_type(self):
        resp = self.client.delete("/my_test_route/status/204")

        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp.content, b"")
        self.assertFalse(resp.has_header("Content-Type"))

    def test_not_modified_comes_back(self):
        resp = self.client.get("/my_test_route/etag", HTTP_IF_NONE_MATCH='"v1"')

        self.assertEqual(resp.status_code, 304)
        self.assertEqual(resp["ETag"], '"v1"')

    def test_headers_and_content_type_come_back(self):
        resp = self.client.get("/my_test_route/status/405")

        self.assertEqual(resp.status_code, 405)
        self.assertEqual(resp["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(resp["X-Upstream"], "yes")
        # Django REST framework would otherwise say what *it* allows, and add Accept.
        self.assertEqual(resp["Allow"], "GET")
        self.assertEqual(resp["Vary"], "Origin")
        # Your web server sets these itself.
        self.assertFalse(resp.has_header("Server"))
        self.assertFalse(resp.has_header("Date"))

    def test_hop_by_hop_response_headers_stay_behind(self):
        resp = self.client.get("/my_test_route/hop")

        self.assertEqual(resp["X-End-To-End"], "kept")
        for name in ["Connection", "Keep-Alive", "X-Hop-Secret"]:
            self.assertFalse(resp.has_header(name), name)

    def test_binary_reply_comes_back(self):
        resp = self.client.get("/my_test_route/png")

        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertEqual(resp.content, PNG)

    def test_compressed_reply_comes_back_decoded(self):
        resp = self.client.get("/my_test_route/gzip", HTTP_ACCEPT_ENCODING="gzip")

        self.assertEqual(resp.content, GZIPPED_TEXT.encode())
        self.assertFalse(resp.has_header("Content-Encoding"))
        self.assertNotEqual(resp.content, gzip.compress(GZIPPED_TEXT.encode()))

    def test_every_cookie_comes_back(self):
        resp = self.client.get("/my_test_route/cookies")

        self.assertEqual(resp.cookies["theme"].value, "dark")
        self.assertEqual(resp.cookies["theme"]["path"], "/")
        session = resp.cookies["session"]
        self.assertEqual(session.value, "abc123")
        self.assertTrue(session["httponly"])
        self.assertEqual(session["samesite"], "Lax")

    def test_json_reply_is_not_rewritten(self):
        resp = self.client.get("/my_test_route/echo")

        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertEqual(json.loads(resp.content)["method"], "GET")


class FailureTests(UpstreamTestCase):

    def capture_errors(self):
        errors = []
        sink = logger.add(errors.append, level="ERROR", format="{message}")
        self.addCleanup(logger.remove, sink)
        return errors

    def test_timeout_is_a_504(self):
        class Impatient(PassThroughProxy):
            def send_request(self, *args, **kwargs):
                return super().send_request(*args, **{**kwargs, "timeout": 0.2})

        errors = self.capture_errors()

        resp = Impatient.as_view()(APIRequestFactory().get("/my_test_route/slow"))

        self.assertEqual(resp.status_code, 504)
        self.assertEqual(json.loads(resp.content), {"status": "error"})
        self.assertEqual(
            [e.record["message"] for e in errors],
            ['Could not proxy GET /my_test_route/slow: the API server did not answer in time. '
             'Replying 504 {"status": "error"}'],
        )

    def test_unreachable_api_server_is_a_502(self):
        class Nowhere(PassThroughProxy):
            target_api_url = "http://127.0.0.1:9"  # the discard port; nothing listens

        errors = self.capture_errors()

        resp = Nowhere.as_view()(APIRequestFactory().get("/my_test_route/"))

        self.assertEqual(resp.status_code, 502)
        self.assertEqual(json.loads(resp.content), {"status": "error"})
        self.assertIs(errors[0].record["exception"].type, requests.ConnectionError)
        self.assertIn("could not connect to the API server. Replying 502", errors[0])

    def test_login_in_front_still_works(self):
        class LoginRequired(PassThroughProxy):
            permission_classes = [IsAuthenticated]

        with mock.patch("django_simple_api_proxy.views.requests.request") as upstream:
            resp = LoginRequired.as_view()(APIRequestFactory().get("/my_test_route/echo"))

        self.assertEqual(resp.status_code, 403)
        upstream.assert_not_called()

    def test_proxy_replies_keep_their_headers_drf_replies_get_drf_headers(self):
        class Nowhere(PassThroughProxy):
            target_api_url = "http://127.0.0.1:9"  # the discard port; nothing listens

        class LoginRequired(PassThroughProxy):
            permission_classes = [IsAuthenticated]

        gateway_error = Nowhere.as_view()(APIRequestFactory().get("/my_test_route/"))
        refused = LoginRequired.as_view()(APIRequestFactory().get("/my_test_route/"))

        self.assertEqual(gateway_error.status_code, 502)
        self.assertFalse(gateway_error.has_header("Allow"))
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused["Allow"], "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS")


class LoggingTests(UpstreamTestCase):

    def test_log_names_headers_but_never_shows_values_or_bodies(self):
        messages = []
        sink = logger.add(messages.append, level="DEBUG", format="{message}")
        self.addCleanup(logger.remove, sink)
        request = APIRequestFactory().post(
            "/my_test_route/echo", data='{"password": "hunter2"}', content_type="application/json",
            HTTP_AUTHORIZATION="Bearer s3cret-token",
        )

        PassThroughProxy.as_view()(request)

        log = "".join(messages)
        self.assertIn("'Authorization'", log)
        self.assertIn("23 bytes of body", log)
        self.assertIn("API server answered 200 (application/json,", log)
        self.assertNotIn("hunter2", log)
        self.assertNotIn("s3cret-token", log)


def redirect_to(location, status=302, path="/my_test_route/redirect"):
    """A path on the proxy that makes the local API server answer `status` to `location`."""
    return f"{path}/{status}?location={quote(location, safe='')}"


@override_settings(ROOT_URLCONF="tests.urls_pass_through")
class RedirectTests(UpstreamTestCase):
    """Pass-through mode hands redirects to the client, pointed back through the proxy."""

    def test_every_redirect_status_comes_back_unfollowed(self):
        for status in [301, 302, 303, 307, 308]:
            with self.subTest(status=status):
                resp = self.client.post(redirect_to("/echo/next", status))

                self.assertEqual(resp.status_code, status)
                self.assertEqual(resp.content, b"moved")  # the redirect itself, not /echo/next
                self.assertEqual(resp["Location"], "/my_test_route/echo/next")

    def test_absolute_location_on_the_api_server_goes_through_the_route(self):
        resp = self.client.get(redirect_to(self.upstream.url + "/echo/next?x=1#top"))

        self.assertEqual(resp["Location"], "/my_test_route/echo/next?x=1#top")

    def test_relative_locations_stay_relative(self):
        for location in ["next", "../list", "?page=2", "#part"]:
            with self.subTest(location=location):
                resp = self.client.get(redirect_to(location))

                self.assertEqual(resp["Location"], location)

    def test_locations_on_other_hosts_stay_as_they_are(self):
        for location in ["https://example.com/elsewhere?a=1", "//example.com/x",
                         self.upstream.url.replace("127.0.0.1", "localhost") + "/echo"]:
            with self.subTest(location=location):
                resp = self.client.get(redirect_to(location))

                self.assertEqual(resp["Location"], location)

    def test_cookies_on_the_redirect_come_back(self):
        resp = self.client.get(redirect_to("/echo/next"))

        self.assertEqual(resp.cookies["visited"].value, "yes")
        self.assertTrue(resp.cookies["visited"]["httponly"])

    def test_client_following_the_redirect_comes_back_through_the_proxy(self):
        resp = self.client.get(redirect_to(self.upstream.url + "/echo/next"), follow=True)

        self.assertEqual(resp.redirect_chain, [("/my_test_route/echo/next", 302)])
        self.assertEqual(echoed(resp)[1], "/echo/next")

    def test_location_of_other_replies_is_rewritten_too(self):
        resp = self.client.post(redirect_to("/echo/items/9", 201))

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp["Location"], "/my_test_route/echo/items/9")

    def test_target_path_is_swapped_back_for_the_route(self):
        class UnderEcho(PassThroughProxy):
            proxy_target_path = "echo"

        view = UnderEcho.as_view()
        factory = APIRequestFactory()
        for location, expected in [
            (self.upstream.url + "/echo/a?b=1", "/my_test_route/a?b=1"),
            ("/echo/a", "/my_test_route/a"),
            ("/echo", "/my_test_route"),
            # On the API server but outside /echo: the client has to go there itself.
            ("/login", self.upstream.url + "/login"),
            ("/echoes", self.upstream.url + "/echoes"),
            (self.upstream.url + "/login", self.upstream.url + "/login"),
        ]:
            with self.subTest(location=location):
                resp = view(factory.get(redirect_to(location)))

                self.assertEqual(resp.status_code, 302)
                self.assertEqual(resp["Location"], expected)

    def test_route_under_a_prefix_keeps_the_prefix(self):
        request = APIRequestFactory().get(
            redirect_to("/echo/n", path="/api/my_test_route/redirect"))

        resp = PassThroughProxy.as_view()(request)

        self.assertEqual(resp["Location"], "/api/my_test_route/echo/n")


class JSONModeRedirectTests(UpstreamTestCase):
    """JSON mode keeps following redirects on the server, as 1.x did."""

    class JSONMode(APIProxy):
        pass_through = False
        proxy_target_path = "echo"

    def test_json_mode_follows_redirects(self):
        with mock.patch.object(self.JSONMode, "target_api_url", self.upstream.url):
            resp = self.JSONMode.as_view()(
                APIRequestFactory().get(redirect_to("/echo/after")))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["path"], "/echo/after")

    def test_json_mode_calls_requests_as_before(self):
        with mock.patch("django_simple_api_proxy.views.requests.request") as upstream:
            upstream.return_value.json.return_value = {}
            self.JSONMode.as_view()(APIRequestFactory().get("/my_test_route/"))

        self.assertNotIn("allow_redirects", upstream.call_args.kwargs)
        self.assertNotIn("headers", upstream.call_args.kwargs)
