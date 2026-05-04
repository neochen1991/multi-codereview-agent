const REVIEW_TEXT_REPLACEMENTS: Array<[RegExp, string]> = [
  [/仓库评论预算/g, "仓库提交上限"],
  [/评论预算/g, "问题提交上限"],
  [/评论限额/g, "提交上限"],
  [/降级保留/g, "保留观察"],
  [/被降级/g, "保留观察"],
  [/阈值过滤/g, "条件筛选"],
  [/质量过滤/g, "质量筛选"],
  [/过滤与降噪/g, "筛选与降噪"],
  [/压制低风险提示类问题/g, "低风险提示暂不提交"],
  [/Issue 过滤治理/g, "问题升级治理"],
  [/正式议题/g, "正式问题"],
  [/待人工议题/g, "待人工确认"],
  [/议题/g, "问题"],
  [/人工裁决/g, "人工确认"],
  [/Judge/g, "结果复核"],
  [/主Agent/g, "审核调度"],
  [/主 Agent/g, "审核调度"],
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
