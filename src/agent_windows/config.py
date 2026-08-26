from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    file = Path(path)
    if not file.exists():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    log_level: str
    llm_order: tuple[str, ...]
    llm_timeout: float
    llm_attempts: int
    retry_base: float
    retry_max: float
    transient_cooldown: float
    rate_cooldown: float
    auth_cooldown: float
    groq_key: str
    groq_model: str
    gemini_key: str
    gemini_model: str
    openrouter_key: str
    openrouter_model: str
    local_llm_url: str
    local_llm_model: str
    assemblyai_key: str
    deepgram_key: str
    stt_order: tuple[str, ...]
    elevenlabs_key: str
    elevenlabs_voice: str
    elevenlabs_model: str
    relay_url: str
    relay_token: str
    direct_allowed: bool
    microphone_device: str
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    realtime_backend: str = "auto"
    livekit_agent_name: str = "ai-aharon"
    wigolo_url: str = "http://127.0.0.1:3333"
    wigolo_token: str = ""
    firecrawl_key: str = ""
    firecrawl_url: str = "https://api.firecrawl.dev"

    @classmethod
    def from_env(cls, dotenv: str | Path = ".env") -> "Settings":
        load_dotenv(dotenv)
        split = lambda value: tuple(x.strip() for x in value.split(",") if x.strip())
        return cls(
            Path(os.getenv("AGENT_DATA_DIR", "data")), os.getenv("AGENT_LOG_LEVEL", "INFO"),
            split(os.getenv("AGENT_LLM_ORDER", "groq,gemini,openrouter,local")),
            _float("AGENT_LLM_TIMEOUT_SECONDS", 30),
            _int("AGENT_LLM_MAX_ATTEMPTS", 2), _float("AGENT_LLM_RETRY_BASE_SECONDS", .25),
            _float("AGENT_LLM_RETRY_MAX_SECONDS", 2), _float("AGENT_LLM_TRANSIENT_COOLDOWN_SECONDS", 15),
            _float("AGENT_LLM_RATE_LIMIT_COOLDOWN_SECONDS", 60), _float("AGENT_LLM_AUTH_COOLDOWN_SECONDS", 300),
            os.getenv("GROQ_API_KEY", ""), os.getenv("GROQ_MODEL", ""),
            os.getenv("GEMINI_API_KEY", ""), os.getenv("GEMINI_MODEL", ""),
            os.getenv("OPENROUTER_API_KEY", ""), os.getenv("OPENROUTER_MODEL", ""),
            os.getenv("LOCAL_LLM_BASE_URL", ""), os.getenv("LOCAL_LLM_MODEL", ""),
            os.getenv("ASSEMBLYAI_API_KEY", ""), os.getenv("DEEPGRAM_API_KEY", ""),
            split(os.getenv("AGENT_STT_ORDER", "assemblyai,deepgram")),
            os.getenv("ELEVENLABS_API_KEY", ""), os.getenv("ELEVENLABS_VOICE_ID", ""),
            os.getenv("ELEVENLABS_MODEL", "eleven_v3"), os.getenv("AGENT_RELAY_BASE_URL", "").rstrip("/"),
            os.getenv("AGENT_RELAY_TOKEN", ""), os.getenv("AGENT_DIRECT_ALLOWED", "true").lower() in {"1","true","yes"},
            os.getenv("AGENT_MICROPHONE_DEVICE", "default"),
            os.getenv("LIVEKIT_URL", ""), os.getenv("LIVEKIT_API_KEY", ""),
            os.getenv("LIVEKIT_API_SECRET", ""), os.getenv("AGENT_REALTIME_BACKEND", "auto"),
            os.getenv("LIVEKIT_AGENT_NAME", "ai-aharon"),
            os.getenv("WIGOLO_BASE_URL", "http://127.0.0.1:3333"),
            os.getenv("WIGOLO_TOKEN", ""), os.getenv("FIRECRAWL_API_KEY", ""),
            os.getenv("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev"),
        )
