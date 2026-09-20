"""Network-free regression tests. Literals follow the documented API contract."""
import io
import json
import queue
import threading
import unittest
import urllib.error
from unittest.mock import patch

import sms_man_app as app


class ApiRegressionTests(unittest.TestCase):
    def test_limits_accepts_documented_list(self):
        session = FakeSession([[{"application_id": "297", "country_id": "140", "numbers": "17"}]])
        self.assertEqual(app.SmsManClient("dummy", session=session).get_limits(application_id="297", country_id="140"), 17)

    def test_errors_never_reflect_server_secret(self):
        session = FakeSession([app.HttpResponse(400, 'token=DUMMY-SECRET code=483921')])
        with self.assertRaises(app.ApiError) as ctx:
            app.SmsManClient("DUMMY-SECRET", session=session).get_limits(application_id="297", country_id="140")
        self.assertNotIn("DUMMY-SECRET", str(ctx.exception))
        self.assertNotIn("483921", str(ctx.exception))

    def test_unknown_api_error_code_is_not_reflected(self):
        with self.assertRaises(app.ApiError) as ctx:
            app.parse_get_number_response({"error_code": "DUMMY-SECRET", "error_msg": "DUMMY-SECRET"})
        self.assertNotIn("DUMMY-SECRET", str(ctx.exception))

    def test_limits_filters_other_country(self):
        s = FakeSession([[{"application_id": "297", "country_id": "1", "numbers": "17"}]])
        self.assertEqual(app.SmsManClient("dummy", session=s).get_limits(application_id="297", country_id="140"), 0)

    def test_limits_duplicate_or_invalid_rows_rejected(self):
        row = {"application_id": "297", "country_id": "140", "numbers": "17"}
        for payload in [[row, row], [{**row, "numbers": -1}], {"count": 17}, [None]]:
            with self.subTest(payload=payload), self.assertRaises(app.ApiError):
                app.SmsManClient("dummy", session=FakeSession([payload])).get_limits(application_id="297", country_id="140")

    def test_purchase_requires_price_and_valid_ids(self):
        for country, price, currency in [("0", "2", "RUB"), ("140", "NaN", "RUB"), ("140", "1.5", "RUB"), ("140", "2", "JPY")]:
            s = FakeSession([])
            with self.subTest(country=country, price=price), self.assertRaises(ValueError):
                app.SmsManClient("dummy", session=s).get_number(application_id="297", country_id=country, max_price=price, currency=currency)
            self.assertEqual(s.calls, [])

    def test_purchase_passes_server_price_cap(self):
        s = FakeSession([{"request_id": 55, "number": "15551234567"}])
        a = app.SmsManClient("dummy", session=s).get_number(application_id="297", country_id="140", max_price="2", currency="USD")
        self.assertEqual(a.request_id, "55")
        self.assertEqual(s.calls[0][1]["maxPrice"], "2")
        self.assertEqual(s.calls[0][1]["currency"], "USD")

    def test_non_numeric_sms_is_rejected(self):
        for value in [{}, ["483921"], "DUMMY-SECRET", "123456789012"]:
            with self.subTest(value=value), self.assertRaises(app.ApiError):
                app.parse_get_sms_response({"sms_code": value})

    def test_unexpected_purchase_payload_is_uncertain(self):
        with self.assertRaises(app.ApiError) as ctx:
            app.SmsManClient("dummy", session=FakeSession([{"request_id": 55}])).get_number(application_id="297", country_id="140", max_price="2", currency="RUB")
        self.assertTrue(ctx.exception.uncertain)

    def test_undocumented_purchase_aliases_do_not_confirm_purchase(self):
        with self.assertRaises(app.ApiError) as ctx:
            app.parse_get_number_response({"id": "99", "phone": "15551234567"})
        self.assertTrue(ctx.exception.uncertain)

    def test_empty_sms_response_is_not_assumed_waiting(self):
        for payload in ({}, {"sms_code": None}, {"sms": []}, {"sms": [{"text": "Reference 1234, no code"}]}, {"code": "1234"}, {"error_code": "no_sms"}):
            with self.subTest(payload=payload), self.assertRaises(app.ApiError):
                app.parse_get_sms_response(payload)

    def test_wrong_endpoint_rejected_before_transport(self):
        for url in ["http://api.sms-man.com/control", "https://example.org/control"]:
            with self.assertRaises(ValueError):
                app.SmsManClient("dummy", base_url=url)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params, timeout))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, app.HttpResponse) else app.HttpResponse(200, json.dumps(result))


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.now += seconds
        return False


class FakeClient:
    def __init__(self, sms=None):
        self.calls = []
        self.sms = sms
        self.on_buy = None
        self.on_sms = None
        self.buy_error = None
        self.reject_error = None

    def get_number(self, **kwargs):
        self.calls.append("buy")
        if self.on_buy:
            self.on_buy()
        if self.buy_error:
            raise self.buy_error
        return app.Activation(str(self.calls.count("buy")), "15551234567")

    def get_sms(self, request_id):
        self.calls.append("sms")
        if self.on_sms:
            self.on_sms()
        return self.sms

    def set_status(self, request_id, status):
        self.calls.append("reject:" + request_id)
        if self.reject_error:
            raise self.reject_error


class LifecycleTests(unittest.TestCase):
    def make(self, client=None, **options):
        clock = Clock()
        client = client or FakeClient()
        p = app.Poller(client, "297", "140", queue.Queue(), max_price="2", currency="RUB", clock=clock, **options)
        p.stop_event.wait = clock.wait
        return p, client, clock

    def test_timeout_default_does_not_repurchase(self):
        p, c, _ = self.make(sms_timeout=0)
        p._run()
        self.assertEqual(c.calls, ["buy", "reject:1"])
        self.assertEqual(p.state, "STOPPED")
        self.assertIsNone(p.activation)

    def test_auto_reissue_stops_at_purchase_cap(self):
        p, c, _ = self.make(sms_timeout=0, auto_reissue=True, max_activations=2)
        p._run()
        self.assertEqual(c.calls, ["buy", "reject:1", "buy", "reject:2"])

    def test_stop_during_purchase_cleans_late_activation(self):
        p, c, _ = self.make()
        c.on_buy = p.stop
        p._run()
        self.assertEqual(c.calls, ["buy", "reject:1"])
        self.assertEqual(p.state, "STOPPED")

    def test_stop_during_sms_does_not_emit_code(self):
        p, c, _ = self.make(FakeClient("483921"))
        c.on_sms = p.stop
        p._run()
        self.assertEqual(p.state, "STOPPED")
        self.assertFalse(any(e.kind == "code" for e in list(p.events.queue)))

    def test_late_sms_response_does_not_commit_expired_success(self):
        for limits in ({"sms_timeout": 5}, {"overall_timeout": 5}):
            with self.subTest(limits=limits):
                p, c, clock = self.make(FakeClient("483921"), **limits)
                c.on_sms = lambda: clock.wait(10)
                p._run()
                self.assertEqual(p.state, "STOPPED")
                self.assertEqual(c.calls, ["buy", "sms", "reject:1"])
                self.assertFalse(any(e.kind == "code" for e in list(p.events.queue)))

    def test_failed_reject_blocks_and_retains_activation(self):
        p, c, _ = self.make(sms_timeout=0, auto_reissue=True, max_activations=2)
        c.reject_error = app.ApiError("synthetic")
        p._run()
        self.assertEqual(p.state, "UNRESOLVED")
        self.assertEqual(p.activation.request_id, "1")
        self.assertEqual(c.calls.count("buy"), 1)

    def test_uncertain_purchase_blocks_new_purchase(self):
        p, c, _ = self.make()
        c.buy_error = TimeoutError("synthetic secret")
        p._run()
        self.assertEqual(p.state, "UNRESOLVED")
        self.assertEqual(c.calls, ["buy"])
        self.assertNotIn("synthetic secret", str(list(p.events.queue)))

    def test_no_inventory_retries_are_bounded(self):
        p, c, clock = self.make(max_attempts=3)
        c.buy_error = app.ApiError("no inventory", retryable=True)
        p._run()
        self.assertEqual(c.calls, ["buy"] * 3)
        self.assertEqual(clock.now, 6)

    def test_overall_deadline_blocks_next_purchase(self):
        p, c, _ = self.make(overall_timeout=5)
        c.buy_error = app.ApiError("no inventory", retryable=True)
        p._run()
        self.assertEqual(c.calls, ["buy", "buy"])

    def test_success_retains_result_and_no_repurchase(self):
        p, c, _ = self.make(FakeClient("483921"), auto_reissue=True, max_activations=5)
        p._run()
        self.assertEqual(p.state, "SUCCESS")
        self.assertEqual(c.calls, ["buy", "sms"])
        self.assertEqual([e.value for e in list(p.events.queue) if e.kind == "code"], ["483921"])

    def test_reject_only_signals_worker(self):
        p, c, _ = self.make()
        p.activation = app.Activation("1", "15551234567")
        p.reject()
        p.reject()
        self.assertEqual(c.calls, [])
        self.assertTrue(p.stop_event.is_set())

    def test_invalid_caps_fail_before_purchase(self):
        for options in [{"max_activations": 0}, {"max_activations": 6}, {"max_attempts": 0}, {"overall_timeout": -1}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.make(**options)

    def test_start_twice_while_purchase_waits_only_creates_one_worker(self):
        entered, release = threading.Event(), threading.Event()
        c = FakeClient()
        def block():
            entered.set()
            release.wait(2)
        c.on_buy = block
        p = app.Poller(c, "297", "140", queue.Queue(), max_price="2", currency="RUB")
        self.assertTrue(p.start())
        self.assertTrue(entered.wait(1))
        self.assertFalse(p.start())
        p.stop()
        release.set()
        p.thread.join(2)
        self.assertFalse(p.running)
        self.assertEqual(c.calls, ["buy", "reject:1"])

    def test_recovery_does_not_purchase_and_requires_explicit_resolution(self):
        p, c, _ = self.make(sms_timeout=0)
        c.reject_error = app.ApiError("synthetic")
        p._run()
        c.reject_error = None
        self.assertTrue(p.retry_cleanup())
        p.thread.join(2)
        self.assertEqual(p.state, "STOPPED")
        self.assertEqual(c.calls, ["buy", "reject:1", "reject:1"])

    def test_auth_failure_is_not_retried(self):
        p, c, _ = self.make()
        c.buy_error = app.api_error({"error_code": "wrong_token"})
        p._run()
        self.assertEqual(p.state, "ERROR")
        self.assertEqual(c.calls, ["buy"])


class TransportTests(unittest.TestCase):
    def test_invalid_utf8_is_safe_error(self):
        response = io.BytesIO(b"\xff")
        response.status = 200
        with patch("urllib.request.OpenerDirector.open", return_value=response), self.assertRaises(app.ApiError):
            app.HttpSession().get(app.API_BASE_URL + "/limits", params={"token": "dummy"}, timeout=1)

    def test_json_scalars_and_malformed_json_fail(self):
        for body in ['"string"', '12', 'null', '{', 'true']:
            with self.subTest(body=body), self.assertRaises(app.ApiError):
                app.HttpResponse(200, body).json()

    def test_http_429_cannot_be_retried_as_inventory(self):
        s = FakeSession([app.HttpResponse(429, 'no_free_phones')])
        with self.assertRaises(app.ApiError) as error:
            app.SmsManClient("dummy", session=s).get_number(application_id="297", country_id="140", max_price="2", currency="RUB")
        self.assertFalse(error.exception.retryable)

    def test_timeout_has_no_secret_exception_chain(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError("DUMMY-SECRET")):
            with self.assertRaises(app.ApiError) as ctx:
                app.HttpSession().get(app.API_BASE_URL + "/limits", params={"token": "dummy"}, timeout=1)
        self.assertNotIn("DUMMY-SECRET", str(ctx.exception))
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_oversized_body_is_rejected(self):
        response = io.BytesIO(b"x" * (1048576 + 1))
        response.status = 200
        with patch("urllib.request.OpenerDirector.open", return_value=response), self.assertRaises(app.ApiError):
            app.HttpSession().get(app.API_BASE_URL + "/limits", params={"token": "dummy"}, timeout=1)

    def test_redirect_handler_does_not_follow(self):
        self.assertTrue(hasattr(app, "NoRedirect"), "A redirect-blocking handler is required")
        handler = app.NoRedirect()
        req = __import__("urllib.request", fromlist=["Request"]).Request(app.API_BASE_URL + "/limits")
        self.assertIsNone(handler.redirect_request(req, None, 302, "redirect", {}, "http://example.org"))


if __name__ == "__main__":
    unittest.main()
