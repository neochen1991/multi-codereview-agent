import React from "react";
import { Card, Descriptions, Empty, Tag, Typography } from "antd";

import type { DebateIssue, ReviewFinding } from "@/services/api";

const { Paragraph } = Typography;

type IssueDetailPanelProps = {
  issue: DebateIssue | null;
  finding?: ReviewFinding | null;
};

// 议题详情卡主要展示当前选中 issue 的摘要和参与专家。
const IssueDetailPanel: React.FC<IssueDetailPanelProps> = ({ issue, finding }) => {
  const confidenceBreakdown = issue?.confidence_breakdown || {};
  const codeContext = finding?.code_context;
  const contextFiles = codeContext?.context_files || finding?.context_files || [];

  return (
    <Card className="module-card process-sidebar-card process-sidebar-card-md" title="议题详情">
      <div className="process-card-scroll">
        {!issue ? (
          <Empty description="选择一个议题后，这里会展示裁决信息、证据和参与专家。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="状态">
                <Tag color={issue.status === "needs_human" ? "error" : issue.status === "resolved" ? "success" : "processing"}>
                  {issue.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="严重度">
                <Tag color={issue.severity === "blocker" || issue.severity === "high" ? "error" : "processing"}>
                  {issue.severity}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="裁决路径">
                {issue.resolution || (issue.needs_human ? "human_gate" : "judge_merge")}
              </Descriptions.Item>
              <Descriptions.Item label="是否辩论">
                <Tag color={issue.needs_debate ? "processing" : "default"}>
                  {issue.needs_debate ? "debated" : "direct-merge"}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="问题位置">
                {issue.file_path ? `${issue.file_path}:${issue.line_start || 1}` : "-"}
              </Descriptions.Item>
              <Descriptions.Item label="参与专家">
                {issue.participant_expert_ids.join("、") || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="证据">
                {issue.evidence.join("、") || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="关联 findings">
                {issue.finding_ids.join("、") || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="置信度">
                {`${(issue.confidence * 100).toFixed(0)}%`}
              </Descriptions.Item>
            </Descriptions>

            {Object.keys(confidenceBreakdown).length ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>置信度分解</Paragraph>
                <Descriptions column={1} size="small">
                  {"base_weighted_confidence" in confidenceBreakdown ? (
                    <Descriptions.Item label="基础加权分">
                      {String(confidenceBreakdown.base_weighted_confidence)}
                    </Descriptions.Item>
                  ) : null}
                  {"consensus_bonus" in confidenceBreakdown ? (
                    <Descriptions.Item label="一致性加分">{String(confidenceBreakdown.consensus_bonus)}</Descriptions.Item>
                  ) : null}
                  {"evidence_bonus" in confidenceBreakdown ? (
                    <Descriptions.Item label="证据加分">{String(confidenceBreakdown.evidence_bonus)}</Descriptions.Item>
                  ) : null}
                  {"verification_bonus" in confidenceBreakdown ? (
                    <Descriptions.Item label="核验加分">{String(confidenceBreakdown.verification_bonus)}</Descriptions.Item>
                  ) : null}
                  {"hypothesis_penalty" in confidenceBreakdown ? (
                    <Descriptions.Item label="推测扣分">{String(confidenceBreakdown.hypothesis_penalty)}</Descriptions.Item>
                  ) : null}
                </Descriptions>
              </div>
            ) : null}

            {finding ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>关联代码上下文</Paragraph>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="提出专家">{finding.expert_id}</Descriptions.Item>
                  <Descriptions.Item label="路由原因">
                    {codeContext?.routing_reason || "当前未记录路由原因"}
                  </Descriptions.Item>
                  <Descriptions.Item label="目标 hunk">
                    {codeContext?.target_hunk?.hunk_header || "当前未记录 target hunk"}
                  </Descriptions.Item>
                  <Descriptions.Item label="上下文文件">
                    {contextFiles.length ? contextFiles.join("、") : "-"}
                  </Descriptions.Item>
                </Descriptions>
              </div>
            ) : null}
          </>
        )}
      </div>
    </Card>
  );
};

export default IssueDetailPanel;
