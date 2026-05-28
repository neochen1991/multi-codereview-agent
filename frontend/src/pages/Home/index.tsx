import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Col, Row, Space, Table, Tag, Tooltip, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  ArrowRightOutlined,
  BookOutlined,
  CodeOutlined,
  DashboardOutlined,
  ExperimentOutlined,
  HistoryOutlined,
  RobotOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";

import { projectApi, reviewApi, settingsApi, type GitNexusIndexStatus, type ReviewSummary } from "@/services/api";
import { getReviewStatusColor, getReviewStatusLabel } from "@/utils/reviewStatus";

type QuickEntry = {
  key: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  path: string;
  accentClass: string;
};

const buildReviewLabel = (record: ReviewSummary) =>
  record.subject.title || `${record.subject.source_ref} -> ${record.subject.target_ref}`;

const { Text } = Typography;

// 首页现在承担平台入口职责：展示系统状态、最近审核和关键导航，不再直接创建审核。
const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const [reviews, setReviews] = useState<ReviewSummary[]>([]);
  const [pendingQueue, setPendingQueue] = useState<ReviewSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncingQueue, setSyncingQueue] = useState(false);
  const [queueStartingId, setQueueStartingId] = useState("");
  const [gitnexusStatus, setGitnexusStatus] = useState<GitNexusIndexStatus | null>(null);
  const [currentProjectId, setCurrentProjectId] = useState("");
  const initialLoadStartedRef = useRef(false);

  const openReviewTab = (reviewId: string, tab: "overview" | "process" | "result") => {
    navigate(`/review/${reviewId}?tab=${tab}`);
  };

  const loadReviews = useCallback(async () => {
    setLoading(true);
    try {
      const projectPayload = await projectApi.list();
      const projectId = projectPayload.default_project_id || projectPayload.projects?.[0]?.project_id || "";
      setCurrentProjectId(projectId);
      const [allReviews, queueRows, gitnexus] = await Promise.all([
        reviewApi.list(projectId),
        reviewApi.listQueue(projectId),
        settingsApi.getGitNexusIndexStatus().catch(() => null),
      ]);
      setReviews(allReviews);
      setPendingQueue(queueRows);
      setGitnexusStatus(gitnexus);
    } catch (error: any) {
      message.error(error?.message || "加载审核列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (initialLoadStartedRef.current) {
      return;
    }
    initialLoadStartedRef.current = true;
    void loadReviews();
  }, [loadReviews]);

  useEffect(() => {
    const handleProjectChanged = () => {
      void loadReviews();
    };
    window.addEventListener("project-changed", handleProjectChanged);
    return () => window.removeEventListener("project-changed", handleProjectChanged);
  }, [loadReviews]);

  const stats = useMemo(() => {
    const total = reviews.length;
    const completed = reviews.filter((item) => item.status === "completed").length;
    const running = reviews.filter((item) => item.status === "running").length;
    const queued = pendingQueue.length;
    const pendingHuman = reviews.filter((item) => item.human_review_status === "requested").length;
    const completedIn24h = reviews.filter((item) => {
      if (item.status !== "completed") return false;
      const updatedAt = new Date(item.updated_at || item.completed_at || item.created_at || 0).getTime();
      return Number.isFinite(updatedAt) && Date.now() - updatedAt <= 24 * 60 * 60 * 1000;
    }).length;
    return { total, completed, running, queued, pendingHuman, completedIn24h };
  }, [pendingQueue.length, reviews]);

  const quickEntries = useMemo<QuickEntry[]>(
    () => [
      {
        key: "review",
        title: "开始一次新审核",
        description: "进入检视工作台，粘贴 Git 平台链接、选择检查范围并启动分析。",
        icon: <CodeOutlined />,
        path: "/review",
        accentClass: "home-entry-review",
      },
      {
        key: "experts",
        title: "管理检查角色与规范",
        description: "查看检查职责、核心规范文档和运行时工具绑定。",
        icon: <RobotOutlined />,
        path: "/experts",
        accentClass: "home-entry-experts",
      },
      {
        key: "knowledge",
        title: "维护知识库",
        description: "上传并绑定 Markdown 文档，让代码检视时引用团队知识和规则。",
        icon: <BookOutlined />,
        path: "/knowledge",
        accentClass: "home-entry-knowledge",
      },
      {
        key: "benchmark",
        title: "评估检视效果",
        description: "选择指定 MR 发起评测，对照正式问题、证据覆盖和影响分析质量。",
        icon: <ExperimentOutlined />,
        path: "/benchmark",
        accentClass: "home-entry-benchmark",
      },
      {
        key: "settings",
        title: "调整系统设置",
        description: "维护代码仓、平台凭据、模型参数和标准/轻量运行模式。",
        icon: <SettingOutlined />,
        path: "/settings",
        accentClass: "home-entry-settings",
      },
    ],
    [],
  );

  const systemHints = useMemo(() => {
    const hints: Array<{ title: string; tone: "warning" | "info" | "success" }> = [];
    if (stats.pendingHuman > 0) {
      hints.push({
        title: `当前有 ${stats.pendingHuman} 条审核待人工确认，建议优先从历史记录或检视工作台进入处理。`,
        tone: "warning",
      });
    }
    if (stats.running > 0) {
      hints.push({
        title: `当前有 ${stats.running} 条审核仍在运行中，首页适合看全局状态，详细过程请进入检视工作台。`,
        tone: "info",
      });
    }
    if (stats.total === 0) {
      hints.push({
        title: "当前还没有审核记录，可以从“开始一次新审核”进入工作台发起第一条审查任务。",
        tone: "success",
      });
    }
    return hints.slice(0, 3);
  }, [stats.pendingHuman, stats.running, stats.total]);

  const recentReviews = useMemo(() => reviews.slice(0, 6), [reviews]);

  const columns: ColumnsType<ReviewSummary> = [
    { title: "Review ID", dataIndex: "review_id", key: "review_id", width: 150 },
    {
      title: "主题",
      key: "subject",
      width: 260,
      render: (_, record) => (
        <div className="review-table-title-cell" title={buildReviewLabel(record)}>
          {buildReviewLabel(record)}
        </div>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 180,
      render: (value: string, record) => (
        <Space size={6} wrap>
          <Tag color={getReviewStatusColor(value)}>{getReviewStatusLabel(value)}</Tag>
          {record.analysis_mode === "light" ? <Tag color="gold">轻量模式</Tag> : <Tag color="blue">标准模式</Tag>}
        </Space>
      ),
    },
    {
      title: "更新时间",
      key: "updated_at",
      width: 180,
      render: (_, record) =>
        new Date(record.updated_at || record.completed_at || record.created_at || 0).toLocaleString("zh-CN"),
    },
    {
      title: "操作",
      key: "action",
      width: 220,
      render: (_, record) => (
        <Space size={4} wrap>
          <Button type="link" size="small" onClick={() => openReviewTab(record.review_id, "overview")}>
            概览
          </Button>
          <Button type="link" size="small" onClick={() => openReviewTab(record.review_id, "process")}>
            过程
          </Button>
          <Button type="link" size="small" onClick={() => openReviewTab(record.review_id, "result")}>
            结果
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="home-page">
      <Card className="module-card home-hero-card">
        <Row gutter={[24, 24]} align="middle">
          <Col xs={24} xl={15}>
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Tag color="processing" style={{ width: "fit-content" }}>
                代码检视工作台
              </Tag>
              <h2 className="home-title">代码检视协同平台</h2>
              <p className="home-subtitle">
                首页展示平台状态、最近审核和关键入口。新建与启动审核统一放在“检视工作台”中完成，
                让首页更像系统总入口，而不是表单页面。
              </p>
              <Space wrap size="middle">
                <Button type="primary" size="large" icon={<CodeOutlined />} onClick={() => navigate("/review")}>
                  进入检视工作台
                </Button>
                <Button size="large" icon={<HistoryOutlined />} onClick={() => navigate("/history")}>
                  查看历史记录
                </Button>
                <Button
                  size="large"
                  loading={syncingQueue}
                  onClick={async () => {
                    setSyncingQueue(true);
                    try {
                      const result = await reviewApi.syncQueue(currentProjectId);
                      if (result.message) {
                        message.info(result.message);
                      } else {
                        message.success(
                          `队列同步完成：新增 ${result.created_count} 条，${result.started_review_id ? `已启动 ${result.started_review_id}` : "当前未启动新任务"}`,
                        );
                      }
                      await loadReviews();
                    } catch (error: any) {
                      message.error(error?.message || "同步待处理队列失败");
                    } finally {
                      setSyncingQueue(false);
                    }
                  }}
                >
                  立即同步 MR 队列
                </Button>
                <Button size="large" icon={<DashboardOutlined />} onClick={() => navigate("/governance")}>
                  查看治理中心
                </Button>
              </Space>
            </Space>
          </Col>
          <Col xs={24} xl={9}>
            <div className="home-hero-status-grid">
              <div className="home-hero-status-card">
                <span className="home-hero-status-label">运行中</span>
                <strong>{stats.running}</strong>
              </div>
              <div className="home-hero-status-card">
                <span className="home-hero-status-label">待处理队列</span>
                <strong>{stats.queued}</strong>
              </div>
              <div className="home-hero-status-card">
                <span className="home-hero-status-label">待人工确认</span>
                <strong>{stats.pendingHuman}</strong>
              </div>
              <div className="home-hero-status-card">
                <span className="home-hero-status-label">24h 完成数</span>
                <strong>{stats.completedIn24h}</strong>
              </div>
              <div className="home-hero-status-card">
                <span className="home-hero-status-label">累计审核</span>
                <strong>{stats.total}</strong>
              </div>
            </div>
          </Col>
        </Row>
      </Card>

      {gitnexusStatus?.gitnexus_installed === false ? (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          message="当前机器未预装 GitNexus"
          description="关联影响分析仍会展示，但会自动降级为 diff/路径规则报告。建议先到设置页完成 GitNexus 安装检查。"
          action={
            <Button size="small" onClick={() => navigate("/settings")}>
              前往设置页
            </Button>
          }
        />
      ) : null}

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24}>
          <Card className="module-card" title="待处理 MR 队列">
            <Table
              className="review-list-table"
              rowKey="review_id"
              loading={loading}
              dataSource={pendingQueue}
              pagination={false}
              scroll={{ x: 1120 }}
              columns={[
                {
                  title: "排队顺序",
                  width: 100,
                  render: (_, record: ReviewSummary, index) => record.queue_position || index + 1,
                },
                {
                  title: "MR 标题",
                  key: "title",
                  width: 260,
                  render: (_, record: ReviewSummary) => (
                    <div className="review-table-title-cell" title={buildReviewLabel(record)}>
                      {buildReviewLabel(record)}
                    </div>
                  ),
                },
                {
                  title: "MR 链接",
                  dataIndex: ["subject", "mr_url"],
                  width: 280,
                  render: (value: string) =>
                    value ? (
                      <a className="review-table-link-cell" href={value} target="_blank" rel="noreferrer" title={value}>
                        {value}
                      </a>
                    ) : (
                      "-"
                    ),
                },
                {
                  title: "创建时间",
                  width: 180,
                  render: (_, record: ReviewSummary) =>
                    new Date(record.created_at || record.updated_at || 0).toLocaleString("zh-CN"),
                },
                {
                  title: "状态",
                  width: 320,
                  render: (_, record: ReviewSummary) => (
                    <Space direction="vertical" size={4} style={{ width: "100%" }}>
                      <Space size={6} wrap>
                        <Tag color={record.is_next_candidate ? "processing" : "default"}>
                          {record.is_next_candidate ? "等待自动启动" : "排队中"}
                        </Tag>
                        <Tag color={getReviewStatusColor(record.status)}>{getReviewStatusLabel(record.status)}</Tag>
                      </Space>
                      <Text type="secondary" style={{ whiteSpace: "normal", wordBreak: "break-word" }}>
                        {record.queue_blocker_message || "等待调度器拉起审核任务。"}
                      </Text>
                      {record.blocking_review_id ? (
                        <Tooltip title={`当前阻塞任务：${record.blocking_review_id}`}>
                          <Text type="secondary">阻塞任务：{record.blocking_review_id}</Text>
                        </Tooltip>
                      ) : null}
                    </Space>
                  ),
                },
                {
                  title: "操作",
                  width: 240,
                  render: (_, record: ReviewSummary) => (
                    <Space size={4} wrap>
                      <Button
                        type="primary"
                        size="small"
                        loading={queueStartingId === record.review_id}
                        onClick={async () => {
                          setQueueStartingId(record.review_id);
                          try {
                            const result = await reviewApi.queueStart(record.review_id);
                            message.success(result.message || "任务启动请求已提交");
                            await loadReviews();
                          } catch (error: any) {
                            message.error(error?.message || "启动队列任务失败");
                          } finally {
                            setQueueStartingId("");
                          }
                        }}
                      >
                        {record.is_next_candidate ? "立即启动" : "插队启动"}
                      </Button>
                      <Button type="link" size="small" onClick={() => openReviewTab(record.review_id, "overview")}>
                        概览
                      </Button>
                      <Button type="link" size="small" onClick={() => openReviewTab(record.review_id, "process")}>
                        过程
                      </Button>
                    </Space>
                  ),
                },
              ]}
              locale={{ emptyText: "当前没有待处理任务，自动调度会在后台持续拉取开放中的 MR。" }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} xl={16}>
          <Card className="module-card" title="最近审核">
            <Table
              className="review-list-table"
              rowKey="review_id"
              columns={columns}
              dataSource={recentReviews}
              loading={loading}
              pagination={false}
              scroll={{ x: 1020 }}
            />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card className="module-card" title="快速导航">
            <div className="home-entry-grid">
              {quickEntries.map((entry) => (
                <button
                  key={entry.key}
                  type="button"
                  className={`home-entry-card ${entry.accentClass}`}
                  onClick={() => navigate(entry.path)}
                >
                  <div className="home-entry-icon">{entry.icon}</div>
                  <div className="home-entry-body">
                    <div className="home-entry-title">{entry.title}</div>
                    <div className="home-entry-desc">{entry.description}</div>
                  </div>
                  <ArrowRightOutlined className="home-entry-arrow" />
                </button>
              ))}
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24}>
          <Card className="module-card" title="系统提示">
            <div className="home-hint-list">
              {systemHints.length > 0 ? (
                systemHints.map((hint, index) => (
                  <div key={`${hint.title}-${index}`} className={`home-hint-card home-hint-${hint.tone}`}>
                    <span className="home-hint-dot" />
                    <span>{hint.title}</span>
                  </div>
                ))
              ) : (
                <div className="home-hint-card home-hint-success">
                  <span className="home-hint-dot" />
                  <span>当前系统运行平稳，可以直接进入检视工作台发起新的代码审查。</span>
                </div>
              )}
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default HomePage;
