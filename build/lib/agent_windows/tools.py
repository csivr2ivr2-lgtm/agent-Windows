from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import Tool


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool] = ()) -> None:
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        self._tools = {tool.name: tool for tool in tools}

    def schemas(self) -> list[Mapping[str, Any]]:
        return [
            {"name": tool.name, "description": tool.description, "parameters": tool.schema}
            for tool in self._tools.values()
        ]

    def invoke(self, name: str, arguments: Mapping[str, Any]) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name].invoke(arguments)
