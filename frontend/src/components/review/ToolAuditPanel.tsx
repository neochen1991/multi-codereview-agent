import React from "react";
import { Card, Descriptions, Empty, List, Space, Tag, Typography } from "antd";

import type { DebateIssue } from "@/services/api";
import { humanizeReviewText } from "@/utils/displayText";

const { Text } = Typography;

type ToolAuditPanelProps = {
  issue: DebateIssue | null;
};

const stepLabel = (step: string): string => {
  const labels: Record<string, string> = {
    claim: "主张",
    anchor: "代码锚点",
    verifier: "工具核验",
    static_analysis: "静态信号",
    false_positive_filter: "误报过滤",
    confidence: "置信度",
  };
  return labels[step] || step;
};

const statusColor = (status?: string): string => {
  if (!status) return "default";
  if (["verified", "present", "anchored", "signal_matched", "true_positive"].includes(status)) return "success";
  if (["needs_verification", "weak", "not_verified", "abstain"].includes(status)) return "warning";
  if (["false_positive", "high_false_positive_risk", "missing"].includes(status)) return "error";
  return "processing";
};

const stepDescription = (step: NonNullable<DebateIssue["evidence_chain"]>[number]): string => {
  if (step.step === "claim") return step.claim || "-";
  if (step.step === "anchor") {
    const reasons = (step.reasons || []).slice(0, 3).join(" / ");
    const evidence = (step.evidence || []).slice(0, 2).join(" / ");
    return [step.file_path || "", step.line_start ? `L${step.line_start}` : "", evidence, reasons].filter(Boolean).join(" · ") || "-";
  }
  if (step.step === "verifier") return [step.tool_name, step.summary].filter(Boolean).join(" · ") || "-";
  if (step.step === "static_analysis") return (step.signals || []).join(" / ") || "-";
  if (step.step === "false_positive_filter") return step.reason || step.verdict || "-";
  if (step.step === "confidence") {
    const delta = typeof step.confidence_delta === "number" ? `${step.confidence_delta >= 0 ? "+" : ""}${step.confidence_delta.toFixed(2)}` : "";
    const final = typeof step.final_confidence === "number" ? `${Math.round(step.final_confidence * 100)}%` : "";
    return [final ? `最终 ${final}` : "", delta ? `变化 ${delta}` : "", step.false_positive_risk ? `误报风险 ${step.false_positive_risk}` : ""]
      .filter(Boolean)
      .join(" · ") || "-";
  }
  return step.summary || step.reason || "-";
};

// 工具核验卡用于展示某条 issue 的 verifier/tool 结果。
const ToolAuditPanel: React.FC<ToolAuditPanelProps> = ({ issue }) => {
  const evidenceChain = issue?.evidence_chain || [];
  return (
    <Card className="module-card process-sidebar-card process-sidebar-card-md" title="证据链与工具核验">
      <div className="process-card-scroll">
        {!issue ? (
          <Empty description="选择一个问题后，这里会展示核验器和工具核验结果。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="核验器">
                {humanizeReviewText(issue.verifier_name || "builtin_verifier")}
              </Descriptions.Item>
              <Descriptions.Item label="工具">
                {issue.tool_name ? <Tag color="processing">{humanizeReviewText(issue.tool_name)}</Tag> : "-"}
              </Descriptions.Item>
              <Descriptions.Item label="结果">
                <Tag color={issue.tool_verified ? "success" : "warning"}>
                  {humanizeReviewText(issue.tool_verified ? "tool_verified" : "not_verified")}
                </Tag>
              </Descriptions.Item>
            </Descriptions>
            {evidenceChain.length ? (
              <List
                size="small"
                dataSource={evidenceChain}
                renderItem={(step) => (
                  <List.Item>
                    <Space direction="vertical" size={2} style={{ width: "100%" }}>
                      <Space wrap>
                        <Tag color="blue">{stepLabel(step.step)}</Tag>
                        <Tag color={statusColor(step.status)}>{humanizeReviewText(step.status || "-")}</Tag>
                      </Space>
                      <Text type="secondary">{humanizeReviewText(stepDescription(step))}</Text>
                    </Space>
                  </List.Item>
                )}
              />
            ) : (
              <Empty description="当前问题尚未写入结构化证据链。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Space>
        )}
      </div>
    </Card>
  );
};

export default ToolAuditPanel;
