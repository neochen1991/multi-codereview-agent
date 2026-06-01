from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewTask


class ConfidenceSummary(BaseModel):
    """聚合报告页需要展示的关键信心度统计指标。"""

    high_confidence_count: int = 0
    debated_issue_count: int = 0
    needs_human_count: int = 0
    verified_issue_count: int = 0
    direct_defect_count: int = 0
    risk_hypothesis_count: int = 0
    test_gap_count: int = 0
    design_concern_count: int = 0
    llm_judged_issue_count: int = 0
    llm_judge_accepted_count: int = 0
    llm_judge_needs_verification_count: int = 0
    llm_judge_needs_human_count: int = 0
    llm_judge_rejected_count: int = 0
    quality_filtered_issue_count: int = 0
    evidence_chain_issue_count: int = 0
    evidence_chain_coverage: float = 0.0
    policy_comment_budget_filtered_count: int = 0
    review_policy_excluded_file_count: int = 0
    review_policy_reviewable_file_count: int = 0
    review_policy_path_rule_count: int = 0
    review_policy_required_expert_count: int = 0
    quality_gate_passed: bool = True
    quality_gate_missing_count: int = 0
    security_expert_activated: bool = True
    business_expert_activated: bool = True
    expert_activation_missing_count: int = 0
    finding_issue_family_mismatch_count: int = 0
    todo_anchor_failure_count: int = 0
    cross_anchor_duplicate_text_count: int = 0
    fallback_text_failure_count: int = 0


class LlmUsageSummary(BaseModel):
    """审核任务内的大模型调用与 token 汇总。"""

    total_calls: int = 0
    successful_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class IssueFilterDecision(BaseModel):
    """结果页使用的轻量治理/阈值过滤决策。"""

    topic: str = ""
    rule_code: str = ""
    rule_label: str = ""
    reason: str = ""
    severity: str = ""
    finding_ids: list[str] = Field(default_factory=list)
    finding_titles: list[str] = Field(default_factory=list)
    expert_ids: list[str] = Field(default_factory=list)


class ImpactSymbol(BaseModel):
    """一次变更中被影响的代码符号。"""

    file_path: str = ""
    symbol: str = ""
    kind: str = ""
    container: str = ""
    line_start: int = 0


class ImpactFile(BaseModel):
    """关联影响分析识别出的受影响文件。"""

    file_path: str = ""
    relationship: str = ""
    reason: str = ""
    risk_level: str = "low"


class ImpactPath(BaseModel):
    """从变更点到受影响对象的调用/依赖路径。"""

    source: str = ""
    target: str = ""
    path: list[str] = Field(default_factory=list)
    depth: int = 0
    risk: str = ""
    confidence_label: str = "candidate"
    confirmation_reason: str = ""


class ImpactGraphNode(BaseModel):
    """关联影响图中的节点。"""

    node_id: str = ""
    label: str = ""
    kind: str = ""
    file_path: str = ""
    role: str = ""
    risk: str = ""


class ImpactGraphEdge(BaseModel):
    """关联影响图中的边。"""

    source: str = ""
    target: str = ""
    relationship: str = ""
    confidence: float = 0.0


class ImpactGraph(BaseModel):
    """关联影响图。"""

    nodes: list[ImpactGraphNode] = Field(default_factory=list)
    edges: list[ImpactGraphEdge] = Field(default_factory=list)


class TestScopeRecommendation(BaseModel):
    """关联影响报告给出的测试范围建议。"""

    scope: str = ""
    reason: str = ""
    paths: list[str] = Field(default_factory=list)
    priority: str = "medium"


class ImpactIssueLink(BaseModel):
    """关联影响分析结果与代码检视 issue 的交叉引用。"""

    issue_id: str = ""
    issue_title: str = ""
    issue_file_path: str = ""
    impact_target: str = ""
    relationship: str = ""
    reason: str = ""


class ImpactReport(BaseModel):
    """面向每个 MR 输出的关联影响报告。

    仅在 GitNexus 图谱和 MCP 分析成功时生成，用于展示变更影响范围、
    关键路径和建议测试范围。
    """

    graph_status: str = "missing"
    graph_indexed_at: str = ""
    graph_commit: str = ""
    fact_source: str = "gitnexus_mcp"
    analysis_workflow: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    changed_symbols: list[ImpactSymbol] = Field(default_factory=list)
    impacted_files: list[ImpactFile] = Field(default_factory=list)
    impacted_modules: list[str] = Field(default_factory=list)
    impact_paths: list[ImpactPath] = Field(default_factory=list)
    impact_graph: ImpactGraph = Field(default_factory=ImpactGraph)
    external_entrypoints: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    recommended_test_scope: list[TestScopeRecommendation] = Field(default_factory=list)
    must_run_tests: list[str] = Field(default_factory=list)
    queried_targets: list[str] = Field(default_factory=list)
    successful_context_targets: list[str] = Field(default_factory=list)
    successful_impact_targets: list[str] = Field(default_factory=list)
    dynamic_targets: list[str] = Field(default_factory=list)
    skipped_invalid_targets: list[str] = Field(default_factory=list)
    skipped_missing_context_targets: list[str] = Field(default_factory=list)
    skipped_missing_impact_targets: list[str] = Field(default_factory=list)
    manual_verification: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    report_summary: str = ""
    key_impact_points: list[str] = Field(default_factory=list)
    test_focus: list[str] = Field(default_factory=list)
    related_issue_ids: list[str] = Field(default_factory=list)
    impact_issue_links: list[ImpactIssueLink] = Field(default_factory=list)
    llm_markdown: str = ""
    llm_generated: bool = False


class ReviewReport(BaseModel):
    """面向前端结果页输出的最终 Code Review 报告模型。"""

    review_id: str
    status: str
    phase: str
    summary: str
    review: ReviewTask
    findings: list[ReviewFinding] = Field(default_factory=list)
    issues: list[DebateIssue] = Field(default_factory=list)
    issue_count: int = 0
    confidence_summary: ConfidenceSummary = Field(default_factory=ConfidenceSummary)
    llm_usage_summary: LlmUsageSummary = Field(default_factory=LlmUsageSummary)
    human_review_status: str = "not_required"
    issue_filter_decisions: list[IssueFilterDecision] = Field(default_factory=list)
    impact_report: ImpactReport | None = None
