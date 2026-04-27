import React, { useMemo } from "react";
import { Alert, Button, Card, Empty, Space, Tag, Typography } from "antd";

import type { ImpactFile, ImpactPath, ImpactReport, ReviewReport, ReviewSummary, TestScopeRecommendation } from "@/services/api";

const { Paragraph, Text, Title } = Typography;

const riskColor = (value?: string): string => {
  if (value === "high" || value === "critical") return "error";
  if (value === "medium") return "warning";
  if (value === "low") return "success";
  return "default";
};

const priorityColor = (value?: string): string => {
  if (value === "high" || value === "p0" || value === "p1") return "error";
  if (value === "medium" || value === "p2") return "warning";
  if (value === "low" || value === "p3") return "success";
  return "default";
};

const graphStatusLabel = (value?: string): string => {
  if (value === "ready") return "GitNexus 图谱已命中";
  if (value === "running") return "GitNexus 建图中";
  if (value === "missing") return "GitNexus 图谱未命中";
  if (value === "failed") return "GitNexus 图谱不可用";
  return value || "状态未知";
};

const readImpactFailure = (review: ReviewSummary | null): { state: string; error_message?: string } | null => {
  const raw = review?.subject?.metadata?.impact_analysis_progress;
  if (!raw || typeof raw !== "object") return null;
  const payload = raw as Record<string, unknown>;
  return {
    state: String(payload.state || ""),
    error_message: payload.error_message ? String(payload.error_message) : undefined,
  };
};

const formatTime = (value?: string): string => {
  if (!value) return "暂无";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
};

const joinOrFallback = (items: string[], fallback = "暂无"): string => (items.length ? items.join("、") : fallback);

const dedupeStrings = (items: string[]): string[] => Array.from(new Set(items.map((item) => item.trim()).filter(Boolean)));

const buildExecutiveSummary = (impactReport: ImpactReport): string => {
  const moduleText = impactReport.impacted_modules.length ? `重点波及 ${impactReport.impacted_modules.join("、")}` : "";
  const entrypointText = impactReport.external_entrypoints.length
    ? `需要重点关注入口 ${impactReport.external_entrypoints.slice(0, 3).join("、")}`
    : "";
  const testText = impactReport.recommended_test_scope.length
    ? `建议优先补做 ${impactReport.recommended_test_scope
        .slice(0, 2)
        .map((item) => item.scope)
        .join("、")} 的验证`
    : "当前还没有明确的测试范围建议";
  return [moduleText, entrypointText, testText].filter(Boolean).join("；") || "本次改动的关联影响已生成，请结合下方范围和测试建议评估上线风险。";
};

const buildImpactReportMarkdown = (review: ReviewReport | null): string => {
  const impactReport = review?.impact_report;
  if (!review || !impactReport) return "";
  const sections: string[] = [
    `# 关联影响报告 - ${review.review_id}`,
    "",
    `- 风险等级: ${impactReport.risk_level || "unknown"}`,
    `- 图谱状态: ${graphStatusLabel(impactReport.graph_status)}`,
    `- 图谱时间: ${impactReport.graph_indexed_at ? formatTime(impactReport.graph_indexed_at) : "暂无"}`,
    `- 图谱提交: ${impactReport.graph_commit || "暂无"}`,
    "",
    "## 执行摘要",
    buildExecutiveSummary(impactReport),
    "",
    "## 本次变更文件",
    ...(impactReport.changed_files.length ? impactReport.changed_files.map((item) => `- ${item}`) : ["- 暂无"]),
    "",
    "## 影响模块",
    ...(impactReport.impacted_modules.length ? impactReport.impacted_modules.map((item) => `- ${item}`) : ["- 暂无"]),
    "",
    "## 外部入口",
    ...(impactReport.external_entrypoints.length ? impactReport.external_entrypoints.map((item) => `- ${item}`) : ["- 暂无"]),
    "",
    "## 受影响文件",
    ...(impactReport.impacted_files.length
      ? impactReport.impacted_files.map(
          (item) => `- ${item.file_path} | ${item.relationship} | 风险 ${item.risk_level} | ${item.reason}`,
        )
      : ["- 暂无"]),
    "",
    "## 关键影响路径",
    ...((impactReport.impact_paths || []).length
      ? (impactReport.impact_paths || []).map((item) => {
          const pathText = item.path?.length ? item.path.join(" -> ") : `${item.source} -> ${item.target}`;
          return `- ${pathText} | 深度 ${item.depth || 0} | 风险 ${item.risk || "unknown"}`;
        })
      : ["- 暂无"]),
    "",
    "## 建议测试范围",
    ...(impactReport.recommended_test_scope.length
      ? impactReport.recommended_test_scope.flatMap((item) => [
          `### ${item.scope} (${item.priority})`,
          `- 原因: ${item.reason}`,
          `- 关联路径: ${item.paths.length ? item.paths.join("、") : "暂无"}`,
          "",
        ])
      : ["- 暂无", ""]),
    "",
    "## 建议执行项",
    ...(impactReport.must_run_tests.length ? impactReport.must_run_tests.map((item) => `- ${item}`) : ["- 暂无"]),
    "",
    "## 人工确认项",
    ...(impactReport.manual_verification.length ? impactReport.manual_verification.map((item) => `- ${item}`) : ["- 暂无"]),
    "",
    "## 边界说明",
    ...(impactReport.limitations.length ? impactReport.limitations.map((item) => `- ${item}`) : ["- 暂无"]),
  ];
  return sections.join("\n");
};

const downloadImpactReportMarkdown = (review: ReviewReport | null) => {
  const markdown = buildImpactReportMarkdown(review);
  if (!review || !markdown) return;
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${review.review_id}-impact-report.md`;
  anchor.click();
  URL.revokeObjectURL(url);
};

const renderListSection = (title: string, items: string[], emptyText: string) => (
  <section className="impact-report-section">
    <Title level={5}>{title}</Title>
    {items.length ? (
      <div className="impact-report-bullet-list">
        {items.map((item) => (
          <div key={`${title}-${item}`} className="impact-report-bullet-item">
            <span className="impact-report-bullet-dot" />
            <Text>{item}</Text>
          </div>
        ))}
      </div>
    ) : (
      <Text type="secondary">{emptyText}</Text>
    )}
  </section>
);

const renderImpactedFiles = (items: ImpactFile[]) => (
  <section className="impact-report-section">
    <Title level={5}>影响文件</Title>
    {items.length ? (
      <div className="impact-report-file-list">
        {items.map((item) => (
          <div key={`${item.file_path}-${item.relationship}-${item.reason}`} className="impact-report-file-card">
            <div className="impact-report-file-head">
              <Text strong>{item.file_path}</Text>
              <Space size={8} wrap>
                <Tag>{item.relationship || "关联"}</Tag>
                <Tag color={riskColor(item.risk_level)}>{`风险 ${item.risk_level || "unknown"}`}</Tag>
              </Space>
            </div>
            <Paragraph type="secondary" style={{ marginBottom: 0 }}>
              {item.reason || "暂无影响说明"}
            </Paragraph>
          </div>
        ))}
      </div>
    ) : (
      <Text type="secondary">当前没有识别到明确的受影响文件。</Text>
    )}
  </section>
);

const renderImpactPaths = (items: ImpactPath[]) => (
  <section className="impact-report-section">
    <Title level={5}>关键影响路径</Title>
    {items.length ? (
      <div className="impact-report-path-list">
        {items.map((item, index) => {
          const pathText = item.path?.length ? item.path : [item.source, item.target].filter(Boolean);
          return (
            <div key={`${item.source}-${item.target}-${index}`} className="impact-report-path-card">
              <div className="impact-report-path-head">
                <Text strong>{`路径 ${index + 1}`}</Text>
                <Space size={8} wrap>
                  <Tag>{`深度 ${item.depth || 0}`}</Tag>
                  <Tag color={riskColor(item.risk)}>{`风险 ${item.risk || "unknown"}`}</Tag>
                </Space>
              </div>
              <div className="impact-report-path-chain">
                {pathText.map((node, nodeIndex) => (
                  <React.Fragment key={`${node}-${nodeIndex}`}>
                    <span className="impact-report-path-node">{node}</span>
                    {nodeIndex < pathText.length - 1 ? <span className="impact-report-path-arrow">→</span> : null}
                  </React.Fragment>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    ) : (
      <Text type="secondary">当前没有可展示的调用链或依赖路径。</Text>
    )}
  </section>
);

const renderTestScope = (items: TestScopeRecommendation[]) => (
  <section className="impact-report-section">
    <Title level={5}>建议测试范围</Title>
    {items.length ? (
      <div className="impact-report-test-list">
        {items.map((item) => (
          <div key={`${item.scope}-${item.priority}`} className="impact-report-test-card">
            <div className="impact-report-test-head">
              <Text strong>{item.scope}</Text>
              <Tag color={priorityColor(item.priority)}>{item.priority || "unknown"}</Tag>
            </div>
            <Paragraph style={{ marginBottom: 8 }}>{item.reason || "暂无原因说明"}</Paragraph>
            {item.paths.length ? (
              <div className="impact-report-inline-meta">
                <Text type="secondary">关联路径</Text>
                <Text>{item.paths.join("、")}</Text>
              </div>
            ) : null}
          </div>
        ))}
      </div>
    ) : (
      <Text type="secondary">当前没有生成测试范围建议。</Text>
    )}
  </section>
);

type ImpactReportMarkdownPanelProps = {
  report: ReviewReport | null;
  review?: ReviewSummary | null;
  className?: string;
};

const ImpactReportMarkdownPanel: React.FC<ImpactReportMarkdownPanelProps> = ({ report, review, className }) => {
  const impactReport = report?.impact_report || null;
  const markdown = useMemo(() => buildImpactReportMarkdown(report), [report]);
  const impactFailure = useMemo(() => readImpactFailure(review || null), [review]);

  const summaryCards = useMemo(() => {
    if (!impactReport) return [];
    return [
      { label: "风险等级", value: impactReport.risk_level || "unknown", tone: riskColor(impactReport.risk_level) },
      { label: "图谱状态", value: graphStatusLabel(impactReport.graph_status), tone: impactReport.graph_status === "ready" ? "success" : "default" },
      { label: "变更文件", value: `${impactReport.changed_files.length}`, tone: "default" },
      { label: "影响文件", value: `${impactReport.impacted_files.length}`, tone: "default" },
      { label: "测试建议", value: `${impactReport.recommended_test_scope.length}`, tone: "default" },
    ];
  }, [impactReport]);

  const impactedFilePaths = useMemo(
    () => (impactReport ? dedupeStrings(impactReport.impacted_files.map((item) => item.file_path)) : []),
    [impactReport],
  );
  const changedSymbols = useMemo(
    () =>
      impactReport
        ? dedupeStrings(
            impactReport.changed_symbols.map((item) =>
              [item.file_path, item.symbol || item.kind || "unknown"].filter(Boolean).join(" · "),
            ),
          )
        : [],
    [impactReport],
  );

  return (
    <Card
      className={`module-card ${className || ""}`.trim()}
      title="关联影响报告"
      extra={
        <Button size="small" onClick={() => downloadImpactReportMarkdown(report)} disabled={!markdown}>
          导出 MD
        </Button>
      }
    >
      {impactReport ? (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <div className="impact-report-summary-grid">
            {summaryCards.map((item) => (
              <div key={item.label} className={`impact-report-summary-card impact-report-summary-card-${item.tone}`}>
                <Text type="secondary">{item.label}</Text>
                <div className="impact-report-summary-value">{item.value}</div>
              </div>
            ))}
          </div>

          <Alert
            type="info"
            showIcon
            message="报告解读"
            description={buildExecutiveSummary(impactReport)}
          />

          <section className="impact-report-section">
            <Title level={5}>分析基线</Title>
            <div className="impact-report-meta-grid">
              <div className="impact-report-meta-item">
                <Text type="secondary">图谱时间</Text>
                <Text>{formatTime(impactReport.graph_indexed_at)}</Text>
              </div>
              <div className="impact-report-meta-item">
                <Text type="secondary">图谱提交</Text>
                <Text>{impactReport.graph_commit || "暂无"}</Text>
              </div>
              <div className="impact-report-meta-item">
                <Text type="secondary">影响模块</Text>
                <Text>{joinOrFallback(impactReport.impacted_modules)}</Text>
              </div>
              <div className="impact-report-meta-item">
                <Text type="secondary">外部入口</Text>
                <Text>{joinOrFallback(impactReport.external_entrypoints)}</Text>
              </div>
            </div>
          </section>

          {renderListSection("本次变更文件", impactReport.changed_files, "当前没有记录到变更文件。")}
          {renderImpactedFiles(impactReport.impacted_files)}
          {renderListSection("影响文件清单", impactedFilePaths, "当前没有整理出的影响文件清单。")}
          {renderImpactPaths(impactReport.impact_paths)}
          {renderTestScope(impactReport.recommended_test_scope)}
          {renderListSection("变更符号", changedSymbols, "当前没有结构化符号信息。")}
          {renderListSection("建议执行项", impactReport.must_run_tests, "当前没有额外的必跑项。")}
          {renderListSection("人工确认项", impactReport.manual_verification, "当前没有额外的人工确认项。")}
          {renderListSection("边界说明", impactReport.limitations, "当前没有额外的边界说明。")}
        </Space>
      ) : impactFailure?.state === "failed" ? (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type="error"
            showIcon
            message="关联影响报告生成失败"
            description={impactFailure.error_message || "GitNexus 调用失败，本次没有生成可导出的影响报告。"}
          />
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="本次任务没有可用的关联影响报告。请先修复 GitNexus 环境或图谱状态后重试。"
          />
        </Space>
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无关联影响报告。任务完成后，这里会生成本次改动的影响范围与测试建议。"
        />
      )}
    </Card>
  );
};

export default ImpactReportMarkdownPanel;
