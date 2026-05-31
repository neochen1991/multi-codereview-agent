import type { EvidenceChainStep } from "@/services/api";
import { rewriteUserFacingIssueText } from "./issueDisplayQuality";

const normalizeUnknownList = (value: unknown): string[] => {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (typeof item === "string") return item.trim();
      if (!item || typeof item !== "object") return "";
      const payload = item as Record<string, unknown>;
      const title = String(payload.flow || payload.name || payload.qualified_name || payload.symbol || payload.title || payload.entrypoint || "").trim();
      const relationship = String(payload.relationship || "").trim();
      const criticality = String(payload.criticality || payload.risk_level || payload.kind || "").trim();
      const path = String(payload.file_path || payload.path || "").trim();
      const lineStart = typeof payload.line_start === "number" ? payload.line_start : typeof payload.line_number === "number" ? payload.line_number : 0;
      const location = path ? `${path}${lineStart ? `:${lineStart}` : ""}` : "";
      const endpoints = [payload.source, payload.target].map((value) => String(value || "").trim()).filter(Boolean);
      const relationText = endpoints.length ? `${endpoints.join(" -> ")}${relationship ? ` (${relationship})` : ""}` : "";
      return [title || relationText, criticality, location].filter(Boolean).join(" · ");
    })
    .filter(Boolean);
};

export const evidenceContextSourceLabel = (value: unknown): string => {
  const source = String(value || "").trim();
  if (source === "tree_sitter") return "代码结构关系检索";
  if (source === "keyword_search") return "关键词搜索";
  if (source === "repository_context") return "仓库源码检索";
  if (source === "diff") return "本次 diff";
  return source || "-";
};

const statusLabel = (value: unknown): string => {
  const status = String(value || "").trim();
  if (status === "present") return "已提取到问题主张";
  if (status === "anchored") return "已定位到具体代码位置";
  if (status === "weak") return "代码位置证据较弱，需要人工复核";
  if (status === "verified") return "工具核验已通过";
  if (status === "not_verified") return "工具未能自动确认";
  if (status === "signal_matched" || status === "matched") return "已命中相关证据";
  if (status === "ranked") return "已按风险排序";
  if (status === "missing") return "暂无可用证据";
  if (status === "needs_verification") return "证据仍需人工复核";
  if (status === "true_positive") return "倾向确认是真问题";
  if (status === "false_positive") return "倾向判断为误报";
  if (status === "high_false_positive_risk") return "误报风险较高";
  if (status === "tree_sitter" || status === "keyword_search" || status === "repository_context" || status === "diff") {
    return evidenceContextSourceLabel(status);
  }
  return status;
};

export const evidenceStepLabel = (step: EvidenceChainStep): string => {
  const payload = step as EvidenceChainStep & Record<string, unknown>;
  const stepName = String(payload.step || "").trim();
  if (stepName === "claim") return "问题结论";
  if (stepName === "anchor" || stepName === "diff_anchor" || stepName === "changed_node") return "代码依据";
  if (stepName === "verifier") return "自动核验";
  if (stepName === "static_analysis" || stepName === "static_observation" || stepName === "sast_prescan") return "静态检查";
  if (stepName === "confidence") return "置信度说明";
  if (stepName === "context_source") return "上下文来源";
  if (stepName === "minimal_context") return "关联上下文概览";
  if (stepName === "affected_flows") return "影响流程";
  if (stepName === "related_contexts") return "关联代码片段";
  if (stepName === "graph_relationship") return "调用关系依据";
  if (stepName === "review_priority") return "优先核查点";
  if (stepName === "false_positive_filter") return "误报过滤";
  return stepName || "证据";
};

export const evidenceStepSummary = (step: EvidenceChainStep, issueContext?: string | null): string => {
  const payload = step as EvidenceChainStep & Record<string, unknown>;
  const stepName = String(payload.step || "").trim();
  const context = [issueContext || "", JSON.stringify(payload)].filter(Boolean).join("\n");

  if (stepName === "claim") {
    const claim = rewriteUserFacingIssueText(String(payload.claim || payload.summary || "").trim(), context);
    return claim ? `本条问题认为：${claim}` : statusLabel(payload.status);
  }

  if (stepName === "anchor" || stepName === "diff_anchor" || stepName === "changed_node") {
    const filePath = String(payload.file_path || "").trim();
    const lineStart = Number(payload.line_start || 0);
    const location = filePath ? `${filePath}${lineStart ? `:${lineStart}` : ""}` : "";
    const evidence = normalizeUnknownList(payload.evidence);
    const nodes = normalizeUnknownList(payload.nodes);
    const details = [...evidence, ...nodes]
      .map((item) => rewriteUserFacingIssueText(item, context))
      .slice(0, 3)
      .join("；");
    return [location ? `已定位到 ${location}` : statusLabel(payload.status), details].filter(Boolean).join("。");
  }

  if (stepName === "verifier") {
    const toolName = String(payload.tool_name || "").trim();
    const summary = rewriteUserFacingIssueText(String(payload.summary || "").trim(), context);
    if (summary && !/Static diff signals:\s*none/i.test(summary)) {
      return [toolName ? `工具：${toolName}` : "", summary].filter(Boolean).join("。");
    }
    return [toolName ? `工具：${toolName}` : "", "自动核验未发现足够强的确定性信号，需要结合代码上下文复核。"].filter(Boolean).join("。");
  }

  if (stepName === "static_analysis") {
    const signals = normalizeUnknownList(payload.signals);
    return signals.length ? `静态检查命中：${signals.slice(0, 4).join("；")}` : statusLabel(payload.status);
  }

  if (stepName === "static_observation") {
    const observationIds = normalizeUnknownList(payload.observation_ids);
    return observationIds.length ? `命中静态观察规则：${observationIds.slice(0, 6).join("；")}` : statusLabel(payload.status);
  }

  if (stepName === "sast_prescan") {
    const matches = normalizeUnknownList(payload.matches);
    return matches.length ? `SAST 预扫描命中：${matches.slice(0, 5).join("；")}` : statusLabel(payload.status);
  }

  if (stepName === "confidence") {
    const originalConfidence = typeof payload.original_confidence === "number" ? payload.original_confidence : null;
    const finalConfidence = typeof payload.final_confidence === "number" ? payload.final_confidence : null;
    const delta = typeof payload.confidence_delta === "number" ? payload.confidence_delta : null;
    const parts = [
      statusLabel(payload.status),
      originalConfidence !== null && finalConfidence !== null ? `置信度 ${Math.round(originalConfidence * 100)}% -> ${Math.round(finalConfidence * 100)}%` : "",
      delta !== null ? `调整 ${delta >= 0 ? "+" : ""}${Math.round(delta * 100)}%` : "",
    ].filter(Boolean);
    return parts.join("，");
  }

  if (stepName === "context_source") {
    const source = payload.primary_source || payload.context_source || payload.status;
    const fallbackReason = String(payload.fallback_reason || "").trim();
    return fallbackReason
      ? `${evidenceContextSourceLabel(source)}，备用原因：${fallbackReason}`
      : `本条证据主要来自：${evidenceContextSourceLabel(source)}`;
  }

  if (stepName === "minimal_context") {
    const summary = String(payload.summary || "").trim();
    const riskLevel = String(payload.risk_level || "").trim();
    const riskScore = typeof payload.risk_score === "number" ? payload.risk_score : null;
    return [summary, riskLevel ? `风险级别：${riskLevel}` : "", riskScore !== null ? `风险评分：${riskScore}` : ""].filter(Boolean).join("。");
  }

  if (stepName === "graph_relationship" || stepName === "related_contexts") {
    const contexts = normalizeUnknownList(payload.contexts || payload.related_contexts);
    return contexts.length ? `代码结构检索找到相关调用/引用关系：${contexts.slice(0, 4).join("；")}` : statusLabel(payload.status);
  }

  if (stepName === "review_priority") {
    const priorities = normalizeUnknownList(payload.priorities);
    return priorities.length ? `建议优先核查：${priorities.slice(0, 5).join("；")}` : statusLabel(payload.status);
  }

  if (stepName === "affected_flows") {
    const flows = normalizeUnknownList(payload.flows || payload.affected_flows);
    return flows.length ? `可能影响流程：${flows.slice(0, 5).join("；")}` : statusLabel(payload.status);
  }

  if (stepName === "false_positive_filter") {
    const reason = rewriteUserFacingIssueText(String(payload.reason || "").trim(), context);
    return [statusLabel(payload.verdict || payload.status), reason].filter(Boolean).join("：");
  }

  const explicit = rewriteUserFacingIssueText(String(payload.summary || payload.claim || payload.reason || payload.verdict || "").trim(), context);
  if (explicit) return explicit;
  const source = String(payload.source || payload.context_source || "").trim();
  const relationship = String(payload.relationship || "").trim();
  const values = [
    ...normalizeUnknownList(payload.reasons),
    ...normalizeUnknownList(payload.signals),
    ...normalizeUnknownList(payload.evidence),
    ...normalizeUnknownList(payload.cross_file_evidence),
  ];
  if (source) return evidenceContextSourceLabel(source);
  if (relationship) return `图谱关系：${relationship}`;
  if (values.length) return values.slice(0, 3).join("；");
  return statusLabel(payload.status) || "-";
};
