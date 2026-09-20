import queue
import unittest
from unittest.mock import Mock

from sms_man_app import (
    ApiError,
    Activation,
    HttpResponse,
    Poller,
    SmsManClient,
    strip_country_code,
    parse_get_number_response,
    parse_limits_response,
    parse_get_sms_response,
)


class ParseResponseTests(unittest.TestCase):
    def test_parses_successful_get_number_payload(self):
        activation = parse_get_number_response(
            {"request_id": "123", "country_id": "140", "application_id": "297", "number": "15551234567"}
        )

        self.assertEqual(activation.request_id, "123")
        self.assertEqual(activation.phone_number, "15551234567")

    def test_marks_no_free_phone_response_as_retryable(self):
        with self.assertRaises(ApiError) as raised:
            parse_get_number_response({"success": False, "error_code": "no_free_phones"})

        self.assertTrue(raised.exception.retryable)

    def test_marks_no_numbers_try_again_message_as_retryable(self):
        with self.assertRaises(ApiError) as raised:
            parse_get_number_response({"success": False, "error_msg": "No Numbers, try again."})

        self.assertTrue(raised.exception.retryable)

    def test_parses_available_number_count_from_limits_response(self):
        self.assertEqual(parse_limits_response([{"application_id": "297", "country_id": "140", "numbers": "17"}], "297", "140"), 17)

    def test_strips_explicit_country_code_from_phone_number(self):
        self.assertEqual(strip_country_code("+82 10-1234-5678", "+82"), "1012345678")

    def test_rejects_undocumented_message_text_as_verification_code(self):
        with self.assertRaises(ApiError):
            parse_get_sms_response(
                {
                    "success": True,
                    "sms": [
                        {"text": "Your login code is 483921"},
                        {"text": "Older message 100000"},
                    ],
                }
            )

    def test_returns_none_while_no_sms_has_arrived(self):
        self.assertIsNone(parse_get_sms_response({"request_id": 123, "error_code": "wait_sms", "error_msg": "Still waiting..."}))

    def test_parses_documented_sms_code_payload(self):
        self.assertEqual(
            parse_get_sms_response({"request_id": 123, "sms_code": "483921"}),
            "483921",
        )


class ClientTests(unittest.TestCase):
    def test_get_number_uses_sms_man_endpoint_and_parameters(self):
        session = Mock()
        session.get.return_value = fake_response(
            {"request_id": 55, "country_id": 140, "application_id": 297, "number": "15551234567"}
        )
        client = SmsManClient("secret", session=session)

        activation = client.get_number(application_id="297", country_id="140", max_price="2", currency="RUB")

        self.assertEqual(activation.request_id, "55")
        session.get.assert_called_once_with(
            "https://api.sms-man.com/control/get-number",
            params={"token": "secret", "application_id": "297", "country_id": "140", "maxPrice": "2", "currency": "RUB"},
            timeout=15.0,
        )

    def test_get_limits_uses_country_and_application_parameters(self):
        session = Mock()
        session.get.return_value = fake_response([{"application_id": "297", "country_id": "140", "numbers": "17"}])
        client = SmsManClient("secret", session=session)

        self.assertEqual(client.get_limits(application_id="297", country_id="140"), 17)
        session.get.assert_called_once_with(
            "https://api.sms-man.com/control/limits",
            params={"token": "secret", "application_id": "297", "country_id": "140"},
            timeout=15.0,
        )

    def test_does_not_include_remote_detail_for_http_failure(self):
        session = Mock()
        session.get.return_value = HttpResponse(
            400,
            '{"success":false,"error_code":"wrong_application_id","error_msg":"Wrong application ID!"}',
        )
        client = SmsManClient("secret", session=session)

        with self.assertRaises(ApiError) as error:
            client.get_number(application_id="297", country_id="140", max_price="2", currency="RUB")
        self.assertNotIn("Wrong application ID!", str(error.exception))

    def test_sets_status_with_activation_id(self):
        session = Mock()
        session.get.return_value = fake_response({"success": True})
        client = SmsManClient("secret", session=session)

        client.set_status("55", "reject")

        session.get.assert_called_once_with(
            "https://api.sms-man.com/control/set-status",
            params={"token": "secret", "request_id": "55", "status": "reject"},
            timeout=15.0,
        )

class PollerTests(unittest.TestCase):
    def test_rejects_timed_out_number_before_requesting_another(self):
        client = Mock()
        first = Activation(request_id="first", phone_number="821011111111")
        second = Activation(request_id="second", phone_number="821022222222")
        client.get_number.side_effect = [first, second]
        client.get_sms.side_effect = [None, "483921"]
        events: queue.Queue[tuple[str, str]] = queue.Queue()
        from test_hardening import Clock
        clock = Clock()
        poller = Poller(client, "297", "140", events, sms_timeout=5, auto_reissue=True, max_activations=2, max_price="2", currency="RUB", clock=clock)
        poller.stop_event.wait = clock.wait

        poller._run()

        client.set_status.assert_called_once_with("first", "reject")
        self.assertEqual(client.get_number.call_count, 2)
        self.assertIn("483921", [e.value for e in list(events.queue) if e.kind == "code"])


def fake_response(payload):
    response = Mock()
    response.status_code = 200
    response.json.return_value = payload
    return response


if __name__ == "__main__":
    unittest.main()
