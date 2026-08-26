import json
import unittest
from unittest import mock

from agent_windows.model_fit import (
    HardwareSnapshot,
    assess_model_fit,
    estimate_model_bytes,
    hardware_snapshot,
    infer_parameter_billions,
    installed_ollama_fits,
    model_fit_report,
    quantization_bits,
)
from agent_windows.safe_http import SafeHTTPError, SafeHTTPResponse


HARDWARE = HardwareSnapshot(
    os="Windows",
    machine="AMD64",
    cpu="test",
    cpu_count=4,
    memory_total_bytes=8 * 1024**3,
    memory_available_bytes=6 * 1024**3,
    gpu_names=("Intel UHD",),
)


class ModelFitTests(unittest.TestCase):
    def test_quantization_and_estimation_validation(self):
        self.assertEqual(quantization_bits("Q4_K_M"), 4.5)
        self.assertEqual(quantization_bits("fp16"), 16.0)
        self.assertEqual(quantization_bits("7bit"), 7.0)
        self.assertEqual(quantization_bits("unknown"), 4.5)
        self.assertGreater(estimate_model_bytes(0.6, "q4", 4096), 0)
        with self.assertRaises(ValueError):
            estimate_model_bytes(0, "q4", 8192)
        with self.assertRaises(ValueError):
            estimate_model_bytes(1, "q4", 0)

    def test_fit_verdicts_and_unknown_memory(self):
        small = assess_model_fit(
            "small", 0.6, quantization="q4", context_tokens=4096, hardware=HARDWARE
        )
        self.assertIn(small.verdict, {"COMFORTABLE", "FITS"})
        self.assertGreaterEqual(small.score, 85)
        huge = assess_model_fit("huge", 70, hardware=HARDWARE)
        self.assertEqual(huge.verdict, "DOES_NOT_FIT")
        unknown = assess_model_fit(
            "unknown",
            1,
            hardware=HardwareSnapshot("x", "x", "x", 1, None, None),
        )
        self.assertEqual(unknown.verdict, "UNKNOWN")
        self.assertIsNone(unknown.headroom_bytes)

    def test_parameter_inference(self):
        self.assertEqual(infer_parameter_billions("qwen3:0.6b"), 0.6)
        self.assertEqual(infer_parameter_billions("llama-3.2-3B-q4"), 3.0)
        self.assertIsNone(infer_parameter_billions("mystery"))

    def test_ollama_model_fit_and_report(self):
        tags = [
            {"name": "qwen3:0.6b", "details": {"quantization_level": "Q4_K_M"}},
            {
                "name": "mystery",
                "details": {"parameter_size": "1.5B", "quantization_level": "Q8_0"},
            },
            {"name": "skip"},
        ]
        with mock.patch("agent_windows.model_fit._ollama_tags", return_value=tags):
            fits = installed_ollama_fits("http://localhost:11434/v1", hardware=HARDWARE)
        self.assertEqual({fit.model for fit in fits}, {"qwen3:0.6b", "mystery"})
        with mock.patch(
            "agent_windows.model_fit.hardware_snapshot", return_value=HARDWARE
        ), mock.patch("agent_windows.model_fit._ollama_tags", return_value=tags):
            report = model_fit_report(parameter_billions=2, model="candidate")
        self.assertEqual(report["candidate"]["model"], "candidate")
        self.assertTrue(report["ollama_models"])
        self.assertIsNotNone(report["recommended_local_model"])

    def test_ollama_tags_network_and_json_fail_closed(self):
        from agent_windows import model_fit

        payload = json.dumps({"models": [{"name": "x:1b"}]}).encode()
        with mock.patch(
            "agent_windows.model_fit.request_bytes",
            return_value=SafeHTTPResponse(200, payload, {}),
        ):
            self.assertEqual(
                model_fit._ollama_tags("http://localhost:11434/v1")[0]["name"], "x:1b"
            )
        with mock.patch(
            "agent_windows.model_fit.request_bytes", side_effect=SafeHTTPError("down")
        ):
            self.assertEqual(model_fit._ollama_tags("http://localhost:11434/v1"), [])
        with mock.patch("agent_windows.model_fit.request_bytes") as request:
            self.assertEqual(model_fit._ollama_tags("http://example.com:11434/v1"), [])
            request.assert_not_called()
        with mock.patch(
            "agent_windows.model_fit.request_bytes",
            return_value=SafeHTTPResponse(200, b"not-json", {}),
        ):
            self.assertEqual(model_fit._ollama_tags("http://localhost:11434"), [])
        with mock.patch(
            "agent_windows.model_fit.request_bytes",
            return_value=SafeHTTPResponse(503, b"unavailable", {}),
        ):
            self.assertEqual(model_fit._ollama_tags("http://localhost:11434"), [])

    def test_hardware_snapshot_uses_platform_memory_paths(self):
        with mock.patch(
            "agent_windows.model_fit.sys_platform_is_windows", return_value=False
        ), mock.patch("agent_windows.model_fit._linux_memory", return_value=(100, 50)):
            snap = hardware_snapshot(include_gpu=False)
        self.assertEqual((snap.memory_total_bytes, snap.memory_available_bytes), (100, 50))
        self.assertEqual(snap.gpu_names, ())


if __name__ == "__main__":
    unittest.main()
