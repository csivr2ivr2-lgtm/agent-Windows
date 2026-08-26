import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_windows.model_lab import ModelLab, TrainingDatasetManager, build_model_lab_tools


class ModelLabTests(unittest.TestCase):
    def test_dataset_export_requires_opt_in_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "raw.jsonl"
            source.write_text(json.dumps({"instruction": "x", "output": "api_key=supersecret123456789"}) + "\n", encoding="utf-8")
            manager = TrainingDatasetManager(Path(directory) / "datasets")
            with self.assertRaises(PermissionError):
                manager.export(source, Path(directory) / "no.jsonl", approved=False)
            target = manager.export(source, Path(directory) / "yes.jsonl", approved=True)
            self.assertIn("[REDACTED]", target.read_text(encoding="utf-8"))
            self.assertNotIn("supersecret", target.read_text(encoding="utf-8"))

    def test_prepare_unsloth_and_soup_are_real_runnable_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "data.jsonl"
            source.write_text(json.dumps({"text": "hello"}) + "\n", encoding="utf-8")
            lab = ModelLab(Path(directory) / "lab")
            unsloth = lab.prepare("unsloth", source, "unsloth/Qwen3-0.6B", approved_dataset=True)
            soup = lab.prepare("soup", source, "Qwen/Qwen3-0.6B", approved_dataset=True)
            script = Path(unsloth.config_or_script).read_text(encoding="utf-8")
            config = Path(soup.config_or_script).read_text(encoding="utf-8")
            self.assertIn("FastLanguageModel.from_pretrained", script)
            self.assertIn("SFTTrainer", script)
            self.assertIn("task: sft", config)
            self.assertIn("quantization: nf4", config)

    def test_execute_requires_second_approval_and_compatible_gpu(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "data.jsonl"
            source.write_text(json.dumps({"text": "hello"}) + "\n", encoding="utf-8")
            lab = ModelLab(Path(directory) / "lab")
            job = lab.prepare("unsloth", source, "model", approved_dataset=True)
            with patch.object(lab, "_nvidia_gpus", return_value=()):
                report = lab.run(job.job_id, approved_run=False, execute=False)
                self.assertEqual(report["status"], "INCOMPATIBLE_LOCAL_HOST")
                with self.assertRaises(PermissionError):
                    lab.run(job.job_id, approved_run=False, execute=True)

    def test_model_lab_tools_do_not_start_training(self):
        with tempfile.TemporaryDirectory() as directory:
            lab = ModelLab(Path(directory) / "lab")
            tools = {tool.name: tool for tool in build_model_lab_tools(lab)}
            self.assertEqual(tools["model_lab_status"].risk, "read_only")
            self.assertEqual(tools["training_dataset_preview"].risk, "read_only")


if __name__ == "__main__":
    unittest.main()
