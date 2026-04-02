import React from "react";
import { Button, Card, Col, Descriptions, Row, Space, Statistic, Tag, Typography } from "antd";

import type { DebateIssue, ReviewFinding, ReviewReport, ReviewSummary } from "@/services/api";

const { Paragraph } = Typography;

type ReportSummaryPanelProps = {
  report: ReviewReport | null;
  findings: ReviewFinding[];
  issues: DebateIssue[];
  review?: ReviewSummary | null;
  className?: string;
  onNavigateToGroup?: (
    group:
      | "all"
      | "blocking"
      | "should_fix"
      | "non_blocking"
      | "verified"
      | "design_misaligned"
      | "direct_defect"
      | "risk_hypothesis"
      | "test_gap"
      | "design_concern",
  ) => void;
};

const isReviewStillRunning = (review?: ReviewSummary | null): boolean =>
  Boolean(review && ["pending", "queued", "running"].includes(String(review.status || "").toLowerCase()));

const getMergeDecision = (report: ReviewReport | null, findings: ReviewFinding[], review?: ReviewSummary | null): string => {
  if (isReviewStillRunning(review)) return "审核进行中，结果尚未最终收敛";
  const highRiskCount = findings.filter((item) => ["blocker", "critical", "high"].includes(item.severity)).length;
  if ((report?.confidence_summary.needs_human_count || 0) > 0) return "阻塞合并，等待人工确认";
  if (highRiskCount > 0) return "建议修复高风险问题后再合并";
  if (findings.length > 0) return "建议处理审核发现后合并";
  return "可直接合并";
};

const getVerdict = (report: ReviewReport | null, findings: ReviewFinding[], review?: ReviewSummary | null): { label: string; color: string } => {
  if (isReviewStillRunning(review)) return { label: "In progress", color: "processing" };
  const decision = getMergeDecision(report, findings, review);
  if (decision.includes("阻塞")) return { label: "Request changes", color: "error" };
  if (findings.length > 0 || decision.includes("修复")) return { label: "Comment", color: "warning" };
  return { label: "Approve", color: "success" };
};

const getOverallPriority = (findings: ReviewFinding[]): string => {
  if (findings.some((item) => ["blocker", "critical"].includes(item.severity))) return "P0";
  if (findings.some((item) => item.severity === "high")) return "P1";
  if (findings.some((item) => item.severity === "medium")) return "P2";
  if (findings.length > 0) return "P3";
  return "无";
};

const getHumanReviewTone = (status: string): { color: string; label: string } => {
  if (status === "requested") return { color: "error", label: "人工裁决中" };
  if (status === "approved") return { color: "success", label: "人工已批准" };
  if (status === "rejected") return { color: "warning", label: "人工已驳回" };
  return { color: "default", label: "无需人工裁决" };
};

const findingTypeLabel = (value: string): string => {
  if (value === "direct_defect") return "直接缺陷";
  if (value === "test_gap") return "测试缺口";
  if (value === "design_concern") return "设计关注";
  return "待验证风险";
};

const getPriority = (finding: ReviewFinding): string => {
  if (["blocker", "critical"].includes(finding.severity)) return "P0";
  if (finding.severity === "high") return "P1";
  if (finding.severity === "medium") return "P2";
  return "P3";
};

const getFindingMergeImpact = (finding: ReviewFinding, needsHumanCount: number): string => {
  if (needsHumanCount > 0 && ["blocker", "critical", "high"].includes(finding.severity)) return "Blocking";
  if (["blocker", "critical", "high"].includes(finding.severity)) return "Should fix before merge";
  return "Non-blocking";
};

const downloadMarkdownReport = (report: ReviewReport, findings: ReviewFinding[]) => {
  const mergeDecision = getMergeDecision(report, findings);
  const priority = getOverallPriority(findings);
  const blockingFindings = findings.filter((finding) =>
    getFindingMergeImpact(finding, report.confidence_summary.needs_human_count).includes("Blocking"),
  );
  const shouldFixFindings = findings.filter(
    (finding) =>
      !blockingFindings.includes(finding) &&
      getFindingMergeImpact(finding, report.confidence_summary.needs_human_count).includes("Should fix"),
  );
  const nonBlockingFindings = findings.filter(
    (finding) =>
      !blockingFindings.includes(finding) &&
      !shouldFixFindings.includes(finding),
  );
  const renderFindingBlock = (finding: ReviewFinding, index: number) => [
    `### ${index + 1}. ${finding.title}`,
    `- 文件: ${finding.file_path}:${finding.line_start}`,
    `- 级别: ${finding.severity}`,
    `- 优先级: ${getPriority(finding)}`,
    `- 合并影响: ${getFindingMergeImpact(finding, report.confidence_summary.needs_human_count)}`,
    `- 提出专家: ${finding.expert_id}`,
    `- 置信度: ${(finding.confidence * 100).toFixed(0)}%`,
    `- 问题说明: ${finding.summary}`,
    `- 修复建议: ${finding.remediation_suggestion || "无"}`,
    "",
    "```diff",
    finding.code_excerpt || `${finding.file_path}:${finding.line_start}`,
    "```",
    "",
  ];
  const lines = [
    `# Code Review 报告 - ${report.review_id}`,
    "",
    `- 状态: ${report.status}`,
    `- 阶段: ${report.phase}`,
    `- 合并建议: ${mergeDecision}`,
    `- 建议优先级: ${priority}`,
    `- 人工裁决状态: ${report.human_review_status}`,
    "",
    "## 摘要",
    report.summary,
    "",
    "## Blocking",
    ...(blockingFindings.length
      ? blockingFindings.flatMap((finding, index) => renderFindingBlock(finding, index))
      : ["- 无", ""]),
    "## Should Fix Before Merge",
    ...(shouldFixFindings.length
      ? shouldFixFindings.flatMap((finding, index) => renderFindingBlock(finding, index))
      : ["- 无", ""]),
    "## Non-blocking",
    ...(nonBlockingFindings.length
      ? nonBlockingFindings.flatMap((finding, index) => renderFindingBlock(finding, index))
      : ["- 无", ""]),
  ].join("\n");

  const blob = new Blob([lines], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${report.review_id}-code-review-report.md`;
  anchor.click();
  URL.revokeObjectURL(url);
};

// 报告摘要卡负责展示最终 verdict、统计指标和导出入口。
const clickableStatistic = (
  title: string,
  value: number,
  onClick?: () => void,
) => (
  <button type="button" className={`summary-stat-button ${onClick ? "summary-stat-button-clickable" : ""}`} onClick={onClick}>
    <Statistic title={title} value={value} />
  </button>
);

const ReportSummaryPanel: React.FC<ReportSummaryPanelProps> = ({ report, findings, issues, review, className, onNavigateToGroup }) => {
  const totalCount = findings.length;
  const formalIssueCount = issues.length;
  const fileCount = new Set(findings.map((item) => item.file_path).filter(Boolean)).size;
  const criticalCount = findings.filter((item) => ["blocker", "critical", "high"].includes(item.severity)).length;
  const issueByFindingId = new Map<string, DebateIssue>();
  for (const issue of issues) {
    for (const findingId of issue.finding_ids) {
      issueByFindingId.set(findingId, issue);
    }
  }
  const verifiedFindingCount = findings.filter((item) => Boolean(issueByFindingId.get(item.finding_id)?.verified)).length;
  const blockingCount = report
    ? findings.filter((item) =>
        getFindingMergeImpact(item, report.confidence_summary.needs_human_count).includes("Blocking"),
      ).length
    : 0;
  const shouldFixCount = report
    ? findings.filter((item) =>
        getFindingMergeImpact(item, report.confidence_summary.needs_human_count).includes("Should fix"),
      ).length
    : 0;
  const topFinding = findings[0] || null;
  const mergeDecision = getMergeDecision(report, findings, review);
  const overallPriority = getOverallPriority(findings);
  const verdict = getVerdict(report, findings, review);
  const humanReview = getHumanReviewTone(report?.human_review_status || "not_required");
  const pendingHumanCount = report?.confidence_summary.needs_human_count || 0;
  const llmUsage = report?.llm_usage_summary || {
    total_calls: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
  };
  const typeCounts = findings.reduce<Record<string, number>>((acc, item) => {
    const key = item.finding_type || "risk_hypothesis";
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});

  return (
    <Card className={`module-card ${className || ""}`.trim()} title="Code Review 报告摘要">
      <Space style={{ marginBottom: 16 }} wrap>
        <Tag color={verdict.color} style={{ fontSize: 13, paddingInline: 10, borderRadius: 999 }}>
          {verdict.label}
        </Tag>
        <Tag color={mergeDecision.includes("阻塞") ? "error" : mergeDecision.includes("修复") ? "warning" : "success"}>
          {mergeDecision}
        </Tag>
        <Tag color="purple">{`建议优先级 ${overallPriority}`}</Tag>
        <Tag color={humanReview.color}>{humanReview.label}</Tag>
        <Tag color={pendingHumanCount > 0 ? "error" : "default"}>{`待人工 ${pendingHumanCount}`}</Tag>
        <Button size="small" onClick={() => report && downloadMarkdownReport(report, findings)} disabled={!report}>
          导出 Markdown 报告
        </Button>
      </Space>
      <Paragraph style={{ marginBottom: 16 }}>
        {isReviewStillRunning(review)
          ? "当前审核仍在运行中，以下统计与建议仅代表阶段性结果，最终结论以任务完成后的收敛结果为准。"
          : report?.summary || "运行审核后，这里会显示最终的 Code Review 报告摘要、风险统计和裁决状态。"}
      </Paragraph>
      <Row gutter={[12, 12]}>
        <Col xs={12} xl={6}>
          {clickableStatistic("审核发现", totalCount, onNavigateToGroup ? () => onNavigateToGroup("all") : undefined)}
        </Col>
        <Col xs={12} xl={6}>
          {clickableStatistic("正式议题", formalIssueCount)}
        </Col>
        <Col xs={12} xl={6}>
          {clickableStatistic("高风险发现", criticalCount, onNavigateToGroup ? () => onNavigateToGroup("should_fix") : undefined)}
        </Col>
        <Col xs={12} xl={6}>
          {clickableStatistic(
            "待人工裁决",
            report?.confidence_summary.needs_human_count || 0,
            onNavigateToGroup ? () => onNavigateToGroup("blocking") : undefined,
          )}
        </Col>
        <Col xs={12} xl={6}>
          {clickableStatistic("已核验问题", verifiedFindingCount, onNavigateToGroup ? () => onNavigateToGroup("verified") : undefined)}
        </Col>
      </Row>
      <Space wrap style={{ marginTop: 16 }}>
        <Tag color={blockingCount > 0 ? "error" : "default"}>{`阻塞合并 ${blockingCount}`}</Tag>
        <Tag color={shouldFixCount > 0 ? "warning" : "default"}>{`建议先修 ${shouldFixCount}`}</Tag>
        <Tag color="success">{`非阻塞 ${Math.max(findings.length - blockingCount - shouldFixCount, 0)}`}</Tag>
        {Object.entries(typeCounts).map(([key, count]) => (
          <Tag key={key}>{`${findingTypeLabel(key)} ${count}`}</Tag>
        ))}
      </Space>
      <Descriptions
        column={2}
        size="small"
        style={{ marginTop: 16 }}
        items={[
          {
            key: "files",
            label: "受影响文件数",
            children: fileCount || 0,
          },
          {
            key: "risk",
            label: "高风险问题数",
            children: criticalCount,
          },
          {
            key: "merge",
            label: "合并建议",
            children: mergeDecision,
          },
          {
            key: "llm_calls",
            label: "LLM 调用次数",
            children: llmUsage.total_calls,
          },
          {
            key: "llm_tokens",
            label: "LLM 总 Token",
            children: llmUsage.total_tokens,
          },
          {
            key: "top",
            label: "首要问题",
            children: topFinding
              ? `${topFinding.file_path}:${topFinding.line_start} · ${topFinding.expert_id}`
              : "尚无问题",
          },
          {
            key: "llm_prompt_tokens",
            label: "Prompt Tokens",
            children: llmUsage.prompt_tokens,
          },
          {
            key: "llm_completion_tokens",
            label: "Completion Tokens",
            children: llmUsage.completion_tokens,
          },
          {
            key: "priority",
            label: "建议优先级",
            children: overallPriority,
          },
        ]}
      />
    </Card>
  );
};

export default ReportSummaryPanel;
