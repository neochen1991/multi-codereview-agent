from __future__ import annotations

from collections.abc import Callable
from typing import Any


class CapabilityGateway:
    """维护工具绑定与运行时调用的最小注册表。"""

    def __init__(self) -> None:
        """初始化工具和通用绑定索引。"""

        self._tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._bindings: dict[str, tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]] = {}

    def register_tool(self, name: str, tool: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        """注册一个仅以 tool 语义使用的处理器。"""

        self._tools[name] = tool
        self._bindings[name] = ("tool", tool)

    def register(
        self, binding_id: str, capability_type: str, handler: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        """注册一个通用能力绑定，兼容 tool 和其他扩展类型。"""

        self._bindings[binding_id] = (capability_type, handler)
        if capability_type == "tool":
            self._tools[binding_id] = handler

    def has_tool(self, name: str) -> bool:
        """判断某个工具是否已经注册。"""

        return name in self._tools

    def invoke(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """按工具名调用处理器。"""

        if name not in self._tools:
            raise KeyError(f"tool not allowed: {name}")
        return self._tools[name](payload)

    def invoke_binding(self, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """按通用绑定 ID 调用处理器。"""

        if binding_id not in self._bindings:
            raise KeyError(f"binding not allowed: {binding_id}")
        _capability_type, handler = self._bindings[binding_id]
        return handler(payload)
