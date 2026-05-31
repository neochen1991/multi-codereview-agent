import React, { useEffect, useMemo, useState } from "react";
import { Card, Empty, List, Slider, Space, Tag, Typography } from "antd";

import type { ReviewEvent, ReviewReplayBundle } from "@/services/api";
import { humanizeExpertId, humanizeReviewText } from "@/utils/displayText";

const { Paragraph, Text } = Typography;

type ReplayConsolePanelProps = {
  replay: ReviewReplayBundle | null;
};

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

const asArray = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

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
  const diagnosticMessages = useMemo(
    () =>
      visibleMessages.filter((message) => {
        const metadata = asRecord(message.metadata);
        return Boolean(
          metadata.prompt_snapshot_summary ||
            metadata.prompt_snapshot_full ||
            metadata.model_raw_response_excerpt ||
            metadata.model_raw_response_full ||
            metadata.rule_check_results ||
            metadata.candidate_findings ||
            metadata.context_gaps ||
            metadata.environment_status,
        );
      }),
    [visibleMessages],
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
              {humanizeReviewText(replay.review.status)}
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
                        <Tag color="geekblue">{humanizeReviewText(item.phase)}</Tag>
                        <span>{humanizeReviewText(item.message)}</span>
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
          {diagnosticMessages.length ? (
            <List
              size="small"
              header={<Text strong>模型与规则诊断</Text>}
              dataSource={diagnosticMessages.slice(-6)}
              renderItem={(message) => {
                const metadata = asRecord(message.metadata);
                const promptSummary = asRecord(metadata.prompt_snapshot_summary);
                const ruleCoverage = asRecord(metadata.rule_coverage);
                const ruleChecks = asArray(metadata.rule_check_results);
                const candidates = asArray(metadata.candidate_findings);
                const contextGaps = asArray(metadata.context_gaps).map(String).filter(Boolean);
                const promptFull = String(metadata.prompt_snapshot_full || "");
                const rawExcerpt = String(metadata.model_raw_response_excerpt || "");
                const rawFull = String(metadata.model_raw_response_full || rawExcerpt);
                return (
                  <List.Item>
                    <Space direction="vertical" size={6} style={{ width: "100%" }}>
                      <Space wrap>
                        <Tag color="geekblue">{humanizeExpertId(message.expert_id)}</Tag>
                        <Tag>{humanizeReviewText(message.message_type)}</Tag>
                        {metadata.mode ? <Tag color="blue">{humanizeReviewText(String(metadata.mode))}</Tag> : null}
                        {metadata.model ? <Tag color="processing">{String(metadata.model)}</Tag> : null}
                        {metadata.prompt_profile ? <Tag color="purple">{humanizeReviewText(String(metadata.prompt_profile))}</Tag> : null}
                        {metadata.environment_status ? (
                          <Tag color={metadata.environment_status === "passed" ? "success" : "warning"}>
                            环境 {humanizeReviewText(String(metadata.environment_status))}
                          </Tag>
                        ) : null}
                      </Space>
                      {Object.keys(promptSummary).length ? (
                        <Text type="secondary">
                          提示词 {String(promptSummary.prompt_chars || 0)} 字符 · 规则{" "}
                          {String(ruleCoverage.checked_rule_count || ruleChecks.length)} · 候选发现{" "}
                          {String(ruleCoverage.candidate_count || candidates.length)}
                        </Text>
                      ) : null}
                      {promptFull ? (
                        <Paragraph
                          ellipsis={{ rows: 4, expandable: true, symbol: "展开 prompt" }}
                        >
                          {promptFull}
                        </Paragraph>
                      ) : null}
                      {contextGaps.length ? (
                        <Space wrap>
                          {contextGaps.slice(0, 6).map((item) => (
                            <Tag key={item} color="warning">
                              {humanizeReviewText(item)}
                            </Tag>
                          ))}
                        </Space>
                      ) : null}
                      {rawFull ? (
                        <Paragraph ellipsis={{ rows: 3, expandable: true, symbol: "展开" }}>
                          {humanizeReviewText(rawFull)}
                        </Paragraph>
                      ) : null}
                    </Space>
                  </List.Item>
                );
              }}
            />
          ) : null}
          <Paragraph className="replay-note">
            回放面板按事件时间顺序重放审查轨迹，便于查看从检视发现、复核、工具核验到
            人工确认的收敛过程。
          </Paragraph>
        </Space>
      )}
    </Card>
  );
};

export default ReplayConsolePanel;
