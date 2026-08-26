import asyncio
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_windows.config import Settings
from agent_windows.livekit_runtime import (
    _build_stt,
    _build_tts,
    _latest_user_text,
    create_server,
    livekit_configured,
    realtime_backend,
)


def settings(**overrides):
    base = Settings(
        data_dir=Path("data"), log_level="ERROR", llm_order=("local",), llm_timeout=30,
        llm_attempts=1, retry_base=.1, retry_max=1, transient_cooldown=1, rate_cooldown=1,
        auth_cooldown=1, groq_key="", groq_model="", gemini_key="", gemini_model="",
        openrouter_key="", openrouter_model="", local_llm_url="", local_llm_model="",
        assemblyai_key="a", deepgram_key="d", stt_order=("assemblyai", "deepgram"),
        elevenlabs_key="e", elevenlabs_voice="v", elevenlabs_model="eleven_v3",
        relay_url="", relay_token="", direct_allowed=True, microphone_device="default",
        livekit_url="wss://example", livekit_api_key="lk", livekit_api_secret="secret",
        realtime_backend="auto", livekit_agent_name="ai-aharon",
    )
    return replace(base, **overrides)


class Recorder:
    def __init__(self, **kwargs): self.kwargs = kwargs


class Fallback:
    def __init__(self, items, **kwargs): self.items, self.kwargs = items, kwargs


class FakeServer:
    def __init__(self): self.registration = None
    def rtc_session(self, fn, *, agent_name):
        self.registration = (fn, agent_name)
        return fn


class FakeAgent:
    def __init__(self, **kwargs): self.kwargs = kwargs


class FakeSession:
    instances = []
    def __init__(self, **kwargs):
        self.kwargs = kwargs; self.started = None; self.__class__.instances.append(self)
    async def start(self, *, room, agent): self.started = (room, agent)


class FakeCtx:
    def __init__(self): self.room = object(); self.callbacks = []; self.connected = False
    def add_shutdown_callback(self, fn): self.callbacks.append(fn)
    async def connect(self): self.connected = True


class FakeRuntime:
    def __init__(self, _settings): self.closed = False
    def close(self): self.closed = True
    def stream_text(self, text, *, cancel_event=None):
        yield "שלום"
        yield " עולם"


class LiveKitRuntimeTests(unittest.TestCase):
    def modules(self):
        return {
            "Agent": FakeAgent, "AgentServer": FakeServer, "AgentSession": FakeSession,
            "TurnHandlingOptions": Recorder, "cli": SimpleNamespace(run_app=lambda server: None),
            "stt": SimpleNamespace(FallbackAdapter=Fallback),
            "assemblyai": SimpleNamespace(STT=Recorder),
            "deepgram": SimpleNamespace(STT=Recorder),
            "elevenlabs": SimpleNamespace(TTS=Recorder),
        }

    def test_backend_requires_installation_and_configuration(self):
        configured = settings()
        self.assertTrue(livekit_configured(configured))
        with patch("agent_windows.livekit_runtime.livekit_installed", return_value=True):
            self.assertEqual(realtime_backend(configured), "livekit")
            self.assertEqual(realtime_backend(replace(configured, realtime_backend="local")), "local")
        with patch("agent_windows.livekit_runtime.livekit_installed", return_value=False):
            self.assertEqual(realtime_backend(configured), "local")

    def test_stt_uses_ordered_livekit_fallback_and_tts_uses_existing_voice(self):
        modules = self.modules(); cfg = settings()
        stt = _build_stt(cfg, modules)
        self.assertIsInstance(stt, Fallback)
        self.assertEqual(len(stt.items), 2)
        self.assertEqual(stt.items[0].kwargs["api_key"], "a")
        self.assertEqual(stt.items[1].kwargs["language"], "he")
        tts = _build_tts(cfg, modules)
        self.assertEqual(tts.kwargs["voice_id"], "v")
        self.assertEqual(tts.kwargs["language"], "he")

    def test_missing_stt_or_tts_fails_closed(self):
        modules = self.modules()
        with self.assertRaises(RuntimeError): _build_stt(settings(assemblyai_key="", deepgram_key=""), modules)
        with self.assertRaises(RuntimeError): _build_tts(settings(elevenlabs_key=""), modules)

    def test_latest_user_text(self):
        ctx = SimpleNamespace(items=[
            SimpleNamespace(role="assistant", text_content="x"),
            SimpleNamespace(role="user", text_content=lambda: " שלום "),
        ])
        self.assertEqual(_latest_user_text(ctx), "שלום")

    def test_server_registers_real_agent_session_entrypoint(self):
        FakeSession.instances.clear(); modules = self.modules(); cfg = settings()
        server = create_server(
            modules=modules,
            settings_factory=lambda _dotenv: cfg,
            runtime_factory=FakeRuntime,
        )
        entrypoint, agent_name = server.registration
        self.assertEqual(agent_name, "ai-aharon")
        ctx = FakeCtx()
        asyncio.run(entrypoint(ctx))
        self.assertTrue(ctx.connected)
        self.assertEqual(len(FakeSession.instances), 1)
        session = FakeSession.instances[0]
        self.assertEqual(session.kwargs["turn_handling"].kwargs["turn_detection"], "stt")
        self.assertIsNone(session.kwargs["vad"])
        self.assertEqual(session.started[0], ctx.room)
        runtime = session.started[1].runtime
        self.assertFalse(runtime.closed)
        asyncio.run(ctx.callbacks[0]())
        self.assertTrue(runtime.closed)

    def test_runtime_agent_llm_node_streams_shared_runtime(self):
        modules = self.modules(); cfg = settings()
        server = create_server(modules=modules, settings_factory=lambda _dotenv: cfg, runtime_factory=FakeRuntime)
        entrypoint, _ = server.registration; ctx = FakeCtx(); asyncio.run(entrypoint(ctx))
        agent = FakeSession.instances[-1].started[1]
        chat = SimpleNamespace(items=[SimpleNamespace(role="user", text_content="מה נשמע")])
        async def collect():
            return [chunk async for chunk in agent.llm_node(chat, [], None)]
        self.assertEqual(asyncio.run(collect()), ["שלום", " עולם"])


if __name__ == "__main__": unittest.main()
