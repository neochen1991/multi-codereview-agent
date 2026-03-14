import React, { useEffect, useMemo, useRef, useState } from "react";
import { App as AntdApp, Button, Card, Col, Empty, Modal, Row, Space, Tabs, Tag, Typography } from "antd";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import ArtifactSummaryPanel from "@/components/review/ArtifactSummaryPanel";
import CodeReviewConclusionPanel from "@/components/review/CodeReviewConclusionPanel";
import DiffPreviewPanel from "@/components/review/DiffPreviewPanel";
import EventTimeline from "@/components/review/EventTimeline";
import FindingsPanel from "@/components/review/FindingsPanel";
import HumanGatePanel from "@/components/review/HumanGatePanel";
import IssueDetailPanel from "@/components/review/IssueDetailPanel";
import IssueThreadList from "@/components/review/IssueThreadList";
import KnowledgeRefPanel from "@/components/review/KnowledgeRefPanel";
import OverviewCards from "@/components/review/OverviewCards";
import ReplayConsolePanel from "@/components/review/ReplayConsolePanel";
import ReportSummaryPanel from "@/components/review/ReportSummaryPanel";
import ReviewDialogueStream from "@/components/review/ReviewDialogueStream";
import ReviewOverviewPanel, { type ReviewFormState } from "@/components/review/ReviewOverviewPanel";
import ReviewSubjectPanel from "@/components/review/ReviewSubjectPanel";
import ToolAuditPanel from "@/components/review/ToolAuditPanel";
import {
  buildReviewEventStreamUrl,
  expertApi,
  knowledgeApi,
  reviewApi,
  settingsApi,
  type DebateIssue,
  type ExpertProfile,
  type KnowledgeDocument,
  type ReviewArtifacts,
  type ReviewReplayBundle,
  type ReviewSummary,
  type RuntimeSettings,
} from "@/services/api";
import { subscribeReviewEventStream } from "@/services/stream";

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

const { Paragraph, Text, Title } = Typography;

const defaultFormState: ReviewFormState = {
  subject_type: "mr",
  analysis_mode: "standard",
  mr_url: "",
  title: "",
  repo_id: "",
  project_id: "",
  source_ref: "",
  target_ref: "",
  access_token: "",
  selected_experts: [
    "correctness_business",
    "architecture_design",
    "security_compliance",
    "performance_reliability",
    "maintainability_code_health",
    "test_verification",
  ],
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

const ReviewWorkbenchPage: React.FC = () => {
  // 这是前端真正的“审核工作台容器”。
  // 概览、过程、结果三个页签共用同一份 review/replay/artifact 状态，
  // 这样页面切换时不会丢失当前审核上下文。
  const { message } = AntdApp.useApp();
  const { reviewId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [activeStep, setActiveStep] = useState("overview");
  const [review, setReview] = useState<ReviewSummary | null>(null);
  const [replay, setReplay] = useState<ReviewReplayBundle | null>(null);
  const [artifacts, setArtifacts] = useState<ReviewArtifacts | null>(null);
  const [experts, setExperts] = useState<ExpertProfile[]>([]);
  const [runtimeSettings, setRuntimeSettings] = useState<RuntimeSettings | null>(null);
  const [knowledgeDocs, setKnowledgeDocs] = useState<KnowledgeDocument[]>([]);
  const [selectedIssueId, setSelectedIssueId] = useState("");
  const [selectedFindingId, setSelectedFindingId] = useState("");
  const [decisionComment, setDecisionComment] = useState("");
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [submittingDecision, setSubmittingDecision] = useState(false);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [findingModalOpen, setFindingModalOpen] = useState(false);
  const [form, setForm] = useState<ReviewFormState>(defaultFormState);
  const autoStartTriggeredRef = useRef<string>("");
  const isReadonlyOverview = Boolean(reviewId);

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
        repo_id: detail.subject.repo_id || "",
        project_id: detail.subject.project_id || "",
        source_ref: detail.subject.source_ref || "",
        target_ref: detail.subject.target_ref || "main",
        access_token: "",
        selected_experts:
          detail.selected_experts && detail.selected_experts.length > 0
            ? detail.selected_experts
            : defaultFormState.selected_experts,
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
      setActiveStep("overview");
      return;
    }
    setKnowledgeDocs([]);
    setSelectedIssueId("");
    setSelectedFindingId("");
    setDecisionComment("");
    setFindingModalOpen(false);
    void loadReviewBundle(reviewId);
  }, [reviewId, runtimeSettings]);

  useEffect(() => {
    // 根据 review 状态自动切换当前工作台页签：
    // - 运行中优先过程页
    // - 完成/失败优先结果页
    if (!review) return;
    if (review.status === "completed" || review.status === "failed") {
      setActiveStep("result");
      return;
    }
    if (reviewId) {
      setActiveStep("process");
    }
  }, [review, reviewId]);

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
  const pendingHumanIssues = useMemo(
    () => issues.filter((item) => item.needs_human && item.status !== "resolved"),
    [issues],
  );
  const expertRoutingSummary = useMemo(() => readExpertRoutingSummary(review), [review]);
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
      ? "当前是审核记录查看模式，可核对当时提交的审核对象、专家选择与 diff 上下文。"
      : currentTab.hint;

  const createPayload = (): Parameters<typeof reviewApi.create>[0] => ({
    subject_type: form.subject_type,
    analysis_mode: form.analysis_mode,
    mr_url: form.mr_url.trim(),
    title: form.title.trim(),
    repo_id: form.repo_id.trim(),
    project_id: form.project_id.trim(),
    source_ref: form.source_ref.trim(),
    target_ref: form.target_ref.trim() || "main",
    access_token: form.access_token.trim(),
    selected_experts: form.selected_experts,
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
      navigate(`/review/${created.review_id}${autoStart ? "?auto_start=1" : ""}`);
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
      if (location.search) navigate(`/review/${reviewId}`, { replace: true });
      return;
    }
    if (autoStartTriggeredRef.current === reviewId) return;
    autoStartTriggeredRef.current = reviewId;
    setActiveStep("process");
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
        navigate(`/review/${reviewId}`, { replace: true });
      }
    })();
  }, [location.search, navigate, review, reviewId, starting]);

  const startExistingReview = async () => {
    // 历史记录里还处于 pending 的任务，可以从这里继续启动。
    if (!reviewId) {
      await createReview(true);
      return;
    }
    setActiveStep("process");
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
            onChange={setActiveStep}
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
          expertCount={review?.selected_experts?.length || form.selected_experts.length}
          findingCount={findings.length}
          issueCount={issues.length}
          humanGateCount={review?.pending_human_issue_ids?.length || 0}
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
                onChange={(patch) => setForm((prev) => ({ ...prev, ...patch }))}
                onStart={() => void (reviewId ? startExistingReview() : createReview(true))}
                onCreateOnly={() => void createReview(false)}
              />
            </Col>
            <Col xs={24} xl={9}>
              <Space direction="vertical" size={16} style={{ width: "100%" }}>
                <ReviewSubjectPanel review={review} />
                <ArtifactSummaryPanel artifacts={artifacts} />
                <DiffPreviewPanel diff={review?.subject?.unified_diff || ""} />
              </Space>
            </Col>
          </Row>
        )}

        {activeStep === "process" && (
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={17}>
              <Space direction="vertical" size={16} style={{ width: "100%" }} className="process-main-stack">
                <ExpertRoutingPanel summary={expertRoutingSummary} />
                <Card className="module-card process-dialogue-card" title="专家对话流">
                  {reviewId ? (
                    <ReviewDialogueStream messages={allMessages} review={review} events={events} />
                  ) : (
                    <Empty description="请先在“概览与启动”创建一个审核任务。" />
                  )}
                </Card>
                <DiffPreviewPanel diff={review?.subject?.unified_diff || ""} />
                <ReplayConsolePanel replay={replay} />
              </Space>
            </Col>
            <Col xs={24} xl={7}>
              <Space direction="vertical" size={16} style={{ width: "100%" }} className="process-sidebar-stack">
                <IssueThreadList
                  issues={issues}
                  selectedIssueId={selectedIssueId}
                  onSelect={setSelectedIssueId}
                />
                <IssueDetailPanel issue={selectedIssue} />
                <ToolAuditPanel issue={selectedIssue} />
                <KnowledgeRefPanel documents={knowledgeDocs} loading={knowledgeLoading} />
                <EventTimeline events={events} />
              </Space>
            </Col>
          </Row>
        )}

        {activeStep === "result" && (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Row gutter={[16, 16]} align="stretch">
              <Col xs={24} xl={15}>
                <ReportSummaryPanel className="result-top-card" report={report} findings={findings} issues={issues} />
              </Col>
              <Col xs={24} xl={9}>
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
              </Col>
            </Row>
            <FindingsPanel
              findings={findings}
              issues={issues}
              selectedFindingId={selectedFindingId}
              onSelectFinding={(findingId) => {
                setSelectedFindingId(findingId);
                const issue = issueByFindingId.get(findingId);
                if (issue) setSelectedIssueId(issue.issue_id);
                setFindingModalOpen(true);
              }}
            />
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
              <CodeReviewConclusionPanel
                finding={selectedFinding}
                issue={selectedFindingIssue}
                onJumpToProcess={() => {
                  setFindingModalOpen(false);
                  setActiveStep("process");
                  if (selectedFindingIssue) setSelectedIssueId(selectedFindingIssue.issue_id);
                }}
              />
            </Modal>
          </Space>
        )}
      </div>
    </div>
  );
};

export default ReviewWorkbenchPage;
