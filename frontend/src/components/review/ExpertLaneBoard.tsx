import React from "react";
import { Card, List, Tag, Typography } from "antd";

import type { ReviewEvent, ReviewSummary } from "@/services/api";

const { Text } = Typography;

type ExpertLaneBoardProps = {
  review: ReviewSummary | null;
  events: ReviewEvent[];
};

const ExpertLaneBoard: React.FC<ExpertLaneBoardProps> = ({ review, events }) => {
  const experts = review?.selected_experts || [];

  return (
    <Card className="module-card" title="专家泳道">
      <List
        dataSource={experts}
        locale={{ emptyText: "当前还没有专家分配信息。" }}
        renderItem={(expertId) => {
          const expertEvents = events.filter((item) => String(item.payload?.expert_id || "") === expertId);
          return (
            <List.Item>
              <List.Item.Meta
                title={
                  <div className="review-event-title">
                    <Tag color="blue">{expertId}</Tag>
                    <span>{expertEvents.length > 0 ? "已参与" : "待触发"}</span>
                  </div>
                }
                description={
                  <Text type="secondary">
                    {expertEvents[expertEvents.length - 1]?.message || "当前还没有该专家的实时事件"}
                  </Text>
                }
              />
            </List.Item>
          );
        }}
      />
    </Card>
  );
};

export default ExpertLaneBoard;
