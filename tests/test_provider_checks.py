import unittest

from agent_windows.contracts import LLMResponse
from agent_windows.errors import ProviderPermissionError
from agent_windows.provider_checks import check_provider


class FakeClock:
    def __init__(self):
        self.values = iter([1.0, 1.125])

    def __call__(self):
        return next(self.values)


class FakeProvider:
    name = "fake"

    def __init__(self, result=None, available=True):
        self.result = result
        self.available = available

    def is_available(self):
        return self.available

    def complete(self, messages, tools):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result or LLMResponse(text="OK", provider=self.name)


class ProviderCheckTests(unittest.TestCase):
    def test_unconfigured_is_not_fake_pass(self):
        result = check_provider(FakeProvider(available=False))
        self.assertEqual(result.status, "UNCONFIGURED")
        self.assertIsNone(result.latency_ms)

    def test_success_records_latency(self):
        result = check_provider(FakeProvider(), clock=FakeClock())
        self.assertEqual(result.status, "OK")
        self.assertEqual(result.latency_ms, 125.0)

    def test_403_is_classified_as_permission_not_bad_key(self):
        error = ProviderPermissionError("groq permission/model access denied (HTTP 403)")
        result = check_provider(FakeProvider(error), clock=FakeClock())
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.http_status, 403)
        self.assertEqual(result.error_type, "ProviderPermissionError")


if __name__ == "__main__":
    unittest.main()
