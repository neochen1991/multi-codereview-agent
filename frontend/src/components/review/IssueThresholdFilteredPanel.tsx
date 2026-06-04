import React, { useMemo } from "react";
import { Card, Empty, Table, Tag, Typography } from "antd";

import type { IssueFilterDecision, ReviewFinding } from "@/services/api";
import { humanizeExpertId, humanizeReviewText, humanizeSeverity } from "@/utils/displayText";
import {
  buildReadableIssueSummary,
  buildReadableIssueTitle,
  cleanUserFacingText,
  issueTypeDisplayLabel,
} from "./issueDisplayQuality";

const { Text } = Typography;

type ThresholdFilteredRow = {
  finding_id: string;
  file_path: string;
  line_start: number;
  title: string;
  summary: string;
  finding_type?: string;
  normalized_issue_type?: string;
  category_label?: string;
  severity: string;
  confidence: number;
  expert_id: string;
  threshold_label: string;
  threshold_reason: string;
  rule_code: string;
};

type IssueThresholdFilteredPanelProps = {
  findings: ReviewFinding[];
  issueFilterDecisions: IssueFilterDecision[];
  onSelectFinding?: (findingId: string) => void;
};

const decisionTagColor = (ruleCode: string): string => {
  if (ruleCode === "removed_line_only" || ruleCode === "evidence_anchor_failed") return "red";
  if (ruleCode === "conditional_conclusion" || ruleCode === "llm_judge_rejected") return "gold";
  if (ruleCode === "repo_policy_comment_budget" || ruleCode === "review_learning_false_positive_case") return "purple";
  if (ruleCode === "below_issue_priority_threshold" || ruleCode === "below_priority_confidence_threshold") return "default";
  return "blue";
};

const buildFilteredRow = (finding: ReviewFinding, decision: Partial<IssueFilterDecision>): ThresholdFilteredRow => {
  const fallbackReason = "该发现未满足本轮有效问题升级条件，保留为检视发现。";
  return {
    finding_id: finding.finding_id,
    file_path: finding.file_path,
    line_start: finding.line_start,
    title: finding.title,
    summary: finding.summary,
    finding_type: finding.finding_type,
    normalized_issue_type: finding.normalized_issue_type,
    category_label: finding.category_label,
    severity: finding.severity,
    confidence: finding.confidence,
    expert_id: finding.expert_id,
    threshold_label: cleanUserFacingText(decision.rule_label || "") || humanizeReviewText(decision.rule_label || "") || "未升级为有效问题",
    threshold_reason: cleanUserFacingText(decision.reason || "") || humanizeReviewText(decision.reason || "") || fallbackReason,
    rule_code: decision.rule_code || "unpromoted_finding",
  };
};

const IssueThresholdFilteredPanel: React.FC<IssueThresholdFilteredPanelProps> = ({
  findings,
  issueFilterDecisions,
  onSelectFinding,
}) => {
  const findingById = useMemo(() => {
    const map = new Map<string, ReviewFinding>();
    for (const finding of findings) {
      map.set(finding.finding_id, finding);
    }
    return map;
  }, [findings]);

  const rows = useMemo<ThresholdFilteredRow[]>(() => {
    const result: ThresholdFilteredRow[] = [];
    const seenFindingIds = new Set<string>();
    for (const decision of issueFilterDecisions) {
      for (const findingId of decision.finding_ids || []) {
        const finding = findingById.get(findingId);
        if (!finding || seenFindingIds.has(finding.finding_id)) continue;
        result.push(buildFilteredRow(finding, decision));
        seenFindingIds.add(finding.finding_id);
      }
    }
    for (const finding of findings) {
      if (seenFindingIds.has(finding.finding_id)) continue;
      const decision = finding.code_context?.unpromoted_decision;
      if (!decision) continue;
      result.push(buildFilteredRow(finding, decision));
      seenFindingIds.add(finding.finding_id);
    }
    return result;
  }, [findingById, findings, issueFilterDecisions]);

  if (rows.length === 0) return null;

  return (
    <Card
      className="module-card review-threshold-filter-card"
      title={`保留观察清单 (${rows.length})`}
      extra={<Text type="secondary">这些发现会保留在结果中，但不会升级为正式问题；每条都展示对应治理或裁决原因。</Text>}
    >
      <Table<ThresholdFilteredRow>
        rowKey="finding_id"
        size="middle"
        pagination={{ pageSize: 6, hideOnSinglePage: true }}
        scroll={{ x: 1540 }}
        onRow={(record) => ({
          onClick: () => onSelectFinding?.(record.finding_id),
          style: { cursor: onSelectFinding ? "pointer" : "default" },
        })}
        locale={{ emptyText: <Empty description="当前没有保留观察的问题。" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        columns={[
          {
            title: "代码文件",
            dataIndex: "file_path",
            key: "file_path",
            width: 280,
            render: (value: string) => <span title={value || "-"}>{value || "-"}</span>,
          },
          {
            title: "行号",
            dataIndex: "line_start",
            key: "line_start",
            width: 90,
            render: (value: number) => (value ? `L${value}` : "-"),
          },
          {
            title: "检查角色",
            dataIndex: "expert_id",
            key: "expert_id",
            width: 210,
            render: (value: string) => <Tag color="geekblue">{humanizeExpertId(value)}</Tag>,
          },
          {
            title: "级别",
            dataIndex: "severity",
            key: "severity",
            width: 110,
            render: (value: string) => <Tag color={value === "high" || value === "critical" || value === "blocker" ? "volcano" : value === "medium" ? "gold" : "blue"}>{humanizeSeverity(value)}</Tag>,
          },
          {
            title: "置信度",
            dataIndex: "confidence",
            key: "confidence",
            width: 100,
            render: (value: number) => `${(value * 100).toFixed(0)}%`,
          },
          {
            title: "处理规则",
            dataIndex: "threshold_label",
            key: "threshold_label",
            width: 220,
            render: (value: string, row: ThresholdFilteredRow) => (
              <Tag color={decisionTagColor(row.rule_code)}>
                {humanizeReviewText(value)}
              </Tag>
            ),
          },
          {
            title: "问题摘要",
            key: "summary",
            width: 340,
            render: (_: unknown, row: ThresholdFilteredRow) => {
              const issueType = row.normalized_issue_type || row.finding_type || row.category_label;
              const title = buildReadableIssueTitle({
                title: row.title,
                summary: row.summary,
                file_path: row.file_path,
                line_start: row.line_start,
                finding_type: row.finding_type,
                normalized_issue_type: issueType,
                category_label: row.category_label,
              });
              const summary = buildReadableIssueSummary({
                title: row.title,
                summary: row.summary,
                file_path: row.file_path,
                line_start: row.line_start,
                finding_type: row.finding_type,
                normalized_issue_type: issueType,
                category_label: row.category_label || issueTypeDisplayLabel(issueType),
              });
              return (
                <div className="review-summary-cell">
                  <div className="review-summary-title" title={title}>
                    {title}
                  </div>
                  <div className="review-summary-text" title={summary}>
                    {summary}
                  </div>
                </div>
              );
            },
          },
          {
            title: "处理说明",
            dataIndex: "threshold_reason",
            key: "threshold_reason",
            width: 420,
            render: (value: string) => {
              const text = cleanUserFacingText(value) || humanizeReviewText(value);
              return (
              <div className="review-summary-cell">
                <div className="review-summary-text" title={text}>
                  {text}
                </div>
              </div>
              );
            },
          },
        ]}
        dataSource={rows}
      />
    </Card>
  );
};

export default IssueThresholdFilteredPanel;
