import React from "react";
import { Button, Card, Col, Descriptions, Row, Space, Statistic, Tag, Typography } from "antd";

import type { ReviewFinding, ReviewReport } from "@/services/api";

const { Paragraph } = Typography;

type ReportSummaryPanelProps = {
  report: ReviewReport | null;
  findings: ReviewFinding[];
};

const getMergeDecision = (report: ReviewReport | null, findings: ReviewFinding[]): string => {
  const highRiskCount = findings.filter((item) => ["blocker", "critical", "high"].includes(item.severity)).length;
  if ((report?.confidence_summary.needs_human_count || 0) > 0) return "阻塞合并，等待人工确认";
  if (highRiskCount > 0) return "建议修复高风险问题后再合并";
  if (findings.length > 0) return "建议处理问题清单后合并";
  return "可直接合并";
};

const getVerdict = (report: ReviewReport | null, findings: ReviewFinding[]): { label: string; color: string } => {
  const decision = getMergeDecision(report, findings);
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

const ReportSummaryPanel: React.FC<ReportSummaryPanelProps> = ({ report, findings }) => {
  const fileCount = new Set(findings.map((item) => item.file_path).filter(Boolean)).size;
  const criticalCount = findings.filter((item) => ["blocker", "critical", "high"].includes(item.severity)).length;
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
  const mergeDecision = getMergeDecision(report, findings);
  const overallPriority = getOverallPriority(findings);
  const verdict = getVerdict(report, findings);

  return (
    <Card className="module-card" title="Code Review 报告摘要">
      <Space style={{ marginBottom: 16 }} wrap>
        <Tag color={verdict.color} style={{ fontSize: 13, paddingInline: 10, borderRadius: 999 }}>
          {verdict.label}
        </Tag>
        <Tag color={mergeDecision.includes("阻塞") ? "error" : mergeDecision.includes("修复") ? "warning" : "success"}>
          {mergeDecision}
        </Tag>
        <Tag color="purple">{`建议优先级 ${overallPriority}`}</Tag>
        <Button size="small" onClick={() => report && downloadMarkdownReport(report, findings)} disabled={!report}>
          导出 Markdown 报告
        </Button>
      </Space>
      <Paragraph style={{ marginBottom: 16 }}>
        {report?.summary || "运行审核后，这里会显示最终的 Code Review 报告摘要、风险统计和裁决状态。"}
      </Paragraph>
      <Row gutter={[12, 12]}>
        <Col xs={12} xl={6}>
          <Statistic title="高置信发现" value={report?.confidence_summary.high_confidence_count || 0} />
        </Col>
        <Col xs={12} xl={6}>
          <Statistic title="已辩论议题" value={report?.confidence_summary.debated_issue_count || 0} />
        </Col>
        <Col xs={12} xl={6}>
          <Statistic title="待人工确认" value={report?.confidence_summary.needs_human_count || 0} />
        </Col>
        <Col xs={12} xl={6}>
          <Statistic title="已核验证据" value={report?.confidence_summary.verified_issue_count || 0} suffix={<Tag color="processing">judge</Tag>} />
        </Col>
      </Row>
      <Space wrap style={{ marginTop: 16 }}>
        <Tag color={blockingCount > 0 ? "error" : "default"}>{`Blocking ${blockingCount}`}</Tag>
        <Tag color={shouldFixCount > 0 ? "warning" : "default"}>{`Should Fix ${shouldFixCount}`}</Tag>
        <Tag color="success">{`Non-blocking ${Math.max(findings.length - blockingCount - shouldFixCount, 0)}`}</Tag>
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
            key: "human",
            label: "人工裁决状态",
            children: report?.human_review_status || "not_required",
          },
          {
            key: "merge",
            label: "合并建议",
            children: mergeDecision,
          },
          {
            key: "top",
            label: "首要问题",
            children: topFinding
              ? `${topFinding.file_path}:${topFinding.line_start} · ${topFinding.expert_id}`
              : "尚无问题",
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
