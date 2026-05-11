import React from "react";
import { Alert, Button, Card, Divider, Input, Select, Space, Tag, Typography, Upload } from "antd";
import { UploadOutlined } from "@ant-design/icons";

import type { ExpertProfile, ReviewDesignDocumentInput } from "@/services/api";
import { humanizeExpertId } from "@/utils/displayText";

const { Text } = Typography;
const DEFAULT_REQUIRED_EXPERT_ID = "change_impact_analysis";
const RECOMMENDED_EXPERT_IDS = [
  "correctness_business",
  "architecture_design",
  "security_compliance",
  "performance_reliability",
  "maintainability_code_health",
  "test_verification",
];

export type ReviewFormState = {
  subject_type: "mr" | "branch";
  analysis_mode: "standard" | "light";
  mr_url: string;
  title: string;
  source_ref: string;
  target_ref: string;
  selected_experts: string[];
  expert_selection_mode: "auto" | "manual";
  design_docs: ReviewDesignDocumentInput[];
};

export type ReviewOverviewExpertSelectionSummary = {
  requested_expert_ids: string[];
  selected_experts: Array<{
    expert_id: string;
    expert_name?: string;
    reason?: string;
  }>;
  skipped_experts: Array<{
    expert_id: string;
    expert_name?: string;
    reason?: string;
  }>;
};

type Props = {
  form: ReviewFormState;
  loading: boolean;
  running: boolean;
  reviewId: string;
  status: string;
  readonly: boolean;
  experts: ExpertProfile[];
  expertSelectionSummary?: ReviewOverviewExpertSelectionSummary | null;
  onChange: (patch: Partial<ReviewFormState>) => void;
  onStart: () => void;
};

const buildExpertSummary = (expert?: ExpertProfile): string => {
  if (!expert) return "当前未找到该专家的职责摘要。";
  const focus = expert.focus_areas.slice(0, 2).join(" / ");
  const requiredCheck = expert.required_checks[0];
  return [expert.role, focus, requiredCheck].filter(Boolean).join(" | ") || "当前未配置职责摘要。";
};

const normalizeSelectedExperts = (selectedExperts: string[], subjectType: "mr" | "branch"): string[] => {
  const deduped = Array.from(new Set((selectedExperts || []).filter(Boolean)));
  if (subjectType === "mr" && !deduped.includes(DEFAULT_REQUIRED_EXPERT_ID)) {
    return [DEFAULT_REQUIRED_EXPERT_ID, ...deduped];
  }
  return deduped;
};

const ReviewOverviewPanel: React.FC<Props> = ({
  form,
  loading,
  running,
  reviewId,
  status,
  readonly,
  experts,
  expertSelectionSummary,
  onChange,
  onStart,
}) => {
  // 概览页只负责“审核输入 + 启动前状态提示”，
  // 不承担过程流和结果渲染逻辑。
  const statusLabel = reviewId ? status || "pending" : "未开始";
  const hasExperts = experts.length > 0;
  const hasReviewInput = Boolean(form.mr_url.trim() || form.source_ref.trim());
  const disableActions = loading || running || (!readonly && (!hasExperts || !hasReviewInput));
  const designDocNames = form.design_docs.map((item) => item.filename || item.title);
  const expertNameById = new Map(experts.map((item) => [item.expert_id, item.name_zh]));
  const expertById = new Map(experts.map((item) => [item.expert_id, item]));
  const requestedExpertIds =
    expertSelectionSummary?.requested_expert_ids && expertSelectionSummary.requested_expert_ids.length > 0
      ? expertSelectionSummary.requested_expert_ids
      : form.selected_experts;
  const selectedExperts = expertSelectionSummary?.selected_experts || [];
  const skippedExperts = expertSelectionSummary?.skipped_experts || [];
  const selectedExpertIds = selectedExperts.map((item) => item.expert_id);
  const selectedExpertIdSet = new Set(selectedExpertIds);
  const skippedExpertIdSet = new Set(skippedExperts.map((item) => item.expert_id));
  const recommendedExpertIds = RECOMMENDED_EXPERT_IDS.filter((expertId) => expertById.has(expertId));
  const selectedFromCandidates = requestedExpertIds.filter((expertId) => selectedExpertIdSet.has(expertId));
  const removedFromCandidates = requestedExpertIds.filter((expertId) => skippedExpertIdSet.has(expertId));
  const addedByModel = selectedExperts.filter((item) => !requestedExpertIds.includes(item.expert_id));
  const isMrReview = form.subject_type === "mr";

  return (
    <Card className="module-card" title={readonly ? "检视概览" : "提交检视"}>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message={
            readonly
              ? "当前是审核记录查看模式。这里展示当时提交的审核对象与候选角色，实际参与集合由系统在启动后判定，过程细节请切到“检视过程”，最终结论请切到“检视结果”。"
              : "先输入 Codehub MR 链接，再选择候选检查角色（可选）并启动审核。关联影响分析会默认参与每一次 MR 检视；如果你没有额外指定其他候选角色，启动后系统仍会判定本次参与审核的检查角色。"
          }
        />
        {!hasExperts ? (
          <Alert
            type="error"
            showIcon
            message="当前没有可用的检查角色"
            description="请检查后端是否已加载预置检查角色，或先在角色中心创建/启用检查角色后再启动审核。"
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
              onChange={(value) =>
                onChange({
                  subject_type: value,
                  selected_experts:
                    value === "mr"
                      ? normalizeSelectedExperts(form.selected_experts, "mr")
                      : form.selected_experts.filter((expertId) => expertId !== DEFAULT_REQUIRED_EXPERT_ID),
                  expert_selection_mode:
                    value === "mr" && form.expert_selection_mode === "auto" ? "auto" : form.expert_selection_mode,
                })
              }
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
            {!readonly ? (
              <div className="review-expert-quick-actions">
                <Text strong>候选角色快捷选择</Text>
                <Space wrap>
                  <Button
                    size="small"
                    onClick={() =>
                      onChange({
                        selected_experts: normalizeSelectedExperts(recommendedExpertIds, form.subject_type),
                        expert_selection_mode: "manual",
                      })
                    }
                  >
                    推荐候选
                  </Button>
                  <Button
                    size="small"
                    onClick={() =>
                      onChange({
                        selected_experts: normalizeSelectedExperts(
                          experts.map((expert) => expert.expert_id),
                          form.subject_type,
                        ),
                        expert_selection_mode: "manual",
                      })
                    }
                  >
                    全部候选
                  </Button>
                  <Button
                    size="small"
                    onClick={() =>
                      onChange({
                        selected_experts: normalizeSelectedExperts([], form.subject_type),
                        expert_selection_mode: "auto",
                      })
                    }
                  >
                    清空
                  </Button>
                </Space>
              </div>
            ) : null}
            <Select
              mode="multiple"
              allowClear={!readonly}
              disabled={readonly}
              style={{ width: "100%" }}
              placeholder="选择候选检查角色（启动后由系统最终判定本次参与集合）"
              value={form.selected_experts}
              onChange={(value) =>
                onChange({
                  selected_experts: normalizeSelectedExperts(value, form.subject_type),
                  expert_selection_mode:
                    value.some((item) => item !== DEFAULT_REQUIRED_EXPERT_ID) || form.subject_type === "branch"
                      ? "manual"
                      : "auto",
                })
              }
              options={experts.map((expert) => ({
                label:
                  `${expert.name_zh}${expert.custom ? "（自定义）" : ""}` +
                  (expert.expert_id === DEFAULT_REQUIRED_EXPERT_ID && isMrReview ? "（系统默认）" : ""),
                value: expert.expert_id,
              }))}
            />
            <Space direction="vertical" size={8} style={{ width: "100%", marginTop: 12 }}>
              <div className="review-expert-decision-layout">
                <div className="review-expert-decision-column">
                  {!readonly && isMrReview ? (
                    <Alert
                      type="info"
                      showIcon
                      message="关联影响分析会默认参与每一次 MR 检视"
                      description="系统会默认勾选该角色，用于生成影响范围、调用链和测试建议报告。你仍然可以继续补充其他候选角色。"
                    />
                  ) : null}
                  <div className="review-design-docs-readonly review-expert-panel">
                    <div className="review-expert-panel-head">
                      <div>
                        <Text strong>候选检查角色</Text>
                        <div className="review-expert-panel-subtitle">这里展示系统默认带入和你手动补充的候选集合。</div>
                      </div>
                      {isMrReview ? <Tag color="gold">MR 默认带入关联影响分析</Tag> : null}
                    </div>
                    <Space wrap style={{ width: "100%", marginTop: 8 }}>
                      {requestedExpertIds.length > 0 ? (
                        requestedExpertIds.map((expertId) => (
                          <Tag key={`candidate-${expertId}`} color="blue">
                            {expertNameById.get(expertId) || expertId}
                            {expertId === DEFAULT_REQUIRED_EXPERT_ID && isMrReview ? " · 系统默认" : ""}
                          </Tag>
                        ))
                      ) : (
                        <Text type="secondary">当前还没有候选角色</Text>
                      )}
                    </Space>
                  </div>
                  {requestedExpertIds.length > 0 ? (
                    <div className="review-design-docs-readonly review-expert-panel">
                      <Text strong>候选角色职责速览</Text>
                      <div className="review-expert-summary-grid">
                        {requestedExpertIds.map((expertId) => {
                          const expert = expertById.get(expertId);
                          const isSelected = selectedExpertIdSet.has(expertId);
                          const isRemoved = skippedExpertIdSet.has(expertId);
                          const stateClassName = isSelected
                            ? "review-expert-summary-card-selected"
                            : isRemoved
                              ? "review-expert-summary-card-removed"
                              : "review-expert-summary-card-candidate";
                          return (
                            <div key={`summary-${expertId}`} className={`review-expert-summary-card ${stateClassName}`}>
                              <div className="review-expert-summary-card-head">
                                <Text strong>{expert?.name_zh || expertId}</Text>
                                <Space size={6} wrap>
                                  <Tag color="blue">候选</Tag>
                                  {expertId === DEFAULT_REQUIRED_EXPERT_ID && isMrReview ? <Tag color="gold">系统默认</Tag> : null}
                                  {isSelected ? <Tag color="green">已参与</Tag> : null}
                                  {isRemoved ? <Tag color="orange">未纳入</Tag> : null}
                                </Space>
                              </div>
                              <Text type="secondary" className="review-expert-summary-text">
                                {buildExpertSummary(expert)}
                              </Text>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                </div>
                <div className="review-expert-decision-column">
                  {reviewId ? (
                    <div className="review-design-docs-readonly review-expert-panel review-expert-panel-final">
                      <div className="review-expert-panel-head">
                        <div>
                          <Text strong>最终参与角色</Text>
                          <div className="review-expert-panel-subtitle">系统会基于完整 diff、风险信号和角色职责，补齐真正需要参与本轮审核的检查角色。</div>
                        </div>
                        {selectedExperts.length > 0 ? <Tag color="green">已判定 {selectedExperts.length} 位</Tag> : null}
                      </div>
                      {selectedExperts.length > 0 ? (
                        <>
                          <div className="review-expert-result-metrics">
                            <div className="review-expert-result-metric">
                              <Text type="secondary">原始候选</Text>
                              <strong>{requestedExpertIds.length}</strong>
                            </div>
                            <div className="review-expert-result-metric">
                              <Text type="secondary">系统补充</Text>
                              <strong>{addedByModel.length}</strong>
                            </div>
                            <div className="review-expert-result-metric">
                              <Text type="secondary">最终参与</Text>
                              <strong>{selectedExperts.length}</strong>
                            </div>
                          </div>
                          <Space wrap style={{ width: "100%", marginTop: 8 }}>
                            {selectedExperts.map((item) => (
                              <Tag key={`selected-${item.expert_id}`} color="green">
                                {item.expert_name || expertNameById.get(item.expert_id) || humanizeExpertId(item.expert_id)}
                              </Tag>
                            ))}
                          </Space>
                          <Space direction="vertical" size={6} style={{ width: "100%", marginTop: 8 }}>
                            {selectedExperts.map((item) => (
                              <Text key={`selected-reason-${item.expert_id}`} type="secondary">
                                {(item.expert_name || expertNameById.get(item.expert_id) || humanizeExpertId(item.expert_id)) + "："}
                                {item.reason || "与当前 MR 变更高度相关"}
                              </Text>
                            ))}
                          </Space>
                        </>
                      ) : (
                        <Text type="secondary">审核启动后，系统会先判定本次真正参与审核的检查角色。</Text>
                      )}
                    </div>
                  ) : null}
                  {reviewId ? (
                    <div className="review-design-docs-readonly review-expert-panel">
                      <Text strong>候选与最终参与差异</Text>
                      <div className="review-expert-diff-grid">
                        <div className="review-expert-diff-block">
                          <Text strong>候选后被选中</Text>
                          <Space wrap style={{ width: "100%", marginTop: 8 }}>
                            {selectedFromCandidates.length > 0 ? (
                              selectedFromCandidates.map((expertId) => (
                                <Tag key={`candidate-hit-${expertId}`} color="green">
                                  {expertNameById.get(expertId) || humanizeExpertId(expertId)}
                                </Tag>
                              ))
                            ) : (
                              <Text type="secondary">当前还没有命中的候选角色</Text>
                            )}
                          </Space>
                        </div>
                        <div className="review-expert-diff-block">
                          <Text strong>候选后被剔除</Text>
                          <Space wrap style={{ width: "100%", marginTop: 8 }}>
                            {removedFromCandidates.length > 0 ? (
                              removedFromCandidates.map((expertId) => (
                                <Tag key={`candidate-drop-${expertId}`} color="orange">
                                  {expertNameById.get(expertId) || humanizeExpertId(expertId)}
                                </Tag>
                              ))
                            ) : (
                              <Text type="secondary">当前没有被剔除的候选角色</Text>
                            )}
                          </Space>
                        </div>
                        {addedByModel.length > 0 ? (
                          <div className="review-expert-diff-block review-expert-diff-block-accent">
                            <Text strong>系统补充纳入</Text>
                            <Space wrap style={{ width: "100%", marginTop: 8 }}>
                              {addedByModel.map((item) => (
                                <Tag key={`candidate-added-${item.expert_id}`} color="cyan">
                                  {item.expert_name || expertNameById.get(item.expert_id) || humanizeExpertId(item.expert_id)}
                                </Tag>
                              ))}
                            </Space>
                          </div>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                  {skippedExperts.length > 0 ? (
                    <div className="review-design-docs-readonly review-expert-panel">
                      <Text strong>未参与本轮</Text>
                      <Space direction="vertical" size={6} style={{ width: "100%", marginTop: 8 }}>
                        {skippedExperts.map((item) => (
                          <Text key={`skipped-${item.expert_id}`} type="secondary">
                            {(item.expert_name || expertNameById.get(item.expert_id) || humanizeExpertId(item.expert_id)) + "："}
                            {item.reason || "系统未将其纳入本次参与集合"}
                          </Text>
                        ))}
                      </Space>
                    </div>
                  ) : null}
                </div>
              </div>
            </Space>
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
          系统会先根据 PR / MR / Commit 链接、改动文件和风险提示拆解任务，再向不同检查角色下发带文件/行号的审查指令。
          {form.analysis_mode === "light"
            ? " 当前为轻量模式：会提高模型超时、降低并发和复核轮次，更适合内网或 Windows 高延迟环境。"
            : " 当前为标准模式：保留更完整的角色协作和深度分析。"}
        </Text>

        {readonly ? (
          status === "pending" ? (
            <Space>
              <Button type="primary" loading={loading || running} disabled={!hasExperts} onClick={onStart}>
                开始检视
              </Button>
            </Space>
          ) : null
        ) : (
          <Space>
            <Button type="primary" loading={loading || running} disabled={disableActions} onClick={onStart}>
              开始检视
            </Button>
          </Space>
        )}
      </Space>
    </Card>
  );
};

export default ReviewOverviewPanel;
