from __future__ import annotations

from pydantic import BaseModel, Field


class ReviewToolPlugin(BaseModel):
    """描述一个可插拔运行时 tool 的元数据。"""

    tool_id: str
    name: str
    description: str = ""
    runtime: str = "python"
    entry: str = "run.py"
    timeout_seconds: int = 60
    allowed_experts: list[str] = Field(default_factory=list)
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)
    tool_path: str = ""
