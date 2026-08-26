from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LatencyMetrics:
    speech_end: float | None = None
    transcript_ready: float | None = None
    first_llm_token: float | None = None
    first_audio_byte: float | None = None
    first_audible: float | None = None
    barge_in_detected: float | None = None
    playback_stopped: float | None = None

    def mark(self, name: str) -> None:
        setattr(self, name, time.monotonic())

    def milliseconds(self) -> dict[str, float]:
        pairs = {
            "speech_end_to_transcript_ready": (self.speech_end, self.transcript_ready),
            "transcript_ready_to_first_llm_token": (self.transcript_ready, self.first_llm_token),
            "first_llm_token_to_first_audio_byte": (self.first_llm_token, self.first_audio_byte),
            "speech_end_to_first_audible_response": (self.speech_end, self.first_audible),
            "barge_in_to_playback_stopped": (self.barge_in_detected, self.playback_stopped),
        }
        return {k: round((b-a)*1000, 1) for k, (a, b) in pairs.items() if a is not None and b is not None}

    def log(self) -> None:
        values = self.milliseconds()
        if values:
            logger.info("voice_latency_ms=%s", values)


@dataclass
class CancellationScope:
    event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self.event.set()

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()


class LiveKitSessionAdapter:
    """Active optional LiveKit Agents runtime boundary."""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def available(self) -> bool:
        if not self.enabled:
            return False
        try:
            from livekit.agents import AgentSession  # noqa: F401
            return True
        except ImportError:
            return False

    def require(self):
        if not self.enabled:
            raise RuntimeError("LiveKit Agents runtime is disabled")
        try:
            from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, inference
        except ImportError as exc:
            raise RuntimeError(
                "LiveKit Agents is not installed; install agent-windows[livekit]"
            ) from exc
        return Agent, AgentServer, AgentSession, JobContext, cli, inference

    def build_server(
        self,
        *,
        agent_name: str,
        instructions: str,
        stt_model: str,
        llm_model: str,
        tts_model: str,
        tts_voice: str | None = None,
    ):
        Agent, AgentServer, AgentSession, JobContext, _cli, inference = self.require()
        server = AgentServer()

        @server.rtc_session(agent_name=agent_name)
        async def entrypoint(ctx: JobContext):
            ctx.log_context_fields = {"room": ctx.room.name, "runtime": "agent-windows-livekit"}
            tts = inference.TTS(model=tts_model, voice=tts_voice) if tts_voice else inference.TTS(model=tts_model)
            session = AgentSession(
                stt=inference.STT(model=stt_model, language="he"),
                llm=inference.LLM(model=llm_model),
                tts=tts,
                preemptive_generation=True,
            )
            await session.start(
                room=ctx.room,
                agent=Agent(instructions=instructions),
            )
            await ctx.connect()

        return server

    def run(self, **server_options) -> None:
        _Agent, _AgentServer, _AgentSession, _JobContext, cli, _inference = self.require()
        cli.run_app(self.build_server(**server_options))
