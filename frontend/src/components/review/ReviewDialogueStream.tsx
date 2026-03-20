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

type BoundDocumentEntry = {
  title: string;
  sourceFilename: string;
  docType: string;
  indexedOutline: string[];
  matchedSections: string[];
  matchedTerms: string[];
  matchedSignals: string[];
};

type IssueFilterDecisionEntry = {
  topic: string;
  ruleCode: string;
  ruleLabel: string;
  reason: string;
  severity: string;
  findingTitles: string[];
  expertIds: string[];
};

type RuleScreeningEntry = {
  ruleId: string;
  title: string;
  priority: string;
  decision: string;
  reason: string;
  matchedTerms: string[];
};

type RuleScreeningBatchEntry = {
  ruleId: string;
  title: string;
  priority: string;
  decision: string;
  reason: string;
  matchedTerms: string[];
  matchedSignals: string[];
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

const INITIAL_VISIBLE_DIALOGUE_ROWS = 160;
const VISIBLE_DIALOGUE_ROWS_STEP = 160;

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

const limitValueList = (values: string[], maxItems = 12): string[] => {
  if (values.length <= maxItems) return values;
  return [...values.slice(0, maxItems), `... 另 ${values.length - maxItems} 项`];
};

const normalizeSingleValue = (value: unknown): string[] => {
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
};

const normalizeKeywordSourceEntries = (value: unknown): string[] => {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (!item || typeof item !== "object") return "";
      const payload = item as Record<string, unknown>;
      const keyword = typeof payload.keyword === "string" ? payload.keyword.trim() : "";
      const sourceLabel = typeof payload.source_label === "string" ? payload.source_label.trim() : "";
      const source = typeof payload.source === "string" ? payload.source.trim() : "";
      if (keyword && sourceLabel) return `${keyword} · ${sourceLabel}`;
      if (keyword && source) return `${keyword} · ${source}`;
      return keyword;
    })
    .filter(Boolean);
};

const formatContextEntry = (value: unknown): string => {
  if (typeof value === "string") return value.trim();
  if (!value || typeof value !== "object") return "";
  const payload = value as Record<string, unknown>;
  const path = typeof payload.path === "string" ? payload.path.trim() : "";
  const lineStart = typeof payload.line_start === "number" ? payload.line_start : 0;
  const snippet = typeof payload.snippet === "string" ? payload.snippet.trim() : "";
  const firstSnippetLine = snippet.split("\n").map((line) => line.trim()).filter(Boolean)[0] || "";
  const title = path ? `${path}${lineStart ? `:${lineStart}` : ""}` : "";
  if (title && firstSnippetLine) return `${title} · ${firstSnippetLine}`;
  return title || firstSnippetLine;
};

const normalizeContextValueList = (value: unknown): string[] => {
  if (!Array.isArray(value)) return [];
  return value.map(formatContextEntry).filter(Boolean);
};

const normalizeBoundDocumentEntries = (value: unknown): BoundDocumentEntry[] => {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const payload = item as Record<string, unknown>;
      const matchedSections = Array.isArray(payload.matched_sections)
        ? payload.matched_sections
            .map((section) => {
              if (!section || typeof section !== "object") return "";
              const sectionPayload = section as Record<string, unknown>;
              const path = String(sectionPayload.path || sectionPayload.title || "").trim();
              const summary = String(sectionPayload.summary || "").trim();
              if (path && summary) return `${path} · ${summary}`;
              return path || summary;
            })
            .filter(Boolean)
        : [];
      const matchedTerms = Array.isArray(payload.matched_sections)
        ? payload.matched_sections
            .flatMap((section) => {
              if (!section || typeof section !== "object") return [];
              return normalizeValueList((section as Record<string, unknown>).matched_terms);
            })
            .filter(Boolean)
        : [];
      const matchedSignals = Array.isArray(payload.matched_sections)
        ? payload.matched_sections
            .flatMap((section) => {
              if (!section || typeof section !== "object") return [];
              return normalizeValueList((section as Record<string, unknown>).matched_signals);
            })
            .filter(Boolean)
        : [];
      return {
        title: String(payload.title || "").trim(),
        sourceFilename: String(payload.source_filename || "").trim(),
        docType: String(payload.doc_type || "").trim(),
        indexedOutline: normalizeValueList(payload.indexed_outline),
        matchedSections,
        matchedTerms,
        matchedSignals,
      };
    })
    .filter((item): item is BoundDocumentEntry => Boolean(item && item.title));
};

const buildBoundDocumentGroup = (value: unknown): StructuredGroup | null => {
  const documents = normalizeBoundDocumentEntries(value);
  if (!documents.length) return null;
  const documentRows = documents.map((item) =>
    [item.title, item.sourceFilename ? `(${item.sourceFilename})` : "", item.docType ? `· ${item.docType}` : ""]
      .join(" ")
      .trim(),
  );
  const matchedSectionRows = documents.flatMap((item) =>
    item.matchedSections.map((section) => `${item.title} · ${section}`),
  );
  const outlineRows = documents.flatMap((item) =>
    item.indexedOutline.slice(0, 6).map((outline) => `${item.title} · ${outline}`),
  );
  const matchedTermRows = documents.flatMap((item) =>
    item.matchedTerms.slice(0, 8).map((term) => `${item.title} · ${term}`),
  );
  const matchedSignalRows = documents.flatMap((item) =>
    item.matchedSignals.slice(0, 8).map((signal) => `${item.title} · ${signal}`),
  );
  const sections = [
    { label: "绑定文档", values: documentRows },
    { label: "命中章节", values: matchedSectionRows },
    { label: "命中关键词", values: matchedTermRows },
    { label: "命中来源", values: matchedSignalRows },
    { label: "章节索引", values: matchedSectionRows.length ? [] : outlineRows },
  ].filter((section) => section.values.length > 0);
  if (!sections.length) return null;
  return { title: "知识文档命中", sections };
};

const buildKnowledgeContextGroup = (value: unknown): StructuredGroup | null => {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  const focusFile = typeof payload.focus_file === "string" ? payload.focus_file.trim() : "";
  const focusLine = typeof payload.focus_line === "number" && payload.focus_line > 0 ? [`L${payload.focus_line}`] : [];
  const sections = [
    { label: "聚焦文件", values: focusFile ? [focusFile] : [] },
    { label: "聚焦行", values: focusLine },
    { label: "变更文件", values: limitValueList(normalizeValueList(payload.changed_files), 10) },
    { label: "召回关键词", values: limitValueList(normalizeValueList(payload.query_terms), 10) },
    { label: "知识源绑定", values: limitValueList(normalizeValueList(payload.knowledge_sources), 10) },
  ].filter((section) => section.values.length > 0);
  if (!sections.length) return null;
  return { title: "知识检索上下文", sections };
};

const normalizeIssueFilterDecisionEntries = (value: unknown): IssueFilterDecisionEntry[] => {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const payload = item as Record<string, unknown>;
      return {
        topic: String(payload.topic || "").trim(),
        ruleCode: String(payload.rule_code || "").trim(),
        ruleLabel: String(payload.rule_label || "").trim(),
        reason: String(payload.reason || "").trim(),
        severity: String(payload.severity || "").trim(),
        findingTitles: normalizeValueList(payload.finding_titles),
        expertIds: normalizeValueList(payload.expert_ids),
      };
    })
    .filter((item): item is IssueFilterDecisionEntry => Boolean(item && item.reason));
};

const buildIssueFilterGroup = (value: unknown): StructuredGroup | null => {
  const decisions = normalizeIssueFilterDecisionEntries(value);
  if (!decisions.length) return null;
  return {
    title: "Issue 治理结果",
    sections: [
      { label: "治理规则", values: decisions.map((item) => `${item.ruleLabel}${item.ruleCode ? ` (${item.ruleCode})` : ""}`) },
      { label: "保留原因", values: decisions.map((item) => item.reason) },
      { label: "保留的 finding", values: decisions.flatMap((item) => item.findingTitles) },
      { label: "涉及专家", values: decisions.flatMap((item) => item.expertIds) },
      {
        label: "定位主题",
        values: decisions
          .map((item) => {
            const prefix = item.severity ? `[${item.severity}] ` : "";
            return `${prefix}${item.topic}`;
          })
          .filter(Boolean),
      },
    ].filter((section) => section.values.length > 0),
  };
};

const normalizeRuleScreeningEntries = (value: unknown): RuleScreeningEntry[] => {
  if (!value || typeof value !== "object") return [];
  const payload = value as Record<string, unknown>;
  const matched = Array.isArray(payload.matched_rules_for_llm) ? payload.matched_rules_for_llm : [];
  return matched
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const entry = item as Record<string, unknown>;
      return {
        ruleId: String(entry.rule_id || "").trim(),
        title: String(entry.title || "").trim(),
        priority: String(entry.priority || "").trim(),
        decision: String(entry.decision || "").trim(),
        reason: String(entry.reason || "").trim(),
        matchedTerms: normalizeValueList(entry.matched_terms),
      };
    })
    .filter((item): item is RuleScreeningEntry => Boolean(item && (item.ruleId || item.title)));
};

const buildRuleScreeningGroup = (value: unknown): StructuredGroup | null => {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  const totalRules = typeof payload.total_rules === "number" ? payload.total_rules : 0;
  const enabledRules = typeof payload.enabled_rules === "number" ? payload.enabled_rules : 0;
  const mustReviewCount = typeof payload.must_review_count === "number" ? payload.must_review_count : 0;
  const possibleHitCount = typeof payload.possible_hit_count === "number" ? payload.possible_hit_count : 0;
  const matchedRuleCount = typeof payload.matched_rule_count === "number" ? payload.matched_rule_count : 0;
  const screeningMode = typeof payload.screening_mode === "string" ? payload.screening_mode.trim() : "";
  const fallbackUsed = Boolean(payload.screening_fallback_used);
  const rules = normalizeRuleScreeningEntries(value);
  if (totalRules <= 0 && !rules.length) return null;
  return {
    title: "规则覆盖报告",
    sections: [
      {
        label: "覆盖统计",
        values: [
          `总规则 ${totalRules}`,
          `启用规则 ${enabledRules || totalRules}`,
          `强命中 ${mustReviewCount}`,
          `候选 ${possibleHitCount}`,
          `带入审查 ${matchedRuleCount}`,
          screeningMode ? `筛选模式 ${screeningMode}` : "",
          fallbackUsed ? "已回退到启发式" : "",
        ].filter(Boolean),
      },
      {
        label: "命中规则",
        values: rules.map((item) => {
          const priority = item.priority ? `[${item.priority}] ` : "";
          const title = item.title || item.ruleId;
          const decision = item.decision ? ` · ${item.decision}` : "";
          return `${priority}${title}${decision}`;
        }),
      },
      {
        label: "命中原因",
        values: rules.map((item) => {
          const title = item.title || item.ruleId;
          const reason = item.reason || "命中规则信号";
          return `${title} · ${reason}`;
        }),
      },
      {
        label: "命中关键词",
        values: rules.flatMap((item) =>
          item.matchedTerms.map((term) => `${item.title || item.ruleId} · ${term}`),
        ),
      },
    ].filter((section) => section.values.length > 0),
  };
};

const normalizeRuleScreeningBatchEntries = (value: unknown): RuleScreeningBatchEntry[] => {
  if (!value || typeof value !== "object") return [];
  const payload = value as Record<string, unknown>;
  const decisions = Array.isArray(payload.decisions) ? payload.decisions : [];
  return decisions
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const entry = item as Record<string, unknown>;
      return {
        ruleId: String(entry.rule_id || "").trim(),
        title: String(entry.title || "").trim(),
        priority: String(entry.priority || "").trim(),
        decision: String(entry.decision || "").trim(),
        reason: String(entry.reason || "").trim(),
        matchedTerms: normalizeValueList(entry.matched_terms),
        matchedSignals: normalizeValueList(entry.matched_signals),
      };
    })
    .filter((item): item is RuleScreeningBatchEntry => Boolean(item && (item.ruleId || item.title)));
};

const buildRuleScreeningBatchGroup = (value: unknown): StructuredGroup | null => {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  const batchIndex = typeof payload.batch_index === "number" ? payload.batch_index : 0;
  const batchCount = typeof payload.batch_count === "number" ? payload.batch_count : 0;
  const screeningMode = typeof payload.screening_mode === "string" ? payload.screening_mode.trim() : "";
  const inputRuleCount = typeof payload.input_rule_count === "number" ? payload.input_rule_count : 0;
  const mustReviewCount = typeof payload.must_review_count === "number" ? payload.must_review_count : 0;
  const possibleHitCount = typeof payload.possible_hit_count === "number" ? payload.possible_hit_count : 0;
  const noHitCount = typeof payload.no_hit_count === "number" ? payload.no_hit_count : 0;
  const inputRules = Array.isArray(payload.input_rules) ? payload.input_rules : [];
  const rules = normalizeRuleScreeningBatchEntries(value);
  if (!batchIndex && !inputRuleCount && !rules.length) return null;
  return {
    title: "规则筛选批次",
    sections: [
      {
        label: "批次统计",
        values: [
          batchIndex && batchCount ? `第 ${batchIndex}/${batchCount} 批` : "",
          screeningMode ? `筛选模式 ${screeningMode}` : "",
          inputRuleCount ? `输入规则 ${inputRuleCount}` : "",
          `强命中 ${mustReviewCount}`,
          `候选 ${possibleHitCount}`,
          `跳过 ${noHitCount}`,
        ].filter(Boolean),
      },
      {
        label: "输入规则",
        values: inputRules
          .map((item) => {
            if (!item || typeof item !== "object") return "";
            const entry = item as Record<string, unknown>;
            const title = String(entry.title || entry.rule_id || "").trim();
            const priority = String(entry.priority || "").trim();
            return `${priority ? `[${priority}] ` : ""}${title}`;
          })
          .filter(Boolean),
      },
      {
        label: "筛选结果",
        values: rules.map((item) => {
          const title = item.title || item.ruleId;
          const priority = item.priority ? `[${item.priority}] ` : "";
          const decision = item.decision ? ` · ${item.decision}` : "";
          return `${priority}${title}${decision}`;
        }),
      },
      {
        label: "筛选原因",
        values: rules.map((item) => `${item.title || item.ruleId} · ${item.reason || "命中本批规则信号"}`),
      },
      {
        label: "命中关键词",
        values: rules.flatMap((item) =>
          item.matchedTerms.map((term) => `${item.title || item.ruleId} · ${term}`),
        ),
      },
      {
        label: "命中信号",
        values: rules.flatMap((item) =>
          item.matchedSignals.map((signal) => `${item.title || item.ruleId} · ${signal}`),
        ),
      },
    ].filter((section) => section.values.length > 0),
  };
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

const hasDesignEvidencePayload = (payload: Record<string, unknown>): boolean =>
  normalizeValueList(payload.design_doc_titles).length > 0 ||
  normalizeValueList(payload.matched_design_points).length > 0 ||
  normalizeValueList(payload.missing_design_points).length > 0 ||
  normalizeValueList(payload.extra_implementation_points).length > 0 ||
  normalizeValueList(payload.design_conflicts).length > 0;

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
  if (
    eventType === "judge_summary" ||
    eventType === "main_agent_summary" ||
    eventType === "main_agent_intake" ||
    eventType === "main_agent_expert_selection" ||
    eventType === "main_agent_routing_preparing" ||
    eventType === "main_agent_routing_ready" ||
    eventType === "main_agent_expert_execution_completed" ||
    eventType === "issue_filter_applied" ||
    eventType === "expert_rule_screening_batch"
  ) messageKind = "status";
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
  } else if (eventType === "expert_rule_screening_batch") {
    const batch = metadata.rule_screening_batch && typeof metadata.rule_screening_batch === "object"
      ? (metadata.rule_screening_batch as Record<string, unknown>)
      : null;
    const batchIndex = typeof batch?.batch_index === "number" ? batch.batch_index : 0;
    const batchCount = typeof batch?.batch_count === "number" ? batch.batch_count : 0;
    summaryParts.push(
      `${message.expert_id} 已完成规则筛选${batchIndex && batchCount ? `第 ${batchIndex}/${batchCount} 批` : ""}`,
    );
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
  } else if (eventType === "main_agent_intake") {
    summaryParts.push("主Agent 已接收并整理本次审核输入");
  } else if (eventType === "main_agent_expert_selection") {
    summaryParts.push("主Agent 已基于 MR 信息判定本次参与审核的专家");
  } else if (eventType === "main_agent_routing_preparing") {
    summaryParts.push("主Agent 正在构建派工上下文");
  } else if (eventType === "main_agent_routing_ready") {
    summaryParts.push("主Agent 已完成派工规划，准备下发专家任务");
  } else if (eventType === "main_agent_expert_execution_completed") {
    summaryParts.push("专家审查执行阶段已完成");
  } else if (eventType === "issue_filter_applied") {
    summaryParts.push("主Agent 已按治理规则筛出仅保留为 finding 的提示性问题");
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
  const boundDocumentGroup = buildBoundDocumentGroup(metadata.bound_documents);
  const knowledgeContextGroup = buildKnowledgeContextGroup(metadata.knowledge_context);
  const ruleScreeningGroup = buildRuleScreeningGroup(metadata.rule_screening);
  const ruleScreeningBatchGroup = buildRuleScreeningBatchGroup(metadata.rule_screening_batch);
  const issueFilterGroup = buildIssueFilterGroup(metadata.issue_filter_decisions);

  if (row.eventType === "expert_ack" && (boundDocumentGroup || knowledgeContextGroup || ruleScreeningGroup)) {
    return {
      summaryText: row.summary,
      groups: [
        ...(ruleScreeningGroup ? [ruleScreeningGroup] : []),
        ...(boundDocumentGroup ? [boundDocumentGroup] : []),
        ...(knowledgeContextGroup ? [knowledgeContextGroup] : []),
      ],
    };
  }

  if (row.eventType === "expert_rule_screening_batch" && ruleScreeningBatchGroup) {
    return {
      summaryText: row.summary,
      groups: [ruleScreeningBatchGroup],
    };
  }

  if (row.eventType === "expert_skill_call" && skillResult) {
    if (!hasDesignEvidencePayload(skillResult)) {
      return { groups: [] };
    }
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

  if (row.eventType === "main_agent_intake") {
    return {
      summaryText: row.summary,
      groups: [
        {
          title: "MR 基本信息",
          sections: [
            { label: "标题", values: normalizeSingleValue(metadata.title) },
            { label: "链接", values: normalizeSingleValue(metadata.review_url) },
            { label: "平台", values: normalizeSingleValue(metadata.platform_kind) },
            { label: "源分支", values: normalizeSingleValue(metadata.source_ref) },
            { label: "目标分支", values: normalizeSingleValue(metadata.target_ref) },
            { label: "对比模式", values: normalizeSingleValue(metadata.compare_mode) },
          ].filter((section) => section.values.length > 0),
        },
        {
          title: "变更文件",
          sections: [
            { label: "全部变更文件", values: limitValueList(normalizeValueList(metadata.changed_files), 16) },
            { label: "业务变更文件", values: limitValueList(normalizeValueList(metadata.business_changed_files), 16) },
          ].filter((section) => section.values.length > 0),
        },
      ].filter((group) => group.sections.length > 0),
    };
  }

  if (row.eventType === "main_agent_expert_selection") {
    const selectedExperts = Array.isArray(metadata.selected_experts) ? metadata.selected_experts : [];
    const skippedExperts = Array.isArray(metadata.skipped_experts) ? metadata.skipped_experts : [];
    const formatExpertRows = (value: unknown): string[] => {
      if (!Array.isArray(value)) return [];
      return value
        .map((item) => {
          if (!item || typeof item !== "object") return "";
          const payload = item as Record<string, unknown>;
          const expertName = String(payload.expert_name || payload.expert_id || "").trim();
          const reason = String(payload.reason || "").trim();
          if (expertName && reason) return `${expertName} · ${reason}`;
          return expertName || reason;
        })
        .filter(Boolean);
    };
    return {
      summaryText: row.summary,
      groups: [
        {
          title: "阶段耗时",
          sections: [
            {
              label: "专家判定耗时",
              values:
                typeof metadata.selection_elapsed_ms === "number"
                  ? [`${metadata.selection_elapsed_ms} ms`]
                  : [],
            },
          ].filter((section) => section.values.length > 0),
        },
        {
          title: "参与审核的专家",
          sections: [
            { label: "大模型选中", values: formatExpertRows(selectedExperts) },
          ].filter((section) => section.values.length > 0),
        },
        {
          title: "未参与本轮的专家",
          sections: [
            { label: "跳过原因", values: formatExpertRows(skippedExperts) },
          ].filter((section) => section.values.length > 0),
        },
      ].filter((group) => group.sections.length > 0),
    };
  }

  if (row.eventType === "main_agent_routing_preparing") {
    return {
      summaryText: row.summary,
      groups: [
        {
          title: "派工准备",
          sections: [
            { label: "分析模式", values: normalizeSingleValue(metadata.analysis_mode) },
            { label: "已选专家", values: normalizeValueList(metadata.selected_expert_ids) },
            {
              label: "变更文件数",
              values:
                typeof metadata.changed_file_count === "number"
                  ? [String(metadata.changed_file_count)]
                  : [],
            },
          ].filter((section) => section.values.length > 0),
        },
      ],
    };
  }

  if (row.eventType === "main_agent_routing_ready") {
    return {
      summaryText: row.summary,
      groups: [
        {
          title: "派工规划完成",
          sections: [
            {
              label: "派工规划耗时",
              values:
                typeof metadata.routing_elapsed_ms === "number"
                  ? [`${metadata.routing_elapsed_ms} ms`]
                  : [],
            },
            { label: "分析模式", values: normalizeSingleValue(metadata.analysis_mode) },
            { label: "已选专家", values: normalizeValueList(metadata.selected_expert_ids) },
          ].filter((section) => section.values.length > 0),
        },
      ],
    };
  }

  if (row.eventType === "main_agent_expert_execution_completed") {
    return {
      summaryText: row.summary,
      groups: [
        {
          title: "专家执行耗时",
          sections: [
            {
              label: "专家执行耗时",
              values:
                typeof metadata.expert_execution_elapsed_ms === "number"
                  ? [`${metadata.expert_execution_elapsed_ms} ms`]
                  : [],
            },
            {
              label: "执行专家数",
              values:
                typeof metadata.expert_job_count === "number"
                  ? [String(metadata.expert_job_count)]
                  : [],
            },
            { label: "已选专家", values: normalizeValueList(metadata.selected_expert_ids) },
          ].filter((section) => section.values.length > 0),
        },
      ],
    };
  }

  if (row.eventType === "issue_filter_applied" && issueFilterGroup) {
    return {
      summaryText: row.summary,
      groups: [issueFilterGroup],
    };
  }

  if (row.eventType === "expert_tool_call" && toolResult) {
    const toolName = typeof metadata.tool_name === "string" ? metadata.tool_name : "";
    if (toolName === "design_spec_alignment") {
      if (!hasDesignEvidencePayload(toolResult)) {
        return { groups: [] };
      }
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
              { label: "检索关键词", values: normalizeValueList(toolResult.search_keywords) },
              { label: "关键词来源", values: normalizeKeywordSourceEntries(toolResult.search_keyword_sources) },
              { label: "搜索命令", values: normalizeValueList(toolResult.search_commands) },
              { label: "上下文文件", values: normalizeValueList(toolResult.context_files) },
              { label: "关联上下文", values: normalizeContextValueList(toolResult.related_contexts) },
              { label: "定义命中", values: normalizeValueList(toolResult.definition_hits) },
              { label: "引用命中", values: normalizeValueList(toolResult.reference_hits) },
              { label: "判定逻辑", values: normalizeSingleValue(toolResult.symbol_match_strategy) },
              { label: "结果说明", values: normalizeSingleValue(toolResult.symbol_match_explanation) },
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
        ...(ruleScreeningGroup ? [ruleScreeningGroup] : []),
        ...(boundDocumentGroup ? [boundDocumentGroup] : []),
        ...(knowledgeContextGroup ? [knowledgeContextGroup] : []),
      ].filter((group) => group.sections.length > 0),
    };
  }

  if (row.eventType === "debate_message" && (boundDocumentGroup || knowledgeContextGroup || ruleScreeningGroup)) {
    return {
      summaryText: row.summary,
      groups: [
        ...(ruleScreeningGroup ? [ruleScreeningGroup] : []),
        ...(boundDocumentGroup ? [boundDocumentGroup] : []),
        ...(knowledgeContextGroup ? [knowledgeContextGroup] : []),
      ],
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
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE_DIALOGUE_ROWS);
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
  const visibleRows = useMemo(
    () => filteredRows.slice(-visibleCount),
    [filteredRows, visibleCount],
  );
  const hiddenRowCount = Math.max(filteredRows.length - visibleRows.length, 0);

  useEffect(() => {
    setVisibleCount(INITIAL_VISIBLE_DIALOGUE_ROWS);
  }, [categoryFilter, expertFilter, messages.length]);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTo({
      top: node.scrollHeight,
      behavior: visibleRows.length > 120 ? "auto" : "smooth",
    });
  }, [visibleRows.length]);

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
          {hiddenRowCount > 0 ? (
            <Button type="link" size="small" onClick={() => setVisibleCount((current) => current + VISIBLE_DIALOGUE_ROWS_STEP)}>
              {`加载更早消息（剩余 ${hiddenRowCount} 条）`}
            </Button>
          ) : null}
        </Space>
      </div>
      <div ref={scrollRef} className="dialogue-stream dialogue-stream-scroll discord-thread">
        {filteredRows.length === 0 ? (
          <Empty description="当前筛选条件下暂无对话记录。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : null}
        {visibleRows.map((row) => {
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
        const ruleScreening =
          row.metadata.rule_screening && typeof row.metadata.rule_screening === "object"
            ? (row.metadata.rule_screening as Record<string, unknown>)
            : null;
        const totalRuleCount =
          ruleScreening && typeof ruleScreening.total_rules === "number" ? ruleScreening.total_rules : 0;
        const matchedRuleCount =
          ruleScreening && typeof ruleScreening.matched_rule_count === "number" ? ruleScreening.matched_rule_count : 0;
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
                {filePath ? <Tag className="dialogue-tag dialogue-tag-path">{filePath}</Tag> : null}
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
              {totalRuleCount > 0 ? (
                <div className="dialogue-rule-strip">
                  <Tag color="purple">{`规则 ${totalRuleCount}`}</Tag>
                  <Tag color={matchedRuleCount > 0 ? "magenta" : "default"}>{`命中 ${matchedRuleCount}`}</Tag>
                </div>
              ) : null}
              {activeSkills.length ? (
                <Paragraph className="dialogue-skill-summary">
                  {`本轮已激活 ${activeSkills.length} 个技能，会据此自动展开工具调用并约束专家输出。`}
                </Paragraph>
              ) : null}
              {designAlignmentStatus && designDocTitles.length > 0 ? (
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
