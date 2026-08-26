from __future__ import annotations

import os

from .config import load_dotenv
from .realtime import LiveKitSessionAdapter


DEFAULT_INSTRUCTIONS = (
    "אתה Aharon, סוכן אישי ל-Windows. ענה בעברית כברירת מחדל, בקצרה ובדיוק, "
    "והיה ניתן להפרעה טבעית בזמן דיבור."
)


def main() -> None:
    load_dotenv(os.getenv("AGENT_DOTENV", ".env"))
    adapter = LiveKitSessionAdapter(enabled=True)
    adapter.run(
        agent_name=os.getenv("LIVEKIT_AGENT_NAME", "ai-aharon"),
        instructions=os.getenv("LIVEKIT_AGENT_INSTRUCTIONS", DEFAULT_INSTRUCTIONS),
        stt_model=os.getenv("LIVEKIT_STT_MODEL", "deepgram/nova-3-general"),
        llm_model=os.getenv("LIVEKIT_LLM_MODEL", "google/gemma-4-31b-it"),
        tts_model=os.getenv("LIVEKIT_TTS_MODEL", "inworld/inworld-tts-2"),
        tts_voice=os.getenv("LIVEKIT_TTS_VOICE", "Ashley") or None,
    )


if __name__ == "__main__":
    main()
