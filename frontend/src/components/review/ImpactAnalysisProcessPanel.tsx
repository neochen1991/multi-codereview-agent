import React, { useMemo } from "react";
import { Alert, Card, Descriptions, Empty, Space, Steps, Tag, Typography } from "antd";

import type { ConversationMessage, ImpactReport, ReviewSummary } from "@/services/api";

const { Paragraph, Text } = Typography;

type Props = {
  review: ReviewSummary | null;
  messages: ConversationMessage[];
};

type ImpactProgress = {
  state: string;
  expert_id?: string;
  expert_name?: string;
  started_at?: string;
  completed_at?: string;
  graph_status?: string;
  risk_level?: string;
  changed_file_count?: number;
  impacted_file_count?: number;
  recommended_test_scope_count?: number;
  failed_at?: string;
  error_message?: string;
};

const riskColor = (value?: string): string => {
  if (value === "high" || value === "critical") return "error";
  if (value === "medium") return "warning";
  if (value === "low") return "success";
  return "default";
};

const graphStatusLabel = (value?: string): string => {
  if (value === "ready") return "GitNexus 图谱已命中";
  if (value === "running") return "GitNexus 建图中";
  if (value === "missing") return "GitNexus 图谱未命中";
  if (value === "failed") return "GitNexus 图谱不可用";
  return value || "状态未知";
};

const readImpactProgress = (review: ReviewSummary | null): ImpactProgress | null => {
  const raw = review?.subject?.metadata?.impact_analysis_progress;
  if (!raw || typeof raw !== "object") return null;
  const payload = raw as Record<string, unknown>;
  return {
    state: String(payload.state || ""),
    expert_id: payload.expert_id ? String(payload.expert_id) : undefined,
    expert_name: payload.expert_name ? String(payload.expert_name) : undefined,
    started_at: payload.started_at ? String(payload.started_at) : undefined,
    completed_at: payload.completed_at ? String(payload.completed_at) : undefined,
    graph_status: payload.graph_status ? String(payload.graph_status) : undefined,
    risk_level: payload.risk_level ? String(payload.risk_level) : undefined,
    changed_file_count: typeof payload.changed_file_count === "number" ? payload.changed_file_count : undefined,
    impacted_file_count: typeof payload.impacted_file_count === "number" ? payload.impacted_file_count : undefined,
    recommended_test_scope_count:
      typeof payload.recommended_test_scope_count === "number" ? payload.recommended_test_scope_count : undefined,
    failed_at: payload.failed_at ? String(payload.failed_at) : undefined,
    error_message: payload.error_message ? String(payload.error_message) : undefined,
  };
};

const readImpactReport = (review: ReviewSummary | null): ImpactReport | null => {
  const raw = review?.subject?.metadata?.impact_report;
  if (!raw || typeof raw !== "object") return null;
  return raw as ImpactReport;
};

const formatTime = (value?: string): string => {
  if (!value) return "暂无";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
};

const ImpactAnalysisProcessPanel: React.FC<Props> = ({ review, messages }) => {
  const progress = useMemo(() => readImpactProgress(review), [review]);
  const report = useMemo(() => readImpactReport(review), [review]);
  const processMessages = useMemo(
    () =>
      messages.filter((item) =>
        ["impact_analysis_started", "impact_report_generated", "impact_report_failed"].includes(item.message_type),
      ),
    [messages],
  );
  const currentStep = progress?.state === "completed" ? 2 : progress?.state === "failed" ? 1 : progress?.state === "started" ? 1 : 0;

  return (
    <Card className="module-card" title="关联影响分析">
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <Paragraph style={{ marginBottom: 0 }}>
          这一步不是代码检视，也不会进入正式问题收敛。系统会单独调用 GitNexus，分析本次改动的影响范围和建议测试范围。
        </Paragraph>
        <Steps
          size="small"
          current={currentStep}
          items={[
            { title: "等待开始", description: "准备读取 MR 变更" },
            { title: "调用 GitNexus", description: "分析影响范围与调用链" },
            { title: "生成报告", description: "写入 impact_report" },
          ]}
        />
        {progress ? (
          <>
            <Space wrap>
              <Tag color="blue">{progress.expert_name || progress.expert_id || "change_impact_analysis"}</Tag>
              {progress.graph_status ? <Tag color={progress.graph_status === "ready" ? "success" : progress.graph_status === "failed" ? "error" : "warning"}>{graphStatusLabel(progress.graph_status)}</Tag> : null}
              {progress.risk_level ? <Tag color={riskColor(progress.risk_level)}>{`风险 ${progress.risk_level}`}</Tag> : null}
              {typeof progress.changed_file_count === "number" ? <Tag>{`变更文件 ${progress.changed_file_count}`}</Tag> : null}
              {typeof progress.impacted_file_count === "number" ? <Tag>{`影响文件 ${progress.impacted_file_count}`}</Tag> : null}
              {typeof progress.recommended_test_scope_count === "number" ? <Tag>{`测试建议 ${progress.recommended_test_scope_count}`}</Tag> : null}
            </Space>
            {progress.state === "failed" ? (
              <Alert
                type="error"
                showIcon
                message="关联影响报告生成失败"
                description={progress.error_message || "GitNexus 调用失败，当前没有可用的关联影响报告。"}
              />
            ) : null}
            <Descriptions
              size="small"
              column={2}
              items={[
                { key: "state", label: "当前状态", children: progress.state || "unknown" },
                { key: "started", label: "开始时间", children: formatTime(progress.started_at) },
                { key: "completed", label: progress.state === "failed" ? "失败时间" : "完成时间", children: formatTime(progress.state === "failed" ? progress.failed_at : progress.completed_at) },
                { key: "graph", label: "图谱状态", children: graphStatusLabel(progress.graph_status) },
              ]}
            />
          </>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前还没有关联影响分析进度。" />
        )}
        {processMessages.length ? (
          <div className="impact-analysis-message-stack">
            {processMessages.map((item) => (
              <div key={item.message_id} className="impact-analysis-message-item">
                <div className="impact-analysis-message-head">
                  <Text strong>
                    {item.message_type === "impact_analysis_started"
                      ? "开始分析"
                      : item.message_type === "impact_report_failed"
                        ? "报告生成失败"
                        : "报告已生成"}
                  </Text>
                  <Text type="secondary">{formatTime(item.created_at)}</Text>
                </div>
                <Paragraph style={{ marginBottom: 0 }}>{item.content}</Paragraph>
              </div>
            ))}
          </div>
        ) : null}
        {report ? (
          <Descriptions
            size="small"
            column={1}
            items={[
              {
                key: "modules",
                label: "影响模块",
                children: report.impacted_modules.length ? report.impacted_modules.join("、") : "暂无",
              },
              {
                key: "tests",
                label: "建议测试范围",
                children: report.recommended_test_scope.length
                  ? report.recommended_test_scope.map((item) => item.scope).join("、")
                  : "暂无",
              },
              {
                key: "commands",
                label: "建议执行项",
                children: report.must_run_tests.length ? report.must_run_tests.join("、") : "暂无",
              },
            ]}
          />
        ) : progress?.state === "failed" ? null : null}
      </Space>
    </Card>
  );
};

export default ImpactAnalysisProcessPanel;
