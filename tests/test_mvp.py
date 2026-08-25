import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_windows.audio.adaptation import NetworkState
from agent_windows.config import Settings
from agent_windows.contracts import LLMResponse, Message, ToolCall
from agent_windows.diagnostics import collect
from agent_windows.http import HTTPResponse
from agent_windows.logging_utils import redact
from agent_windows.memory import SQLiteMemoryStore
from agent_windows.network import NetworkMonitor
from agent_windows.optimizer import RequestOptimizer
from agent_windows.provider_manager import ProviderManager, RetryPolicy
from agent_windows.relay import RelayAudioTransport
from agent_windows.runtime import AgentRuntime
from agent_windows.speech import AssemblyAISTT, DeepgramSTT, ElevenLabsTTS
from agent_windows.voice_runtime import VoiceService
from agent_windows.audio import OfflineAudioSpool, AudioChunker, ChunkAck
from agent_windows.errors import ProviderConnectionError


class SequenceClient:
    def __init__(self, responses): self.responses=list(responses); self.calls=[]
    def request(self, method,url,headers,body,timeout):
        self.calls.append((method,url,headers,body,timeout)); return self.responses.pop(0)


def json_response(status, data): return HTTPResponse(status,json.dumps(data).encode(),{})


class RepliesProvider:
    def __init__(self,name,replies,timeout=30): self.name=name; self.replies=list(replies); self.timeout=timeout
    def is_available(self): return True
    def complete(self,messages,tools):
        value=self.replies.pop(0)
        if isinstance(value,Exception): raise value
        return value


def settings(root, **overrides):
    values=dict(data_dir=Path(root),log_level="ERROR",llm_order=("groq","gemini","openrouter","local"),llm_timeout=30,
        llm_attempts=2,retry_base=.25,retry_max=2,transient_cooldown=15,rate_cooldown=60,auth_cooldown=300,
        groq_key="",groq_model="",gemini_key="",gemini_model="",openrouter_key="",openrouter_model="",
        local_llm_url="",local_llm_model="",assemblyai_key="",deepgram_key="",stt_order=("assemblyai","deepgram"),
        elevenlabs_key="",elevenlabs_voice="",elevenlabs_model="eleven_v3",relay_url="",relay_token="",direct_allowed=True,
        microphone_device="default")
    values.update(overrides); return Settings(**values)


class MVPTests(unittest.TestCase):
    def test_persistent_memory_survives_restart_and_deletes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"memory.db"; SQLiteMemoryStore(path).remember("Ari likes lightweight agents")
            reopened=SQLiteMemoryStore(path); self.assertEqual(len(reopened.search("lightweight")),1)
            self.assertEqual(reopened.delete(),1); self.assertEqual(reopened.search("lightweight"),[])

    def test_complete_text_conversation_with_tool_and_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=AgentRuntime(settings(directory))
            provider=RepliesProvider("fake",[LLMResponse(tool_calls=[ToolCall("current_time",{})]),LLMResponse(text="done",provider="fake")])
            runtime.provider_manager=ProviderManager([provider],network_monitor=runtime.network)
            runtime.agent.router.manager=runtime.provider_manager
            self.assertEqual(runtime.handle_text("what time is it"),"done")
            self.assertTrue(runtime.memory.search("time"))

    def test_offline_runtime_keeps_local_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=AgentRuntime(settings(directory)); runtime.network.state=NetworkState.OFFLINE
            result=json.loads(runtime.handle_text("/tool current_time")); self.assertIn("T",result)
            self.assertIn("Offline reasoning",runtime.handle_text("reason about this"))

    def test_network_transitions_and_adaptive_policy(self):
        network=NetworkMonitor()
        network.record(latency_ms=3000); self.assertIn(network.state,{NetworkState.POOR,NetworkState.DEGRADED})
        for _ in range(4): network.record(success=False)
        self.assertEqual(network.state,NetworkState.OFFLINE)
        self.assertEqual(network.policy()["attempts"],1)

    def test_adaptive_timeout_reaches_provider(self):
        network=NetworkMonitor(state=NetworkState.POOR); provider=RepliesProvider("p",[LLMResponse(text="ok")])
        manager=ProviderManager([provider],network_monitor=network); manager.apply_network_policy(network.policy())
        self.assertEqual(provider.timeout,65); self.assertEqual(manager.retry_policy.max_attempts,3)

    def test_context_reduction_deduplicates_and_filters_tools(self):
        messages=[Message("user","same   text"),Message("user","same text"),Message("assistant","x"*100)]
        tools=[{"name":"weather"},{"name":"current_time"},{"name":"system_info"}]
        optimized,selected=RequestOptimizer().optimize(messages,tools,max_chars=30,max_tools=2)
        self.assertLessEqual(sum(len(m.content) for m in optimized),30); self.assertEqual(len(selected),2)

    def test_assemblyai_stt_adapter(self):
        client=SequenceClient([json_response(200,{"upload_url":"https://upload"}),json_response(200,{"id":"id"}),
                               json_response(200,{"status":"completed","text":"שלום"})])
        self.assertEqual(AssemblyAISTT("key",client=client,poll_seconds=0).transcribe(b"audio"),"שלום")
        self.assertEqual([c[0] for c in client.calls],["POST","POST","GET"])

    def test_deepgram_stt_adapter(self):
        client=SequenceClient([json_response(200,{"results":{"channels":[{"alternatives":[{"transcript":"שלום"}]}]}})])
        self.assertEqual(DeepgramSTT("key",client=client).transcribe(b"audio"),"שלום")
        self.assertIn("language=he",client.calls[0][1])

    def test_elevenlabs_tts_adapter(self):
        client=SequenceClient([HTTPResponse(200,b"mp3",{})]); tts=ElevenLabsTTS("key","voice",client=client)
        self.assertEqual(tts.synthesize("שלום"),b"mp3"); self.assertIn("mp3_22050_32",client.calls[0][1])

    def test_missing_keys_are_safely_unavailable_and_diagnostics_redact(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=AgentRuntime(settings(directory)); report=collect(runtime)
            self.assertFalse(any(report["providers"].values())); self.assertFalse(any(report["stt"].values()))
        self.assertNotIn("secret123",redact("Authorization: secret123 api_key=another"))

    def test_relay_transport_auth_headers_and_validation(self):
        client=SequenceClient([json_response(200,{"session_id":"1234567890123456","received_sequences":[]}),
                               json_response(200,{"session_id":"1234567890123456","sequence":0,"accepted":True})])
        relay=RelayAudioTransport("https://relay.example","client-token",client=client)
        relay.open("1234567890123456",{"codec":"ogg_opus"})
        from agent_windows.audio.chunking import AudioChunk
        ack=relay.send_chunk(AudioChunk("1234567890123456",0,0,b"x","a"*64))
        self.assertTrue(ack.accepted); self.assertEqual(client.calls[0][2]["Authorization"],"Bearer client-token")

    def test_voice_falls_from_unhealthy_relay_to_direct_stt(self):
        class Mic:
            def capture_pcm_utterance(self,target,vad): target.write_bytes(b"\0\0"*320)
        class STTProvider:
            supported_codecs={"pcm_s16le"}
            def is_available(self): return True
        class STT:
            providers=[STTProvider()]
            def transcribe(self,audio,**kwargs): return "direct transcript"
        class Relay:
            def is_available(self): return True
            def health(self): return False
        with tempfile.TemporaryDirectory() as directory, \
             patch("agent_windows.voice_runtime.FFmpegCapabilities.supported_codecs",return_value={"pcm_s16le"}), \
             patch("agent_windows.voice_runtime.subprocess.run") as run:
            run.return_value.returncode=0
            service=VoiceService(microphone=Mic(),stt=STT(),tts=None,relay=Relay(),network_monitor=NetworkMonitor(),
                                 spool=OfflineAudioSpool(directory),direct_allowed=True)
            self.assertEqual(service.listen(),"direct transcript")

    def test_relay_failure_without_direct_permission_fails_closed(self):
        class Mic:
            def capture_pcm_utterance(self,target,vad): target.write_bytes(b"\0\0"*320)
        class STTProvider:
            supported_codecs={"pcm_s16le"}
            def is_available(self): return True
        class STT: providers=[STTProvider()]
        class Relay:
            def is_available(self): return True
            def health(self): return False
        with tempfile.TemporaryDirectory() as directory, \
             patch("agent_windows.voice_runtime.FFmpegCapabilities.supported_codecs",return_value={"pcm_s16le"}), \
             patch("agent_windows.voice_runtime.subprocess.run") as run:
            run.return_value.returncode=0
            service=VoiceService(microphone=Mic(),stt=STT(),tts=None,relay=Relay(),network_monitor=NetworkMonitor(),
                                 spool=OfflineAudioSpool(directory),direct_allowed=False)
            with self.assertRaises(ProviderConnectionError): service.listen()


@unittest.skipUnless(shutil.which("php"), "PHP CLI not installed")
class PHPRelayIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(); sock=socket.socket(); sock.bind(("127.0.0.1",0)); cls.port=sock.getsockname()[1]; sock.close()
        env=os.environ.copy(); env.update(RELAY_AGENT_TOKEN="x"*32,RELAY_STORAGE_DIR=cls.temp.name,RELAY_REQUIRE_HTTPS="false",RELAY_RATE_LIMIT_PER_MINUTE="1000")
        cls.process=subprocess.Popen([shutil.which("php"),"-S",f"127.0.0.1:{cls.port}","-t","relay/public","relay/public/index.php"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env)
        time.sleep(.4)
    @classmethod
    def tearDownClass(cls): cls.process.terminate(); cls.process.wait(timeout=3); cls.temp.cleanup()
    def request(self,path,method="GET",body=None,headers=None):
        try:
            with urlopen(Request(f"http://127.0.0.1:{self.port}{path}",data=body,method=method,headers=headers or {}),timeout=3) as r:return r.status,r.read()
        except HTTPError as e:return e.code,e.read()
    def test_authentication_and_upload_validation(self):
        self.assertEqual(self.request("/v1/health")[0],401)
        auth={"Authorization":"Bearer "+"x"*32,"Content-Type":"application/json"}
        bad=json.dumps({"session_id":"1"*16,"codec":"exe","content_type":"application/x-executable"}).encode()
        self.assertEqual(self.request("/v1/audio/sessions","POST",bad,auth)[0],415)
        session="a"*32; good=json.dumps({"session_id":session,"codec":"ogg_opus","content_type":"audio/ogg; codecs=opus","sample_rate":16000,"channels":1}).encode()
        self.assertEqual(self.request("/v1/audio/sessions","POST",good,auth)[0],200)
        import hashlib
        payload=b"encoded"; headers={"Authorization":"Bearer "+"x"*32,"Content-Type":"application/octet-stream","X-Chunk-SHA256":hashlib.sha256(payload).hexdigest()}
        path=f"/v1/audio/sessions/{session}/chunks/0"
        self.assertEqual(self.request(path,"PUT",payload,headers)[0],200)
        status,body=self.request(path,"PUT",payload,headers); self.assertEqual(status,200); self.assertTrue(json.loads(body)["duplicate"])


if __name__=="__main__": unittest.main()
