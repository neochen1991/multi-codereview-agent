import type {
  DebateIssue,
  IssueFilterDecision,
  ReviewFinding,
  ReviewSummary,
  RuleScreeningMetadata,
} from "@/services/api";
import type { ReviewOverviewExpertSelectionSummary } from "@/components/review/ReviewOverviewPanel";

export type RoutingExpertItem = {
  expert_id: string;
  expert_name?: string;
  reason?: string;
  file_path?: string;
  line_start?: number;
  source?: string;
};

export type ExpertRoutingSummary = {
  user_selected_experts: RoutingExpertItem[];
  skipped_experts: RoutingExpertItem[];
  effective_experts: RoutingExpertItem[];
  system_added_experts: RoutingExpertItem[];
  fallback_expert_added: boolean;
};

export type ExpertSelectionSummary = {
  requested_expert_ids: string[];
  candidate_expert_ids: string[];
  selected_experts: RoutingExpertItem[];
  skipped_experts: RoutingExpertItem[];
};

export type ExpertRuleCoverageSummary = {
  expert_id: string;
  expert_name: string;
  rule_screening: RuleScreeningMetadata;
};

export const THRESHOLD_RULE_CODES = new Set([
  "below_issue_priority_threshold",
  "below_priority_confidence_threshold",
]);

export const normalizeDesignDocs = (value: unknown) => {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    .map((item) => ({
      doc_id: item.doc_id ? String(item.doc_id) : undefined,
      title: String(item.title || item.filename || "详细设计文档"),
      filename: String(item.filename || item.title || "design-spec.md"),
      content: String(item.content || ""),
      doc_type: "design_spec" as const,
    }))
    .filter((item) => item.content.trim());
};

export const normalizeRoutingItems = (value: unknown): RoutingExpertItem[] => {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    .map((item) => ({
      expert_id: String(item.expert_id || ""),
      expert_name: item.expert_name ? String(item.expert_name) : undefined,
      reason: item.reason ? String(item.reason) : undefined,
      file_path: item.file_path ? String(item.file_path) : undefined,
      line_start: typeof item.line_start === "number" ? item.line_start : undefined,
      source: item.source ? String(item.source) : undefined,
    }))
    .filter((item) => item.expert_id);
};

export const readExpertRoutingSummary = (review?: ReviewSummary | null): ExpertRoutingSummary | null => {
  const metadata = review?.subject?.metadata;
  if (!metadata || typeof metadata !== "object") return null;
  const routing = (metadata as Record<string, unknown>).expert_routing;
  if (!routing || typeof routing !== "object") return null;
  const payload = routing as Record<string, unknown>;
  return {
    user_selected_experts: normalizeRoutingItems(payload.user_selected_experts),
    skipped_experts: normalizeRoutingItems(payload.skipped_experts),
    effective_experts: normalizeRoutingItems(payload.effective_experts),
    system_added_experts: normalizeRoutingItems(payload.system_added_experts),
    fallback_expert_added: Boolean(payload.fallback_expert_added),
  };
};

export const readExpertSelectionSummary = (review?: ReviewSummary | null): ExpertSelectionSummary | null => {
  const metadata = review?.subject?.metadata;
  if (!metadata || typeof metadata !== "object") return null;
  const selection = (metadata as Record<string, unknown>).expert_selection;
  if (!selection || typeof selection !== "object") return null;
  const payload = selection as Record<string, unknown>;
  return {
    requested_expert_ids: Array.isArray(payload.requested_expert_ids)
      ? payload.requested_expert_ids.map((item) => String(item)).filter(Boolean)
      : [],
    candidate_expert_ids: Array.isArray(payload.candidate_expert_ids)
      ? payload.candidate_expert_ids.map((item) => String(item)).filter(Boolean)
      : [],
    selected_experts: normalizeRoutingItems(payload.selected_experts),
    skipped_experts: normalizeRoutingItems(payload.skipped_experts),
  };
};

export const normalizeIssueFilterDecisions = (
  messages: { message_type: string; metadata: Record<string, unknown> }[],
): IssueFilterDecision[] =>
  messages
    .filter((message) => message.message_type === "issue_filter_applied")
    .flatMap((message) => {
      const raw = message.metadata?.issue_filter_decisions;
      if (!Array.isArray(raw)) return [];
      return raw
        .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
        .map((item) => ({
          topic: String(item.topic || ""),
          rule_code: String(item.rule_code || ""),
          rule_label: String(item.rule_label || ""),
          reason: String(item.reason || ""),
          severity: String(item.severity || ""),
          finding_ids: Array.isArray(item.finding_ids) ? item.finding_ids.map((entry) => String(entry)).filter(Boolean) : [],
          finding_titles: Array.isArray(item.finding_titles)
            ? item.finding_titles.map((entry) => String(entry)).filter(Boolean)
            : [],
          expert_ids: Array.isArray(item.expert_ids) ? item.expert_ids.map((entry) => String(entry)).filter(Boolean) : [],
        }));
    });

export const pickRepresentativeFindingForIssue = (
  issue: DebateIssue,
  findingById: Map<string, ReviewFinding>,
): ReviewFinding | null => {
  const candidates = (issue.finding_ids || [])
    .map((id) => findingById.get(id))
    .filter((item): item is ReviewFinding => Boolean(item));
  if (!candidates.length) return null;
  const issuePath = String(issue.file_path || "").trim();
  const issueLine = Number(issue.line_start || 0);
  const scored = candidates.map((finding) => {
    const samePath = issuePath && finding.file_path === issuePath ? 1 : 0;
    const lineDistance = issueLine > 0 ? Math.abs(Number(finding.line_start || 0) - issueLine) : 999999;
    return { finding, samePath, lineDistance };
  });
  scored.sort((a, b) => {
    if (a.samePath !== b.samePath) return b.samePath - a.samePath;
    if (a.lineDistance !== b.lineDistance) return a.lineDistance - b.lineDistance;
    return (b.finding.confidence || 0) - (a.finding.confidence || 0);
  });
  return scored[0]?.finding || candidates[0];
};

export const normalizeRuleScreeningMetadata = (value: unknown): RuleScreeningMetadata | null => {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  const matchedRules = Array.isArray(payload.matched_rules_for_llm) ? payload.matched_rules_for_llm : [];
  return {
    total_rules: typeof payload.total_rules === "number" ? payload.total_rules : 0,
    enabled_rules: typeof payload.enabled_rules === "number" ? payload.enabled_rules : 0,
    must_review_count: typeof payload.must_review_count === "number" ? payload.must_review_count : 0,
    possible_hit_count: typeof payload.possible_hit_count === "number" ? payload.possible_hit_count : 0,
    matched_rule_count: typeof payload.matched_rule_count === "number" ? payload.matched_rule_count : 0,
    batch_count: typeof payload.batch_count === "number" ? payload.batch_count : 0,
    screening_mode: typeof payload.screening_mode === "string" ? payload.screening_mode : undefined,
    screening_fallback_used: Boolean(payload.screening_fallback_used),
    matched_rules_for_llm: matchedRules
      .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
      .map((item) => ({
        rule_id: String(item.rule_id || ""),
        title: String(item.title || ""),
        priority: String(item.priority || ""),
        decision: String(item.decision || ""),
        reason: String(item.reason || ""),
        matched_terms: Array.isArray(item.matched_terms)
          ? item.matched_terms.map((entry) => String(entry)).filter(Boolean)
          : [],
      })),
  };
};

export const formatElapsedDuration = (seconds: number): string => {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const remainingSeconds = safeSeconds % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分 ${remainingSeconds} 秒`;
  if (minutes > 0) return `${minutes} 分 ${remainingSeconds} 秒`;
  return `${remainingSeconds} 秒`;
};

export const toOverviewExpertSelectionSummary = (
  summary: ExpertSelectionSummary | null,
): ReviewOverviewExpertSelectionSummary | null => {
  if (!summary) return null;
  return {
    requested_expert_ids: summary.requested_expert_ids,
    selected_experts: summary.selected_experts.map((item) => ({
      expert_id: item.expert_id,
      expert_name: item.expert_name,
      reason: item.reason,
    })),
    skipped_experts: summary.skipped_experts.map((item) => ({
      expert_id: item.expert_id,
      expert_name: item.expert_name,
      reason: item.reason,
    })),
  };
};
