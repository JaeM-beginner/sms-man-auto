import queue
import os
import unittest
from unittest.mock import patch, Mock
from sms_engine import Event, ViewState, ClipboardOwner


class FakeClipboard:
    def __init__(self):
        self.value = "other"

    def clipboard_clear(self):
        self.value = ""

    def clipboard_append(self, value):
        self.value += value

    def clipboard_get(self):
        return self.value


class ProjectionTests(unittest.TestCase):
    def test_stale_run_cannot_set_code_or_finish_new_run(self):
        view = ViewState()
        view.begin("new")
        self.assertFalse(view.apply(Event("old", "state", "SUCCESS", "1")))
        self.assertFalse(view.apply(Event("old", "code", "483921", "1")))
        self.assertEqual(view.state, "REQUESTING")
        self.assertEqual(view.code, "")

    def test_cancelled_activation_code_is_not_accepted(self):
        view = ViewState()
        view.begin("new")
        view.apply(Event("new", "number", "15551234567", "1"))
        view.apply(Event("new", "state", "STOPPED", "1"))
        self.assertFalse(view.apply(Event("new", "code", "483921", "1")))

    def test_clipboard_does_not_clear_other_application_value(self):
        cb, owner = FakeClipboard(), ClipboardOwner()
        owner.copy(cb, "483921")
        cb.value = "user work"
        owner.clear_owned(cb)
        self.assertEqual(cb.value, "user work")

    def test_owned_clipboard_can_be_cleared(self):
        cb, owner = FakeClipboard(), ClipboardOwner()
        owner.copy(cb, "483921")
        owner.clear_owned(cb)
        self.assertEqual(cb.value, "")


class HeadlessUiTests(unittest.TestCase):
    def test_focus_scroll_reveals_controls_below_short_viewport(self):
        import sms_ui
        self.assertTrue(hasattr(sms_ui, "focus_scroll_fraction"))
        # Control at 950..990 must be visible in a 400px viewport of a 1200px form.
        fraction = sms_ui.focus_scroll_fraction(0, 400, 1200, 950, 40)
        top = fraction * 1200
        self.assertLessEqual(top, 950)
        self.assertGreaterEqual(top + 400, 990)

    def test_focus_scroll_reveals_controls_above_viewport(self):
        import sms_ui
        self.assertTrue(hasattr(sms_ui, "focus_scroll_fraction"))
        self.assertEqual(sms_ui.focus_scroll_fraction(700, 400, 1200, 20, 25), 20 / 1200)

    def make(self):
        from sms_ui import Application
        ui = Application.__new__(Application)
        ui.poller, ui.view = None, ViewState()
        ui.closing = ui.refresh_busy = False
        ui.feedback = ""
        ui.fields = []
        for name in ("currency_box", "reissue_box", "start_button", "clear_button", "refresh_button", "stop_button", "retry_button", "resolved_button", "number_button", "code_button", "number", "code", "status"):
            setattr(ui, name, Mock())
        return ui

    def test_copy_failure_feedback_survives_periodic_render(self):
        import tkinter as tk
        ui = self.make()
        ui.clipboard, ui.root = Mock(), Mock()
        ui.clipboard.copy.side_effect = tk.TclError("not available")
        ui.copy_value("483921")
        ui.render()
        self.assertIn("클립보드", ui.status.set.call_args.args[0])

    def test_stop_feedback_survives_periodic_render(self):
        ui = self.make()
        ui.poller = Mock(running=True)
        ui.view.state = "WAITING_SMS"
        ui.stop()
        ui.render()
        self.assertIn("중지 및 미사용 번호 정리 중", ui.status.set.call_args.args[0])


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError:
            if os.environ.get("SMS_MAN_REQUIRE_GUI") == "1":
                raise RuntimeError("Required Windows GUI tests cannot run") from None
            raise unittest.SkipTest("Display unavailable; Windows GUI gate remains open")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from sms_ui import Application
        self.app = Application(self.root)

    def tearDown(self):
        self.app.dispose()

    def test_auto_copy_and_auto_purchase_default_off(self):
        self.assertFalse(self.app.auto_copy.get())
        self.assertFalse(self.app.auto_reissue.get())

    def test_clear_removes_sensitive_ui_state(self):
        self.app.token.set("dummy")
        self.app.view.number, self.app.view.code = "15551234567", "483921"
        self.app.clear()
        self.assertEqual(self.app.token.get(), "")
        self.assertEqual(self.app.view.code, "")

    def test_no_purchase_when_confirmation_cancelled(self):
        self.app.token.set("dummy")
        self.app.max_price.set("2")
        with patch("sms_ui.messagebox.askyesno", return_value=False):
            self.app.begin()
        self.assertIsNone(self.app.poller)

    def test_unknown_activation_blocks_start(self):
        from test_hardening import FakeClient
        from sms_engine import Poller
        self.app.poller = Poller(FakeClient(), "297", "140", queue.Queue(), max_price="2", currency="RUB")
        self.app.poller.state = "UNRESOLVED"
        with patch("sms_ui.messagebox.showwarning"):
            self.app.begin()
        self.assertEqual(self.app.poller.state, "UNRESOLVED")

    def test_short_window_can_scroll_to_last_control(self):
        self.root.geometry("700x400")
        self.root.update_idletasks()
        self.app.canvas.yview_moveto(1)
        self.root.update_idletasks()
        self.assertGreater(self.app.canvas.yview()[0], 0)
