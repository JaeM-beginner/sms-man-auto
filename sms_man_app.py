"""SMS-Man Auto desktop entry point; core imports remain available for clients."""
from sms_api import (API_BASE_URL, Activation, ApiError, HttpResponse, HttpSession,
                     NoRedirect, SmsManClient, api_error, parse_get_number_response,
                     parse_get_sms_response, parse_limits_response, strip_country_code)
from sms_engine import Poller, Event, ViewState, ClipboardOwner

VERSION = "0.2.0-rc1"


def main():
    from sms_ui import run
    run()


if __name__ == "__main__":
    main()
