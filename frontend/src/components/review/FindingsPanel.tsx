import React, { useMemo } from "react";
import { Tag } from "antd";

import type { DebateIssue, IssueFilterDecision, ReviewFinding } from "@/services/api";
import { humanizeExpertId } from "@/utils/displayText";
import { issueTypeDisplayLabel } from "./issueDisplayQuality";
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
  if (!issue) return "未升级为有效问题";
  if (issue.needs_human && issue.status !== "resolved") return "阻塞合并，等待人工确认";
  if (["blocker", "critical", "high"].includes(finding.severity)) return "建议合并前修复";
  return "不阻塞合并";
};

const buildRecommendedAction = (issue: DebateIssue | undefined, finding: ReviewFinding): string => {
  if (!issue) {
    return finding.severity === "high" || finding.severity === "blocker"
      ? "补充证据后重新评估"
      : "作为审核发现跟进";
  }
  if (issue.needs_human && issue.status !== "resolved") return "提交人工复核";
  if (issue.resolution === "human_approved" || issue.resolution === "judge_accepted") return "进入修复清单";
  if (issue.resolution === "human_rejected") return "关闭问题并补证据";
  if (issue.needs_debate && issue.status !== "resolved") return "继续复核";
  if (issue.verified) return "按核验证据整改";
  return "补充证据后再裁决";
};

const hasDesignEvidence = (finding: ReviewFinding): boolean =>
  (finding.design_doc_titles?.length || 0) > 0 ||
  (finding.matched_design_points?.length || 0) > 0 ||
  (finding.missing_design_points?.length || 0) > 0 ||
  (finding.extra_implementation_points?.length || 0) > 0 ||
  (finding.design_conflicts?.length || 0) > 0;

const normalizePath = (value?: string | null): string =>
  String(value || "").replace(/\\/g, "/").trim().toLowerCase();

const isSameFindingAnchor = (issue: DebateIssue | undefined, finding: ReviewFinding): boolean => {
  if (!issue) return false;
  const issuePath = normalizePath(issue.file_path);
  const findingPath = normalizePath(finding.file_path);
  const samePath =
    Boolean(issuePath && findingPath) &&
    (issuePath === findingPath || issuePath.endsWith(`/${findingPath}`) || findingPath.endsWith(`/${issuePath}`));
  const issueLine = Number(issue.line_start || 0);
  const findingLine = Number(finding.line_start || 0);
  return samePath && Boolean(issueLine && findingLine) && issueLine === findingLine;
};

const buildFindingTypeLabels = (finding: ReviewFinding): string[] => {
  const primaryValues = [
    finding.normalized_issue_type,
    finding.category_label,
    finding.title,
    finding.summary,
  ];
  const primaryLabels = primaryValues
    .map((item) => classifySpecificIssueType(String(item || "")) || issueTypeDisplayLabel(item))
    .filter((item) => item && item !== "代码风险");
  if (primaryLabels.length) return Array.from(new Set(primaryLabels as string[])).slice(0, 2);
  const ruleLabels = [...(finding.matched_rules || []), ...(finding.violated_guidelines || [])]
    .map((item) => classifySpecificIssueType(String(item || "")))
    .filter(Boolean);
  return Array.from(new Set(ruleLabels as string[])).slice(0, 2);
};

const buildConfidenceMetaSummary = (finding: ReviewFinding): string | undefined => {
  const parts = [
    finding.category_label ? `分类：${finding.category_label}` : "",
    finding.confidence_rationale ? `置信度理由：${finding.confidence_rationale}` : "",
  ].filter(Boolean);
  return parts.length ? parts.join("；") : undefined;
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
        const sameAnchorIssue = isSameFindingAnchor(issue, finding) ? issue : undefined;
        const displayTitle = sameAnchorIssue?.title || finding.title;
        const displaySummary = sameAnchorIssue?.summary || finding.summary;
        const displayTypeLabel = classifySpecificIssueType(`${displayTitle}\n${displaySummary}`);
        const designMisaligned =
          hasDesignEvidence(finding) &&
          (["misaligned", "partially_aligned"].includes(String(finding.design_alignment_status || "").trim()) ||
            (finding.missing_design_points?.length || 0) > 0 ||
            (finding.design_conflicts?.length || 0) > 0);
        return {
          id: finding.finding_id,
          file_path: finding.file_path,
          line_start: finding.line_start,
          title: displayTitle,
          summary: displaySummary,
          metaSummary: buildConfidenceMetaSummary(finding),
          finding_type: finding.finding_type,
          finding_types: [
            sameAnchorIssue?.normalized_issue_type,
            sameAnchorIssue?.finding_type,
            finding.normalized_issue_type,
            finding.finding_type,
          ].filter(Boolean) as string[],
          finding_type_labels: displayTypeLabel ? [displayTypeLabel] : buildFindingTypeLabels(finding),
          severity: finding.severity,
          confidence: finding.confidence,
          expert_labels: finding.expert_id ? [humanizeExpertId(finding.expert_id)] : [],
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
      extra={<Tag color="default">展示全部审核发现，并标明是否已升级为有效问题</Tag>}
      rows={rows}
      selectedRowId={selectedFindingId}
      onSelectRow={onSelectFinding}
      emptyText="当前还没有审核发现。"
    />
  );
};

export default FindingsPanel;
