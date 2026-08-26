import json
import unittest
from unittest import mock

from agent_windows.openviking_memory import OpenVikingClient, OpenVikingError, TieredMemoryStore
from agent_windows.safe_http import SafeHTTPError, SafeHTTPResponse


class Primary:
    def __init__(self):
        self.items = []
        self.closed = False
        self.deleted = []

    def remember(self, text, *, metadata=None):
        self.items.append(text)

    def search(self, query, *, limit=5):
        return ["local", "duplicate"][:limit]

    def delete(self, memory_id=None):
        self.deleted.append(memory_id)
        return 1

    def close(self):
        self.closed = True


class Semantic:
    def __init__(self, *, configured=True, fail=False):
        self.configured = configured
        self.fail = fail
        self.remembered = []
        self.last_error = None

    def is_configured(self):
        return self.configured

    def remember(self, text):
        if self.fail:
            raise OpenVikingError("down")
        self.remembered.append(text)

    def find(self, query, *, limit=5):
        if self.fail:
            raise OpenVikingError("down")
        return ["semantic", "duplicate"][:limit]

    def status(self):
        return {"configured": self.configured, "last_error": self.last_error}


class OpenVikingMemoryTests(unittest.TestCase):
    def test_tiered_write_search_fallback_delete_close(self):
        primary = Primary()
        semantic = Semantic()
        store = TieredMemoryStore(primary, semantic)
        store.remember("hello", metadata={"x": 1})
        self.assertEqual(primary.items, ["hello"])
        self.assertEqual(semantic.remembered, ["hello"])
        self.assertEqual(list(store.search("q", limit=4)), ["semantic", "duplicate", "local"])
        self.assertEqual(store.delete(4), 1)
        self.assertEqual(primary.deleted, [4])
        self.assertTrue(store.status()["semantic"]["configured"])
        store.close()
        self.assertTrue(primary.closed)

        primary = Primary()
        semantic = Semantic(fail=True)
        store = TieredMemoryStore(primary, semantic)
        store.remember("still local")
        self.assertEqual(primary.items, ["still local"])
        self.assertIn("down", semantic.last_error)
        self.assertEqual(list(store.search("q")), ["local", "duplicate"])
        store = TieredMemoryStore(Primary(), None)
        store.remember("x")
        self.assertEqual(list(store.search("q")), ["local", "duplicate"])

    def test_client_session_write_commit_and_find(self):
        client = OpenVikingClient("http://127.0.0.1:1933/", "key", "session x")
        replies = iter(
            [
                {"session_id": "session x"},
                {"message_count": 1},
                {"task_id": "t"},
                {"resources": [{"abstract": "semantic fact"}, {"uri": "viking://x"}, "plain"]},
            ]
        )
        with mock.patch.object(client, "_post", side_effect=lambda *a, **k: next(replies)) as post:
            client.remember(" hello ")
            found = client.find("hello", limit=3)
        self.assertEqual(found, ["semantic fact", "viking://x", "plain"])
        self.assertEqual(post.call_args_list[0].args[0], "/api/v1/sessions")
        self.assertIn("session%20x", post.call_args_list[1].args[0])
        self.assertIn("/commit", post.call_args_list[2].args[0])
        self.assertEqual(client.status()["configured"], True)

    def test_existing_session_is_tolerated_other_error_is_not(self):
        client = OpenVikingClient("https://x", session_id="s")
        with mock.patch.object(client, "_post", side_effect=OpenVikingError("HTTP 409 exists")):
            client.ensure_session()
            self.assertTrue(client._session_ready)
        client = OpenVikingClient("https://x", session_id="s")
        with mock.patch.object(client, "_post", side_effect=OpenVikingError("HTTP 500")):
            with self.assertRaises(OpenVikingError):
                client.ensure_session()

    def test_post_http_json_and_network_failures(self):
        client = OpenVikingClient("https://x", "secret")
        payload = json.dumps({"status": "ok", "result": {"x": 1}}).encode()
        with mock.patch(
            "agent_windows.openviking_memory.request_bytes",
            return_value=SafeHTTPResponse(200, payload, {}),
        ) as request:
            self.assertEqual(client._post("/p", {"a": 1}), {"x": 1})
        self.assertEqual(request.call_args.kwargs["headers"]["X-API-Key"], "secret")
        with mock.patch(
            "agent_windows.openviking_memory.request_bytes",
            return_value=SafeHTTPResponse(200, b"bad", {}),
        ):
            with self.assertRaisesRegex(OpenVikingError, "malformed"):
                client._post("/p")
        with mock.patch(
            "agent_windows.openviking_memory.request_bytes",
            side_effect=SafeHTTPError("down"),
        ):
            with self.assertRaisesRegex(OpenVikingError, "connection"):
                client._post("/p")
        with mock.patch(
            "agent_windows.openviking_memory.request_bytes",
            return_value=SafeHTTPResponse(401, b"secret echoed body", {}),
        ):
            with self.assertRaisesRegex(OpenVikingError, "HTTP 401") as raised:
                client._post("/p")
        self.assertNotIn("secret echoed body", str(raised.exception))
        with self.assertRaises(OpenVikingError):
            OpenVikingClient("")._post("/p")
        with self.assertRaises(OpenVikingError):
            OpenVikingClient("http://example.com")

    def test_empty_memory_and_empty_query_do_not_call_server(self):
        client = OpenVikingClient("https://x")
        with mock.patch.object(client, "_post") as post:
            client.remember("  ")
            self.assertEqual(client.find(" "), [])
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
