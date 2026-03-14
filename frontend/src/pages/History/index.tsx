import React, { useEffect, useState } from "react";
import { Button, Card, Space, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";

import { reviewApi, type ReviewSummary } from "@/services/api";

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

// 历史记录页用于回看审核结果，并从“查看工作台”跳回详情。
const HistoryPage: React.FC = () => {
  const navigate = useNavigate();
  const [reviews, setReviews] = useState<ReviewSummary[]>([]);
  const [loading, setLoading] = useState(true);

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
      render: (_, record) => record.subject.title || `${record.subject.source_ref} -> ${record.subject.target_ref}`,
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
      render: (value: string) => <Tag>{value}</Tag>,
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
      render: (_, record) => (
        <Space>
          <Button type="link" onClick={() => navigate(`/review/${record.review_id}`)}>
            查看工作台
          </Button>
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
      <Table rowKey="review_id" columns={columns} dataSource={reviews} loading={loading} />
    </Card>
  );
};

export default HistoryPage;
