import React from "react";
import { Button, Card, Empty, Input, Space, Tag, Typography } from "antd";

import type { DebateIssue, ReviewSummary } from "@/services/api";

const { Paragraph, Text } = Typography;

type HumanGatePanelProps = {
  review: ReviewSummary | null;
  selectedIssue: DebateIssue | null;
  isFallbackIssue?: boolean;
  decisionComment: string;
  submitting: boolean;
  onDecisionCommentChange: (value: string) => void;
  onApprove: () => void;
  onReject: () => void;
  className?: string;
};

// 人工裁决卡负责处理待人工 issue 的批准/驳回动作。
const HumanGatePanel: React.FC<HumanGatePanelProps> = ({
  review,
  selectedIssue,
  isFallbackIssue = false,
  decisionComment,
  submitting,
  onDecisionCommentChange,
  onApprove,
  onReject,
  className,
}) => {
  const humanStatus = review?.human_review_status || "not_required";
  const canSubmitDecision = Boolean(selectedIssue?.needs_human && selectedIssue?.status !== "resolved");

  return (
    <Card className={`module-card ${className || ""}`.trim()} title="人工裁决">
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <div className="human-gate-status-row">
          <Tag color={humanStatus === "requested" ? "error" : humanStatus === "approved" ? "success" : "default"}>
            {humanStatus}
          </Tag>
          <Text type="secondary">
            待人工议题 {review?.pending_human_issue_ids?.length || 0} 个
          </Text>
        </div>
        {!selectedIssue ? (
          <Empty description="先从问题清单中选择一条需要人工确认的议题。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <>
            {isFallbackIssue ? (
              <Text type="secondary">
                当前选中的问题无需人工裁决，已自动切换到一条待人工确认的议题。
              </Text>
            ) : null}
            <div className="human-gate-issue-box">
              <div className="human-gate-issue-head">
                <Text strong>{selectedIssue.title}</Text>
                {selectedIssue.needs_human ? <Tag color="error">高风险</Tag> : <Tag>常规</Tag>}
              </div>
              <Paragraph style={{ marginBottom: 8 }}>{selectedIssue.summary}</Paragraph>
              <Text type="secondary">
                当前状态 {selectedIssue.status} · 参与专家 {selectedIssue.participant_expert_ids.join("、") || "-"}
              </Text>
            </div>
            <Input.TextArea
              rows={4}
              value={decisionComment}
              onChange={(event) => onDecisionCommentChange(event.target.value)}
              placeholder="填写人工裁决意见，记录为什么接受或驳回该议题。"
            />
            <Space>
              <Button
                type="primary"
                danger
                disabled={!canSubmitDecision}
                loading={submitting}
                onClick={onReject}
              >
                驳回议题
              </Button>
              <Button
                type="primary"
                disabled={!canSubmitDecision}
                loading={submitting}
                onClick={onApprove}
              >
                批准并收敛
              </Button>
            </Space>
          </>
        )}
      </Space>
    </Card>
  );
};

export default HumanGatePanel;
