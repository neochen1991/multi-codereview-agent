from __future__ import annotations

from pydantic import BaseModel, Field


class ReviewSkillProfile(BaseModel):
    """描述一个可插拔审核 skill 的结构化元信息。"""

    skill_id: str
    name: str
    description: str = ""
    bound_experts: list[str] = Field(default_factory=list)
    applicable_experts: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_doc_types: list[str] = Field(default_factory=list)
    activation_hints: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    allowed_modes: list[str] = Field(default_factory=lambda: ["standard", "light"])
    output_contract: dict[str, object] = Field(default_factory=dict)
    prompt_body: str = ""
    skill_path: str = ""
