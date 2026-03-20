import React from "react";
import { Button, Card, List, Space, Tag, Typography } from "antd";

import type { ReviewEvent } from "@/services/api";

const { Text } = Typography;

type EventTimelineProps = {
  events: ReviewEvent[];
};

const INITIAL_VISIBLE_EVENTS = 120;
const VISIBLE_EVENTS_STEP = 120;

// 事件时间线卡用于按时间顺序回看审核运行过程。
const EventTimeline: React.FC<EventTimelineProps> = ({ events }) => {
  const [visibleCount, setVisibleCount] = React.useState(INITIAL_VISIBLE_EVENTS);
  const visibleEvents = React.useMemo(() => events.slice(-visibleCount), [events, visibleCount]);
  const hiddenCount = Math.max(events.length - visibleEvents.length, 0);

  React.useEffect(() => {
    setVisibleCount(INITIAL_VISIBLE_EVENTS);
  }, [events.length]);

  return (
    <Card className="module-card process-sidebar-card process-sidebar-card-lg" title="专家实时对话流">
      {hiddenCount > 0 ? (
        <Space style={{ padding: "0 16px 12px" }}>
          <Button type="link" size="small" onClick={() => setVisibleCount((current) => current + VISIBLE_EVENTS_STEP)}>
            {`加载更早事件（剩余 ${hiddenCount} 条）`}
          </Button>
        </Space>
      ) : null}
      <div className="process-card-scroll">
        <List
          dataSource={visibleEvents}
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
