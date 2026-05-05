from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CodeGraphNode:
    kind: str
    name: str
    qualified_name: str
    language: str
    file_path: str
    line_start: int = 1
    line_end: int = 1
    parent_qualified_name: str = ""
    signature: str = ""
    return_type: str = ""
    modifiers: list[str] = field(default_factory=list)
    is_test: bool = False
    snippet: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeGraphEdge:
    kind: str
    source_qualified_name: str
    target_qualified_name: str
    file_path: str
    line_number: int = 1
    confidence: float = 1.0
    confidence_tier: str = "extracted"
    metadata: dict[str, Any] = field(default_factory=dict)
