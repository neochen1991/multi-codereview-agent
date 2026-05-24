const REVIEW_TEXT_REPLACEMENTS: Array<[RegExp, string]> = [
  [/\bneeds_human_review\b/g, "待人工确认"],
  [/\bneeds_human\b/g, "待人工确认"],
  [/\bneeds_verification\b/g, "待补充核验"],
  [/\bjudge_accepted\b/g, "复核通过"],
  [/\bhuman_approved\b/g, "人工确认通过"],
  [/\bhuman_rejected\b/g, "人工驳回"],
  [/\bauto_confirmed_direct_evidence\b/g, "证据充分，自动确认"],
  [/\bconsistency_validation_failed\b/g, "内容一致性待确认"],
  [/\bnot_required\b/g, "无需人工确认"],
  [/\btool_verified\b/g, "工具已核验"],
  [/\bnot_verified\b/g, "工具未核验"],
  [/\bverified\b/g, "已核验"],
  [/\bpresent\b/g, "已确认存在"],
  [/\banchored\b/g, "已定位代码"],
  [/\bsignal_matched\b/g, "静态信号命中"],
  [/\btrue_positive\b/g, "倾向真实问题"],
  [/\bfalse_positive\b/g, "倾向误报"],
  [/\bhigh_false_positive_risk\b/g, "误报风险高"],
  [/\bweak\b/g, "证据较弱"],
  [/\babstain\b/g, "暂不判断"],
  [/\brequested\b/g, "等待人工确认"],
  [/\bapproved\b/g, "已确认"],
  [/\brejected\b/g, "已驳回"],
  [/\bpending\b/g, "待处理"],
  [/\bresolved\b/g, "已处理"],
  [/\bopen\b/g, "处理中"],
  [/\bstarted\b/g, "已开始"],
  [/\brunning\b/g, "运行中"],
  [/\bcompleted\b/g, "已完成"],
  [/\bfailed\b/g, "失败"],
  [/\bmissing\b/g, "缺失"],
  [/\bfallback\b/g, "备用流程"],
  [/\bblocker\b/g, "阻断"],
  [/\bcritical\b/g, "严重"],
  [/\bhigh\b/g, "高"],
  [/\bmedium\b/g, "中"],
  [/\blow\b/g, "低"],
  [/仓库评论预算/g, "仓库提交上限"],
  [/评论预算/g, "问题提交上限"],
  [/评论限额/g, "提交上限"],
  [/降级保留/g, "保留观察"],
  [/被降级/g, "保留观察"],
  [/阈值过滤/g, "条件筛选"],
  [/质量过滤/g, "质量筛选"],
  [/过滤与降噪/g, "筛选与降噪"],
  [/同一代码行存在\s*\d+\s*个问题[：:]/g, ""],
  [/同一变更引入\s*\d+\s*个问题[：:]/g, ""],
  [/问题汇总[：:]/g, ""],
  [/修复建议汇总[：:]/g, "修复建议："],
  [/定向辩论预裁决[：:].*?(。|$)/g, ""],
  [/\bmain_agent_command\b/g, "指派检查"],
  [/\bexpert_ack\b/g, "接收任务"],
  [/\bexpert_analysis\b/g, "初步分析"],
  [/\bexpert_rule_screening_batch\b/g, "规则筛选"],
  [/\bexpert_tool_call\b/g, "工具核验"],
  [/\bexpert_skill_call\b/g, "能力调用"],
  [/\bdebate_message\b/g, "复核讨论"],
  [/\bjudge_summary\b/g, "结果复核"],
  [/\bjudge_consistency_validation\b/g, "一致性校验"],
  [/\bmain_agent_summary\b/g, "收敛汇总"],
  [/\bmain_agent_intake\b/g, "输入整理"],
  [/\bmain_agent_expert_selection\b/g, "角色选择"],
  [/\bmain_agent_routing_preparing\b/g, "准备派工"],
  [/\bmain_agent_routing_ready\b/g, "派工就绪"],
  [/\bmain_agent_expert_execution_completed\b/g, "专项检查完成"],
  [/\bissue_filter_applied\b/g, "问题筛选"],
  [/\bimpact_analysis_started\b/g, "开始关联影响分析"],
  [/\bimpact_report_generated\b/g, "关联影响报告生成"],
  [/\bimpact_report_failed\b/g, "关联影响报告失败"],
  [/\bddd_architecture\b/g, "DDD 架构"],
  [/\barchitecture_design\b/g, "通用编码规范"],
  [/\bperformance_reliability\b/g, "性能与可靠性"],
  [/\bdatabase_analysis\b/g, "数据库与查询"],
  [/\bcorrectness_business\b/g, "正确性与业务"],
  [/\bsecurity_compliance\b/g, "安全与合规"],
  [/\bchange_impact_analysis\b/g, "关联影响分析"],
  [/\btest_verification\b/g, "测试验证"],
  [/\bmaintainability_code_health\b/g, "可维护性"],
  [/\bmain_agent_expert_execution_partial_failure\b/g, "部分检查角色执行失败"],
  [/\bbuiltin_verifier\b/g, "内置核验器"],
  [/\bschema_diff\b/g, "结构差异工具"],
  [/压制低风险提示类问题/g, "低风险提示暂不提交"],
  [/Issue 过滤治理/g, "问题升级治理"],
  [/正式议题/g, "正式问题"],
  [/待人工议题/g, "待人工确认"],
  [/议题/g, "问题"],
  [/人工裁决/g, "人工确认"],
  [/Judge/g, "结果复核"],
  [/主Agent/g, "审核调度"],
  [/主 Agent/g, "审核调度"],
  [/\bmain_agent\b/g, "审核调度"],
  [/\bjudge\b/g, "结果复核"],
  [/大模型/g, "系统"],
  [/\bfindings\b/gi, "检视发现"],
  [/\bfinding\b/gi, "检视发现"],
  [/\bissues\b/gi, "问题"],
  [/\bissue\b/gi, "问题"],
];

export const humanizeReviewText = (value?: string | null): string => {
  let text = String(value || "");
  for (const [pattern, replacement] of REVIEW_TEXT_REPLACEMENTS) {
    text = text.replace(pattern, replacement);
  }
  return text;
};

export const humanizeReviewStatus = (value?: string | null): string => humanizeReviewText(value || "-");

export const humanizeSeverity = (value?: string | null): string => humanizeReviewText(String(value || "-").toLowerCase());

const EXPERT_LABELS: Record<string, string> = {
  main_agent: "审核调度",
  judge: "结果复核",
  ddd_architecture: "DDD 架构",
  architecture_design: "通用编码规范",
  performance_reliability: "性能与可靠性",
  database_analysis: "数据库与查询",
  correctness_business: "正确性与业务",
  security_compliance: "安全与合规",
  change_impact_analysis: "关联影响分析",
  test_verification: "测试验证",
  maintainability_code_health: "可维护性",
  java_backend: "Java 后端",
  test_engineer: "测试工程",
  security_reviewer: "安全复核",
  human_reviewer: "人工复核",
};

export const humanizeExpertId = (value?: string | null): string => {
  const text = String(value || "").trim();
  if (!text) return "-";
  if (EXPERT_LABELS[text]) return EXPERT_LABELS[text];
  return humanizeReviewText(text.replace(/_/g, " "));
};

export const humanizeExpertList = (values?: Array<string | null | undefined> | null): string[] =>
  Array.from(new Set((values || []).map((item) => humanizeExpertId(item)).filter((item) => item && item !== "-")));

export const stripReviewSupplementSections = (value?: string | null): string => {
  const text = humanizeReviewText(value || "");
  const splitIndex = ["修复建议：", "复核结论："].reduce((current, marker) => {
    const index = text.indexOf(marker);
    if (index < 0) return current;
    return current < 0 ? index : Math.min(current, index);
  }, -1);
  return (splitIndex >= 0 ? text.slice(0, splitIndex) : text).trim() || "-";
};
