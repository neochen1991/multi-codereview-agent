import React, { useEffect, useState } from "react";
import { Alert, Button, Card, Popconfirm, Space, Table, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { TableRowSelection } from "antd/es/table/interface";
import { useNavigate } from "react-router-dom";

import { reviewApi, type ReviewSummary } from "@/services/api";
import { getReviewPhaseLabel, getReviewStatusColor, getReviewStatusLabel } from "@/utils/reviewStatus";

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

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

const asNumber = (value: unknown, fallback = 0): number =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

const readQualitySummary = (record: ReviewSummary) => {
  const metadata = asRecord(record.subject.metadata);
  const nested = asRecord(metadata.quality_summary);
  return {
    evidenceChainIssueCount: asNumber(record.quality_summary?.evidence_chain_issue_count, asNumber(nested.evidence_chain_issue_count)),
    qualityFilteredIssueCount: asNumber(
      record.quality_summary?.quality_filtered_issue_count,
      asNumber(nested.quality_filtered_issue_count),
    ),
    budgetFilteredIssueCount: asNumber(
      record.quality_summary?.policy_comment_budget_filtered_count,
      asNumber(nested.policy_comment_budget_filtered_count),
    ),
  };
};

const readImpactSummary = (record: ReviewSummary) => {
  const metadata = asRecord(record.subject.metadata);
  const nested = asRecord(metadata.impact_summary);
  const graphStatus = String(record.impact_summary?.graph_status || nested.graph_status || "").trim();
  const riskLevel = String(record.impact_summary?.risk_level || nested.risk_level || "").trim();
  return {
    graphStatus,
    riskLevel,
    impactedFileCount: asNumber(record.impact_summary?.impacted_file_count, asNumber(nested.impacted_file_count)),
    recommendedTestScopeCount: asNumber(
      record.impact_summary?.recommended_test_scope_count,
      asNumber(nested.recommended_test_scope_count),
    ),
    successfulContextTargetCount: asNumber(
      record.impact_summary?.successful_context_target_count,
      asNumber(nested.successful_context_target_count),
    ),
    successfulImpactTargetCount: asNumber(
      record.impact_summary?.successful_impact_target_count,
      asNumber(nested.successful_impact_target_count),
    ),
  };
};

const impactRiskColor = (riskLevel: string) => {
  if (riskLevel === "high" || riskLevel === "critical") return "red";
  if (riskLevel === "medium") return "orange";
  if (riskLevel === "low") return "green";
  return "default";
};

const impactGraphColor = (graphStatus: string) => {
  if (graphStatus === "ready") return "success";
  if (graphStatus === "fallback") return "warning";
  if (graphStatus === "failed" || graphStatus === "missing") return "error";
  return "default";
};

// 历史记录页用于回看审核结果，并从“查看工作台”跳回详情。
const HistoryPage: React.FC = () => {
  const navigate = useNavigate();
  const [reviews, setReviews] = useState<ReviewSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [closingReviewId, setClosingReviewId] = useState("");
  const [rerunningReviewId, setRerunningReviewId] = useState("");
  const [deletingReviewId, setDeletingReviewId] = useState("");
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [loadError, setLoadError] = useState("");

  const openReviewTab = (reviewId: string, tab: "overview" | "process" | "result" | "impact") => {
    navigate(`/review/${reviewId}?tab=${tab}`);
  };

  const loadReviews = async () => {
    setLoading(true);
    try {
      const rows = await reviewApi.list();
      setLoadError("");
      setReviews(
        rows
          .slice()
          .sort(
            (left, right) =>
              new Date(right.updated_at || right.created_at || 0).getTime() -
              new Date(left.updated_at || left.created_at || 0).getTime(),
          ),
      );
    } catch (error: any) {
      const detail = error?.response?.data?.detail || error?.message || "加载历史记录失败";
      setLoadError(String(detail));
      message.error(String(detail));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadReviews();
  }, []);

  const terminalStatuses = new Set(["completed", "failed", "closed"]);
  const selectedDeletableIds = reviews
    .filter((record) => selectedRowKeys.includes(record.review_id) && terminalStatuses.has(record.status))
    .map((record) => record.review_id);

  const rowSelection: TableRowSelection<ReviewSummary> = {
    selectedRowKeys,
    onChange: (keys) => setSelectedRowKeys(keys),
    getCheckboxProps: (record) => ({
      disabled: !terminalStatuses.has(record.status),
    }),
  };

  const columns: ColumnsType<ReviewSummary> = [
    { title: "Review ID", dataIndex: "review_id", key: "review_id", width: 160 },
    {
      title: "标题",
      key: "title",
      width: 340,
      render: (_, record) => {
        const quality = readQualitySummary(record);
        const impact = readImpactSummary(record);
        const issueCount = Number(record.issue_count || 0);
        const evidenceCoverage = issueCount > 0 ? Math.round((quality.evidenceChainIssueCount / issueCount) * 100) : 0;
        const hasImpact = impact.graphStatus || impact.impactedFileCount > 0 || impact.recommendedTestScopeCount > 0;
        return (
          <Space direction="vertical" size={6} className="review-table-title-stack">
            <div className="review-table-title-cell" title={buildReviewLabel(record)}>
              {buildReviewLabel(record)}
            </div>
            <Space size={4} wrap>
              <Tag color={evidenceCoverage >= 80 ? "green" : issueCount > 0 ? "gold" : "default"}>{`证据链 ${evidenceCoverage}%`}</Tag>
              {quality.qualityFilteredIssueCount ? <Tag color="blue">{`过滤 ${quality.qualityFilteredIssueCount}`}</Tag> : null}
              {hasImpact ? (
                <>
                  <Tag color={impactGraphColor(impact.graphStatus)}>{impact.graphStatus || "impact"}</Tag>
                  {impact.riskLevel ? <Tag color={impactRiskColor(impact.riskLevel)}>{impact.riskLevel}</Tag> : null}
                  <Tag>{`影响 ${impact.impactedFileCount}`}</Tag>
                </>
              ) : null}
            </Space>
          </Space>
        );
      },
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
      render: (value: string) => <Tag color={value === "impact_analysis" ? "geekblue" : "processing"}>{getReviewPhaseLabel(value)}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (value: string, record) => (
        <Space size={6} wrap>
          <Tag color={getReviewStatusColor(value)}>{getReviewStatusLabel(value)}</Tag>
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
      title: "检视议题数",
      dataIndex: "issue_count",
      key: "issue_count",
      width: 120,
      render: (value?: number) => Number(value || 0),
    },
    {
      title: "检视质量",
      key: "quality_summary",
      width: 220,
      render: (_, record) => {
        const summary = readQualitySummary(record);
        const issueCount = Number(record.issue_count || 0);
        const coverage = issueCount > 0 ? Math.round((summary.evidenceChainIssueCount / issueCount) * 100) : 0;
        return (
          <Space size={4} wrap>
            <Tag color={coverage >= 80 ? "green" : issueCount > 0 ? "gold" : "default"}>{`证据链 ${coverage}%`}</Tag>
            <Tag color={summary.qualityFilteredIssueCount ? "blue" : "default"}>{`过滤 ${summary.qualityFilteredIssueCount}`}</Tag>
            <Tag color={summary.budgetFilteredIssueCount ? "gold" : "default"}>{`预算 ${summary.budgetFilteredIssueCount}`}</Tag>
          </Space>
        );
      },
    },
    {
      title: "影响分析",
      key: "impact_summary",
      width: 260,
      render: (_, record) => {
        const summary = readImpactSummary(record);
        const hasImpact =
          summary.graphStatus ||
          summary.impactedFileCount > 0 ||
          summary.recommendedTestScopeCount > 0 ||
          summary.successfulContextTargetCount > 0 ||
          summary.successfulImpactTargetCount > 0;
        if (!hasImpact) {
          return <Tag>暂无报告</Tag>;
        }
        return (
          <Space size={4} wrap>
            <Tag color={impactGraphColor(summary.graphStatus)}>{summary.graphStatus || "unknown"}</Tag>
            {summary.riskLevel ? <Tag color={impactRiskColor(summary.riskLevel)}>{summary.riskLevel}</Tag> : null}
            <Tag>{`影响文件 ${summary.impactedFileCount}`}</Tag>
            <Tag>{`测试 ${summary.recommendedTestScopeCount}`}</Tag>
            <Tag>{`命中 ${summary.successfulImpactTargetCount}`}</Tag>
          </Space>
        );
      },
    },
    {
      title: "人工裁决",
      dataIndex: "human_review_status",
      key: "human_review_status",
      width: 140,
      render: (value?: string) => (
        <Tag color={value === "requested" ? "error" : value === "approved" ? "success" : "default"}>
          {value === "requested" ? "待人工裁决" : value === "approved" ? "人工已批准" : value === "rejected" ? "人工已驳回" : "无需人工"}
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
          <Button type="link" size="small" onClick={() => openReviewTab(record.review_id, "impact")}>
            影响
          </Button>
          {["pending", "running", "waiting_human"].includes(record.status) ? (
            <Popconfirm
              title="确认强制结束这个未完成任务吗？"
              description="关闭后会停止后续审核流程，并把任务状态更新为 closed。"
              okText="确认结束"
              cancelText="取消"
              onConfirm={async () => {
                setClosingReviewId(record.review_id);
                try {
                  await reviewApi.close(record.review_id);
                  message.success("任务已强制结束");
                  await loadReviews();
                } catch (error: any) {
                  message.error(error?.message || "强制结束任务失败");
                } finally {
                  setClosingReviewId("");
                }
              }}
            >
              <Button type="link" size="small" danger loading={closingReviewId === record.review_id}>
                强制结束
              </Button>
            </Popconfirm>
          ) : null}
          {["failed", "closed"].includes(record.status) ? (
            <Popconfirm
              title="确认重跑这个任务吗？"
              description="系统会清理上一轮运行产生的过程数据，并重新发起审核。"
              okText="确认重跑"
              cancelText="取消"
              onConfirm={async () => {
                setRerunningReviewId(record.review_id);
                try {
                  const result = await reviewApi.rerun(record.review_id);
                  message.success(result.message || "任务已重新发起");
                  await loadReviews();
                } catch (error: any) {
                  message.error(error?.message || "重跑任务失败");
                } finally {
                  setRerunningReviewId("");
                }
              }}
            >
              <Button type="link" size="small" loading={rerunningReviewId === record.review_id}>
                重跑
              </Button>
            </Popconfirm>
          ) : null}
          {["completed", "failed", "closed"].includes(record.status) ? (
            <Popconfirm
              title="确认删除这条历史审核记录吗？"
              description="删除后会同时清理该审核的过程消息、发现、议题、产物和 SQLite 记录，操作不可恢复。"
              okText="确认删除"
              cancelText="取消"
              onConfirm={async () => {
                setDeletingReviewId(record.review_id);
                try {
                  await reviewApi.delete(record.review_id);
                  message.success("历史记录已删除");
                  await loadReviews();
                } catch (error: any) {
                  message.error(error?.message || "删除历史记录失败");
                } finally {
                  setDeletingReviewId("");
                }
              }}
            >
              <Button type="link" size="small" danger loading={deletingReviewId === record.review_id}>
                删除
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
        <Space>
          <Popconfirm
            title="确认批量删除选中的历史记录吗？"
            description="只会删除已结束记录，并清理关联消息、发现、议题和产物。SQLite 压缩会在后台统一执行一次。"
            okText="确认删除"
            cancelText="取消"
            disabled={selectedDeletableIds.length === 0}
            onConfirm={async () => {
              if (selectedDeletableIds.length === 0) {
                return;
              }
              setBatchDeleting(true);
              try {
                const result = await reviewApi.batchDelete(selectedDeletableIds);
                message.success(`已删除 ${result.deleted_count} 条历史记录`);
                setSelectedRowKeys([]);
                await loadReviews();
              } catch (error: any) {
                message.error(error?.message || "批量删除历史记录失败");
              } finally {
                setBatchDeleting(false);
              }
            }}
          >
            <Button danger disabled={selectedDeletableIds.length === 0} loading={batchDeleting}>
              批量删除
            </Button>
          </Popconfirm>
          <Button onClick={() => void loadReviews()} loading={loading}>
            刷新列表
          </Button>
        </Space>
      }
    >
      {loadError ? (
        <Alert
          className="review-history-load-alert"
          type="error"
          showIcon
          message="历史记录加载失败"
          description={loadError}
          action={
            <Button size="small" danger onClick={() => void loadReviews()}>
              重试
            </Button>
          }
        />
      ) : null}
      <Table
        className="review-list-table"
        rowKey="review_id"
        rowSelection={rowSelection}
        columns={columns}
        dataSource={reviews}
        loading={loading}
        scroll={{ x: 2120 }}
      />
    </Card>
  );
};

export default HistoryPage;
