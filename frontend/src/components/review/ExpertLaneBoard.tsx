import React, { useMemo } from "react";
import { Card, Empty, Segmented, Space, Tag, Typography } from "antd";

import type { ConversationMessage, ReviewSummary } from "@/services/api";

const { Paragraph, Text } = Typography;

type ExpertLaneBoardProps = {
  review: ReviewSummary | null;
  messages: ConversationMessage[];
};

type LaneCategory = "all" | "command" | "skill" | "tool" | "chat" | "status";

type LaneEntry = {
  id: string;
  expertId: string;
  timeText: string;
  category: Exclude<LaneCategory, "all">;
  title: string;
  summary: string;
};

const categorizeMessage = (message: ConversationMessage): Exclude<LaneCategory, "all"> => {
  const metadata = message.metadata || {};
  if (message.message_type === "main_agent_command") return "command";
  if (message.message_type === "expert_skill_call") return "skill";
  if (message.message_type === "expert_tool_call" || String(metadata.tool_name || "")) return "tool";
  if (message.message_type === "judge_summary" || message.message_type === "main_agent_summary") return "status";
  return "chat";
};

const buildLaneEntry = (message: ConversationMessage): LaneEntry => {
  const metadata = message.metadata || {};
  const category = categorizeMessage(message);
  const toolName =
    typeof metadata.tool_name === "string"
      ? metadata.tool_name
      : typeof metadata.skill_name === "string"
        ? metadata.skill_name
        : "";
  const targetExpertId = typeof metadata.target_expert_id === "string" ? metadata.target_expert_id : "";
  const designAlignmentStatus =
    typeof metadata.design_alignment_status === "string" ? metadata.design_alignment_status : "";
  const designDocTitles = Array.isArray(metadata.design_doc_titles)
    ? metadata.design_doc_titles.map((item) => String(item)).filter(Boolean)
    : [];
  const titles: Record<Exclude<LaneCategory, "all">, string> = {
    command: `接收命令${targetExpertId ? ` · ${targetExpertId}` : ""}`,
    skill: `激活技能 ${toolName || "skill"}`,
    tool: `调用工具 ${toolName || "tool"}`,
    chat: message.message_type === "expert_analysis" ? "输出分析结论" : "专家对话",
    status: "收敛状态",
  };
  const summaryParts: string[] = [];
  if (typeof metadata.file_path === "string" && metadata.file_path) {
    summaryParts.push(metadata.file_path);
  }
  if (designAlignmentStatus && designDocTitles.length > 0) {
    summaryParts.push(`设计一致性: ${designAlignmentStatus}`);
  }
  if (Array.isArray(metadata.active_skills) && metadata.active_skills.length > 0) {
    summaryParts.push(`skills: ${metadata.active_skills.map((item) => String(item)).join(", ")}`);
  }
  summaryParts.push(message.content.trim());
  return {
    id: message.message_id,
    expertId: message.expert_id,
    timeText: new Date(message.created_at).toLocaleString("zh-CN"),
    category,
    title: titles[category],
    summary: summaryParts.filter(Boolean).join(" · "),
  };
};

const ExpertLaneBoard: React.FC<ExpertLaneBoardProps> = ({ review, messages }) => {
  const [categoryFilter, setCategoryFilter] = React.useState<LaneCategory>("all");
  const expertIds = useMemo(() => {
    const selected = review?.selected_experts || [];
    const fromMessages = Array.from(
      new Set(
        messages
          .map((item) => item.expert_id)
          .filter((value) => value && value !== "main_agent" && value !== "judge"),
      ),
    );
    return Array.from(new Set([...selected, ...fromMessages]));
  }, [messages, review?.selected_experts]);
  const laneEntries = useMemo(() => messages.map(buildLaneEntry), [messages]);

  return (
    <Card className="module-card expert-lane-card" title="专家泳道">
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Segmented
          value={categoryFilter}
          onChange={(value) => setCategoryFilter(value as LaneCategory)}
          options={[
            { label: "全部", value: "all" },
            { label: "命令", value: "command" },
            { label: "技能", value: "skill" },
            { label: "工具", value: "tool" },
            { label: "对话", value: "chat" },
            { label: "状态", value: "status" },
          ]}
        />
        {expertIds.length === 0 ? (
          <Empty description="当前还没有专家泳道数据。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <div className="expert-lane-grid">
            {expertIds.map((expertId) => {
              const rows = laneEntries.filter(
                (item) => item.expertId === expertId && (categoryFilter === "all" || item.category === categoryFilter),
              );
              return (
                <div key={expertId} className="expert-lane-column">
                  <div className="expert-lane-header">
                    <Tag color="blue">{expertId}</Tag>
                    <Text type="secondary">{rows.length > 0 ? `${rows.length} 条记录` : "暂无记录"}</Text>
                  </div>
                  <div className="expert-lane-body">
                    {rows.length === 0 ? (
                      <div className="expert-lane-empty">当前筛选下没有命中记录</div>
                    ) : (
                      rows.map((row) => (
                        <div key={row.id} className={`expert-lane-node expert-lane-node-${row.category}`}>
                          <div className="expert-lane-node-meta">
                            <Tag className="expert-lane-node-tag">{row.category}</Tag>
                            <Text type="secondary">{row.timeText}</Text>
                          </div>
                          <Text strong className="expert-lane-node-title">
                            {row.title}
                          </Text>
                          <Paragraph ellipsis={{ rows: 4, expandable: false }} className="expert-lane-node-summary">
                            {row.summary}
                          </Paragraph>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Space>
    </Card>
  );
};

export default ExpertLaneBoard;
