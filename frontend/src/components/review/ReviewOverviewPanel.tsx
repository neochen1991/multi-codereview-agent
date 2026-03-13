import React from "react";
import { Alert, Button, Card, Divider, Input, Select, Space, Tag, Typography } from "antd";

import type { ExpertProfile } from "@/services/api";

const { Text } = Typography;

export type ReviewFormState = {
  subject_type: "mr" | "branch";
  mr_url: string;
  title: string;
  repo_id: string;
  project_id: string;
  source_ref: string;
  target_ref: string;
  access_token: string;
  selected_experts: string[];
};

type Props = {
  form: ReviewFormState;
  loading: boolean;
  running: boolean;
  reviewId: string;
  status: string;
  readonly: boolean;
  experts: ExpertProfile[];
  onChange: (patch: Partial<ReviewFormState>) => void;
  onStart: () => void;
  onCreateOnly: () => void;
};

const ReviewOverviewPanel: React.FC<Props> = ({
  form,
  loading,
  running,
  reviewId,
  status,
  readonly,
  experts,
  onChange,
  onStart,
  onCreateOnly,
}) => {
  const statusLabel = reviewId ? status || "pending" : "未开始";
  const hasExperts = experts.length > 0;
  const hasSelectedExperts = form.selected_experts.length > 0;
  const hasReviewInput = Boolean(form.mr_url.trim() || form.source_ref.trim());
  const disableActions = loading || running || (!readonly && (!hasExperts || !hasSelectedExperts || !hasReviewInput));

  return (
    <Card className="module-card" title={readonly ? "概览" : "概览与启动"}>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message={
            readonly
              ? "当前是审核记录查看模式。这里展示当时提交的审核对象与专家选择，过程细节请切到“审核过程”，最终结论请切到“结论与行动”。"
              : "先输入 GitHub PR / GitLab MR / GitHub Commit 链接，选择参与专家，再启动审核。启动后去“审核过程”查看主 Agent 调度、专家发言和裁决收敛。"
          }
        />
        {!hasExperts ? (
          <Alert
            type="error"
            showIcon
            message="当前没有可用的专家 agent"
            description="请检查后端是否已加载预置专家，或先在专家中心创建/启用专家后再启动审核。"
          />
        ) : null}
        <div className="incident-overview-status-strip">
          <Tag color={reviewId ? "blue" : "default"}>
            {reviewId ? `Review: ${reviewId}` : "未创建审核"}
          </Tag>
          <Tag
            color={
              status === "completed"
                ? "success"
                : status === "waiting_human" || status === "failed"
                  ? "error"
                  : status === "running"
                    ? "processing"
                    : "default"
            }
          >
            {statusLabel}
          </Tag>
          {running ? <Tag color="processing">审核运行中</Tag> : null}
        </div>

        <div className="incident-overview-form-grid">
          <div>
            <Input
              placeholder="Git PR / MR / Commit 链接 *"
              value={form.mr_url}
              disabled={readonly}
              onChange={(event) => onChange({ mr_url: event.target.value, subject_type: "mr" })}
            />
          </div>
          <div>
            <Input
              placeholder="审核标题（可选，留空则自动推断）"
              value={form.title}
              disabled={readonly}
              onChange={(event) => onChange({ title: event.target.value })}
            />
          </div>
          <div>
            <Select
              value={form.subject_type}
              style={{ width: "100%" }}
              disabled={readonly}
              onChange={(value) => onChange({ subject_type: value })}
              options={[
                { label: "Merge Request", value: "mr" },
                { label: "Branch Compare", value: "branch" },
              ]}
            />
          </div>
          <div>
            <Input
              placeholder="Access Token（可选）"
              value={form.access_token}
              disabled={readonly}
              onChange={(event) => onChange({ access_token: event.target.value })}
            />
          </div>
          <div>
            <Input
              placeholder="Repo ID（MR 链接可自动推断）"
              value={form.repo_id}
              disabled={readonly}
              onChange={(event) => onChange({ repo_id: event.target.value })}
            />
          </div>
          <div>
            <Input
              placeholder="Project ID（MR 链接可自动推断）"
              value={form.project_id}
              disabled={readonly}
              onChange={(event) => onChange({ project_id: event.target.value })}
            />
          </div>
          <div>
            <Input
              placeholder="源分支 / MR Ref"
              value={form.source_ref}
              disabled={readonly}
              onChange={(event) => onChange({ source_ref: event.target.value })}
            />
          </div>
          <div>
            <Input
              placeholder="目标分支"
              value={form.target_ref}
              disabled={readonly}
              onChange={(event) => onChange({ target_ref: event.target.value })}
            />
          </div>
          <div style={{ gridColumn: "1 / -1" }}>
            <Select
              mode="multiple"
              allowClear={!readonly}
              disabled={readonly}
              style={{ width: "100%" }}
              placeholder="选择参与审核的专家"
              value={form.selected_experts}
              onChange={(value) => onChange({ selected_experts: value })}
              options={experts.map((expert) => ({
                label: `${expert.name_zh}${expert.custom ? "（自定义）" : ""}`,
                value: expert.expert_id,
              }))}
            />
          </div>
        </div>

        <Divider style={{ margin: 0 }} />
        <Text type="secondary">
          主 Agent 会先根据 PR / MR / Commit 链接、改动文件和风险提示拆解任务，再向不同专家下发带文件/行号的审查指令。
        </Text>

        {readonly ? (
          status === "pending" ? (
            <Space>
              <Button type="primary" loading={loading || running} disabled={!hasExperts} onClick={onStart}>
                启动审核
              </Button>
            </Space>
          ) : null
        ) : (
          <Space>
            <Button type="primary" loading={loading || running} disabled={disableActions} onClick={onStart}>
              创建并启动审核
            </Button>
            <Button loading={loading} disabled={disableActions} onClick={onCreateOnly}>
              仅创建审核
            </Button>
          </Space>
        )}
      </Space>
    </Card>
  );
};

export default ReviewOverviewPanel;
