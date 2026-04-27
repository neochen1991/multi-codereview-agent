export const getReviewStatusLabel = (value?: string): string => {
  if (value === "idle") return "未开始";
  if (value === "pending") return "排队中";
  if (value === "running") return "运行中";
  if (value === "waiting_human") return "待人工确认";
  if (value === "completed") return "已完成";
  if (value === "failed") return "执行失败";
  if (value === "closed") return "已关闭";
  return value || "-";
};

export const getReviewStatusColor = (value?: string): string => {
  if (value === "running") return "processing";
  if (value === "completed") return "success";
  if (value === "failed") return "error";
  if (value === "closed") return "warning";
  if (value === "waiting_human") return "error";
  return "default";
};

export const getReviewPhaseLabel = (value?: string): string => {
  if (value === "not_started") return "尚未启动";
  if (value === "queued") return "排队等待";
  if (value === "intake") return "输入整理";
  if (value === "coordination") return "主Agent 编排";
  if (value === "impact_analysis") return "关联影响分析";
  if (value === "expert_review") return "专家审查";
  if (value === "debate") return "议题收敛";
  if (value === "judge") return "Judge 校验";
  if (value === "human_gate") return "人工裁决";
  if (value === "completed") return "审核完成";
  if (value === "failed") return "执行失败";
  return value || "-";
};
