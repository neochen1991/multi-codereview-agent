import React, { useMemo, useState } from "react";
import { Card, Input, Select, Space, Table, Tag, Tooltip } from "antd";

import type { IssueFilterDecision } from "@/services/api";

export type ReviewResultListRow = {
  id: string;
  file_path: string;
  line_start?: number;
  title: string;
  summary: string;
  metaSummary?: string;
  finding_type: string;
  finding_types?: string[];
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

const getRowFindingTypes = (row: ReviewResultListRow): string[] =>
  row.finding_types && row.finding_types.length > 0 ? row.finding_types : [row.finding_type];

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

const ReviewResultListTable: React.FC<ReviewResultListTableProps> = ({
  cardClassName,
  title,
  extra,
  toolbarExtra,
  rows,
  selectedRowId,
  onSelectRow,
  selectedRowIds,
  onSelectedRowIdsChange,
  emptyText,
}) => {
  const [fileKeyword, setFileKeyword] = useState("");
  const [findingTypeFilter, setFindingTypeFilter] = useState<string | undefined>(undefined);
  const [severityFilter, setSeverityFilter] = useState<string | undefined>(undefined);
  const [priorityFilter, setPriorityFilter] = useState<string | undefined>(undefined);

  const findingTypeOptions = useMemo(
    () =>
      Array.from(new Set(rows.flatMap((item) => getRowFindingTypes(item)).filter(Boolean))).map((value) => {
        const meta = findingTypeMeta(value);
        return { label: meta.label, value };
      }),
    [rows],
  );

  const severityOptions = useMemo(
    () =>
      Array.from(new Set(rows.map((item) => item.severity).filter(Boolean))).map((value) => ({
        label: value,
        value,
      })),
    [rows],
  );

  const priorityOptions = useMemo(
    () =>
      Array.from(new Set(rows.map((item) => item.priority).filter(Boolean))).map((value) => ({
        label: value,
        value,
      })),
    [rows],
  );

  const filteredRows = useMemo(() => {
    const normalizedKeyword = fileKeyword.trim().toLowerCase();
    return rows.filter((item) => {
      if (normalizedKeyword && !String(item.file_path || "").toLowerCase().includes(normalizedKeyword)) {
        return false;
      }
      if (findingTypeFilter && !getRowFindingTypes(item).includes(findingTypeFilter)) {
        return false;
      }
      if (severityFilter && item.severity !== severityFilter) {
        return false;
      }
      if (priorityFilter && item.priority !== priorityFilter) {
        return false;
      }
      return true;
    });
  }, [fileKeyword, findingTypeFilter, priorityFilter, rows, severityFilter]);

  return (
    <Card className={`module-card ${cardClassName}`} title={title} extra={extra}>
      <Space wrap style={{ marginBottom: 6, width: "100%", justifyContent: "space-between" }}>
        <Space wrap>
          <Input
            allowClear
            value={fileKeyword}
            onChange={(event) => setFileKeyword(event.target.value)}
            placeholder="输入文件名筛选"
            style={{ width: 240 }}
          />
          <Select
            allowClear
            value={findingTypeFilter}
            onChange={(value) => setFindingTypeFilter(value)}
            placeholder="问题类型"
            style={{ width: 160 }}
            options={findingTypeOptions}
          />
          <Select
            allowClear
            value={severityFilter}
            onChange={(value) => setSeverityFilter(value)}
            placeholder="级别"
            style={{ width: 140 }}
            options={severityOptions}
          />
          <Select
            allowClear
            value={priorityFilter}
            onChange={(value) => setPriorityFilter(value)}
            placeholder="优先级"
            style={{ width: 140 }}
            options={priorityOptions}
          />
          <Tag color="default">筛选后 {filteredRows.length} / 全部 {rows.length}</Tag>
        </Space>
        {toolbarExtra}
      </Space>
      <Table<ReviewResultListRow>
        rowKey="id"
        size="middle"
        pagination={{ pageSize: 8, hideOnSinglePage: true }}
        scroll={{ x: 1520 }}
        dataSource={filteredRows}
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
            width: 180,
            render: (value: string, item: ReviewResultListRow) => {
              const types = getRowFindingTypes(item);
              if (types.length <= 1) {
                const meta = findingTypeMeta(value);
                return <Tag color={meta.color}>{meta.label}</Tag>;
              }
              return (
                <Tooltip
                  placement="topLeft"
                  title={types.map((type) => findingTypeMeta(type).label).join(" / ")}
                >
                  <Tag color="purple">混合问题 {types.length}</Tag>
                </Tooltip>
              );
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
