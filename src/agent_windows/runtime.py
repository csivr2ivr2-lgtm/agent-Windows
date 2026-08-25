from __future__ import annotations

import json
from pathlib import Path

from .audio import OfflineAudioSpool
from .audio import ResilientUploader, UploadSession
from .config import Settings
from .errors import ProviderUnavailable
from .memory import SQLiteMemoryStore
from .network import NetworkMonitor
from .orchestrator import AgentOrchestrator
from .provider_manager import ProviderManager, RetryPolicy
from .providers import GeminiProvider, GroqProvider, LocalLLMProvider, OpenRouterProvider
from .relay import RelayAudioTransport
from .router import LLMRouter
from .speech import AssemblyAISTT, DeepgramSTT, ElevenLabsTTS, STTManager
from .tools import ToolRegistry
from .voice_runtime import FFmpegMicrophone, VoiceService
from .windows_tools import build_windows_tools


class AgentRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.network = NetworkMonitor()
        self.memory = SQLiteMemoryStore(settings.data_dir / "memory.sqlite3")
        providers_by_name = {
            "groq": GroqProvider(api_key=settings.groq_key,model=settings.groq_model,timeout=settings.llm_timeout),
            "gemini": GeminiProvider(api_key=settings.gemini_key,model=settings.gemini_model,timeout=settings.llm_timeout),
            "openrouter": OpenRouterProvider(api_key=settings.openrouter_key,model=settings.openrouter_model,timeout=settings.llm_timeout),
            "local": LocalLLMProvider(base_url=settings.local_llm_url,model=settings.local_llm_model,timeout=10),
        }
        providers = [providers_by_name[name] for name in settings.llm_order if name in providers_by_name]
        self.provider_manager = ProviderManager(providers, retry_policy=RetryPolicy(
            max_attempts=settings.llm_attempts,base_delay=settings.retry_base,max_delay=settings.retry_max,
            transient_cooldown=settings.transient_cooldown,rate_limit_cooldown=settings.rate_cooldown,
            auth_cooldown=settings.auth_cooldown), network_monitor=self.network)
        self.tools = ToolRegistry(build_windows_tools((Path.cwd(), settings.data_dir)))
        self.agent = AgentOrchestrator(LLMRouter(self.provider_manager), self.memory, self.tools,
                                       policy_provider=self.network.policy)
        stt_by_name = {"assemblyai": AssemblyAISTT(settings.assemblyai_key), "deepgram": DeepgramSTT(settings.deepgram_key)}
        self.stt = STTManager([stt_by_name[n] for n in settings.stt_order if n in stt_by_name])
        self.tts = ElevenLabsTTS(settings.elevenlabs_key,settings.elevenlabs_voice,model=settings.elevenlabs_model)
        self.relay = RelayAudioTransport(settings.relay_url,settings.relay_token) if settings.relay_url else None
        self.spool = OfflineAudioSpool(settings.data_dir/"audio-spool")
        self.voice = VoiceService(microphone=FFmpegMicrophone(settings.microphone_device),stt=self.stt,tts=self.tts,
                                  relay=self.relay,network_monitor=self.network,spool=self.spool,direct_allowed=settings.direct_allowed)

    def handle_text(self, text: str) -> str:
        self.provider_manager.apply_network_policy(self.network.policy())
        if text.startswith("/tool "):
            parts=text.split(" ",2); name=parts[1]; args=json.loads(parts[2]) if len(parts)>2 else {}
            return json.dumps(self.tools.invoke(name,args),ensure_ascii=False,default=str)
        if text.startswith("/memory "):
            return json.dumps(list(self.memory.search(text[8:])),ensure_ascii=False)
        try: return self.agent.handle_text(text)
        except ProviderUnavailable:
            return "Offline reasoning is unavailable. Local tools and memory still work; use /tool or /memory."

    def recover_audio(self) -> int:
        if not self.relay or not self.relay.health(): return 0
        completed = 0
        for session_id in self.spool.sessions():
            metadata = self.spool.session_metadata(session_id)
            if not metadata: continue
            self.relay.open(session_id, metadata)
            state = UploadSession(session_id, set(self.relay.received_sequences.get(session_id, set())))
            ResilientUploader(self.relay).upload(self.spool.iter_session(session_id), metadata=metadata, session=state)
            self.spool.delete_session(session_id); completed += 1
        return completed


def build_runtime(dotenv=".env") -> AgentRuntime:
    return AgentRuntime(Settings.from_env(dotenv))
