import React from "react";
import { Card, List, Tag, Typography } from "antd";

import type { ReviewEvent } from "@/services/api";

const { Text } = Typography;

type EventTimelineProps = {
  events: ReviewEvent[];
};

const EventTimeline: React.FC<EventTimelineProps> = ({ events }) => {
  return (
    <Card className="module-card process-sidebar-card process-sidebar-card-lg" title="专家实时对话流">
      <div className="process-card-scroll">
        <List
          dataSource={events}
          locale={{ emptyText: "当前还没有事件，启动审核后会在这里显示流程轨迹。" }}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <div className="review-event-title">
                    <Tag color="blue">{item.phase}</Tag>
                    <span>{item.message}</span>
                  </div>
                }
                description={
                  <Text type="secondary">
                    {item.event_type} · {new Date(item.created_at).toLocaleString("zh-CN")}
                  </Text>
                }
              />
            </List.Item>
          )}
        />
      </div>
    </Card>
  );
};

export default EventTimeline;
