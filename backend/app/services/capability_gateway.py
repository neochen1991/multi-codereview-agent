from __future__ import annotations

from collections.abc import Callable
from typing import Any


class CapabilityGateway:
    def __init__(self) -> None:
        self._tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._bindings: dict[str, tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]] = {}

    def register_tool(self, name: str, tool: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._tools[name] = tool
        self._bindings[name] = ("tool", tool)

    def register(
        self, binding_id: str, capability_type: str, handler: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        self._bindings[binding_id] = (capability_type, handler)
        if capability_type == "tool":
            self._tools[binding_id] = handler

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def invoke(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools:
            raise KeyError(f"tool not allowed: {name}")
        return self._tools[name](payload)

    def invoke_binding(self, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if binding_id not in self._bindings:
            raise KeyError(f"binding not allowed: {binding_id}")
        _capability_type, handler = self._bindings[binding_id]
        return handler(payload)
