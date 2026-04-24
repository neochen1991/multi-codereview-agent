import React from "react";
import { Card, Empty, Space, Tag, Typography } from "antd";

import type {
  ExpertRoutingSummary,
  ExpertRuleCoverageSummary,
  ExpertSelectionSummary,
  RoutingExpertItem,
} from "@/pages/ReviewWorkbench/helpers";

const { Paragraph, Text } = Typography;

export const WorkbenchPanelFallback: React.FC<{ description?: string }> = ({
  description = "模块加载中...",
}) => (
  <Card className="module-card">
    <Empty description={description} image={Empty.PRESENTED_IMAGE_SIMPLE} />
  </Card>
);

export const RoutingExpertTags: React.FC<{
  title: string;
  items: RoutingExpertItem[];
  color: string;
}> = ({ title, items, color }) => {
  if (items.length === 0) return null;
  return (
    <div className="routing-group">
      <Text className="routing-group-title">{title}</Text>
      <Space size={[8, 8]} wrap>
        {items.map((item) => (
          <Tag key={`${title}-${item.expert_id}-${item.file_path || "none"}`} color={color}>
            {item.expert_name || item.expert_id}
          </Tag>
        ))}
      </Space>
    </div>
  );
};

export const ExpertRoutingPanel: React.FC<{ summary: ExpertRoutingSummary | null }> = ({ summary }) => {
  if (!summary) return null;
  const hasAdjustments = summary.skipped_experts.length > 0 || summary.system_added_experts.length > 0;
  const bannerTone = summary.system_added_experts.length > 0 ? "warning" : hasAdjustments ? "info" : "default";
  const heading =
    summary.system_added_experts.length > 0
      ? "专家与代码不完全匹配，系统已自动补入兜底专家继续审查"
      : hasAdjustments
        ? "部分已选择专家与当前变更相关性较低，系统已自动跳过"
        : "本轮专家路由已完成";
  return (
    <Card className={`module-card expert-routing-card expert-routing-card-${bannerTone}`} title="专家路由提示">
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Paragraph className="expert-routing-summary">{heading}</Paragraph>
        <RoutingExpertTags title="用户选择" items={summary.user_selected_experts} color="blue" />
        <RoutingExpertTags title="实际参与" items={summary.effective_experts} color="green" />
        <RoutingExpertTags title="系统补入" items={summary.system_added_experts} color="gold" />
        {summary.skipped_experts.length > 0 ? (
          <div className="routing-group">
            <Text className="routing-group-title">已跳过</Text>
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              {summary.skipped_experts.map((item) => (
                <div key={`skipped-${item.expert_id}-${item.file_path || "none"}`} className="routing-skip-item">
                  <Text strong>{item.expert_name || item.expert_id}</Text>
                  <Text type="secondary">
                    {item.reason || "当前变更未命中该专家的有效审查线索"}
                    {item.file_path ? ` · ${item.file_path}${item.line_start ? `:${item.line_start}` : ""}` : ""}
                  </Text>
                </div>
              ))}
            </Space>
          </div>
        ) : null}
      </Space>
    </Card>
  );
};

export const ExpertRuleCoveragePanel: React.FC<{ items: ExpertRuleCoverageSummary[] }> = ({ items }) => {
  if (items.length === 0) return null;
  return (
    <Card className="module-card" title="专家规则命中统计">
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        {items.map((item) => (
          <Card key={item.expert_id} size="small">
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              <Space wrap>
                <Tag color="geekblue">{item.expert_name}</Tag>
                <Tag color="purple">{`总规则 ${item.rule_screening.total_rules}`}</Tag>
                <Tag>{`启用 ${item.rule_screening.enabled_rules || item.rule_screening.total_rules}`}</Tag>
                <Tag color="magenta">{`命中 ${item.rule_screening.matched_rule_count}`}</Tag>
                <Tag color="volcano">{`强命中 ${item.rule_screening.must_review_count}`}</Tag>
                <Tag color="blue">{`候选 ${item.rule_screening.possible_hit_count}`}</Tag>
                {item.rule_screening.batch_count ? <Tag>{`批次 ${item.rule_screening.batch_count}`}</Tag> : null}
                {item.rule_screening.screening_mode ? <Tag>{item.rule_screening.screening_mode}</Tag> : null}
                {item.rule_screening.screening_fallback_used ? <Tag color="orange">fallback</Tag> : null}
              </Space>
              {item.rule_screening.matched_rules_for_llm?.length ? (
                <Space wrap>
                  {item.rule_screening.matched_rules_for_llm.map((rule) => (
                    <Tag key={`${item.expert_id}-${rule.rule_id || rule.title}`} color="cyan">
                      {`${rule.priority ? `[${rule.priority}] ` : ""}${rule.title || rule.rule_id}`}
                    </Tag>
                  ))}
                </Space>
              ) : (
                <Text type="secondary">本轮未命中需要带入深审的规则。</Text>
              )}
            </Space>
          </Card>
        ))}
      </Space>
    </Card>
  );
};

export const ExpertSelectionPanel: React.FC<{ summary: ExpertSelectionSummary | null }> = ({ summary }) => {
  return (
    <Card className="module-card expert-routing-card expert-routing-card-info" title="专家参与判定">
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Paragraph className="expert-routing-summary">
          {summary
            ? "主Agent 已基于当前 MR 信息、完整 diff 和专家画像，先由大模型判定本次真正需要参与审核的专家集合。"
            : "主Agent 正在结合当前 MR、完整 diff 和专家画像判定本轮需要参与审核的专家，请稍候。"}
        </Paragraph>
        {summary ? <RoutingExpertTags title="大模型选中" items={summary.selected_experts} color="green" /> : null}
        {summary?.requested_expert_ids.length ? (
          <div className="routing-group">
            <Text className="routing-group-title">原始候选</Text>
            <Space size={[8, 8]} wrap>
              {summary.requested_expert_ids.map((item) => (
                <Tag key={`requested-${item}`} color="blue">
                  {item}
                </Tag>
              ))}
            </Space>
          </div>
        ) : null}
        {summary?.skipped_experts.length ? (
          <div className="routing-group">
            <Text className="routing-group-title">未参与本轮</Text>
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              {summary.skipped_experts.map((item) => (
                <div key={`selection-skipped-${item.expert_id}-${item.file_path || "none"}`} className="routing-skip-item">
                  <Text strong>{item.expert_name || item.expert_id}</Text>
                  <Text type="secondary">{item.reason || "大模型未将其纳入本次 MR 的审核集合"}</Text>
                </div>
              ))}
            </Space>
          </div>
        ) : !summary ? (
          <Tag color="processing">正在判定参与专家</Tag>
        ) : null}
      </Space>
    </Card>
  );
};
