import json
import unittest
from unittest import mock

from agent_windows.errors import (
    ProviderAuthenticationError,
    ProviderBadResponse,
    ProviderConnectionError,
    ProviderPermissionError,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTimeout,
)
from agent_windows.http import HTTPResponse
from agent_windows.speech import AssemblyAISTT, DeepgramSTT, ElevenLabsTTS, STTManager, _check


def response(status, payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return HTTPResponse(status, body, {})


class SequenceClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        return self.responses.pop(0)


class StreamingClient(SequenceClient):
    def __init__(self, chunks):
        super().__init__([])
        self.chunks = chunks

    def iter_request(self, method, url, headers, body, timeout, *, chunk_size=4096):
        self.calls.append((method, url, headers, body, timeout, chunk_size))
        yield from self.chunks


class SpeechEdgeTests(unittest.TestCase):
    def test_http_status_check_classifies_provider_failures(self):
        cases = [
            (401, ProviderAuthenticationError),
            (403, ProviderPermissionError),
            (429, ProviderRateLimited),
            (500, ProviderServerError),
            (418, ProviderBadResponse),
        ]
        for status, error in cases:
            with self.subTest(status=status), self.assertRaises(error):
                _check(response(status, b"error"), "provider")
        _check(response(204, b""), "provider")

    def test_assemblyai_rejects_malformed_upload_and_submit_responses(self):
        with self.assertRaisesRegex(ProviderBadResponse, "upload response malformed"):
            AssemblyAISTT("key", client=SequenceClient([response(200, {})])).transcribe(b"audio")

        client = SequenceClient([response(200, {"upload_url": "https://upload"}), response(200, {})])
        with self.assertRaisesRegex(ProviderBadResponse, "submit response malformed"):
            AssemblyAISTT("key", client=client).transcribe(b"audio")

    def test_assemblyai_handles_pending_error_and_poll_timeout(self):
        error_client = SequenceClient([
            response(200, {"upload_url": "https://upload"}),
            response(200, {"id": "abc"}),
            response(200, {"status": "error"}),
        ])
        with self.assertRaisesRegex(ProviderBadResponse, "transcription failed"):
            AssemblyAISTT("key", client=error_client, poll_seconds=0).transcribe(b"audio")

        pending_client = SequenceClient([
            response(200, {"upload_url": "https://upload"}),
            response(200, {"id": "abc"}),
            response(200, {"status": "processing"}),
            response(200, {"status": "completed", "text": "done"}),
        ])
        with mock.patch("agent_windows.speech.time.sleep") as sleep:
            self.assertEqual(
                AssemblyAISTT("key", client=pending_client, poll_seconds=0.01).transcribe(b"audio", language=None),
                "done",
            )
            sleep.assert_called_once_with(0.01)

        timeout_client = SequenceClient([
            response(200, {"upload_url": "https://upload"}),
            response(200, {"id": "abc"}),
        ])
        with mock.patch("agent_windows.speech.time.monotonic", side_effect=[10.0, 12.0]):
            with self.assertRaisesRegex(ProviderTimeout, "polling timed out"):
                AssemblyAISTT("key", client=timeout_client, timeout=1).transcribe(b"audio")

    def test_deepgram_rejects_malformed_response_and_supports_multi_language(self):
        client = SequenceClient([response(200, {"results": {"channels": []}})])
        with self.assertRaisesRegex(ProviderBadResponse, "deepgram response malformed"):
            DeepgramSTT("key", client=client).transcribe(b"audio", language=None)
        self.assertIn("language=multi", client.calls[0][1])

    def test_stt_manager_skips_unavailable_falls_back_and_reports_errors(self):
        class Provider:
            def __init__(self, available=True, result=None, error=None):
                self.available = available
                self.result = result
                self.error = error

            def is_available(self):
                return self.available

            def transcribe(self, audio, *, content_type, language):
                if self.error:
                    raise self.error
                return self.result

        manager = STTManager([
            Provider(available=False),
            Provider(error=ProviderTimeout("slow")),
            Provider(result="recovered"),
        ])
        self.assertEqual(manager.transcribe(b"audio"), "recovered")

        failed = STTManager([
            Provider(error=ProviderAuthenticationError("bad key")),
            Provider(error=ProviderBadResponse("bad body")),
        ])
        with self.assertRaises(ProviderConnectionError) as caught:
            failed.transcribe(b"audio")
        self.assertIn("configuration: bad key", str(caught.exception))
        self.assertIn("bad body", str(caught.exception))

    def test_elevenlabs_availability_streaming_and_buffered_fallback(self):
        unconfigured = ElevenLabsTTS("", "")
        self.assertFalse(unconfigured.is_available())
        with self.assertRaises(ProviderAuthenticationError):
            unconfigured.synthesize("hello")
        with self.assertRaises(ProviderAuthenticationError):
            list(unconfigured.iter_audio("hello"))

        buffered = ElevenLabsTTS("key", "voice", client=SequenceClient([response(200, b"audio")]))
        self.assertEqual(list(buffered.iter_audio("hello")), [b"audio"])

        streaming_client = StreamingClient([b"a", b"b"])
        streaming = ElevenLabsTTS("key", "voice", client=streaming_client)
        self.assertEqual(list(streaming.iter_audio("hello")), [b"a", b"b"])
        self.assertIn("/stream?", streaming_client.calls[0][1])


if __name__ == "__main__":
    unittest.main()
