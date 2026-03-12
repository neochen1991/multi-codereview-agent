import React, { useMemo, useState } from "react";
import { Button, Card, Space, Table, Tag, Typography } from "antd";

import type { DebateIssue, ReviewFinding } from "@/services/api";

const { Paragraph } = Typography;

type FindingsPanelProps = {
  findings: ReviewFinding[];
  issues: DebateIssue[];
  selectedFindingId?: string;
  onSelectFinding?: (findingId: string) => void;
};

type StructuredFindingRow = ReviewFinding & {
  issueStatus: string;
  resolution: string;
  recommendedAction: string;
  needsHuman: boolean;
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
  const [activeGroup, setActiveGroup] = useState<"all" | "blocking" | "should_fix" | "non_blocking">("all");
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
    return rows;
  }, [activeGroup, rows]);

  const blockingCount = rows.filter((item) => item.mergeImpact === "Blocking").length;
  const shouldFixCount = rows.filter((item) => item.mergeImpact === "Should fix before merge").length;
  const nonBlockingCount = rows.filter((item) => item.mergeImpact === "Non-blocking").length;

  const columns = [
    {
      title: "代码文件",
      dataIndex: "file_path",
      key: "file_path",
      width: 260,
      render: (value: string, item: StructuredFindingRow) => (
        <button
          type="button"
          className="review-location-link"
          onClick={(event) => {
            event.stopPropagation();
            onSelectFinding?.(item.finding_id);
          }}
        >
          {value || "-"}
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
        <>
          <Tag color={item.issueStatus === "resolved" ? "success" : item.needsHuman ? "error" : "processing"}>
            {item.issueStatus}
          </Tag>
          <Tag>{item.resolution}</Tag>
        </>
      ),
    },
    {
      title: "推荐动作",
      dataIndex: "recommendedAction",
      key: "recommendedAction",
      width: 180,
      render: (value: string) => <Tag color="purple">{value}</Tag>,
    },
    {
      title: "问题摘要",
      dataIndex: "summary",
      key: "summary",
      render: (value: string, item: StructuredFindingRow) => (
        <div>
          <Paragraph style={{ marginBottom: 4, fontWeight: 600 }}>{item.title}</Paragraph>
          <Paragraph style={{ marginBottom: 0 }} ellipsis={{ rows: 2, expandable: true, symbol: "展开" }}>
            {value}
          </Paragraph>
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
    <Card className="module-card" title="Code Review 问题清单">
      <Space wrap style={{ marginBottom: 16 }}>
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
      </Space>
      <Table<StructuredFindingRow>
        rowKey="finding_id"
        size="middle"
        pagination={{ pageSize: 6, hideOnSinglePage: true }}
        scroll={{ x: 1620 }}
        columns={columns}
        dataSource={groupedRows}
        rowClassName={(record) => (record.finding_id === selectedFindingId ? "thread-selected" : "")}
        onRow={(record) => ({
          onClick: () => onSelectFinding?.(record.finding_id),
          style: { cursor: onSelectFinding ? "pointer" : "default" },
        })}
        expandable={{
          expandedRowRender: (item) => (
            <div style={{ display: "grid", gap: 12 }}>
              <div>
                <Paragraph style={{ marginBottom: 4, fontWeight: 600 }}>问题说明</Paragraph>
                <Paragraph style={{ marginBottom: 0 }}>{item.summary}</Paragraph>
              </div>
              <div>
                <Paragraph style={{ marginBottom: 4, fontWeight: 600 }}>修复建议</Paragraph>
                <Paragraph style={{ marginBottom: 0 }}>{item.remediation_suggestion || item.recommendedAction}</Paragraph>
              </div>
              <div>
                <Paragraph style={{ marginBottom: 4, fontWeight: 600 }}>问题代码</Paragraph>
                <pre className="review-conclusion-code">{item.code_excerpt || `${item.file_path}:${item.line_start}`}</pre>
              </div>
            </div>
          ),
          rowExpandable: () => true,
        }}
        locale={{ emptyText: "当前还没有问题结论，运行审核后这里会生成正式的 Code Review 问题清单。" }}
      />
    </Card>
  );
};

export default FindingsPanel;
