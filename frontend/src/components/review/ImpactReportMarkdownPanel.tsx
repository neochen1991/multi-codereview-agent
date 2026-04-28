import React, { useMemo } from "react";
import { Alert, Button, Card, Empty, Space, Tag, Typography } from "antd";

import type {
  ImpactFile,
  ImpactGraph,
  ImpactGraphEdge,
  ImpactGraphNode,
  ImpactPath,
  ImpactReport,
  ReviewReport,
  ReviewSummary,
  TestScopeRecommendation,
} from "@/services/api";

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
  if (impactReport.report_summary?.trim()) return impactReport.report_summary.trim();
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

const buildAnalysisBasis = (impactReport: ImpactReport): string[] => {
  const items = [
    "先通过 list_repos 确认当前仓库已经按 GitNexus 官方方式注册。",
    "再调用 detect_changes(repo, scope=all) 识别本次改动的受影响文件、模块和候选测试范围。",
    "最后对关键变更符号调用 impact(repo, target)，补充调用链和 blast radius。",
  ];
  return [...items, ...impactReport.limitations];
};

const buildRelationshipInsights = (impactReport: ImpactReport): string[] => {
  const insights: string[] = [];
  for (const item of impactReport.impacted_files) {
    if (item.relationship === "changed") continue;
    insights.push(`${item.file_path} 被标记为 ${item.relationship}，原因是：${item.reason}`);
  }
  for (const scope of impactReport.recommended_test_scope) {
    if (!scope.paths.length) continue;
    insights.push(`${scope.scope} 需要优先验证，主要覆盖：${scope.paths.join("、")}`);
  }
  for (const path of impactReport.impact_paths.slice(0, 6)) {
    const chain = path.path?.length ? path.path.join(" -> ") : [path.source, path.target].filter(Boolean).join(" -> ");
    if (chain) {
      insights.push(`影响链路：${chain}`);
    }
  }
  if (!insights.length && impactReport.impacted_modules.length) {
    insights.push(`当前至少识别到模块级波及：${impactReport.impacted_modules.join("、")}`);
  }
  return dedupeStrings(insights).slice(0, 8);
};

const buildRiskDistribution = (items: ImpactFile[]): Array<{ label: string; count: number; tone: string }> => {
  const high = items.filter((item) => priorityRank(item.risk_level) >= 3).length;
  const medium = items.filter((item) => priorityRank(item.risk_level) === 2).length;
  const low = items.filter((item) => priorityRank(item.risk_level) <= 1).length;
  return [
    { label: "高风险", count: high, tone: "error" },
    { label: "中风险", count: medium, tone: "warning" },
    { label: "低风险", count: low, tone: "success" },
  ];
};

type GraphLayoutNode = ImpactGraphNode & {
  x: number;
  y: number;
  width: number;
  height: number;
  column: number;
};

type GraphLayoutEdge = ImpactGraphEdge & {
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
};

type GraphLayout = {
  width: number;
  height: number;
  nodes: GraphLayoutNode[];
  edges: GraphLayoutEdge[];
};

const roleLabel = (value?: string): string => {
  if (value === "changed" || value === "start") return "变更起点";
  if (value === "impacted") return "影响落点";
  if (value === "incoming") return "上游调用";
  if (value === "outgoing") return "下游调用";
  if (value === "process") return "业务流程";
  if (value === "file") return "受影响文件";
  if (value === "module") return "受影响模块";
  if (value === "test") return "测试建议";
  if (value === "context") return "上下文";
  return "关联节点";
};

const nodeTone = (node: Pick<ImpactGraphNode, "role" | "risk">): string => {
  if (node.role === "changed" || node.role === "start") return "primary";
  if (node.role === "impacted") return "accent";
  if (node.role === "process") return "process";
  if (node.role === "test") return "accent";
  if (node.role === "module") return "process";
  if (node.risk === "high" || node.risk === "critical") return "danger";
  return "default";
};

const buildFallbackGraph = (paths: ImpactPath[]): ImpactGraph => {
  const nodes = new Map<string, ImpactGraphNode>();
  const edges = new Map<string, ImpactGraphEdge>();
  paths.forEach((item) => {
    const values = item.path?.length ? item.path : [item.source, item.target].filter(Boolean);
    values.forEach((value, index) => {
      const normalized = String(value || "").trim();
      if (!normalized || nodes.has(normalized)) return;
      nodes.set(normalized, {
        node_id: normalized,
        label: normalized,
        kind: "symbol",
        file_path: "",
        role: index === 0 ? "start" : index === values.length - 1 ? "impacted" : "path",
        risk: item.risk || "medium",
      });
    });
    values.forEach((value, index) => {
      if (index === values.length - 1) return;
      const source = String(value || "").trim();
      const target = String(values[index + 1] || "").trim();
      if (!source || !target) return;
      edges.set(`${source}=>${target}`, {
        source,
        target,
        relationship: "calls",
        confidence: 1,
      });
    });
  });
  return { nodes: Array.from(nodes.values()), edges: Array.from(edges.values()) };
};

const buildGraphLayout = (impactReport: ImpactReport): GraphLayout | null => {
  const rawGraph = impactReport.impact_graph?.nodes?.length ? impactReport.impact_graph : buildFallbackGraph(impactReport.impact_paths || []);
  if (!rawGraph.nodes.length) return null;
  const nodes = rawGraph.nodes.filter((item) => item.node_id && item.label);
  const edges = rawGraph.edges.filter((item) => item.source && item.target && item.source !== item.target);
  const rank = new Map<string, number>();
  nodes.forEach((node) => {
    if (node.role === "changed" || node.role === "start") rank.set(node.node_id, 0);
  });
  if (!rank.size && nodes[0]) rank.set(nodes[0].node_id, 0);
  for (let step = 0; step < nodes.length * 3; step += 1) {
    let moved = false;
    edges.forEach((edge) => {
      const sourceRank = rank.get(edge.source);
      const targetRank = rank.get(edge.target);
      if (sourceRank == null) return;
      const nextRank = sourceRank + 1;
      if (targetRank == null || targetRank < nextRank) {
        rank.set(edge.target, nextRank);
        moved = true;
      }
    });
    if (!moved) break;
  }
  nodes.forEach((node) => {
    if (rank.has(node.node_id)) return;
    if (node.role === "process" || node.role === "incoming") {
      rank.set(node.node_id, 0);
      return;
    }
    if (node.role === "file") {
      rank.set(node.node_id, 3);
      return;
    }
    if (node.role === "module" || node.role === "test") {
      rank.set(node.node_id, 4);
      return;
    }
    if (node.role === "outgoing" || node.role === "impacted") {
      rank.set(node.node_id, 2);
      return;
    }
    rank.set(node.node_id, 1);
  });

  const grouped = new Map<number, ImpactGraphNode[]>();
  nodes.forEach((node) => {
    const column = rank.get(node.node_id) || 0;
    const bucket = grouped.get(column) || [];
    bucket.push(node);
    grouped.set(column, bucket);
  });

  const columnOrder = Array.from(grouped.keys()).sort((a, b) => a - b);
  const roleOrder: Record<string, number> = {
    changed: 0,
    start: 0,
    process: 1,
    incoming: 1,
    context: 2,
    path: 3,
    outgoing: 4,
    impacted: 5,
    file: 6,
    module: 7,
    test: 8,
  };
  const nodeWidth = 220;
  const nodeHeight = 76;
  const xGap = 260;
  const yGap = 110;
  const padding = 28;
  const positioned = new Map<string, GraphLayoutNode>();
  let maxRows = 1;
  columnOrder.forEach((column) => {
    const bucket = (grouped.get(column) || []).slice().sort((a, b) => {
      const roleGap = (roleOrder[a.role || "path"] ?? 99) - (roleOrder[b.role || "path"] ?? 99);
      if (roleGap !== 0) return roleGap;
      return a.label.localeCompare(b.label);
    });
    maxRows = Math.max(maxRows, bucket.length);
    bucket.forEach((node, rowIndex) => {
      positioned.set(node.node_id, {
        ...node,
        column,
        width: nodeWidth,
        height: nodeHeight,
        x: padding + column * xGap,
        y: padding + rowIndex * yGap,
      });
    });
  });

  const layoutEdges: GraphLayoutEdge[] = edges
    .map((edge) => {
      const source = positioned.get(edge.source);
      const target = positioned.get(edge.target);
      if (!source || !target) return null;
      return {
        ...edge,
        fromX: source.x + source.width,
        fromY: source.y + source.height / 2,
        toX: target.x,
        toY: target.y + target.height / 2,
      };
    })
    .filter(Boolean) as GraphLayoutEdge[];

  return {
    width: padding * 2 + Math.max(columnOrder.length - 1, 0) * xGap + nodeWidth,
    height: padding * 2 + Math.max(maxRows - 1, 0) * yGap + nodeHeight,
    nodes: Array.from(positioned.values()),
    edges: layoutEdges,
  };
};

const priorityRank = (value?: string): number => {
  if (value === "high" || value === "p0" || value === "p1") return 3;
  if (value === "medium" || value === "p2") return 2;
  if (value === "low" || value === "p3") return 1;
  return 0;
};

const renderTemplateMarkdown = (markdown: string): React.ReactNode[] => {
  const lines = String(markdown || "").split(/\r?\n/);
  const nodes: React.ReactNode[] = [];
  let bulletBuffer: string[] = [];
  let paragraphBuffer: string[] = [];

  const flushBullets = () => {
    if (!bulletBuffer.length) return;
    nodes.push(
      <ul key={`template-ul-${nodes.length}`} className="template-preview-list">
        {bulletBuffer.map((item, index) => (
          <li key={`${item}-${index}`}>{item}</li>
        ))}
      </ul>,
    );
    bulletBuffer = [];
  };

  const flushParagraph = () => {
    if (!paragraphBuffer.length) return;
    nodes.push(
      <Paragraph key={`template-p-${nodes.length}`} className="template-preview-paragraph">
        {paragraphBuffer.join(" ")}
      </Paragraph>,
    );
    paragraphBuffer = [];
  };

  lines.forEach((line, index) => {
    const text = line.trim();
    if (!text) {
      flushBullets();
      flushParagraph();
      return;
    }
    if (text.startsWith("### ")) {
      flushBullets();
      flushParagraph();
      nodes.push(<Title key={`template-h3-${index}`} level={5}>{text.slice(4)}</Title>);
      return;
    }
    if (text.startsWith("## ")) {
      flushBullets();
      flushParagraph();
      nodes.push(<Title key={`template-h2-${index}`} level={4}>{text.slice(3)}</Title>);
      return;
    }
    if (text.startsWith("# ")) {
      flushBullets();
      flushParagraph();
      nodes.push(<Title key={`template-h1-${index}`} level={3}>{text.slice(2)}</Title>);
      return;
    }
    if (text.startsWith("- ")) {
      flushParagraph();
      bulletBuffer.push(text.slice(2));
      return;
    }
    flushBullets();
    paragraphBuffer.push(text);
  });

  flushBullets();
  flushParagraph();
  return nodes;
};

const priorityLabel = (value?: string): string => {
  if (value === "high" || value === "p0" || value === "p1") return "优先执行";
  if (value === "medium" || value === "p2") return "建议执行";
  if (value === "low" || value === "p3") return "补充关注";
  return "一般";
};

const buildImpactReportMarkdown = (review: ReviewReport | null): string => {
  const impactReport = review?.impact_report;
  if (!review || !impactReport) return "";
  if (impactReport.llm_markdown?.trim()) return impactReport.llm_markdown.trim();
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
    "## 分析依据与边界",
    ...buildAnalysisBasis(impactReport).map((item) => `- ${item}`),
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

const renderMiniStats = (title: string, items: Array<{ label: string; count: number; tone: string }>) => (
  <section className="impact-report-section">
    <Title level={5}>{title}</Title>
    <div className="impact-report-mini-stats">
      {items.map((item) => (
        <div key={item.label} className={`impact-report-mini-stat impact-report-mini-stat-${item.tone}`}>
          <Text type="secondary">{item.label}</Text>
          <div className="impact-report-mini-stat-value">{item.count}</div>
        </div>
      ))}
    </div>
  </section>
);

const renderImpactedFiles = (items: ImpactFile[]) => (
  <section className="impact-report-section">
    <Title level={5}>影响文件</Title>
    {items.length ? (
      <div className="impact-report-file-groups">
        {[
          { key: "high", title: "高风险影响", matcher: (item: ImpactFile) => priorityRank(item.risk_level) >= 3 },
          { key: "medium", title: "中风险影响", matcher: (item: ImpactFile) => priorityRank(item.risk_level) === 2 },
          { key: "low", title: "低风险影响", matcher: (item: ImpactFile) => priorityRank(item.risk_level) <= 1 },
        ]
          .map((group) => ({
            ...group,
            items: items.filter(group.matcher),
          }))
          .filter((group) => group.items.length)
          .map((group) => (
            <div key={group.key} className="impact-report-file-group">
              <div className="impact-report-test-group-head">
                <Title level={5}>{group.title}</Title>
                <Tag>{group.items.length}</Tag>
              </div>
              <div className="impact-report-file-list">
                {group.items.map((item) => (
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
              <div className="impact-report-path-timeline">
                {pathText.map((node, nodeIndex) => (
                  <div key={`${node}-timeline-${nodeIndex}`} className="impact-report-path-timeline-item">
                    <div className="impact-report-path-timeline-dot" />
                    <div className="impact-report-path-timeline-line" hidden={nodeIndex === pathText.length - 1} />
                    <div className="impact-report-path-timeline-content">
                      <Text strong>{node}</Text>
                      <Text type="secondary">{nodeIndex === 0 ? "影响起点" : nodeIndex === pathText.length - 1 ? "影响落点" : "中间传播节点"}</Text>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    ) : (
      <Alert
        type="warning"
        showIcon
        message="本次图谱结果没有返回显式调用链"
        description="当前报告仍可用于判断影响范围和测试建议；如果需要更细的跨模块传播路径，可以在 GitNexus 图谱更新后重新分析。"
      />
    )}
  </section>
);

const renderImpactGraph = (impactReport: ImpactReport) => {
  const layout = buildGraphLayout(impactReport);
  return (
    <section className="impact-report-section">
      <Title level={5}>关键调用链路图</Title>
      {layout ? (
        <div className="impact-graph-scroll">
          <div className="impact-graph-canvas" style={{ width: layout.width, height: layout.height }}>
            <svg className="impact-graph-svg" width={layout.width} height={layout.height} viewBox={`0 0 ${layout.width} ${layout.height}`}>
              <defs>
                <marker id="impact-graph-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">
                  <path d="M0,0 L10,5 L0,10 z" fill="#94a3b8" />
                </marker>
              </defs>
              {layout.edges.map((edge) => {
                const midX = (edge.fromX + edge.toX) / 2;
                const path = `M ${edge.fromX} ${edge.fromY} C ${midX} ${edge.fromY}, ${midX} ${edge.toY}, ${edge.toX} ${edge.toY}`;
                return (
                  <g key={`${edge.source}-${edge.target}-${edge.relationship}`}>
                    <path d={path} className="impact-graph-edge" markerEnd="url(#impact-graph-arrow)" />
                    <text x={midX} y={(edge.fromY + edge.toY) / 2 - 6} className="impact-graph-edge-label">
                      {edge.relationship}
                    </text>
                  </g>
                );
              })}
            </svg>
            {layout.nodes.map((node) => (
              <div
                key={node.node_id}
                className={`impact-graph-node impact-graph-node-${nodeTone(node)}`}
                style={{ left: node.x, top: node.y, width: node.width, height: node.height }}
              >
                <div className="impact-graph-node-head">
                  <span className="impact-graph-node-role">{roleLabel(node.role)}</span>
                  {node.risk ? <span className="impact-graph-node-risk">{node.risk}</span> : null}
                </div>
                <div className="impact-graph-node-title">{node.label}</div>
                {node.file_path ? <div className="impact-graph-node-meta">{node.file_path}</div> : null}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <Text type="secondary">当前还没有足够的链路节点，暂时无法绘制调用图。</Text>
      )}
    </section>
  );
};

const renderTestScope = (items: TestScopeRecommendation[]) => (
  <section className="impact-report-section">
    <Title level={5}>建议测试范围</Title>
    {items.length ? (
      <div className="impact-report-test-groups">
        {[
          { key: "high", title: "必须先跑", matcher: (item: TestScopeRecommendation) => priorityRank(item.priority) >= 3 },
          {
            key: "medium",
            title: "建议补跑",
            matcher: (item: TestScopeRecommendation) => priorityRank(item.priority) === 2,
          },
          { key: "low", title: "补充关注", matcher: (item: TestScopeRecommendation) => priorityRank(item.priority) <= 1 },
        ]
          .map((group) => ({
            ...group,
            items: items.filter(group.matcher),
          }))
          .filter((group) => group.items.length)
          .map((group) => (
            <div key={group.key} className="impact-report-test-group">
              <div className="impact-report-test-group-head">
                <Title level={5}>{group.title}</Title>
                <Tag>{group.items.length}</Tag>
              </div>
              <div className="impact-report-test-list">
                {group.items.map((item) => (
                  <div key={`${item.scope}-${item.priority}`} className="impact-report-test-card">
                    <div className="impact-report-test-head">
                      <Text strong>{item.scope}</Text>
                      <Tag color={priorityColor(item.priority)}>{priorityLabel(item.priority)}</Tag>
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
            </div>
          ))}
      </div>
    ) : (
      <Text type="secondary">当前没有生成测试范围建议。</Text>
    )}
  </section>
);

const renderExecutionChecklist = (items: TestScopeRecommendation[], commands: string[], manualVerification: string[]) => {
  const checklist = [
    ...items
      .slice()
      .sort((a, b) => priorityRank(b.priority) - priorityRank(a.priority))
      .map((item) => ({
        title: item.scope,
        detail: item.reason || "补充相关测试验证。",
        kind: priorityLabel(item.priority),
      })),
    ...commands.slice(0, 4).map((item) => ({
      title: item,
      detail: "建议纳入本次回归执行集。",
      kind: "执行命令",
    })),
    ...manualVerification.slice(0, 3).map((item) => ({
      title: item,
      detail: "这部分需要研发或测试人工补判断。",
      kind: "人工确认",
    })),
  ].slice(0, 8);

  return (
    <section className="impact-report-section">
      <Title level={5}>建议执行顺序</Title>
      {checklist.length ? (
        <div className="impact-report-checklist">
          {checklist.map((item, index) => (
            <div key={`${item.kind}-${item.title}-${index}`} className="impact-report-checklist-item">
              <div className="impact-report-checklist-index">{index + 1}</div>
              <div className="impact-report-checklist-content">
                <div className="impact-report-checklist-head">
                  <Text strong>{item.title}</Text>
                  <Tag>{item.kind}</Tag>
                </div>
                <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                  {item.detail}
                </Paragraph>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Text type="secondary">当前没有生成明确的执行顺序建议。</Text>
      )}
    </section>
  );
};

const renderReportHeadline = (impactReport: ImpactReport) => {
  const headline = impactReport.report_summary?.trim()
    ? impactReport.report_summary.trim()
    : impactReport.recommended_test_scope.length > 0
      ? `本次改动已识别出 ${impactReport.recommended_test_scope.length} 类优先测试范围，建议先围绕高风险影响面执行回归。`
      : "本次改动已完成影响分析，请结合受影响范围安排后续验证。";
  return (
    <section className="impact-report-hero">
      <div className="impact-report-hero-main">
        <Text type="secondary">关联影响结论</Text>
        <Title level={4}>{headline}</Title>
        <Paragraph style={{ marginBottom: 0 }}>
          {buildExecutiveSummary(impactReport)}
        </Paragraph>
      </div>
      <div className="impact-report-hero-side">
        <div className="impact-report-hero-badge">
          <Text type="secondary">本次整体风险</Text>
          <div className="impact-report-hero-risk">
            <Tag color={riskColor(impactReport.risk_level)}>{impactReport.risk_level || "unknown"}</Tag>
          </div>
        </div>
      </div>
    </section>
  );
};

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

  const analysisBasis = useMemo(() => (impactReport ? buildAnalysisBasis(impactReport) : []), [impactReport]);
  const relationshipInsights = useMemo(
    () => (impactReport ? buildRelationshipInsights(impactReport) : []),
    [impactReport],
  );
  const reportKeyPoints = useMemo(() => dedupeStrings(impactReport?.key_impact_points || []), [impactReport]);
  const reportTestFocus = useMemo(() => dedupeStrings(impactReport?.test_focus || []), [impactReport]);
  const riskDistribution = useMemo(() => (impactReport ? buildRiskDistribution(impactReport.impacted_files) : []), [impactReport]);
  const topAttentionItems = useMemo(() => {
    if (!impactReport) return [];
    const impactTargets = impactReport.impacted_files
      .slice()
      .sort((a, b) => priorityRank(a.risk_level) - priorityRank(b.risk_level))
      .reverse()
      .slice(0, 3)
      .map((item) => `${item.file_path}：${item.reason}`);
    const testTargets = impactReport.recommended_test_scope
      .slice()
      .sort((a, b) => priorityRank(a.priority) - priorityRank(b.priority))
      .reverse()
      .slice(0, 2)
      .map((item) => `${item.scope}：${item.reason}`);
    return dedupeStrings([...impactTargets, ...testTargets]).slice(0, 5);
  }, [impactReport]);

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
        impactReport.llm_markdown?.trim() ? (
          <div className="template-preview-rendered">{renderTemplateMarkdown(impactReport.llm_markdown)}</div>
        ) : (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            {renderReportHeadline(impactReport)}

            {renderListSection("报告重点", reportKeyPoints, "当前没有额外的重点结论。")}
            {renderListSection("优先测试建议", reportTestFocus, "当前没有额外的测试结论。")}

            <div className="impact-report-summary-grid">
              {summaryCards.map((item) => (
                <div key={item.label} className={`impact-report-summary-card impact-report-summary-card-${item.tone}`}>
                  <Text type="secondary">{item.label}</Text>
                  <div className="impact-report-summary-value">{item.value}</div>
                </div>
              ))}
            </div>

            {renderListSection("本次最值得优先关注", topAttentionItems, "当前没有额外的重点关注项。")}
            {renderListSection("关联影响解读", relationshipInsights, "当前没有识别出更细的传播关系。")}
            {renderImpactGraph(impactReport)}

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
            {renderMiniStats("影响风险分布", riskDistribution)}
            {renderImpactedFiles(impactReport.impacted_files)}
            {renderImpactPaths(impactReport.impact_paths)}
            {renderTestScope(impactReport.recommended_test_scope)}
            {renderExecutionChecklist(
              impactReport.recommended_test_scope,
              impactReport.must_run_tests,
              impactReport.manual_verification,
            )}
            {renderListSection("建议执行项", impactReport.must_run_tests, "当前没有额外的必跑项。")}
            {renderListSection("人工确认项", impactReport.manual_verification, "当前没有额外的人工确认项。")}
            {renderListSection("分析依据与边界", analysisBasis, "当前没有额外的分析说明。")}
          </Space>
        )
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
