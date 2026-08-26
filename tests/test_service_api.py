import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib import error, request

from agent_windows.service_api import ServiceBackend, ensure_token


class FakeRuntime:
    def handle_text(self, text):
        return f"reply:{text}"


class ServiceApiTests(unittest.TestCase):
    def test_token_is_persistent(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            first = ensure_token(data)
            second = ensure_token(data)
            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)

    def test_health_requires_auth_and_chat_works(self):
        with TemporaryDirectory() as directory:
            backend = ServiceBackend(FakeRuntime(), Path(directory), port=0)
            thread = threading.Thread(target=backend.serve_forever, daemon=True)
            thread.start()
            deadline = time.monotonic() + 3
            while backend._server is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(backend._server)
            port = backend._server.server_address[1]
            base = f"http://127.0.0.1:{port}"
            try:
                with self.assertRaises(error.HTTPError) as unauthorized:
                    request.urlopen(base + "/v1/health", timeout=2)
                self.assertEqual(unauthorized.exception.code, 401)

                health = request.Request(
                    base + "/v1/health",
                    headers={"Authorization": f"Bearer {backend.token}"},
                )
                with request.urlopen(health, timeout=2) as response:
                    self.assertEqual(response.status, 200)

                chat = request.Request(
                    base + "/v1/chat",
                    data=json.dumps({"text": "hello"}).encode(),
                    headers={
                        "Authorization": f"Bearer {backend.token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with request.urlopen(chat, timeout=2) as response:
                    payload = json.loads(response.read())
                self.assertEqual(payload["answer"], "reply:hello")
            finally:
                backend.stop()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
