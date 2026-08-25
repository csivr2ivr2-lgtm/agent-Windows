import unittest

from agent_windows.contracts import LLMResponse, Message
from agent_windows.errors import (
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTimeout,
)
from agent_windows.provider_manager import ProviderManager, RetryPolicy


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)


class FakeProvider:
    def __init__(self, name, replies, available=True):
        self.name = name
        self.replies = list(replies)
        self.available = available
        self.calls = 0

    def is_available(self):
        return self.available

    def complete(self, messages, tools):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class ProviderManagerTests(unittest.TestCase):
    def test_poor_network_prefers_local_provider(self):
        network = type("Network", (), {"state": type("State", (), {"value": "POOR"})(), "record": lambda *a, **k: None})()
        cloud = FakeProvider("cloud", [LLMResponse(text="cloud")])
        local = FakeProvider("local", [LLMResponse(text="local")])
        manager = ProviderManager([cloud, local], network_monitor=network)
        self.assertEqual(manager.complete([], []).text, "local")
        self.assertEqual(cloud.calls, 0)
    def make_manager(self, providers, clock, attempts=2):
        return ProviderManager(
            providers,
            retry_policy=RetryPolicy(
                max_attempts=attempts,
                base_delay=0.5,
                max_delay=2,
                transient_cooldown=15,
                rate_limit_cooldown=60,
                auth_cooldown=300,
            ),
            clock=clock,
            sleep=clock.sleep,
        )

    def test_timeout_retries_then_succeeds_without_fallback(self):
        clock = FakeClock()
        first = FakeProvider("first", [ProviderTimeout("slow"), LLMResponse(text="ok", provider="first")])
        second = FakeProvider("second", [LLMResponse(text="backup", provider="second")])
        result = self.make_manager([first, second], clock).complete([Message("user", "hi")], [])
        self.assertEqual(result.provider, "first")
        self.assertEqual(first.calls, 2)
        self.assertEqual(second.calls, 0)
        self.assertEqual(clock.sleeps, [0.5])

    def test_connection_and_5xx_retry_then_fallback(self):
        for error in (ProviderConnectionError("offline"), ProviderServerError("503")):
            with self.subTest(error=type(error).__name__):
                clock = FakeClock()
                first = FakeProvider("first", [error, error])
                second = FakeProvider("second", [LLMResponse(text="backup", provider="second")])
                manager = self.make_manager([first, second], clock)
                self.assertEqual(manager.complete([Message("user", "hi")], []).provider, "second")
                self.assertEqual(first.calls, 2)
                self.assertFalse(manager.health["first"].healthy)
                self.assertEqual(manager.health["first"].cooldown_until, 115)

    def test_auth_failure_is_not_retried_and_enters_long_cooldown(self):
        clock = FakeClock()
        first = FakeProvider("first", [ProviderAuthenticationError("401")])
        second = FakeProvider("second", [LLMResponse(text="backup", provider="second")])
        manager = self.make_manager([first, second], clock)
        self.assertEqual(manager.complete([Message("user", "hi")], []).provider, "second")
        self.assertEqual(first.calls, 1)
        self.assertEqual(manager.health["first"].cooldown_until, 400)

    def test_rate_limit_is_not_retried_and_honors_retry_after(self):
        clock = FakeClock()
        first = FakeProvider("first", [ProviderRateLimited("429", retry_after=90)])
        second = FakeProvider("second", [LLMResponse(text="backup", provider="second")])
        manager = self.make_manager([first, second], clock)
        self.assertEqual(manager.complete([Message("user", "hi")], []).provider, "second")
        self.assertEqual(first.calls, 1)
        self.assertEqual(manager.health["first"].cooldown_until, 190)

    def test_provider_in_cooldown_is_skipped_then_recovers(self):
        clock = FakeClock()
        first = FakeProvider("first", [ProviderTimeout("slow"), LLMResponse(text="primary", provider="first")])
        second = FakeProvider("second", [
            LLMResponse(text="backup1", provider="second"),
            LLMResponse(text="backup2", provider="second"),
        ])
        manager = self.make_manager([first, second], clock, attempts=1)
        self.assertEqual(manager.complete([Message("user", "one")], []).provider, "second")
        self.assertEqual(manager.complete([Message("user", "two")], []).provider, "second")
        self.assertEqual(first.calls, 1)
        clock.now = 116
        self.assertEqual(manager.complete([Message("user", "three")], []).provider, "first")
        self.assertTrue(manager.health["first"].healthy)

    def test_unconfigured_provider_is_skipped(self):
        clock = FakeClock()
        first = FakeProvider("first", [], available=False)
        second = FakeProvider("second", [LLMResponse(text="ok", provider="second")])
        result = self.make_manager([first, second], clock).complete([Message("user", "hi")], [])
        self.assertEqual(result.provider, "second")
        self.assertEqual(first.calls, 0)


if __name__ == "__main__":
    unittest.main()
