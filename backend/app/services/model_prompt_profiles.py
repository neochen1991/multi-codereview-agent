from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPromptProfile:
    name: str
    max_rules_per_prompt: int
    max_context_chars: int
    use_two_pass_review: bool
    require_rule_check_results: bool
    json_schema_complexity: str
    avoid_long_system_prompt: bool
    preserve_message_roles: bool
    allow_legacy_expert_output: bool


LEGACY_PROMPT_PROFILE = ModelPromptProfile(
    name="legacy",
    max_rules_per_prompt=20,
    max_context_chars=64000,
    use_two_pass_review=False,
    require_rule_check_results=False,
    json_schema_complexity="full",
    avoid_long_system_prompt=False,
    preserve_message_roles=True,
    allow_legacy_expert_output=True,
)

STRICT_JSON_SMALL_CONTEXT_PROFILE = ModelPromptProfile(
    name="strict-json-small-context",
    max_rules_per_prompt=6,
    max_context_chars=16000,
    use_two_pass_review=True,
    require_rule_check_results=True,
    json_schema_complexity="simple",
    avoid_long_system_prompt=True,
    preserve_message_roles=True,
    allow_legacy_expert_output=False,
)

STANDARD_RULE_GUIDED_PROMPT_PROFILE = ModelPromptProfile(
    name="rule-guided-standard",
    max_rules_per_prompt=16,
    max_context_chars=64000,
    use_two_pass_review=True,
    require_rule_check_results=True,
    json_schema_complexity="simple",
    avoid_long_system_prompt=True,
    preserve_message_roles=True,
    allow_legacy_expert_output=False,
)

COMPACT_RULE_GUIDED_PROMPT_PROFILE = ModelPromptProfile(
    name="rule-guided-compact",
    max_rules_per_prompt=8,
    max_context_chars=24000,
    use_two_pass_review=True,
    require_rule_check_results=True,
    json_schema_complexity="simple",
    avoid_long_system_prompt=True,
    preserve_message_roles=True,
    allow_legacy_expert_output=False,
)

LONG_CONTEXT_CAPABLE_PROFILE = ModelPromptProfile(
    name="long-context-capable",
    max_rules_per_prompt=24,
    max_context_chars=96000,
    use_two_pass_review=True,
    require_rule_check_results=True,
    json_schema_complexity="simple",
    avoid_long_system_prompt=True,
    preserve_message_roles=True,
    allow_legacy_expert_output=False,
)


PROMPT_PROFILES = {
    "legacy": LEGACY_PROMPT_PROFILE,
    "strict-json-small-context": STRICT_JSON_SMALL_CONTEXT_PROFILE,
    "rule-guided-standard": STANDARD_RULE_GUIDED_PROMPT_PROFILE,
    "rule-guided-compact": COMPACT_RULE_GUIDED_PROMPT_PROFILE,
    "long-context-capable": LONG_CONTEXT_CAPABLE_PROFILE,
}


def resolve_model_prompt_profile(
    model_name: str | None,
    *,
    profile_name: str | None = "auto",
) -> ModelPromptProfile:
    requested = str(profile_name or "auto").strip().lower()
    if requested and requested != "auto":
        return PROMPT_PROFILES.get(requested, STANDARD_RULE_GUIDED_PROMPT_PROFILE)
    normalized = str(model_name or "").strip().lower()
    if not normalized:
        return STANDARD_RULE_GUIDED_PROMPT_PROFILE
    if "minimax" in normalized:
        return COMPACT_RULE_GUIDED_PROMPT_PROFILE
    if any(token in normalized for token in ("gpt-5", "claude", "gemini", "qwen-long", "long")):
        return LONG_CONTEXT_CAPABLE_PROFILE
    return STANDARD_RULE_GUIDED_PROMPT_PROFILE
