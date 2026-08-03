# SMS-Man Auto

Windows desktop client for **your own SMS-Man API account**. It requests one
activation, retries conservatively when no number is available, polls the
activation for an SMS code, copies a received code to the clipboard, and can
reject the active number.

It communicates only with SMS-Man's API endpoints; it does not automate
third-party websites or bypass CAPTCHAs.

## Features

- API token, application ID, and country ID inputs
- One activation at a time (avoids duplicate purchases)
- Retry every 3 seconds only for `no_free_phones`
- Background network polling so the UI stays responsive
- Displayed phone number and SMS code; received code is copied to clipboard
- `reject` status action for the active activation
- No API token is written to disk

## Requirements

- Windows with Python 3.12+ **including Tcl/Tk** (the official python.org
  installer includes it)
- An SMS-Man account, API token, application ID, and country ID

No third-party Python package is required.

## Run

```bash
py -3.12 sms_man_app.py
```

Enter the API token, application ID, and country ID, then select **시작**. Select
**중지** to stop future requests. Select **현재 번호 거절** to send
`status=reject` for the activation currently displayed.

The client uses these API paths by default:

- `https://api.sms-man.com/control/get-number`
- `https://api.sms-man.com/control/get-sms`
- `https://api.sms-man.com/control/set-status`

## Tests

```bash
py -3.12 -m unittest discover -s tests -v
```

Tests use fake HTTP sessions and never call SMS-Man or require a token.
