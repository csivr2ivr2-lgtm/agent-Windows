import threading
import unittest

from agent_windows.agent_loop import AgentBudget, AgentLoop
from agent_windows.contracts import LLMStreamEvent, ToolCall
from agent_windows.errors import ProviderConnectionError, ProviderUnavailable
from agent_windows.memory import InMemoryStore
from agent_windows.provider_manager import ProviderManager
from agent_windows.router import LLMRouter
from agent_windows.tools import ToolRegistry


class Tool:
    name = "current_time"
    description = "current local time"
    schema = {"type": "object", "properties": {}}
    risk = "read_only"

    def __init__(self):
        self.calls = 0

    def invoke(self, arguments):
        self.calls += 1
        return {"time": "17:30", "arguments": dict(arguments)}


class StreamingProvider:
    name = "streaming"

    def __init__(self):
        self.turn = 0
        self.seen_messages = []

    def is_available(self):
        return True

    def stream_events(self, messages, tools, *, cancel_event=None):
        self.turn += 1
        self.seen_messages.append(list(messages))
        if self.turn == 1:
            yield LLMStreamEvent.text_delta(self.name, "אני בודק. ")
            yield LLMStreamEvent.call(self.name, ToolCall("current_time", {}))
        else:
            assert any(m.role == "tool" and "17:30" in m.content for m in messages)
            yield LLMStreamEvent.text_delta(self.name, "השעה 17:30.")


class ErrorStreamProvider:
    def __init__(self, name, *, emit_first=False, fail=True):
        self.name = name
        self.emit_first = emit_first
        self.fail = fail
        self.calls = 0

    def is_available(self):
        return True

    def stream_events(self, messages, tools, *, cancel_event=None):
        self.calls += 1
        if self.emit_first:
            yield LLMStreamEvent.text_delta(self.name, "partial")
        if self.fail:
            raise ProviderConnectionError("stream down")
        yield LLMStreamEvent.text_delta(self.name, "backup")


class StreamingAgentLoopTests(unittest.TestCase):
    def test_voice_stream_executes_tool_then_continues_same_agent_loop(self):
        provider = StreamingProvider()
        tool = Tool()
        memory = InMemoryStore()
        loop = AgentLoop(
            LLMRouter(ProviderManager([provider])),
            memory,
            ToolRegistry([tool]),
            system_prompt="system",
        )
        chunks = list(loop.stream("מה השעה?"))
        self.assertEqual(chunks, ["אני בודק. ", "השעה 17:30."])
        self.assertEqual(tool.calls, 1)
        self.assertEqual(provider.turn, 2)
        self.assertTrue(memory.search("17:30"))

    def test_stream_tool_budget_stops_before_second_tool_execution(self):
        class AlwaysTool(StreamingProvider):
            def stream_events(self, messages, tools, *, cancel_event=None):
                yield LLMStreamEvent.call(self.name, ToolCall("current_time", {}))

        tool = Tool()
        loop = AgentLoop(
            LLMRouter(ProviderManager([AlwaysTool()])),
            InMemoryStore(),
            ToolRegistry([tool]),
            system_prompt="system",
        )
        chunks = list(loop.stream("loop", budget=AgentBudget(max_steps=3, max_tool_calls=1)))
        self.assertEqual(tool.calls, 1)
        self.assertIn("מגבלת פעולות", "".join(chunks))

    def test_stream_cancellation_prevents_tool_execution(self):
        event = threading.Event()

        class CancelsBeforeCall(StreamingProvider):
            def stream_events(self, messages, tools, *, cancel_event=None):
                yield LLMStreamEvent.text_delta(self.name, "רגע")
                event.set()
                yield LLMStreamEvent.call(self.name, ToolCall("current_time", {}))

        tool = Tool()
        loop = AgentLoop(
            LLMRouter(ProviderManager([CancelsBeforeCall()])),
            InMemoryStore(),
            ToolRegistry([tool]),
            system_prompt="system",
        )
        self.assertEqual(list(loop.stream("x", cancel_event=event)), ["רגע"])
        self.assertEqual(tool.calls, 0)

    def test_provider_manager_falls_back_before_any_stream_output(self):
        first = ErrorStreamProvider("first")
        second = ErrorStreamProvider("second", fail=False)
        manager = ProviderManager([first, second])
        events = list(manager.stream_events([], []))
        self.assertEqual([event.text for event in events], ["backup"])
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    def test_provider_manager_does_not_replay_after_partial_output(self):
        first = ErrorStreamProvider("first", emit_first=True)
        second = ErrorStreamProvider("second", fail=False)
        manager = ProviderManager([first, second])
        stream = manager.stream_events([], [])
        self.assertEqual(next(stream).text, "partial")
        with self.assertRaises(ProviderUnavailable):
            list(stream)
        self.assertEqual(second.calls, 0)


if __name__ == "__main__":
    unittest.main()
