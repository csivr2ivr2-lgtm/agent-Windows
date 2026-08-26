import unittest
from pathlib import Path


class FinalizerScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (
            Path(__file__).resolve().parents[1] / "scripts" / "finalize-ai-aharon.ps1"
        ).read_text(encoding="utf-8")

    def test_native_arguments_are_passed_as_an_explicit_array(self):
        self.assertNotIn("ValueFromRemainingArguments", self.script)
        self.assertIn("[string[]]$ArgumentList = @()", self.script)
        self.assertIn("& $FilePath @ArgumentList", self.script)

    def test_editable_install_does_not_expose_dash_e_to_powershell_binding(self):
        self.assertIn(
            "Invoke-Checked -FilePath $Python -ArgumentList @('-m', 'pip', 'install', '-e'",
            self.script,
        )


if __name__ == "__main__":
    unittest.main()
