import React, { useMemo } from "react";
import { Tag } from "antd";

import type { DebateIssue, IssueFilterDecision, ReviewFinding } from "@/services/api";
import ReviewResultListTable, { classifySpecificIssueType, type ReviewResultListRow } from "./ReviewResultListTable";

type FindingsPanelProps = {
  findings: ReviewFinding[];
  issues: DebateIssue[];
  issueFilterDecisions?: IssueFilterDecision[];
  selectedFindingId?: string;
  onSelectFinding?: (findingId: string) => void;
};

const getPriority = (finding: ReviewFinding): string => {
  if (["blocker", "critical"].includes(finding.severity)) return "P0";
  if (finding.severity === "high") return "P1";
  if (finding.severity === "medium") return "P2";
  return "P3";
};

const getMergeImpact = (issue: DebateIssue | undefined, finding: ReviewFinding): string => {
  if (!issue) return "Finding only";
  if (issue.needs_human && issue.status !== "resolved") return "Blocking";
  if (["blocker", "critical", "high"].includes(finding.severity)) return "Should fix before merge";
  return "Non-blocking";
};

const buildRecommendedAction = (issue: DebateIssue | undefined, finding: ReviewFinding): string => {
  if (!issue) {
    return finding.severity === "high" || finding.severity === "blocker"
      ? "补充证据并优先修复"
      : "继续观察并补上下文";
  }
  if (issue.needs_human && issue.status !== "resolved") return "提交人工复核";
  if (issue.resolution === "human_approved" || issue.resolution === "judge_accepted") return "进入修复清单";
  if (issue.resolution === "human_rejected") return "关闭议题并补证据";
  if (issue.needs_debate && issue.status !== "resolved") return "继续专家辩论";
  if (issue.verified) return "按核验证据整改";
  return "补充证据后再裁决";
};

const hasDesignEvidence = (finding: ReviewFinding): boolean =>
  (finding.design_doc_titles?.length || 0) > 0 ||
  (finding.matched_design_points?.length || 0) > 0 ||
  (finding.missing_design_points?.length || 0) > 0 ||
  (finding.extra_implementation_points?.length || 0) > 0 ||
  (finding.design_conflicts?.length || 0) > 0;

const buildFindingTypeLabels = (finding: ReviewFinding): string[] => {
  const values = [
    finding.title,
    ...(finding.matched_rules || []),
    ...(finding.violated_guidelines || []),
    finding.summary,
  ];
  return Array.from(new Set(values.map((item) => classifySpecificIssueType(String(item || ""))).filter(Boolean) as string[]));
};

const FindingsPanel: React.FC<FindingsPanelProps> = ({
  findings,
  issues,
  issueFilterDecisions = [],
  selectedFindingId,
  onSelectFinding,
}) => {
  const issueByFindingId = useMemo(() => {
    const map = new Map<string, DebateIssue>();
    for (const issue of issues) {
      for (const findingId of issue.finding_ids) {
        map.set(findingId, issue);
      }
    }
    return map;
  }, [issues]);

  const governanceDecisionByFindingId = useMemo(() => {
    const map = new Map<string, IssueFilterDecision>();
    for (const decision of issueFilterDecisions) {
      for (const findingId of decision.finding_ids || []) {
        if (findingId) map.set(findingId, decision);
      }
    }
    return map;
  }, [issueFilterDecisions]);

  const rows = useMemo<ReviewResultListRow[]>(
    () =>
      findings.map((finding) => {
        const issue = issueByFindingId.get(finding.finding_id);
        const designMisaligned =
          hasDesignEvidence(finding) &&
          (["misaligned", "partially_aligned"].includes(String(finding.design_alignment_status || "").trim()) ||
            (finding.missing_design_points?.length || 0) > 0 ||
            (finding.design_conflicts?.length || 0) > 0);
        return {
          id: finding.finding_id,
          file_path: finding.file_path,
          line_start: finding.line_start,
          title: finding.title,
          summary: finding.summary,
          metaSummary: undefined,
          finding_type: finding.finding_type,
          finding_type_labels: buildFindingTypeLabels(finding),
          severity: finding.severity,
          confidence: finding.confidence,
          expert_labels: finding.expert_id ? [finding.expert_id] : [],
          mergeImpact: getMergeImpact(issue, finding),
          priority: getPriority(finding),
          issueStatus: issue?.status || "finding_only",
          resolution: issue?.resolution || "not_promoted",
          recommendedAction: buildRecommendedAction(issue, finding),
          needsHuman: Boolean(issue?.needs_human),
          verified: Boolean(issue?.verified),
          hasIssue: Boolean(issue),
          governanceDecision: governanceDecisionByFindingId.get(finding.finding_id) || null,
          designAlignmentStatus: designMisaligned ? "design_misaligned" : finding.design_alignment_status,
          hasDesignEvidence: hasDesignEvidence(finding),
        };
      }),
    [findings, governanceDecisionByFindingId, issueByFindingId],
  );

  return (
    <ReviewResultListTable
      cardClassName="review-findings-card"
      title="审核发现清单"
      extra={<Tag color="default">这里展示本次审核产出的全部 findings，包含已升级为正式议题和保留为 finding 的证据项</Tag>}
      rows={rows}
      selectedRowId={selectedFindingId}
      onSelectRow={onSelectFinding}
      emptyText="当前还没有审核发现。"
    />
  );
};

export default FindingsPanel;
