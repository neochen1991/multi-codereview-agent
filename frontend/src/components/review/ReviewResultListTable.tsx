import React from "react";
import { Button, Card, Space, Table, Tag, Tooltip } from "antd";

import type { IssueFilterDecision } from "@/services/api";

export type ReviewResultGroup =
  | "all"
  | "blocking"
  | "should_fix"
  | "non_blocking"
  | "verified"
  | "design_misaligned"
  | "direct_defect"
  | "risk_hypothesis"
  | "test_gap"
  | "design_concern";

export type ReviewResultListRow = {
  id: string;
  file_path: string;
  line_start?: number;
  title: string;
  summary: string;
  metaSummary?: string;
  finding_type: string;
  severity: string;
  confidence: number;
  expert_labels: string[];
  mergeImpact: string;
  priority: string;
  issueStatus: string;
  resolution: string;
  recommendedAction: string;
  needsHuman: boolean;
  verified: boolean;
  hasIssue: boolean;
  governanceDecision?: IssueFilterDecision | null;
  designAlignmentStatus?: string;
  hasDesignEvidence: boolean;
};

type ReviewResultListTableProps = {
  cardClassName: string;
  title: React.ReactNode;
  extra?: React.ReactNode;
  toolbarExtra?: React.ReactNode;
  rows: ReviewResultListRow[];
  activeGroup: ReviewResultGroup;
  onGroupChange: (group: ReviewResultGroup) => void;
  selectedRowId?: string;
  onSelectRow?: (rowId: string) => void;
  selectedRowIds?: string[];
  onSelectedRowIdsChange?: (rowIds: string[]) => void;
  emptyText: string;
};

const findingTypeMeta = (value: string): { label: string; color: string } => {
  if (value === "direct_defect") return { label: "直接缺陷", color: "red" };
  if (value === "test_gap") return { label: "测试缺口", color: "gold" };
  if (value === "design_concern") return { label: "设计关注", color: "blue" };
  return { label: "待验证风险", color: "processing" };
};

const getSeverityColor = (value: string): string => {
  if (value === "blocker" || value === "critical") return "red";
  if (value === "high") return "volcano";
  if (value === "medium") return "gold";
  return "blue";
};

const hasDesignMisalignment = (row: ReviewResultListRow): boolean =>
  row.hasDesignEvidence &&
  (["misaligned", "partially_aligned", "design_misaligned"].includes(String(row.designAlignmentStatus || "").trim()) ||
    row.designAlignmentStatus === "design_misaligned");

const getDesignAlignmentLabel = (value: string): string => {
  if (value === "misaligned" || value === "design_misaligned") return "设计不一致";
  if (value === "partially_aligned") return "部分偏离设计";
  if (value === "aligned") return "符合设计";
  if (value === "insufficient_design_context") return "设计上下文不足";
  return "设计待核对";
};

const filterRowsByGroup = (rows: ReviewResultListRow[], activeGroup: ReviewResultGroup): ReviewResultListRow[] => {
  if (activeGroup === "blocking") {
    return rows.filter((item) => item.mergeImpact === "Blocking");
  }
  if (activeGroup === "should_fix") {
    return rows.filter((item) => item.mergeImpact === "Should fix before merge");
  }
  if (activeGroup === "non_blocking") {
    return rows.filter((item) => item.mergeImpact === "Non-blocking");
  }
  if (activeGroup === "verified") {
    return rows.filter((item) => item.verified);
  }
  if (activeGroup === "design_misaligned") {
    return rows.filter((item) => hasDesignMisalignment(item));
  }
  if (["direct_defect", "risk_hypothesis", "test_gap", "design_concern"].includes(activeGroup)) {
    return rows.filter((item) => item.finding_type === activeGroup);
  }
  return rows;
};

const ReviewResultListTable: React.FC<ReviewResultListTableProps> = ({
  cardClassName,
  title,
  extra,
  toolbarExtra,
  rows,
  activeGroup,
  onGroupChange,
  selectedRowId,
  onSelectRow,
  selectedRowIds,
  onSelectedRowIdsChange,
  emptyText,
}) => {
  const groupedRows = filterRowsByGroup(rows, activeGroup);
  const blockingCount = rows.filter((item) => item.mergeImpact === "Blocking").length;
  const shouldFixCount = rows.filter((item) => item.mergeImpact === "Should fix before merge").length;
  const nonBlockingCount = rows.filter((item) => item.mergeImpact === "Non-blocking").length;
  const verifiedCount = rows.filter((item) => item.verified).length;
  const designMisalignedCount = rows.filter((item) => hasDesignMisalignment(item)).length;
  const directDefectCount = rows.filter((item) => item.finding_type === "direct_defect").length;
  const riskHypothesisCount = rows.filter((item) => item.finding_type === "risk_hypothesis").length;
  const testGapCount = rows.filter((item) => item.finding_type === "test_gap").length;
  const designConcernCount = rows.filter((item) => item.finding_type === "design_concern").length;

  return (
    <Card className={`module-card ${cardClassName}`} title={title} extra={extra}>
      <Space wrap style={{ marginBottom: 6, width: "100%", justifyContent: "space-between" }}>
        <Space wrap>
        <Button type={activeGroup === "all" ? "primary" : "default"} onClick={() => onGroupChange("all")}>
          全部 {rows.length}
        </Button>
        <Button
          danger={activeGroup === "blocking"}
          type={activeGroup === "blocking" ? "primary" : "default"}
          onClick={() => onGroupChange("blocking")}
        >
          Blocking {blockingCount}
        </Button>
        <Button type={activeGroup === "should_fix" ? "primary" : "default"} onClick={() => onGroupChange("should_fix")}>
          Should Fix {shouldFixCount}
        </Button>
        <Button
          type={activeGroup === "non_blocking" ? "primary" : "default"}
          onClick={() => onGroupChange("non_blocking")}
        >
          Non-blocking {nonBlockingCount}
        </Button>
        <Button type={activeGroup === "verified" ? "primary" : "default"} onClick={() => onGroupChange("verified")}>
          已核验 {verifiedCount}
        </Button>
        <Button
          type={activeGroup === "design_misaligned" ? "primary" : "default"}
          onClick={() => onGroupChange("design_misaligned")}
        >
          设计不一致 {designMisalignedCount}
        </Button>
        <Button
          type={activeGroup === "direct_defect" ? "primary" : "default"}
          onClick={() => onGroupChange("direct_defect")}
        >
          直接缺陷 {directDefectCount}
        </Button>
        <Button
          type={activeGroup === "risk_hypothesis" ? "primary" : "default"}
          onClick={() => onGroupChange("risk_hypothesis")}
        >
          待验证风险 {riskHypothesisCount}
        </Button>
        <Button type={activeGroup === "test_gap" ? "primary" : "default"} onClick={() => onGroupChange("test_gap")}>
          测试缺口 {testGapCount}
        </Button>
        <Button
          type={activeGroup === "design_concern" ? "primary" : "default"}
          onClick={() => onGroupChange("design_concern")}
        >
          设计关注 {designConcernCount}
        </Button>
        </Space>
        {toolbarExtra}
      </Space>
      <Table<ReviewResultListRow>
        rowKey="id"
        size="middle"
        pagination={{ pageSize: 8, hideOnSinglePage: true }}
        scroll={{ x: 1520 }}
        dataSource={groupedRows}
        rowClassName={(record) => (record.id === selectedRowId ? "thread-selected" : "")}
        rowSelection={
          onSelectedRowIdsChange
            ? {
                selectedRowKeys: selectedRowIds || [],
                onChange: (keys) => onSelectedRowIdsChange(keys.map((item) => String(item))),
              }
            : undefined
        }
        onRow={(record) => ({
          onClick: () => onSelectRow?.(record.id),
          style: { cursor: onSelectRow ? "pointer" : "default" },
        })}
        className="review-findings-table"
        locale={{ emptyText }}
        columns={[
          {
            title: "代码文件",
            dataIndex: "file_path",
            key: "file_path",
            width: 260,
            render: (value: string, item: ReviewResultListRow) => (
              <button
                type="button"
                className="review-location-link review-file-link"
                onClick={(event) => {
                  event.stopPropagation();
                  onSelectRow?.(item.id);
                }}
                title={value || "-"}
              >
                <span className="review-file-full">{value || "-"}</span>
              </button>
            ),
          },
          {
            title: "行号",
            dataIndex: "line_start",
            key: "line_start",
            width: 90,
            render: (value: number, item: ReviewResultListRow) => (
              <button
                type="button"
                className="review-location-chip"
                onClick={(event) => {
                  event.stopPropagation();
                  onSelectRow?.(item.id);
                }}
              >
                {value ? `L${value}` : "-"}
              </button>
            ),
          },
          {
            title: "问题摘要",
            dataIndex: "summary",
            key: "summary",
            width: 620,
            render: (value: string, item: ReviewResultListRow) => (
              <Tooltip
                placement="topLeft"
                title={
                  <div style={{ maxWidth: 720, whiteSpace: "pre-wrap", lineHeight: 1.6 }}>
                    <div style={{ fontWeight: 600, marginBottom: 8 }}>{item.title}</div>
                    <div>{value}</div>
                    {item.metaSummary ? <div style={{ marginTop: 8, color: "rgba(255,255,255,0.85)" }}>{item.metaSummary}</div> : null}
                  </div>
                }
              >
                <div className="review-summary-cell">
                  <div
                    className="review-summary-title"
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {item.title}
                  </div>
                  <div
                    className="review-summary-text"
                    style={{
                      lineHeight: 1.6,
                      overflow: "hidden",
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                    }}
                  >
                    {value}
                  </div>
                  {item.metaSummary ? (
                    <div
                      className="review-summary-text"
                      style={{
                        marginTop: 6,
                        color: "var(--text-muted)",
                        lineHeight: 1.5,
                        overflow: "hidden",
                        display: "-webkit-box",
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: "vertical",
                      }}
                    >
                      {item.metaSummary}
                    </div>
                  ) : null}
                </div>
              </Tooltip>
            ),
          },
          {
            title: "置信度",
            dataIndex: "confidence",
            key: "confidence",
            width: 110,
            render: (value: number) => `${(value * 100).toFixed(0)}%`,
          },
          {
            title: "问题类型",
            dataIndex: "finding_type",
            key: "finding_type",
            width: 130,
            render: (value: string) => {
              const meta = findingTypeMeta(value);
              return <Tag color={meta.color}>{meta.label}</Tag>;
            },
          },
          {
            title: "级别",
            dataIndex: "severity",
            key: "severity",
            width: 120,
            render: (value: string) => <Tag color={getSeverityColor(value)}>{value}</Tag>,
          },
          {
            title: "提出专家",
            dataIndex: "expert_labels",
            key: "expert_labels",
            width: 220,
            render: (value: string[]) => (
              <div className="review-tag-stack">
                {value.length > 0 ? value.map((entry) => <Tag key={entry} color="geekblue">{entry}</Tag>) : <Tag color="default">-</Tag>}
              </div>
            ),
          },
          {
            title: "优先级",
            dataIndex: "priority",
            key: "priority",
            width: 100,
            render: (value: string) => <Tag color="purple">{value}</Tag>,
          },
          {
            title: "设计一致性",
            key: "design_alignment_status",
            width: 150,
            render: (_: unknown, item: ReviewResultListRow) =>
              !item.hasDesignEvidence ? (
                <span style={{ color: "var(--text-tertiary)" }}>-</span>
              ) : hasDesignMisalignment(item) ? (
                <Tag color="magenta">{getDesignAlignmentLabel(item.designAlignmentStatus || "")}</Tag>
              ) : item.designAlignmentStatus ? (
                <Tag color="success">{getDesignAlignmentLabel(item.designAlignmentStatus || "")}</Tag>
              ) : (
                <span style={{ color: "var(--text-tertiary)" }}>-</span>
              ),
          },
        ]}
      />
    </Card>
  );
};

export default ReviewResultListTable;
