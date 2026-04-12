import React, { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { App as AntdApp, Button, Card, Col, Empty, Modal, Popconfirm, Row, Space, Tabs, Tag, Typography } from "antd";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import ArtifactSummaryPanel from "@/components/review/ArtifactSummaryPanel";
import EventTimeline from "@/components/review/EventTimeline";
import OverviewCards from "@/components/review/OverviewCards";
import ReviewOverviewPanel, {
  type ReviewFormState,
  type ReviewOverviewExpertSelectionSummary,
} from "@/components/review/ReviewOverviewPanel";
import ReviewSubjectPanel from "@/components/review/ReviewSubjectPanel";
import {
  buildReviewEventStreamUrl,
  expertApi,
  knowledgeApi,
  reviewApi,
  settingsApi,
  type DebateIssue,
  type ExpertProfile,
  type IssueFilterDecision,
  type KnowledgeDocument,
  type ReviewArtifacts,
  type ReviewDesignDocumentInput,
  type ReviewFinding,
  type ReviewReplayBundle,
  type ReviewReport,
  type ReviewSummary,
  type RuleScreeningMetadata,
  type RuntimeSettings,
} from "@/services/api";
import { subscribeReviewEventStream } from "@/services/stream";

const CodeReviewConclusionPanel = lazy(() => import("@/components/review/CodeReviewConclusionPanel"));
const DiffPreviewPanel = lazy(() => import("@/components/review/DiffPreviewPanel"));
const ExpertLaneBoard = lazy(() => import("@/components/review/ExpertLaneBoard"));
const FindingsPanel = lazy(() => import("@/components/review/FindingsPanel"));
const HumanGatePanel = lazy(() => import("@/components/review/HumanGatePanel"));
const IssueDetailPanel = lazy(() => import("@/components/review/IssueDetailPanel"));
const IssueThresholdFilteredPanel = lazy(() => import("@/components/review/IssueThresholdFilteredPanel"));
const IssueThreadList = lazy(() => import("@/components/review/IssueThreadList"));
const KnowledgeRefPanel = lazy(() => import("@/components/review/KnowledgeRefPanel"));
const ReplayConsolePanel = lazy(() => import("@/components/review/ReplayConsolePanel"));
const ResultIssuePanel = lazy(() => import("@/components/review/ResultIssuePanel"));
const ReportSummaryPanel = lazy(() => import("@/components/review/ReportSummaryPanel"));
const ReviewDialogueStream = lazy(() => import("@/components/review/ReviewDialogueStream"));
const ToolAuditPanel = lazy(() => import("@/components/review/ToolAuditPanel"));

type RoutingExpertItem = {
  expert_id: string;
  expert_name?: string;
  reason?: string;
  file_path?: string;
  line_start?: number;
  source?: string;
};

type ExpertRoutingSummary = {
  user_selected_experts: RoutingExpertItem[];
  skipped_experts: RoutingExpertItem[];
  effective_experts: RoutingExpertItem[];
  system_added_experts: RoutingExpertItem[];
  fallback_expert_added: boolean;
};

type ExpertSelectionSummary = {
  requested_expert_ids: string[];
  candidate_expert_ids: string[];
  selected_experts: RoutingExpertItem[];
  skipped_experts: RoutingExpertItem[];
};

type ExpertRuleCoverageSummary = {
  expert_id: string;
  expert_name: string;
  rule_screening: RuleScreeningMetadata;
};

type WorkspaceTabKey = "overview" | "process" | "result";
type ProcessMainTabKey = "dialogue" | "lanes" | "diff" | "replay";
type ProcessSidebarTabKey = "issues" | "knowledge" | "events";

const { Paragraph, Text, Title } = Typography;

const defaultFormState: ReviewFormState = {
  subject_type: "mr",
  analysis_mode: "standard",
  mr_url: "",
  title: "",
  source_ref: "",
  target_ref: "",
  design_docs: [],
  selected_experts: [],
};

const WORKSPACE_TAB_KEYS: WorkspaceTabKey[] = ["overview", "process", "result"];
const PROCESS_INCREMENTAL_LIMIT = 500;
const PROCESS_CLIENT_CACHE_LIMIT = 4000;

const WorkbenchPanelFallback: React.FC<{ description?: string }> = ({ description = "模块加载中..." }) => (
  <Card className="module-card">
    <Empty description={description} image={Empty.PRESENTED_IMAGE_SIMPLE} />
  </Card>
);

const isWorkspaceTabKey = (value: string | null): value is WorkspaceTabKey =>
  Boolean(value && WORKSPACE_TAB_KEYS.includes(value as WorkspaceTabKey));

const normalizeDesignDocs = (value: unknown): ReviewDesignDocumentInput[] => {
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

const normalizeRoutingItems = (value: unknown): RoutingExpertItem[] => {
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

const readExpertRoutingSummary = (review?: ReviewSummary | null): ExpertRoutingSummary | null => {
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

const readExpertSelectionSummary = (review?: ReviewSummary | null): ExpertSelectionSummary | null => {
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

const normalizeIssueFilterDecisions = (messages: { message_type: string; metadata: Record<string, unknown> }[]): IssueFilterDecision[] =>
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
          finding_titles: Array.isArray(item.finding_titles) ? item.finding_titles.map((entry) => String(entry)).filter(Boolean) : [],
          expert_ids: Array.isArray(item.expert_ids) ? item.expert_ids.map((entry) => String(entry)).filter(Boolean) : [],
        }));
    });

const normalizeRuleScreeningMetadata = (value: unknown): RuleScreeningMetadata | null => {
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

const formatElapsedDuration = (seconds: number): string => {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const remainingSeconds = safeSeconds % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分 ${remainingSeconds} 秒`;
  if (minutes > 0) return `${minutes} 分 ${remainingSeconds} 秒`;
  return `${remainingSeconds} 秒`;
};

const THRESHOLD_RULE_CODES = new Set([
  "below_issue_priority_threshold",
  "below_priority_confidence_threshold",
]);

const toOverviewExpertSelectionSummary = (
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

const RoutingExpertTags: React.FC<{
  title: string;
  items: RoutingExpertItem[];
  color: string;
}> = ({ title, items, color }) => {
  if (items.length === 0) return null;
  return (
    <div className="routing-group">
      <Text className="routing-group-title">{title}</Text>
      <Space size={[8, 8]} wrap>
        {items.map((item) => (
          <Tag key={`${title}-${item.expert_id}-${item.file_path || "none"}`} color={color}>
            {item.expert_name || item.expert_id}
          </Tag>
        ))}
      </Space>
    </div>
  );
};

const ExpertRoutingPanel: React.FC<{ summary: ExpertRoutingSummary | null }> = ({ summary }) => {
  if (!summary) return null;
  const hasAdjustments = summary.skipped_experts.length > 0 || summary.system_added_experts.length > 0;
  const bannerTone = summary.system_added_experts.length > 0 ? "warning" : hasAdjustments ? "info" : "default";
  const heading =
    summary.system_added_experts.length > 0
      ? "专家与代码不完全匹配，系统已自动补入兜底专家继续审查"
      : hasAdjustments
        ? "部分已选择专家与当前变更相关性较低，系统已自动跳过"
        : "本轮专家路由已完成";
  return (
    <Card className={`module-card expert-routing-card expert-routing-card-${bannerTone}`} title="专家路由提示">
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Paragraph className="expert-routing-summary">{heading}</Paragraph>
        <RoutingExpertTags title="用户选择" items={summary.user_selected_experts} color="blue" />
        <RoutingExpertTags title="实际参与" items={summary.effective_experts} color="green" />
        <RoutingExpertTags title="系统补入" items={summary.system_added_experts} color="gold" />
        {summary.skipped_experts.length > 0 ? (
          <div className="routing-group">
            <Text className="routing-group-title">已跳过</Text>
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              {summary.skipped_experts.map((item) => (
                <div key={`skipped-${item.expert_id}-${item.file_path || "none"}`} className="routing-skip-item">
                  <Text strong>{item.expert_name || item.expert_id}</Text>
                  <Text type="secondary">
                    {item.reason || "当前变更未命中该专家的有效审查线索"}
                    {item.file_path ? ` · ${item.file_path}${item.line_start ? `:${item.line_start}` : ""}` : ""}
                  </Text>
                </div>
              ))}
            </Space>
          </div>
        ) : null}
      </Space>
    </Card>
  );
};

const ExpertRuleCoveragePanel: React.FC<{ items: ExpertRuleCoverageSummary[] }> = ({ items }) => {
  if (items.length === 0) return null;
  return (
    <Card className="module-card" title="专家规则命中统计">
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        {items.map((item) => (
          <Card key={item.expert_id} size="small">
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              <Space wrap>
                <Tag color="geekblue">{item.expert_name}</Tag>
                <Tag color="purple">{`总规则 ${item.rule_screening.total_rules}`}</Tag>
                <Tag>{`启用 ${item.rule_screening.enabled_rules || item.rule_screening.total_rules}`}</Tag>
                <Tag color="magenta">{`命中 ${item.rule_screening.matched_rule_count}`}</Tag>
                <Tag color="volcano">{`强命中 ${item.rule_screening.must_review_count}`}</Tag>
                <Tag color="blue">{`候选 ${item.rule_screening.possible_hit_count}`}</Tag>
                {item.rule_screening.batch_count ? <Tag>{`批次 ${item.rule_screening.batch_count}`}</Tag> : null}
                {item.rule_screening.screening_mode ? <Tag>{item.rule_screening.screening_mode}</Tag> : null}
                {item.rule_screening.screening_fallback_used ? <Tag color="orange">fallback</Tag> : null}
              </Space>
              {item.rule_screening.matched_rules_for_llm?.length ? (
                <Space wrap>
                  {item.rule_screening.matched_rules_for_llm.map((rule) => (
                    <Tag key={`${item.expert_id}-${rule.rule_id || rule.title}`} color="cyan">
                      {`${rule.priority ? `[${rule.priority}] ` : ""}${rule.title || rule.rule_id}`}
                    </Tag>
                  ))}
                </Space>
              ) : (
                <Text type="secondary">本轮未命中需要带入深审的规则。</Text>
              )}
            </Space>
          </Card>
        ))}
      </Space>
    </Card>
  );
};

const ExpertSelectionPanel: React.FC<{ summary: ExpertSelectionSummary | null }> = ({ summary }) => {
  return (
    <Card className="module-card expert-routing-card expert-routing-card-info" title="专家参与判定">
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Paragraph className="expert-routing-summary">
          {summary
            ? "主Agent 已基于当前 MR 信息、完整 diff 和专家画像，先由大模型判定本次真正需要参与审核的专家集合。"
            : "主Agent 正在结合当前 MR、完整 diff 和专家画像判定本轮需要参与审核的专家，请稍候。"}
        </Paragraph>
        {summary ? <RoutingExpertTags title="大模型选中" items={summary.selected_experts} color="green" /> : null}
        {summary?.requested_expert_ids.length ? (
          <div className="routing-group">
            <Text className="routing-group-title">原始候选</Text>
            <Space size={[8, 8]} wrap>
              {summary.requested_expert_ids.map((item) => (
                <Tag key={`requested-${item}`} color="blue">
                  {item}
                </Tag>
              ))}
            </Space>
          </div>
        ) : null}
        {summary?.skipped_experts.length ? (
          <div className="routing-group">
            <Text className="routing-group-title">未参与本轮</Text>
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              {summary.skipped_experts.map((item) => (
                <div key={`selection-skipped-${item.expert_id}-${item.file_path || "none"}`} className="routing-skip-item">
                  <Text strong>{item.expert_name || item.expert_id}</Text>
                  <Text type="secondary">{item.reason || "大模型未将其纳入本次 MR 的审核集合"}</Text>
                </div>
              ))}
            </Space>
          </div>
        ) : !summary ? (
          <Tag color="processing">正在判定参与专家</Tag>
        ) : null}
      </Space>
    </Card>
  );
};

const ReviewWorkbenchPage: React.FC = () => {
  // 这是前端真正的“审核工作台容器”。
  // 概览、过程、结果三个页签共用同一份 review/replay/artifact 状态，
  // 这样页面切换时不会丢失当前审核上下文。
  const { message } = AntdApp.useApp();
  const { reviewId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [activeStep, setActiveStep] = useState<WorkspaceTabKey>("overview");
  const [processMainTab, setProcessMainTab] = useState<ProcessMainTabKey>("dialogue");
  const [processSidebarTab, setProcessSidebarTab] = useState<ProcessSidebarTabKey>("issues");
  const [review, setReview] = useState<ReviewSummary | null>(null);
  const [replay, setReplay] = useState<ReviewReplayBundle | null>(null);
  const [report, setReport] = useState<ReviewReport | null>(null);
  const [messages, setMessages] = useState<ReviewReplayBundle["messages"]>([]);
  const [issues, setIssues] = useState<DebateIssue[]>([]);
  const [events, setEvents] = useState<ReviewReplayBundle["events"]>([]);
  const [findings, setFindings] = useState<ReviewFinding[]>([]);
  const [artifacts, setArtifacts] = useState<ReviewArtifacts | null>(null);
  const [experts, setExperts] = useState<ExpertProfile[]>([]);
  const [runtimeSettings, setRuntimeSettings] = useState<RuntimeSettings | null>(null);
  const [knowledgeDocs, setKnowledgeDocs] = useState<KnowledgeDocument[]>([]);
  const [selectedIssueId, setSelectedIssueId] = useState("");
  const [selectedFindingId, setSelectedFindingId] = useState("");
  const [decisionComment, setDecisionComment] = useState("");
  const [elapsedNow, setElapsedNow] = useState(() => Date.now());
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [submittingDecision, setSubmittingDecision] = useState(false);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [findingModalOpen, setFindingModalOpen] = useState(false);
  const [resultFindingDetailsLoading, setResultFindingDetailsLoading] = useState(false);
  const [resultFindingDetailsError, setResultFindingDetailsError] = useState("");
  const [resultFindingDetailCache, setResultFindingDetailCache] = useState<Record<string, ReviewFinding>>({});
  const [form, setForm] = useState<ReviewFormState>(defaultFormState);
  const autoStartTriggeredRef = useRef<string>("");
  const workspaceLoadRef = useRef<{ key: string; promise: Promise<void> | null }>({ key: "", promise: null });
  const processCursorRef = useRef<{
    reviewId: string;
    eventSince: string;
    messageSince: string;
    findingSince: string;
  }>({
    reviewId: "",
    eventSince: "",
    messageSince: "",
    findingSince: "",
  });
  const processDialogueRef = useRef<HTMLDivElement | null>(null);
  const processIssuesRef = useRef<HTMLDivElement | null>(null);
  const resultSummaryRef = useRef<HTMLDivElement | null>(null);
  const resultHumanRef = useRef<HTMLDivElement | null>(null);
  const resultFindingsRef = useRef<HTMLDivElement | null>(null);
  const isReadonlyOverview = Boolean(reviewId);
  const requestedTab = useMemo<WorkspaceTabKey | null>(() => {
    const search = new URLSearchParams(location.search);
    const value = search.get("tab");
    return isWorkspaceTabKey(value) ? value : null;
  }, [location.search]);

  const syncTabToUrl = (nextTab: WorkspaceTabKey, replace = true) => {
    const search = new URLSearchParams(location.search);
    if (search.get("tab") === nextTab) return;
    search.set("tab", nextTab);
    const path = reviewId ? `/review/${reviewId}` : "/review";
    const nextSearch = search.toString();
    navigate(nextSearch ? `${path}?${nextSearch}` : path, { replace });
  };

  const openWorkspaceTab = (nextTab: WorkspaceTabKey, options?: { replace?: boolean; syncUrl?: boolean }) => {
    setActiveStep(nextTab);
    if (options?.syncUrl === false) return;
    syncTabToUrl(nextTab, options?.replace ?? true);
  };

  const applyReviewDetail = (detail: ReviewSummary) => {
    setReview((current) => {
      const currentSubject = (current || detail).subject || detail.subject;
      const currentMetadata =
        currentSubject?.metadata && typeof currentSubject.metadata === "object"
          ? (currentSubject.metadata as Record<string, unknown>)
          : {};
      const nextMetadata =
        detail.subject?.metadata && typeof detail.subject.metadata === "object"
          ? (detail.subject.metadata as Record<string, unknown>)
          : {};
      const nextSubject = {
        ...currentSubject,
        ...(detail.subject || {}),
        metadata: {
          ...currentMetadata,
          ...nextMetadata,
        },
      };
      if (!detail.subject?.unified_diff && currentSubject?.unified_diff) {
        nextSubject.unified_diff = currentSubject.unified_diff;
      }
      return {
        ...(current || detail),
        ...detail,
        subject: nextSubject,
      };
    });
    setForm({
      subject_type: detail.subject.subject_type === "branch" ? "branch" : "mr",
      analysis_mode: detail.analysis_mode === "light" ? "light" : "standard",
      mr_url: detail.subject.mr_url || "",
      title: detail.subject.title || "",
      source_ref: detail.subject.source_ref || "",
      target_ref: detail.subject.target_ref || "main",
      selected_experts:
        detail.selected_experts && detail.selected_experts.length > 0
          ? detail.selected_experts
          : defaultFormState.selected_experts,
      design_docs: normalizeDesignDocs(
        ((detail.subject.metadata || {}) as Record<string, unknown>).design_docs,
      ),
    });
  };

  const loadReviewBase = async (targetReviewId: string) => {
    const detail = await reviewApi.get(targetReviewId);
    applyReviewDetail(detail);
    return detail;
  };

  const loadReviewSnapshot = async (targetReviewId: string) => {
    const detail = await reviewApi.getSnapshot(targetReviewId);
    applyReviewDetail(detail);
    return detail;
  };

  const loadProcessBundle = async (targetReviewId: string) => {
    const isIncremental = processCursorRef.current.reviewId === targetReviewId;
    const sinceEvent = isIncremental ? processCursorRef.current.eventSince : "";
    const sinceMessage = isIncremental ? processCursorRef.current.messageSince : "";
    const sinceFinding = isIncremental ? processCursorRef.current.findingSince : "";
    const [nextIssues, nextEvents, nextMessages, nextFindings] = await Promise.all([
      reviewApi.listIssues(targetReviewId),
      reviewApi.listEvents(targetReviewId, {
        since: sinceEvent || undefined,
        limit: PROCESS_INCREMENTAL_LIMIT,
      }),
      reviewApi.listMessages(targetReviewId, {
        since: sinceMessage || undefined,
        limit: PROCESS_INCREMENTAL_LIMIT,
      }),
      reviewApi.listFindings(targetReviewId, {
        since: sinceFinding || undefined,
        limit: PROCESS_INCREMENTAL_LIMIT,
      }),
    ]);
    const mergeById = <T extends { created_at?: string }>(
      base: T[],
      incoming: T[],
      getId: (item: T) => string,
    ): T[] => {
      const map = new Map<string, T>();
      for (const item of base) {
        map.set(getId(item), item);
      }
      for (const item of incoming) {
        map.set(getId(item), item);
      }
      return Array.from(map.values())
        .sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")))
        .slice(-PROCESS_CLIENT_CACHE_LIMIT);
    };

    const mergedEvents = isIncremental
      ? mergeById(events, nextEvents, (item) => item.event_id)
      : nextEvents;
    const mergedMessages = isIncremental
      ? mergeById(messages, nextMessages, (item) => item.message_id)
      : nextMessages;
    const mergedFindings = isIncremental
      ? mergeById(findings, nextFindings, (item) => item.finding_id)
      : nextFindings;

    setIssues(nextIssues);
    setEvents(mergedEvents);
    setMessages(mergedMessages);
    setFindings(mergedFindings);

    processCursorRef.current = {
      reviewId: targetReviewId,
      eventSince: mergedEvents.length ? String(mergedEvents[mergedEvents.length - 1]?.created_at || "") : "",
      messageSince: mergedMessages.length ? String(mergedMessages[mergedMessages.length - 1]?.created_at || "") : "",
      findingSince: mergedFindings.length ? String(mergedFindings[mergedFindings.length - 1]?.created_at || "") : "",
    };
    return { issues: nextIssues, events: mergedEvents, messages: mergedMessages, findings: mergedFindings };
  };

  const loadResultBundle = async (targetReviewId: string) => {
    const [nextReport, artifactBundle] = await Promise.all([
      reviewApi.getReport(targetReviewId, {
        findings_limit: 800,
        findings_offset: 0,
        issues_limit: 800,
        issues_offset: 0,
      }),
      reviewApi.getArtifacts(targetReviewId).catch(() => null),
    ]);
    setReport(nextReport);
    setIssues(nextReport.issues || []);
    setFindings(nextReport.findings || []);
    setArtifacts(artifactBundle);
    setResultFindingDetailCache({});
    setResultFindingDetailsLoading(false);
    setResultFindingDetailsError("");
    return { report: nextReport, artifacts: artifactBundle };
  };

  const loadReplayBundle = async (targetReviewId: string) => {
    const replayBundle = await reviewApi.getReplay(targetReviewId);
    setReplay(replayBundle);
    if (replayBundle.review) {
      applyReviewDetail(replayBundle.review);
    }
    setEvents(replayBundle.events || []);
    setMessages(replayBundle.messages || []);
    return replayBundle;
  };

  const syncSelectionFromData = (nextIssues: DebateIssue[], nextFindings: ReviewFinding[]) => {
    setSelectedIssueId((prev) => (nextIssues.some((item) => item.issue_id === prev) ? prev : nextIssues[0]?.issue_id || ""));
    setSelectedFindingId((prev) =>
      nextFindings.some((item) => item.finding_id === prev) ? prev : nextFindings[0]?.finding_id || "",
    );
  };

  const loadWorkspaceData = async (targetReviewId: string, options?: { forceFullReview?: boolean }) => {
    const loadKey = [targetReviewId, activeStep, processMainTab, options?.forceFullReview ? "full" : "smart"].join("|");
    if (workspaceLoadRef.current.promise && workspaceLoadRef.current.key === loadKey) {
      return workspaceLoadRef.current.promise;
    }

    const task = (async () => {
      setLoading(true);
      try {
        const detail =
          options?.forceFullReview || !review?.subject?.unified_diff
            ? await loadReviewBase(targetReviewId)
            : await loadReviewSnapshot(targetReviewId);
        if (activeStep === "process") {
          setResultFindingDetailsLoading(false);
          setResultFindingDetailsError("");
          const processBundle = await loadProcessBundle(targetReviewId);
          if (processMainTab === "replay") {
            await loadReplayBundle(targetReviewId);
            syncSelectionFromData(processBundle.issues || [], processBundle.findings || []);
          } else {
            setReplay(null);
            syncSelectionFromData(processBundle.issues || [], processBundle.findings || []);
          }
          return;
        }
        if (activeStep === "result") {
          const resultBundle = await loadResultBundle(targetReviewId);
          setEvents([]);
          setMessages([]);
          processCursorRef.current = { reviewId: "", eventSince: "", messageSince: "", findingSince: "" };
          setReplay(null);
          syncSelectionFromData(resultBundle.report?.issues || [], resultBundle.report?.findings || []);
          return;
        }
        setResultFindingDetailsLoading(false);
        setResultFindingDetailsError("");
        setReplay(null);
        setReport(null);
        setArtifacts(null);
        setIssues([]);
        setEvents([]);
        setMessages([]);
        setFindings([]);
        processCursorRef.current = { reviewId: "", eventSince: "", messageSince: "", findingSince: "" };
        setResultFindingDetailCache({});
        syncSelectionFromData([], []);
        applyReviewDetail(detail);
      } catch (error: any) {
        message.error(error?.message || "加载审核详情失败");
      } finally {
        setLoading(false);
        if (workspaceLoadRef.current.key === loadKey) {
          workspaceLoadRef.current = { key: "", promise: null };
        }
      }
    })();

    workspaceLoadRef.current = { key: loadKey, promise: task };
    return task;
  };

  useEffect(() => {
    // 页面初始化时先拉专家列表和 runtime settings，
    // 这样“概览与启动”页才能拿到默认模式、默认分支和可选专家。
    void Promise.all([expertApi.list(), settingsApi.getRuntime()])
      .then(([rows, runtime]) => {
        setExperts(rows.filter((item) => item.enabled));
        setRuntimeSettings(runtime);
        if (!reviewId) {
          setForm((current) => ({
            ...current,
            analysis_mode: current.analysis_mode || runtime.default_analysis_mode || "standard",
            target_ref: current.target_ref || runtime.default_target_branch || "",
            selected_experts:
              current.selected_experts.length > 0 ? current.selected_experts : defaultFormState.selected_experts,
          }));
        }
      })
      .catch(() => {
        setExperts([]);
        setRuntimeSettings(null);
      });
  }, [reviewId]);

  useEffect(() => {
    // reviewId 变化时，要么进入“新建审核”空态，要么恢复某条历史审核。
    if (!reviewId) {
      setReview(null);
      setReplay(null);
      setReport(null);
      setArtifacts(null);
      setIssues([]);
      setEvents([]);
      setMessages([]);
      setFindings([]);
      processCursorRef.current = { reviewId: "", eventSince: "", messageSince: "", findingSince: "" };
      setResultFindingDetailCache({});
      setForm({
        ...defaultFormState,
        analysis_mode: runtimeSettings?.default_analysis_mode || "standard",
        target_ref: runtimeSettings?.default_target_branch || "",
      });
      setKnowledgeDocs([]);
      setSelectedIssueId("");
      setSelectedFindingId("");
      setDecisionComment("");
      setFindingModalOpen(false);
      setActiveStep(requestedTab || "overview");
      return;
    }
    setKnowledgeDocs([]);
    setSelectedIssueId("");
    setSelectedFindingId("");
    setDecisionComment("");
    setFindingModalOpen(false);
    void loadWorkspaceData(reviewId, { forceFullReview: true });
  }, [activeStep, processMainTab, requestedTab, reviewId]);

  useEffect(() => {
    if (reviewId || !runtimeSettings) return;
    setForm((current) => ({
      ...current,
      analysis_mode: current.analysis_mode || runtimeSettings.default_analysis_mode || "standard",
      target_ref: current.target_ref || runtimeSettings.default_target_branch || "",
      selected_experts:
        current.selected_experts.length > 0 ? current.selected_experts : defaultFormState.selected_experts,
    }));
  }, [reviewId, runtimeSettings]);

  useEffect(() => {
    if (!requestedTab || requestedTab === activeStep) return;
    setActiveStep(requestedTab);
  }, [activeStep, requestedTab]);

  useEffect(() => {
    // 根据 review 状态自动切换当前工作台页签：
    // - 运行中优先过程页
    // - 完成/失败优先结果页
    if (requestedTab) return;
    if (!review) return;
    if (review.status === "completed" || review.status === "failed") {
      setActiveStep("result");
      return;
    }
    if (reviewId) {
      setActiveStep("process");
    }
  }, [requestedTab, review, reviewId]);

  useEffect(() => {
    // 过程页使用 SSE 增量刷新，保证主 Agent / 专家消息尽快出现在界面上。
    if (!reviewId || activeStep !== "process") return;
    return subscribeReviewEventStream(buildReviewEventStreamUrl(reviewId), () => {
      void loadWorkspaceData(reviewId);
    });
  }, [activeStep, processMainTab, reviewId]);

  useEffect(() => {
    // 对 running/pending 状态再补一层轮询兜底，防止流式连接偶发断开。
    if (!reviewId || activeStep !== "process") return;
    if (!review || !["pending", "running"].includes(review.status)) return;
    const timer = window.setInterval(() => {
      void loadWorkspaceData(reviewId);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [activeStep, processMainTab, review, reviewId]);

  useEffect(() => {
    if (activeStep !== "process" || review?.status !== "running" || !review?.started_at) return;
    setElapsedNow(Date.now());
    const timer = window.setInterval(() => {
      setElapsedNow(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, [activeStep, review?.started_at, review?.status]);

  const allMessages = messages;
  const issueFilterDecisions = useMemo(
    () => (activeStep === "result" ? report?.issue_filter_decisions || [] : normalizeIssueFilterDecisions(allMessages)),
    [activeStep, allMessages, report?.issue_filter_decisions],
  );
  const expertSelectionSummary = useMemo(() => readExpertSelectionSummary(review), [review]);
  const overviewExpertSelectionSummary = useMemo(
    () => toOverviewExpertSelectionSummary(expertSelectionSummary),
    [expertSelectionSummary],
  );
  const expertRoutingSummary = useMemo(() => readExpertRoutingSummary(review), [review]);

  const selectedIssue = useMemo(
    () => issues.find((item) => item.issue_id === selectedIssueId) || issues[0] || null,
    [issues, selectedIssueId],
  );
  const issueByFindingId = useMemo(() => {
    const map = new Map<string, DebateIssue>();
    for (const issue of issues) {
      for (const findingId of issue.finding_ids) {
        map.set(findingId, issue);
      }
    }
    return map;
  }, [issues]);
  const findingById = useMemo(() => {
    const map = new Map<string, ReviewFinding>();
    for (const finding of findings) {
      map.set(finding.finding_id, finding);
    }
    return map;
  }, [findings]);
  const selectedFinding = useMemo(
    () => findings.find((item) => item.finding_id === selectedFindingId) || findings[0] || null,
    [findings, selectedFindingId],
  );
  const selectedFindingDetail = useMemo(
    () => (selectedFinding ? resultFindingDetailCache[selectedFinding.finding_id] || selectedFinding : null),
    [resultFindingDetailCache, selectedFinding],
  );
  const selectedFindingIssue = useMemo(
    () => (selectedFinding ? issueByFindingId.get(selectedFinding.finding_id) || null : null),
    [issueByFindingId, selectedFinding],
  );
  const selectedIssueFinding = useMemo(() => {
    if (!selectedIssue) return null;
    for (const findingId of selectedIssue.finding_ids || []) {
      const finding = findingById.get(findingId);
      if (finding) return finding;
    }
    return null;
  }, [findingById, selectedIssue]);
  const issueFindingMap = useMemo(() => {
    const map: Record<string, typeof findings[number] | null> = {};
    for (const issue of issues) {
      map[issue.issue_id] = null;
      for (const findingId of issue.finding_ids || []) {
        const finding = findingById.get(findingId);
        if (finding) {
          map[issue.issue_id] = finding;
          break;
        }
      }
    }
    return map;
  }, [findingById, issues]);
  const issueFilterDecisionByFindingId = useMemo(() => {
    const map = new Map<string, IssueFilterDecision>();
    for (const decision of issueFilterDecisions) {
      for (const findingId of decision.finding_ids || []) {
        if (findingId) map.set(findingId, decision);
      }
    }
    return map;
  }, [issueFilterDecisions]);
  const visibleFindings = useMemo(
    () =>
      findings.filter((finding) => {
        if (issueByFindingId.has(finding.finding_id)) return false;
        const decision = issueFilterDecisionByFindingId.get(finding.finding_id);
        if (decision && THRESHOLD_RULE_CODES.has(decision.rule_code)) return false;
        return true;
      }),
    [findings, issueByFindingId, issueFilterDecisionByFindingId],
  );
  const selectedFindingGovernanceDecision = useMemo(
    () => (selectedFinding ? issueFilterDecisionByFindingId.get(selectedFinding.finding_id) || null : null),
    [issueFilterDecisionByFindingId, selectedFinding],
  );
  const selectedFindingRuleScreening = useMemo(() => {
    if (!selectedFindingDetail || !replay?.messages?.length) return null;
    const candidate = replay.messages
      .slice()
      .reverse()
      .find((message) => {
        if (!["expert_analysis", "expert_ack", "debate_message"].includes(message.message_type)) return false;
        if (message.expert_id !== selectedFindingDetail.expert_id) return false;
        const metadata = message.metadata || {};
        const filePath = typeof metadata.file_path === "string" ? metadata.file_path : "";
        if (filePath && selectedFindingDetail.file_path && filePath !== selectedFindingDetail.file_path) return false;
        return Boolean(metadata.rule_screening);
      });
    return normalizeRuleScreeningMetadata(candidate?.metadata?.rule_screening);
  }, [replay?.messages, selectedFindingDetail]);

  useEffect(() => {
    if (activeStep !== "result" || !reviewId || !selectedFindingId) return;
    if (resultFindingDetailCache[selectedFindingId]) {
      setResultFindingDetailsLoading(false);
      setResultFindingDetailsError("");
      return;
    }
    setResultFindingDetailsLoading(true);
    setResultFindingDetailsError("");
    void reviewApi
      .getFinding(reviewId, selectedFindingId)
      .then((finding) => {
        setResultFindingDetailCache((current) => ({
          ...current,
          [finding.finding_id]: finding,
        }));
      })
      .catch((error: any) => {
        setResultFindingDetailsError(error?.message || "完整问题详情加载失败");
      })
      .finally(() => {
        setResultFindingDetailsLoading(false);
      });
  }, [activeStep, reviewId, resultFindingDetailCache, selectedFindingId]);
  const expertRuleCoverage = useMemo(() => {
    if (!replay?.messages?.length) return [] as ExpertRuleCoverageSummary[];
    const expertNameById = new Map(experts.map((item) => [item.expert_id, item.name_zh || item.expert_id]));
    const seen = new Set<string>();
    const rows: ExpertRuleCoverageSummary[] = [];
    for (const message of replay.messages.slice().reverse()) {
      if (!["expert_analysis", "expert_ack", "debate_message"].includes(message.message_type)) continue;
      if (!message.expert_id || seen.has(message.expert_id)) continue;
      const ruleScreening = normalizeRuleScreeningMetadata(message.metadata?.rule_screening);
      if (!ruleScreening || ruleScreening.total_rules <= 0) continue;
      seen.add(message.expert_id);
      rows.push({
        expert_id: message.expert_id,
        expert_name: expertNameById.get(message.expert_id) || message.expert_id,
        rule_screening: ruleScreening,
      });
    }
    return rows.sort((left, right) => right.rule_screening.matched_rule_count - left.rule_screening.matched_rule_count);
  }, [experts, replay?.messages]);
  const pendingHumanIssues = useMemo(
    () => issues.filter((item) => item.needs_human && item.status !== "resolved"),
    [issues],
  );
  const preferredHumanIssue = selectedFindingIssue?.needs_human
    ? selectedFindingIssue
    : selectedIssue?.needs_human
      ? selectedIssue
      : null;
  const activeHumanIssue = preferredHumanIssue || pendingHumanIssues[0] || null;
  const humanGateUsingFallbackIssue = Boolean(activeHumanIssue && activeHumanIssue !== preferredHumanIssue);

  useEffect(() => {
    if (selectedIssue && selectedIssue.issue_id !== selectedIssueId) {
      setSelectedIssueId(selectedIssue.issue_id);
    }
  }, [selectedIssue, selectedIssueId]);

  useEffect(() => {
    // 右侧知识引用面板跟随当前选中的 issue 专家动态刷新。
    const expertId = selectedIssue?.participant_expert_ids?.[0];
    const changedFiles = review?.subject.changed_files || [];
    if (!expertId || changedFiles.length === 0) {
      setKnowledgeDocs([]);
      return;
    }
    setKnowledgeLoading(true);
    void knowledgeApi
      .retrieve(expertId, changedFiles)
      .then(setKnowledgeDocs)
      .finally(() => setKnowledgeLoading(false));
  }, [selectedIssue, review?.subject.changed_files]);

  const headerTitle = useMemo(() => {
    if (!review) return "多专家代码审核工作台";
    return review.subject.title || `${review.subject.source_ref} -> ${review.subject.target_ref}`;
  }, [review]);
  const processElapsedLabel = useMemo(() => {
    if (!review) return "";
    if (review.status === "running" && review.started_at) {
      const startedAtMs = Date.parse(review.started_at);
      if (Number.isFinite(startedAtMs)) {
        return `当前任务已运行 ${formatElapsedDuration((elapsedNow - startedAtMs) / 1000)}`;
      }
    }
    if (["completed", "failed", "waiting_human"].includes(review.status) && typeof review.duration_seconds === "number") {
      return `本次任务运行耗时 ${formatElapsedDuration(review.duration_seconds)}`;
    }
    return "";
  }, [elapsedNow, review]);

  const workspaceTabs = useMemo(
    () => [
      {
        key: "overview",
        label: "概览与启动",
        hint: "先录入 MR 链接或分支信息，确认审核是否已创建并启动。",
      },
      {
        key: "process",
        label: "审核过程",
        hint: "查看主 Agent 调度、专家发言、代码定位和裁决轨迹。",
      },
      {
        key: "result",
        label: "结论与行动",
        hint: "查看最终 Code Review 报告、问题清单、人工裁决和修复建议。",
      },
    ],
    [],
  );

  const currentTab = workspaceTabs.find((item) => item.key === activeStep) || workspaceTabs[0];
  const currentTabHint =
    reviewId && activeStep === "overview"
      ? "当前是审核记录查看模式，可核对当时提交的审核对象、候选专家、大模型判定的参与专家与 diff 上下文。"
      : currentTab.hint;

  const createPayload = (): Parameters<typeof reviewApi.create>[0] => ({
    subject_type: form.subject_type,
    analysis_mode: form.analysis_mode,
    mr_url: form.mr_url.trim(),
    title: form.title.trim(),
    source_ref: form.source_ref.trim(),
    target_ref: form.target_ref.trim() || "main",
    selected_experts: form.selected_experts,
    design_docs: form.design_docs,
  });

  const createReview = async (autoStart: boolean) => {
    // 概览页主入口：先创建任务，再决定是否自动启动并跳转到过程页。
    if (!form.mr_url.trim() && !form.source_ref.trim()) {
      message.warning("请至少输入 Git MR 链接或源分支信息");
      return;
    }
    setStarting(true);
    try {
      const created = await reviewApi.create(createPayload());
      const search = new URLSearchParams();
      search.set("tab", autoStart ? "process" : "overview");
      if (autoStart) {
        search.set("auto_start", "1");
      }
      navigate(`/review/${created.review_id}?${search.toString()}`);
      setActiveStep(autoStart ? "process" : "overview");
      message.success(autoStart ? `审核已创建，正在启动：${created.review_id}` : `审核已创建：${created.review_id}`);
    } catch (error: any) {
      message.error(error?.message || "创建审核失败");
    } finally {
      setStarting(false);
    }
  };

  useEffect(() => {
    // auto_start 主要用于“创建成功后立即进入过程页”的跳转链路。
    const search = new URLSearchParams(location.search);
    const shouldAutoStart = search.get("auto_start") === "1";
    if (!shouldAutoStart || !reviewId || !review || starting) return;
    if (review.status !== "pending") {
      search.delete("auto_start");
      search.set("tab", requestedTab || "process");
      navigate(`/review/${reviewId}?${search.toString()}`, { replace: true });
      return;
    }
    if (autoStartTriggeredRef.current === reviewId) return;
    autoStartTriggeredRef.current = reviewId;
    openWorkspaceTab("process", { replace: true });
    void (async () => {
      setStarting(true);
      try {
        const started = await reviewApi.start(reviewId);
        setReview((prev) =>
          prev
            ? {
                ...prev,
                status: started.status,
                phase: started.phase,
              }
            : prev,
        );
        await loadWorkspaceData(reviewId);
        message.success("审核已启动");
      } catch (error: any) {
        message.error(error?.message || "启动审核失败");
      } finally {
        setStarting(false);
        search.delete("auto_start");
        search.set("tab", "process");
        navigate(`/review/${reviewId}?${search.toString()}`, { replace: true });
      }
    })();
  }, [location.search, navigate, openWorkspaceTab, requestedTab, review, reviewId, starting]);

  const startExistingReview = async () => {
    // 历史记录里还处于 pending 的任务，可以从这里继续启动。
    if (!reviewId) {
      await createReview(true);
      return;
    }
    openWorkspaceTab("process");
    setStarting(true);
    try {
      const started = await reviewApi.start(reviewId);
      setReview((prev) =>
        prev
          ? {
              ...prev,
              status: started.status,
              phase: started.phase,
            }
          : prev,
      );
      await loadWorkspaceData(reviewId);
      message.success("审核已启动");
    } catch (error: any) {
      message.error(error?.message || "启动审核失败");
    } finally {
      setStarting(false);
    }
  };

  const currentStatus = review?.status || "idle";
  const scrollToRef = (target: React.RefObject<HTMLDivElement | null>) => {
    window.setTimeout(() => {
      target.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 80);
  };
  const focusProcess = () => openWorkspaceTab("process");
  const focusProcessDialogue = () => {
    openWorkspaceTab("process");
    setProcessMainTab("dialogue");
    scrollToRef(processDialogueRef);
  };
  const focusProcessIssues = () => {
    openWorkspaceTab("process");
    setProcessSidebarTab("issues");
    scrollToRef(processIssuesRef);
  };
  const focusResultGroup = (_group?: string) => {
    openWorkspaceTab("result");
    scrollToRef(resultFindingsRef);
  };
  const focusResultSummary = () => {
    openWorkspaceTab("result");
    scrollToRef(resultSummaryRef);
  };
  const focusResultHuman = () => {
    openWorkspaceTab("result");
    scrollToRef(resultHumanRef);
  };
  const canForceCloseReview = Boolean(reviewId && review && ["pending", "running", "waiting_human"].includes(review.status));
  const forceCloseReview = async () => {
    if (!reviewId) return;
    setStarting(true);
    try {
      const closed = await reviewApi.close(reviewId);
      setReview((prev) =>
        prev
          ? {
              ...prev,
              status: closed.status,
              phase: closed.phase,
            }
          : prev,
      );
      await loadWorkspaceData(reviewId, { forceFullReview: true });
      message.success("任务已强制结束");
    } catch (error: any) {
      message.error(error?.message || "强制结束任务失败");
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="review-workbench-page">
      <Card className="module-card review-hero-card" loading={loading}>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Tag color="processing">Code Review Workbench</Tag>
          <Space wrap>
            <Tag
              color={
                currentStatus === "waiting_human" || currentStatus === "failed"
                  ? "error"
                  : currentStatus === "completed"
                    ? "success"
                    : currentStatus === "running"
                      ? "processing"
                      : "default"
              }
            >
              {currentStatus === "idle" ? "未开始" : currentStatus}
            </Tag>
            <Tag color={review?.human_review_status === "requested" ? "error" : "success"}>
              human: {review?.human_review_status || "not_required"}
            </Tag>
          </Space>
          <Title level={3} style={{ margin: 0 }}>
            {headerTitle}
          </Title>
          {review?.report_summary ? (
            <Paragraph className="review-inline-summary">{review.report_summary}</Paragraph>
          ) : null}
          <Space wrap>
            {reviewId ? <Text type="secondary">Review ID: {reviewId}</Text> : <Text type="secondary">尚未创建审核</Text>}
            {reviewId ? <Button onClick={() => reviewId && void loadWorkspaceData(reviewId, { forceFullReview: true })}>刷新</Button> : null}
            {canForceCloseReview ? (
              <Popconfirm
                title="确认强制结束这个未完成任务吗？"
                description="结束后会停止后续审核流程，并把任务状态更新为 closed。"
                okText="确认结束"
                cancelText="取消"
                onConfirm={() => void forceCloseReview()}
              >
                <Button danger loading={starting}>
                  强制结束
                </Button>
              </Popconfirm>
            ) : null}
          </Space>
        </Space>
      </Card>

      <div className="incident-section-nav-shell" style={{ marginTop: 16 }}>
        <div className="incident-section-nav">
          <Tabs
            className="incident-workspace-tabs"
            activeKey={activeStep}
            onChange={(key) => isWorkspaceTabKey(key) && openWorkspaceTab(key)}
            items={workspaceTabs.map((item) => ({
              key: item.key,
              label: item.label,
              children: null,
            }))}
          />
          <Text type="secondary">{currentTabHint}</Text>
        </div>
      </div>

      <div style={{ marginTop: 16 }}>
        <OverviewCards
          status={review?.status || "idle"}
          phase={review?.phase || "not_started"}
          expertCount={
            expertSelectionSummary?.selected_experts.length ||
            review?.selected_experts?.length ||
            form.selected_experts.length
          }
          findingCount={visibleFindings.length}
          issueCount={issues.length}
          humanGateCount={review?.pending_human_issue_ids?.length || 0}
          onStatusClick={focusProcessDialogue}
          onPhaseClick={focusProcessDialogue}
          onExpertClick={focusProcessDialogue}
          onFindingClick={() => focusResultGroup("all")}
          onIssueClick={focusProcessIssues}
          onHumanGateClick={focusResultHuman}
        />
      </div>

      <div style={{ marginTop: 16 }}>
        {activeStep === "overview" && (
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={15}>
              <ReviewOverviewPanel
                form={form}
                loading={loading}
                running={currentStatus === "running"}
                reviewId={reviewId}
                status={currentStatus}
                readonly={isReadonlyOverview}
                experts={experts}
                expertSelectionSummary={overviewExpertSelectionSummary}
                onChange={(patch) => setForm((prev) => ({ ...prev, ...patch }))}
                onStart={() => void (reviewId ? startExistingReview() : createReview(true))}
                onCreateOnly={() => void createReview(false)}
              />
            </Col>
            <Col xs={24} xl={9}>
              <Space direction="vertical" size={16} style={{ width: "100%" }}>
                <ExpertSelectionPanel summary={expertSelectionSummary} />
                <ReviewSubjectPanel review={review} />
                <ArtifactSummaryPanel artifacts={artifacts} />
                <Suspense fallback={<WorkbenchPanelFallback description="Diff 预览加载中..." />}>
                  <DiffPreviewPanel
                    diff={review?.subject?.unified_diff || ""}
                    changedFileCount={review?.subject?.changed_files?.length}
                  />
                </Suspense>
              </Space>
            </Col>
          </Row>
        )}

        {activeStep === "process" && (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            {processElapsedLabel ? (
              <Card className="module-card">
                <Space wrap>
                  <Tag color={review?.status === "running" ? "processing" : "default"}>
                    {review?.status === "running" ? "运行中" : "已结束"}
                  </Tag>
                  <Text>{processElapsedLabel}</Text>
                </Space>
              </Card>
            ) : null}
            <Row gutter={[16, 16]} align="stretch">
              <Col xs={24} xl={15}>
                <div className="process-main-stack">
                  <Space direction="vertical" size={16} style={{ width: "100%" }}>
                    <ExpertSelectionPanel summary={expertSelectionSummary} />
                    <ExpertRoutingPanel summary={expertRoutingSummary} />
                  </Space>
                </div>
              </Col>
              <Col xs={24} xl={9}>
                <div className="process-sidebar-stack process-sidebar-top">
                  <Tabs
                    className="process-sidebar-tabs"
                    activeKey={processSidebarTab}
                    onChange={(key) => setProcessSidebarTab(key as ProcessSidebarTabKey)}
                    destroyOnHidden
                    items={[
                      {
                        key: "issues",
                        label: "议题与工具",
                        children: (
                          <Space direction="vertical" size={16} style={{ width: "100%" }}>
                            <div ref={processIssuesRef}>
                              <Suspense fallback={<WorkbenchPanelFallback description="Issue 列表加载中..." />}>
                                <IssueThreadList
                                  issues={issues}
                                  issueFindingMap={issueFindingMap}
                                  selectedIssueId={selectedIssueId}
                                  onSelect={setSelectedIssueId}
                                />
                              </Suspense>
                            </div>
                            <Suspense fallback={<WorkbenchPanelFallback description="Issue 详情加载中..." />}>
                            <IssueDetailPanel issue={selectedIssue} finding={selectedIssueFinding} />
                            </Suspense>
                            <Suspense fallback={<WorkbenchPanelFallback description="工具轨迹加载中..." />}>
                              <ToolAuditPanel issue={selectedIssue} />
                            </Suspense>
                          </Space>
                        ),
                      },
                      {
                        key: "knowledge",
                        label: "知识引用",
                        children: (
                          <Suspense fallback={<WorkbenchPanelFallback description="知识引用加载中..." />}>
                            <KnowledgeRefPanel documents={knowledgeDocs} loading={knowledgeLoading} />
                          </Suspense>
                        ),
                      },
                      {
                        key: "events",
                        label: "事件时间线",
                        children: <EventTimeline events={events} />,
                      },
                    ]}
                  />
                </div>
              </Col>
            </Row>
            <div className="process-main-stack">
              <Tabs
                className="process-main-tabs"
                activeKey={processMainTab}
                onChange={(key) => setProcessMainTab(key as ProcessMainTabKey)}
                destroyOnHidden
                items={[
                  {
                    key: "dialogue",
                    label: "专家对话流",
                    children: (
                      <div ref={processDialogueRef}>
                        <Card className="module-card process-dialogue-card" title="专家对话流">
                          {reviewId ? (
                            <Suspense fallback={<Empty description="专家对话流加载中..." image={Empty.PRESENTED_IMAGE_SIMPLE} />}>
                              <ReviewDialogueStream messages={allMessages} review={review} events={events} />
                            </Suspense>
                          ) : (
                            <Empty description="请先在“概览与启动”创建一个审核任务。" />
                          )}
                        </Card>
                      </div>
                    ),
                  },
                  {
                    key: "lanes",
                    label: "专家泳道",
                    children: (
                      <Suspense fallback={<WorkbenchPanelFallback description="专家泳道加载中..." />}>
                        <ExpertLaneBoard review={review} messages={allMessages} />
                      </Suspense>
                    ),
                  },
                  {
                    key: "diff",
                    label: "Diff 预览",
                    children: (
                      <Suspense fallback={<WorkbenchPanelFallback description="Diff 预览加载中..." />}>
                        <DiffPreviewPanel
                          diff={review?.subject?.unified_diff || ""}
                          changedFileCount={review?.subject?.changed_files?.length}
                        />
                      </Suspense>
                    ),
                  },
                  {
                    key: "replay",
                    label: "回放控制台",
                    children: (
                      <Suspense fallback={<WorkbenchPanelFallback description="回放控制台加载中..." />}>
                        <ReplayConsolePanel replay={replay} />
                      </Suspense>
                    ),
                  },
                ]}
              />
            </div>
          </Space>
        )}

        {activeStep === "result" && (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Row gutter={[16, 16]} align="stretch">
              <Col xs={24} xl={15}>
                <div ref={resultSummaryRef}>
                  <Suspense fallback={<WorkbenchPanelFallback description="审核报告加载中..." />}>
                    <ReportSummaryPanel
                      className="result-top-card"
                      report={report}
                      findings={findings}
                      issues={issues}
                      issueFilterDecisions={issueFilterDecisions}
                      review={review}
                      onNavigateToGroup={focusResultGroup}
                    />
                  </Suspense>
                </div>
              </Col>
              <Col xs={24} xl={9}>
                <div ref={resultHumanRef}>
                  <Suspense fallback={<WorkbenchPanelFallback description="人工门禁加载中..." />}>
                    <HumanGatePanel
                      className="result-top-card"
                      review={review}
                      selectedIssue={activeHumanIssue}
                      isFallbackIssue={humanGateUsingFallbackIssue}
                      decisionComment={decisionComment}
                      submitting={submittingDecision}
                      onDecisionCommentChange={setDecisionComment}
                      onApprove={async () => {
                        const targetIssue = activeHumanIssue;
                        if (!reviewId || !targetIssue) return;
                        setSubmittingDecision(true);
                        try {
                          await reviewApi.submitHumanDecision(reviewId, {
                            issue_id: targetIssue.issue_id,
                            decision: "approved",
                            comment: decisionComment.trim() || "人工审核确认存在风险，批准进入整改。",
                          });
                          await loadWorkspaceData(reviewId);
                          setDecisionComment("");
                          message.success("已记录人工批准结论");
                        } catch (error: any) {
                          message.error(error?.message || "提交人工结论失败");
                        } finally {
                          setSubmittingDecision(false);
                        }
                      }}
                      onReject={async () => {
                        const targetIssue = activeHumanIssue;
                        if (!reviewId || !targetIssue) return;
                        setSubmittingDecision(true);
                        try {
                          await reviewApi.submitHumanDecision(reviewId, {
                            issue_id: targetIssue.issue_id,
                            decision: "rejected",
                            comment: decisionComment.trim() || "人工审核认为证据不足，暂不采纳。",
                          });
                          await loadWorkspaceData(reviewId);
                          setDecisionComment("");
                          message.success("已记录人工驳回结论");
                        } catch (error: any) {
                          message.error(error?.message || "提交人工结论失败");
                        } finally {
                          setSubmittingDecision(false);
                        }
                      }}
                    />
                  </Suspense>
                </div>
              </Col>
            </Row>
            <ExpertRuleCoveragePanel items={expertRuleCoverage} />
            <Suspense fallback={<WorkbenchPanelFallback description="正式 issue 清单加载中..." />}>
              <ResultIssuePanel
                reviewId={reviewId}
                issues={issues}
                findings={findings}
                selectedIssueId={selectedIssueId}
                onSelectIssue={(issueId) => {
                  setSelectedIssueId(issueId);
                  const issue = issues.find((item) => item.issue_id === issueId);
                  const findingId = issue?.finding_ids.find((id) => findingById.has(id));
                  if (findingId) {
                    setSelectedFindingId(findingId);
                    setFindingModalOpen(true);
                  }
                }}
              />
            </Suspense>
            <Suspense fallback={<WorkbenchPanelFallback description="阈值过滤问题清单加载中..." />}>
              <IssueThresholdFilteredPanel
                findings={findings}
                issueFilterDecisions={issueFilterDecisions}
                onSelectFinding={(findingId) => {
                  setSelectedFindingId(findingId);
                  const issue = issueByFindingId.get(findingId);
                  if (issue) setSelectedIssueId(issue.issue_id);
                  setFindingModalOpen(true);
                }}
              />
            </Suspense>
            <div ref={resultFindingsRef}>
              <Suspense fallback={<WorkbenchPanelFallback description="问题清单加载中..." />}>
                <FindingsPanel
                  findings={findings}
                  issues={issues}
                  issueFilterDecisions={issueFilterDecisions}
                  selectedFindingId={selectedFindingId}
                  onSelectFinding={(findingId) => {
                    setSelectedFindingId(findingId);
                    const issue = issueByFindingId.get(findingId);
                    if (issue) setSelectedIssueId(issue.issue_id);
                    setFindingModalOpen(true);
                  }}
                />
              </Suspense>
            </div>
            <ReviewSubjectPanel review={review} />
            <ArtifactSummaryPanel artifacts={artifacts} />
            <Modal
              title="问题详情"
              open={findingModalOpen}
              onCancel={() => setFindingModalOpen(false)}
              footer={null}
              width={1240}
              destroyOnHidden={false}
            >
              <Suspense fallback={<WorkbenchPanelFallback description="问题详情加载中..." />}>
                <CodeReviewConclusionPanel
                  finding={selectedFindingDetail}
                  issue={selectedFindingIssue}
                  governanceDecision={selectedFindingGovernanceDecision}
                  ruleScreening={selectedFindingRuleScreening}
                  findingDetailsLoading={resultFindingDetailsLoading}
                  findingDetailsError={resultFindingDetailsError}
                  onJumpToProcess={() => {
                    setFindingModalOpen(false);
                    setActiveStep("process");
                    if (selectedFindingIssue) setSelectedIssueId(selectedFindingIssue.issue_id);
                  }}
                />
              </Suspense>
            </Modal>
          </Space>
        )}
      </div>
    </div>
  );
};

export default ReviewWorkbenchPage;
