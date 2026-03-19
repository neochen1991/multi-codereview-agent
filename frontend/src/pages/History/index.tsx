import React, { useEffect, useState } from "react";
import { Button, Card, Popconfirm, Space, Table, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";

import { reviewApi, type ReviewSummary } from "@/services/api";

const buildReviewLabel = (record: ReviewSummary) =>
  record.subject.title || `${record.subject.source_ref} -> ${record.subject.target_ref}`;

const formatDateTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";

const formatDuration = (seconds?: number | null) => {
  if (seconds == null) return "-";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const remain = Math.round(seconds % 60);
  return `${minutes}m ${remain}s`;
};

const formatAnalysisMode = (value?: string) => {
  if (value === "light") {
    return { label: "轻量模式", color: "gold" as const };
  }
  return { label: "标准模式", color: "blue" as const };
};

const statusColor = (value?: string) => {
  if (value === "running") return "processing";
  if (value === "completed") return "success";
  if (value === "failed") return "error";
  if (value === "closed") return "warning";
  return "default";
};

// 历史记录页用于回看审核结果，并从“查看工作台”跳回详情。
const HistoryPage: React.FC = () => {
  const navigate = useNavigate();
  const [reviews, setReviews] = useState<ReviewSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [closingReviewId, setClosingReviewId] = useState("");

  const openReviewTab = (reviewId: string, tab: "overview" | "process" | "result") => {
    navigate(`/review/${reviewId}?tab=${tab}`);
  };

  const loadReviews = async () => {
    setLoading(true);
    try {
      const rows = await reviewApi.list();
      setReviews(
        rows
          .slice()
          .sort(
            (left, right) =>
              new Date(right.updated_at || right.created_at || 0).getTime() -
              new Date(left.updated_at || left.created_at || 0).getTime(),
          ),
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadReviews();
  }, []);

  const columns: ColumnsType<ReviewSummary> = [
    { title: "Review ID", dataIndex: "review_id", key: "review_id", width: 160 },
    {
      title: "标题",
      key: "title",
      width: 260,
      render: (_, record) => (
        <div className="review-table-title-cell" title={buildReviewLabel(record)}>
          {buildReviewLabel(record)}
        </div>
      ),
    },
    {
      title: "MR 链接",
      key: "mr_url",
      width: 280,
      render: (_, record) =>
        record.subject.mr_url ? (
          <a
            className="review-table-link-cell"
            href={record.subject.mr_url}
            target="_blank"
            rel="noreferrer"
            title={record.subject.mr_url}
          >
            {record.subject.mr_url}
          </a>
        ) : (
          "-"
        ),
    },
    {
      title: "阶段",
      dataIndex: "phase",
      key: "phase",
      width: 140,
      render: (value: string) => <Tag color="processing">{value}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (value: string, record) => (
        <Space size={6} wrap>
          <Tag color={statusColor(value)}>{value}</Tag>
          {record.subject.metadata?.trigger_source === "auto_scheduler" ? <Tag color="purple">自动队列</Tag> : null}
        </Space>
      ),
    },
    {
      title: "模式",
      dataIndex: "analysis_mode",
      key: "analysis_mode",
      width: 120,
      render: (value?: string) => {
        const mode = formatAnalysisMode(value);
        return <Tag color={mode.color}>{mode.label}</Tag>;
      },
    },
    {
      title: "人工裁决",
      dataIndex: "human_review_status",
      key: "human_review_status",
      width: 140,
      render: (value?: string) => (
        <Tag color={value === "requested" ? "error" : value === "approved" ? "success" : "default"}>
          {value || "not_required"}
        </Tag>
      ),
    },
    {
      title: "开始时间",
      key: "started_at",
      width: 180,
      render: (_, record) => formatDateTime(record.started_at || record.created_at),
    },
    {
      title: "分析耗时",
      key: "duration_seconds",
      width: 120,
      render: (_, record) => formatDuration(record.duration_seconds),
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
          {record.status === "running" ? (
            <Popconfirm
              title="确认关闭这个运行中的任务吗？"
              description="关闭后会停止后续审核流程，并把任务状态更新为 closed。"
              okText="确认关闭"
              cancelText="取消"
              onConfirm={async () => {
                setClosingReviewId(record.review_id);
                try {
                  await reviewApi.close(record.review_id);
                  message.success("任务已关闭");
                  await loadReviews();
                } catch (error: any) {
                  message.error(error?.message || "关闭任务失败");
                } finally {
                  setClosingReviewId("");
                }
              }}
            >
              <Button type="link" size="small" danger loading={closingReviewId === record.review_id}>
                关闭
              </Button>
            </Popconfirm>
          ) : null}
        </Space>
      ),
    },
  ];

  return (
    <Card
      className="module-card"
      title="历史审核记录"
      extra={
        <Button onClick={() => void loadReviews()} loading={loading}>
          刷新列表
        </Button>
      }
    >
      <Table
        className="review-list-table"
        rowKey="review_id"
        columns={columns}
        dataSource={reviews}
        loading={loading}
        scroll={{ x: 1520 }}
      />
    </Card>
  );
};

export default HistoryPage;
