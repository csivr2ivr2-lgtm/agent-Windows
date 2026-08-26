from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_windows.audio.adaptation import AudioProfile, NetworkAudioAdapter
from agent_windows.audio.chunking import AckTracker, AudioChunk, chunk_payload
from agent_windows.audio.encoder import FFmpegOpusEncoder
from agent_windows.audio.spool import OfflineAudioSpool
from agent_windows.audio.transport import ChunkUploader
from agent_windows.audio.vad import EnergyVAD
from agent_windows.config import Settings
from agent_windows.contracts import LLMResponse, Message, ToolCall
from agent_windows.diagnostics import collect
from agent_windows.errors import ProviderConnectionError, ProviderRateLimited
from agent_windows.http import HTTPResponse
from agent_windows.memory import SQLiteMemoryStore
from agent_windows.network import NetworkMonitor, NetworkState
from agent_windows.optimizer import RequestOptimizer
from agent_windows.provider_manager import ProviderManager
from agent_windows.providers.local import LocalProvider
from agent_windows.relay import RelayClient
from agent_windows.runtime import AgentRuntime
from agent_windows.security import redact
from agent_windows.speech import AssemblyAISTT, DeepgramSTT, ElevenLabsTTS


class SequenceClient:
    def __init__(self,responses): self.responses=list(responses); self.calls=[]
    def request(self,method,url,headers,body,timeout):
        self.calls.append((method,url,headers,body,timeout)); response=self.responses.pop(0)
        if isinstance(response,Exception): raise response
        return response


def json_response(status,payload,headers=None): return HTTPResponse(status,json.dumps(payload).encode(),headers or {})


def settings(directory):
    return Settings(data_dir=Path(directory),allowed_roots=(Path(directory),),relay_url="",relay_token="",allow_direct_providers=True,
                    openrouter_api_key="",openrouter_model="",groq_api_key="",groq_model="",gemini_api_key="",gemini_model="",
                    local_api_key="local",local_model="local",assemblyai_api_key="",deepgram_api_key="",elevenlabs_api_key="",elevenlabs_voice_id="")


class RepliesProvider:
    def __init__(self,name,replies): self.name=name; self.replies=list(replies)
    def is_available(self): return True
    def complete(self,messages,tools): return self.replies.pop(0)


class MVPTests(unittest.TestCase):
    def test_complete_text_conversation_with_tool_and_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            with AgentRuntime(settings(directory)) as runtime:
                provider=RepliesProvider("fake",[LLMResponse(tool_calls=[ToolCall("current_time",{})]),LLMResponse(text="done",provider="fake")])
                runtime.provider_manager=ProviderManager([provider],network_monitor=runtime.network)
                runtime.orchestrator.providers=runtime.provider_manager
                self.assertEqual(runtime.handle_text("time"),"done")
                runtime.memory.remember("remember me")
            with AgentRuntime(settings(directory)) as second: self.assertIn("remember me",second.memory.search("remember"))

    def test_persistent_memory_survives_restart_and_deletes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"memory.db"
            store=SQLiteMemoryStore(path,max_items=4); store.remember("alpha"); store.remember("beta"); store.close()
            second=SQLiteMemoryStore(path,max_items=4); self.assertIn("alpha",second.search("alpha")); self.assertTrue(second.delete("alpha")); self.assertNotIn("alpha",second.search("alpha")); second.close()

    def test_offline_keeps_tools_and_memory_working(self):
        with tempfile.TemporaryDirectory() as directory:
            with AgentRuntime(settings(directory)) as runtime:
                runtime.network.observe_exception(OSError("offline"))
                self.assertIn("current_time",runtime.handle_text('/tool current_time {}'))
                runtime.memory.remember("offline-note")
                self.assertIn("offline-note",runtime.handle_text("/memory offline-note"))

    def test_optimizer_limits_context_and_tool_schema(self):
        optimizer=RequestOptimizer(max_messages=2,max_chars=80)
        messages=[Message("user","x"*100),Message("assistant","same"),Message("assistant","same"),Message("user","final")]
        optimized=optimizer.optimize(messages,{"a":{"description":"keep"},"b":{"description":"drop"}},allowed_tools={"a"})
        self.assertLessEqual(len(optimized.messages),2); self.assertEqual(set(optimized.tools),{"a"})

    def test_network_monitor_transitions_without_speed_test(self):
        monitor=NetworkMonitor()
        for _ in range(3): monitor.observe_success(4.0)
        self.assertEqual(monitor.state,NetworkState.POOR)
        monitor.observe_exception(OSError("offline")); self.assertEqual(monitor.state,NetworkState.OFFLINE)
        monitor.observe_success(.1); self.assertEqual(monitor.state,NetworkState.GOOD)

    def test_assemblyai_stt_adapter(self):
        client=SequenceClient([
            json_response(200,{"upload_url":"https://upload"}),json_response(200,{"id":"abc"}),
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
            with AgentRuntime(settings(directory)) as runtime:
                report=collect(runtime)
                self.assertFalse(any(report["providers"].values())); self.assertFalse(any(report["stt"].values()))
        self.assertNotIn("secret123",redact("Authorization: secret123 api_key=another"))

    def test_relay_transport_auth_headers_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            client=SequenceClient([json_response(200,{"status":"ok"}),json_response(200,{"answer":"ok"})])
            relay=RelayClient("https://relay.example","token",transport=client,spool_dir=Path(directory))
            self.assertTrue(relay.health()); self.assertEqual(relay.chat([Message("user","hi")],{},"fast").text,"ok")
            self.assertTrue(all(call[2].get("Authorization")=="Bearer token" for call in client.calls))

    def test_relay_failure_without_direct_permission_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory)
            cfg=Settings(**{**cfg.__dict__,"relay_url":"https://relay.example","relay_token":"token","allow_direct_providers":False})
            with AgentRuntime(cfg) as runtime:
                runtime.relay.transport=SequenceClient([ProviderConnectionError("down")])
                with self.assertRaises(ProviderConnectionError): runtime.handle_text("hello")

    def test_voice_falls_from_unhealthy_relay_to_direct_stt(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory)
            cfg=Settings(**{**cfg.__dict__,"relay_url":"https://relay.example","relay_token":"token","allow_direct_providers":True})
            with AgentRuntime(cfg) as runtime:
                runtime.voice.relay=type("Relay",(),{"is_configured":lambda self:True,"health":lambda self:False})()
                runtime.voice.stt=type("STT",(),{"transcribe":lambda self,*args,**kwargs:"direct"})()
                runtime.voice.microphone=type("Mic",(),{"capture":lambda self,*args,**kwargs:b"audio"})()
                runtime.voice.encoder=type("Encoder",(),{"encode":lambda self,*args,**kwargs:b"encoded"})()
                self.assertEqual(runtime.voice.listen(),"direct")


@unittest.skipUnless(shutil.which("php") and os.name != "nt", "PHP relay integration runs only on non-Windows CI")
class PHPRelayIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(); sock=socket.socket(); sock.bind(("127.0.0.1",0)); cls.port=sock.getsockname()[1]; sock.close()
        env=os.environ.copy(); env.update(RELAY_AGENT_TOKEN="x"*32,RELAY_STORAGE_DIR=cls.temp.name,RELAY_REQUIRE_HTTPS="false",RELAY_RATE_LIMIT_PER_MINUTE="1000")
        root=Path(__file__).resolve().parents[1]; public=root/"relay"/"public"; router=public/"index.php"
        cls.process=subprocess.Popen([shutil.which("php"),"-S",f"127.0.0.1:{cls.port}","-t",str(public),str(router)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,env=env)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if cls.process.poll() is not None:
                details=(cls.process.stderr.read() if cls.process.stderr else b"").decode(errors="replace")
                raise RuntimeError("PHP relay failed to start: "+details)
            try:
                request=Request(f"http://127.0.0.1:{cls.port}/v1/health",headers={"Authorization":"Bearer "+"x"*32})
                with urlopen(request,timeout=.25) as response:
                    if response.status==200: break
            except Exception: time.sleep(.05)
        else: raise RuntimeError("PHP relay did not become ready")

    @classmethod
    def tearDownClass(cls):
        if cls.process.poll() is None:
            cls.process.terminate()
            try: cls.process.wait(timeout=2)
            except subprocess.TimeoutExpired: cls.process.kill(); cls.process.wait(timeout=2)
        if cls.process.stderr: cls.process.stderr.close()
        cls.temp.cleanup()

    def request(self,path,method="GET",body=None,headers=None):
        request=Request(f"http://127.0.0.1:{self.port}{path}",data=body,headers=headers or {},method=method)
        try:
            with urlopen(request,timeout=2) as response: return response.status,response.read()
        except HTTPError as exc: return exc.code,exc.read()

    def test_auth_validation_chunk_integrity_and_rate_limit_surface(self):
        self.assertEqual(self.request("/v1/health")[0],401)
        auth={"Authorization":"Bearer "+"x"*32,"Content-Type":"application/json"}
        bad=json.dumps({"session_id":"1"*16,"codec":"exe","content_type":"application/x-executable"}).encode()
        self.assertEqual(self.request("/v1/audio/sessions","POST",bad,auth)[0],415)
        session="a"*32; good=json.dumps({"session_id":session,"codec":"ogg_opus","content_type":"audio/ogg; codecs=opus","sample_rate":16000,"channels":1}).encode()
        self.assertEqual(self.request("/v1/audio/sessions","POST",good,auth)[0],200)
        import hashlib
        payload=b"hello"; headers={"Authorization":"Bearer "+"x"*32,"Content-Type":"application/octet-stream","X-Chunk-SHA256":hashlib.sha256(payload).hexdigest(),"X-Audio-Timestamp-Ms":"0","X-Final-Chunk":"1"}
        self.assertEqual(self.request(f"/v1/audio/sessions/{session}/chunks/0","PUT",payload,headers)[0],200)
        self.assertEqual(self.request(f"/v1/audio/sessions/{session}/chunks/0","PUT",payload,headers)[0],200)
        status,body=self.request(f"/v1/audio/sessions/{session}/finish","POST",b"{}",auth)
        self.assertEqual(status,200); self.assertIsNone(json.loads(body)["transcript"])


if __name__ == "__main__": unittest.main()
