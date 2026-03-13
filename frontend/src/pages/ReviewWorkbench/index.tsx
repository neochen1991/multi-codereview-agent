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

const { Paragraph, Text, Title } = Typography;

const defaultFormState: ReviewFormState = {
  subject_type: "mr",
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

const ReviewWorkbenchPage: React.FC = () => {
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
    void Promise.all([expertApi.list(), settingsApi.getRuntime()])
      .then(([rows, runtime]) => {
        setExperts(rows.filter((item) => item.enabled));
        setRuntimeSettings(runtime);
        if (!reviewId) {
          setForm((current) => ({
            ...current,
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
    if (!reviewId) {
      setReview(null);
      setReplay(null);
      setArtifacts(null);
      setForm({
        ...defaultFormState,
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
    if (!reviewId) return;
    return subscribeReviewEventStream(buildReviewEventStreamUrl(reviewId), () => {
      void loadReviewBundle(reviewId);
    });
  }, [reviewId]);

  useEffect(() => {
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
          <Paragraph style={{ marginBottom: 0 }}>
            页面布局直接参考故障分析页的 V1 工作台结构，统一改成“概览与启动 / 审核过程 /
            结论与行动”三段式；审核过程页重点展示主 Agent 协调和专家聊天式对话流。
          </Paragraph>
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
              width={980}
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
