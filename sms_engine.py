"""Single-worker activation lifecycle. UI only sends stop/recovery commands."""
from __future__ import annotations
import math
import threading
import time
import uuid
from dataclasses import dataclass
from sms_api import Activation, ApiError, positive_id, price_cap

TERMINAL = {"SUCCESS", "STOPPED", "ERROR", "UNRESOLVED"}


@dataclass(frozen=True, repr=False)
class Event:
    run_id: str
    kind: str
    value: str
    activation_id: str | None = None


class Poller:
    def __init__(self, client, application_id, country_id, events, *, max_price, currency,
                 auto_reissue=False, max_activations=1, max_attempts=20, sms_timeout=120,
                 overall_timeout=600, clock=time.monotonic):
        self.application_id, self.country_id = positive_id(application_id), positive_id(country_id)
        self.max_price, self.currency = price_cap(max_price, currency), currency
        if type(max_activations) is not int or not 1 <= max_activations <= 5:
            raise ValueError("최대 발급 수는 1~5회입니다.")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("재고 재시도는 1~20회입니다.")
        if not all(isinstance(x, (float, int)) and math.isfinite(x) for x in (sms_timeout, overall_timeout)) or sms_timeout < 0 or overall_timeout <= 0:
            raise ValueError("대기 시간을 확인하세요.")
        self.client, self.events = client, events
        self.auto_reissue, self.max_activations = auto_reissue, max_activations if auto_reissue else 1
        self.max_attempts, self.sms_timeout, self.overall_timeout = max_attempts, sms_timeout, overall_timeout
        self.clock = clock
        self.stop_event = threading.Event()
        self.run_id = uuid.uuid4().hex
        self.activation: Activation | None = None
        self.state = "IDLE"
        self.thread = None
        self._lock = threading.Lock()

    def _emit(self, kind, value):
        self.events.put(Event(self.run_id, kind, value, self.activation.request_id if self.activation else None))

    def _state(self, state):
        self.state = state
        self._emit("state", state)

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        with self._lock:
            if self.thread is not None or self.state != "IDLE":
                return False
            self.thread = threading.Thread(target=self._run, name="sms-man-worker", daemon=True)
            self.thread.start()
            return True

    def stop(self):
        self.stop_event.set()

    def reject(self):
        self.stop()

    def _cleanup(self):
        if self.activation is None:
            return True
        self._state("REJECTING")
        try:
            self.client.set_status(self.activation.request_id, "reject")
        except Exception:
            self._state("UNRESOLVED")
            return False
        self.activation = None
        self._state("STOPPED")
        return True

    def retry_cleanup(self):
        with self._lock:
            if self.running or self.state != "UNRESOLVED" or self.activation is None:
                return False
            self.thread = threading.Thread(target=self._recover, name="sms-man-recovery", daemon=True)
            self.thread.start()
            return True

    def _recover(self):
        try:
            self._cleanup()
        finally:
            self._emit("finished", "")

    def confirm_external_resolution(self):
        with self._lock:
            if self.running or self.state != "UNRESOLVED":
                return False
            self.activation = None
            self._state("STOPPED")
            return True

    def _run(self):
        began = self.clock()
        attempts = purchases = 0
        try:
            while not self.stop_event.is_set() and purchases < self.max_activations:
                if self.clock() - began >= self.overall_timeout or attempts >= self.max_attempts:
                    break
                self._state("REQUESTING")
                attempts += 1
                try:
                    self.activation = self.client.get_number(application_id=self.application_id, country_id=self.country_id,
                                                             max_price=self.max_price, currency=self.currency)
                except ApiError as error:
                    if error.retryable:
                        if attempts < self.max_attempts and not self.stop_event.is_set():
                            self.stop_event.wait(min(3, max(0, self.overall_timeout - (self.clock() - began))))
                        continue
                    self._state("UNRESOLVED" if error.uncertain else "ERROR")
                    self._emit("status", "번호 발급을 중단했습니다. 계정에서 상태를 확인하세요.")
                    return
                except Exception:
                    self._state("UNRESOLVED")
                    return
                purchases += 1
                self._emit("number", self.activation.phone_number)
                self._state("WAITING_SMS")
                acquired = self.clock()
                while not self.stop_event.is_set():
                    remaining = min(self.sms_timeout - (self.clock() - acquired), self.overall_timeout - (self.clock() - began))
                    if remaining <= 0:
                        break
                    self.stop_event.wait(min(3, remaining))
                    if self.stop_event.is_set() or self.clock() - acquired >= self.sms_timeout or self.clock() - began >= self.overall_timeout:
                        break
                    try:
                        code = self.client.get_sms(self.activation.request_id)
                    except Exception:
                        self._state("UNRESOLVED")
                        return
                    if self.stop_event.is_set() or self.clock() - acquired >= self.sms_timeout or self.clock() - began >= self.overall_timeout:
                        break
                    if code:
                        self._state("SUCCESS")
                        self._emit("code", code)
                        return
                self._state("STOPPING")
                if not self._cleanup():
                    return
                if self.stop_event.is_set() or not self.auto_reissue:
                    break
            self._state("STOPPED")
        except Exception:
            self._state("UNRESOLVED")
        finally:
            self._emit("finished", "")


class ViewState:
    """Main-thread projection rejects stale run and activation events."""
    def __init__(self):
        self.run_id = self.activation_id = None
        self.state = "IDLE"
        self.number = self.code = ""

    def begin(self, run_id):
        self.run_id, self.activation_id = run_id, None
        self.state, self.number, self.code = "REQUESTING", "", ""

    def apply(self, event):
        if event.run_id != self.run_id:
            return False
        if event.kind == "number":
            self.activation_id = event.activation_id
            self.number, self.code = event.value, ""
        elif event.kind == "state":
            self.state = event.value
        elif event.kind == "code":
            if self.state != "SUCCESS" or self.activation_id != event.activation_id:
                return False
            self.code = event.value
        return True


class ClipboardOwner:
    def __init__(self):
        self.last = None

    def copy(self, root, value):
        root.clipboard_clear()
        root.clipboard_append(value)
        self.last = value

    def clear_owned(self, root):
        try:
            if self.last is not None and root.clipboard_get() == self.last:
                root.clipboard_clear()
        finally:
            self.last = None
