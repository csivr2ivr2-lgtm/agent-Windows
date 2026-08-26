from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 64 * 1024


def _service_data_dir(data_dir: Path) -> Path:
    """Resolve the machine service data directory for user-session clients.

    The Windows service is installed under ProgramData so LocalSystem does not
    need access to the signed-in user's profile. Session clients still pass
    their normal Settings.data_dir; when the machine service token exists we
    transparently use the ProgramData token instead.
    """
    override = os.getenv("AGENT_SERVICE_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    program_data = os.getenv("PROGRAMDATA", "").strip()
    if program_data:
        machine_data = Path(program_data) / "AgentWindowsAI" / "data"
        if (machine_data / "service.token").is_file():
            return machine_data

    return Path(data_dir)


def _service_port() -> int:
    try:
        port = int(os.getenv("AGENT_SERVICE_PORT", str(DEFAULT_PORT)))
    except ValueError:
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def token_path(data_dir: Path) -> Path:
    return data_dir / "service.token"


def ensure_token(data_dir: Path) -> str:
    path = token_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(48)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


def read_token(data_dir: Path) -> str:
    path = token_path(_service_data_dir(data_dir))
    if not path.exists():
        raise RuntimeError("Agent Windows service token does not exist; install/start the service first")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("Agent Windows service token is empty")
    return token


class ServiceBackend:
    def __init__(self, runtime, data_dir: Path, *, host: str = DEFAULT_HOST, port: int | None = None):
        self.runtime = runtime
        self.data_dir = Path(data_dir)
        self.host = host
        self.port = _service_port() if port is None else port
        self.token = ensure_token(self.data_dir)
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    def _handler(self):
        backend = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "AgentWindowsService/1"

            def log_message(self, _format, *_args):
                return

            def _authorized(self) -> bool:
                header = self.headers.get("Authorization", "")
                supplied = header[7:] if header.startswith("Bearer ") else ""
                return bool(supplied) and hmac.compare_digest(supplied, backend.token)

            def _json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _require_auth(self) -> bool:
                if self._authorized():
                    return True
                self._json(401, {"error": "unauthorized"})
                return False

            def do_GET(self):
                if self.path != "/v1/health":
                    self._json(404, {"error": "not_found"})
                    return
                if not self._require_auth():
                    return
                self._json(200, {"status": "ok"})

            def do_POST(self):
                if self.path != "/v1/chat":
                    self._json(404, {"error": "not_found"})
                    return
                if not self._require_auth():
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._json(400, {"error": "invalid_content_length"})
                    return
                if length <= 0 or length > MAX_REQUEST_BYTES:
                    self._json(413, {"error": "request_too_large"})
                    return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._json(400, {"error": "invalid_json"})
                    return
                text = payload.get("text") if isinstance(payload, dict) else None
                if not isinstance(text, str) or not text.strip():
                    self._json(400, {"error": "text_required"})
                    return
                try:
                    with backend._lock:
                        answer = backend.runtime.handle_text(text.strip())
                except Exception as exc:
                    self._json(500, {"error": "agent_failed", "detail": str(exc)[:300]})
                    return
                self._json(200, {"answer": answer})

        return Handler

    def serve_forever(self) -> None:
        self._server = ThreadingHTTPServer((self.host, self.port), self._handler())
        self._server.daemon_threads = True
        self._server.serve_forever(poll_interval=0.25)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def service_health(data_dir: Path, *, timeout: float = 2.0) -> bool:
    try:
        token = read_token(data_dir)
        req = request.Request(
            f"http://{DEFAULT_HOST}:{_service_port()}/v1/health",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except (OSError, RuntimeError, error.URLError):
        return False


def service_chat(text: str, data_dir: Path, *, timeout: float = 90.0) -> str:
    token = read_token(data_dir)
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"http://{DEFAULT_HOST}:{_service_port()}/v1/chat",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Agent Windows service HTTP {exc.code}: {detail}") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Agent Windows service unavailable: {exc}") from exc
    answer = payload.get("answer") if isinstance(payload, dict) else None
    if not isinstance(answer, str):
        raise RuntimeError("Agent Windows service returned an invalid response")
    return answer
