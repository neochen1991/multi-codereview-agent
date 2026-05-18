from app.services.model_prompt_profiles import resolve_model_prompt_profile


def test_minimax_25_uses_short_rule_guided_profile() -> None:
    profile = resolve_model_prompt_profile("minimax-2.5", profile_name="auto")

    assert profile.name == "rule-guided-compact"
    assert profile.max_rules_per_prompt == 8
    assert profile.max_context_chars == 24000
    assert profile.use_two_pass_review is True
    assert profile.require_rule_check_results is True
    assert profile.json_schema_complexity == "simple"
    assert profile.avoid_long_system_prompt is True
    assert profile.preserve_message_roles is True


def test_any_configured_non_minimax_model_uses_standard_rule_guided_profile() -> None:
    profile = resolve_model_prompt_profile("gpt-compatible", profile_name="auto")

    assert profile.name == "rule-guided-standard"
    assert profile.use_two_pass_review is True
    assert profile.require_rule_check_results is True
    assert profile.max_rules_per_prompt == 16
    assert profile.max_context_chars == 64000


def test_empty_model_uses_standard_rule_guided_profile() -> None:
    profile = resolve_model_prompt_profile("", profile_name="auto")

    assert profile.name == "rule-guided-standard"
    assert profile.use_two_pass_review is True
    assert profile.require_rule_check_results is True
    assert profile.allow_legacy_expert_output is False


def test_profile_can_be_forced_for_any_model() -> None:
    compact = resolve_model_prompt_profile("gpt-compatible", profile_name="rule-guided-compact")
    standard = resolve_model_prompt_profile("minimax-2.5", profile_name="rule-guided-standard")
    strict = resolve_model_prompt_profile("any-model", profile_name="strict-json-small-context")
    long_context = resolve_model_prompt_profile("any-model", profile_name="long-context-capable")
    legacy = resolve_model_prompt_profile("minimax-2.5", profile_name="legacy")

    assert compact.name == "rule-guided-compact"
    assert compact.max_rules_per_prompt == 8
    assert standard.name == "rule-guided-standard"
    assert standard.max_rules_per_prompt == 16
    assert strict.name == "strict-json-small-context"
    assert strict.max_context_chars == 16000
    assert strict.allow_legacy_expert_output is False
    assert long_context.name == "long-context-capable"
    assert long_context.max_rules_per_prompt == 24
    assert long_context.max_context_chars == 96000
    assert legacy.name == "legacy"
    assert legacy.avoid_long_system_prompt is False
    assert legacy.allow_legacy_expert_output is True


def test_long_context_models_auto_use_long_context_profile() -> None:
    profile = resolve_model_prompt_profile("gpt-5.5", profile_name="auto")

    assert profile.name == "long-context-capable"
    assert profile.require_rule_check_results is True
    assert profile.allow_legacy_expert_output is False
