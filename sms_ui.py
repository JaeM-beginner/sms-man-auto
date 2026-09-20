"""Tk main-thread UI. Workers never access widgets or write logs to disk."""
import queue
import threading
import time
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from sms_api import SmsManClient, ApiError, strip_country_code, positive_id
from sms_engine import Poller, ViewState, ClipboardOwner

STATE_LABELS = {
    "IDLE": "설정을 입력하고 시작하세요.",
    "REQUESTING": "번호 요청 중입니다. 중지를 누르면 진행 중인 응답까지 확인합니다.",
    "WAITING_SMS": "SMS 대기 중입니다. 중지·거절 시 미사용 번호 정리를 시도합니다.",
    "STOPPING": "중지 및 미사용 번호 정리 중입니다.",
    "REJECTING": "번호 거절 결과를 확인하고 있습니다.",
    "SUCCESS": "SMS 수신 완료. 결과는 새 실행 또는 정보 지우기까지 유지됩니다.",
    "STOPPED": "작업이 중지되었습니다. 확인된 미정리 번호는 없습니다.",
    "ERROR": "발급을 중단했습니다. 토큰·잔액·설정을 확인하세요.",
    "UNRESOLVED": "요청 결과 또는 번호 취소를 확인하지 못했습니다. 계정에서 확인하세요. 새 발급은 차단됩니다.",
}
DOCS = ("README.md", "PRIVACY.md", "SUPPORT.md", "SECURITY.md", "TERMS.md", "RELEASE.md", "THIRD_PARTY_NOTICES.md")


def focus_scroll_fraction(top, viewport_height, content_height, widget_top, widget_height):
    if widget_top < top:
        return max(0, widget_top) / max(1, content_height)
    if widget_top + widget_height > top + viewport_height:
        return max(0, widget_top + widget_height - viewport_height) / max(1, content_height)
    return top / max(1, content_height)


class Application:
    def __init__(self, root):
        self.root = root
        self.poller = None
        self.view, self.clipboard = ViewState(), ClipboardOwner()
        self.events = queue.Queue()
        self.refresh_busy = False
        self.refresh_generation = 0
        self.closing = False
        self.feedback = ""
        self.close_started = 0
        self.token = tk.StringVar()
        self.application_id = tk.StringVar(value="297")
        self.country_id = tk.StringVar(value="140")
        self.calling_code = tk.StringVar()
        self.max_price = tk.StringVar()
        self.currency = tk.StringVar(value="RUB")
        self.max_count = tk.StringVar(value="1")
        self.auto_reissue, self.auto_copy = tk.BooleanVar(), tk.BooleanVar()
        self.status = tk.StringVar(value=STATE_LABELS["IDLE"])
        self.number, self.code = tk.StringVar(value="—"), tk.StringVar(value="—")
        self.availability = tk.StringVar(value="재고 미조회")
        self.viewport = ttk.Frame(root)
        self.viewport.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.viewport, highlightthickness=0, width=760, height=680)
        scrollbar = ttk.Scrollbar(self.viewport, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.frame = ttk.Frame(self.canvas, padding=12)
        self.content_window = self.canvas.create_window((0, 0), window=self.frame, anchor="nw")
        self.frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.content_window, width=e.width))
        self.frame.columnconfigure(1, weight=1)
        self.fields = []
        ttk.Label(self.frame, text="SMS-Man Auto · 0.2.0-rc1 (비공식)", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(self.frame, text="토큰은 SMS-Man API 요청에 사용되며, 이 앱은 토큰을 파일에 저장하지 않습니다.", wraplength=650).grid(row=1, column=0, columnspan=3, sticky="w", pady=6)
        for row, (label, var) in enumerate((("API 토큰", self.token), ("서비스 ID", self.application_id), ("국가 ID", self.country_id),
                                            ("국가 전화 코드 (+82 등)", self.calling_code), ("최대 단가 (정수)", self.max_price), ("최대 발급 수 (1~5)", self.max_count)), start=2):
            ttk.Label(self.frame, text=label).grid(row=row, column=0, sticky="w", pady=3)
            entry = ttk.Entry(self.frame, textvariable=var, show="•" if row == 2 else "")
            entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
            self.fields.append(entry)
        ttk.Label(self.frame, text="통화").grid(row=8, column=0, sticky="w")
        self.currency_box = ttk.Combobox(self.frame, textvariable=self.currency, values=("RUB", "USD", "EUR"), state="readonly")
        self.currency_box.grid(row=8, column=1, sticky="ew")
        self.reissue_box = ttk.Checkbutton(self.frame, text="시간 초과 시 자동 재발급 (기본 OFF)", variable=self.auto_reissue)
        self.reissue_box.grid(row=9, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(self.frame, text="수신 코드 자동 복사 (클립보드 기록·동기화 주의)", variable=self.auto_copy).grid(row=10, column=0, columnspan=3, sticky="w")
        self.refresh_button = ttk.Button(self.frame, text="재고 조회", command=self.refresh)
        self.refresh_button.grid(row=11, column=0, sticky="w")
        ttk.Label(self.frame, textvariable=self.availability).grid(row=11, column=1, sticky="w")
        ttk.Label(self.frame, textvariable=self.status, wraplength=650).grid(row=12, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(self.frame, textvariable=self.number, font=("Segoe UI", 13)).grid(row=13, column=0, columnspan=2, sticky="w")
        self.number_button = ttk.Button(self.frame, text="국가 코드 제외 복사", command=self.copy_number)
        self.number_button.grid(row=13, column=2)
        ttk.Label(self.frame, textvariable=self.code, font=("Segoe UI", 20, "bold")).grid(row=14, column=0, columnspan=2, sticky="w")
        self.code_button = ttk.Button(self.frame, text="코드 복사", command=self.copy_code)
        self.code_button.grid(row=14, column=2)
        actions = ttk.Frame(self.frame)
        actions.grid(row=15, column=0, columnspan=3, sticky="ew", pady=6)
        self.start_button = ttk.Button(actions, text="발급 시작", command=self.begin)
        self.stop_button = ttk.Button(actions, text="중지 / 현재 번호 거절", command=self.stop)
        self.start_button.pack(side="left")
        self.stop_button.pack(side="left", padx=4)
        self.clear_button = ttk.Button(actions, text="민감정보 지우기", command=self.clear)
        self.clear_button.pack(side="left")
        recovery = ttk.Frame(self.frame)
        recovery.grid(row=16, column=0, columnspan=3, sticky="ew")
        self.retry_button = ttk.Button(recovery, text="번호 정리 재시도", command=self.retry)
        self.retry_button.pack(side="left")
        ttk.Button(recovery, text="SMS-Man 계정 열기", command=lambda: webbrowser.open("https://sms-man.com/")).pack(side="left", padx=4)
        self.resolved_button = ttk.Button(recovery, text="계정에서 해결 확인", command=self.resolve)
        self.resolved_button.pack(side="left")
        self.log = tk.Text(self.frame, height=5, state="disabled", wrap="word")
        self.log.grid(row=17, column=0, columnspan=3, sticky="nsew", pady=6)
        self.frame.rowconfigure(17, weight=1)
        self.doc_choice = tk.StringVar(value="README.md")
        ttk.Combobox(self.frame, textvariable=self.doc_choice, values=DOCS, state="readonly").grid(row=18, column=0, columnspan=2, sticky="ew")
        ttk.Button(self.frame, text="도움말 / 안내", command=self.help).grid(row=18, column=2)
        root.bind("<Escape>", lambda e: self.stop())
        root.bind("<Return>", self.enter)
        root.bind("<FocusIn>", self.reveal_focus)
        root.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        root.bind("<Next>", lambda e: self.canvas.yview_scroll(1, "pages"))
        root.bind("<Prior>", lambda e: self.canvas.yview_scroll(-1, "pages"))
        root.protocol("WM_DELETE_WINDOW", self.close)
        # Safe fallback: Tk callbacks must not print potentially sensitive tracebacks.
        root.report_callback_exception = lambda *args: self.tell("화면 처리 오류입니다. 진행 중인 번호를 확인하세요.")
        self.after_id = root.after(100, self.consume)
        self.render()

    def busy(self):
        return self.poller is not None and self.poller.running

    def reveal_focus(self, event):
        if not str(event.widget).startswith(str(self.frame) + "."):
            return
        self.root.update_idletasks()
        y = event.widget.winfo_rooty() - self.frame.winfo_rooty()
        fraction = focus_scroll_fraction(self.canvas.canvasy(0), self.canvas.winfo_height(),
                                        self.frame.winfo_height(), y, event.widget.winfo_height())
        self.canvas.yview_moveto(fraction)

    def tell(self, message):
        self.feedback = message
        self.status.set(message)

    def blocked(self):
        return self.busy() or self.closing or (self.poller is not None and self.poller.state == "UNRESOLVED")

    def enter(self, event):
        if isinstance(event.widget, (ttk.Entry, ttk.Combobox)) and not self.blocked():
            self.begin()
            return "break"

    def begin(self):
        if self.blocked() or self.refresh_busy:
            return
        try:
            new = Poller(SmsManClient(self.token.get()), self.application_id.get(), self.country_id.get(), self.events,
                         max_price=self.max_price.get(), currency=self.currency.get(), auto_reissue=self.auto_reissue.get(), max_activations=int(self.max_count.get()))
        except (ValueError, OverflowError):
            messagebox.showerror("입력 확인", "토큰, 양의 정수 ID·단가, 통화, 최대 발급 수(1~5)를 확인하세요.")
            return
        summary = (f"국가 ID {new.country_id} / 서비스 ID {new.application_id}\n"
                   f"단가 상한 {new.max_price} {new.currency} / 최대 {new.max_activations}회\n"
                   f"자동 재발급: {'ON' if new.auto_reissue else 'OFF'}\n"
                   "SMS-Man 잔액이 사용됩니다. 취소·환불은 제공자 정책에 따릅니다.\n구매를 시작할까요?")
        if not messagebox.askyesno("구매 확인", summary):
            return
        self.refresh_generation += 1
        self.feedback = ""
        self.poller = new
        self.view.begin(new.run_id)
        new.start()
        self.render()

    def stop(self):
        if self.busy():
            self.poller.stop()
            self.tell(STATE_LABELS["STOPPING"])

    def retry(self):
        if self.poller:
            self.poller.retry_cleanup()
            self.render()

    def resolve(self):
        if self.poller and not self.busy() and self.poller.state == "UNRESOLVED":
            if messagebox.askyesno("외부 확인", "SMS-Man 계정에서 발급·취소·비용을 확인하고 미정리 번호를 해결했습니까?\n이 버튼 자체는 서버 번호를 취소하지 않습니다."):
                self.poller.confirm_external_resolution()
                self.render()

    def copy_value(self, value):
        try:
            self.clipboard.copy(self.root, value)
            self.tell("클립보드에 복사했습니다.")
        except tk.TclError:
            self.tell("클립보드에 복사하지 못했습니다.")

    def copy_number(self):
        if not self.view.number:
            return
        try:
            self.copy_value(strip_country_code(self.view.number, self.calling_code.get()))
        except ApiError:
            self.tell("번호와 국가 전화 코드를 확인하세요.")

    def copy_code(self):
        if self.view.code:
            self.copy_value(self.view.code)

    def clear(self):
        if self.blocked() or self.refresh_busy:
            return
        try:
            self.clipboard.clear_owned(self.root)
        except tk.TclError:
            pass
        self.token.set("")
        self.feedback = ""
        self.poller, self.view = None, ViewState()
        self.refresh_generation += 1
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.render()

    def refresh(self):
        if self.blocked() or self.refresh_busy:
            return
        try:
            client = SmsManClient(self.token.get())
            aid, cid = positive_id(self.application_id.get()), positive_id(self.country_id.get())
        except ValueError:
            self.tell("토큰과 국가·서비스 ID를 확인하세요.")
            return
        self.refresh_busy = True
        self.refresh_generation += 1
        generation = self.refresh_generation
        self.render()
        def worker():
            try:
                result = f"{client.get_limits(application_id=aid, country_id=cid)}개 (국가 {cid} / 서비스 {aid})"
            except Exception:
                result = "재고 조회 실패. 토큰·설정·연결을 확인하세요."
            self.events.put((generation, result))
        threading.Thread(target=worker, name="sms-man-limits", daemon=True).start()

    def activity(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S  ") + text + "\n")
        lines = int(self.log.index("end-1c").split(".")[0]) - 1
        if lines > 500:
            self.log.delete("1.0", f"{lines - 500 + 1}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def consume(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if isinstance(event, tuple):
                generation, value = event
                self.refresh_busy = False
                if generation == self.refresh_generation:
                    self.availability.set(value)
                continue
            if not self.view.apply(event):
                continue
            if event.kind == "state":
                self.feedback = ""
                self.activity(STATE_LABELS[event.value])
            elif event.kind == "number":
                self.activity("번호 발급: " + "*" * max(0, len(event.value) - 4) + event.value[-4:])
            elif event.kind == "code":
                self.activity("SMS 코드 수신 완료")
                if self.auto_copy.get() and not self.closing and self.poller and not self.poller.stop_event.is_set():
                    self.copy_value(event.value)
        self.render()
        if self.closing and self.finish_close():
            return
        self.after_id = self.root.after(100, self.consume)

    def render(self):
        blocked = self.blocked() or self.refresh_busy
        for field in self.fields:
            field.configure(state="disabled" if blocked else "normal")
        self.currency_box.configure(state="disabled" if blocked else "readonly")
        self.reissue_box.configure(state="disabled" if blocked else "normal")
        for button in (self.start_button, self.clear_button, self.refresh_button):
            button.configure(state="disabled" if blocked else "normal")
        self.stop_button.configure(state="normal" if self.busy() else "disabled")
        unresolved = self.poller is not None and self.poller.state == "UNRESOLVED" and not self.busy()
        self.retry_button.configure(state="normal" if unresolved and self.poller.activation else "disabled")
        self.resolved_button.configure(state="normal" if unresolved else "disabled")
        self.number_button.configure(state="normal" if self.view.number else "disabled")
        self.code_button.configure(state="normal" if self.view.code else "disabled")
        self.number.set(self.view.number or "—")
        self.code.set(self.view.code or "—")
        self.status.set(self.feedback or STATE_LABELS[self.view.state])

    def help(self):
        path = Path(__file__).parent / self.doc_choice.get()
        if path.name not in DOCS:
            return
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            self.tell("안내 파일을 찾을 수 없습니다. 배포 파일 구성을 확인하세요.")
            return
        win = tk.Toplevel(self.root)
        win.title(path.name)
        text = tk.Text(win, wrap="word", width=85, height=30)
        text.pack(fill="both", expand=True)
        text.insert("1.0", content)
        text.configure(state="disabled")

    def close(self):
        self.closing, self.close_started = True, time.monotonic()
        self.stop()
        self.finish_close()

    def finish_close(self):
        unresolved = self.poller is not None and self.poller.state == "UNRESOLVED"
        if self.busy() and time.monotonic() - self.close_started < 20:
            return False
        if self.busy() or unresolved:
            if not messagebox.askyesno("미정리 요청 주의", "종료해도 서버 번호가 취소된 것은 아닙니다.\nSMS-Man 계정에서 발급·취소·비용을 확인해야 합니다.\n그래도 종료할까요?"):
                self.closing = False
                return False
        self.dispose()
        self.root.destroy()
        return True

    def dispose(self):
        self.root.after_cancel(self.after_id)
        self.viewport.destroy()


def run():
    root = tk.Tk()
    root.title("SMS-Man Auto")
    Application(root)
    root.update_idletasks()
    root.minsize(min(640, root.winfo_screenwidth() - 80), min(400, root.winfo_screenheight() - 100))
    root.geometry(f"{min(800, root.winfo_screenwidth() - 80)}x{min(760, root.winfo_screenheight() - 100)}")
    root.mainloop()
