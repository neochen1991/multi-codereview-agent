import React from "react";
import { Card, Empty, List, Tag, Typography } from "antd";

import type { ConversationMessage } from "@/services/api";

const { Paragraph, Text } = Typography;

type ConversationMessageListProps = {
  issueId: string;
  messages: ConversationMessage[];
};

const ConversationMessageList: React.FC<ConversationMessageListProps> = ({
  issueId,
  messages,
}) => {
  return (
    <Card className="module-card" title="议题对话">
      {!issueId ? (
        <Empty description="请先从左侧选择一个议题。" />
      ) : (
        <List
          dataSource={messages}
          locale={{ emptyText: "当前议题还没有更多专家发言。" }}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <div className="review-event-title">
                    <Tag color="geekblue">{item.expert_id}</Tag>
                    <span>{item.message_type}</span>
                  </div>
                }
                description={
                  <>
                    <Paragraph style={{ marginBottom: 8 }}>{item.content}</Paragraph>
                    <div style={{ marginBottom: 8 }}>
                      {typeof item.metadata?.file_path === "string" ? (
                        <Tag>{String(item.metadata.file_path)}</Tag>
                      ) : null}
                      {typeof item.metadata?.line_start === "number" ? (
                        <Tag color="blue">L{String(item.metadata.line_start)}</Tag>
                      ) : null}
                      {typeof item.metadata?.decision === "string" ? (
                        <Tag color="purple">{String(item.metadata.decision)}</Tag>
                      ) : null}
                    </div>
                    <Text type="secondary">
                      {new Date(item.created_at).toLocaleString("zh-CN")}
                    </Text>
                  </>
                }
              />
            </List.Item>
          )}
        />
      )}
    </Card>
  );
};

export default ConversationMessageList;
