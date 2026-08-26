import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from agent_windows.computer_use import (
    ComputerRouter,
    ComputerUseError,
    UFOExecutor,
    WindowsUseExecutor,
    build_computer_tools,
)


class ComputerUseTests(unittest.TestCase):
    def test_ufo_executes_direct_request_without_console(self):
        executor = UFOExecutor(timeout=12, python_executable="python")
        result = SimpleNamespace(returncode=0, stdout="done", stderr="")
        with mock.patch("agent_windows.computer_use._is_windows", return_value=True), \
             mock.patch("agent_windows.computer_use._module_available", return_value=True), \
             mock.patch("agent_windows.computer_use.subprocess.run", return_value=result) as run:
            output = executor.execute("Open Notepad & write hello")
        self.assertEqual(output["backend"], "ufo")
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["python", "-m", "ufo"])
        self.assertIn("--request", command)
        self.assertIn("Open Notepad & write hello", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 12)

    def test_ufo_failures_and_empty_task(self):
        executor = UFOExecutor()
        with mock.patch("agent_windows.computer_use._is_windows", return_value=False):
            with self.assertRaises(ComputerUseError): executor.execute("x")
        with mock.patch("agent_windows.computer_use._is_windows", return_value=True), \
             mock.patch("agent_windows.computer_use._module_available", return_value=True):
            with self.assertRaises(ValueError): executor.execute(" ")
            with mock.patch("agent_windows.computer_use.subprocess.run", side_effect=__import__('subprocess').TimeoutExpired('ufo', 1)):
                with self.assertRaisesRegex(ComputerUseError, "timeout"): executor.execute("x")
            bad = SimpleNamespace(returncode=2, stdout="", stderr="bad")
            with mock.patch("agent_windows.computer_use.subprocess.run", return_value=bad):
                with self.assertRaisesRegex(ComputerUseError, "failed"): executor.execute("x")

    def _windows_use_modules(self, *, reject_max_steps=False, fail=None):
        module = types.ModuleType("windows_use")
        provider = types.ModuleType("windows_use.providers.ollama")
        class ChatOllama:
            def __init__(self, model): self.model = model
        class Agent:
            instances = []
            def __init__(self, **kwargs):
                if reject_max_steps and "max_steps" in kwargs: raise TypeError("old signature")
                self.kwargs = kwargs; Agent.instances.append(self)
            def invoke(self, *, task):
                if fail: raise fail
                return SimpleNamespace(content="finished:" + task)
        module.Agent = Agent; provider.ChatOllama = ChatOllama
        return module, provider, Agent

    def test_windows_use_ollama_execution_and_signature_fallback(self):
        for reject in (False, True):
            module, provider, Agent = self._windows_use_modules(reject_max_steps=reject)
            with mock.patch("agent_windows.computer_use._is_windows", return_value=True), \
                 mock.patch("agent_windows.computer_use._module_available", return_value=True), \
                 mock.patch.dict(sys.modules, {"windows_use": module, "windows_use.providers.ollama": provider}):
                result = WindowsUseExecutor("qwen3:0.6b").execute("open calc")
            self.assertEqual(result["backend"], "windows-use")
            self.assertIn("finished:open calc", result["output"])
            self.assertEqual(Agent.instances[-1].kwargs["llm"].model, "qwen3:0.6b")

    def test_windows_use_unavailable_and_failure(self):
        with mock.patch("agent_windows.computer_use._is_windows", return_value=False):
            with self.assertRaises(ComputerUseError): WindowsUseExecutor("m").execute("x")
        module, provider, _ = self._windows_use_modules(fail=RuntimeError("boom"))
        with mock.patch("agent_windows.computer_use._is_windows", return_value=True), \
             mock.patch("agent_windows.computer_use._module_available", return_value=True), \
             mock.patch.dict(sys.modules, {"windows_use": module, "windows_use.providers.ollama": provider}):
            with self.assertRaisesRegex(ComputerUseError, "RuntimeError"):
                WindowsUseExecutor("m").execute("x")

    def test_router_primary_fallback_status_and_validation(self):
        class Exec:
            def __init__(self, name, available=True, result=None, error=None, model=""):
                self.name=name; self.available=available; self.result=result; self.error=error; self.model=model
            def is_available(self): return self.available
            def execute(self, task):
                if self.error: raise self.error
                return self.result or {"backend": self.name}
        ufo = Exec("ufo", error=ComputerUseError("bad"))
        win = Exec("windows-use", result={"backend":"windows-use"}, model="m")
        router = ComputerRouter(ufo, win)
        self.assertEqual(router.execute("x")["backend"], "windows-use")
        self.assertEqual(router.status()["selected"], "auto")
        with self.assertRaises(ValueError): ComputerRouter(ufo, win, backend="bad")
        with self.assertRaises(ComputerUseError): ComputerRouter(Exec("ufo", False), Exec("windows-use", False, model="m")).execute("x")
        with self.assertRaises(ComputerUseError): ComputerRouter(Exec("ufo", True, error=ComputerUseError("x")), Exec("windows-use", False, model="m")).execute("x")

    def test_computer_tools_require_high_risk_for_execution(self):
        router = SimpleNamespace(status=mock.Mock(return_value={}), execute=mock.Mock(return_value={"ok":1}))
        tools = {tool.name: tool for tool in build_computer_tools(router)}
        self.assertEqual(tools["computer_status"].risk, "read_only")
        self.assertEqual(tools["computer_task"].risk, "high")
        tools["computer_task"].invoke({"task":"x"})
        router.execute.assert_called_once_with("x")


if __name__ == "__main__":
    unittest.main()
