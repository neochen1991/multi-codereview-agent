from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field


class ExpertProfile(BaseModel):
    """定义专家 Agent 的职责、能力绑定和模型配置。"""

    expert_id: str
    name: str
    name_zh: str
    role: str
    enabled: bool = True
    focus_areas: list[str] = Field(default_factory=list)
    activation_hints: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    preferred_artifacts: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    tool_bindings: list[str] = Field(default_factory=list)
    mcp_tools: list[str] = Field(default_factory=list)
    runtime_tool_bindings: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("runtime_tool_bindings", "skill_bindings"),
    )
    agent_bindings: list[str] = Field(default_factory=list)
    max_tool_calls: int = 4
    max_debate_rounds: int = 2
    custom: bool = False
    provider: str | None = None
    api_base_url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    model: str | None = None
    system_prompt: str = ""
    review_spec: str = ""
