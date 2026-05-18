from app.services.review_rule_compiler import compile_review_rules_from_markdown


STANDARD_RULE_MARKDOWN = """# Java DDD Rules

## RULE: ARCH-JDDD-002

### Title
Application service must not directly construct aggregate roots

### Scope
- language: java
- expert: ddd_architecture
- layer: application-service

### Trigger Signals
- `new Course(`
- changed file path contains `/application/`

### Must Check
- Check whether the changed application service directly constructs the aggregate root.
- Check whether a factory method exists and is bypassed.

### Required Context
- changed_file_full_content
- aggregate_root_definition
- aggregate_factory_method
- domain_event_publication

### Evidence Required
- Direct construction line.
- Factory method or aggregate creation method.
- Skipped invariant or event logic.

### False Positive Guards
- If the constructor is the documented factory, do not report.
- If the constructed class is not an aggregate root, do not report.

### Severity
major

### Normalized Issue Type
aggregate_factory_bypassed
"""


def test_compile_standard_markdown_rule_to_rule_card() -> None:
    rules = compile_review_rules_from_markdown(
        STANDARD_RULE_MARKDOWN,
        source_doc_id="doc_ddd",
        expert_id="ddd_architecture",
        source_path="ddd.md",
    )

    assert len(rules) == 1
    rule = rules[0]
    assert rule.rule_id == "ARCH-JDDD-002"
    assert rule.title == "Application service must not directly construct aggregate roots"
    assert rule.expert_id == "ddd_architecture"
    assert rule.scope == ["language: java", "expert: ddd_architecture", "layer: application-service"]
    assert "`new Course(`" in rule.trigger_patterns
    assert "aggregate_factory_method" in rule.required_context
    assert "Factory method or aggregate creation method." in rule.evidence_required
    assert rule.severity_default == "major"
    assert rule.normalized_issue_type == "aggregate_factory_bypassed"
    assert rule.status == "active"
    assert rule.source.doc_id == "doc_ddd"
    assert rule.source.source_path == "ddd.md"
    assert rule.source.line_start == 3
    assert rule.source.line_end > rule.source.line_start


def test_missing_false_positive_guards_marks_rule_as_needs_review() -> None:
    markdown = STANDARD_RULE_MARKDOWN.replace(
        "\n### False Positive Guards\n- If the constructor is the documented factory, do not report.\n- If the constructed class is not an aggregate root, do not report.\n",
        "\n",
    )

    rules = compile_review_rules_from_markdown(
        markdown,
        source_doc_id="doc_ddd",
        expert_id="ddd_architecture",
    )

    assert len(rules) == 1
    assert rules[0].status == "needs_review"
    assert rules[0].false_positive_guards == []


def test_invalid_rule_missing_required_context_is_skipped_with_error() -> None:
    markdown = STANDARD_RULE_MARKDOWN.replace(
        "\n### Required Context\n- changed_file_full_content\n- aggregate_root_definition\n- aggregate_factory_method\n- domain_event_publication\n",
        "\n",
    )

    rules = compile_review_rules_from_markdown(
        markdown,
        source_doc_id="doc_ddd",
        expert_id="ddd_architecture",
    )

    assert rules == []


def test_compiler_ignores_rule_headings_inside_code_blocks() -> None:
    markdown = """```markdown
## RULE: FAKE-001
```

## RULE: REAL-001

### Title
Real rule

### Scope
- language: java

### Must Check
- Check real code.

### Required Context
- changed_file_full_content

### Evidence Required
- Real evidence.

### Severity
major

### Normalized Issue Type
real_rule
"""

    rules = compile_review_rules_from_markdown(markdown, source_doc_id="doc_real")

    assert [rule.rule_id for rule in rules] == ["REAL-001"]
