from __future__ import annotations

import os
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


def update_env_file(path: str | Path, updates: dict[str, str]) -> None:
    """Atomically update known keys without removing comments or unknown settings."""
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    original = file.read_text(encoding="utf-8") if file.exists() else ""
    lines = original.splitlines()
    remaining = dict(updates)
    output: list[str] = []

    for raw in lines:
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

    text = "\n".join(output).rstrip() + "\n"
    temporary = file.with_suffix(file.suffix + ".tmp")
    backup = file.with_suffix(file.suffix + ".bak")
    temporary.write_text(text, encoding="utf-8")
    if file.exists():
        backup.write_text(original, encoding="utf-8")
    os.replace(temporary, file)


def show_settings_window(  # pragma: no cover - interactive Tk window
    parent,
    env_path: str | Path,
    *,
    on_saved: Callable[[], None] | None = None,
) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    values = read_env_file(env_path)
    window = tk.Toplevel(parent)
    window.title("AI Aharon — הגדרות")
    window.geometry("650x720")
    window.minsize(560, 520)
    window.transient(parent)

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

    def add_section(title: str, fields, *, secret: bool = False) -> None:
        ttk.Label(body, text=title, font=("Segoe UI", 11, "bold")).pack(
            anchor="e", fill="x", pady=(12, 5)
        )
        for key, label in fields:
            row = ttk.Frame(body)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=22, anchor="e").pack(side="right", padx=(8, 0))
            variable = tk.StringVar(value=values.get(key, ""))
            entry = ttk.Entry(row, textvariable=variable, show="•" if secret else "")
            entry.pack(side="right", fill="x", expand=True)
            entries[key] = variable
            if secret:
                secret_entries.append(entry)

    add_section("מפתחות API", SECRET_FIELDS, secret=True)
    add_section("מודלים וניתוב", TEXT_FIELDS)

    show_secrets = tk.BooleanVar(value=False)

    def toggle_secrets() -> None:
        mask = "" if show_secrets.get() else "•"
        for entry in secret_entries:
            entry.configure(show=mask)

    ttk.Checkbutton(
        body, text="הצג מפתחות", variable=show_secrets, command=toggle_secrets
    ).pack(anchor="e", pady=(10, 4))

    def save() -> None:
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
        except OSError as exc:
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

    buttons = ttk.Frame(window, padding=(16, 8, 16, 16))
    buttons.pack(fill="x")
    ttk.Button(buttons, text="ביטול", command=window.destroy).pack(side="left")
    ttk.Button(buttons, text="שמור", command=save).pack(side="right")
    window.grab_set()
