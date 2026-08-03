"""Local desktop client for the SMS-Man API.

This application talks only to SMS-Man's documented API endpoints. It does not
interact with third-party sign-up pages or attempt to bypass CAPTCHAs.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

API_BASE_URL = "https://api.sms-man.com/control"
SMS_CODE_PATTERN = re.compile(r"\b(\d{4,10})\b")


class ApiError(RuntimeError):
    """An error returned by SMS-Man or its HTTP transport."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Activation:
    request_id: str
    phone_number: str


class Session(Protocol):
    def get(self, url: str, *, params: dict[str, str], timeout: float) -> Any: ...


class HttpSession:
    """Small requests-compatible adapter built on Python's standard library."""

    def get(self, url: str, *, params: dict[str, str], timeout: float) -> Any:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{url}?{query}", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return HttpResponse(response.status, body)
        except urllib.error.HTTPError as error:
            return HttpResponse(error.code, error.read().decode("utf-8", errors="replace"))
        except urllib.error.URLError as error:
            raise ApiError(f"Network error: {error.reason}") from error


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: str

    def json(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.body)
        except json.JSONDecodeError as error:
            raise ApiError("SMS-Man returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise ApiError("SMS-Man returned an unexpected JSON response")
        return payload


ERROR_LABELS = {
    "no_free_phones": "No free phones",
    "not_enough_balance": "Insufficient SMS-Man balance",
    "wrong_token": "Invalid API token",
    "bad_token": "Invalid API token",
    "wrong_service_id": "Invalid service ID",
    "wrong_country_id": "Invalid country ID",
}
WAITING_SMS_CODES = {"wait_sms", "sms_not_found", "no_sms"}


def api_error(payload: dict[str, Any]) -> ApiError:
    code = str(payload.get("error_code") or payload.get("error") or "unknown_error").lower()
    raw_detail = payload.get("error_msg") or payload.get("message") or ERROR_LABELS.get(code)
    if isinstance(raw_detail, dict):
        detail = "; ".join(f"{key}: {value}" for key, value in raw_detail.items())
    else:
        detail = str(raw_detail or code.replace("_", " "))
    return ApiError(detail[:200], retryable=code == "no_free_phones")


def parse_get_number_response(payload: dict[str, Any]) -> Activation:
    if payload.get("success") is False or payload.get("error_code") or payload.get("error"):
        raise api_error(payload)

    request_id = payload.get("request_id") or payload.get("id")
    phone_number = payload.get("number") or payload.get("phone")
    if request_id is None or not phone_number:
        raise ApiError("SMS-Man response did not include an activation ID and phone number")
    return Activation(request_id=str(request_id), phone_number=str(phone_number))


def parse_get_sms_response(payload: dict[str, Any]) -> str | None:
    error_code = str(payload.get("error_code") or payload.get("error") or "").lower()
    if error_code in WAITING_SMS_CODES:
        return None
    if payload.get("success") is False or error_code:
        raise api_error(payload)

    direct_code = payload.get("code") or payload.get("sms_code")
    if direct_code is not None:
        return str(direct_code)

    messages = payload.get("sms") or payload.get("messages") or []
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list):
        raise ApiError("SMS-Man returned an invalid SMS list")
    for message in messages:
        text = message.get("text", "") if isinstance(message, dict) else str(message)
        match = SMS_CODE_PATTERN.search(text)
        if match:
            return match.group(1)
    return None


class SmsManClient:
    def __init__(self, token: str, *, session: Session | None = None, base_url: str = API_BASE_URL, timeout: float = 15.0) -> None:
        token = token.strip()
        if not token:
            raise ValueError("API token is required")
        self.token = token
        self.session = session or HttpSession()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, action: str, **params: str) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}/{action}",
            params={"token": self.token, **params},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            message = f"SMS-Man HTTP {response.status_code}"
            body = getattr(response, "body", "").strip()
            if body:
                try:
                    detail = str(api_error(response.json()))
                except ApiError:
                    detail = " ".join(body.split())[:200]
                message = f"{message}: {detail}"
            raise ApiError(message)
        return response.json()

    def get_number(self, *, service_id: str, country_id: str) -> Activation:
        return parse_get_number_response(
            self._get("get-number", application_id=service_id.strip(), country_id=country_id.strip())
        )

    def get_sms(self, request_id: str) -> str | None:
        return parse_get_sms_response(self._get("get-sms", request_id=request_id))

    def set_status(self, request_id: str, status: str) -> None:
        payload = self._get("set-status", request_id=request_id, status=status)
        if payload.get("success") is not True:
            raise api_error(payload)


class Poller:
    """Obtains one activation, then polls it until a code arrives or it is stopped."""

    def __init__(self, client: SmsManClient, service_id: str, country_id: str, events: queue.Queue[tuple[str, str]]) -> None:
        self.client = client
        self.service_id = service_id
        self.country_id = country_id
        self.events = events
        self.stop_event = threading.Event()
        self.activation: Activation | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, name="sms-man-poller", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()

    def reject(self) -> None:
        if self.activation:
            self.client.set_status(self.activation.request_id, "reject")

    def _emit(self, event: str, value: str) -> None:
        self.events.put((event, value))

    def _run(self) -> None:
        try:
            attempts = 0
            while not self.stop_event.is_set() and self.activation is None:
                attempts += 1
                self._emit("status", f"Requesting a phone number (attempt {attempts})…")
                try:
                    self.activation = self.client.get_number(
                        service_id=self.service_id, country_id=self.country_id
                    )
                except ApiError as error:
                    if not error.retryable:
                        raise
                    self._emit("status", f"No number available (attempt {attempts}); retrying in 3 seconds…")
                    self.stop_event.wait(3)
            if self.activation is None:
                return
            self._emit("number", self.activation.phone_number)
            self._emit("status", f"Number acquired. Waiting for SMS (activation {self.activation.request_id})…")
            while not self.stop_event.wait(3):
                code = self.client.get_sms(self.activation.request_id)
                if code:
                    self._emit("code", code)
                    self._emit("status", "SMS code received.")
                    return
                self._emit("status", "Waiting for SMS…")
        except (ApiError, ValueError) as error:
            self._emit("error", str(error))
        finally:
            self._emit("finished", "")


def main() -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("SMS-Man 자동 발급")
    root.resizable(False, False)
    root.columnconfigure(0, weight=1)

    events: queue.Queue[tuple[str, str]] = queue.Queue()
    poller: Poller | None = None
    token = tk.StringVar()
    service_id = tk.StringVar(value="297")
    country_id = tk.StringVar(value="140")
    number = tk.StringVar(value="—")
    code = tk.StringVar(value="—")
    status = tk.StringVar(value="API 토큰, 서비스 ID, 국가 ID를 입력하세요.")

    frame = ttk.Frame(root, padding=16)
    frame.grid(sticky="nsew")
    frame.columnconfigure(1, weight=1)

    def add_field(row: int, label: str, variable: tk.StringVar, show: str | None = None) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=variable, show=show, width=42).grid(row=row, column=1, sticky="ew", pady=4)

    add_field(0, "API 토큰", token, "•")
    add_field(1, "서비스 ID", service_id)
    add_field(2, "국가 ID", country_id)
    ttk.Separator(frame).grid(row=3, column=0, columnspan=2, sticky="ew", pady=10)

    ttk.Label(frame, text="번호").grid(row=4, column=0, sticky="w", pady=3)
    ttk.Label(frame, textvariable=number, font=("Segoe UI", 11, "bold")).grid(row=4, column=1, sticky="w", pady=3)
    ttk.Label(frame, text="코드").grid(row=5, column=0, sticky="w", pady=3)
    ttk.Label(frame, textvariable=code, font=("Segoe UI", 11, "bold")).grid(row=5, column=1, sticky="w", pady=3)
    ttk.Label(frame, textvariable=status, wraplength=380).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 6))

    buttons = ttk.Frame(frame)
    buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def begin() -> None:
        nonlocal poller
        if poller:
            return
        try:
            poller = Poller(SmsManClient(token.get()), service_id.get().strip(), country_id.get().strip(), events)
        except ValueError as error:
            messagebox.showerror("입력 오류", str(error))
            return
        if not poller.service_id or not poller.country_id:
            poller = None
            messagebox.showerror("입력 오류", "서비스 ID와 국가 ID는 필수입니다.")
            return
        number.set("—")
        code.set("—")
        start_button.configure(state="disabled")
        stop_button.configure(state="normal")
        reject_button.configure(state="normal")
        poller.start()

    def stop() -> None:
        if poller:
            poller.stop()
        status.set("Stopped.")

    def reject() -> None:
        if not poller or not poller.activation:
            messagebox.showinfo("안내", "먼저 번호를 발급받아야 합니다.")
            return
        try:
            poller.reject()
            status.set("Activation rejected.")
        except ApiError as error:
            messagebox.showerror("SMS-Man 오류", str(error))

    start_button = ttk.Button(buttons, text="시작", command=begin)
    start_button.grid(row=0, column=0, padx=(0, 6))
    stop_button = ttk.Button(buttons, text="중지", command=stop, state="disabled")
    stop_button.grid(row=0, column=1, padx=6)
    reject_button = ttk.Button(buttons, text="현재 번호 거절", command=reject, state="disabled")
    reject_button.grid(row=0, column=2, padx=(6, 0))

    def consume_events() -> None:
        nonlocal poller
        while True:
            try:
                event, value = events.get_nowait()
            except queue.Empty:
                break
            if event == "number":
                number.set(value)
            elif event == "code":
                code.set(value)
                root.clipboard_clear()
                root.clipboard_append(value)
            elif event == "status":
                status.set(value)
            elif event == "error":
                status.set(f"오류: {value}")
                messagebox.showerror("SMS-Man 오류", value)
            elif event == "finished":
                poller = None
                start_button.configure(state="normal")
                stop_button.configure(state="disabled")
                reject_button.configure(state="disabled")
        root.after(150, consume_events)

    root.protocol("WM_DELETE_WINDOW", lambda: (poller and poller.stop(), root.destroy()))
    root.after(150, consume_events)
    root.mainloop()


if __name__ == "__main__":
    main()
