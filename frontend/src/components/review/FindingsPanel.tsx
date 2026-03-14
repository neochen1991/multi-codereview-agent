import React, { useMemo, useState } from "react";
import { Button, Card, Space, Table, Tag } from "antd";

import type { DebateIssue, ReviewFinding } from "@/services/api";

type FindingsPanelProps = {
  findings: ReviewFinding[];
  issues: DebateIssue[];
  selectedFindingId?: string;
  onSelectFinding?: (findingId: string) => void;
};

const findingTypeMeta = (value: string): { label: string; color: string } => {
  if (value === "direct_defect") return { label: "直接缺陷", color: "red" };
  if (value === "test_gap") return { label: "测试缺口", color: "gold" };
  if (value === "design_concern") return { label: "设计关注", color: "blue" };
  return { label: "待验证风险", color: "processing" };
};

type StructuredFindingRow = ReviewFinding & {
  issueStatus: string;
  resolution: string;
  recommendedAction: string;
  needsHuman: boolean;
  verified: boolean;
  mergeImpact: string;
  priority: string;
};

const getPriority = (finding: ReviewFinding): string => {
  if (["blocker", "critical"].includes(finding.severity)) return "P0";
  if (finding.severity === "high") return "P1";
  if (finding.severity === "medium") return "P2";
  return "P3";
};

const getMergeImpact = (issue: DebateIssue | undefined, finding: ReviewFinding): string => {
  if (issue?.needs_human && issue.status !== "resolved") return "Blocking";
  if (["blocker", "critical", "high"].includes(finding.severity)) return "Should fix before merge";
  return "Non-blocking";
};

const buildRecommendedAction = (issue: DebateIssue | undefined, finding: ReviewFinding): string => {
  if (!issue) {
    return finding.severity === "high" || finding.severity === "blocker"
      ? "补充证据并优先修复"
      : "继续观察并补上下文";
  }
  if (issue.needs_human && issue.status !== "resolved") {
    return "提交人工复核";
  }
  if (issue.resolution === "human_approved" || issue.resolution === "judge_accepted") {
    return "进入修复清单";
  }
  if (issue.resolution === "human_rejected") {
    return "关闭议题并补证据";
  }
  if (issue.needs_debate && issue.status !== "resolved") {
    return "继续专家辩论";
  }
  if (issue.verified) {
    return "按核验证据整改";
  }
  return "补充证据后再裁决";
};

const FindingsPanel: React.FC<FindingsPanelProps> = ({
  findings,
  issues,
  selectedFindingId,
  onSelectFinding,
}) => {
  // 结果页的问题清单承担“正式报告索引”的职责：
  // 顶部筛选负责切换问题集合，表格负责让用户快速定位到具体 finding。
  const [activeGroup, setActiveGroup] = useState<
    "all" | "blocking" | "should_fix" | "non_blocking" | "verified" | "direct_defect" | "risk_hypothesis" | "test_gap" | "design_concern"
  >("all");
  const issueByFindingId = new Map<string, DebateIssue>();
  for (const issue of issues) {
    for (const findingId of issue.finding_ids) {
      issueByFindingId.set(findingId, issue);
    }
  }

  const rows: StructuredFindingRow[] = findings.map((finding) => {
    const issue = issueByFindingId.get(finding.finding_id);
    return {
      ...finding,
      issueStatus: issue?.status || "open",
      resolution: issue?.resolution || "pending",
      recommendedAction: buildRecommendedAction(issue, finding),
      needsHuman: Boolean(issue?.needs_human),
      verified: Boolean(issue?.verified),
      mergeImpact: getMergeImpact(issue, finding),
      priority: getPriority(finding),
    };
  });

  const groupedRows = useMemo(() => {
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
    if (["direct_defect", "risk_hypothesis", "test_gap", "design_concern"].includes(activeGroup)) {
      return rows.filter((item) => item.finding_type === activeGroup);
    }
    return rows;
  }, [activeGroup, rows]);

  const blockingCount = rows.filter((item) => item.mergeImpact === "Blocking").length;
  const shouldFixCount = rows.filter((item) => item.mergeImpact === "Should fix before merge").length;
  const nonBlockingCount = rows.filter((item) => item.mergeImpact === "Non-blocking").length;
  const verifiedCount = rows.filter((item) => item.verified).length;
  const directDefectCount = rows.filter((item) => item.finding_type === "direct_defect").length;
  const riskHypothesisCount = rows.filter((item) => item.finding_type === "risk_hypothesis").length;
  const testGapCount = rows.filter((item) => item.finding_type === "test_gap").length;
  const designConcernCount = rows.filter((item) => item.finding_type === "design_concern").length;

  const columns = [
    {
      title: "代码文件",
      dataIndex: "file_path",
      key: "file_path",
      width: 260,
      render: (value: string, item: StructuredFindingRow) => (
        <button
          type="button"
          className="review-location-link review-file-link"
          onClick={(event) => {
            event.stopPropagation();
            onSelectFinding?.(item.finding_id);
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
      render: (value: number, item: StructuredFindingRow) => (
        <button
          type="button"
          className="review-location-chip"
          onClick={(event) => {
            event.stopPropagation();
            onSelectFinding?.(item.finding_id);
          }}
        >
          {value ? `L${value}` : "-"}
        </button>
      ),
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
      render: (value: string) => {
        const color =
          value === "blocker" || value === "critical"
            ? "red"
            : value === "high"
              ? "volcano"
              : value === "medium"
                ? "gold"
                : "blue";
        return <Tag color={color}>{value}</Tag>;
      },
    },
    {
      title: "提出专家",
      dataIndex: "expert_id",
      key: "expert_id",
      width: 220,
      render: (value: string) => <Tag color="geekblue">{value}</Tag>,
    },
    {
      title: "合并影响",
      dataIndex: "mergeImpact",
      key: "mergeImpact",
      width: 170,
      render: (value: string) => (
        <Tag color={value === "Blocking" ? "error" : value.includes("Should fix") ? "warning" : "success"}>
          {value}
        </Tag>
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
      title: "裁决状态",
      key: "issue_status",
      width: 180,
      render: (_: unknown, item: StructuredFindingRow) => (
        <div className="review-tag-stack">
          <Tag color={item.issueStatus === "resolved" ? "success" : item.needsHuman ? "error" : "processing"}>
            {item.issueStatus}
          </Tag>
          <Tag>{item.resolution}</Tag>
        </div>
      ),
    },
    {
      title: "核验状态",
      key: "verified",
      width: 130,
      render: (_: unknown, item: StructuredFindingRow) =>
        item.verified ? <Tag color="success">已核验</Tag> : <Tag>未核验</Tag>,
    },
    {
      title: "推荐动作",
      dataIndex: "recommendedAction",
      key: "recommendedAction",
      width: 180,
      render: (value: string) => (
        <span className="review-action-chip" title={value}>
          {value}
        </span>
      ),
    },
    {
      title: "问题摘要",
      dataIndex: "summary",
      key: "summary",
      width: 320,
      render: (value: string, item: StructuredFindingRow) => (
        <div className="review-summary-cell">
          <div className="review-summary-title" title={item.title}>
            {item.title}
          </div>
          <div className="review-summary-text" title={value}>
            {value}
          </div>
        </div>
      ),
    },
    {
      title: "置信度",
      dataIndex: "confidence",
      key: "confidence",
      width: 110,
      render: (value: number) => `${(value * 100).toFixed(0)}%`,
    },
  ];

  return (
    <Card className="module-card review-findings-card" title="Code Review 问题清单">
      <Space wrap style={{ marginBottom: 6 }}>
        <Button type={activeGroup === "all" ? "primary" : "default"} onClick={() => setActiveGroup("all")}>
          全部 {rows.length}
        </Button>
        <Button danger={activeGroup === "blocking"} type={activeGroup === "blocking" ? "primary" : "default"} onClick={() => setActiveGroup("blocking")}>
          Blocking {blockingCount}
        </Button>
        <Button
          type={activeGroup === "should_fix" ? "primary" : "default"}
          onClick={() => setActiveGroup("should_fix")}
        >
          Should Fix {shouldFixCount}
        </Button>
        <Button
          type={activeGroup === "non_blocking" ? "primary" : "default"}
          onClick={() => setActiveGroup("non_blocking")}
        >
          Non-blocking {nonBlockingCount}
        </Button>
        <Button type={activeGroup === "verified" ? "primary" : "default"} onClick={() => setActiveGroup("verified")}>
          已核验 {verifiedCount}
        </Button>
        <Button type={activeGroup === "direct_defect" ? "primary" : "default"} onClick={() => setActiveGroup("direct_defect")}>
          直接缺陷 {directDefectCount}
        </Button>
        <Button type={activeGroup === "risk_hypothesis" ? "primary" : "default"} onClick={() => setActiveGroup("risk_hypothesis")}>
          待验证风险 {riskHypothesisCount}
        </Button>
        <Button type={activeGroup === "test_gap" ? "primary" : "default"} onClick={() => setActiveGroup("test_gap")}>
          测试缺口 {testGapCount}
        </Button>
        <Button type={activeGroup === "design_concern" ? "primary" : "default"} onClick={() => setActiveGroup("design_concern")}>
          设计关注 {designConcernCount}
        </Button>
      </Space>
      <Table<StructuredFindingRow>
        rowKey="finding_id"
        size="middle"
        pagination={{ pageSize: 8, hideOnSinglePage: true }}
        scroll={{ x: 1620 }}
        columns={columns}
        dataSource={groupedRows}
        rowClassName={(record) => (record.finding_id === selectedFindingId ? "thread-selected" : "")}
        onRow={(record) => ({
          onClick: () => onSelectFinding?.(record.finding_id),
          style: { cursor: onSelectFinding ? "pointer" : "default" },
        })}
        className="review-findings-table"
        locale={{ emptyText: "当前还没有问题结论，运行审核后这里会生成正式的 Code Review 问题清单。" }}
      />
    </Card>
  );
};

export default FindingsPanel;
