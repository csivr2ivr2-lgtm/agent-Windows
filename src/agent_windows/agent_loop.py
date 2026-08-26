from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterator, Mapping, Sequence

from .contracts import LLMResponse, MemoryStore, Message, ToolCall
from .optimizer import RequestOptimizer
from .policy import ConfirmationGrant, PolicyEngine
from .router import LLMRouter
from .tools import ToolRegistry


class AgentState(str, Enum):
    RECEIVE = "receive"
    LOAD_CONTEXT = "load_context"
    PLAN = "plan"
    POLICY_CHECK = "policy_check"
    ACT = "act"
    OBSERVE = "observe"
    VERIFY = "verify"
    RECOVER = "recover"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class AgentBudget:
    max_steps: int = 8
    max_tool_calls: int = 12
    max_replans: int = 3

    def __post_init__(self) -> None:
        if self.max_steps < 1 or self.max_tool_calls < 0 or self.max_replans < 0:
            raise ValueError("agent budgets must be non-negative and max_steps must be positive")


@dataclass(frozen=True)
class AgentRunResult:
    text: str
    state: AgentState
    steps: int
    tool_calls: int
    replans: int
    provider: str = "unknown"


class AgentCancelled(RuntimeError):
    pass


class AgentLoop:
    """Bounded plan -> act -> observe -> verify loop built on existing agent contracts."""

    def __init__(
        self,
        router: LLMRouter,
        memory: MemoryStore,
        tools: ToolRegistry,
        *,
        system_prompt: str,
        optimizer: RequestOptimizer | None = None,
        policy_provider: Callable[[], Mapping[str, int]] | None = None,
        policy_engine: PolicyEngine | None = None,
        confirmation_provider: Callable[[ToolCall, str], ConfirmationGrant | None] | None = None,
        tool_planner=None,
        plan_reviewer=None,
    ) -> None:
        self.router = router
        self.memory = memory
        self.tools = tools
        self.system_prompt = system_prompt
        self.optimizer = optimizer or RequestOptimizer()
        self.policy_provider = policy_provider or (lambda: {"context_chars": 12000, "tools": 20})
        self.policy_engine = policy_engine or PolicyEngine()
        self.confirmation_provider = confirmation_provider
        self.tool_planner = tool_planner
        self.plan_reviewer = plan_reviewer

    def run(
        self,
        user_text: str,
        *,
        budget: AgentBudget | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> AgentRunResult:
        budget = budget or AgentBudget()
        cancelled = cancelled or (lambda: False)
        context = self.memory.search(user_text)
        messages = [Message("system", self.system_prompt)]
        if context:
            messages.append(Message("system", "Relevant memory:\n" + "\n".join(context)))
        messages.append(Message("user", user_text))

        steps = tool_calls = replans = 0
        tool_calls, replans, planner_failure = self._apply_tool_planner(
            user_text, messages, budget, tool_calls, replans, cancelled=cancelled
        )
        if planner_failure:
            return AgentRunResult(
                text=planner_failure,
                state=AgentState.FAILED,
                steps=steps,
                tool_calls=tool_calls,
                replans=replans,
                provider="needle",
            )
        last_response = LLMResponse()
        while steps < budget.max_steps:
            if cancelled():
                raise AgentCancelled("agent run cancelled")
            steps += 1
            optimized, schemas = self._optimized(messages)
            last_response = self.router.complete(optimized, schemas)

            if not last_response.tool_calls:
                text = last_response.text.strip()
                if text:
                    self.memory.remember(f"User: {user_text}\nAssistant: {text}")
                return AgentRunResult(
                    text=text,
                    state=AgentState.COMPLETE,
                    steps=steps,
                    tool_calls=tool_calls,
                    replans=replans,
                    provider=last_response.provider,
                )

            messages = list(optimized)
            if last_response.text.strip():
                messages.append(Message("assistant", last_response.text.strip()))

            calls = self._review_calls(last_response.tool_calls)
            for call in calls:
                if cancelled():
                    raise AgentCancelled("agent run cancelled")
                if tool_calls >= budget.max_tool_calls:
                    return AgentRunResult(
                        text="Tool-call budget exhausted before the goal was completed.",
                        state=AgentState.FAILED,
                        steps=steps,
                        tool_calls=tool_calls,
                        replans=replans,
                        provider=last_response.provider,
                    )
                tool_calls += 1
                outcome, failed = self._execute_tool(call)
                messages.append(Message("tool", f"{call.name}: {outcome}"))
                if failed:
                    replans += 1
                    if replans > budget.max_replans:
                        return AgentRunResult(
                            text=f"Tool recovery budget exhausted after {call.name} failed.",
                            state=AgentState.FAILED,
                            steps=steps,
                            tool_calls=tool_calls,
                            replans=replans,
                            provider=last_response.provider,
                        )

        return AgentRunResult(
            text=last_response.text.strip() or "Agent step budget exhausted before completion.",
            state=AgentState.FAILED,
            steps=steps,
            tool_calls=tool_calls,
            replans=replans,
            provider=last_response.provider,
        )

    def stream(
        self,
        user_text: str,
        *,
        budget: AgentBudget | None = None,
        cancel_event=None,
    ) -> Iterator[str]:
        """Stream a bounded tool-aware agent turn for realtime voice.

        Text deltas are forwarded immediately. Tool-call events stay internal, pass through
        the same policy engine as non-streaming turns, and their observations are fed back
        into the next model step before the final spoken answer continues.
        """
        budget = budget or AgentBudget()
        context = self.memory.search(user_text)
        messages = [Message("system", self.system_prompt)]
        if context:
            messages.append(Message("system", "Relevant memory:\n" + "\n".join(context)))
        messages.append(Message("user", user_text))

        steps = tool_calls = replans = 0
        cancelled = lambda: bool(cancel_event is not None and cancel_event.is_set())
        tool_calls, replans, planner_failure = self._apply_tool_planner(
            user_text, messages, budget, tool_calls, replans, cancelled=cancelled
        )
        if planner_failure:
            yield " לא הצלחתי להשלים את תכנון הכלים המקומי בבטחה."
            return
        spoken: list[str] = []
        while steps < budget.max_steps:
            if cancel_event is not None and cancel_event.is_set():
                return
            steps += 1
            optimized, schemas = self._optimized(messages)
            calls: list[ToolCall] = []
            step_text: list[str] = []
            for event in self.router.stream_events(optimized, schemas, cancel_event=cancel_event):
                if cancel_event is not None and cancel_event.is_set():
                    return
                if event.kind == "text" and event.text:
                    step_text.append(event.text)
                    spoken.append(event.text)
                    yield event.text
                elif event.kind == "tool_call" and event.tool_call is not None:
                    calls.append(event.tool_call)

            if not calls:
                answer = "".join(spoken).strip()
                if answer:
                    self.memory.remember(f"User: {user_text}\nAssistant: {answer}")
                return

            calls = list(self._review_calls(calls))
            messages = list(optimized)
            assistant_text = "".join(step_text).strip()
            if assistant_text:
                messages.append(Message("assistant", assistant_text))

            for call in calls:
                if cancel_event is not None and cancel_event.is_set():
                    return
                if tool_calls >= budget.max_tool_calls:
                    yield " הגעתי למגבלת פעולות הכלים במשימה הזאת."
                    return
                tool_calls += 1
                outcome, failed = self._execute_tool(call)
                messages.append(Message("tool", f"{call.name}: {outcome}"))
                if failed:
                    replans += 1
                    if replans > budget.max_replans:
                        yield " לא הצלחתי להשלים את הפעולה אחרי ניסיונות התאוששות."
                        return

        yield " הגעתי למגבלת שלבי הביצוע לפני שהמשימה הושלמה."

    def _review_calls(self, calls: Sequence[ToolCall]) -> tuple[ToolCall, ...]:
        if not self.plan_reviewer or not calls:
            return tuple(calls)
        try:
            review = self.plan_reviewer.review_tool_calls(calls)
            reviewed = getattr(review, "calls", calls)
            return tuple(reviewed)
        except Exception:
            return tuple(calls)

    def _apply_tool_planner(
        self,
        user_text: str,
        messages: list[Message],
        budget: AgentBudget,
        tool_calls: int,
        replans: int,
        *,
        cancelled: Callable[[], bool],
    ) -> tuple[int, int, str | None]:
        if self.tool_planner is None or cancelled():
            return tool_calls, replans, None
        try:
            plan = self.tool_planner.plan(user_text, self.tools.schemas())
        except Exception:
            return tool_calls, replans, None
        if not getattr(plan, "accepted", False):
            return tool_calls, replans, None
        planned_calls = self._review_calls(getattr(plan, "calls", ()))
        for call in planned_calls:
            if cancelled():
                raise AgentCancelled("agent run cancelled")
            if tool_calls >= budget.max_tool_calls:
                return tool_calls, replans, "Tool-call budget exhausted during local tool planning."
            tool_calls += 1
            outcome, failed = self._execute_tool(call)
            messages.append(Message("tool", f"Needle preplan {call.name}: {outcome}"))
            if failed:
                replans += 1
                if replans > budget.max_replans:
                    return tool_calls, replans, f"Tool recovery budget exhausted after {call.name} failed."
        return tool_calls, replans, None

    def _optimized(self, messages: Sequence[Message]) -> tuple[list[Message], list[Mapping[str, object]]]:
        policy = self.policy_provider()
        optimized, schemas = self.optimizer.optimize(
            list(messages),
            self.tools.schemas(),
            max_chars=int(policy["context_chars"]),
            max_tools=int(policy["tools"]),
        )
        if not optimized or optimized[0].role != "system" or self.system_prompt not in optimized[0].content:
            optimized = [Message("system", self.system_prompt), *optimized]
        return optimized, list(schemas)

    def _execute_tool(self, call: ToolCall) -> tuple[object, bool]:
        try:
            tool = self.tools.get(call.name)
        except KeyError as exc:
            return f"ERROR {exc}", True
        decision = self.policy_engine.evaluate(tool, call.arguments)
        if decision.requires_confirmation and self.confirmation_provider:
            grant = self.confirmation_provider(call, decision.action_hash)
            decision = self.policy_engine.evaluate(tool, call.arguments, grant=grant)
        if not decision.allowed:
            prefix = "CONFIRMATION_REQUIRED" if decision.requires_confirmation else "POLICY_DENIED"
            return f"{prefix} risk={decision.risk.name} action={decision.action_hash} reason={decision.reason}", True
        try:
            return self.tools.invoke(call.name, call.arguments), False
        except Exception as exc:
            return f"ERROR {type(exc).__name__}: {exc}", True
