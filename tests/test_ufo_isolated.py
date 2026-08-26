import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_windows.computer_use import UFOExecutor


class IsolatedUfoTests(unittest.TestCase):
    def test_checkout_venv_is_a_valid_ufo_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ufo").mkdir()
            python = root / ".venv" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_text("stub", encoding="utf-8")
            executor = UFOExecutor(workdir=root, python_executable="fallback")
            with mock.patch("agent_windows.computer_use._is_windows", return_value=True), mock.patch(
                "agent_windows.computer_use._module_available", return_value=False
            ):
                self.assertTrue(executor.is_available())
                self.assertEqual(executor._runtime_python(), str(python))


if __name__ == "__main__":
    unittest.main()
