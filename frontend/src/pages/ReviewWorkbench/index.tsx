import React, { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { App as AntdApp, Button, Card, Col, Empty, Modal, Row, Space, Tabs, Tag, Typography } from "antd";
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
  type ReviewReplayBundle,
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
const IssueThreadList = lazy(() => import("@/components/review/IssueThreadList"));
const KnowledgeRefPanel = lazy(() => import("@/components/review/KnowledgeRefPanel"));
const ReplayConsolePanel = lazy(() => import("@/components/review/ReplayConsolePanel"));
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
  selected_experts: [
    "correctness_business",
    "architecture_design",
    "security_compliance",
    "performance_reliability",
    "maintainability_code_health",
    "test_verification",
  ],
};

const WORKSPACE_TAB_KEYS: WorkspaceTabKey[] = ["overview", "process", "result"];

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
  if (!summary) return null;
  return (
    <Card className="module-card expert-routing-card expert-routing-card-info" title="专家参与判定">
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Paragraph className="expert-routing-summary">
          主Agent 已基于当前 MR 信息、完整 diff 和专家画像，先由大模型判定本次真正需要参与审核的专家集合。
        </Paragraph>
        <RoutingExpertTags title="大模型选中" items={summary.selected_experts} color="green" />
        {summary.requested_expert_ids.length > 0 ? (
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
        {summary.skipped_experts.length > 0 ? (
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
  const [artifacts, setArtifacts] = useState<ReviewArtifacts | null>(null);
  const [experts, setExperts] = useState<ExpertProfile[]>([]);
  const [runtimeSettings, setRuntimeSettings] = useState<RuntimeSettings | null>(null);
  const [knowledgeDocs, setKnowledgeDocs] = useState<KnowledgeDocument[]>([]);
  const [selectedIssueId, setSelectedIssueId] = useState("");
  const [selectedFindingId, setSelectedFindingId] = useState("");
  const [findingsActiveGroup, setFindingsActiveGroup] = useState<
    | "all"
    | "blocking"
    | "should_fix"
    | "non_blocking"
    | "verified"
    | "design_misaligned"
    | "direct_defect"
    | "risk_hypothesis"
    | "test_gap"
    | "design_concern"
  >("all");
  const [decisionComment, setDecisionComment] = useState("");
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [submittingDecision, setSubmittingDecision] = useState(false);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [findingModalOpen, setFindingModalOpen] = useState(false);
  const [form, setForm] = useState<ReviewFormState>(defaultFormState);
  const autoStartTriggeredRef = useRef<string>("");
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

  const loadReviewBundle = async (targetReviewId: string) => {
    // 工作台恢复和轮询刷新统一走这一条数据加载链，
    // 确保概览、过程、结果看到的都是同一份后端快照。
    setLoading(true);
    try {
      const [detail, replayBundle, artifactBundle] = await Promise.all([
        reviewApi.get(targetReviewId),
        reviewApi.getReplay(targetReviewId),
        reviewApi.getArtifacts(targetReviewId).catch(() => null),
      ]);
      setReview(replayBundle?.review || detail);
      setReplay(replayBundle);
      setArtifacts(artifactBundle);
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
      setSelectedIssueId((prev) =>
        replayBundle.issues.some((item) => item.issue_id === prev) ? prev : replayBundle.issues[0]?.issue_id || "",
      );
      setSelectedFindingId((prev) =>
        replayBundle.report.findings.some((item) => item.finding_id === prev)
          ? prev
          : replayBundle.report.findings[0]?.finding_id || "",
      );
    } catch (error: any) {
      message.error(error?.message || "加载审核详情失败");
    } finally {
      setLoading(false);
    }
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
      setArtifacts(null);
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
    void loadReviewBundle(reviewId);
  }, [requestedTab, reviewId, runtimeSettings]);

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
    if (!reviewId) return;
    return subscribeReviewEventStream(buildReviewEventStreamUrl(reviewId), () => {
      void loadReviewBundle(reviewId);
    });
  }, [reviewId]);

  useEffect(() => {
    // 对 running/pending 状态再补一层轮询兜底，防止流式连接偶发断开。
    if (!reviewId) return;
    if (!review || !["pending", "running"].includes(review.status)) return;
    const timer = window.setInterval(() => {
      void loadReviewBundle(reviewId);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [review, reviewId]);

  const issues = replay?.issues || [];
  const events = replay?.events || [];
  const report = replay?.report || null;
  const findings = report?.findings || [];
  const allMessages = replay?.messages || [];
  const issueFilterDecisions = useMemo(() => normalizeIssueFilterDecisions(allMessages), [allMessages]);
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
  const selectedFinding = useMemo(
    () => findings.find((item) => item.finding_id === selectedFindingId) || findings[0] || null,
    [findings, selectedFindingId],
  );
  const selectedFindingIssue = useMemo(
    () => (selectedFinding ? issueByFindingId.get(selectedFinding.finding_id) || null : null),
    [issueByFindingId, selectedFinding],
  );
  const issueFilterDecisionByFindingId = useMemo(() => {
    const map = new Map<string, IssueFilterDecision>();
    for (const decision of issueFilterDecisions) {
      for (const findingId of decision.finding_ids || []) {
        if (findingId) map.set(findingId, decision);
      }
    }
    return map;
  }, [issueFilterDecisions]);
  const selectedFindingGovernanceDecision = useMemo(
    () => (selectedFinding ? issueFilterDecisionByFindingId.get(selectedFinding.finding_id) || null : null),
    [issueFilterDecisionByFindingId, selectedFinding],
  );
  const selectedFindingRuleScreening = useMemo(() => {
    if (!selectedFinding || !replay?.messages?.length) return null;
    const candidate = replay.messages
      .slice()
      .reverse()
      .find((message) => {
        if (!["expert_analysis", "expert_ack", "debate_message"].includes(message.message_type)) return false;
        if (message.expert_id !== selectedFinding.expert_id) return false;
        const metadata = message.metadata || {};
        const filePath = typeof metadata.file_path === "string" ? metadata.file_path : "";
        if (filePath && selectedFinding.file_path && filePath !== selectedFinding.file_path) return false;
        return Boolean(metadata.rule_screening);
      });
    return normalizeRuleScreeningMetadata(candidate?.metadata?.rule_screening);
  }, [replay?.messages, selectedFinding]);
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
        await loadReviewBundle(reviewId);
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
      await loadReviewBundle(reviewId);
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
  const focusResultGroup = (
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
  ) => {
    setFindingsActiveGroup(group);
    openWorkspaceTab("result");
    scrollToRef(resultFindingsRef);
  };
  const focusResultSummary = () => {
    openWorkspaceTab("result");
    scrollToRef(resultSummaryRef);
  };
  const focusResultHuman = () => {
    setFindingsActiveGroup("blocking");
    openWorkspaceTab("result");
    scrollToRef(resultHumanRef);
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
          <Space>
            {reviewId ? <Text type="secondary">Review ID: {reviewId}</Text> : <Text type="secondary">尚未创建审核</Text>}
            {reviewId ? <Button onClick={() => reviewId && void loadReviewBundle(reviewId)}>刷新</Button> : null}
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
          findingCount={findings.length}
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
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={17}>
              <Space direction="vertical" size={16} style={{ width: "100%" }} className="process-main-stack">
                <ExpertSelectionPanel summary={expertSelectionSummary} />
                <ExpertRoutingPanel summary={expertRoutingSummary} />
                <Tabs
                  activeKey={processMainTab}
                  onChange={(key) => setProcessMainTab(key as ProcessMainTabKey)}
                  destroyInactiveTabPane
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
              </Space>
            </Col>
            <Col xs={24} xl={7}>
              <Space direction="vertical" size={16} style={{ width: "100%" }} className="process-sidebar-stack">
                <Tabs
                  activeKey={processSidebarTab}
                  onChange={(key) => setProcessSidebarTab(key as ProcessSidebarTabKey)}
                  destroyInactiveTabPane
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
                                selectedIssueId={selectedIssueId}
                                onSelect={setSelectedIssueId}
                              />
                            </Suspense>
                          </div>
                          <Suspense fallback={<WorkbenchPanelFallback description="Issue 详情加载中..." />}>
                            <IssueDetailPanel issue={selectedIssue} />
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
              </Space>
            </Col>
          </Row>
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
                          await loadReviewBundle(reviewId);
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
                          await loadReviewBundle(reviewId);
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
            <div ref={resultFindingsRef}>
              <Suspense fallback={<WorkbenchPanelFallback description="问题清单加载中..." />}>
                <FindingsPanel
                  findings={findings}
                  issues={issues}
                  issueFilterDecisions={issueFilterDecisions}
                  selectedFindingId={selectedFindingId}
                  activeGroup={findingsActiveGroup}
                  onGroupChange={setFindingsActiveGroup}
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
              destroyOnClose={false}
            >
              <Suspense fallback={<WorkbenchPanelFallback description="问题详情加载中..." />}>
                <CodeReviewConclusionPanel
                  finding={selectedFinding}
                  issue={selectedFindingIssue}
                  governanceDecision={selectedFindingGovernanceDecision}
                  ruleScreening={selectedFindingRuleScreening}
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
