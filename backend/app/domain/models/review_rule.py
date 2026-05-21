from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


RuleCheckStatus = Literal["violated", "passed", "not_applicable", "insufficient_context"]
RuleCardStatus = Literal["active", "draft", "needs_review", "disabled"]
FindingConfidence = Literal["high", "medium", "low"]
VerificationStatus = Literal["accepted", "rejected", "needs_context"]


def _compact_list(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


class ReviewRuleSource(BaseModel):
    """Trace a structured rule back to the uploaded or built-in specification."""

    doc_id: str = ""
    source_path: str = ""
    section_title: str = ""
    line_start: int = 1
    line_end: int = 1


class ReviewRuleCard(BaseModel):
    """Executable review rule used by prompts and verification."""

    rule_id: str
    title: str
    scope: list[str] = Field(default_factory=list)
    trigger_patterns: list[str] = Field(default_factory=list)
    must_check: list[str]
    required_context: list[str]
    evidence_required: list[str]
    false_positive_guards: list[str] = Field(default_factory=list)
    severity_default: str = "major"
    normalized_issue_type: str
    expert_id: str = ""
    source: ReviewRuleSource = Field(default_factory=ReviewRuleSource)
    status: RuleCardStatus = "active"

    @model_validator(mode="after")
    def validate_executable_rule(self) -> "ReviewRuleCard":
        self.rule_id = str(self.rule_id or "").strip()
        self.title = str(self.title or "").strip()
        self.normalized_issue_type = str(self.normalized_issue_type or "").strip()
        self.severity_default = str(self.severity_default or "major").strip() or "major"
        self.expert_id = str(self.expert_id or "").strip()
        self.scope = _compact_list(self.scope)
        self.trigger_patterns = _compact_list(self.trigger_patterns)
        self.must_check = _compact_list(self.must_check)
        self.required_context = _compact_list(self.required_context)
        self.evidence_required = _compact_list(self.evidence_required)
        self.false_positive_guards = _compact_list(self.false_positive_guards)
        missing = []
        if not self.rule_id:
            missing.append("rule_id")
        if not self.title:
            missing.append("title")
        if not self.scope:
            missing.append("scope")
        if not self.must_check:
            missing.append("must_check")
        if not self.required_context:
            missing.append("required_context")
        if not self.evidence_required:
            missing.append("evidence_required")
        if not self.normalized_issue_type:
            missing.append("normalized_issue_type")
        if missing:
            raise ValueError(f"ReviewRuleCard missing required fields: {', '.join(missing)}")
        return self


class ReviewRulePlan(BaseModel):
    """Rules selected for a review and the context they require."""

    review_id: str = ""
    expert_id: str = ""
    common_rule_ids: list[str] = Field(default_factory=list)
    expert_rule_ids: list[str] = Field(default_factory=list)
    skipped_rule_ids: list[str] = Field(default_factory=list)
    skip_reasons: dict[str, str] = Field(default_factory=dict)
    required_context_plan: list[str] = Field(default_factory=list)

    @property
    def applicable_rule_ids(self) -> list[str]:
        return _compact_list([*self.common_rule_ids, *self.expert_rule_ids])


class RequiredContextItem(BaseModel):
    """One context item requested by a rule and its retrieval state."""

    key: str
    rule_ids: list[str] = Field(default_factory=list)
    status: Literal["loaded", "missing", "partial"] = "missing"
    source_paths: list[str] = Field(default_factory=list)
    reason: str = ""


class ReviewRuleCheckResult(BaseModel):
    """Per-rule result that proves the rule was checked."""

    rule_id: str
    status: RuleCheckStatus
    evidence: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {
            "violated",
            "violation",
            "direct_defect",
            "direct_code_issue",
            "risk",
            "risk_hypothesis",
            "issue",
            "problem",
            "hit",
            "matched",
            "failed",
            "fail",
        }:
            return "violated"
        if normalized in {"passed", "pass", "ok", "clean", "no_issue", "no_problem"}:
            return "passed"
        if normalized in {"not_applicable", "notapplicable", "n/a", "na", "not_apply"}:
            return "not_applicable"
        if normalized in {
            "insufficient_context",
            "insufficient",
            "needs_context",
            "need_context",
            "unknown",
            "uncertain",
        }:
            return "insufficient_context"
        return normalized

    @model_validator(mode="after")
    def normalize_result(self) -> "ReviewRuleCheckResult":
        self.rule_id = str(self.rule_id or "").strip()
        self.evidence = _compact_list(self.evidence)
        self.missing_context = _compact_list(self.missing_context)
        self.reason = str(self.reason or "").strip()
        if not self.rule_id:
            raise ValueError("ReviewRuleCheckResult requires rule_id")
        return self


class CandidateFinding(BaseModel):
    """High-recall candidate emitted before evidence verification."""

    rule_id: str
    title: str
    file_path: str
    line: int
    evidence: str
    confidence: FindingConfidence = "medium"

    @model_validator(mode="after")
    def validate_candidate(self) -> "CandidateFinding":
        self.rule_id = str(self.rule_id or "").strip()
        self.title = str(self.title or "").strip()
        self.file_path = str(self.file_path or "").strip().replace("\\", "/")
        self.evidence = str(self.evidence or "").strip()
        if not self.rule_id:
            raise ValueError("CandidateFinding requires rule_id")
        if not self.title:
            raise ValueError("CandidateFinding requires title")
        if not self.file_path:
            raise ValueError("CandidateFinding requires file_path")
        if self.line <= 0:
            raise ValueError("CandidateFinding line must be positive")
        if not self.evidence:
            raise ValueError("CandidateFinding requires evidence")
        return self


class VerifiedFinding(BaseModel):
    """Verification result for a candidate finding."""

    candidate: CandidateFinding
    status: VerificationStatus
    reasons: list[str] = Field(default_factory=list)
    matched_false_positive_guards: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
