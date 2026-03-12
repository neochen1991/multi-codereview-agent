import React from "react";
import { Card, Descriptions, Empty, Tag } from "antd";

import type { DebateIssue } from "@/services/api";

type ToolAuditPanelProps = {
  issue: DebateIssue | null;
};

const ToolAuditPanel: React.FC<ToolAuditPanelProps> = ({ issue }) => {
  return (
    <Card className="module-card process-sidebar-card process-sidebar-card-sm" title="工具核验">
      <div className="process-card-scroll">
        {!issue ? (
          <Empty description="选择一个议题后，这里会展示 verifier 和工具核验结果。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <Descriptions column={1} size="small">
            <Descriptions.Item label="Verifier">
              {issue.verifier_name || "builtin_verifier"}
            </Descriptions.Item>
            <Descriptions.Item label="Tool">
              {issue.tool_name ? <Tag color="processing">{issue.tool_name}</Tag> : "-"}
            </Descriptions.Item>
            <Descriptions.Item label="结果">
              <Tag color={issue.tool_verified ? "success" : "warning"}>
                {issue.tool_verified ? "tool_verified" : "not_verified"}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="说明">
              {issue.tool_verified
                ? "当前议题已经过工具核验，可作为 judge / human gate 的强证据。"
                : "当前议题还没有足够强的工具证据，仍需结合 debate 与人工判断。"}
            </Descriptions.Item>
          </Descriptions>
        )}
      </div>
    </Card>
  );
};

export default ToolAuditPanel;
