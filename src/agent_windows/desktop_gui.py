from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import logging
import os
import sys
import threading
from pathlib import Path

from .config import Settings
from .logging_utils import configure_logging
from .runtime import AgentRuntime
from .service_api import service_chat, service_health


LOGGER = logging.getLogger(__name__)
HOTKEY_ID = 0xA618
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_SPACE = 0x20
WM_HOTKEY = 0x0312


class AgentDesktopApp:
    def __init__(self, root, runtime: AgentRuntime, settings: Settings):
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.runtime = runtime
        self.settings = settings
        self._busy_lock = threading.Lock()
        self._closing = threading.Event()
        self._service_ok = False

        root.title("Agent Windows AI")
        root.geometry("720x760")
        root.minsize(560, 560)
        root.protocol("WM_DELETE_WINDOW", self.close)

        try:
            root.iconname("Agent Windows AI")
        except Exception:
            pass

        self._build_ui(scrolledtext)
        self._start_health_monitor()
        self._start_hotkey_listener()

    def _build_ui(self, scrolledtext) -> None:
        ttk = self.ttk
        tk = self.tk

        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="Agent Windows AI", font=("Segoe UI", 18, "bold")).pack(side="right")
        self.service_label = ttk.Label(header, text="בודק שירות…")
        self.service_label.pack(side="left")

        self.status_var = tk.StringVar(value="מוכן")
        ttk.Label(outer, textvariable=self.status_var, anchor="e").pack(fill="x", pady=(0, 8))

        self.chat = scrolledtext.ScrolledText(
            outer,
            wrap="word",
            state="disabled",
            font=("Segoe UI", 11),
            padx=10,
            pady=10,
        )
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_configure("user", justify="right", spacing3=8)
        self.chat.tag_configure("agent", justify="right", spacing3=12)
        self.chat.tag_configure("system", justify="center", spacing3=8)

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(12, 0))

        self.entry = ttk.Entry(controls, font=("Segoe UI", 11), justify="right")
        self.entry.pack(side="right", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _event: self.send_text())

        self.send_button = ttk.Button(controls, text="שלח", command=self.send_text)
        self.send_button.pack(side="right", padx=(8, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(10, 0))

        self.mic_button = ttk.Button(actions, text="🎤  דבר", command=self.start_voice)
        self.mic_button.pack(side="right", ipadx=20, ipady=8)

        self.speak_text = tk.BooleanVar(value=False)
        ttk.Checkbutton(actions, text="הקרא גם הודעות כתובות", variable=self.speak_text).pack(side="right", padx=12)

        ttk.Button(actions, text="נקה שיחה", command=self.clear_chat).pack(side="left")

        ttk.Separator(outer).pack(fill="x", pady=(12, 8))
        ttk.Label(
            outer,
            text="קיצור דרך מכל מקום: Ctrl + Alt + Space",
            anchor="center",
        ).pack(fill="x")

        self._append("system", "הסוכן מוכן. אפשר לכתוב או ללחוץ על 'דבר'.")

    def _append(self, role: str, text: str) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self._append(role, text))
            return
        prefix = {"user": "אתה: ", "agent": "Agent: ", "system": ""}.get(role, "")
        self.chat.configure(state="normal")
        self.chat.insert("end", prefix + text.strip() + "\n", role)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _set_status(self, text: str) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self._set_status(text))
            return
        self.status_var.set(text)

    def _set_service_status(self, ok: bool) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self._set_service_status(ok))
            return
        self._service_ok = ok
        self.service_label.configure(text="● שירות מחובר" if ok else "● שירות לא זמין")

    def _begin_job(self) -> bool:
        if not self._busy_lock.acquire(blocking=False):
            self._set_status("הסוכן כבר מטפל בבקשה…")
            return False
        self.root.after(0, lambda: self._set_controls_enabled(False))
        return True

    def _end_job(self) -> None:
        try:
            self._busy_lock.release()
        except RuntimeError:
            pass
        self.root.after(0, lambda: self._set_controls_enabled(True))
        self._set_status("מוכן")

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.send_button.configure(state=state)
        self.mic_button.configure(state=state)
        self.entry.configure(state=state)
        if enabled:
            self.entry.focus_set()

    def _answer(self, text: str) -> str:
        if service_health(self.settings.data_dir):
            self._set_service_status(True)
            return service_chat(text, self.settings.data_dir)
        self._set_service_status(False)
        LOGGER.warning("Windows service unavailable; using desktop fallback")
        return self.runtime.handle_text(text)

    def send_text(self) -> None:
        text = self.entry.get().strip()
        if not text or not self._begin_job():
            return
        self.entry.delete(0, "end")
        self._append("user", text)
        threading.Thread(
            target=self._text_worker,
            args=(text, bool(self.speak_text.get())),
            daemon=True,
            name="AgentGuiText",
        ).start()

    def _text_worker(self, text: str, speak: bool) -> None:
        try:
            self._set_status("חושב…")
            answer = self._answer(text)
            self._append("agent", answer)
            if speak:
                self._set_status("מדבר…")
                self.runtime.voice.speak(answer)
        except Exception as exc:
            LOGGER.exception("GUI text interaction failed")
            self._append("system", f"שגיאה: {exc}")
        finally:
            self._end_job()

    def start_voice(self) -> None:
        if not self._begin_job():
            return
        threading.Thread(target=self._voice_worker, daemon=True, name="AgentGuiVoice").start()

    def _voice_worker(self) -> None:
        try:
            self._set_status("מקשיב… דבר עכשיו")
            text = self.runtime.voice.listen()
            self._append("user", text)
            self._set_status("חושב…")
            answer = self._answer(text)
            self._append("agent", answer)
            self._set_status("מדבר…")
            self.runtime.voice.speak(answer)
        except Exception as exc:
            LOGGER.exception("GUI voice interaction failed")
            self._append("system", f"שגיאת קול: {exc}")
        finally:
            self._end_job()

    def clear_chat(self) -> None:
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")

    def _start_health_monitor(self) -> None:
        def monitor() -> None:
            while not self._closing.wait(8):
                self._set_service_status(service_health(self.settings.data_dir))

        threading.Thread(target=monitor, daemon=True, name="AgentGuiHealth").start()
        threading.Thread(
            target=lambda: self._set_service_status(service_health(self.settings.data_dir)),
            daemon=True,
            name="AgentGuiHealthInitial",
        ).start()

    def _start_hotkey_listener(self) -> None:
        def listen() -> None:
            user32 = ctypes.windll.user32
            if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_SPACE):
                LOGGER.warning("Could not register Ctrl+Alt+Space")
                self._append("system", "לא ניתן לרשום Ctrl+Alt+Space; אפשר להשתמש בכפתור 'דבר'.")
                return
            try:
                msg = wintypes.MSG()
                while not self._closing.is_set():
                    result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if result <= 0:
                        return
                    if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                        self.root.after(0, self.start_voice)
            finally:
                user32.UnregisterHotKey(None, HOTKEY_ID)

        threading.Thread(target=listen, daemon=True, name="AgentGuiHotkey").start()

    def close(self) -> None:
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
        print("Agent Windows GUI is only available on Windows.", file=sys.stderr)
        return 2

    parser = argparse.ArgumentParser(prog="agent-windows-gui")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--minimized", action="store_true")
    args = parser.parse_args(argv)

    env_path = Path(args.env).expanduser().resolve()
    os.chdir(env_path.parent)
    settings = Settings.from_env(env_path)
    configure_logging(settings.log_level)
    _configure_file_logging(settings.data_dir)

    import tkinter as tk

    root = tk.Tk()
    runtime = AgentRuntime(settings)
    AgentDesktopApp(root, runtime, settings)
    if args.minimized:
        root.after(200, root.iconify)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
