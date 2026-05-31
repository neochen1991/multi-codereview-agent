import React, { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Empty, Space, Tag, Typography, message } from "antd";

import {
  reviewApi,
  type ImpactFile,
  type ImpactGraph,
  type ImpactGraphEdge,
  type ImpactGraphNode,
  type ImpactPath,
  type ImpactReport,
  type ImpactSymbol,
  type ReviewReport,
  type ReviewSummary,
  type TestScopeRecommendation,
} from "@/services/api";
import { humanizeReviewText } from "@/utils/displayText";

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

const confidenceLabelText = (value?: string): string => {
  if (value === "confirmed") return "图谱确认";
  if (value === "historically_confirmed") return "历史确认";
  if (value === "needs_verification") return "需复核";
  if (value === "inferred") return "推断";
  if (value === "candidate") return "候选";
  return value || "候选";
};

const confidenceLabelColor = (value?: string): string => {
  if (value === "confirmed" || value === "historically_confirmed") return "success";
  if (value === "needs_verification") return "warning";
  if (value === "candidate" || value === "inferred") return "processing";
  return "default";
};

const graphStatusLabel = (value?: string): string => {
  if (value === "ready") return "GitNexus 图谱已命中";
  if (value === "running") return "GitNexus 建图中";
  if (value === "degraded") return "GitNexus 图谱信息不完整";
  if (value === "missing") return "GitNexus 图谱未命中";
  if (value === "failed") return "GitNexus 图谱不可用";
  return value || "状态待确认";
};

const countTestActionItems = (impactReport: ImpactReport): number =>
  dedupeStrings([
    ...impactReport.recommended_test_scope.map((item) => item.scope || item.reason),
    ...impactReport.must_run_tests,
  ]).length;

const buildGitNexusRepairTips = (impactReport: ImpactReport): string[] => {
  if (impactReport.graph_status === "ready") return [];
  return [
    "在目标仓库目录执行 gitnexus analyze，等待建图完成后重新运行检视。",
    "Windows PowerShell 如安装路径包含空格，建议在设置页配置 GITNEXUS_BIN 或 GITNEXUS_MCP_COMMAND，并使用带引号的完整路径。",
    "确认 GITNEXUS_HOME、registry 和当前 repo_path 指向同一份本地仓库，避免盘符或路径大小写不一致。",
  ];
};

const readImpactReportFromReview = (review: ReviewSummary | null | undefined): ImpactReport | null => {
  const raw = review?.subject?.metadata?.impact_report;
  if (!raw || typeof raw !== "object") return null;
  return raw as ImpactReport;
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

const cleanImpactReportText = (value?: string | null): string =>
  humanizeReviewText(value || "")
    .replace(/已降级为候选范围/g, "按候选影响范围展示")
    .replace(/报告已降级为候选影响分析/g, "报告按候选影响范围展示")
    .replace(/未确认可用仓库/g, "暂未拿到可用仓库")
    .replace(/人工确认真实调用链/g, "结合代码核对真实调用链")
    .replace(/更多影响需结合代码人工确认/g, "更多影响需结合代码进一步核对")
    .replace(/降级/g, "改用备用方案")
    .replace(/未确认/g, "待核对");

const joinOrFallback = (items: string[], fallback = "暂无"): string => (items.length ? items.map(cleanImpactReportText).join("、") : fallback);

const dedupeStrings = (items: string[]): string[] => Array.from(new Set(items.map(cleanImpactReportText).map((item) => item.trim()).filter(Boolean)));

const buildExecutiveSummary = (impactReport: ImpactReport): string => {
  if (impactReport.report_summary?.trim()) return cleanImpactReportText(impactReport.report_summary).trim();
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
  return [moduleText, entrypointText, testText].filter(Boolean).join("；") || "本次改动的关联影响已生成，优先按下方范围和测试建议评估上线风险。";
};

const buildAnalysisBasis = (impactReport: ImpactReport): string[] => {
  const items = [
    "先通过 list_repos 确认当前仓库已经按 GitNexus 官方方式注册。",
    "再调用 detect_changes(repo, scope=all) 识别本次改动的受影响文件、模块和候选测试范围。",
    "最后对关键变更符号调用 impact(repo, target)，补充调用链和 blast radius。",
  ];
  return [...items, ...impactReport.limitations.map(cleanImpactReportText)];
};

const buildRelationshipInsights = (impactReport: ImpactReport): string[] => {
  const insights: string[] = [];
  for (const item of impactReport.impacted_files) {
    if (item.relationship === "changed") continue;
    insights.push(`${item.file_path} 被标记为${relationshipLabel(item.relationship)}，原因是：${item.reason}`);
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
  for (const link of impactReport.impact_issue_links || []) {
    insights.push(`检视问题 ${link.issue_id} 与影响目标 ${link.impact_target} 相关：${link.reason || link.issue_title}`);
  }
  if (!insights.length && impactReport.impacted_modules.length) {
    insights.push(`当前至少识别到模块级波及：${impactReport.impacted_modules.join("、")}`);
  }
  return dedupeStrings(insights).slice(0, 8);
};

const buildTargetDiagnostics = (
  impactReport: ImpactReport,
): Array<{ title: string; items: string[]; tone: "default" | "success" | "warning" }> => [
  {
    title: "已请求查询目标",
    items: (impactReport.queried_targets || []).map(cleanImpactReportText),
    tone: "default",
  },
  {
    title: "Context 命中目标",
    items: (impactReport.successful_context_targets || []).map(cleanImpactReportText),
    tone: "success",
  },
  {
    title: "Impact 命中目标",
    items: (impactReport.successful_impact_targets || []).map(cleanImpactReportText),
    tone: "success",
  },
  {
    title: "过滤的无效目标",
    items: (impactReport.skipped_invalid_targets || []).map(cleanImpactReportText),
    tone: "warning",
  },
  {
    title: "Context 跳过目标",
    items: (impactReport.skipped_missing_context_targets || []).map(cleanImpactReportText),
    tone: "warning",
  },
  {
    title: "Impact 跳过目标",
    items: (impactReport.skipped_missing_impact_targets || []).map(cleanImpactReportText),
    tone: "warning",
  },
];

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

type ImpactQualitySummary = {
  verdict: string;
  graphTone: string;
  hitRate: number;
  hitLabel: string;
  skippedCount: number;
  highRiskCount: number;
  executableTestCount: number;
  blindSpotCount: number;
  nextActions: string[];
};

type ImpactChecklistItem = {
  title: string;
  detail: string;
  kind: string;
};

type ImpactFeedbackLabel = "confirmed" | "false_positive";

type ImpactFeedbackState = {
  submittingKey: string;
  submittedByTarget: Record<string, ImpactFeedbackLabel>;
  disabled: boolean;
};

type ImpactFeedbackTargetType = "impact_path" | "impact_file" | "test_scope";

type ImpactFeedbackHandler = (targetType: ImpactFeedbackTargetType, targetKey: string, label: ImpactFeedbackLabel) => void;

const buildImpactFeedbackTargetId = (targetType: string, targetKey: string): string => `${targetType}:${targetKey}`;

const buildImpactFeedbackActionId = (targetType: string, targetKey: string, label: string): string =>
  `${targetType}:${targetKey}:${label}`;

const buildImpactPathTargetKey = (item: ImpactPath, index: number): string => {
  const nodes = item.path?.length ? item.path : [item.source, item.target].filter(Boolean);
  const chain = nodes.map((node) => String(node || "").trim()).filter(Boolean).join(" -> ");
  return chain || `impact_path_${index + 1}`;
};

const buildImpactFileTargetKey = (item: ImpactFile, index: number): string =>
  [item.file_path, item.relationship, item.reason].map((value) => String(value || "").trim()).filter(Boolean).join(" | ") ||
  `impact_file_${index + 1}`;

const buildTestScopeTargetKey = (item: TestScopeRecommendation, index: number): string =>
  [item.scope, item.priority, item.paths?.join(","), item.reason].map((value) => String(value || "").trim()).filter(Boolean).join(" | ") ||
  `test_scope_${index + 1}`;

const renderImpactFeedbackActions = (
  targetType: ImpactFeedbackTargetType,
  targetKey: string,
  label: string,
  onFeedback?: ImpactFeedbackHandler,
  feedbackState?: ImpactFeedbackState,
) => {
  if (!onFeedback) return null;
  const targetId = buildImpactFeedbackTargetId(targetType, targetKey);
  const confirmedActionId = buildImpactFeedbackActionId(targetType, targetKey, "confirmed");
  const falsePositiveActionId = buildImpactFeedbackActionId(targetType, targetKey, "false_positive");
  const submittedLabel = feedbackState?.submittedByTarget[targetId];
  return (
    <div className="impact-report-feedback-actions">
      <Text type="secondary">{label}</Text>
      <Space size={8} wrap>
        {submittedLabel ? (
          <Tag color={submittedLabel === "confirmed" ? "success" : "warning"}>
            {submittedLabel === "confirmed" ? "已确认" : "已标记误报"}
          </Tag>
        ) : null}
        <Button
          size="small"
          type={submittedLabel === "confirmed" ? "primary" : "default"}
          loading={feedbackState?.submittingKey === confirmedActionId}
          disabled={feedbackState?.disabled || Boolean(feedbackState?.submittingKey)}
          onClick={() => onFeedback(targetType, targetKey, "confirmed")}
        >
          确认
        </Button>
        <Button
          size="small"
          danger={submittedLabel === "false_positive"}
          loading={feedbackState?.submittingKey === falsePositiveActionId}
          disabled={feedbackState?.disabled || Boolean(feedbackState?.submittingKey)}
          onClick={() => onFeedback(targetType, targetKey, "false_positive")}
        >
          误报
        </Button>
      </Space>
    </div>
  );
};

const buildImpactQualitySummary = (impactReport: ImpactReport): ImpactQualitySummary => {
  const requestedCount = impactReport.queried_targets?.length || 0;
  const contextHitCount = impactReport.successful_context_targets?.length || 0;
  const impactHitCount = impactReport.successful_impact_targets?.length || 0;
  const skippedCount =
    (impactReport.skipped_invalid_targets?.length || 0) +
    (impactReport.skipped_missing_context_targets?.length || 0) +
    (impactReport.skipped_missing_impact_targets?.length || 0);
  const hitBase = requestedCount || contextHitCount + impactHitCount + skippedCount;
  const hitRate = hitBase > 0 ? Math.round(((contextHitCount + impactHitCount) / (hitBase * 2)) * 100) : 0;
  const highRiskCount = impactReport.impacted_files.filter((item) => priorityRank(item.risk_level) >= 3).length;
  const executableTestCount = countTestActionItems(impactReport);
  const blindSpotCount = skippedCount + impactReport.limitations.length + (impactReport.graph_status === "ready" ? 0 : 1);
  const graphTone = impactReport.graph_status === "ready" && hitRate > 0 ? "success" : impactReport.graph_status === "failed" ? "error" : "warning";
  const nextActions = dedupeStrings([
    highRiskCount > 0 ? `先验证 ${highRiskCount} 个高风险影响文件` : "",
    executableTestCount > 0 ? `按优先级执行 ${executableTestCount} 条建议测试项` : "",
    blindSpotCount > 0 ? `补齐 ${blindSpotCount} 个图谱/上下文盲区` : "",
    impactReport.manual_verification.length > 0 ? `人工确认 ${impactReport.manual_verification.length} 项边界条件` : "",
  ]).slice(0, 3);
  const verdict =
    impactReport.graph_status !== "ready" || hitRate === 0
      ? "图谱未完全可用，影响结论需要人工补充确认。"
      : highRiskCount > 0
        ? "已命中高风险影响面，合并前应优先跑完核心回归。"
        : executableTestCount > 0
          ? "影响范围可执行，建议按建议测试项完成验证后再合并。"
          : "当前影响面较轻，但仍需确认报告覆盖范围。";
  return {
    verdict,
    graphTone,
    hitRate,
    hitLabel: hitBase > 0 ? `${hitRate}%` : impactReport.changed_symbols.length ? `符号 ${impactReport.changed_symbols.length}` : "暂无",
    skippedCount,
    highRiskCount,
    executableTestCount,
    blindSpotCount,
    nextActions,
  };
};

const buildExecutionChecklistItems = (
  items: TestScopeRecommendation[],
  commands: string[],
  manualVerification: string[],
): ImpactChecklistItem[] =>
  [
    ...items
      .slice()
      .sort((a, b) => priorityRank(b.priority) - priorityRank(a.priority))
      .map((item) => ({
        title: cleanImpactReportText(item.scope),
        detail: cleanImpactReportText(item.reason || "补充相关测试验证。"),
        kind: priorityLabel(item.priority),
      })),
    ...commands.slice(0, 4).map((item) => ({
      title: cleanImpactReportText(item),
      detail: "建议纳入本次回归执行集。",
      kind: "执行命令",
    })),
    ...manualVerification.slice(0, 3).map((item) => ({
      title: cleanImpactReportText(item),
      detail: "这部分需要研发或测试补充核对。",
      kind: "补充核对",
    })),
  ].slice(0, 8);

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

const hasUnresolvedTemplateVariables = (markdown?: string): boolean =>
  /\{\{\s*[^{}]+\s*\}\}/.test(String(markdown || ""));

const renderInlineTemplateText = (text: string): React.ReactNode[] => {
  const source = String(text || "");
  const tokens: React.ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null = pattern.exec(source);
  while (match) {
    if (match.index > lastIndex) {
      tokens.push(source.slice(lastIndex, match.index));
    }
    const token = match[0] || "";
    if (token.startsWith("**") && token.endsWith("**")) {
      tokens.push(
        <strong key={`strong-${match.index}`} className="template-preview-strong">
          {token.slice(2, -2)}
        </strong>,
      );
    } else if (token.startsWith("`") && token.endsWith("`")) {
      tokens.push(
        <code key={`code-${match.index}`} className="template-preview-inline-code">
          {token.slice(1, -1)}
        </code>,
      );
    } else {
      tokens.push(token);
    }
    lastIndex = match.index + token.length;
    match = pattern.exec(source);
  }
  if (lastIndex < source.length) {
    tokens.push(source.slice(lastIndex));
  }
  return tokens.length ? tokens : [source];
};

type MermaidApi = typeof import("mermaid").default;

let mermaidConfigured = false;
let mermaidPromise: Promise<MermaidApi> | null = null;

const normalizeMermaidNodeLabel = (value: string): string => String(value || "").replace(/\s+/g, " ").trim();

const extractMermaidNodeLabels = (chart: string): string[] => {
  const labels = Array.from(chart.matchAll(/\["([^"]+)"\]/g)).map((match) => normalizeMermaidNodeLabel(match[1] || ""));
  return Array.from(new Set(labels.filter(Boolean)));
};

const extractMermaidCenterLabel = (chart: string): string =>
  normalizeMermaidNodeLabel(chart.match(/\["([^"]+)"\]/)?.[1] || "");

const buildMermaidSearchTerms = (label: string): string[] => {
  const normalized = normalizeMermaidNodeLabel(label);
  if (!normalized) return [];
  const raw = normalized.replace(/^文件:\s*/, "").replace(/^测试:\s*/, "");
  const terms = [normalized, raw];
  if (raw.includes(" -> ")) {
    raw.split(" -> ").forEach((item) => terms.push(item.trim()));
  }
  if (raw.includes(".")) {
    const pieces = raw.split(".").map((item) => item.trim()).filter(Boolean);
    if (pieces.length) {
      terms.push(pieces[pieces.length - 1]);
    }
  }
  return Array.from(new Set(terms.map((item) => item.trim()).filter(Boolean)));
};

const clearMermaidLinkedHighlights = (scope: ParentNode | null) => {
  if (!scope) return;
  scope.querySelectorAll(".template-preview-linked-highlight").forEach((node) => {
    node.classList.remove("template-preview-linked-highlight");
  });
  scope.querySelectorAll(".template-preview-mermaid-block-linked").forEach((node) => {
    node.classList.remove("template-preview-mermaid-block-linked");
  });
};

const contentSelector =
  ".template-preview-h1, .template-preview-h2, .template-preview-h3, .template-preview-h4, .template-preview-paragraph, .template-preview-list li, .template-preview-table td, .template-preview-quote div";

const collectMermaidLocalCandidates = (block: Element | null): HTMLElement[] => {
  if (!block || !block.parentElement) return [];
  const results: HTMLElement[] = [];
  const pushIfMatch = (element: Element | null) => {
    if (!(element instanceof HTMLElement)) return;
    if (element.matches(contentSelector)) {
      results.push(element);
    }
    results.push(...Array.from(element.querySelectorAll<HTMLElement>(contentSelector)));
  };
  pushIfMatch(block.previousElementSibling);
  let cursor = block.nextElementSibling;
  while (cursor) {
    if (
      cursor.classList.contains("template-preview-mermaid-block") ||
      cursor.classList.contains("template-preview-divider") ||
      cursor.classList.contains("template-preview-h4")
    ) {
      break;
    }
    pushIfMatch(cursor);
    cursor = cursor.nextElementSibling;
  }
  return results;
};

const scrollToMermaidRelatedContent = (scope: ParentNode | null, label: string, block?: Element | null) => {
  if (!scope) return;
  clearMermaidLinkedHighlights(scope);
  const terms = buildMermaidSearchTerms(label);
  if (!terms.length) return;
  const localCandidates = collectMermaidLocalCandidates(block || null);
  const candidates = localCandidates.length
    ? localCandidates
    : Array.from(scope.querySelectorAll<HTMLElement>(contentSelector));
  const matched = candidates.filter((element) => {
    const text = normalizeMermaidNodeLabel(element.textContent || "");
    return terms.some((term) => text.includes(term));
  });
  if (!matched.length) return;
  matched.slice(0, 6).forEach((element) => {
    element.classList.add("template-preview-linked-highlight");
  });
  matched[0]?.scrollIntoView({ behavior: "smooth", block: "center" });
};

const findLinkedMermaidBlock = (scope: ParentNode | null, currentBlock: Element | null, label: string): HTMLElement | null => {
  if (!scope) return null;
  const terms = buildMermaidSearchTerms(label);
  if (!terms.length) return null;
  const blocks = Array.from(scope.querySelectorAll<HTMLElement>(".template-preview-mermaid-block[data-center-label]"));
  return (
    blocks.find((block) => {
      if (currentBlock && block === currentBlock) return false;
      const centerLabel = normalizeMermaidNodeLabel(block.dataset.centerLabel || "");
      return terms.some((term) => centerLabel.includes(term) || term.includes(centerLabel));
    }) || null
  );
};

const ensureMermaid = async (): Promise<MermaidApi> => {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((module) => module.default);
  }
  const mermaid = await mermaidPromise;
  if (mermaidConfigured) return mermaid;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "loose",
    theme: "neutral",
    flowchart: { useMaxWidth: true, htmlLabels: false, curve: "basis" },
  });
  mermaidConfigured = true;
  return mermaid;
};

const MermaidBlock = ({ chart }: { chart: string }) => {
  const blockRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string>("");
  const [zoom, setZoom] = useState<number>(1);
  const [activeNode, setActiveNode] = useState<string>("");
  const [linkedBlockLabel, setLinkedBlockLabel] = useState<string>("");
  const nodeLabels = useMemo(() => extractMermaidNodeLabels(chart), [chart]);
  const centerLabel = useMemo(() => extractMermaidCenterLabel(chart), [chart]);

  useEffect(() => {
    let disposed = false;
    const render = async () => {
      if (!containerRef.current) return;
      setError("");
      const id = `impact-mermaid-${Math.random().toString(36).slice(2, 10)}`;
      try {
        const mermaid = await ensureMermaid();
        const result = await mermaid.render(id, chart);
        if (disposed || !containerRef.current) return;
        containerRef.current.innerHTML = result.svg;
      } catch (err) {
        if (disposed || !containerRef.current) return;
        containerRef.current.innerHTML = "";
        setError(err instanceof Error ? err.message : "Mermaid 渲染失败");
      }
    };
    void render();
    return () => {
      disposed = true;
    };
  }, [chart]);

  useEffect(() => {
    if (!containerRef.current) return;
    const nodes = Array.from(containerRef.current.querySelectorAll<SVGGElement>(".node"));
    nodes.forEach((node) => {
      const label = normalizeMermaidNodeLabel(node.textContent || "");
      node.classList.toggle("template-preview-mermaid-node-active", Boolean(activeNode) && label === activeNode);
      node.classList.toggle("template-preview-mermaid-node-clickable", Boolean(label));
      node.setAttribute("data-node-label", label);
      node.style.cursor = label ? "pointer" : "default";
      node.onclick = () => {
        if (!label) return;
        setActiveNode((current) => (current === label ? "" : label));
      };
    });
  }, [chart, activeNode]);

  useEffect(() => {
    const scope = containerRef.current?.closest(".template-preview-rendered");
    const block = blockRef.current;
    if (!scope) return;
    if (!activeNode) {
      clearMermaidLinkedHighlights(scope);
      setLinkedBlockLabel("");
      return;
    }
    scrollToMermaidRelatedContent(scope, activeNode, block);
    const linkedBlock = findLinkedMermaidBlock(scope, block, activeNode);
    if (linkedBlock) {
      linkedBlock.classList.add("template-preview-mermaid-block-linked");
      linkedBlock.scrollIntoView({ behavior: "smooth", block: "center" });
      setLinkedBlockLabel(normalizeMermaidNodeLabel(linkedBlock.dataset.centerLabel || ""));
      return;
    }
    setLinkedBlockLabel("");
  }, [activeNode]);

  return (
    <div ref={blockRef} className="template-preview-mermaid-block" data-center-label={centerLabel}>
      <div className="template-preview-mermaid-toolbar">
        <div className="template-preview-mermaid-legend" aria-label="调用链图例">
          <span className="template-preview-mermaid-legend-item">
            <span className="template-preview-mermaid-legend-swatch template-preview-mermaid-legend-swatch-changed" />
            变更方法
          </span>
          <span className="template-preview-mermaid-legend-item">
            <span className="template-preview-mermaid-legend-swatch template-preview-mermaid-legend-swatch-downstream" />
            调用链节点
          </span>
          <span className="template-preview-mermaid-legend-item">
            <span className="template-preview-mermaid-legend-swatch template-preview-mermaid-legend-swatch-file" />
            受影响文件
          </span>
          <span className="template-preview-mermaid-legend-item">
            <span className="template-preview-mermaid-legend-swatch template-preview-mermaid-legend-swatch-test" />
            建议测试项
          </span>
        </div>
        <Space size={8} className="template-preview-mermaid-actions">
          <Text type="secondary" className="template-preview-mermaid-zoom-label">
            {Math.round(zoom * 100)}%
          </Text>
          <Button size="small" onClick={() => setZoom((value) => Math.max(0.7, Number((value - 0.1).toFixed(2))))}>
            缩小
          </Button>
          <Button size="small" onClick={() => setZoom(1)}>
            重置
          </Button>
          <Button
            size="small"
            type="primary"
            ghost
            onClick={() => setZoom((value) => Math.min(1.8, Number((value + 0.1).toFixed(2))))}
          >
            放大
          </Button>
        </Space>
      </div>
      <div className="template-preview-mermaid-canvas">
        <div
          className="template-preview-mermaid-zoom-surface"
          style={{ transform: `scale(${zoom})`, transformOrigin: "top center" }}
        >
          <div ref={containerRef} />
        </div>
      </div>
      <div className="template-preview-mermaid-node-panel">
        <div className="template-preview-mermaid-node-panel-header">
          <Text strong>图内节点</Text>
          <Text type="secondary">
            {activeNode ? `当前聚焦：${activeNode}` : "点击节点或下方名称，可高亮查看"}
          </Text>
        </div>
        {linkedBlockLabel ? (
          <Alert
            type="info"
            showIcon
            className="template-preview-mermaid-link-alert"
            message={`已联动到另一张调用图：${linkedBlockLabel}`}
          />
        ) : null}
        <div className="template-preview-mermaid-node-list">
          {nodeLabels.map((label) => (
            <Button
              key={label}
              size="small"
              type={activeNode === label ? "primary" : "default"}
              ghost={activeNode === label}
              className="template-preview-mermaid-node-chip"
              onClick={() => setActiveNode((current) => (current === label ? "" : label))}
            >
              {label}
            </Button>
          ))}
        </div>
      </div>
      {error ? (
        <Alert
          type="warning"
          showIcon
          className="template-preview-mermaid-fallback"
          message="Mermaid 图渲染失败，已保留原始图代码"
          description={<pre className="template-preview-mermaid-code">{chart}</pre>}
        />
      ) : null}
    </div>
  );
};

const renderTemplateMarkdown = (markdown: string): React.ReactNode[] => {
  const lines = String(markdown || "").split(/\r?\n/);
  const nodes: React.ReactNode[] = [];
  let bulletBuffer: string[] = [];
  let orderedBuffer: string[] = [];
  let paragraphBuffer: string[] = [];
  let quoteBuffer: string[] = [];
  let tableBuffer: string[] = [];
  let mermaidBuffer: string[] | null = null;

  const normalizeTableRow = (value: string): string[] => {
    const text = value.trim().replace(/^｜/, "|").replace(/｜$/g, "|").replace(/｜/g, "|");
    return text
      .split("|")
      .map((item) => item.trim())
      .filter(Boolean);
  };

  const flushTable = () => {
    if (!tableBuffer.length) return;
    const rows = tableBuffer.map(normalizeTableRow).filter((row) => row.length);
    if (!rows.length) {
      tableBuffer = [];
      return;
    }
    const dividerPattern = /^:?-{2,}:?$/;
    const header = rows[0];
    const body = rows.slice(1).filter((row) => !row.every((cell) => dividerPattern.test(cell.replace(/\s+/g, ""))));
    nodes.push(
      <div key={`template-table-${nodes.length}`} className="template-preview-table-wrap">
        <table className="template-preview-table">
          <thead>
            <tr>
              {header.map((cell, index) => (
                <th key={`th-${index}`}>{renderInlineTemplateText(cell)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, rowIndex) => (
              <tr key={`tr-${rowIndex}`}>
                {row.map((cell, cellIndex) => (
                  <td key={`td-${rowIndex}-${cellIndex}`}>{renderInlineTemplateText(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>,
    );
    tableBuffer = [];
  };

  const flushBullets = () => {
    if (!bulletBuffer.length) return;
    nodes.push(
      <ul key={`template-ul-${nodes.length}`} className="template-preview-list">
        {bulletBuffer.map((item, index) => (
          <li key={`${item}-${index}`}>{renderInlineTemplateText(item)}</li>
        ))}
      </ul>,
    );
    bulletBuffer = [];
  };

  const flushOrdered = () => {
    if (!orderedBuffer.length) return;
    nodes.push(
      <ol key={`template-ol-${nodes.length}`} className="template-preview-list template-preview-list-ordered">
        {orderedBuffer.map((item, index) => (
          <li key={`${item}-${index}`}>{renderInlineTemplateText(item)}</li>
        ))}
      </ol>,
    );
    orderedBuffer = [];
  };

  const flushParagraph = () => {
    if (!paragraphBuffer.length) return;
    nodes.push(
      <Paragraph key={`template-p-${nodes.length}`} className="template-preview-paragraph">
        {renderInlineTemplateText(paragraphBuffer.join(" "))}
      </Paragraph>,
    );
    paragraphBuffer = [];
  };

  const flushQuote = () => {
    if (!quoteBuffer.length) return;
    nodes.push(
      <blockquote key={`template-quote-${nodes.length}`} className="template-preview-quote">
        {quoteBuffer.map((item, index) => (
          <div key={`${item}-${index}`}>{renderInlineTemplateText(item)}</div>
        ))}
      </blockquote>,
    );
    quoteBuffer = [];
  };

  lines.forEach((line, index) => {
    const text = line.trim();
    if (mermaidBuffer) {
      if (text === "```") {
        flushTable();
        flushQuote();
        flushBullets();
        flushOrdered();
        flushParagraph();
        nodes.push(<MermaidBlock key={`template-mermaid-${index}`} chart={mermaidBuffer.join("\n")} />);
        mermaidBuffer = null;
      } else {
        mermaidBuffer.push(line);
      }
      return;
    }
    if (!text) {
      flushTable();
      flushQuote();
      flushBullets();
      flushOrdered();
      flushParagraph();
      return;
    }
    if (text === "```mermaid") {
      flushTable();
      flushQuote();
      flushBullets();
      flushOrdered();
      flushParagraph();
      mermaidBuffer = [];
      return;
    }
    if (/^[|｜].*[|｜]$/.test(text)) {
      flushQuote();
      flushBullets();
      flushOrdered();
      flushParagraph();
      tableBuffer.push(text);
      return;
    }
    flushTable();
    if (text === "---") {
      flushQuote();
      flushBullets();
      flushOrdered();
      flushParagraph();
      nodes.push(<hr key={`template-hr-${index}`} className="template-preview-divider" />);
      return;
    }
    if (text.startsWith(">")) {
      flushBullets();
      flushOrdered();
      flushParagraph();
      quoteBuffer.push(text.replace(/^>\s?/, ""));
      return;
    }
    flushQuote();
    if (text.startsWith("#### ")) {
      flushBullets();
      flushOrdered();
      flushParagraph();
      nodes.push(<Title key={`template-h4-${index}`} level={5} className="template-preview-h4">{renderInlineTemplateText(text.slice(5))}</Title>);
      return;
    }
    if (text.startsWith("### ")) {
      flushBullets();
      flushOrdered();
      flushParagraph();
      nodes.push(<Title key={`template-h3-${index}`} level={5} className="template-preview-h3">{renderInlineTemplateText(text.slice(4))}</Title>);
      return;
    }
    if (text.startsWith("## ")) {
      flushBullets();
      flushOrdered();
      flushParagraph();
      nodes.push(<Title key={`template-h2-${index}`} level={4} className="template-preview-h2">{renderInlineTemplateText(text.slice(3))}</Title>);
      return;
    }
    if (text.startsWith("# ")) {
      flushBullets();
      flushOrdered();
      flushParagraph();
      nodes.push(<Title key={`template-h1-${index}`} level={3} className="template-preview-h1">{renderInlineTemplateText(text.slice(2))}</Title>);
      return;
    }
    if (text.startsWith("- ")) {
      flushParagraph();
      flushOrdered();
      bulletBuffer.push(text.slice(2));
      return;
    }
    if (/^\d+\.\s+/.test(text)) {
      flushParagraph();
      flushBullets();
      orderedBuffer.push(text.replace(/^\d+\.\s+/, ""));
      return;
    }
    flushBullets();
    flushOrdered();
    paragraphBuffer.push(text);
  });

  flushTable();
  flushQuote();
  flushBullets();
  flushOrdered();
  flushParagraph();
  const tailMermaid = mermaidBuffer as string[] | null;
  if (tailMermaid && tailMermaid.length > 0) {
    nodes.push(<MermaidBlock key={`template-mermaid-tail-${nodes.length}`} chart={tailMermaid.join("\n")} />);
  }
  return nodes;
};

const priorityLabel = (value?: string): string => {
  if (value === "high" || value === "p0" || value === "p1") return "优先执行";
  if (value === "medium" || value === "p2") return "建议执行";
  if (value === "low" || value === "p3") return "补充关注";
  return "一般";
};

const riskLabel = (value?: string): string => {
  if (value === "critical") return "严重";
  if (value === "high") return "高";
  if (value === "medium") return "中";
  if (value === "low") return "低";
  return "未知";
};

const relationshipLabel = (value?: string): string => {
  if (value === "changed") return "本次修改";
  if (value === "test_candidate") return "候选测试";
  if (value === "impacted") return "受影响";
  if (value === "caller") return "上游调用";
  if (value === "callee") return "下游调用";
  if (value === "impact_path") return "影响路径";
  return value || "关联";
};

const symbolKindLabel = (value?: string): string => {
  if (value === "function" || value === "method") return "方法";
  if (value === "class") return "类";
  if (value === "field") return "字段";
  if (value === "symbol") return "符号";
  return value || "符号";
};

const buildImpactReportMarkdown = (review: ReviewReport | null): string => {
  const impactReport = review?.impact_report;
  if (!review || !impactReport) return "";
  if (impactReport.llm_markdown?.trim() && !hasUnresolvedTemplateVariables(impactReport.llm_markdown)) {
    return cleanImpactReportText(impactReport.llm_markdown).trim();
  }
  const sections: string[] = [
    `# 影响范围报告 - ${review.review_id}`,
    "",
    `- 风险等级: ${riskLabel(impactReport.risk_level)}`,
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
    "## 图谱查询明细",
    ...(buildTargetDiagnostics(impactReport).flatMap((group) => [
      `### ${group.title}`,
      ...(group.items.length ? group.items.map((item) => `- ${item}`) : ["- 暂无"]),
      "",
    ])),
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
          (item) => `- ${item.file_path} | ${relationshipLabel(item.relationship)} | 风险 ${riskLabel(item.risk_level)} | ${item.reason}`,
        )
      : ["- 暂无"]),
    "",
    "## 关键影响路径",
    ...((impactReport.impact_paths || []).length
      ? (impactReport.impact_paths || []).map((item) => {
          const pathText = item.path?.length ? item.path.join(" -> ") : `${item.source} -> ${item.target}`;
          const confidence = item.confidence_label ? ` | 可信度 ${item.confidence_label}` : "";
          const reason = item.confirmation_reason ? ` | ${item.confirmation_reason}` : "";
          return `- ${pathText} | 深度 ${item.depth || 0} | 风险 ${riskLabel(item.risk)}${confidence}${reason}`;
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
  return cleanImpactReportText(sections.join("\n"));
};

const buildExecutionChecklistMarkdown = (
  reviewId: string,
  impactReport: ImpactReport,
  checklist: ImpactChecklistItem[],
): string => {
  const lines = [
    `## GitNexus 影响分析执行清单`,
    "",
    `- Review ID: ${reviewId}`,
    `- 风险等级: ${riskLabel(impactReport.risk_level)}`,
    `- 图谱状态: ${graphStatusLabel(impactReport.graph_status)}`,
    `- 影响文件: ${impactReport.impacted_files.length}`,
    `- 建议测试项: ${countTestActionItems(impactReport)}`,
    "",
    "### 建议执行顺序",
    ...(
      checklist.length
        ? checklist.map((item, index) => `${index + 1}. [ ] ${item.title}（${item.kind}）\n   - ${item.detail}`)
        : ["- 暂无明确执行项"]
    ),
    "",
    "### 人工确认项",
    ...(
      impactReport.manual_verification.length
        ? impactReport.manual_verification.map((item) => `- [ ] ${item}`)
        : ["- 暂无额外人工确认项"]
    ),
  ];
  return lines.join("\n");
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
            <Text>{cleanImpactReportText(item)}</Text>
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

const formatImpactSymbol = (item: ImpactSymbol): string => {
  const symbol = [item.container, item.symbol].filter(Boolean).join(".");
  return symbol || item.file_path || "未识别符号";
};

const renderChangedSymbols = (items: ImpactSymbol[]) => (
  <section className="impact-report-section">
    <div className="impact-report-section-head">
      <Title level={5}>变更符号</Title>
      <Tag color={items.length ? "processing" : "default"}>{items.length}</Tag>
    </div>
    {items.length ? (
      <div className="impact-report-symbol-grid">
        {items.slice(0, 12).map((item) => (
          <div key={`${item.file_path}-${item.container}-${item.symbol}-${item.line_start}`} className="impact-report-symbol-card">
            <div className="impact-report-symbol-main">
              <Text strong>{formatImpactSymbol(item)}</Text>
              <Space size={6} wrap>
                <Tag>{symbolKindLabel(item.kind || "symbol")}</Tag>
                {item.line_start ? <Tag color="default">L{item.line_start}</Tag> : null}
              </Space>
            </div>
            <Text type="secondary" className="impact-report-symbol-path">
              {item.file_path}
            </Text>
          </div>
        ))}
      </div>
    ) : (
      <Text type="secondary">当前没有从 diff 或图谱中提取到变更符号。</Text>
    )}
  </section>
);

const renderImpactQualitySummary = (summary: ImpactQualitySummary) => (
  <section className={`impact-report-quality-strip impact-report-quality-strip-${summary.graphTone}`}>
    <div className="impact-report-quality-main">
      <Text type="secondary">影响分析质量判读</Text>
      <div className="impact-report-quality-verdict">{summary.verdict}</div>
      <Space size={6} wrap>
        {summary.nextActions.length ? (
          summary.nextActions.map((item) => (
            <Tag key={item} color="processing">
              {item}
            </Tag>
          ))
        ) : (
          <Tag>暂无额外动作</Tag>
        )}
      </Space>
    </div>
    <div className="impact-report-quality-metrics">
      <div>
        <Text type="secondary">目标命中</Text>
        <strong>{summary.hitLabel}</strong>
      </div>
      <div>
        <Text type="secondary">高风险</Text>
        <strong>{summary.highRiskCount}</strong>
      </div>
      <div>
        <Text type="secondary">建议测试项</Text>
        <strong>{summary.executableTestCount}</strong>
      </div>
      <div>
        <Text type="secondary">盲区</Text>
        <strong>{summary.blindSpotCount}</strong>
      </div>
    </div>
  </section>
);

const renderTargetDiagnostics = (impactReport: ImpactReport) => {
  const groups = buildTargetDiagnostics(impactReport).filter((group) => group.items.length);
  return (
    <section className="impact-report-section">
      <Title level={5}>图谱查询明细</Title>
      {groups.length ? (
        <div className="impact-report-file-groups">
          {groups.map((group) => (
            <div key={group.title} className="impact-report-file-group">
              <div className="impact-report-test-group-head">
                <Title level={5}>{group.title}</Title>
                <Tag color={group.tone === "success" ? "success" : group.tone === "warning" ? "warning" : "default"}>
                  {group.items.length}
                </Tag>
              </div>
              <div className="impact-report-bullet-list">
                {group.items.map((item) => (
                  <div key={`${group.title}-${item}`} className="impact-report-bullet-item">
                    <span className="impact-report-bullet-dot" />
                    <Text>{item}</Text>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Text type="secondary">当前没有额外的图谱查询明细。</Text>
      )}
    </section>
  );
};

const renderCollapsibleTargetDiagnostics = (impactReport: ImpactReport) => {
  const groups = buildTargetDiagnostics(impactReport).filter((group) => group.items.length);
  const requestedCount = impactReport.queried_targets?.length || 0;
  const contextHitCount = impactReport.successful_context_targets?.length || 0;
  const impactHitCount = impactReport.successful_impact_targets?.length || 0;
  const skippedCount =
    (impactReport.skipped_invalid_targets?.length || 0) +
    (impactReport.skipped_missing_context_targets?.length || 0) +
    (impactReport.skipped_missing_impact_targets?.length || 0);
  const hasStructuredDiagnostics =
    requestedCount > 0 || contextHitCount > 0 || impactHitCount > 0 || skippedCount > 0 || groups.length > 0;

  if (!hasStructuredDiagnostics) {
    return (
      <details className="impact-report-diagnostics-details">
        <summary className="impact-report-diagnostics-summary">
          <div className="impact-report-diagnostics-summary-copy">
            <span>图谱查询明细</span>
            <Text type="secondary" className="impact-report-diagnostics-summary-text">
              本次报告未记录 target 命中明细，当前仅保留最终影响分析结果。
            </Text>
          </div>
          <Space size={8}>
            <Tag>0 组</Tag>
            <Tag>{graphStatusLabel(impactReport.graph_status)}</Tag>
          </Space>
        </summary>
      </details>
    );
  }

  const totalItems = groups.reduce((sum, group) => sum + group.items.length, 0);
  return (
    <details className="impact-report-diagnostics-details">
      <summary className="impact-report-diagnostics-summary">
        <div className="impact-report-diagnostics-summary-copy">
          <span>图谱查询明细</span>
          <Text type="secondary" className="impact-report-diagnostics-summary-text">
            {`已请求 ${requestedCount} 个 target，Context 命中 ${contextHitCount} 个，Impact 命中 ${impactHitCount} 个，跳过 ${skippedCount} 个`}
          </Text>
        </div>
        <Space size={8}>
          <Tag>{`${groups.length} 组`}</Tag>
          <Tag color="processing">{`${totalItems} 项`}</Tag>
        </Space>
      </summary>
      <div className="impact-report-diagnostics-body">{renderTargetDiagnostics(impactReport)}</div>
    </details>
  );
};

const renderImpactedFiles = (
  items: ImpactFile[],
  onFeedback?: ImpactFeedbackHandler,
  feedbackState?: ImpactFeedbackState,
) => (
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
                {group.items.map((item, index) => {
                  const targetKey = buildImpactFileTargetKey(item, index);
                  return (
                    <div key={`${item.file_path}-${item.relationship}-${item.reason}`} className="impact-report-file-card">
                      <div className="impact-report-file-head">
                        <Text strong>{item.file_path}</Text>
                        <Space size={8} wrap>
                          <Tag>{relationshipLabel(item.relationship)}</Tag>
                          <Tag color={riskColor(item.risk_level)}>{`风险 ${riskLabel(item.risk_level)}`}</Tag>
                        </Space>
                      </div>
                      <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                        {item.reason || "暂无影响说明"}
                      </Paragraph>
                      {renderImpactFeedbackActions("impact_file", targetKey, "反馈这个影响文件", onFeedback, feedbackState)}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
      </div>
    ) : (
      <Text type="secondary">当前没有识别到明确的受影响文件。</Text>
    )}
  </section>
);

const renderImpactPaths = (
  items: ImpactPath[],
  onFeedback?: ImpactFeedbackHandler,
  feedbackState?: ImpactFeedbackState,
) => (
  <section className="impact-report-section">
    <Title level={5}>关键影响路径</Title>
    {items.length ? (
      <div className="impact-report-path-list">
        {items.map((item, index) => {
          const pathText = item.path?.length ? item.path : [item.source, item.target].filter(Boolean);
          const targetKey = buildImpactPathTargetKey(item, index);
          const targetId = buildImpactFeedbackTargetId("impact_path", targetKey);
          const submittedLabel = feedbackState?.submittedByTarget[targetId];
          return (
            <div key={`${item.source}-${item.target}-${index}`} className="impact-report-path-card">
              <div className="impact-report-path-head">
                <Text strong>{`路径 ${index + 1}`}</Text>
                <Space size={8} wrap>
                  <Tag>{`深度 ${item.depth || 0}`}</Tag>
                  <Tag color={riskColor(item.risk)}>{`风险 ${riskLabel(item.risk)}`}</Tag>
                  <Tag color={confidenceLabelColor(item.confidence_label)}>
                    {confidenceLabelText(item.confidence_label)}
                  </Tag>
                  {submittedLabel ? (
                    <Tag color={submittedLabel === "confirmed" ? "success" : "warning"}>
                      {submittedLabel === "confirmed" ? "已确认" : "已标记误报"}
                    </Tag>
                  ) : null}
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
              {item.confirmation_reason ? (
                <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                  {item.confirmation_reason}
                </Paragraph>
              ) : null}
              {renderImpactFeedbackActions("impact_path", targetKey, "反馈这条影响路径", onFeedback, feedbackState)}
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
                      {relationshipLabel(edge.relationship)}
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

const renderTestScope = (
  items: TestScopeRecommendation[],
  onFeedback?: ImpactFeedbackHandler,
  feedbackState?: ImpactFeedbackState,
) => (
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
                {group.items.map((item, index) => {
                  const targetKey = buildTestScopeTargetKey(item, index);
                  return (
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
                      {renderImpactFeedbackActions("test_scope", targetKey, "反馈这个测试建议", onFeedback, feedbackState)}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
      </div>
    ) : (
      <Text type="secondary">当前没有生成测试范围建议。</Text>
    )}
  </section>
);

const renderExecutionChecklist = (checklist: ImpactChecklistItem[], onCopy?: () => void) => {
  return (
    <section className="impact-report-section">
      <div className="impact-report-section-head">
        <Title level={5}>建议执行顺序</Title>
        {checklist.length && onCopy ? (
          <Button size="small" onClick={onCopy}>
            复制清单
          </Button>
        ) : null}
      </div>
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
    ? cleanImpactReportText(impactReport.report_summary).trim()
    : impactReport.recommended_test_scope.length > 0
      ? `本次改动已识别出 ${impactReport.recommended_test_scope.length} 类优先测试范围，建议先围绕高风险影响面执行回归。`
      : "本次改动已完成影响分析，建议按受影响范围安排后续验证。";
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
            <Tag color={riskColor(impactReport.risk_level)}>{riskLabel(impactReport.risk_level)}</Tag>
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
  const impactReport = report?.impact_report || readImpactReportFromReview(review);
  const [impactFeedbackSubmittingKey, setImpactFeedbackSubmittingKey] = useState("");
  const [impactFeedbackSubmittedByTarget, setImpactFeedbackSubmittedByTarget] = useState<Record<string, ImpactFeedbackLabel>>({});
  const effectiveReport = useMemo(
    () => report || (impactReport ? ({ review_id: review?.review_id || "impact-report", impact_report: impactReport } as ReviewReport) : null),
    [impactReport, report, review?.review_id],
  );
  const markdown = useMemo(() => buildImpactReportMarkdown(effectiveReport), [effectiveReport]);
  const impactFailure = useMemo(() => readImpactFailure(review || null), [review]);
  const hasConfirmedGraphFacts = Boolean(
    impactReport &&
      impactReport.graph_status === "ready" &&
      ((impactReport.successful_context_targets?.length || 0) > 0 || (impactReport.successful_impact_targets?.length || 0) > 0),
  );

  const summaryCards = useMemo(() => {
    if (!impactReport) return [];
    return [
      { label: "风险等级", value: riskLabel(impactReport.risk_level), tone: riskColor(impactReport.risk_level) },
      { label: "图谱状态", value: graphStatusLabel(impactReport.graph_status), tone: impactReport.graph_status === "ready" ? "success" : "default" },
      { label: "变更文件", value: `${impactReport.changed_files.length}`, tone: "default" },
      { label: "影响文件", value: `${impactReport.impacted_files.length}`, tone: "default" },
      { label: "建议测试项", value: `${countTestActionItems(impactReport)}`, tone: "default" },
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
  const impactQualitySummary = useMemo(
    () => (impactReport ? buildImpactQualitySummary(impactReport) : null),
    [impactReport],
  );
  const gitNexusRepairTips = useMemo(
    () => (impactReport ? buildGitNexusRepairTips(impactReport) : []),
    [impactReport],
  );
  const executionChecklist = useMemo(
    () =>
      impactReport
        ? buildExecutionChecklistItems(
            impactReport.recommended_test_scope,
            impactReport.must_run_tests,
            impactReport.manual_verification,
          )
        : [],
    [impactReport],
  );
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
  const reviewId = effectiveReport?.review_id || review?.review_id || "";
  const canSubmitImpactFeedback = Boolean(reviewId && reviewId !== "impact-report");
  const impactFeedbackState = useMemo<ImpactFeedbackState>(
    () => ({
      submittingKey: impactFeedbackSubmittingKey,
      submittedByTarget: impactFeedbackSubmittedByTarget,
      disabled: !canSubmitImpactFeedback,
    }),
    [canSubmitImpactFeedback, impactFeedbackSubmittedByTarget, impactFeedbackSubmittingKey],
  );

  const submitImpactFeedback: ImpactFeedbackHandler = async (targetType, targetKey, label) => {
    if (!canSubmitImpactFeedback) {
      message.warning("当前报告缺少 review_id，无法记录影响反馈");
      return;
    }
    const actionId = buildImpactFeedbackActionId(targetType, targetKey, label);
    const targetId = buildImpactFeedbackTargetId(targetType, targetKey);
    setImpactFeedbackSubmittingKey(actionId);
    try {
      await reviewApi.submitImpactFeedback(reviewId, {
        target_type: targetType,
        target_key: targetKey,
        label,
        comment: label === "confirmed" ? "前端确认影响分析结果" : "前端标记影响分析疑似误报",
      });
      setImpactFeedbackSubmittedByTarget((current) => ({ ...current, [targetId]: label }));
      message.success(label === "confirmed" ? "已记录影响确认" : "已记录误报反馈");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "记录影响反馈失败");
    } finally {
      setImpactFeedbackSubmittingKey("");
    }
  };

  const copyExecutionChecklist = async () => {
    if (!impactReport) return;
    const text = buildExecutionChecklistMarkdown(reviewId || "impact-report", impactReport, executionChecklist);
    try {
      await navigator.clipboard.writeText(text);
      message.success("已复制影响分析执行清单");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "复制执行清单失败");
    }
  };

  return (
    <Card
      className={`module-card ${className || ""}`.trim()}
      title="影响范围报告"
      extra={
        <Button size="small" onClick={() => downloadImpactReportMarkdown(effectiveReport)} disabled={!markdown}>
          导出报告
        </Button>
      }
    >
      {impactReport ? (
        hasConfirmedGraphFacts && impactReport.llm_markdown?.trim() && !hasUnresolvedTemplateVariables(impactReport.llm_markdown) ? (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            {impactQualitySummary ? renderImpactQualitySummary(impactQualitySummary) : null}
            {gitNexusRepairTips.length ? (
              <Alert
                type="warning"
                showIcon
                message="GitNexus 图谱未完全可用"
                description={
                  <div>
                    {gitNexusRepairTips.map((item) => (
                      <div key={item}>{item}</div>
                    ))}
                  </div>
                }
              />
            ) : null}
            {renderExecutionChecklist(executionChecklist, copyExecutionChecklist)}
            <div className="template-preview-rendered">{renderTemplateMarkdown(markdown)}</div>
            {renderCollapsibleTargetDiagnostics(impactReport)}
            {renderImpactPaths(impactReport.impact_paths.slice(0, 5), submitImpactFeedback, impactFeedbackState)}
            {renderImpactedFiles(impactReport.impacted_files.slice(0, 6), submitImpactFeedback, impactFeedbackState)}
            {renderTestScope(impactReport.recommended_test_scope.slice(0, 6), submitImpactFeedback, impactFeedbackState)}
          </Space>
        ) : (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            {renderReportHeadline(impactReport)}
            {impactQualitySummary ? renderImpactQualitySummary(impactQualitySummary) : null}
            {gitNexusRepairTips.length ? (
              <Alert
                type="warning"
                showIcon
                message="GitNexus 图谱未完全可用"
                description={
                  <div>
                    {gitNexusRepairTips.map((item) => (
                      <div key={item}>{item}</div>
                    ))}
                  </div>
                }
              />
            ) : null}

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
            {renderChangedSymbols(impactReport.changed_symbols)}
            {renderTargetDiagnostics(impactReport)}
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
            {renderImpactedFiles(impactReport.impacted_files, submitImpactFeedback, impactFeedbackState)}
            {renderImpactPaths(impactReport.impact_paths, submitImpactFeedback, impactFeedbackState)}
            {renderTestScope(impactReport.recommended_test_scope, submitImpactFeedback, impactFeedbackState)}
            {renderExecutionChecklist(executionChecklist, copyExecutionChecklist)}
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
            message="影响范围报告生成失败"
            description={impactFailure.error_message || "GitNexus 调用失败，本次没有生成可导出的影响报告。"}
          />
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="本次任务没有可用的影响范围报告。请先修复 GitNexus 环境或图谱状态后重试。"
          />
        </Space>
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无影响范围报告。任务完成后，这里会生成本次改动的影响范围与测试建议。"
        />
      )}
    </Card>
  );
};

export default ImpactReportMarkdownPanel;
