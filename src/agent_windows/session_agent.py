from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import logging
import os
import sys
from pathlib import Path

from .config import Settings
from .logging_utils import configure_logging
from .runtime import AgentRuntime
from .service_api import service_chat, service_health


LOGGER = logging.getLogger(__name__)
HOTKEY_ID = 0xA617
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_SPACE = 0x20
WM_HOTKEY = 0x0312


def _configure_file_logging(data_dir: Path) -> logging.FileHandler:
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "session-agent.log"
    log_path.touch(exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    return handler


def _register_hotkey() -> None:
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_SPACE):
        raise RuntimeError("Could not register Ctrl+Alt+Space; another application may already use it")


def _unregister_hotkey() -> None:
    try:
        ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
    except Exception:
        pass


def _message_loop(runtime: AgentRuntime, settings: Settings) -> None:
    user32 = ctypes.windll.user32
    msg = wintypes.MSG()
    LOGGER.info("Session companion ready; press Ctrl+Alt+Space to speak")
    while True:
        result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if result <= 0:
            return
        if msg.message != WM_HOTKEY or msg.wParam != HOTKEY_ID:
            continue
        try:
            text = runtime.voice.listen()
            LOGGER.info("Voice input captured: %s", text)
            if service_health(settings.data_dir):
                answer = service_chat(text, settings.data_dir)
            else:
                LOGGER.warning("Windows service unavailable; using in-session fallback")
                answer = runtime.handle_text(text)
            LOGGER.info("Agent answer: %s", answer)
            runtime.voice.speak(answer)
        except Exception:
            LOGGER.exception("Voice interaction failed")


def main(argv=None) -> int:
    if not sys.platform.startswith("win"):
        print("Session companion is only available on Windows.", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="agent-windows-session")
    parser.add_argument("--env", default=".env")
    args = parser.parse_args(argv)
    env_path = Path(args.env).expanduser().resolve()
    os.chdir(env_path.parent)
    settings = Settings.from_env(env_path)
    configure_logging(settings.log_level)
    file_handler = _configure_file_logging(settings.data_dir)
    logging.getLogger().addHandler(file_handler)
    try:
        _register_hotkey()
        try:
            with AgentRuntime(settings) as runtime:
                _message_loop(runtime, settings)
        finally:
            _unregister_hotkey()
        return 0
    finally:
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
