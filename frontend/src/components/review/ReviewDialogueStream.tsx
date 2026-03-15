import React, { useEffect, useMemo, useRef, useState } from "react";
import { Avatar, Button, Empty, Segmented, Select, Space, Tag, Typography } from "antd";

import type { ConversationMessage, ReviewEvent, ReviewSummary } from "@/services/api";

const { Paragraph, Text } = Typography;

type StructuredSection = {
  label: string;
  values: string[];
};

type StructuredGroup = {
  title: string;
  sections: StructuredSection[];
};

export type ReviewDialogueViewMessage = {
  id: string;
  timeText: string;
  agentName: string;
  side: "agent" | "system";
  isMainAgent?: boolean;
  messageKind: "chat" | "tool" | "skill" | "command" | "status";
  categories: Array<"chat" | "tool" | "skill" | "command" | "status">;
  phase: string;
  eventType: string;
  status: "streaming" | "done" | "error";
  summary: string;
  detail: string;
  metadata: Record<string, unknown>;
  headerNote?: string;
};

type Props = {
  messages: ConversationMessage[];
  review?: ReviewSummary | null;
  events?: ReviewEvent[];
};

const normalizeText = (value: string): string =>
  value
    .replace(/\r/g, "")
    .split("\n")
    .map((line) => line.replace(/^\s{0,3}#{1,6}\s+/, "").replace(/^\s*[-*]\s+/, "• "))
    .join("\n");

const buildCompactDetail = (value: string): { text: string; truncated: boolean } => {
  const normalized = normalizeText(value || "");
  const lines = normalized.split("\n").map((line) => line.trim()).filter(Boolean);
  const compact = lines.slice(0, 3).join("\n");
  if (compact.length > 220) return { text: `${compact.slice(0, 220).trim()}...`, truncated: true };
  return { text: lines.length > 3 ? `${compact}\n...` : compact, truncated: lines.length > 3 };
};

const normalizeValueList = (value: unknown): string[] => {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (typeof item === "string") return item.trim();
      if (item == null) return "";
      try {
        return JSON.stringify(item);
      } catch {
        return String(item);
      }
    })
    .filter(Boolean);
};

const normalizeSingleValue = (value: unknown): string[] => {
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
};

const tryParseJsonBlock = (value: string): Record<string, unknown> | null => {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const fenced = raw.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  const candidate = fenced ? fenced[1].trim() : raw;
  if (!(candidate.startsWith("{") && candidate.endsWith("}"))) return null;
  try {
    const parsed = JSON.parse(candidate);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    return null;
  }
  return null;
};

const getDesignAlignmentLabel = (status: string): string => {
  switch (status) {
    case "aligned":
      return "设计一致";
    case "partially_aligned":
      return "部分符合设计";
    case "misaligned":
      return "与设计冲突";
    case "insufficient_design_context":
      return "设计上下文不足";
    default:
      return status;
  }
};

const mapMessage = (message: ConversationMessage): ReviewDialogueViewMessage => {
  // 把后端原始消息统一映射成聊天视图模型，
  // 这样主 Agent、专家、Judge、工具调用都能复用同一种渲染壳。
  const metadata = message.metadata || {};
  const eventType = message.message_type;
  const activeSkills = Array.isArray(metadata.active_skills)
    ? metadata.active_skills.map((item) => String(item)).filter(Boolean)
    : [];
  let messageKind: ReviewDialogueViewMessage["messageKind"] = "chat";
  if (eventType === "main_agent_command") messageKind = "command";
  if (eventType === "judge_summary" || eventType === "main_agent_summary") messageKind = "status";
  if (eventType === "expert_skill_call") messageKind = "skill";
  if (eventType === "expert_tool_call" || (String(metadata.tool_name || "") && eventType !== "expert_skill_call")) messageKind = "tool";
  const categorySet = new Set<ReviewDialogueViewMessage["categories"][number]>([messageKind]);
  if (activeSkills.length > 0) categorySet.add("skill");
  const filePath = typeof metadata.file_path === "string" ? metadata.file_path : "";
  const lineStart = typeof metadata.line_start === "number" ? metadata.line_start : 0;
  const targetExpertId = typeof metadata.target_expert_id === "string" ? metadata.target_expert_id : "";
  const targetExpertName = typeof metadata.target_expert_name === "string" ? metadata.target_expert_name : "";
  const replyToExpertId = typeof metadata.reply_to_expert_id === "string" ? metadata.reply_to_expert_id : "";
  const model = typeof metadata.model === "string" ? metadata.model : "";
  const mode = typeof metadata.mode === "string" ? metadata.mode : "";
  const targetHunk =
    metadata.target_hunk && typeof metadata.target_hunk === "object"
      ? (metadata.target_hunk as Record<string, unknown>)
      : null;
  const hunkHeader = typeof targetHunk?.hunk_header === "string" ? targetHunk.hunk_header : "";
  const summaryParts: string[] = [];
  if (eventType === "main_agent_command") {
    summaryParts.push(`主Agent 点名 ${targetExpertName || targetExpertId} 处理这段代码`);
  } else if (eventType === "expert_ack") {
    summaryParts.push(`${message.expert_id} 已接单，准备开始分析`);
  } else if (eventType === "expert_analysis") {
    summaryParts.push(`${message.expert_id} 已提交首轮分析`);
  } else if (eventType === "expert_tool_call") {
    summaryParts.push(`${message.expert_id} 正在调用工具 ${String(metadata.tool_name || "")}`);
  } else if (eventType === "expert_skill_call") {
    summaryParts.push(`${message.expert_id} 正在调用运行时工具 ${String(metadata.tool_name || metadata.skill_name || "")}`);
  } else if (eventType === "debate_message") {
    summaryParts.push(`${message.expert_id} 正在回应 ${replyToExpertId || "上一位专家"}`);
  } else if (eventType === "judge_summary") {
    summaryParts.push("Judge 正在收敛本轮议题");
  } else if (eventType === "main_agent_summary") {
    summaryParts.push("主Agent 已输出最终收敛播报");
  }
  if (activeSkills.length > 0) summaryParts.push(`激活技能：${activeSkills.join(" / ")}`);
  if (model) summaryParts.push(`模型：${model}${mode === "fallback" ? " · fallback" : ""}`);
  if (filePath) summaryParts.push(`定位：${filePath}${lineStart ? `:${lineStart}` : ""}`);
  if (hunkHeader) summaryParts.push(`Hunk：${hunkHeader}`);
  const messageStatus =
    mode === "pending" ? "streaming" : mode === "fallback" ? "error" : "done";
  const detail = buildInvocationDetail(eventType, message.content, metadata);
  return {
    id: message.message_id,
    timeText: new Date(message.created_at).toLocaleString("zh-CN"),
    agentName: message.expert_id,
    side: message.expert_id === "main_agent" || message.expert_id === "judge" ? "system" : "agent",
    isMainAgent: message.expert_id === "main_agent",
    messageKind,
    categories: Array.from(categorySet),
    phase: String(metadata.phase || (message.expert_id === "judge" ? "judge" : "review")),
    eventType,
    status: messageStatus,
    summary: summaryParts.join(" · ") || message.content.trim(),
    detail,
    metadata,
    headerNote:
      eventType === "main_agent_command"
        ? `派工给 ${targetExpertName || targetExpertId || "指定专家"}`
        : replyToExpertId
          ? `回应 ${replyToExpertId}`
          : undefined,
  };
};

const buildInvocationDetail = (
  eventType: string,
  content: string,
  metadata: Record<string, unknown>,
): string => {
  // 工具消息会把结构化结果压成更适合阅读的文本，
  // 过程页才能直接看懂“刚调用了什么、拿到了什么证据”。
  if (eventType === "expert_tool_call") {
    const toolName = typeof metadata.tool_name === "string" ? metadata.tool_name : "tool";
    const toolResult = metadata.tool_result;
    return formatInvocationDetail(`工具 ${toolName}`, content, toolResult);
  }
  if (eventType === "expert_skill_call") {
    const skillName = typeof metadata.skill_name === "string" ? metadata.skill_name : typeof metadata.tool_name === "string" ? metadata.tool_name : "skill";
    const skillResult = metadata.skill_result || metadata.tool_result;
    return formatInvocationDetail(`技能 ${skillName}`, content, skillResult);
  }
  return content;
};

const formatInvocationDetail = (label: string, summary: string, result: unknown): string => {
  const lines = [summary];
  if (result && typeof result === "object") {
    const payload = result as Record<string, unknown>;
    Object.entries(payload).forEach(([key, value]) => {
      if (key === "summary" || value == null) return;
      if (typeof value === "string") {
        lines.push(`${key}: ${value}`);
        return;
      }
      if (Array.isArray(value)) {
        lines.push(`${key}: ${value.map((item) => (typeof item === "string" ? item : JSON.stringify(item))).join(" | ")}`);
        return;
      }
      lines.push(`${key}: ${JSON.stringify(value)}`);
    });
  }
  return `${label}\n${lines.join("\n")}`;
};

const buildStructuredGroups = (
  row: ReviewDialogueViewMessage,
): { groups: StructuredGroup[]; summaryText?: string } => {
  const metadata = row.metadata || {};
  const toolResult =
    metadata.tool_result && typeof metadata.tool_result === "object"
      ? (metadata.tool_result as Record<string, unknown>)
      : null;
  const skillResult =
    metadata.skill_result && typeof metadata.skill_result === "object"
      ? (metadata.skill_result as Record<string, unknown>)
      : null;
  const parsedAnalysis = row.eventType === "expert_analysis" ? tryParseJsonBlock(row.detail) : null;

  if (row.eventType === "expert_skill_call" && skillResult) {
    const recognitionSections = [
      { label: "设计文档", values: normalizeValueList(skillResult.design_doc_titles) },
      { label: "业务目标", values: normalizeSingleValue(skillResult.business_goal) },
      { label: "API 定义", values: normalizeValueList(skillResult.api_definitions) },
      { label: "入参字段", values: normalizeValueList(skillResult.request_fields) },
      { label: "关键出参", values: normalizeValueList(skillResult.response_fields) },
      { label: "表结构", values: normalizeValueList(skillResult.table_definitions) },
      { label: "业务时序", values: normalizeValueList(skillResult.business_sequences) },
      { label: "性能要求", values: normalizeValueList(skillResult.performance_requirements) },
      { label: "安全要求", values: normalizeValueList(skillResult.security_requirements) },
      { label: "原始歧义点", values: normalizeValueList(skillResult.unknown_or_ambiguous_points) },
    ].filter((section) => section.values.length > 0);
    const comparisonSections = [
      {
        label: "一致性状态",
        values:
          typeof skillResult.design_alignment_status === "string"
            ? [getDesignAlignmentLabel(String(skillResult.design_alignment_status))]
            : [],
      },
      { label: "命中的设计点", values: normalizeValueList(skillResult.matched_design_points) },
      { label: "缺失设计点", values: normalizeValueList(skillResult.missing_design_points) },
      { label: "设计冲突", values: normalizeValueList(skillResult.design_conflicts) },
      { label: "待专项验证", values: normalizeValueList(skillResult.uncertain_points) },
    ].filter((section) => section.values.length > 0);
    return {
      summaryText: typeof skillResult.summary === "string" ? skillResult.summary : undefined,
      groups: [
        { title: "详细设计识别结果", sections: recognitionSections },
        { title: "设计一致性对比结果", sections: comparisonSections },
      ].filter((group) => group.sections.length > 0),
    };
  }

  if (row.eventType === "expert_tool_call" && toolResult) {
    const toolName = typeof metadata.tool_name === "string" ? metadata.tool_name : "";
    if (toolName === "design_spec_alignment") {
      const recognitionSections = [
        { label: "设计文档", values: normalizeValueList(toolResult.design_doc_titles) },
        { label: "API 定义", values: normalizeValueList(toolResult.structured_design && typeof toolResult.structured_design === "object" ? (toolResult.structured_design as Record<string, unknown>).api_definitions : []) },
        { label: "入参字段", values: normalizeValueList(toolResult.structured_design && typeof toolResult.structured_design === "object" ? (toolResult.structured_design as Record<string, unknown>).request_fields : []) },
        { label: "关键出参", values: normalizeValueList(toolResult.structured_design && typeof toolResult.structured_design === "object" ? (toolResult.structured_design as Record<string, unknown>).response_fields : []) },
        { label: "表结构", values: normalizeValueList(toolResult.structured_design && typeof toolResult.structured_design === "object" ? (toolResult.structured_design as Record<string, unknown>).table_definitions : []) },
        { label: "业务时序", values: normalizeValueList(toolResult.structured_design && typeof toolResult.structured_design === "object" ? (toolResult.structured_design as Record<string, unknown>).business_sequences : []) },
        { label: "性能要求", values: normalizeValueList(toolResult.structured_design && typeof toolResult.structured_design === "object" ? (toolResult.structured_design as Record<string, unknown>).performance_requirements : []) },
        { label: "安全要求", values: normalizeValueList(toolResult.structured_design && typeof toolResult.structured_design === "object" ? (toolResult.structured_design as Record<string, unknown>).security_requirements : []) },
      ].filter((section) => section.values.length > 0);
      const comparisonSections = [
        {
          label: "一致性状态",
          values:
            typeof toolResult.design_alignment_status === "string"
              ? [getDesignAlignmentLabel(String(toolResult.design_alignment_status))]
              : [],
        },
        { label: "命中的实现点", values: normalizeValueList(toolResult.matched_implementation_points) },
        { label: "缺失实现点", values: normalizeValueList(toolResult.missing_implementation_points) },
        { label: "实现冲突点", values: normalizeValueList(toolResult.conflicting_implementation_points) },
        { label: "待专项验证", values: normalizeValueList(toolResult.uncertain_points) },
      ].filter((section) => section.values.length > 0);
      return {
        summaryText: typeof toolResult.summary === "string" ? toolResult.summary : undefined,
        groups: [
          { title: "详细设计识别结果", sections: recognitionSections },
          { title: "设计一致性对比结果", sections: comparisonSections },
        ].filter((group) => group.sections.length > 0),
      };
    }
    if (toolName === "repo_context_search") {
      return {
        summaryText: typeof toolResult.summary === "string" ? toolResult.summary : undefined,
        groups: [
          {
            title: "源码上下文",
            sections: [
              { label: "上下文文件", values: normalizeValueList(toolResult.context_files) },
              { label: "关联上下文", values: normalizeValueList(toolResult.related_contexts) },
              { label: "定义/引用", values: normalizeValueList(toolResult.symbol_contexts) },
            ].filter((section) => section.values.length > 0),
          },
        ].filter((group) => group.sections.length > 0),
      };
    }
    if (toolName === "knowledge_search") {
      return {
        summaryText: typeof toolResult.summary === "string" ? toolResult.summary : undefined,
        groups: [
          {
            title: "知识命中结果",
            sections: [
              { label: "命中文档", values: normalizeValueList(toolResult.documents) },
              { label: "命中摘要", values: normalizeValueList(toolResult.matches) },
            ].filter((section) => section.values.length > 0),
          },
        ].filter((group) => group.sections.length > 0),
      };
    }
  }

  if (row.eventType === "expert_analysis" && parsedAnalysis) {
      return {
      summaryText: typeof parsedAnalysis.title === "string" ? parsedAnalysis.title : undefined,
      groups: [
        {
          title: "专家结论",
          sections: [
            { label: "结论", values: normalizeSingleValue(parsedAnalysis.claim) },
            { label: "命中规则", values: normalizeValueList(parsedAnalysis.matched_rules) },
            { label: "违反规范", values: normalizeValueList(parsedAnalysis.violated_guidelines) },
            { label: "直接证据", values: normalizeValueList(parsedAnalysis.evidence) },
            { label: "跨文件证据", values: normalizeValueList(parsedAnalysis.cross_file_evidence) },
          ].filter((section) => section.values.length > 0),
        },
        {
          title: "设计一致性对比结果",
          sections: [
            { label: "命中的设计点", values: normalizeValueList(parsedAnalysis.matched_design_points) },
            { label: "缺失设计点", values: normalizeValueList(parsedAnalysis.missing_design_points) },
            { label: "设计冲突", values: normalizeValueList(parsedAnalysis.design_conflicts) },
          ].filter((section) => section.values.length > 0),
        },
        {
          title: "修复与验证建议",
          sections: [
            { label: "修复步骤", values: normalizeValueList(parsedAnalysis.change_steps) },
            { label: "验证计划", values: normalizeSingleValue(parsedAnalysis.verification_plan) },
          ].filter((section) => section.values.length > 0),
        },
      ].filter((group) => group.sections.length > 0),
    };
  }

  return { groups: [] };
};

const StructuredMessageCard: React.FC<{ row: ReviewDialogueViewMessage }> = ({ row }) => {
  const { groups, summaryText } = useMemo(() => buildStructuredGroups(row), [row]);
  if (!groups.length) return null;
  return (
    <div className="dialogue-structured-card">
      {summaryText ? <Paragraph className="dialogue-structured-summary">{summaryText}</Paragraph> : null}
      <div className="dialogue-structured-groups">
        {groups.map((group) => (
          <div key={`${row.id}-${group.title}`} className="dialogue-structured-group">
            <div className="dialogue-structured-group-title">{group.title}</div>
            <div className="dialogue-structured-grid">
              {group.sections.map((section) => (
                <div key={`${row.id}-${group.title}-${section.label}`} className="dialogue-structured-section">
                  <Text className="dialogue-structured-label">{section.label}</Text>
                  <div className="dialogue-structured-values">
                    {section.values.map((value, index) => (
                      <div key={`${row.id}-${group.title}-${section.label}-${index}`} className="dialogue-structured-item">
                        {value}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

const buildLiveWaitingRow = (
  review?: ReviewSummary | null,
  events?: ReviewEvent[],
): ReviewDialogueViewMessage | null => {
  // 真实 LLM 首条消息出来前，先展示一个系统占位气泡，
  // 避免用户误以为审核过程页“卡住了”。
  if (!review || !["pending", "running"].includes(review.status)) return null;
  const latestEvent = events && events.length > 0 ? events[events.length - 1] : null;
  const phase = String(review.phase || latestEvent?.phase || "intake");
  const createdAt = latestEvent?.created_at || review.updated_at || review.created_at || new Date().toISOString();
  let summary = "主Agent 正在读取本次变更并拆解专家任务";
  let detail =
    "系统已启动实时审核，正在拉取 diff、识别关键文件，并为第一位专家生成带文件和行号的审查指令。";
  if (phase === "coordination") {
    summary = "主Agent 正在整理首轮派工";
    detail = "主Agent 正在根据改动文件、风险提示和专家职责生成首批派工消息，首条对话会在模型返回后立即显示。";
  } else if (phase === "expert_review") {
    summary = "首位专家已进入审查，正在等待第一条分析回复";
    detail = "系统已经完成派工并收到专家接单，当前正在等待首位专家返回结构化分析结果。";
  } else if (phase === "queued") {
    summary = "审核任务已进入执行队列，准备启动主Agent";
    detail = "系统正在初始化实时审核上下文，马上会进入主Agent 拆解任务和派工阶段。";
  }
  return {
    id: "system-live-waiting",
    timeText: new Date(createdAt).toLocaleString("zh-CN"),
    agentName: "system",
    side: "system",
    isMainAgent: false,
    messageKind: "status",
    categories: ["status"],
    phase,
    eventType: "system_waiting",
    status: "streaming",
    summary,
    detail,
    metadata: {
      phase,
      mode: "pending",
    },
    headerNote: "实时运行中",
  };
};

const ReviewDialogueStream: React.FC<Props> = ({ messages, review, events = [] }) => {
  // 过程页本质上是 replay/messages 的可视化回放器。
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});
  const [categoryFilter, setCategoryFilter] = useState<"all" | "command" | "chat" | "tool" | "skill" | "status">("all");
  const [expertFilter, setExpertFilter] = useState<string>("all");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const rows = useMemo(
    () =>
      messages
        .slice()
        .sort((left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime())
        .map(mapMessage),
    [messages],
  );
  const liveWaitingRow = useMemo(() => buildLiveWaitingRow(review, events), [events, review]);
  const displayRows = rows.length === 0 && liveWaitingRow ? [liveWaitingRow] : rows;
  const expertOptions = useMemo(() => {
    const map = new Map<string, string>();
    displayRows.forEach((row) => {
      if (!map.has(row.agentName)) {
        map.set(
          row.agentName,
          row.agentName === "main_agent" ? "主Agent" : row.agentName === "judge" ? "Judge" : row.agentName,
        );
      }
    });
    return [
      { label: "全部角色", value: "all" },
      { label: "系统消息", value: "system" },
      ...Array.from(map.entries()).map(([value, label]) => ({ label, value })),
    ];
  }, [displayRows]);
  const filteredRows = useMemo(
    () =>
      displayRows.filter((row) => {
        const categoryMatched = categoryFilter === "all" || row.categories.includes(categoryFilter);
        const expertMatched =
          expertFilter === "all"
            ? true
            : expertFilter === "system"
              ? row.side === "system"
              : row.agentName === expertFilter;
        return categoryMatched && expertMatched;
      }),
    [categoryFilter, displayRows, expertFilter],
  );

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }, [filteredRows.length]);

  if (displayRows.length === 0) {
    return <Empty description="暂无专家对话流。" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }

  return (
    <div className="dialogue-stream-shell">
      <div className="dialogue-toolbar">
        <Space size={12} wrap className="dialogue-toolbar-group">
          <Segmented
            value={categoryFilter}
            onChange={(value) => setCategoryFilter(value as typeof categoryFilter)}
            options={[
              { label: "全部", value: "all" },
              { label: "命令", value: "command" },
              { label: "对话", value: "chat" },
              { label: "工具调用", value: "tool" },
              { label: "技能", value: "skill" },
              { label: "状态", value: "status" },
            ]}
          />
          <Select
            value={expertFilter}
            onChange={setExpertFilter}
            options={expertOptions}
            className="dialogue-expert-filter"
            popupMatchSelectWidth={false}
          />
        </Space>
      </div>
      <div ref={scrollRef} className="dialogue-stream dialogue-stream-scroll discord-thread">
        {filteredRows.length === 0 ? (
          <Empty description="当前筛选条件下暂无对话记录。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : null}
        {filteredRows.map((row) => {
        const compact = buildCompactDetail(row.detail);
        const isExpanded = Boolean(expandedIds[row.id]);
        const filePath = typeof row.metadata.file_path === "string" ? row.metadata.file_path : "";
        const lineStart = typeof row.metadata.line_start === "number" ? row.metadata.line_start : 0;
        const targetExpertId = typeof row.metadata.target_expert_id === "string" ? row.metadata.target_expert_id : "";
        const targetExpertName = typeof row.metadata.target_expert_name === "string" ? row.metadata.target_expert_name : "";
        const replyToExpertId = typeof row.metadata.reply_to_expert_id === "string" ? row.metadata.reply_to_expert_id : "";
        const toolName = typeof row.metadata.tool_name === "string" ? row.metadata.tool_name : "";
        const skillName =
          typeof row.metadata.tool_name === "string"
            ? row.metadata.tool_name
            : typeof row.metadata.skill_name === "string"
              ? row.metadata.skill_name
              : "";
        const provider = typeof row.metadata.provider === "string" ? row.metadata.provider : "";
        const model = typeof row.metadata.model === "string" ? row.metadata.model : "";
        const mode = typeof row.metadata.mode === "string" ? row.metadata.mode : "";
        const matchedRules = Array.isArray(row.metadata.matched_rules)
          ? row.metadata.matched_rules.map((item) => String(item)).filter(Boolean)
          : [];
        const violatedGuidelines = Array.isArray(row.metadata.violated_guidelines)
          ? row.metadata.violated_guidelines.map((item) => String(item)).filter(Boolean)
          : [];
        const ruleBasedReasoning =
          typeof row.metadata.rule_based_reasoning === "string" ? row.metadata.rule_based_reasoning : "";
        const activeSkills = Array.isArray(row.metadata.active_skills)
          ? row.metadata.active_skills.map((item) => String(item)).filter(Boolean)
          : [];
        const designAlignmentStatus =
          typeof row.metadata.design_alignment_status === "string" ? row.metadata.design_alignment_status : "";
        const designDocTitles = Array.isArray(row.metadata.design_doc_titles)
          ? row.metadata.design_doc_titles.map((item) => String(item)).filter(Boolean)
          : [];
        const targetHunk =
          row.metadata.target_hunk && typeof row.metadata.target_hunk === "object"
            ? (row.metadata.target_hunk as Record<string, unknown>)
            : null;
        const hunkHeader = typeof targetHunk?.hunk_header === "string" ? targetHunk.hunk_header : "";
        const lineLabel = lineStart ? `L${lineStart}` : "";
        return (
          <div
            key={row.id}
            className={`dialogue-row dialogue-row-${row.messageKind} ${row.side === "agent" ? "dialogue-row-agent" : "dialogue-row-system"} ${
              row.isMainAgent ? "dialogue-row-main-agent" : ""
            }`}
          >
            <Avatar size="small" className={`dialogue-avatar dialogue-avatar-${row.messageKind}`}>
              {row.agentName.slice(0, 1).toUpperCase()}
            </Avatar>
            <div className={`dialogue-message dialogue-status-${row.status}`}>
              <div className="dialogue-meta">
                <Text className="dialogue-username">{row.agentName}</Text>
                {row.isMainAgent ? <Tag className="dialogue-main-badge">主Agent</Tag> : null}
                <Text className="dialogue-time">{row.timeText}</Text>
                <Tag className={`dialogue-kind-tag dialogue-kind-tag-${row.messageKind}`}>
                  {row.messageKind === "command"
                    ? "命令"
                    : row.messageKind === "status"
                      ? "状态"
                      : row.messageKind === "tool"
                        ? "工具调用"
                        : row.messageKind === "skill"
                          ? "技能"
                          : "对话"}
                </Tag>
                {row.headerNote ? <Tag className="dialogue-tag dialogue-tag-focus">{row.headerNote}</Tag> : null}
                <Tag className="dialogue-tag">{row.eventType}</Tag>
                {targetExpertId ? <Tag className="dialogue-tag dialogue-tag-target">{`to ${targetExpertName || targetExpertId}`}</Tag> : null}
                {replyToExpertId ? <Tag className="dialogue-tag dialogue-tag-reply">{`reply ${replyToExpertId}`}</Tag> : null}
                {toolName ? <Tag className="dialogue-tag dialogue-tag-target">{`tool ${toolName}`}</Tag> : null}
                {skillName ? <Tag className="dialogue-tag dialogue-tag-skill">{`skill ${skillName}`}</Tag> : null}
                {filePath ? <Tag className="dialogue-tag">{filePath}</Tag> : null}
                {lineLabel ? <Tag className="dialogue-tag">{lineLabel}</Tag> : null}
                {hunkHeader ? <Tag className="dialogue-tag">{hunkHeader}</Tag> : null}
                {model ? <Tag className="dialogue-tag dialogue-tag-model">{model}</Tag> : null}
                {provider ? <Tag className="dialogue-tag">{provider}</Tag> : null}
                {mode ? (
                  <Tag
                    className={`dialogue-tag ${
                      mode === "fallback"
                        ? "dialogue-tag-fallback"
                        : mode === "pending"
                          ? "dialogue-tag-pending"
                          : "dialogue-tag-live"
                    }`}
                  >
                    {mode}
                  </Tag>
                ) : null}
              </div>
              <Paragraph className="dialogue-summary">{row.summary}</Paragraph>
              {matchedRules.length ? (
                <div className="dialogue-rule-strip">
                  {matchedRules.slice(0, 3).map((rule) => (
                    <Tag key={`${row.id}-matched-${rule}`} color="blue">
                      {rule}
                    </Tag>
                  ))}
                </div>
              ) : null}
              {violatedGuidelines.length ? (
                <div className="dialogue-rule-strip">
                  {violatedGuidelines.slice(0, 3).map((rule) => (
                    <Tag key={`${row.id}-violated-${rule}`} color="volcano">
                      {rule}
                    </Tag>
                  ))}
                </div>
              ) : null}
              {activeSkills.length ? (
                <div className="dialogue-rule-strip">
                  {activeSkills.map((skill) => (
                    <Tag key={`${row.id}-skill-${skill}`} color="geekblue">
                      {`skill ${skill}`}
                    </Tag>
                  ))}
                </div>
              ) : null}
              {activeSkills.length ? (
                <Paragraph className="dialogue-skill-summary">
                  {`本轮已激活 ${activeSkills.length} 个技能，会据此自动展开工具调用并约束专家输出。`}
                </Paragraph>
              ) : null}
              {designAlignmentStatus ? (
                <div className="dialogue-rule-strip">
                  <Tag color="gold">{getDesignAlignmentLabel(designAlignmentStatus)}</Tag>
                  {designDocTitles.slice(0, 2).map((title) => (
                    <Tag key={`${row.id}-design-doc-${title}`} color="cyan">
                      {title}
                    </Tag>
                  ))}
                </div>
              ) : null}
              {ruleBasedReasoning ? <Paragraph className="dialogue-rule-reason">{ruleBasedReasoning}</Paragraph> : null}
              <StructuredMessageCard row={row} />
              <pre className={`dialogue-content dialogue-content-${row.messageKind}`}>
                {isExpanded ? row.detail : compact.text || "暂无更多上下文"}
              </pre>
              {(compact.truncated || row.detail.length > compact.text.length) && (
                <Button
                  type="link"
                  size="small"
                  className="dialogue-expand-btn"
                  style={{ paddingInline: 0, marginTop: 6 }}
                  onClick={() =>
                    setExpandedIds((prev) => ({
                      ...prev,
                      [row.id]: !prev[row.id],
                    }))
                  }
                >
                  {isExpanded ? "收起详情" : "展开详情"}
                </Button>
              )}
            </div>
          </div>
        );
        })}
      </div>
    </div>
  );
};

export default ReviewDialogueStream;
