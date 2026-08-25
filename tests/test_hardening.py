import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

from agent_windows.audio import AudioChunk, OfflineAudioSpool
from agent_windows.audio.encoder import FFmpegCapabilities, ffmpeg_command
from agent_windows.audio.adaptation import NetworkState, profile_for
from agent_windows.benchmark import run_local_benchmark
from agent_windows.config import Settings, load_dotenv
from agent_windows.errors import ProviderBadResponse, ProviderConnectionError, ProviderTimeout
from agent_windows.http import HTTPResponse, UrllibTransport, _read_limited
from agent_windows.logging_utils import redact
from agent_windows.memory import InMemoryStore, SQLiteMemoryStore
from agent_windows.providers.base import OpenAICompatibleProvider
from agent_windows.relay import RelayAudioTransport
from agent_windows.tools import ToolRegistry
from agent_windows.windows_tools import build_windows_tools


class HardeningTests(unittest.TestCase):
    def test_dotenv_preserves_environment_and_parses_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("A='from-file'\nB=\"two\"\n# comment\nbad\n", encoding="utf-8")
            with patch.dict(os.environ, {"A": "existing"}, clear=True):
                load_dotenv(path)
                self.assertEqual(os.environ["A"], "existing")
                self.assertEqual(os.environ["B"], "two")

    def test_invalid_numeric_config_uses_defaults(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"AGENT_LLM_TIMEOUT_SECONDS": "bad", "AGENT_LLM_MAX_ATTEMPTS": "bad", "AGENT_DATA_DIR": directory},
            clear=True,
        ):
            config = Settings.from_env(Path(directory) / "missing")
            self.assertEqual(config.llm_timeout, 30)
            self.assertEqual(config.llm_attempts, 2)

    def test_http_response_limit(self):
        self.assertEqual(_read_limited(io.BytesIO(b"abc"), 3), b"abc")
        with self.assertRaises(ProviderBadResponse):
            _read_limited(io.BytesIO(b"abcd"), 3)

    def test_http_timeout_and_connection_classification(self):
        transport = UrllibTransport()
        with patch("agent_windows.http.urlopen", side_effect=TimeoutError("slow")):
            with self.assertRaises(ProviderTimeout):
                transport.post_json("https://example.test", {}, {}, 1)
        with patch("agent_windows.http.urlopen", side_effect=URLError("down")):
            with self.assertRaises(ProviderConnectionError):
                transport.post_json("https://example.test", {}, {}, 1)

    def test_tool_arguments_must_be_object(self):
        class Transport:
            def post_json(self, *args):
                return HTTPResponse(200, json.dumps({"choices": [{"message": {"tool_calls": [
                    {"function": {"name": "x", "arguments": "[]"}}
                ]}}]}).encode(), {})
        provider = OpenAICompatibleProvider(api_key="k", model="m", endpoint="https://e", transport=Transport())
        with self.assertRaises(ProviderBadResponse):
            provider.complete([], [])

    def test_relay_rejects_unsafe_urls(self):
        for url in ("http://relay.test", "https://user:pass@relay.test", "https://relay.test?q=1", "https://relay.test/#x"):
            self.assertFalse(RelayAudioTransport(url, "token").is_available(), url)
        self.assertTrue(RelayAudioTransport("https://relay.test", "token").is_available())

    def test_relay_rejects_malformed_protocol_responses(self):
        class Client:
            def request(self, *args):
                return HTTPResponse(200, b"[]", {})
        relay = RelayAudioTransport("https://relay.test", "token", client=Client())
        with self.assertRaises(ProviderBadResponse):
            relay.open("a" * 16, {})

    def test_registry_rejects_duplicate_tool_names(self):
        tool = build_windows_tools((Path.cwd(),))[0]
        with self.assertRaises(ValueError):
            ToolRegistry([tool, tool])

    def test_windows_tools_confine_paths_and_limit_listing(self):
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
            tools = {tool.name: tool for tool in build_windows_tools((Path(allowed),))}
            secret = Path(outside) / "secret.txt"
            secret.write_text("secret", encoding="utf-8")
            with self.assertRaises(PermissionError):
                tools["read_text_file"].invoke({"path": str(secret)})
            with self.assertRaises(PermissionError):
                tools["read_text_file"].invoke({"path": str(Path(allowed) / ".." / Path(outside).name / "secret.txt")})
            for index in range(205):
                (Path(allowed) / f"{index:03}.txt").write_text("x", encoding="utf-8")
            self.assertEqual(len(tools["list_directory"].invoke({"path": allowed})), 200)

    def test_windows_tool_blocks_symlink_escape(self):
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "secret.txt"; target.write_text("secret", encoding="utf-8")
            link = Path(allowed) / "link.txt"
            try: link.symlink_to(target)
            except OSError: self.skipTest("symlinks unavailable")
            tool = {x.name: x for x in build_windows_tools((Path(allowed),))}["read_text_file"]
            with self.assertRaises(PermissionError): tool.invoke({"path": str(link)})

    def test_sqlite_dedup_bounds_delete_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.db"; store = SQLiteMemoryStore(path, max_items=2)
            store.remember("first memory"); store.remember("first memory"); store.remember("second memory"); store.remember("third memory")
            self.assertEqual(len(store.search("memory", limit=10)), 2)
            self.assertEqual(store.delete(999999), 0)
            path.unlink(); Path(str(path) + "-wal").unlink(missing_ok=True); Path(str(path) + "-shm").unlink(missing_ok=True)
            path.write_bytes(b"not sqlite")
            with self.assertRaises(sqlite3.DatabaseError): SQLiteMemoryStore(path)

    def test_in_memory_store_deduplicates_and_ignores_empty(self):
        store = InMemoryStore(); store.remember(" hello "); store.remember("hello"); store.remember(" ")
        self.assertEqual(store.search("hello"), ["hello"])

    def test_spool_conflict_corruption_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = b"audio"; checksum = hashlib.sha256(payload).hexdigest()
            chunk = AudioChunk("session", 0, 0, payload, checksum, True)
            spool = OfflineAudioSpool(directory); spool.put(chunk, session_metadata={"codec": "ogg_opus"})
            self.assertEqual(list(spool.iter_session("session"))[0].payload, payload)
            conflict = AudioChunk("session", 0, 0, b"other", hashlib.sha256(b"other").hexdigest(), True)
            with self.assertRaises(ValueError): spool.put(conflict)
            second = AudioChunk("session", 1, 1, b"next", hashlib.sha256(b"next").hexdigest(), True)
            with self.assertRaises(ValueError): spool.put(second, session_metadata={"codec": "mp3"})
            spool._session_dir("session").joinpath("000000000000.audio").write_bytes(b"bad")
            with self.assertRaises(ValueError): list(spool.iter_session("session"))
            spool.delete_session("session"); self.assertEqual(spool.sessions(), [])

    def test_ffmpeg_detection_and_commands(self):
        completed = Mock(stdout="libopus libmp3lame", stderr="")
        with patch("agent_windows.audio.encoder.subprocess.run", return_value=completed):
            self.assertEqual(FFmpegCapabilities().supported_codecs(), {"pcm_s16le", "ogg_opus", "mp3"})
        with patch("agent_windows.audio.encoder.subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(FFmpegCapabilities().supported_codecs(), set())
        self.assertIn("libopus", ffmpeg_command(profile_for(NetworkState.GOOD)))
        with self.assertRaises(ValueError):
            ffmpeg_command(type("P", (), {"sample_rate": 1, "channels": 1, "codec": "bad", "bitrate_bps": 1})())

    def test_redaction_covers_common_secret_forms(self):
        value = redact("Authorization: Bearer topsecret token=abc api_key: xyz password=q")
        for secret in ("topsecret", "abc", "xyz", "q"):
            self.assertNotIn(secret, value)
        self.assertEqual(redact("password=hunter2"), "password=[REDACTED]")

    def test_benchmark_is_local_and_reports_expected_metrics(self):
        report = run_local_benchmark()
        self.assertGreater(report["request_payload_bytes"], report["gzip_bytes"])
        self.assertGreater(report["silence_removed_percent"], 0)
        self.assertIsNone(report["provider_latency_ms"])

    def test_php_relay_static_security_contract(self):
        php = Path("relay/public/index.php").read_text(encoding="utf-8")
        for marker in ("hash_equals", "RELAY_TRUST_PROXY", "realpath($root)", "application/octet-stream", "'x+b'", "chmod($target, 0600)"):
            self.assertIn(marker, php)
        for unsafe in ("eval(", "shell_exec(", "exec("):
            self.assertNotIn(unsafe, php)


if __name__ == "__main__":
    unittest.main()
