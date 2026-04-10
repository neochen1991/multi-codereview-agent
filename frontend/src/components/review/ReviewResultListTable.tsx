import React, { useEffect, useMemo, useState } from "react";
import { Card, Input, Select, Space, Table, Tag, Tooltip } from "antd";
import type { ColumnsType } from "antd/es/table";

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
  finding_type_labels?: string[];
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

type ColumnWidthKey =
  | "file_path"
  | "line_start"
  | "summary"
  | "confidence"
  | "finding_type"
  | "severity"
  | "expert_labels"
  | "priority"
  | "design_alignment_status";

type ColumnWidths = Record<ColumnWidthKey, number>;

type ResizableTitleProps = React.HTMLAttributes<HTMLTableCellElement> & {
  width?: number;
  onResize?: (delta: number) => void;
};

const DEFAULT_COLUMN_WIDTHS: ColumnWidths = {
  file_path: 260,
  line_start: 90,
  summary: 760,
  confidence: 110,
  finding_type: 180,
  severity: 120,
  expert_labels: 150,
  priority: 100,
  design_alignment_status: 150,
};

const MIN_COLUMN_WIDTHS: Partial<ColumnWidths> = {
  file_path: 180,
  line_start: 70,
  summary: 320,
  confidence: 90,
  finding_type: 130,
  severity: 90,
  expert_labels: 100,
  priority: 80,
  design_alignment_status: 110,
};

const ResizableTitleCell: React.FC<ResizableTitleProps> = ({ onResize, width, children, ...restProps }) => {
  const handleMouseDown = (event: React.MouseEvent<HTMLSpanElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!onResize) return;
    const startX = event.clientX;
    const onMouseMove = (moveEvent: MouseEvent) => {
      onResize(moveEvent.clientX - startX);
    };
    const onMouseUp = () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  };

  return (
    <th {...restProps} style={{ ...(restProps.style || {}), width, position: "relative" }}>
      {children}
      {onResize ? (
        <span
          onMouseDown={handleMouseDown}
          style={{
            position: "absolute",
            top: 0,
            right: 0,
            width: 12,
            height: "100%",
            cursor: "col-resize",
            userSelect: "none",
            zIndex: 2,
            borderRight: "2px solid rgba(24, 144, 255, 0.28)",
            background: "linear-gradient(to right, transparent 0%, rgba(24, 144, 255, 0.08) 100%)",
          }}
        />
      ) : null}
    </th>
  );
};

const findingTypeMeta = (value: string): { label: string; color: string } => {
  if (value === "direct_defect") return { label: "直接缺陷", color: "red" };
  if (value === "test_gap") return { label: "测试缺口", color: "gold" };
  if (value === "design_concern") return { label: "设计关注", color: "blue" };
  return { label: "待验证风险", color: "processing" };
};

export const classifySpecificIssueType = (text: string): string | null => {
  const value = text.toLowerCase();
  if (!value) return null;
  if (value.includes("limit") || value.includes("分页") || value.includes("大结果集")) return "SQL分页缺失";
  if (value.includes("n+1")) return "N+1查询风险";
  if (value.includes("sql") && value.includes("注入")) return "SQL注入风险";
  if (value.includes("like") || value.includes("模糊匹配") || value.includes("查询语义")) return "查询语义变更";
  if (value.includes("命名") || value.includes("chunksTmp".toLowerCase()) || value.includes("常量")) return "命名规范问题";
  if (value.includes("魔法值")) return "魔法值问题";
  if (value.includes("catch") || value.includes("吞异常") || value.includes("异常") && value.includes("吞")) return "异常处理问题";
  if (value.includes("domain event") || value.includes("事件发布") || value.includes("持久化顺序")) return "事件时序问题";
  if (value.includes("factory") || value.includes("工厂") || value.includes("直接构造")) return "DDD工厂绕过";
  if (value.includes("aggregate") || value.includes("聚合")) return "DDD聚合边界问题";
  if (value.includes("事务")) return "事务边界问题";
  if (value.includes("鉴权") || value.includes("权限") || value.includes("authorization") || value.includes("auth")) return "鉴权问题";
  if (value.includes("日志") && (value.includes("泄露") || value.includes("敏感"))) return "日志敏感信息问题";
  if (value.includes("空指针") || value.includes("空参") || value.includes("判空") || value.includes("null")) return "空值校验问题";
  if (value.includes("并发") || value.includes("锁")) return "并发安全问题";
  if (value.includes("缓存")) return "缓存使用问题";
  if (value.includes("测试") || value.includes("断言") || value.includes("用例缺失")) return "测试覆盖问题";
  return null;
};

const getSpecificFindingTypeLabels = (row: ReviewResultListRow): string[] => {
  if (row.finding_type_labels && row.finding_type_labels.length > 0) {
    return Array.from(new Set(row.finding_type_labels.filter(Boolean)));
  }
  const fallback = findingTypeMeta(row.finding_type).label;
  return [fallback];
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
  const storageKey = useMemo(() => `review-result-table-widths:v2:${cardClassName}`, [cardClassName]);
  const [columnWidths, setColumnWidths] = useState<ColumnWidths>(() => {
    if (typeof window === "undefined") return DEFAULT_COLUMN_WIDTHS;
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (!raw) return DEFAULT_COLUMN_WIDTHS;
      return { ...DEFAULT_COLUMN_WIDTHS, ...(JSON.parse(raw) as Partial<ColumnWidths>) };
    } catch {
      return DEFAULT_COLUMN_WIDTHS;
    }
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(storageKey, JSON.stringify(columnWidths));
  }, [columnWidths, storageKey]);

  const findingTypeOptions = useMemo(
    () =>
      Array.from(new Set(rows.flatMap((item) => getSpecificFindingTypeLabels(item)).filter(Boolean))).map((value) => ({
        label: value,
        value,
      })),
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
      if (findingTypeFilter && !getSpecificFindingTypeLabels(item).includes(findingTypeFilter)) {
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

  const resizeColumn = (key: ColumnWidthKey, delta: number) => {
    setColumnWidths((current) => {
      const minWidth = MIN_COLUMN_WIDTHS[key] || 80;
      return {
        ...current,
        [key]: Math.max(minWidth, (current[key] || DEFAULT_COLUMN_WIDTHS[key]) + delta),
      };
    });
  };

  const tableScrollX = useMemo(
    () => Object.values(columnWidths).reduce((total, current) => total + current, 0) + 120,
    [columnWidths],
  );

  const columns = useMemo<ColumnsType<ReviewResultListRow>>(
    () => [
      {
        title: "代码文件",
        dataIndex: "file_path",
        key: "file_path",
        width: columnWidths.file_path,
        onHeaderCell: () => ({ width: columnWidths.file_path, onResize: (delta: number) => resizeColumn("file_path", delta) }),
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
        width: columnWidths.line_start,
        onHeaderCell: () => ({ width: columnWidths.line_start, onResize: (delta: number) => resizeColumn("line_start", delta) }),
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
        width: columnWidths.summary,
        onHeaderCell: () => ({ width: columnWidths.summary, onResize: (delta: number) => resizeColumn("summary", delta) }),
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
              <div className="review-summary-title" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
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
        width: columnWidths.confidence,
        onHeaderCell: () => ({ width: columnWidths.confidence, onResize: (delta: number) => resizeColumn("confidence", delta) }),
        render: (value: number) => `${(value * 100).toFixed(0)}%`,
      },
      {
        title: "问题类型",
        dataIndex: "finding_type",
        key: "finding_type",
        width: columnWidths.finding_type,
        onHeaderCell: () => ({ width: columnWidths.finding_type, onResize: (delta: number) => resizeColumn("finding_type", delta) }),
        render: (_value: string, item: ReviewResultListRow) => {
          const labels = getSpecificFindingTypeLabels(item);
          if (labels.length <= 1) {
            return <Tag color="purple">{labels[0]}</Tag>;
          }
          return (
            <Tooltip placement="topLeft" title={labels.join(" / ")}>
              <Tag color="purple">混合问题 {labels.length}</Tag>
            </Tooltip>
          );
        },
      },
      {
        title: "级别",
        dataIndex: "severity",
        key: "severity",
        width: columnWidths.severity,
        onHeaderCell: () => ({ width: columnWidths.severity, onResize: (delta: number) => resizeColumn("severity", delta) }),
        render: (value: string) => <Tag color={getSeverityColor(value)}>{value}</Tag>,
      },
      {
        title: "提出专家",
        dataIndex: "expert_labels",
        key: "expert_labels",
        width: columnWidths.expert_labels,
        onHeaderCell: () => ({ width: columnWidths.expert_labels, onResize: (delta: number) => resizeColumn("expert_labels", delta) }),
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
        width: columnWidths.priority,
        onHeaderCell: () => ({ width: columnWidths.priority, onResize: (delta: number) => resizeColumn("priority", delta) }),
        render: (value: string) => <Tag color="purple">{value}</Tag>,
      },
      {
        title: "设计一致性",
        key: "design_alignment_status",
        width: columnWidths.design_alignment_status,
        onHeaderCell: () => ({ width: columnWidths.design_alignment_status, onResize: (delta: number) => resizeColumn("design_alignment_status", delta) }),
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
    ],
    [columnWidths, onSelectRow],
  );

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
        components={{ header: { cell: ResizableTitleCell } }}
        rowKey="id"
        size="middle"
        pagination={{ pageSize: 8, hideOnSinglePage: true }}
        scroll={{ x: tableScrollX }}
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
        columns={columns}
      />
    </Card>
  );
};

export default ReviewResultListTable;
