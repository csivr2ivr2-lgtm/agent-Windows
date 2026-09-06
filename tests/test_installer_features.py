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
from agent_windows.updater import UpdateInfo, check_for_update, download_update, is_newer


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
            db.execute("INSERT INTO memories(text,created,metadata) VALUES(?,?,?)", ("hello world", time.time(), "{}"))
            db.commit()
            db.close()
            store = SQLiteMemoryStore(path)
            try:
                columns = {row[1] for row in store._database().execute("PRAGMA table_info(memories)")}
                self.assertTrue({"kind", "importance", "last_accessed", "access_count"} <= columns)
                self.assertEqual(list(store.search("hello world")), ["hello world"])
            finally:
                store.close()

    def test_durable_user_fact_detection(self):
        self.assertTrue(AgentLoop._durable_user_memory("אני מעדיף תשובות קצרות"))
        self.assertTrue(AgentLoop._durable_user_memory("Remember that my project uses Windows"))
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
            path.write_text("# keep me\nOTHER=value\nGROQ_API_KEY=old\n", encoding="utf-8")
            update_env_file(path, {"GROQ_API_KEY": "new secret", "GEMINI_API_KEY": "gemini"})
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

    def test_update_metadata_validation(self):
        payload = json.dumps(
            {
                "version": "99.0.0",
                "url": "https://github.com/csivr2ivr2-lgtm/agent-Windows/releases/download/v99.0.0/AI-Aharon-Setup-99.0.0.exe",
                "sha256": "a" * 64,
            }
        ).encode()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = payload
        with mock.patch("agent_windows.updater.urllib.request.urlopen", return_value=response), mock.patch(
            "agent_windows.updater.current_version", return_value="1.0.0"
        ):
            info = check_for_update("https://github.com/csivr2ivr2-lgtm/agent-Windows/releases/latest/download/update.json")
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "99.0.0")

    def test_update_rejects_non_official_feed(self):
        with self.assertRaises(ValueError):
            check_for_update("https://example.invalid/update.json")

    def test_download_update_verifies_sha256(self):
        content = b"signed installer bytes"
        info = UpdateInfo(
            version="9.8.7",
            url="https://github.com/csivr2ivr2-lgtm/agent-Windows/releases/download/v9.8.7/AI-Aharon-Setup-9.8.7.exe",
            sha256=hashlib.sha256(content).hexdigest(),
        )
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [content, b""]
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "agent_windows.updater.tempfile.gettempdir", return_value=directory
        ), mock.patch("agent_windows.updater.urllib.request.urlopen", return_value=response):
            path = download_update(info)
            self.assertEqual(path.read_bytes(), content)

    def test_download_update_removes_bad_hash(self):
        content = b"tampered installer"
        info = UpdateInfo(
            version="9.8.6",
            url="https://github.com/csivr2ivr2-lgtm/agent-Windows/releases/download/v9.8.6/AI-Aharon-Setup-9.8.6.exe",
            sha256="0" * 64,
        )
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [content, b""]
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "agent_windows.updater.tempfile.gettempdir", return_value=directory
        ), mock.patch("agent_windows.updater.urllib.request.urlopen", return_value=response):
            with self.assertRaises(ValueError):
                download_update(info)
            self.assertFalse(any(Path(directory).iterdir()))


if __name__ == "__main__":
    unittest.main()
