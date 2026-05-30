import React from "react";
import { Alert, Button, Card, Descriptions, Empty, Input, Space, Tag, Typography } from "antd";

import type { DebateIssue, ReviewFinding, ReviewSummary } from "@/services/api";
import {
  humanizeExpertId,
  humanizeReviewStatus,
  humanizeReviewText,
  humanizeSeverity,
} from "@/utils/displayText";
import { evidenceStepLabel, evidenceStepSummary } from "./evidenceChainDisplay";
import {
  buildReadableFixSummary,
  buildReadableIssueSummary,
  buildReadableIssueTitle,
  cleanUserFacingList,
  cleanUserFacingText,
  issueTextMatchesIssueType,
  issueTypeDisplayLabel,
} from "./issueDisplayQuality";

const { Paragraph, Text } = Typography;

const uniqueList = (values?: string[]) => Array.from(new Set((values || []).map((item) => String(item || "").trim()).filter(Boolean)));

type HumanGatePanelProps = {
  review: ReviewSummary | null;
  selectedIssue: DebateIssue | null;
  finding?: ReviewFinding | null;
  isFallbackIssue?: boolean;
  decisionComment: string;
  submitting: boolean;
  onDecisionCommentChange: (value: string) => void;
  onApprove: () => void;
  onReject: () => void;
  className?: string;
};

// 人工确认卡负责处理待确认问题的批准/驳回动作。
const HumanGatePanel: React.FC<HumanGatePanelProps> = ({
  review,
  selectedIssue,
  finding = null,
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
  const primaryExpertId = String(selectedIssue?.primary_expert_id || selectedIssue?.participant_expert_ids?.[0] || "").trim();
  const participantExperts = uniqueList(selectedIssue?.participant_expert_ids).filter((item) => item !== primaryExpertId);
  const issueTextType = selectedIssue?.normalized_issue_type || finding?.normalized_issue_type || selectedIssue?.finding_type || finding?.finding_type || selectedIssue?.title || "";
  const selectedSummaryAligned = issueTextMatchesIssueType(issueTextType, selectedIssue?.summary);
  const findingSummaryAligned = issueTextMatchesIssueType(
    issueTextType,
    [
      finding?.normalized_issue_type,
      finding?.finding_type,
      finding?.title,
      finding?.summary,
      finding?.rule_based_reasoning,
    ].filter(Boolean).join("\n"),
  );
  const issueTitle = selectedIssue
    ? buildReadableIssueTitle({
        ...selectedIssue,
        summary: selectedIssue.summary || finding?.summary,
      })
    : "";
  const issueDescription = selectedIssue
    ? buildReadableIssueSummary({
        ...selectedIssue,
        summary:
          (findingSummaryAligned ? finding?.summary : "") ||
          (selectedSummaryAligned ? selectedIssue.summary : "") ||
          finding?.summary ||
          selectedIssue.title,
      })
    : "";
  const issueEvidence = cleanUserFacingList(selectedIssue?.evidence);
  const evidenceChain = selectedIssue?.evidence_chain?.length ? selectedIssue.evidence_chain : finding?.evidence_chain || [];
  const aggregatedStrategies = uniqueList(selectedIssue?.aggregated_remediation_strategies);
  const aggregatedSuggestions = uniqueList(selectedIssue?.aggregated_remediation_suggestions);
  const aggregatedSteps = uniqueList(selectedIssue?.aggregated_remediation_steps);
  const issueStrategy = cleanUserFacingText(selectedIssue?.remediation_strategy || aggregatedStrategies[0] || finding?.remediation_strategy || "");
  const issueSuggestion = cleanUserFacingText(selectedIssue?.remediation_suggestion || aggregatedSuggestions[0] || finding?.remediation_suggestion || "");
  const issueSteps = uniqueList(selectedIssue?.remediation_steps).length
    ? uniqueList(selectedIssue?.remediation_steps)
    : aggregatedSteps.length
      ? aggregatedSteps
      : cleanUserFacingList(finding?.remediation_steps);
  const fixSummary = selectedIssue
    ? buildReadableFixSummary({
        ...selectedIssue,
        remediation_strategy: issueStrategy,
        remediation_suggestion: issueSuggestion,
        remediation_steps: issueSteps,
      })
    : "";
  const needsHumanReason = selectedIssue?.needs_human
    ? "该问题置信度、影响面或证据冲突达到人工确认条件，需要人工判断是否进入正式整改。"
    : "该问题当前不需要人工确认。";

  return (
    <Card className={`module-card ${className || ""}`.trim()} title="人工确认">
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <div className="human-gate-status-row">
          <Tag color={humanStatus === "requested" ? "error" : humanStatus === "approved" ? "success" : "default"}>
            {humanizeReviewStatus(humanStatus)}
          </Tag>
          <Text type="secondary">
            待人工确认 {review?.pending_human_issue_ids?.length || 0} 个
          </Text>
        </div>
        {!selectedIssue ? (
          <Empty description="先从问题清单中选择一条需要人工确认的问题。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <>
            {isFallbackIssue ? (
              <Text type="secondary">
                当前选中的问题无需人工确认，已自动切换到一条待人工确认的问题。
              </Text>
            ) : null}
            <Alert
              type={selectedIssue.needs_human ? "warning" : "info"}
              showIcon
              message="需要人工确认什么"
              description={needsHumanReason}
            />
            <div className="human-gate-issue-box">
              <div className="human-gate-issue-head">
                <Text strong>{issueTitle}</Text>
                <Space size={4} wrap>
                  {selectedIssue.needs_human ? <Tag color="error">待确认</Tag> : <Tag>常规</Tag>}
                  <Tag color={selectedIssue.severity === "high" || selectedIssue.severity === "critical" ? "error" : "processing"}>
                    {humanizeSeverity(selectedIssue.severity)}
                  </Tag>
                  <Tag>{`${(selectedIssue.confidence * 100).toFixed(0)}%`}</Tag>
                </Space>
              </div>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="问题描述">
                  <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>{issueDescription}</Paragraph>
                </Descriptions.Item>
                <Descriptions.Item label="问题位置">
                  {selectedIssue.file_path ? `${selectedIssue.file_path}:${selectedIssue.line_start || 1}` : "-"}
                </Descriptions.Item>
                <Descriptions.Item label="当前状态">
                  <Space wrap size={4}>
                    <Tag color={selectedIssue.status === "needs_human" ? "error" : selectedIssue.status === "resolved" ? "success" : "processing"}>
                      {humanizeReviewStatus(selectedIssue.status)}
                    </Tag>
                    <Tag>{humanizeReviewStatus(selectedIssue.resolution || "needs_human_review")}</Tag>
                    <Tag>{issueTypeDisplayLabel(selectedIssue.category_label, selectedIssue.normalized_issue_type, selectedIssue.finding_type)}</Tag>
                  </Space>
                </Descriptions.Item>
                <Descriptions.Item label="检查角色">
                  {humanizeExpertId(primaryExpertId)}
                  {participantExperts.length ? ` · 参与 ${participantExperts.map((item) => humanizeExpertId(item)).join("、")}` : ""}
                </Descriptions.Item>
                <Descriptions.Item label="判断依据">
                  {issueEvidence.length ? (
                    <div>
                      {issueEvidence.slice(0, 5).map((item, index) => (
                        <Paragraph key={`${index}-${item}`} style={{ marginBottom: 6 }}>
                          {index + 1}. {item}
                        </Paragraph>
                      ))}
                    </div>
                  ) : (
                    "-"
                  )}
                </Descriptions.Item>
                {cleanUserFacingText(selectedIssue.confidence_rationale) ? (
                  <Descriptions.Item label="判断依据">
                    <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                      {cleanUserFacingText(selectedIssue.confidence_rationale)}
                    </Paragraph>
                  </Descriptions.Item>
                ) : null}
                {evidenceChain.length ? (
                  <Descriptions.Item label="系统核对过的证据">
                    <div>
                      {evidenceChain.slice(0, 5).map((step, index) => (
                        <Paragraph key={`${index}-${step.step || "evidence"}`} style={{ marginBottom: 6 }}>
                          <Text strong>{evidenceStepLabel(step)}：</Text>
                          {cleanUserFacingText(evidenceStepSummary(step, JSON.stringify(selectedIssue)))}
                        </Paragraph>
                      ))}
                    </div>
                  </Descriptions.Item>
                ) : null}
                {issueStrategy || issueSuggestion || issueSteps.length || fixSummary ? (
                  <Descriptions.Item label="修复参考">
                    <div>
                      {fixSummary ? <Paragraph style={{ marginBottom: 6 }}>{fixSummary}</Paragraph> : null}
                      {issueStrategy ? <Paragraph style={{ marginBottom: 6 }}>{issueStrategy}</Paragraph> : null}
                      {issueSuggestion ? <Paragraph style={{ marginBottom: 6 }}>{issueSuggestion}</Paragraph> : null}
                      {issueSteps.slice(0, 4).map((item, index) => (
                        <Paragraph key={`${index}-${item}`} style={{ marginBottom: 6 }}>
                          {index + 1}. {humanizeReviewText(item)}
                        </Paragraph>
                      ))}
                    </div>
                  </Descriptions.Item>
                ) : null}
              </Descriptions>
            </div>
            <Alert
              type="info"
              showIcon
              message="人工结论会进入反馈学习"
              description="批准会沉淀为同类真实问题样本；驳回会沉淀为误报样本。后续检视会基于相似案例调整提示、置信度或过滤策略。"
            />
            <Input.TextArea
              rows={4}
              value={decisionComment}
              onChange={(event) => onDecisionCommentChange(event.target.value)}
              placeholder="填写人工确认意见。建议说明批准/驳回依据，例如证据是否成立、是否存在反例、是否影响真实业务。"
            />
            <Space>
              <Button
                type="primary"
                danger
                disabled={!canSubmitDecision}
                loading={submitting}
                onClick={onReject}
              >
                驳回问题
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
