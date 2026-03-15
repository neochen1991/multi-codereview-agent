import React from "react";
import { Alert, Button, Card, Divider, Input, Select, Space, Tag, Typography, Upload } from "antd";
import { UploadOutlined } from "@ant-design/icons";

import type { ExpertProfile, ReviewDesignDocumentInput } from "@/services/api";

const { Text } = Typography;

export type ReviewFormState = {
  subject_type: "mr" | "branch";
  analysis_mode: "standard" | "light";
  mr_url: string;
  title: string;
  source_ref: string;
  target_ref: string;
  selected_experts: string[];
  design_docs: ReviewDesignDocumentInput[];
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
  // 概览页只负责“审核输入 + 启动前状态提示”，
  // 不承担过程流和结果渲染逻辑。
  const statusLabel = reviewId ? status || "pending" : "未开始";
  const hasExperts = experts.length > 0;
  const hasSelectedExperts = form.selected_experts.length > 0;
  const hasReviewInput = Boolean(form.mr_url.trim() || form.source_ref.trim());
  const disableActions = loading || running || (!readonly && (!hasExperts || !hasSelectedExperts || !hasReviewInput));
  const designDocNames = form.design_docs.map((item) => item.filename || item.title);

  return (
    <Card className="module-card" title={readonly ? "概览" : "概览与启动"}>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message={
            readonly
              ? "当前是审核记录查看模式。这里展示当时提交的审核对象与专家选择，过程细节请切到“审核过程”，最终结论请切到“结论与行动”。"
              : "先输入 Codehub MR 链接，选择参与专家，再启动审核。启动后去“审核过程”查看主 Agent 调度、专家发言和裁决收敛。"
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
            <Select
              value={form.analysis_mode}
              style={{ width: "100%" }}
              disabled={readonly}
              onChange={(value) => onChange({ analysis_mode: value })}
              options={[
                { label: "标准模式", value: "standard" },
                { label: "轻量模式", value: "light" },
              ]}
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
          <div style={{ gridColumn: "1 / -1" }}>
            {readonly ? (
              <div className="review-design-docs-readonly">
                <Text strong>本次绑定的详细设计文档</Text>
                <Space wrap style={{ width: "100%", marginTop: 8 }}>
                  {designDocNames.length > 0 ? (
                    designDocNames.map((name) => <Tag key={name} color="purple">{name}</Tag>)
                  ) : (
                    <Text type="secondary">本次审核未绑定详细设计文档</Text>
                  )}
                </Space>
              </div>
            ) : (
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                <Text strong>上传本次审核对应的详细设计文档（md）</Text>
                <Upload
                  multiple
                  accept=".md,text/markdown"
                  beforeUpload={(file) => {
                    void file
                      .text()
                      .then((content) => {
                        onChange({
                          design_docs: [
                            ...form.design_docs.filter((item) => item.filename !== file.name),
                            {
                              doc_id: `design_${file.uid.replace(/[^a-zA-Z0-9_-]/g, "")}`,
                              title: file.name.replace(/\.md$/i, ""),
                              filename: file.name,
                              content,
                              doc_type: "design_spec",
                            },
                          ],
                        });
                      });
                    return false;
                  }}
                  fileList={form.design_docs.map((item, index) => ({
                    uid: item.doc_id || `${item.filename}-${index}`,
                    name: item.filename,
                    status: "done" as const,
                  }))}
                  onRemove={(file) => {
                    onChange({
                      design_docs: form.design_docs.filter((item) => item.filename !== file.name),
                    });
                    return true;
                  }}
                >
                  <Button icon={<UploadOutlined />}>选择详细设计文档</Button>
                </Upload>
                <Text type="secondary">
                  当前上传的详细设计文档只绑定到本次审核，不会自动进入长期知识库。
                </Text>
              </Space>
            )}
          </div>
        </div>

        <Divider style={{ margin: 0 }} />
        <Text type="secondary">
          主 Agent 会先根据 PR / MR / Commit 链接、改动文件和风险提示拆解任务，再向不同专家下发带文件/行号的审查指令。
          {form.analysis_mode === "light"
            ? " 当前为轻量模式：会提高 LLM 超时、降低并发和辩论轮次，更适合内网或 Windows 高延迟环境。"
            : " 当前为标准模式：保留更完整的专家协作和深度分析。"}
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
