from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_windows.agent_loop import AgentLoop
from agent_windows.contracts import Message
from agent_windows.memory import SQLiteMemoryStore
from agent_windows.settings_ui import read_env_file, update_env_file
from agent_windows.updater import (
    DEFAULT_INSTALLER_URL,
    UpdateInfo,
    check_for_update,
    download_update,
    is_newer,
    launch_installer,
)


class MemoryRankingTests(unittest.TestCase):
    def test_profile_memory_beats_low_importance_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteMemoryStore(Path(directory) / "memory.sqlite3")
            try:
                store.remember(
                    "User likes fantasy movies",
                    metadata={"kind": "profile", "importance": 0.95},
                )
                store.remember(
                    "We discussed fantasy movies",
                    metadata={"kind": "turn", "importance": 0.2},
                )
                self.assertEqual(store.search("fantasy movies", limit=1)[0], "User likes fantasy movies")
            finally:
                store.close()

    def test_existing_database_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            db = sqlite3.connect(path)
            db.execute(
                "CREATE TABLE memories(id INTEGER PRIMARY KEY, text TEXT UNIQUE NOT NULL, "
                "created REAL NOT NULL, metadata TEXT)"
            )
            db.execute(
                "INSERT INTO memories(text,created,metadata) VALUES(?,?,?)",
                ("hello world", time.time(), "{}"),
            )
            db.commit()
            db.close()
            store = SQLiteMemoryStore(path)
            try:
                columns = {
                    row[1]
                    for row in store._database().execute("PRAGMA table_info(memories)")
                }
                self.assertTrue(
                    {"kind", "importance", "last_accessed", "access_count"} <= columns
                )
                self.assertEqual(list(store.search("hello world")), ["hello world"])
            finally:
                store.close()

    def test_numeric_memory_tokens_are_searchable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteMemoryStore(Path(directory) / "memory.sqlite3")
            try:
                store.remember("The tool returned 17:30", metadata={"kind": "turn"})
                self.assertEqual(store.search("17:30"), ["The tool returned 17:30"])
            finally:
                store.close()

    def test_durable_user_fact_detection(self):
        self.assertTrue(AgentLoop._durable_user_memory("אני מעדיף תשובות קצרות"))
        self.assertTrue(
            AgentLoop._durable_user_memory("Remember that my project uses Windows")
        )
        self.assertFalse(AgentLoop._durable_user_memory("what time is it"))

    def test_history_is_used_for_memory_query(self):
        memory = mock.MagicMock()
        memory.search.return_value = []
        loop = AgentLoop(
            mock.MagicMock(),
            memory,
            mock.MagicMock(),
            system_prompt="system",
        )
        history = [
            Message("user", "We were configuring the Windows installer"),
            Message("assistant", "The updater is next"),
        ]
        loop._initial_messages("continue", history)
        query = memory.search.call_args.args[0]
        self.assertIn("Windows installer", query)
        self.assertIn("continue", query)


class SettingsFileTests(unittest.TestCase):
    def test_updates_preserve_unknown_values_and_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# keep me\nOTHER=value\nGROQ_API_KEY=old\n", encoding="utf-8"
            )
            update_env_file(
                path, {"GROQ_API_KEY": "new secret", "GEMINI_API_KEY": "gemini"}
            )
            values = read_env_file(path)
            self.assertEqual(values["OTHER"], "value")
            self.assertEqual(values["GROQ_API_KEY"], "new secret")
            self.assertEqual(values["GEMINI_API_KEY"], "gemini")
            self.assertIn("# keep me", path.read_text(encoding="utf-8"))
            self.assertTrue(path.with_name(path.name + ".bak").exists())


class UpdaterTests(unittest.TestCase):
    def test_version_comparison(self):
        self.assertTrue(is_newer("0.2.0", "0.1.9"))
        self.assertTrue(is_newer("v1.0.1", "1.0.0"))
        self.assertFalse(is_newer("1.0.0", "1.0.0"))

    @staticmethod
    def _metadata_response(payload):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(payload).encode()
        response.geturl.return_value = (
            "https://github.com/csivr2ivr2-lgtm/agent-Windows/releases/latest/download/update.json"
        )
        opener = mock.MagicMock()
        opener.open.return_value = response
        return opener

    def test_update_metadata_uses_fixed_official_installer_url(self):
        opener = self._metadata_response(
            {"version": "99.0.0", "sha256": "a" * 64}
        )
        with mock.patch("agent_windows.updater._opener", return_value=opener), mock.patch(
            "agent_windows.updater.current_version", return_value="1.0.0"
        ):
            info = check_for_update()
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "99.0.0")
        self.assertEqual(info.url, DEFAULT_INSTALLER_URL)

    def test_update_metadata_cannot_override_installer_url(self):
        opener = self._metadata_response(
            {
                "version": "99.0.0",
                "url": "https://example.invalid/installer.exe",
                "sha256": "a" * 64,
            }
        )
        with mock.patch("agent_windows.updater._opener", return_value=opener):
            with self.assertRaises(ValueError):
                check_for_update()

    @staticmethod
    def _staging_factory(directory, digest):
        staging = Path(directory) / f"AI-Aharon-Update-{digest}-test"
        staging.mkdir()
        return str(staging)

    def test_download_update_verifies_sha256(self):
        content = b"signed installer bytes"
        info = UpdateInfo(
            version="9.8.7",
            url=DEFAULT_INSTALLER_URL,
            sha256=hashlib.sha256(content).hexdigest(),
        )
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [content, b""]
        response.geturl.return_value = DEFAULT_INSTALLER_URL
        response.headers = {}
        opener = mock.MagicMock()
        opener.open.return_value = response
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "agent_windows.updater.tempfile.mkdtemp",
            side_effect=lambda **_: self._staging_factory(directory, info.sha256),
        ), mock.patch("agent_windows.updater._opener", return_value=opener):
            path = download_update(info)
            self.assertEqual(path.read_bytes(), content)
            self.assertTrue(path.parent.name.startswith(f"AI-Aharon-Update-{info.sha256}-"))

    def test_download_update_removes_bad_hash(self):
        content = b"tampered installer"
        info = UpdateInfo(
            version="9.8.6",
            url=DEFAULT_INSTALLER_URL,
            sha256="0" * 64,
        )
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [content, b""]
        response.geturl.return_value = DEFAULT_INSTALLER_URL
        response.headers = {}
        opener = mock.MagicMock()
        opener.open.return_value = response
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "agent_windows.updater.tempfile.mkdtemp",
            side_effect=lambda **_: self._staging_factory(directory, info.sha256),
        ), mock.patch("agent_windows.updater._opener", return_value=opener):
            with self.assertRaises(ValueError):
                download_update(info)
            self.assertFalse(any(Path(directory).iterdir()))

    def test_launch_rehashes_installer_before_start(self):
        content = b"verified installer"
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / f"AI-Aharon-Update-{digest}-test"
            staging.mkdir()
            installer = staging / "AI-Aharon-Setup-9.8.5.exe"
            installer.write_bytes(content)
            startfile = mock.Mock()
            with mock.patch(
                "agent_windows.updater.tempfile.gettempdir", return_value=directory
            ), mock.patch("agent_windows.updater.os.name", "nt"), mock.patch.object(
                __import__("agent_windows.updater", fromlist=["os"]).os,
                "startfile",
                startfile,
                create=True,
            ):
                launch_installer(installer)
            startfile.assert_called_once_with(str(installer.resolve()))


class DistributionHardeningTests(unittest.TestCase):
    def test_launcher_whitelists_only_minimized_argument_and_sets_tools_path(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "installer" / "launcher.cs").read_text(encoding="utf-8")
        self.assertNotIn("foreach (string arg in args)", source)
        self.assertIn(
            'String.Equals(arg, "--minimized", StringComparison.Ordinal)', source
        )
        self.assertIn('start.EnvironmentVariables["PATH"] = tools + ";"', source)
        self.assertIn("start.UseShellExecute = false", source)
        self.assertIn("AppDomain.CurrentDomain.BaseDirectory", source)
        self.assertIn('Path.Combine(installRoot, "python-runtime", "pythonw.exe")', source)
        self.assertIn('Path.Combine(stateRoot, ".env")', source)

    def test_installer_keeps_privileged_runtime_out_of_programdata(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts" / "installer-apply.ps1").read_text(encoding="utf-8")
        self.assertIn("$RuntimeRoot = Join-Path $InstallRoot 'python-runtime'", source)
        self.assertIn("$ToolsRoot = Join-Path $InstallRoot 'tools'", source)
        self.assertIn("$LegacyRuntimeRoot = Join-Path $ServiceRoot 'python-runtime'", source)
        self.assertIn("[Security.AccessControl.FileSystemRights]::Modify", source)
        self.assertNotIn("robocopy.exe", source)
        self.assertNotIn("pip install", source)

    def test_installer_does_not_bypass_powershell_execution_policy(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "installer" / "AI-Aharon.iss").read_text(encoding="utf-8")
        self.assertNotIn("ExecutionPolicy Bypass", source)
        self.assertIn("-NoProfile -NonInteractive -File", source)

    def test_history_memory_query_uses_only_last_six_history_messages(self):
        memory = mock.MagicMock()
        memory.search.return_value = []
        loop = AgentLoop(
            mock.MagicMock(), memory, mock.MagicMock(), system_prompt="system"
        )
        history = [Message("user", f"history-{index}") for index in range(8)]
        loop._initial_messages("continue", history)
        query = memory.search.call_args.args[0]
        self.assertNotIn("history-0", query)
        self.assertNotIn("history-1", query)
        for index in range(2, 8):
            self.assertIn(f"history-{index}", query)

    def test_updater_rejects_non_github_redirect_destination(self):
        from agent_windows.updater import _validate_final_download_url

        with self.assertRaises(ValueError):
            _validate_final_download_url("https://example.invalid/release.exe")


if __name__ == "__main__":
    unittest.main()
