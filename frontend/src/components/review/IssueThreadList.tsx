import React from "react";
import { Card, List, Tag, Typography } from "antd";

import type { DebateIssue } from "@/services/api";

const { Paragraph, Text } = Typography;

type IssueThreadListProps = {
  issues: DebateIssue[];
  selectedIssueId: string;
  onSelect: (issueId: string) => void;
};

const IssueThreadList: React.FC<IssueThreadListProps> = ({
  issues,
  selectedIssueId,
  onSelect,
}) => {
  return (
    <Card className="module-card process-sidebar-card process-sidebar-card-lg" title="议题线程">
      <div className="process-card-scroll">
        <List
          dataSource={issues}
          locale={{ emptyText: "暂无可进入讨论的议题。" }}
          renderItem={(item) => (
            <List.Item
              className={selectedIssueId === item.issue_id ? "thread-selected" : ""}
              onClick={() => onSelect(item.issue_id)}
              style={{ cursor: "pointer" }}
            >
              <List.Item.Meta
                title={
                  <div className="review-finding-title">
                    <Tag color={selectedIssueId === item.issue_id ? "processing" : "default"}>
                      {item.status}
                    </Tag>
                    {item.needs_human ? <Tag color="error">需人工</Tag> : null}
                    <Tag color={item.verified ? "success" : "warning"}>
                      {item.verified ? "已核验" : "待核验"}
                    </Tag>
                    <span>{item.title}</span>
                  </div>
                }
                description={
                  <>
                    <Paragraph ellipsis={{ rows: 2 }} style={{ marginBottom: 8 }}>
                      {item.summary}
                    </Paragraph>
                    <Text type="secondary">
                      {item.participant_expert_ids.join(" · ") || "暂无参与专家"} · 置信度{" "}
                      {(item.confidence * 100).toFixed(0)}%
                    </Text>
                  </>
                }
              />
            </List.Item>
          )}
        />
      </div>
    </Card>
  );
};

export default IssueThreadList;
