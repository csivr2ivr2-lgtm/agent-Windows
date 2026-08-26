from __future__ import annotations

import asyncio
import importlib.util
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .config import Settings
from .orchestrator import DEFAULT_SYSTEM_PROMPT
from .runtime import AgentRuntime


@dataclass(frozen=True)
class LiveKitRuntimeStatus:
    installed: bool
    configured: bool
    backend: str
    agent_name: str

    def as_dict(self) -> dict[str, object]:
        return {
            "installed": self.installed,
            "configured": self.configured,
            "backend": self.backend,
            "agent_name": self.agent_name,
        }


def livekit_installed() -> bool:
    return importlib.util.find_spec("livekit.agents") is not None


def livekit_configured(settings: Settings) -> bool:
    return bool(settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret)


def realtime_backend(settings: Settings) -> str:
    requested = settings.realtime_backend.casefold().strip() or "auto"
    if requested not in {"auto", "local", "livekit"}:
        requested = "auto"
    if requested == "local":
        return "local"
    ready = livekit_installed() and livekit_configured(settings)
    if requested == "livekit":
        return "livekit" if ready else "local"
    return "livekit" if ready else "local"


def status(settings: Settings) -> LiveKitRuntimeStatus:
    return LiveKitRuntimeStatus(
        installed=livekit_installed(),
        configured=livekit_configured(settings),
        backend=realtime_backend(settings),
        agent_name=settings.livekit_agent_name,
    )


def _latest_user_text(chat_ctx: object) -> str:
    items = list(getattr(chat_ctx, "items", ()) or ())
    for item in reversed(items):
        role = str(getattr(item, "role", "")).casefold()
        if role != "user":
            continue
        value = getattr(item, "text_content", "")
        if callable(value):
            value = value()
        text = str(value or "").strip()
        if text:
            return text
    return ""


_END = object()


def _next_item(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return _END


async def _runtime_text_stream(runtime: AgentRuntime, text: str):
    cancel = threading.Event()
    iterator = runtime.stream_text(text, cancel_event=cancel)
    try:
        while True:
            item = await asyncio.to_thread(_next_item, iterator)
            if item is _END:
                return
            if item:
                yield str(item)
    finally:
        cancel.set()


def _load_livekit_modules() -> dict[str, Any]:
    try:
        from livekit.agents import Agent, AgentServer, AgentSession, TurnHandlingOptions, cli, stt
        from livekit.plugins import assemblyai, deepgram, elevenlabs
    except ImportError as exc:
        raise RuntimeError(
            "LiveKit realtime backend is not installed. Install the project realtime extra."
        ) from exc
    return {
        "Agent": Agent,
        "AgentServer": AgentServer,
        "AgentSession": AgentSession,
        "TurnHandlingOptions": TurnHandlingOptions,
        "cli": cli,
        "stt": stt,
        "assemblyai": assemblyai,
        "deepgram": deepgram,
        "elevenlabs": elevenlabs,
    }


def _build_stt(settings: Settings, modules: dict[str, Any]):
    instances = []
    for name in settings.stt_order:
        if name == "assemblyai" and settings.assemblyai_key:
            instances.append(
                modules["assemblyai"].STT(
                    api_key=settings.assemblyai_key,
                    model="universal-3-5-pro",
                    language_detection=True,
                    min_turn_silence=150,
                    max_turn_silence=1000,
                    vad_threshold=0.3,
                )
            )
        elif name == "deepgram" and settings.deepgram_key:
            instances.append(
                modules["deepgram"].STT(
                    api_key=settings.deepgram_key,
                    model="nova-3",
                    language="he",
                    sample_rate=16000,
                    interim_results=True,
                    smart_format=True,
                    vad_events=True,
                    endpointing_ms=300,
                    utterance_end_ms=1000,
                )
            )
    if not instances:
        raise RuntimeError("LiveKit backend needs AssemblyAI or Deepgram STT credentials")
    if len(instances) == 1:
        return instances[0]
    return modules["stt"].FallbackAdapter(
        instances,
        attempt_timeout=10.0,
        max_retry_per_stt=1,
        retry_interval=1.0,
    )


def _build_tts(settings: Settings, modules: dict[str, Any]):
    if not settings.elevenlabs_key or not settings.elevenlabs_voice:
        raise RuntimeError("LiveKit backend needs configured ElevenLabs TTS")
    return modules["elevenlabs"].TTS(
        api_key=settings.elevenlabs_key,
        voice_id=settings.elevenlabs_voice,
        model=settings.elevenlabs_model,
        language="he",
        auto_mode=True,
        enable_logging=False,
    )


def _runtime_agent_class(modules: dict[str, Any]):
    Agent = modules["Agent"]

    class RuntimeAgent(Agent):
        def __init__(self, runtime: AgentRuntime) -> None:
            super().__init__(
                instructions=DEFAULT_SYSTEM_PROMPT,
                allow_interruptions=True,
            )
            self.runtime = runtime

        async def llm_node(self, chat_ctx, tools, model_settings):
            del tools, model_settings
            text = _latest_user_text(chat_ctx)
            if not text:
                return
            async for chunk in _runtime_text_stream(self.runtime, text):
                yield chunk

    return RuntimeAgent


def create_server(
    *,
    dotenv: str = ".env",
    modules: dict[str, Any] | None = None,
    settings_factory: Callable[[str], Settings] = Settings.from_env,
    runtime_factory: Callable[[Settings], AgentRuntime] = AgentRuntime,
):
    """Build a real LiveKit AgentServer using AgentSession and shared ai-aharon LLM runtime."""
    modules = modules or _load_livekit_modules()
    settings = settings_factory(dotenv)
    if not livekit_configured(settings):
        raise RuntimeError("LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET are required")

    server = modules["AgentServer"]()
    RuntimeAgent = _runtime_agent_class(modules)

    async def entrypoint(ctx):
        runtime = runtime_factory(settings)

        async def cleanup():
            runtime.close()

        ctx.add_shutdown_callback(cleanup)
        session = modules["AgentSession"](
            stt=_build_stt(settings, modules),
            tts=_build_tts(settings, modules),
            vad=None,
            turn_handling=modules["TurnHandlingOptions"](
                turn_detection="stt",
                endpointing={"min_delay": 0.0, "max_delay": 1.5},
            ),
            max_tool_steps=3,
        )
        await session.start(room=ctx.room, agent=RuntimeAgent(runtime))
        await ctx.connect()

    server.rtc_session(entrypoint, agent_name=settings.livekit_agent_name)
    return server


def main() -> None:
    modules = _load_livekit_modules()
    server = create_server(modules=modules)
    modules["cli"].run_app(server)
