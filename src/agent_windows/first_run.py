from __future__ import annotations

import argparse
from pathlib import Path

from .settings_ui import has_primary_provider_key, read_env_file, show_settings_window


def _desktop_args(env_path: Path, minimized: bool) -> list[str]:
    args = ["--env", str(env_path)]
    if minimized:
        args.append("--minimized")
    return args


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ai-aharon-first-run")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--minimized", action="store_true")
    args = parser.parse_args(argv)
    env_path = Path(args.env).expanduser().resolve()

    if has_primary_provider_key(read_env_file(env_path)):
        from .desktop_gui import main as desktop_main

        return desktop_main(_desktop_args(env_path, args.minimized))

    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    saved = {"value": False}

    def finish_saved() -> None:
        saved["value"] = True
        root.after(0, root.quit)

    window = show_settings_window(root, env_path, on_saved=finish_saved)

    def cancel_setup() -> None:
        try:
            window.destroy()
        finally:
            root.quit()

    window.protocol("WM_DELETE_WINDOW", cancel_setup)
    root.mainloop()
    root.destroy()

    if saved["value"] and has_primary_provider_key(read_env_file(env_path)):
        from .desktop_gui import main as desktop_main

        return desktop_main(_desktop_args(env_path, args.minimized))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
