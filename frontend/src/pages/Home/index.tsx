import React, { useEffect, useMemo, useState } from "react";
import { Button, Card, Col, Row, Space, Statistic, Table, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  ArrowRightOutlined,
  BookOutlined,
  CodeOutlined,
  DashboardOutlined,
  HistoryOutlined,
  RobotOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";

import { reviewApi, type ReviewSummary } from "@/services/api";

const statusColor: Record<string, string> = {
  pending: "default",
  running: "processing",
  completed: "success",
  failed: "error",
};

type QuickEntry = {
  key: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  path: string;
  accentClass: string;
};

// 首页现在承担平台入口职责：展示系统状态、最近审核和关键导航，不再直接创建审核。
const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const [reviews, setReviews] = useState<ReviewSummary[]>([]);
  const [loading, setLoading] = useState(false);

  const loadReviews = async () => {
    setLoading(true);
    try {
      setReviews(await reviewApi.list());
    } catch (error: any) {
      message.error(error?.message || "加载审核列表失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadReviews();
  }, []);

  const stats = useMemo(() => {
    const total = reviews.length;
    const completed = reviews.filter((item) => item.status === "completed").length;
    const running = reviews.filter((item) => item.status === "running").length;
    const pendingHuman = reviews.filter((item) => item.human_review_status === "requested").length;
    const completedIn24h = reviews.filter((item) => {
      if (item.status !== "completed") return false;
      const updatedAt = new Date(item.updated_at || item.completed_at || item.created_at || 0).getTime();
      return Number.isFinite(updatedAt) && Date.now() - updatedAt <= 24 * 60 * 60 * 1000;
    }).length;
    return { total, completed, running, pendingHuman, completedIn24h };
  }, [reviews]);

  const quickEntries = useMemo<QuickEntry[]>(
    () => [
      {
        key: "review",
        title: "开始一次新审核",
        description: "进入审核工作台，粘贴 Git 平台链接、选择专家并启动分析。",
        icon: <CodeOutlined />,
        path: "/review",
        accentClass: "home-entry-review",
      },
      {
        key: "experts",
        title: "管理专家与规范",
        description: "查看专家边界、核心规范文档和运行时工具绑定。",
        icon: <RobotOutlined />,
        path: "/experts",
        accentClass: "home-entry-experts",
      },
      {
        key: "knowledge",
        title: "维护知识库",
        description: "上传并绑定 Markdown 文档，让专家审查时引用团队知识和规则。",
        icon: <BookOutlined />,
        path: "/knowledge",
        accentClass: "home-entry-knowledge",
      },
      {
        key: "settings",
        title: "调整系统设置",
        description: "维护代码仓、平台 Token、模型参数和标准/轻量运行模式。",
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
        title: `当前有 ${stats.pendingHuman} 条审核待人工裁决，建议优先从历史记录或审核工作台进入处理。`,
        tone: "warning",
      });
    }
    if (stats.running > 0) {
      hints.push({
        title: `当前有 ${stats.running} 条审核仍在运行中，首页适合看全局状态，详细过程请进入审核工作台。`,
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
      render: (_, record) => record.subject.title || `${record.subject.source_ref} -> ${record.subject.target_ref}`,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 180,
      render: (value: string, record) => (
        <Space size={6} wrap>
          <Tag color={statusColor[value] || "default"}>{value}</Tag>
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
      width: 140,
      render: (_, record) => (
        <Button type="link" onClick={() => navigate(`/review/${record.review_id}`)}>
          查看工作台
        </Button>
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
                Expert Debate Code Review
              </Tag>
              <h2 className="home-title">多专家协同代码审核控制台</h2>
              <p className="home-subtitle">
                首页只负责展示平台状态、最近审核和关键入口。真正的新建与启动审核统一放在“审核工作台”中完成，
                让首页更像系统总入口，而不是表单页面。
              </p>
              <Space wrap size="middle">
                <Button type="primary" size="large" icon={<CodeOutlined />} onClick={() => navigate("/review")}>
                  进入审核工作台
                </Button>
                <Button size="large" icon={<HistoryOutlined />} onClick={() => navigate("/history")}>
                  查看历史记录
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
                <span className="home-hero-status-label">待人工裁决</span>
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

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={6}>
          <Card className="module-card">
            <Statistic title="总审核数" value={stats.total} />
          </Card>
        </Col>
        <Col xs={24} md={6}>
          <Card className="module-card">
            <Statistic title="运行中" value={stats.running} />
          </Card>
        </Col>
        <Col xs={24} md={6}>
          <Card className="module-card">
            <Statistic title="已完成" value={stats.completed} />
          </Card>
        </Col>
        <Col xs={24} md={6}>
          <Card className="module-card">
            <Statistic title="待人工裁决" value={stats.pendingHuman} />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} xl={16}>
          <Card className="module-card" title="最近审核">
            <Table rowKey="review_id" columns={columns} dataSource={recentReviews} loading={loading} pagination={false} />
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
                  <span>当前系统运行平稳，可以直接进入审核工作台发起新的代码审查。</span>
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
