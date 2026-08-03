import unittest
from unittest.mock import Mock

from sms_man_app import ApiError, SmsManClient, parse_get_number_response, parse_get_sms_response


class ParseResponseTests(unittest.TestCase):
    def test_parses_successful_get_number_payload(self):
        activation = parse_get_number_response(
            {"request_id": "123", "country_id": "140", "application_id": "297", "number": "test-number"}
        )

        self.assertEqual(activation.request_id, "123")
        self.assertEqual(activation.phone_number, "test-number")

    def test_marks_no_free_phone_response_as_retryable(self):
        with self.assertRaisesRegex(ApiError, "No free phones") as raised:
            parse_get_number_response({"success": False, "error_code": "no_free_phones"})

        self.assertTrue(raised.exception.retryable)

    def test_marks_no_numbers_try_again_message_as_retryable(self):
        with self.assertRaisesRegex(ApiError, "No Numbers, try again") as raised:
            parse_get_number_response({"success": False, "error_msg": "No Numbers, try again."})

        self.assertTrue(raised.exception.retryable)

    def test_parses_code_from_first_sms_message(self):
        self.assertEqual(
            parse_get_sms_response(
                {
                    "success": True,
                    "sms": [
                        {"text": "Your login code is 483921"},
                        {"text": "Older message 100000"},
                    ],
                }
            ),
            "483921",
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

        activation = client.get_number(application_id="297", country_id="140")

        self.assertEqual(activation.request_id, "55")
        session.get.assert_called_once_with(
            "https://api.sms-man.com/control/get-number",
            params={"token": "secret", "application_id": "297", "country_id": "140"},
            timeout=15.0,
        )

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


def fake_response(payload):
    response = Mock()
    response.status_code = 200
    response.json.return_value = payload
    return response


if __name__ == "__main__":
    unittest.main()
