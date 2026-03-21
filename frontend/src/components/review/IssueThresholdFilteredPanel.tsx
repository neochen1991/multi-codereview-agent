import React, { useMemo } from "react";
import { Card, Empty, Table, Tag, Typography } from "antd";

import type { IssueFilterDecision, ReviewFinding } from "@/services/api";

const { Text } = Typography;

type ThresholdFilteredRow = {
  finding_id: string;
  file_path: string;
  line_start: number;
  title: string;
  summary: string;
  severity: string;
  confidence: number;
  expert_id: string;
  threshold_label: string;
  threshold_reason: string;
};

type IssueThresholdFilteredPanelProps = {
  findings: ReviewFinding[];
  issueFilterDecisions: IssueFilterDecision[];
};

const THRESHOLD_RULE_CODES = new Set([
  "below_issue_priority_threshold",
  "below_priority_confidence_threshold",
]);

const IssueThresholdFilteredPanel: React.FC<IssueThresholdFilteredPanelProps> = ({ findings, issueFilterDecisions }) => {
  const findingById = useMemo(() => {
    const map = new Map<string, ReviewFinding>();
    for (const finding of findings) {
      map.set(finding.finding_id, finding);
    }
    return map;
  }, [findings]);

  const rows = useMemo<ThresholdFilteredRow[]>(() => {
    const result: ThresholdFilteredRow[] = [];
    for (const decision of issueFilterDecisions) {
      if (!THRESHOLD_RULE_CODES.has(decision.rule_code)) continue;
      for (const findingId of decision.finding_ids || []) {
        const finding = findingById.get(findingId);
        if (!finding) continue;
        result.push({
          finding_id: finding.finding_id,
          file_path: finding.file_path,
          line_start: finding.line_start,
          title: finding.title,
          summary: finding.summary,
          severity: finding.severity,
          confidence: finding.confidence,
          expert_id: finding.expert_id,
          threshold_label: decision.rule_label,
          threshold_reason: decision.reason,
        });
      }
    }
    return result;
  }, [findingById, issueFilterDecisions]);

  if (rows.length === 0) return null;

  return (
    <Card
      className="module-card review-threshold-filter-card"
      title={`被阈值过滤的发现清单 (${rows.length})`}
      extra={<Text type="secondary">这些发现会保留在结果中，但不会升级为正式议题</Text>}
    >
      <Table<ThresholdFilteredRow>
        rowKey="finding_id"
        size="middle"
        pagination={{ pageSize: 6, hideOnSinglePage: true }}
        scroll={{ x: 1540 }}
        locale={{ emptyText: <Empty description="当前没有被阈值过滤的问题。" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
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
            title: "提出专家",
            dataIndex: "expert_id",
            key: "expert_id",
            width: 210,
            render: (value: string) => <Tag color="geekblue">{value}</Tag>,
          },
          {
            title: "级别",
            dataIndex: "severity",
            key: "severity",
            width: 110,
            render: (value: string) => <Tag color={value === "high" || value === "critical" || value === "blocker" ? "volcano" : value === "medium" ? "gold" : "blue"}>{value}</Tag>,
          },
          {
            title: "置信度",
            dataIndex: "confidence",
            key: "confidence",
            width: 100,
            render: (value: number) => `${(value * 100).toFixed(0)}%`,
          },
          {
            title: "阈值规则",
            dataIndex: "threshold_label",
            key: "threshold_label",
            width: 220,
            render: (value: string) => <Tag color="default">{value}</Tag>,
          },
          {
            title: "问题摘要",
            key: "summary",
            width: 340,
            render: (_: unknown, row: ThresholdFilteredRow) => (
              <div className="review-summary-cell">
                <div className="review-summary-title" title={row.title}>
                  {row.title}
                </div>
                <div className="review-summary-text" title={row.summary}>
                  {row.summary}
                </div>
              </div>
            ),
          },
          {
            title: "过滤原因",
            dataIndex: "threshold_reason",
            key: "threshold_reason",
            width: 420,
            render: (value: string) => (
              <div className="review-summary-cell">
                <div className="review-summary-text" title={value}>
                  {value}
                </div>
              </div>
            ),
          },
        ]}
        dataSource={rows}
      />
    </Card>
  );
};

export default IssueThresholdFilteredPanel;
