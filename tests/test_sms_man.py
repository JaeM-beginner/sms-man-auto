import unittest
from unittest.mock import Mock

from sms_man_app import (
    ApiError,
    HttpResponse,
    SmsManClient,
    parse_get_number_response,
    parse_get_sms_response,
)


class ParseResponseTests(unittest.TestCase):
    def test_parses_successful_get_number_payload(self):
        activation = parse_get_number_response(
            {"request_id": "123", "number": "+447700900123"}
        )

        self.assertEqual(activation.request_id, "123")
        self.assertEqual(activation.phone_number, "+447700900123")

    def test_marks_no_free_phone_response_as_retryable(self):
        with self.assertRaisesRegex(ApiError, "No free phones") as raised:
            parse_get_number_response({"success": False, "error_code": "no_free_phones"})

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

    def test_parses_sms_code_from_documented_response(self):
        self.assertEqual(
            parse_get_sms_response(
                {
                    "request_id": 1,
                    "country_id": 1,
                    "application_id": 1,
                    "number": "79002415539",
                    "sms_code": "1243",
                }
            ),
            "1243",
        )

    def test_returns_none_for_documented_wait_sms_error(self):
        self.assertIsNone(
            parse_get_sms_response(
                {"request_id": 1, "error_code": "wait_sms", "error_msg": "Still waiting..."}
            )
        )

    def test_uses_error_msg_from_api(self):
        with self.assertRaisesRegex(ApiError, "token: Wrong token"):
            parse_get_number_response(
                {"success": False, "error_code": "wrong_token", "error_msg": {"token": "Wrong token!"}}
            )

    def test_returns_none_while_no_sms_has_arrived(self):
        self.assertIsNone(parse_get_sms_response({"success": True, "sms": []}))


class ClientTests(unittest.TestCase):
    def test_get_number_uses_sms_man_endpoint_and_parameters(self):
        session = Mock()
        session.get.return_value = fake_response(
            {"request_id": 55, "number": "15551234567"}
        )
        client = SmsManClient("secret", session=session)

        activation = client.get_number(service_id="297", country_id="140")

        self.assertEqual(activation.request_id, "55")
        session.get.assert_called_once_with(
            "https://api.sms-man.com/control/get-number",
            params={"token": "secret", "application_id": "297", "country_id": "140"},
            timeout=15.0,
        )

    def test_includes_api_error_detail_for_http_failure(self):
        session = Mock()
        session.get.return_value = HttpResponse(
            400,
            '{"success":false,"error_code":"wrong_service_id","error_msg":"Wrong application ID!"}',
        )
        client = SmsManClient("secret", session=session)

        with self.assertRaisesRegex(ApiError, "SMS-Man HTTP 400: Wrong application ID"):
            client.get_number(service_id="297", country_id="140")

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
