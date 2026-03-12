from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

END = "__end__"


@dataclass
class CompiledGraph:
    entry_point: str
    nodes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]]
    edges: dict[str, str]

    def invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        current = self.entry_point
        payload = dict(state)
        while current != END:
            payload = self.nodes[current](payload)
            current = self.edges.get(current, END)
        return payload


class StateGraph:
    def __init__(self, _state_type: object) -> None:
        self._nodes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._edges: dict[str, str] = {}
        self._entry_point = ""

    def add_node(self, name: str, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._nodes[name] = handler

    def add_edge(self, start: str, end: str) -> None:
        self._edges[start] = end

    def set_entry_point(self, name: str) -> None:
        self._entry_point = name

    def compile(self) -> CompiledGraph:
        return CompiledGraph(
            entry_point=self._entry_point,
            nodes=self._nodes,
            edges=self._edges,
        )
