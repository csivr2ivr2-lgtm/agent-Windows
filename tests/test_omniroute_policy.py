import unittest

from agent_windows.omniroute_policy import OmniRoutePolicy
from agent_windows.provider_manager import ProviderHealth


class Provider:
    def __init__(self, name):
        self.name = name


class OmniRoutePolicyTests(unittest.TestCase):
    def test_fast_strategy_prefers_measured_latency(self):
        a, b = Provider("a"), Provider("b")
        health = {
            "a": ProviderHealth(latency_ema_ms=900),
            "b": ProviderHealth(latency_ema_ms=120),
        }
        ordered = OmniRoutePolicy("fast").order(
            [a, b], health, network_state="GOOD", last_good=None
        )
        self.assertEqual([p.name for p in ordered], ["b", "a"])

    def test_auto_uses_last_good_and_headroom_signals(self):
        a, b = Provider("a"), Provider("b")
        health = {
            "a": ProviderHealth(latency_ema_ms=300),
            "b": ProviderHealth(latency_ema_ms=320),
        }
        policy = OmniRoutePolicy("auto", quota_headroom={"a": 0.1, "b": 1.0})
        ordered = policy.order([a, b], health, network_state="GOOD", last_good="b")
        self.assertEqual(ordered[0].name, "b")

    def test_poor_network_boosts_local_in_auto_mode(self):
        cloud, local = Provider("cloud"), Provider("local")
        health = {
            "cloud": ProviderHealth(latency_ema_ms=100),
            "local": ProviderHealth(latency_ema_ms=500),
        }
        ordered = OmniRoutePolicy("auto").order(
            [cloud, local], health, network_state="POOR", last_good=None
        )
        self.assertEqual(ordered[0].name, "local")


if __name__ == "__main__":
    unittest.main()
