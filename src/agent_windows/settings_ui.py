from __future__ import annotations

import os
import tempfile
import webbrowser
from pathlib import Path
from typing import Callable


SECRET_FIELDS = (
    ("GROQ_API_KEY", "Groq API key"),
    ("GEMINI_API_KEY", "Gemini API key"),
    ("OPENROUTER_API_KEY", "OpenRouter API key"),
    ("ASSEMBLYAI_API_KEY", "AssemblyAI API key"),
    ("DEEPGRAM_API_KEY", "Deepgram API key"),
    ("ELEVENLABS_API_KEY", "ElevenLabs API key"),
    ("CARTESIA_API_KEY", "Cartesia API key"),
    ("LIVEKIT_API_KEY", "LiveKit API key"),
    ("LIVEKIT_API_SECRET", "LiveKit API secret"),
    ("FIRECRAWL_API_KEY", "Firecrawl API key"),
    ("OPENVIKING_API_KEY", "OpenViking API key"),
    ("QDRANT_API_KEY", "Qdrant API key"),
    ("PINECONE_API_KEY", "Pinecone API key"),
    ("WIGOLO_TOKEN", "Wigolo token"),
    ("AGENT_RELAY_TOKEN", "Relay token"),
)

PRIMARY_PROVIDER_KEYS = (
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
)

KEY_ACTIONS = {
    "GROQ_API_KEY": ("צור מפתח ↗", "https://console.groq.com/keys"),
    "GEMINI_API_KEY": ("צור מפתח ↗", "https://aistudio.google.com/apikey"),
    "OPENROUTER_API_KEY": ("צור מפתח ↗", "https://openrouter.ai/settings/keys"),
    "ASSEMBLYAI_API_KEY": ("צור מפתח ↗", "https://www.assemblyai.com/dashboard"),
    "DEEPGRAM_API_KEY": ("צור מפתח ↗", "https://console.deepgram.com/"),
    "ELEVENLABS_API_KEY": ("צור מפתח ↗", "https://elevenlabs.io/app/settings/api-keys"),
    "CARTESIA_API_KEY": ("צור מפתח ↗", "https://play.cartesia.ai/keys"),
    "LIVEKIT_API_KEY": ("פתח Credentials ↗", "https://cloud.livekit.io/"),
    "LIVEKIT_API_SECRET": ("פתח Credentials ↗", "https://cloud.livekit.io/"),
    "FIRECRAWL_API_KEY": ("צור מפתח ↗", "https://www.firecrawl.dev/app"),
    "OPENVIKING_API_KEY": ("הוראות ↗", "https://docs.openviking.ai/en/guides/04-authentication"),
    "QDRANT_API_KEY": ("צור מפתח ↗", "https://cloud.qdrant.io/"),
    "PINECONE_API_KEY": ("צור מפתח ↗", "https://app.pinecone.io/"),
    "WIGOLO_TOKEN": ("הוראות ↗", "https://knockoutez.github.io/wigolo/docs/installation/"),
    "AGENT_RELAY_TOKEN": (
        "הוראות ↗",
        "https://github.com/csivr2ivr2-lgtm/agent-Windows/blob/main/relay/README.md",
    ),
}

_PLACEHOLDER_MARKERS = (
    "your-key",
    "your_api_key",
    "replace-me",
    "change-me",
    "changeme",
    "example",
)


def _looks_configured(value: str) -> bool:
    clean = value.strip().casefold()
    return bool(clean) and not any(marker in clean for marker in _PLACEHOLDER_MARKERS)


def has_primary_provider_key(values: dict[str, str]) -> bool:
    """Return True when at least one usable cloud LLM key is configured."""
    return any(_looks_configured(values.get(key, "")) for key in PRIMARY_PROVIDER_KEYS)


TEXT_FIELDS = (
    ("GROQ_MODEL", "Groq model"),
    ("GEMINI_MODEL", "Gemini model"),
    ("OPENROUTER_MODEL", "OpenRouter model"),
    ("LOCAL_LLM_BASE_URL", "Local LLM URL"),
    ("LOCAL_LLM_MODEL", "Local LLM model"),
    ("ELEVENLABS_VOICE_ID", "ElevenLabs voice ID"),
    ("ELEVENLABS_MODEL", "ElevenLabs model"),
    ("LIVEKIT_URL", "LiveKit URL"),
    ("OPENVIKING_URL", "OpenViking URL"),
    ("QDRANT_URL", "Qdrant URL"),
    ("FIRECRAWL_BASE_URL", "Firecrawl URL"),
    ("WIGOLO_BASE_URL", "Wigolo URL"),
    ("AGENT_LLM_ORDER", "LLM order"),
    ("AGENT_STT_ORDER", "STT order"),
)


def read_env_file(path: str | Path) -> dict[str, str]:
    result: dict[str, str] = {}
    file = Path(path)
    if not file.exists():
        return result
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _env_value(value: str) -> str:
    clean = value.replace("\r", "").replace("\n", "").strip()
    if not clean:
        return ""
    if any(char.isspace() for char in clean) or "#" in clean or '"' in clean:
        return '"' + clean.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return clean


def _render_env(original: str, updates: dict[str, str]) -> str:
    remaining = dict(updates)
    output: list[str] = []
    for raw in original.splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={_env_value(remaining.pop(key))}")
                continue
        output.append(raw)

    if remaining:
        if output and output[-1].strip():
            output.append("")
        output.append("# Saved by AI Aharon settings")
        output.extend(f"{key}={_env_value(value)}" for key, value in remaining.items())
    return "\n".join(output).rstrip() + "\n"


def _atomic_replace_text(file: Path, text: str) -> None:
    """Write through a random same-directory file, then atomically replace .env."""
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=".ai-aharon-env-",
            suffix=".tmp",
            dir=file.parent,
            delete=False,
        ) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            temp_name = stream.name
        os.replace(temp_name, file)
        temp_name = None
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def update_env_file(path: str | Path, updates: dict[str, str]) -> None:
    """Atomically update a real .env file without following a file-level symlink."""
    file = Path(path).expanduser()
    if file.name != ".env":
        raise ValueError("settings may only be written to a .env file")
    if file.is_symlink():
        raise ValueError("refusing to write settings through a symbolic link")
    file.parent.mkdir(parents=True, exist_ok=True)
    original = file.read_text(encoding="utf-8") if file.exists() else ""
    _atomic_replace_text(file, _render_env(original, updates))


def _open_key_link(url: str, parent, messagebox) -> None:
    try:
        opened = webbrowser.open_new_tab(url)
    except Exception as exc:
        messagebox.showerror("פתיחת קישור נכשלה", str(exc), parent=parent)
        return
    if not opened:
        messagebox.showerror(
            "פתיחת קישור נכשלה",
            "לא הצלחתי לפתוח את הדפדפן. אפשר להעתיק את הכתובת ידנית.",
            parent=parent,
        )


def _add_section(
    ttk,
    tk,
    body,
    values,
    entries,
    secret_entries,
    title,
    fields,
    *,
    secret=False,
    parent=None,
    messagebox=None,
):
    ttk.Label(body, text=title, font=("Segoe UI", 11, "bold")).pack(
        anchor="e", fill="x", pady=(12, 5)
    )
    for key, label in fields:
        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=22, anchor="e").pack(side="right", padx=(8, 0))
        action = KEY_ACTIONS.get(key) if secret else None
        if action and parent is not None and messagebox is not None:
            action_text, url = action
            ttk.Button(
                row,
                text=action_text,
                command=lambda target=url: _open_key_link(target, parent, messagebox),
            ).pack(side="left", padx=(0, 8))
        variable = tk.StringVar(value=values.get(key, ""))
        entry = ttk.Entry(row, textvariable=variable, show="•" if secret else "")
        entry.pack(side="right", fill="x", expand=True)
        entries[key] = variable
        if secret:
            secret_entries.append(entry)


def _save_settings(window, env_path, entries, on_saved, messagebox) -> None:
    updates = {key: variable.get().strip() for key, variable in entries.items()}
    try:
        update_env_file(env_path, updates)
    except PermissionError:
        messagebox.showerror(
            "אין הרשאה",
            "Windows לא אפשר לכתוב את קובץ ההגדרות. הפעל את AI Aharon כמנהל מערכת ושמור שוב.",
            parent=window,
        )
        return
    except (OSError, ValueError) as exc:
        messagebox.showerror("שמירה נכשלה", str(exc), parent=window)
        return
    messagebox.showinfo(
        "נשמר",
        "ההגדרות נשמרו. הפעל מחדש את AI Aharon כדי שכל הספקים והשירות ייטענו עם הערכים החדשים.",
        parent=window,
    )
    if on_saved:
        on_saved()
    window.destroy()


def _cancel_settings(window, on_cancel: Callable[[], None] | None) -> None:
    try:
        window.destroy()
    finally:
        if on_cancel:
            on_cancel()


def show_settings_window(  # pragma: no cover - interactive Tk window
    parent,
    env_path: str | Path,
    *,
    on_saved: Callable[[], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
) -> object:
    import tkinter as tk
    from tkinter import messagebox, ttk

    values = read_env_file(env_path)
    window = tk.Toplevel(parent)
    window.title("AI Aharon — הגדרות")
    window.geometry("650x720")
    window.minsize(560, 520)

    outer = ttk.Frame(window, padding=16)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="הגדרות ספקים ומפתחות", font=("Segoe UI", 16, "bold")).pack(
        anchor="e", pady=(0, 8)
    )
    ttk.Label(
        outer,
        text="המפתחות נשמרים רק במחשב הזה ואינם נכללים בעדכוני התוכנה.",
        wraplength=600,
    ).pack(anchor="e", pady=(0, 12))

    canvas = tk.Canvas(outer, highlightthickness=0)
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    body = ttk.Frame(canvas)
    body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    body_id = canvas.create_window((0, 0), window=body, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(body_id, width=e.width))
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    entries: dict[str, tk.StringVar] = {}
    secret_entries = []
    ttk.Label(
        body,
        text="כדי להפעיל את הצ'אט בענן צריך לפחות מפתח אחד: Groq, Gemini או OpenRouter. שאר המפתחות אופציונליים לפי היכולות שבהן משתמשים.",
        wraplength=580,
    ).pack(anchor="e", fill="x", pady=(0, 6))
    _add_section(
        ttk,
        tk,
        body,
        values,
        entries,
        secret_entries,
        "מפתחות API",
        SECRET_FIELDS,
        secret=True,
        parent=window,
        messagebox=messagebox,
    )
    _add_section(ttk, tk, body, values, entries, secret_entries, "מודלים וניתוב", TEXT_FIELDS)

    show_secrets = tk.BooleanVar(value=False)

    def toggle_secrets() -> None:
        mask = "" if show_secrets.get() else "•"
        for entry in secret_entries:
            entry.configure(show=mask)

    ttk.Checkbutton(
        body, text="הצג מפתחות", variable=show_secrets, command=toggle_secrets
    ).pack(anchor="e", pady=(10, 4))

    buttons = ttk.Frame(window, padding=(16, 8, 16, 16))
    buttons.pack(fill="x")
    ttk.Button(buttons, text="ביטול", command=lambda: _cancel_settings(window, on_cancel)).pack(side="left")
    ttk.Button(
        buttons,
        text="שמור",
        command=lambda: _save_settings(window, env_path, entries, on_saved, messagebox),
    ).pack(side="right")
    window.grab_set()
    return window
