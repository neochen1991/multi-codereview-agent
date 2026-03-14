import React from "react";
import { Card, Descriptions, Empty, Tag } from "antd";

import type { DebateIssue } from "@/services/api";

type IssueDetailPanelProps = {
  issue: DebateIssue | null;
};

// 议题详情卡主要展示当前选中 issue 的摘要和参与专家。
const IssueDetailPanel: React.FC<IssueDetailPanelProps> = ({ issue }) => {
  return (
    <Card className="module-card process-sidebar-card process-sidebar-card-md" title="议题详情">
      <div className="process-card-scroll">
        {!issue ? (
          <Empty description="选择一个议题后，这里会展示裁决信息、证据和参与专家。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
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
            <Descriptions.Item label="参与专家">
              {issue.participant_expert_ids.join("、") || "-"}
            </Descriptions.Item>
            <Descriptions.Item label="证据">
              {issue.evidence.join("、") || "-"}
            </Descriptions.Item>
            <Descriptions.Item label="关联 findings">
              {issue.finding_ids.join("、") || "-"}
            </Descriptions.Item>
          </Descriptions>
        )}
      </div>
    </Card>
  );
};

export default IssueDetailPanel;
