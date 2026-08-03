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
    "wrong_application_id": "Invalid application ID",
    "wrong_country_id": "Invalid country ID",
}
WAITING_SMS_CODES = {"wait_sms", "sms_not_found", "no_sms"}


def api_error(payload: dict[str, Any]) -> ApiError:
    code = str(payload.get("error_code") or payload.get("error") or "unknown_error").lower()
    detail = payload.get("error_msg") or payload.get("message") or ERROR_LABELS.get(code) or code.replace("_", " ")
    if isinstance(detail, dict):
        detail = next((str(value) for value in detail.values()), code.replace("_", " "))
    retryable = code == "no_free_phones" or str(detail).strip().casefold().rstrip(".") == "no numbers, try again"
    return ApiError(detail[:200], retryable=retryable)


def parse_get_number_response(payload: dict[str, Any]) -> Activation:
    if payload.get("success") is False or payload.get("error_code"):
        raise api_error(payload)

    request_id = payload.get("request_id") or payload.get("id")
    phone_number = payload.get("number") or payload.get("phone")
    if request_id is None or not phone_number:
        raise ApiError("SMS-Man response did not include an activation ID and phone number")
    return Activation(request_id=str(request_id), phone_number=str(phone_number))


def parse_limits_response(payload: dict[str, Any]) -> int:
    if payload.get("success") is False or payload.get("error_code"):
        raise api_error(payload)
    try:
        return int(payload["count"])
    except (KeyError, TypeError, ValueError) as error:
        raise ApiError("SMS-Man limits response did not include an available number count") from error


def strip_country_code(phone_number: str, country_code: str) -> str:
    number_digits = re.sub(r"\D", "", phone_number)
    code_digits = re.sub(r"\D", "", country_code)
    if not code_digits:
        raise ApiError("Enter the country calling code, for example +82")
    if not number_digits.startswith(code_digits) or len(number_digits) == len(code_digits):
        raise ApiError("The phone number does not start with the entered country calling code")
    return number_digits[len(code_digits) :]


def parse_get_sms_response(payload: dict[str, Any]) -> str | None:
    code = str(payload.get("error_code") or payload.get("error") or "").lower()
    if code:
        if code in WAITING_SMS_CODES:
            return None
        raise api_error(payload)
    if payload.get("success") is False:
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

    def get_number(self, *, application_id: str, country_id: str) -> Activation:
        return parse_get_number_response(
            self._get("get-number", application_id=application_id.strip(), country_id=country_id.strip())
        )

    def get_limits(self, *, application_id: str, country_id: str) -> int:
        return parse_limits_response(
            self._get("limits", application_id=application_id.strip(), country_id=country_id.strip())
        )

    def get_sms(self, request_id: str) -> str | None:
        return parse_get_sms_response(self._get("get-sms", request_id=request_id))

    def set_status(self, request_id: str, status: str) -> None:
        payload = self._get("set-status", request_id=request_id, status=status)
        if payload.get("success") is not True:
            raise api_error(payload)


class Poller:
    """Obtains one activation, then polls it until a code arrives or it is stopped."""

    def __init__(
        self,
        client: SmsManClient,
        application_id: str,
        country_id: str,
        events: queue.Queue[tuple[str, str]],
        *,
        sms_timeout: float = 120,
    ) -> None:
        self.client = client
        self.application_id = application_id
        self.country_id = country_id
        self.events = events
        self.sms_timeout = sms_timeout
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
            while not self.stop_event.is_set():
                while not self.stop_event.is_set() and self.activation is None:
                    attempts += 1
                    self._emit("status", f"Requesting a phone number (attempt {attempts})…")
                    try:
                        self.activation = self.client.get_number(
                            application_id=self.application_id, country_id=self.country_id
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
                started_at = time.monotonic()
                while not self.stop_event.wait(3):
                    code = self.client.get_sms(self.activation.request_id)
                    if code:
                        self._emit("code", code)
                        self._emit("status", "SMS code received.")
                        return
                    if time.monotonic() - started_at >= self.sms_timeout:
                        self._emit("status", "No SMS after 2 minutes; rejecting this number and requesting another…")
                        self.client.set_status(self.activation.request_id, "reject")
                        self.activation = None
                        break
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
    root.minsize(720, 620)
    root.geometry("800x720")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    style = ttk.Style(root)
    style.configure("App.TFrame", background="#eef4fb")
    style.configure("Card.TLabelframe", background="#ffffff", bordercolor="#cbd8e6", relief="solid")
    style.configure("Card.TLabelframe.Label", font=("Segoe UI", 11, "bold"))
    style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))
    style.configure("Result.TLabel", font=("Segoe UI", 13, "bold"), foreground="#163a63")
    style.configure("Code.TLabel", font=("Segoe UI", 20, "bold"), foreground="#0b6b3a")

    events: queue.Queue[tuple[str, str]] = queue.Queue()
    poller: Poller | None = None
    token = tk.StringVar()
    application_id = tk.StringVar(value="297")
    country_id = tk.StringVar(value="140")
    country_calling_code = tk.StringVar()
    availability = tk.StringVar(value="—")
    number = tk.StringVar(value="—")
    code = tk.StringVar(value="—")
    status = tk.StringVar(value="API 토큰, 애플리케이션 ID, 국가 ID를 입력하세요.")

    app = ttk.Frame(root, style="App.TFrame", padding=16)
    app.grid(sticky="nsew")
    app.columnconfigure(0, weight=1)
    app.rowconfigure(3, weight=1)

    header = tk.Frame(app, background="#0757a8", padx=18, pady=14)
    header.grid(row=0, column=0, sticky="ew")
    tk.Label(header, text="SMS-Man 자동 발급", background="#0757a8", foreground="white", font=("Segoe UI", 18, "bold")).pack(anchor="w")
    tk.Label(header, text="토큰은 이 창에서만 사용되며 저장되지 않습니다.", background="#0757a8", foreground="#dcecff", font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))

    setup = ttk.LabelFrame(app, text="설정", style="Card.TLabelframe", padding=14)
    setup.grid(row=1, column=0, sticky="ew", pady=(12, 8))
    setup.columnconfigure(1, weight=1)

    active = ttk.LabelFrame(app, text="현재 작업", style="Card.TLabelframe", padding=14)
    active.grid(row=2, column=0, sticky="ew", pady=8)
    active.columnconfigure(1, weight=1)

    history = ttk.LabelFrame(app, text="활동 기록", style="Card.TLabelframe", padding=10)
    history.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
    history.columnconfigure(0, weight=1)
    history.rowconfigure(0, weight=1)

    def add_field(row: int, label: str, variable: tk.StringVar, show: str | None = None) -> None:
        ttk.Label(setup, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
        ttk.Entry(setup, textvariable=variable, show=show, width=42).grid(row=row, column=1, sticky="ew", pady=4)

    add_field(0, "API 토큰", token, "•")
    add_field(1, "애플리케이션 ID", application_id)
    add_field(2, "국가 ID", country_id)
    add_field(3, "국가 전화 코드 (예: +82)", country_calling_code)
    ttk.Label(setup, text="사용 가능 번호").grid(row=4, column=0, sticky="w", padx=(0, 12), pady=4)
    ttk.Label(setup, textvariable=availability, style="Result.TLabel").grid(row=4, column=1, sticky="w", pady=4)

    status_strip = tk.Label(active, textvariable=status, anchor="w", background="#e4f0ff", foreground="#173b61", font=("Segoe UI", 10), padx=10, pady=8)
    status_strip.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
    ttk.Label(active, text="번호").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=5)
    ttk.Label(active, textvariable=number, style="Result.TLabel").grid(row=1, column=1, sticky="w", pady=5)
    ttk.Label(active, text="코드").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=5)
    ttk.Label(active, textvariable=code, style="Code.TLabel").grid(row=2, column=1, sticky="w", pady=5)

    log = tk.Text(history, height=7, wrap="word", state="disabled", background="#f8fbff", foreground="#243447", relief="flat", font=("Consolas", 9))
    log.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(history, orient="vertical", command=log.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    log.configure(yscrollcommand=scrollbar.set)

    def log_activity(message: str) -> None:
        log.configure(state="normal")
        log.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        log.see("end")
        log.configure(state="disabled")

    def clear_log() -> None:
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")

    def begin() -> None:
        nonlocal poller
        if poller:
            return
        try:
            poller = Poller(SmsManClient(token.get()), application_id.get().strip(), country_id.get().strip(), events)
        except ValueError as error:
            messagebox.showerror("입력 오류", str(error))
            return
        if not poller.application_id or not poller.country_id:
            poller = None
            messagebox.showerror("입력 오류", "애플리케이션 ID와 국가 ID는 필수입니다.")
            return
        number.set("—")
        code.set("—")
        copy_number_button.configure(state="disabled")
        copy_code_button.configure(state="disabled")
        start_button.configure(state="disabled")
        stop_button.configure(state="normal")
        reject_button.configure(state="normal")
        log_activity("번호 발급을 시작했습니다.")
        poller.start()

    def stop() -> None:
        if poller:
            poller.stop()
        status.set("Stopped.")
        log_activity("사용자가 작업을 중지했습니다.")

    def reject() -> None:
        if not poller or not poller.activation:
            messagebox.showinfo("안내", "먼저 번호를 발급받아야 합니다.")
            return
        try:
            poller.reject()
            status.set("Activation rejected.")
            log_activity("현재 번호를 거절했습니다.")
        except ApiError as error:
            messagebox.showerror("SMS-Man 오류", str(error))

    def copy_number_without_country_code() -> None:
        try:
            copied_number = strip_country_code(number.get(), country_calling_code.get())
        except ApiError as error:
            messagebox.showerror("복사 오류", str(error))
            return
        root.clipboard_clear()
        root.clipboard_append(copied_number)
        status.set("국가 코드 제외 번호를 복사했습니다.")
        log_activity("국가 코드 제외 번호를 복사했습니다.")

    def copy_code() -> None:
        if code.get() == "—":
            return
        root.clipboard_clear()
        root.clipboard_append(code.get())
        status.set("SMS 코드를 복사했습니다.")
        log_activity("SMS 코드를 복사했습니다.")

    def refresh_limits() -> None:
        try:
            client = SmsManClient(token.get())
        except ValueError as error:
            messagebox.showerror("입력 오류", str(error))
            return
        application = application_id.get().strip()
        country = country_id.get().strip()
        if not application or not country:
            messagebox.showerror("입력 오류", "애플리케이션 ID와 국가 ID는 필수입니다.")
            return

        refresh_button.configure(state="disabled")
        status.set("사용 가능 번호를 조회하는 중…")

        def worker() -> None:
            try:
                events.put(("limits", str(client.get_limits(application_id=application, country_id=country))))
            except ApiError as error:
                events.put(("limits_error", str(error)))
            finally:
                events.put(("limits_finished", ""))

        threading.Thread(target=worker, name="sms-man-limits", daemon=True).start()

    start_button = ttk.Button(active, text="번호 발급 시작", command=begin, style="Primary.TButton")
    start_button.grid(row=3, column=0, sticky="w", pady=(12, 0))
    stop_button = ttk.Button(active, text="중지", command=stop, state="disabled")
    stop_button.grid(row=3, column=1, sticky="w", padx=6, pady=(12, 0))
    reject_button = ttk.Button(active, text="현재 번호 거절", command=reject, state="disabled")
    reject_button.grid(row=3, column=2, sticky="e", padx=(6, 0), pady=(12, 0))
    refresh_button = ttk.Button(setup, text="재고 새로고침", command=refresh_limits)
    refresh_button.grid(row=4, column=2, padx=(12, 0))
    copy_number_button = ttk.Button(active, text="복사", command=copy_number_without_country_code, state="disabled")
    copy_number_button.grid(row=1, column=2, padx=(12, 0))
    copy_code_button = ttk.Button(active, text="복사", command=copy_code, state="disabled")
    copy_code_button.grid(row=2, column=2, padx=(12, 0))
    ttk.Button(history, text="기록 지우기", command=clear_log).grid(row=1, column=0, sticky="e", pady=(8, 0))

    def consume_events() -> None:
        nonlocal poller
        while True:
            try:
                event, value = events.get_nowait()
            except queue.Empty:
                break
            if event == "number":
                number.set(value)
                copy_number_button.configure(state="normal")
                log_activity(f"번호를 받았습니다: {value}")
            elif event == "limits":
                availability.set(f"{value}개")
                status.set("사용 가능 번호를 갱신했습니다.")
            elif event == "limits_error":
                status.set(f"재고 조회 오류: {value}")
                messagebox.showerror("SMS-Man 오류", value)
            elif event == "limits_finished":
                refresh_button.configure(state="normal")
            elif event == "code":
                code.set(value)
                copy_code_button.configure(state="normal")
                root.clipboard_clear()
                root.clipboard_append(value)
                log_activity("SMS 코드를 받았습니다.")
            elif event == "status":
                status.set(value)
                log_activity(value)
            elif event == "error":
                status.set(f"오류: {value}")
                log_activity(f"오류: {value}")
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
