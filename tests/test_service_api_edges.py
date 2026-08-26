import io
import json
import os
import tempfile
import threading
import types
import unittest
from pathlib import Path
from urllib import error, request
from unittest import mock

from agent_windows import service_api


class Runtime:
    def __init__(self, fail=False): self.fail = fail
    def handle_text(self, text):
        if self.fail: raise RuntimeError("boom")
        return "answer:" + text


class ServiceApiEdgeTests(unittest.TestCase):
    def test_data_dir_port_and_token_edge_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            override = root / "override"
            with mock.patch.dict(os.environ, {"AGENT_SERVICE_DATA_DIR": str(override)}, clear=False):
                self.assertEqual(service_api._service_data_dir(root), override)
            machine = root / "AgentWindowsAI" / "data"; machine.mkdir(parents=True)
            (machine / "service.token").write_text("x", encoding="utf-8")
            with mock.patch.dict(os.environ, {"AGENT_SERVICE_DATA_DIR": "", "PROGRAMDATA": str(root)}, clear=False):
                self.assertEqual(service_api._service_data_dir(root / "user"), machine)
            with mock.patch.dict(os.environ, {"AGENT_SERVICE_DATA_DIR": "", "PROGRAMDATA": ""}, clear=False):
                self.assertEqual(service_api._service_data_dir(root), root)
            for value in ("bad", "0", "70000"):
                with mock.patch.dict(os.environ, {"AGENT_SERVICE_PORT": value}, clear=False):
                    self.assertEqual(service_api._service_port(), service_api.DEFAULT_PORT)
            with mock.patch.dict(os.environ, {"AGENT_SERVICE_PORT": "9999"}, clear=False):
                self.assertEqual(service_api._service_port(), 9999)

            token = service_api.ensure_token(root / "data")
            self.assertEqual(service_api.ensure_token(root / "data"), token)
            path = service_api.token_path(root / "data")
            path.write_text("", encoding="utf-8")
            new_token = service_api.ensure_token(root / "data")
            self.assertTrue(new_token)
            path.unlink()
            with self.assertRaises(RuntimeError): service_api.read_token(root / "data")
            path.write_text("\n", encoding="utf-8")
            with self.assertRaises(RuntimeError): service_api.read_token(root / "data")

    def _server(self, runtime):
        tmp = tempfile.TemporaryDirectory()
        backend = service_api.ServiceBackend(runtime, Path(tmp.name), port=0)
        server = service_api.ThreadingHTTPServer(("127.0.0.1", 0), backend._handler())
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        return tmp, backend, server, thread

    def _call(self, server, path, *, method="GET", body=None, token=None, headers=None):
        hdr = dict(headers or {})
        if token: hdr["Authorization"] = "Bearer " + token
        req = request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=body, headers=hdr, method=method)
        try:
            with request.urlopen(req, timeout=2) as resp:
                return resp.status, json.loads(resp.read())
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_handler_error_branches(self):
        tmp, backend, server, thread = self._server(Runtime())
        try:
            self.assertEqual(self._call(server, "/missing")[0], 404)
            self.assertEqual(self._call(server, "/v1/health")[0], 401)
            self.assertEqual(self._call(server, "/v1/health", token=backend.token)[0], 200)
            self.assertEqual(self._call(server, "/missing", method="POST", token=backend.token)[0], 404)
            self.assertEqual(self._call(server, "/v1/chat", method="POST")[0], 401)
            self.assertEqual(self._call(server, "/v1/chat", method="POST", token=backend.token, body=b"", headers={"Content-Length": "0"})[0], 413)
            huge = b"x" * (service_api.MAX_REQUEST_BYTES + 1)
            self.assertEqual(self._call(server, "/v1/chat", method="POST", token=backend.token, body=huge)[0], 413)
            self.assertEqual(self._call(server, "/v1/chat", method="POST", token=backend.token, body=b"{")[0], 400)
            self.assertEqual(self._call(server, "/v1/chat", method="POST", token=backend.token, body=b"{}")[0], 400)
            body = json.dumps({"text": " hi "}).encode()
            code, payload = self._call(server, "/v1/chat", method="POST", token=backend.token, body=body)
            self.assertEqual(code, 200); self.assertEqual(payload["answer"], "answer:hi")
        finally:
            server.shutdown(); server.server_close(); thread.join(2); tmp.cleanup()

        tmp, backend, server, thread = self._server(Runtime(fail=True))
        try:
            body = json.dumps({"text": "x"}).encode()
            code, payload = self._call(server, "/v1/chat", method="POST", token=backend.token, body=body)
            self.assertEqual(code, 500); self.assertEqual(payload["error"], "agent_failed")
        finally:
            server.shutdown(); server.server_close(); thread.join(2); tmp.cleanup()

    def test_client_helpers_errors_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            with mock.patch.object(service_api, "read_token", side_effect=RuntimeError("missing")):
                self.assertFalse(service_api.service_health(data))

            class Resp:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def read(self): return json.dumps({"answer": "ok"}).encode()
            with mock.patch.object(service_api, "read_token", return_value="t"), \
                 mock.patch.object(service_api.request, "urlopen", return_value=Resp()):
                self.assertTrue(service_api.service_health(data))
                self.assertEqual(service_api.service_chat("x", data), "ok")

            http_exc = error.HTTPError("u", 500, "bad", {}, io.BytesIO(b"detail"))
            with mock.patch.object(service_api, "read_token", return_value="t"), \
                 mock.patch.object(service_api.request, "urlopen", side_effect=http_exc):
                with self.assertRaises(RuntimeError): service_api.service_chat("x", data)
            with mock.patch.object(service_api, "read_token", return_value="t"), \
                 mock.patch.object(service_api.request, "urlopen", side_effect=error.URLError("down")):
                with self.assertRaises(RuntimeError): service_api.service_chat("x", data)
            class BadResp(Resp):
                def read(self): return b"{}"
            with mock.patch.object(service_api, "read_token", return_value="t"), \
                 mock.patch.object(service_api.request, "urlopen", return_value=BadResp()):
                with self.assertRaises(RuntimeError): service_api.service_chat("x", data)

            backend = service_api.ServiceBackend(Runtime(), data, port=0)
            fake_server = types.SimpleNamespace(shutdown=mock.Mock(), server_close=mock.Mock())
            backend._server = fake_server
            backend.stop(); fake_server.shutdown.assert_called_once(); fake_server.server_close.assert_called_once()
            backend.stop()


if __name__ == "__main__":
    unittest.main()
