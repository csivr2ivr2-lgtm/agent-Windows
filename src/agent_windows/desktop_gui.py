from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import logging
import os
import sys
import threading
import time
from pathlib import Path

from .config import Settings
from .logging_utils import configure_logging
from .runtime import AgentRuntime
from .realtime import LocalRealtimeSession, RealtimeState
from .service_api import service_chat, service_health
from .voice_runtime import MicrophoneUnavailable

LOGGER = logging.getLogger(__name__)
APP_NAME = "ai aharon"
APP_USER_MODEL_ID = "ai.aharon.desktop"
HOTKEY_ID = 0xA618
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_SPACE = 0x20
WM_HOTKEY = 0x0312
WM_SETICON = 0x0080
HEBREW_LABELS = {
    "ready": "מוכן",
    "connecting": "מתחבר",
    "listening": "מקשיב",
    "thinking": "חושב",
    "speaking": "מדבר",
    "error": "שגיאה",
    "connected": "שירות מחובר",
    "disconnected": "שירות לא זמין",
    "voice_only": "שיחה קולית רציפה",
    "hint": "דבר כרגיל. אין צורך ללחוץ על כפתור.",
    "end": "סיום שיחה",
}


def _icon_path() -> Path | None:
    for path in (
        Path(__file__).resolve().parents[2] / "assets" / "ai-aharon.ico",
        Path.cwd() / "assets" / "ai-aharon.ico",
    ):
        if path.is_file():
            return path
    return None


def _set_windows_app_identity() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        LOGGER.debug("Could not set AppUserModelID", exc_info=True)


def _apply_windows_icon(root) -> None:
    icon = _icon_path()
    if not icon:
        return
    try:
        root.iconname(APP_NAME)
        root.iconbitmap(default=str(icon))
    except Exception:
        LOGGER.debug("Could not load Tk icon", exc_info=True)
    if not sys.platform.startswith("win"):
        return
    try:
        root.update_idletasks()
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        for size, slot in ((32, 1), (16, 0)):
            handle = user32.LoadImageW(None, str(icon), 1, size, size, 0x0010 | 0x0040)
            if handle:
                user32.SendMessageW(root.winfo_id(), WM_SETICON, slot, handle)
    except Exception:
        LOGGER.debug("Could not force Windows taskbar icon", exc_info=True)


class AgentDesktopApp:
    """Voice-only, continuous turn-taking desktop client."""

    def __init__(self, root, runtime: AgentRuntime, settings: Settings, *, auto_start: bool = True):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.root, self.runtime, self.settings = root, runtime, settings
        self._closing = threading.Event()
        self._call_active = threading.Event()
        self._call_started_at: float | None = None
        self._realtime_session: LocalRealtimeSession | None = None
        self._call_thread: threading.Thread | None = None

        root.title(APP_NAME)
        root.geometry("470x590")
        root.minsize(430, 520)
        root.configure(background="#eef4fb")
        root.protocol("WM_DELETE_WINDOW", self.close)
        _apply_windows_icon(root)
        self._build_ui()
        self._start_health_monitor()
        self._start_hotkey_listener()
        self._tick_timer()
        if auto_start:
            root.after(350, self.start_call)

    def _build_ui(self) -> None:
        tk, ttk = self.tk, self.ttk
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("App.TFrame", background="#eef4fb")
        style.configure("Header.TLabel", background="#eef4fb", foreground="#162033")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Card.TLabel", background="#ffffff", foreground="#162033")
        style.configure("State.TLabel", background="#ffffff", foreground="#0759b8")
        style.configure("Meta.TLabel", background="#ffffff", foreground="#667085")

        outer = ttk.Frame(self.root, padding=24, style="App.TFrame")
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x", pady=(0, 18))
        ttk.Label(header, text=APP_NAME, font=("Segoe UI", 21, "bold"), style="Header.TLabel").pack(side="right")
        self.service_label = ttk.Label(header, text="בודק חיבור…", style="Header.TLabel")
        self.service_label.pack(side="left")

        card = ttk.Frame(outer, padding=(28, 34), style="Card.TFrame")
        card.pack(fill="both", expand=True)
        avatar = tk.Canvas(card, width=142, height=142, highlightthickness=0, bg="#ffffff")
        avatar.pack(pady=(10, 24))
        avatar.create_oval(8, 8, 134, 134, fill="#0b6bdc", outline="")
        avatar.create_text(71, 69, text="A", fill="white", font=("Segoe UI", 54, "bold"))

        ttk.Label(card, text=HEBREW_LABELS["voice_only"], font=("Segoe UI", 15, "bold"), style="Card.TLabel").pack(pady=(0, 8))
        self.status_var = tk.StringVar(value=HEBREW_LABELS["ready"])
        ttk.Label(card, textvariable=self.status_var, font=("Segoe UI", 26, "bold"), style="State.TLabel").pack(pady=(2, 6))
        self.timer_var = tk.StringVar(value="00:00")
        ttk.Label(card, textvariable=self.timer_var, font=("Segoe UI", 12), style="Meta.TLabel").pack(pady=(0, 18))
        ttk.Label(card, text=HEBREW_LABELS["hint"], justify="center", font=("Segoe UI", 11), style="Meta.TLabel").pack(fill="x", pady=(6, 20))

        tk.Button(
            card,
            text="☎  " + HEBREW_LABELS["end"],
            command=self.close,
            font=("Segoe UI", 12, "bold"),
            bg="#d92d20",
            fg="white",
            activebackground="#b42318",
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=26,
            pady=12,
            cursor="hand2",
        ).pack(pady=(8, 4))
        ttk.Label(outer, text="Ctrl + Alt + Space פותח שיחה קולית מכל מקום", anchor="center", style="Header.TLabel").pack(fill="x", pady=(14, 0))

    def _set_status(self, value: str) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self._set_status(value))
        else:
            self.status_var.set(value)

    def _set_service_status(self, ok: bool) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self._set_service_status(ok))
        else:
            label = HEBREW_LABELS["connected"] if ok else HEBREW_LABELS["disconnected"]
            self.service_label.configure(text="● " + label)

    def _answer(self, text: str) -> str:
        if service_health(self.settings.data_dir):
            self._set_service_status(True)
            return service_chat(text, self.settings.data_dir)
        self._set_service_status(False)
        return self.runtime.handle_text(text)

    def start_call(self) -> None:
        if self._closing.is_set() or self._call_active.is_set():
            return
        self._call_active.set()
        self._call_started_at = time.monotonic()
        self._call_thread = threading.Thread(
            target=self._call_loop, daemon=True, name="AiAharonCall"
        )
        self._call_thread.start()

    def _on_realtime_state(self, state: RealtimeState) -> None:
        labels = {
            RealtimeState.CONNECTING: HEBREW_LABELS["connecting"],
            RealtimeState.LISTENING: HEBREW_LABELS["listening"],
            RealtimeState.USER_SPEAKING: HEBREW_LABELS["listening"],
            RealtimeState.THINKING: HEBREW_LABELS["thinking"],
            RealtimeState.SPEAKING: HEBREW_LABELS["speaking"],
            RealtimeState.INTERRUPTING: HEBREW_LABELS["listening"],
            RealtimeState.ERROR: HEBREW_LABELS["error"],
            RealtimeState.ENDING: HEBREW_LABELS["ready"],
        }
        self._set_status(labels[state])

    def _call_loop(self) -> None:
        try:
            self._realtime_session = LocalRealtimeSession(
                self.runtime, status_callback=self._on_realtime_state
            )
            self._realtime_session.run(
                lambda: self._call_active.is_set() and not self._closing.is_set()
            )
        except MicrophoneUnavailable as exc:
            LOGGER.warning("Microphone unavailable: %s", exc)
            self._set_status(HEBREW_LABELS["error"])
        except Exception:
            LOGGER.exception("Realtime voice interaction failed")
            self._set_status(HEBREW_LABELS["error"])
        finally:
            self._realtime_session = None
            self._call_active.clear()

    def _tick_timer(self) -> None:
        if self._closing.is_set():
            return
        if self._call_active.is_set() and self._call_started_at is not None:
            elapsed = int(time.monotonic() - self._call_started_at)
            minutes, seconds = divmod(elapsed, 60)
            hours, minutes = divmod(minutes, 60)
            self.timer_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}")
        self.root.after(1000, self._tick_timer)

    def _start_health_monitor(self) -> None:
        def monitor() -> None:
            while not self._closing.wait(8):
                self._set_service_status(service_health(self.settings.data_dir))
        threading.Thread(target=monitor, daemon=True, name="AgentGuiHealth").start()
        threading.Thread(target=lambda: self._set_service_status(service_health(self.settings.data_dir)), daemon=True).start()

    def _start_hotkey_listener(self) -> None:
        def listen() -> None:
            user32 = ctypes.windll.user32
            if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_SPACE):
                return
            try:
                msg = wintypes.MSG()
                while not self._closing.is_set():
                    if user32.GetMessageW(ctypes.byref(msg), None, 0, 0) <= 0:
                        return
                    if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                        self.root.after(0, self._show_and_start_call)
            finally:
                user32.UnregisterHotKey(None, HOTKEY_ID)
        threading.Thread(target=listen, daemon=True, name="AgentGuiHotkey").start()

    def _show_and_start_call(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.start_call()

    def close(self) -> None:
        if self._closing.is_set():
            return
        self._call_active.clear()
        self._closing.set()
        try:
            self.runtime.close()
        except Exception:
            LOGGER.exception("Failed closing desktop runtime")
        self.root.destroy()


def _configure_file_logging(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(data_dir / "desktop-gui.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


def main(argv=None) -> int:
    if not sys.platform.startswith("win"):
        print("הממשק ai aharon זמין רק ב-Windows.", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="ai-aharon")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--minimized", action="store_true")
    args = parser.parse_args(argv)
    env_path = Path(args.env).expanduser().resolve()
    os.chdir(env_path.parent)
    settings = Settings.from_env(env_path)
    configure_logging(settings.log_level)
    _configure_file_logging(settings.data_dir)
    _set_windows_app_identity()

    import tkinter as tk
    root = tk.Tk()
    runtime = AgentRuntime(settings)
    AgentDesktopApp(root, runtime, settings, auto_start=not args.minimized)
    if args.minimized:
        root.after(200, root.iconify)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
