import React from "react";
import { Button, Card, Empty, Input, Space, Tag, Typography } from "antd";

import type { DebateIssue, ReviewSummary } from "@/services/api";

const { Paragraph, Text } = Typography;

type HumanGatePanelProps = {
  review: ReviewSummary | null;
  selectedIssue: DebateIssue | null;
  decisionComment: string;
  submitting: boolean;
  onDecisionCommentChange: (value: string) => void;
  onApprove: () => void;
  onReject: () => void;
};

const HumanGatePanel: React.FC<HumanGatePanelProps> = ({
  review,
  selectedIssue,
  decisionComment,
  submitting,
  onDecisionCommentChange,
  onApprove,
  onReject,
}) => {
  const humanStatus = review?.human_review_status || "not_required";

  return (
    <Card className="module-card" title="人工裁决">
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
          <Empty description="先从左侧议题线程中选择一个需要查看的议题。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <>
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
                disabled={!selectedIssue.needs_human}
                loading={submitting}
                onClick={onReject}
              >
                驳回议题
              </Button>
              <Button
                type="primary"
                disabled={!selectedIssue.needs_human}
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
