import React, { useEffect, useMemo, useState } from "react";
import { Card, Empty, List, Slider, Space, Tag, Typography } from "antd";

import type { ReviewEvent, ReviewReplayBundle } from "@/services/api";

const { Paragraph, Text } = Typography;

type ReplayConsolePanelProps = {
  replay: ReviewReplayBundle | null;
};

// 回放面板按时间轴重播审核事件，帮助开发者定位收敛路径。
const ReplayConsolePanel: React.FC<ReplayConsolePanelProps> = ({ replay }) => {
  const [cursor, setCursor] = useState(0);
  const events = replay?.events || [];
  const maxCursor = Math.max(events.length - 1, 0);
  useEffect(() => {
    setCursor(maxCursor);
  }, [maxCursor]);
  const visibleEvents = useMemo<ReviewEvent[]>(
    () => events.slice(0, cursor + 1),
    [cursor, events],
  );
  const visibleMessages = useMemo(
    () =>
      (replay?.messages || []).filter((message) => {
        const messageTime = new Date(message.created_at).getTime();
        const lastVisibleEvent = visibleEvents[visibleEvents.length - 1];
        if (!lastVisibleEvent) return false;
        return messageTime <= new Date(lastVisibleEvent.created_at).getTime();
      }),
    [replay?.messages, visibleEvents],
  );

  return (
    <Card className="module-card replay-card" title="回放模式">
      {!replay || events.length === 0 ? (
        <Empty description="当前审核还没有可回放的事件。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <div>
            <Text type="secondary">当前步数</Text>
            <Slider
              min={0}
              max={maxCursor}
              value={Math.min(cursor, maxCursor)}
              onChange={(value) => setCursor(Array.isArray(value) ? value[0] || 0 : value)}
              tooltip={{ formatter: (value) => `step ${value}` }}
            />
          </div>
          <Space wrap>
            <Tag color="processing">events: {visibleEvents.length}</Tag>
            <Tag color="blue">messages: {visibleMessages.length}</Tag>
            <Tag color={replay.review.status === "completed" ? "success" : "error"}>
              {replay.review.status}
            </Tag>
          </Space>
          <div className="replay-list-scroll">
            <List
              size="small"
              dataSource={visibleEvents}
              renderItem={(item) => (
                <List.Item>
                  <List.Item.Meta
                    title={
                      <div className="review-event-title">
                        <Tag color="geekblue">{item.phase}</Tag>
                        <span>{item.message}</span>
                      </div>
                    }
                    description={
                      <Text type="secondary">
                        {new Date(item.created_at).toLocaleString("zh-CN")}
                      </Text>
                    }
                  />
                </List.Item>
              )}
            />
          </div>
          <Paragraph className="replay-note">
            回放面板按事件时间顺序重放审查轨迹，便于查看从 finding、debate、tool verification 到
            human gate 的收敛过程。
          </Paragraph>
        </Space>
      )}
    </Card>
  );
};

export default ReplayConsolePanel;
