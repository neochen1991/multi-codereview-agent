# Minimax Rule-Guided Review Quality Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve review quality for minimax-2.5 by making prompts shorter, rule-driven, context-aware, auditable, and strict about common rules plus expert-bound rules.

**Architecture:** Add a structured rule layer between uploaded specification documents and review prompts. Build a deterministic review rule plan and context packet before calling the model, then use a minimax-specific two-pass prompt profile: high-recall candidate discovery followed by evidence verification. Persist prompt snapshots, rule coverage, context gaps, raw model output, and final findings so quality can be debugged instead of guessed.

**Tech Stack:** FastAPI backend, existing review runner services, existing knowledge rule services, SQLite/Postgres repositories, React frontend, pytest, existing smoke and benchmark scripts.

---

## Problem Statement

Current review quality is weak with minimax-2.5 mainly because the model receives too much loosely structured instruction at once:

- Common standards and expert standards are rendered as long text instead of executable rule cards.
- The prompt asks the model to find issues, follow rules, read context, judge evidence, format JSON, and avoid false positives in one pass.
- Missing associated context is not treated as a hard state; the model may still infer or over-claim.
- Review replay does not expose enough prompt, context, rule coverage, or raw response information for diagnosis.
- If the proxy flattens chat roles into plain text, minimax loses system/user priority separation and follows constraints less reliably.

The fix is not just "rewrite prompt wording". The review pipeline must explicitly provide:

1. Which rules must be checked.
2. Which context is required for each rule.
3. Which context was actually loaded.
4. Which candidates were discovered.
5. Which candidates passed evidence verification.
6. Which rules were passed, violated, not applicable, or blocked by insufficient context.

---

## Success Metrics

Track these metrics per review and in benchmark reports:

- Rule coverage rate: applicable rules with explicit `rule_check_results` / applicable rules.
- Context fulfillment rate: required context items loaded / required context items requested.
- JSON parse success rate for minimax-2.5.
- Candidate recall on benchmark cases.
- Final issue recall on benchmark cases.
- False positive rate on clean benchmark cases.
- `insufficient_context` rate by expert and rule.
- Prompt size by model profile.

Minimum acceptance for the first release:

- minimax-2.5 prompt snapshots are shorter than the current expert prompt for the same review.
- Every applicable common or expert rule has one explicit check result.
- A finding cannot be accepted without `rule_id`, file location, evidence, and verification status.
- Missing required context results in `insufficient_context`, not `passed`.
- The Java DDD benchmark case for bypassing aggregate factory is consistently detected.

---

## Task 1: Add Structured Review Rule Models

**Files:**
- Create: `backend/app/domain/models/review_rule.py`
- Modify: `backend/app/domain/models/__init__.py`
- Test: `backend/tests/domain/test_review_rule_models.py`

**Step 1: Write failing model tests**

Add tests for:

- `ReviewRuleCard` requires `rule_id`, `title`, `scope`, `must_check`, `required_context`, `evidence_required`, and `normalized_issue_type`.
- `ReviewRuleCheckResult.status` only allows `violated`, `passed`, `not_applicable`, `insufficient_context`.
- `ReviewRulePlan` can separate `common_rule_ids`, `expert_rule_ids`, and `skipped_rule_ids`.

Run:

```bash
.venv/bin/python -m pytest backend/tests/domain/test_review_rule_models.py -q
```

Expected: fails because models do not exist.

**Step 2: Implement minimal dataclasses or Pydantic models**

Create:

```python
RuleCheckStatus = Literal[
    "violated",
    "passed",
    "not_applicable",
    "insufficient_context",
]
```

Core models:

- `ReviewRuleCard`
- `ReviewRuleSource`
- `ReviewRulePlan`
- `RequiredContextItem`
- `ReviewRuleCheckResult`
- `CandidateFinding`
- `VerifiedFinding`

Keep fields small and serializable. Do not add persistence yet.

**Step 3: Run tests**

Run:

```bash
.venv/bin/python -m pytest backend/tests/domain/test_review_rule_models.py -q
```

Expected: pass.

**Step 4: Commit**

```bash
git add backend/app/domain/models/review_rule.py backend/app/domain/models/__init__.py backend/tests/domain/test_review_rule_models.py
git commit -m "feat: add structured review rule models"
```

---

## Task 2: Compile Uploaded Expert Markdown into Rule Cards

**Files:**
- Create: `backend/app/services/review_rule_compiler.py`
- Create: `backend/tests/services/test_review_rule_compiler.py`
- Modify: `docs/templates/review-rule-template.md`
- Optional Create: `docs/templates/expert-bound-rule-template.md`

**Step 1: Write failing compiler tests**

Test a standard Markdown block:

```markdown
## RULE: ARCH-JDDD-002

### Title
Application service must not directly construct aggregate roots

### Scope
- language: java
- expert: ddd_architecture

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
```

Expected output:

- `rule_id == "ARCH-JDDD-002"`
- `required_context` includes `aggregate_factory_method`
- missing `False Positive Guards` makes the rule invalid or `needs_review`.

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_rule_compiler.py -q
```

Expected: fails because compiler does not exist.

**Step 2: Implement deterministic Markdown parser first**

Implement:

- `compile_review_rules_from_markdown(markdown: str, source_doc_id: str) -> list[ReviewRuleCard]`
- Section parser for `## RULE: <id>`.
- Required section validation.
- Source metadata: document id, heading, line span.

Do not call LLM in the first version. Standard template compliance should be deterministic.

**Step 3: Add template guidance**

Update `docs/templates/review-rule-template.md` or add `docs/templates/expert-bound-rule-template.md` with required sections:

- Rule id
- Title
- Scope
- Trigger signals
- Must check
- Required context
- Evidence required
- False positive guards
- Severity
- Normalized issue type
- Good example
- Bad example

**Step 4: Run tests**

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_rule_compiler.py -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add backend/app/services/review_rule_compiler.py backend/tests/services/test_review_rule_compiler.py docs/templates/review-rule-template.md docs/templates/expert-bound-rule-template.md
git commit -m "feat: compile expert markdown into review rules"
```

---

## Task 3: Bind Common Rules and Expert Rules into a Review Rule Plan

**Files:**
- Create: `backend/app/services/review_rule_plan_service.py`
- Modify: `backend/app/services/knowledge_rule_index_service.py`
- Modify: `backend/app/services/knowledge_ingestion_service.py`
- Modify: `backend/app/domain/models/expert_profile.py`
- Test: `backend/tests/services/test_review_rule_plan_service.py`
- Test: `backend/tests/services/test_knowledge_rule_index_service.py`

**Step 1: Write failing tests**

Test that:

- Common rules are always included when scope matches language/framework.
- Expert-bound rules are included only for selected or routed experts.
- Rules with unmatched scope are skipped with a reason.
- A review plan records `common_rule_ids`, `expert_rule_ids`, `skipped_rule_ids`, and `required_context_plan`.

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_rule_plan_service.py backend/tests/services/test_knowledge_rule_index_service.py -q
```

Expected: failing tests for missing planner behavior.

**Step 2: Implement rule plan service**

Implement:

- `build_review_rule_plan(review_subject, expert_profile, changed_files, available_rules)`
- Scope matching by language, file path, expert id, and trigger signal.
- Limit per prompt using model profile config, but keep full coverage across batches.

**Step 3: Integrate compiled rules into knowledge ingestion**

When a user uploads or binds an expert MD document:

- Compile rule cards.
- Store valid cards as active rules.
- Store invalid or incomplete cards as `needs_review`.
- Attach valid expert rules to the selected expert profile.

**Step 4: Run tests**

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_rule_plan_service.py backend/tests/services/test_knowledge_rule_index_service.py -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add backend/app/services/review_rule_plan_service.py backend/app/services/knowledge_rule_index_service.py backend/app/services/knowledge_ingestion_service.py backend/app/domain/models/expert_profile.py backend/tests/services/test_review_rule_plan_service.py backend/tests/services/test_knowledge_rule_index_service.py
git commit -m "feat: bind common and expert rules to review plans"
```

---

## Task 4: Build Required Context Packets Before Prompting

**Files:**
- Create: `backend/app/domain/models/review_context.py`
- Create: `backend/app/services/review_context_packet_service.py`
- Modify: `backend/app/services/repository_context_service.py`
- Modify: `backend/app/services/code_graph/context_planner.py`
- Modify: `backend/app/services/review_runner_context_rendering.py`
- Test: `backend/tests/services/test_review_context_packet_service.py`
- Test: `backend/tests/services/test_code_graph_context_planner.py`

**Step 1: Write failing context tests**

Test that a rule requiring:

- `changed_file_full_content`
- `caller_context`
- `callee_context`
- `same_package_files`
- `test_files`

produces a `ContextPacket` with:

- loaded context items
- missing context items
- source paths normalized to `/`
- `context_limited = true` when required items are missing.

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_context_packet_service.py backend/tests/services/test_code_graph_context_planner.py -q
```

Expected: fails.

**Step 2: Implement context packet model and service**

`ContextPacket` should include:

- changed file diff hunks
- changed file full content
- symbol context
- callers
- callees
- sibling files
- related tests
- config files
- missing context list
- retrieval diagnostics

**Step 3: Add Windows path normalization**

Normalize all paths before rendering to prompt:

- Convert `\` to `/`.
- Strip workspace prefixes when rendering to model.
- Preserve absolute paths in backend diagnostics only.

**Step 4: Integrate into review runner**

The review runner must build the context packet before model calls. If required context is missing, the prompt must include that missing state explicitly.

**Step 5: Run tests**

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_context_packet_service.py backend/tests/services/test_code_graph_context_planner.py -q
```

Expected: pass.

**Step 6: Commit**

```bash
git add backend/app/domain/models/review_context.py backend/app/services/review_context_packet_service.py backend/app/services/repository_context_service.py backend/app/services/code_graph/context_planner.py backend/app/services/review_runner_context_rendering.py backend/tests/services/test_review_context_packet_service.py backend/tests/services/test_code_graph_context_planner.py
git commit -m "feat: build rule-required context packets"
```

---

## Task 5: Add Minimax-Specific Prompt Profile

**Files:**
- Create: `backend/app/services/model_prompt_profiles.py`
- Modify: `backend/app/domain/models/runtime_settings.py`
- Modify: `backend/app/services/runtime_settings_service.py`
- Modify: `backend/app/services/review_runner_prompting.py`
- Modify: `backend/app/services/llm_chat_service.py`
- Test: `backend/tests/services/test_model_prompt_profiles.py`
- Test: `backend/tests/services/test_review_runner_prompting.py`

**Step 1: Write failing prompt profile tests**

Test `minimax-2.5` profile:

```json
{
  "max_rules_per_prompt": 8,
  "max_context_chars": 24000,
  "use_two_pass_review": true,
  "require_rule_check_results": true,
  "json_schema_complexity": "simple",
  "avoid_long_system_prompt": true,
  "preserve_message_roles": true
}
```

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_model_prompt_profiles.py backend/tests/services/test_review_runner_prompting.py -q
```

Expected: fails because profile is not implemented.

**Step 2: Implement prompt profile resolution**

Resolve by model name:

- `minimax-2.5`
- `minimax`
- fallback default profile

Profile controls:

- prompt length budget
- rules per batch
- context character budget
- simple JSON schema
- two-pass review
- role preservation requirement

**Step 3: Rewrite minimax prompt rendering**

For minimax, render short sections:

```text
[SYSTEM RULES]
You are a code review expert. Use only DIFF, CONTEXT_PACKET, and RULE_CARDS.
Do not invent missing context. If required context is missing, mark the rule as insufficient_context.

[TASK]
Check every RULE_CARD. Return rule_check_results for every applicable rule.

[RULE_CARDS]
...

[CONTEXT_PACKET]
...

[OUTPUT_JSON]
...
```

Avoid:

- long general review philosophy
- repeated expert instructions
- nested markdown tables
- excessive roleplay
- asking for final report prose in the same response

**Step 4: Keep output schema simple**

Required minimax JSON:

```json
{
  "rule_check_results": [
    {
      "rule_id": "string",
      "status": "violated|passed|not_applicable|insufficient_context",
      "evidence": ["string"],
      "missing_context": ["string"],
      "reason": "string"
    }
  ],
  "candidate_findings": [
    {
      "rule_id": "string",
      "title": "string",
      "file_path": "string",
      "line": 0,
      "evidence": "string",
      "confidence": "high|medium|low"
    }
  ]
}
```

**Step 5: Run tests**

```bash
.venv/bin/python -m pytest backend/tests/services/test_model_prompt_profiles.py backend/tests/services/test_review_runner_prompting.py -q
```

Expected: pass.

**Step 6: Commit**

```bash
git add backend/app/services/model_prompt_profiles.py backend/app/domain/models/runtime_settings.py backend/app/services/runtime_settings_service.py backend/app/services/review_runner_prompting.py backend/app/services/llm_chat_service.py backend/tests/services/test_model_prompt_profiles.py backend/tests/services/test_review_runner_prompting.py
git commit -m "feat: add minimax prompt profile"
```

---

## Task 6: Implement Two-Pass Review for Minimax

**Files:**
- Create: `backend/app/services/review_candidate_verification_service.py`
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/services/review_runner_expert_output.py`
- Modify: `backend/app/services/review_runner_issue_validation.py`
- Modify: `backend/app/services/evidence_verifier_service.py`
- Test: `backend/tests/services/test_review_candidate_verification_service.py`
- Test: `backend/tests/services/test_review_runner.py`

**Step 1: Write failing tests for two-pass behavior**

Test:

- Candidate pass can return low-confidence candidates.
- Verification pass rejects candidates without evidence.
- Verification pass rejects candidates with missing required context.
- Verification pass rejects candidates that match a false positive guard.
- Final findings only include verified candidates.

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_candidate_verification_service.py backend/tests/services/test_review_runner.py -q
```

Expected: fails.

**Step 2: Implement candidate discovery pass**

Prompt goal:

```text
Find possible violations. Prefer high recall. Do not suppress uncertain candidates.
Each candidate must include rule_id, file_path, line, evidence, and confidence.
```

**Step 3: Implement verification pass**

Prompt or deterministic verifier goal:

```text
Accept a candidate only if:
1. It matches a rule.
2. It has concrete code evidence.
3. Required context was available.
4. It does not trigger false_positive_guards.
```

Prefer deterministic checks first. Use LLM verification only when needed.

**Step 4: Integrate with final issue validation**

Final issue must have:

- rule id
- source rule type: common or expert
- code location
- evidence
- verification result
- confidence
- context fulfillment state

**Step 5: Run tests**

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_candidate_verification_service.py backend/tests/services/test_review_runner.py -q
```

Expected: pass.

**Step 6: Commit**

```bash
git add backend/app/services/review_candidate_verification_service.py backend/app/services/review_runner.py backend/app/services/review_runner_expert_output.py backend/app/services/review_runner_issue_validation.py backend/app/services/evidence_verifier_service.py backend/tests/services/test_review_candidate_verification_service.py backend/tests/services/test_review_runner.py
git commit -m "feat: add two-pass minimax review verification"
```

---

## Task 7: Preserve Chat Roles or Add Safe Single-Prompt Fallback

**Files:**
- Modify: `scripts/ngagent-proxy/src/prompt.js`
- Create: `scripts/ngagent-proxy/test/prompt.test.js`
- Modify: `backend/app/services/llm_chat_service.py`
- Test: `backend/tests/services/test_llm_chat_service_roles.py`

**Step 1: Write failing tests**

Test:

- OpenAI-compatible providers receive `messages` with roles preserved.
- Single-prompt fallback clearly renders `[SYSTEM RULES]`, `[USER TASK]`, `[INPUT DATA]`, and `[OUTPUT CONTRACT]`.
- No prior assistant chatter is included in minimax review prompts unless explicitly required.

Run backend tests:

```bash
.venv/bin/python -m pytest backend/tests/services/test_llm_chat_service_roles.py -q
```

Run proxy tests if package scripts exist:

```bash
npm --prefix scripts/ngagent-proxy test
```

Expected: fails until role handling is implemented.

**Step 2: Modify proxy prompt handling**

Do not flatten role messages when provider supports chat messages.

If provider only accepts a single prompt, render:

```text
[SYSTEM RULES]
...

[USER TASK]
...

[INPUT DATA]
...

[OUTPUT CONTRACT]
...
```

**Step 3: Run tests**

```bash
.venv/bin/python -m pytest backend/tests/services/test_llm_chat_service_roles.py -q
npm --prefix scripts/ngagent-proxy test
```

Expected: pass, or document if proxy has no test script and use a direct node test file instead.

**Step 4: Commit**

```bash
git add scripts/ngagent-proxy/src/prompt.js scripts/ngagent-proxy/test/prompt.test.js backend/app/services/llm_chat_service.py backend/tests/services/test_llm_chat_service_roles.py
git commit -m "fix: preserve minimax prompt role boundaries"
```

---

## Task 8: Persist Prompt, Rule Coverage, Context Gaps, and Raw Model Output

**Files:**
- Modify: `backend/app/services/review_service_projection.py`
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/services/review_runner_expert_output.py`
- Modify: `backend/app/domain/models/message.py`
- Modify: `frontend/src/components/review/ReplayConsolePanel.tsx`
- Modify: `frontend/src/components/review/QualityGovernancePanel.tsx`
- Modify: `frontend/src/services/apiTypes.ts`
- Test: `backend/tests/api/test_replay_api.py`
- Test: `backend/tests/services/test_review_service_projection.py`

**Step 1: Write failing replay tests**

Test replay messages include:

- `prompt_snapshot_summary`
- `model_raw_response_excerpt`
- `rule_check_results`
- `candidate_findings`
- `verification_results`
- `context_packet_summary`
- `context_gaps`

Run:

```bash
.venv/bin/python -m pytest backend/tests/api/test_replay_api.py backend/tests/services/test_review_service_projection.py -q
```

Expected: fails because replay currently hides or drops too much content.

**Step 2: Persist review diagnostics**

Store diagnostics as metadata or artifacts:

- rule plan
- prompt profile
- prompt snapshot hash and excerpt
- context packet summary
- raw response excerpt
- parse errors
- rule coverage
- final verification state

Avoid storing secrets or full private repository content in frontend-visible messages. Provide full artifact only behind backend access if needed.

**Step 3: Update frontend panels**

`QualityGovernancePanel` should show:

- common rules checked
- expert rules checked
- violated rules
- passed rules
- insufficient context rules
- skipped rules

`ReplayConsolePanel` should show:

- model profile
- prompt phase
- prompt snapshot summary
- raw response parse status

**Step 4: Run tests**

```bash
.venv/bin/python -m pytest backend/tests/api/test_replay_api.py backend/tests/services/test_review_service_projection.py -q
npm --prefix frontend run typecheck
```

Expected: pass.

**Step 5: Commit**

```bash
git add backend/app/services/review_service_projection.py backend/app/services/review_runner.py backend/app/services/review_runner_expert_output.py backend/app/domain/models/message.py frontend/src/components/review/ReplayConsolePanel.tsx frontend/src/components/review/QualityGovernancePanel.tsx frontend/src/services/apiTypes.ts backend/tests/api/test_replay_api.py backend/tests/services/test_review_service_projection.py
git commit -m "feat: expose rule and prompt diagnostics"
```

---

## Task 9: Add Minimax Quality Benchmarks

**Files:**
- Modify: `backend/tests/fixtures/java_cases/cases.json`
- Create: `backend/tests/fixtures/review_eval_cases/minimax-rule-guided-review.json`
- Modify: `backend/tests/services/test_java_review_benchmarks.py`
- Modify: `backend/tests/services/test_review_quality_eval.py`
- Modify: `scripts/smoke_review.py`
- Optional Modify: `scripts/bench_java_review_cases.py`

**Step 1: Add benchmark cases**

Include cases for:

- DDD aggregate factory bypass.
- Missing transaction boundary.
- Unsafe SQL construction.
- Null handling regression.
- Resource leak.
- Permission bypass.
- Clean diff with no issue.

Each case should define:

- changed files
- required common rules
- required expert rules
- expected issue types
- expected context requirements
- allowed false positives count

**Step 2: Add benchmark assertions**

For minimax profile:

- `rule_check_results` exists.
- Expected `normalized_issue_type` is found.
- No issue is accepted without rule id.
- Context gaps are explicit.

**Step 3: Run targeted benchmark**

```bash
.venv/bin/python -m pytest backend/tests/services/test_java_review_benchmarks.py backend/tests/services/test_review_quality_eval.py -q
```

Expected: pass.

**Step 4: Run smoke review**

```bash
.venv/bin/python scripts/smoke_review.py
```

Expected:

- review completes
- replay includes non-empty prompt/model diagnostics
- DDD aggregate factory bypass issue is detected
- rule coverage is visible

**Step 5: Commit**

```bash
git add backend/tests/fixtures/java_cases/cases.json backend/tests/fixtures/review_eval_cases/minimax-rule-guided-review.json backend/tests/services/test_java_review_benchmarks.py backend/tests/services/test_review_quality_eval.py scripts/smoke_review.py scripts/bench_java_review_cases.py
git commit -m "test: add minimax rule-guided review benchmarks"
```

---

## Task 10: Add UI Support for Expert Rule Upload and Validation

**Files:**
- Modify: `frontend/src/pages/Experts/index.tsx`
- Modify: `frontend/src/pages/Knowledge/index.tsx`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/services/apiTypes.ts`
- Modify: `backend/app/api/routes/experts.py`
- Modify: `backend/app/api/routes/knowledge.py`
- Test: `backend/tests/api/test_expert_management_api.py`
- Test: `backend/tests/api/test_knowledge_write_api.py`

**Step 1: Write failing API tests**

Test:

- Uploading standard expert Markdown returns compiled rules.
- Invalid Markdown returns validation errors.
- Valid rules can be bound to an expert.
- Expert detail API returns bound common and expert rule summaries.

Run:

```bash
.venv/bin/python -m pytest backend/tests/api/test_expert_management_api.py backend/tests/api/test_knowledge_write_api.py -q
```

Expected: fails until APIs are implemented.

**Step 2: Implement backend endpoints**

Add or extend endpoints for:

- preview compiled rules from Markdown
- confirm rule import
- bind rules to expert
- list expert-bound rules
- mark rule active/inactive

**Step 3: Implement frontend flow**

Expert page should support:

- Upload or paste Markdown.
- Preview parsed rule cards.
- Show validation errors.
- Confirm binding.
- Show active bound rules.

Use compact operational UI. Avoid long explanatory prose inside the app.

**Step 4: Run tests and typecheck**

```bash
.venv/bin/python -m pytest backend/tests/api/test_expert_management_api.py backend/tests/api/test_knowledge_write_api.py -q
npm --prefix frontend run typecheck
```

Expected: pass.

**Step 5: Commit**

```bash
git add frontend/src/pages/Experts/index.tsx frontend/src/pages/Knowledge/index.tsx frontend/src/services/api.ts frontend/src/services/apiTypes.ts backend/app/api/routes/experts.py backend/app/api/routes/knowledge.py backend/tests/api/test_expert_management_api.py backend/tests/api/test_knowledge_write_api.py
git commit -m "feat: support expert rule upload and binding"
```

---

## Task 11: Add Windows Preflight and Degraded Context Reporting

**Files:**
- Create: `backend/app/services/review_environment_preflight_service.py`
- Modify: `backend/app/services/platform_adapter.py`
- Modify: `backend/app/services/review_runner.py`
- Modify: `backend/app/services/review_service_projection.py`
- Test: `backend/tests/services/test_review_environment_preflight_service.py`
- Test: `backend/tests/services/test_platform_adapter.py`

**Step 1: Write failing preflight tests**

Test Windows-like paths:

- `C:\repo\src\Main.java` renders as `src/Main.java`.
- Git diff paths map to workspace files.
- Missing code graph is reported as degraded context.
- Missing file content marks required context as missing.

Run:

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_environment_preflight_service.py backend/tests/services/test_platform_adapter.py -q
```

Expected: fails.

**Step 2: Implement preflight service**

Preflight checks:

- Git available.
- Workspace exists.
- Diff files can be resolved.
- Code graph available or fallback enabled.
- Path normalization works.
- Temp directory writable.
- Model profile resolved.

**Step 3: Attach preflight result to review diagnostics**

Surface:

- `environment_status`
- `degraded_context_reasons`
- `path_resolution_failures`

**Step 4: Run tests**

```bash
.venv/bin/python -m pytest backend/tests/services/test_review_environment_preflight_service.py backend/tests/services/test_platform_adapter.py -q
```

Expected: pass.

**Step 5: Commit**

```bash
git add backend/app/services/review_environment_preflight_service.py backend/app/services/platform_adapter.py backend/app/services/review_runner.py backend/app/services/review_service_projection.py backend/tests/services/test_review_environment_preflight_service.py backend/tests/services/test_platform_adapter.py
git commit -m "feat: add Windows review preflight diagnostics"
```

---

## Final Verification

Run backend targeted suite:

```bash
.venv/bin/python -m pytest \
  backend/tests/domain/test_review_rule_models.py \
  backend/tests/services/test_review_rule_compiler.py \
  backend/tests/services/test_review_rule_plan_service.py \
  backend/tests/services/test_review_context_packet_service.py \
  backend/tests/services/test_model_prompt_profiles.py \
  backend/tests/services/test_review_runner_prompting.py \
  backend/tests/services/test_review_candidate_verification_service.py \
  backend/tests/services/test_review_runner.py \
  backend/tests/api/test_replay_api.py \
  backend/tests/api/test_expert_management_api.py \
  backend/tests/api/test_knowledge_write_api.py \
  -q
```

Run frontend typecheck:

```bash
npm --prefix frontend run typecheck
```

Run smoke review:

```bash
.venv/bin/python scripts/smoke_review.py
```

Expected smoke output:

- review completed
- minimax profile resolved when configured
- prompt diagnostics present
- rule coverage present
- context gaps explicit
- expected DDD issue detected

---

## Rollout Plan

1. Ship structured rule models and Markdown compiler first.
2. Enable expert rule upload preview without affecting current reviews.
3. Enable rule plan generation in shadow mode and compare with current review output.
4. Enable minimax prompt profile for selected users or local config only.
5. Enable two-pass minimax review after benchmark passes.
6. Turn on strict rule coverage gating by default.
7. Add Windows preflight warnings to the UI.

---

## Non-Goals

- Do not build a full natural-language policy engine in the first version.
- Do not require all legacy expert documents to be rewritten before rollout.
- Do not make minimax use the same large prompt as stronger long-context models.
- Do not accept a finding only because the model says it is important.

---

## Key Design Decisions

- Treat rules as first-class data, not prompt prose.
- Treat context as a required input with explicit missing states.
- Treat minimax as a model that needs short, direct, structured prompts.
- Treat recall and verification as separate phases.
- Treat replay diagnostics as part of quality, not as debug leftovers.

