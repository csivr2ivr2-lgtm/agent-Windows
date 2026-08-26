import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_windows.safe_http import SafeHTTPError, request_bytes
from agent_windows.security import (
    SecurityValidationError,
    resolve_within,
    validate_external_http_url,
    validate_service_base_url,
    validate_service_endpoint_url,
)


class Response:
    def __init__(self, status=200, body=b"ok", headers=()):
        self.status = status
        self.body = body
        self.headers = list(headers)

    def read(self, limit):
        return self.body

    def getheaders(self):
        return self.headers


class Connection:
    def __init__(self, host, port=None, timeout=None, *, response=None, error=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.response = response or Response()
        self.error = error
        self.requests = []
        self.closed = False

    def request(self, method, target, body=None, headers=None):
        if self.error:
            raise self.error
        self.requests.append((method, target, body, dict(headers or {})))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class SecurityBoundaryTests(unittest.TestCase):
    def test_service_urls_require_tls_except_loopback(self):
        self.assertEqual(
            validate_service_base_url("http://127.0.0.1:3333/"),
            "http://127.0.0.1:3333",
        )
        self.assertEqual(
            validate_service_base_url("https://api.example.com"),
            "https://api.example.com",
        )
        with self.assertRaises(SecurityValidationError):
            validate_service_base_url("http://api.example.com")
        with self.assertRaises(SecurityValidationError):
            validate_service_base_url("https://api.example.com/v1")
        with self.assertRaises(SecurityValidationError):
            validate_service_base_url("https://user:pass@api.example.com")

    def test_service_endpoint_preserves_path_and_rejects_fragment(self):
        self.assertEqual(
            validate_service_endpoint_url("https://api.example.com/v1/items?q=1"),
            "https://api.example.com/v1/items?q=1",
        )
        with self.assertRaises(SecurityValidationError):
            validate_service_endpoint_url("https://api.example.com/x#fragment")

    def test_external_fetch_rejects_private_and_non_http_targets(self):
        blocked = (
            "http://localhost/x",
            "http://127.0.0.1/x",
            "http://10.0.0.1/x",
            "http://169.254.169.254/x",
            "http://[::1]/x",
            "ftp://example.com/x",
            "https://user:pass@example.com/x",
        )
        for value in blocked:
            with self.subTest(value=value):
                with self.assertRaises(SecurityValidationError):
                    validate_external_http_url(value)
        self.assertEqual(
            validate_external_http_url("https://example.com/path?q=1"),
            "https://example.com/path?q=1",
        )

    def test_resolve_within_blocks_escape_and_supports_exist_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            child = root / "child.txt"
            child.write_text("x", encoding="utf-8")
            self.assertEqual(resolve_within(root, child, must_exist=True), child.resolve())
            with self.assertRaises(SecurityValidationError):
                resolve_within(root, Path(directory) / "outside.txt")
            with self.assertRaises(FileNotFoundError):
                resolve_within(root, root / "missing.txt", must_exist=True)

    def test_safe_https_request_is_bounded_and_does_not_follow_redirects(self):
        response = Response(302, b"redirect", [("Location", "http://127.0.0.1/secret")])
        connection = Connection("api.example.com", response=response)
        with mock.patch(
            "agent_windows.safe_http.http.client.HTTPSConnection", return_value=connection
        ) as factory:
            result = request_bytes(
                "https://api.example.com/v1/test?q=1",
                method="POST",
                headers={"X-Test": "1"},
                body=b"{}",
                max_response_bytes=100,
            )
        factory.assert_called_once_with("api.example.com", port=None, timeout=20.0)
        self.assertEqual(result.status, 302)
        self.assertEqual(connection.requests[0][:2], ("POST", "/v1/test?q=1"))
        self.assertTrue(connection.closed)

    def test_safe_http_allows_loopback_only_and_maps_failures(self):
        local = Connection("127.0.0.1")
        with mock.patch("agent_windows.safe_http.http.client.HTTPConnection", return_value=local):
            self.assertEqual(request_bytes("http://127.0.0.1:11434/api/tags").status, 200)
        with self.assertRaises(SafeHTTPError):
            request_bytes("http://example.com/x")

        timed = Connection("x", error=socket.timeout("slow"))
        with mock.patch("agent_windows.safe_http.http.client.HTTPSConnection", return_value=timed):
            with self.assertRaisesRegex(SafeHTTPError, "timed out"):
                request_bytes("https://x/path")
        self.assertTrue(timed.closed)

    def test_safe_http_rejects_oversized_response(self):
        connection = Connection("x", response=Response(200, b"12345"))
        with mock.patch("agent_windows.safe_http.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(SafeHTTPError, "exceeds"):
                request_bytes("https://x/path", max_response_bytes=4)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
