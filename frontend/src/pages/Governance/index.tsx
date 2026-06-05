import React, { useEffect, useState } from "react";
import { Button, Card, Col, Empty, Input, Row, Select, Space, Statistic, Table, Tag, Typography } from "antd";

import {
  governanceApi,
  type GovernanceMetrics,
  type LlmTimeoutMetrics,
  type ReviewLearningCase,
  type RuntimeThresholdRecommendations,
  type StaticToolQualityBreakdown,
} from "@/services/api";

const { Paragraph, Text } = Typography;

// 治理页用于观察平台级质量指标和各专家表现。
const GovernancePage: React.FC = () => {
  const [metrics, setMetrics] = useState<GovernanceMetrics | null>(null);
  const [llmTimeoutMetrics, setLlmTimeoutMetrics] = useState<LlmTimeoutMetrics | null>(null);
  const [thresholdRecommendations, setThresholdRecommendations] = useState<RuntimeThresholdRecommendations | null>(null);
  const [learningCases, setLearningCases] = useState<ReviewLearningCase[]>([]);
  const [learningRepoFilter, setLearningRepoFilter] = useState("");
  const [learningTypeFilter, setLearningTypeFilter] = useState("");
  const [learningStatusFilter, setLearningStatusFilter] = useState<"all" | "active" | "disabled">("all");
  const [loading, setLoading] = useState(false);
  const [updatingLearningCaseId, setUpdatingLearningCaseId] = useState("");

  useEffect(() => {
    setLoading(true);
    void Promise.all([
      governanceApi.getQualityMetrics(),
      governanceApi.getLlmTimeoutMetrics(),
      governanceApi.getRuntimeThresholdRecommendations(),
      governanceApi.getReviewLearningCases(),
    ])
      .then(([quality, llmTimeout, recommendations, cases]) => {
        setMetrics(quality);
        setLlmTimeoutMetrics(llmTimeout);
        setThresholdRecommendations(recommendations);
        setLearningCases(cases);
      })
      .finally(() => setLoading(false));
  }, []);

  const filteredLearningCases = learningCases.filter((item) => {
    const repoMatched =
      !learningRepoFilter.trim() || item.repo_id.toLowerCase().includes(learningRepoFilter.trim().toLowerCase());
    const typeMatched =
      !learningTypeFilter.trim() || item.issue_type.toLowerCase().includes(learningTypeFilter.trim().toLowerCase());
    const statusMatched = learningStatusFilter === "all" || item.status === learningStatusFilter;
    return repoMatched && typeMatched && statusMatched;
  });
  const learningCaseStats = learningCases.reduce(
    (acc, item) => {
      if (item.decision === "approved") {
        acc.confirmed += 1;
      } else if (item.decision === "rejected") {
        acc.rejected += 1;
      } else if (item.decision === "conflicted") {
        acc.conflicted += 1;
      }
      acc.matchCount += item.match_count || 0;
      return acc;
    },
    { confirmed: 0, rejected: 0, conflicted: 0, matchCount: 0 },
  );
  const updateLearningCaseStatus = async (caseId: string, status: "active" | "disabled") => {
    setUpdatingLearningCaseId(caseId);
    try {
      const updated = await governanceApi.updateReviewLearningCaseStatus(caseId, status);
      setLearningCases((current) => current.map((item) => (item.case_id === caseId ? updated : item)));
    } finally {
      setUpdatingLearningCaseId("");
    }
  };
  const effectLevelLabel = (level: string) => {
    if (level === "enforced") {
      return "自动执行";
    }
    if (level === "conflict_review") {
      return "待治理";
    }
    if (level === "strong_hint") {
      return "强提示";
    }
    return "弱提示";
  };
  const effectLevelColor = (level: string) => {
    if (level === "enforced") {
      return "error";
    }
    if (level === "conflict_review") {
      return "warning";
    }
    if (level === "strong_hint") {
      return "processing";
    }
    return "default";
  };
  const rateText = (value?: number) => `${Math.round((value || 0) * 100)}%`;
  const strategyLabel = (strategy: string) => {
    if (strategy === "no_llm") {
      return "无模型";
    }
    if (strategy === "light_review") {
      return "轻量检视";
    }
    if (strategy === "targeted_review") {
      return "定向检视";
    }
    if (strategy === "deep_review") {
      return "深度检视";
    }
    return strategy || "未知";
  };
  const toolBreakdownColumns = [
    {
      title: "工具",
      key: "tool",
      render: (_: unknown, row: StaticToolQualityBreakdown) => <Tag color="cyan">{row.tool || "unknown"}</Tag>,
    },
    { title: "原始", dataIndex: "raw_signal_count", key: "raw_signal_count" },
    { title: "候选", dataIndex: "diff_candidate_count", key: "diff_candidate_count" },
    { title: "工具报告", dataIndex: "deterministic_candidate_count", key: "deterministic_candidate_count" },
    { title: "正式", dataIndex: "formal_issue_count", key: "formal_issue_count" },
    {
      title: "误报率",
      key: "false_positive_rate",
      render: (_: unknown, row: StaticToolQualityBreakdown) => rateText(row.false_positive_rate),
    },
  ];
  const ruleBreakdownColumns = [
    {
      title: "规则",
      key: "rule",
      render: (_: unknown, row: StaticToolQualityBreakdown) => (
        <Space size={4} wrap>
          <Tag>{row.tool || "tool"}</Tag>
          <Text>{row.rule_id || row.rule_key || "unknown"}</Text>
        </Space>
      ),
    },
    { title: "候选", dataIndex: "diff_candidate_count", key: "diff_candidate_count" },
    { title: "正式", dataIndex: "formal_issue_count", key: "formal_issue_count" },
    { title: "误报", dataIndex: "false_positive_count", key: "false_positive_count" },
    {
      title: "正式率",
      key: "formalization_rate",
      render: (_: unknown, row: StaticToolQualityBreakdown) => rateText(row.formalization_rate),
    },
  ];
  const expertBreakdownColumns = [
    {
      title: "专家",
      key: "expert_id",
      render: (_: unknown, row: StaticToolQualityBreakdown) => <Text>{row.expert_id || "unknown"}</Text>,
    },
    { title: "收到候选", dataIndex: "diff_candidate_count", key: "diff_candidate_count" },
    { title: "采纳候选", dataIndex: "expert_adopted_count", key: "expert_adopted_count" },
    { title: "正式问题", dataIndex: "formal_issue_count", key: "formal_issue_count" },
    {
      title: "采纳率",
      key: "adoption_rate",
      render: (_: unknown, row: StaticToolQualityBreakdown) => rateText(row.adoption_rate),
    },
  ];

  return (
    <div className="page-container">
      <Card className="module-card" title="治理中心" loading={loading}>
        <Paragraph>
          这一页对齐设计文档里的治理层，展示工具信号从原始命中到正式问题的采纳漏斗，以及确认率、复核保留率和误报反馈。
        </Paragraph>
        {metrics?.metrics_limited ? (
          <Paragraph type="secondary">
            当前治理指标聚合最近 {metrics.metrics_review_sample_count || 0} 条检视记录；系统共有{" "}
            {metrics.metrics_review_total_count || metrics.review_count || 0} 条历史记录。
          </Paragraph>
        ) : null}
        <Row gutter={[16, 16]}>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="审核任务" value={metrics?.review_count || 0} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="正式问题数" value={metrics?.issue_count || 0} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="工具确认率" value={((metrics?.tool_confirmation_rate || 0) * 100).toFixed(0)} suffix="%" />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="工具观察数" value={metrics?.tool_observation_count || 0} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="工具采纳率" value={((metrics?.tool_adoption_rate || 0) * 100).toFixed(0)} suffix="%" />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="SAST 交叉验证" value={metrics?.sast_cross_validated_issue_count || 0} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="工具误报率" value={((metrics?.tool_false_positive_rate || 0) * 100).toFixed(0)} suffix="%" />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="复核保留率" value={((metrics?.debate_survival_rate || 0) * 100).toFixed(0)} suffix="%" />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="人工确认" value={metrics?.needs_human_count || 0} suffix={<Tag color="error">待确认</Tag>} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="误报标签" value={metrics?.false_positive_count || 0} suffix={<Tag color="warning">人工反馈</Tag>} />
            </Card>
          </Col>
        </Row>
        <Card className="module-card" title="静态工具采纳漏斗" style={{ marginTop: 16 }}>
          <Row gutter={[16, 16]}>
            <Col xs={24} md={6}>
              <Statistic title="原始信号" value={metrics?.tool_funnel?.raw_signal_count ?? metrics?.tool_raw_signal_count ?? 0} />
              <Text type="secondary">SAST/linter/report 原始命中</Text>
            </Col>
            <Col xs={24} md={6}>
              <Statistic title="进入候选" value={metrics?.tool_funnel?.diff_candidate_count ?? metrics?.tool_diff_candidate_count ?? 0} />
              <Text type="secondary">匹配当前 diff 后的工具候选</Text>
            </Col>
            <Col xs={24} md={6}>
              <Statistic title="专家采纳" value={metrics?.tool_funnel?.expert_adopted_count ?? metrics?.tool_expert_adopted_count ?? 0} />
              <Text type="secondary">专家工具信号解释后形成候选</Text>
            </Col>
            <Col xs={24} md={6}>
              <Statistic title="正式问题" value={metrics?.tool_funnel?.formal_issue_count ?? metrics?.tool_formal_issue_count ?? 0} />
              <Text type="secondary">进入最终有效问题清单</Text>
            </Col>
          </Row>
        </Card>
        <Card className="module-card" title="检视加速策略" style={{ marginTop: 16 }}>
          <Row gutter={[16, 16]}>
            <Col xs={24} md={6}>
              <Statistic title="路由优化" value={metrics?.routing_optimized_count || 0} />
              <Text type="secondary">按风险画像裁剪参与专家</Text>
            </Col>
            <Col xs={24} md={6}>
              <Statistic title="缓存命中率" value={((metrics?.review_cache_hit_rate || 0) * 100).toFixed(0)} suffix="%" />
              <Text type="secondary">
                命中 {metrics?.review_cache_hit_count || 0} / 开启 {metrics?.review_cache_enabled_count || 0}
              </Text>
            </Col>
            <Col xs={24} md={6}>
              <Statistic title="静态预过滤观察" value={metrics?.static_prefilter_observation_count || 0} />
              <Text type="secondary">进入路由画像的工具观察</Text>
            </Col>
            <Col xs={24} md={6}>
              <Space wrap size={[8, 8]}>
                {(metrics?.execution_strategy_breakdown || []).slice(0, 6).map((item) => (
                  <Tag key={item.strategy} color={item.strategy === "deep_review" ? "red" : item.strategy === "no_llm" ? "default" : "blue"}>
                    {strategyLabel(item.strategy)} {item.count}
                  </Tag>
                ))}
                {metrics?.execution_strategy_breakdown?.length ? null : <Tag>暂无策略数据</Tag>}
              </Space>
            </Col>
          </Row>
        </Card>
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col xs={24} xl={8}>
            <Card className="module-card" title="工具价值 Top">
              <Table<StaticToolQualityBreakdown>
                size="small"
                pagination={false}
                rowKey={(row) => row.tool || "unknown_tool"}
                columns={toolBreakdownColumns}
                dataSource={(metrics?.tool_breakdown || []).slice(0, 6)}
              />
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card className="module-card" title="规则质量 Top">
              <Table<StaticToolQualityBreakdown>
                size="small"
                pagination={false}
                rowKey={(row) => row.rule_key || `${row.tool}:${row.rule_id}`}
                columns={ruleBreakdownColumns}
                dataSource={(metrics?.rule_breakdown || []).slice(0, 6)}
              />
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card className="module-card" title="专家采纳工具信号">
              <Table<StaticToolQualityBreakdown>
                size="small"
                pagination={false}
                rowKey={(row) => row.expert_id || "unknown_expert"}
                columns={expertBreakdownColumns}
                dataSource={(metrics?.expert_tool_breakdown || []).slice(0, 6)}
              />
            </Card>
          </Col>
        </Row>
      </Card>

      <Card className="module-card" title="反馈阈值建议" style={{ marginTop: 16 }} loading={loading}>
        <Paragraph>
          这里基于历史人工反馈生成阈值建议，只展示建议，不会自动修改运行时配置。建议生效仍需要人工到设置页确认。
        </Paragraph>
        <Space wrap style={{ marginBottom: 16 }}>
          <Tag color={thresholdRecommendations?.should_tighten ? "warning" : "success"}>
            {thresholdRecommendations?.should_tighten ? "建议收紧阈值" : "暂无收紧建议"}
          </Tag>
          <Tag color="default">{thresholdRecommendations?.applied ? "已自动应用" : "未自动应用"}</Tag>
        </Space>
        <Row gutter={[16, 16]}>
          <Col xs={24} md={12} xl={6}>
            <Card className="module-card">
              <Statistic
                title="建议 P1 阈值"
                value={thresholdRecommendations?.recommended_thresholds.issue_confidence_threshold_p1 || 0}
                precision={2}
              />
            </Card>
          </Col>
          <Col xs={24} md={12} xl={6}>
            <Card className="module-card">
              <Statistic
                title="建议 P2 阈值"
                value={thresholdRecommendations?.recommended_thresholds.issue_confidence_threshold_p2 || 0}
                precision={2}
              />
            </Card>
          </Col>
          <Col xs={24} md={12} xl={6}>
            <Card className="module-card">
              <Statistic
                title="建议 P3 阈值"
                value={thresholdRecommendations?.recommended_thresholds.issue_confidence_threshold_p3 || 0}
                precision={2}
              />
            </Card>
          </Col>
          <Col xs={24} md={12} xl={6}>
            <Card className="module-card">
              <Statistic
                title="提示类阈值"
                value={thresholdRecommendations?.recommended_thresholds.hint_issue_confidence_threshold || 0}
                precision={2}
              />
            </Card>
          </Col>
        </Row>
        <Paragraph style={{ marginTop: 16, marginBottom: 0 }}>
          {thresholdRecommendations?.reason || "暂无阈值建议。"}
        </Paragraph>
        {thresholdRecommendations?.basis?.length ? (
          <Space wrap style={{ marginTop: 12 }}>
            {thresholdRecommendations.basis.slice(0, 6).map((item) => (
              <Tag key={`${item.group}-${item.key}`} color="warning">
                {`${item.group}:${item.key} · 样本 ${item.sample_count} · 误报 ${(item.false_positive_rate * 100).toFixed(0)}%`}
              </Tag>
            ))}
          </Space>
        ) : null}
      </Card>

      <Card className="module-card" title="人工反馈学习案例" style={{ marginTop: 16 }} loading={loading}>
        <Paragraph>
          人工反馈会沉淀为检视边界：误报样本用于过滤相似误报，确认样本用于小幅提高同类真实问题置信度。
        </Paragraph>
        <Space wrap style={{ marginBottom: 16 }}>
          <Tag color="success">确认样本 {learningCaseStats.confirmed}</Tag>
          <Tag color="warning">误报样本 {learningCaseStats.rejected}</Tag>
          <Tag color="gold">冲突样本 {learningCaseStats.conflicted}</Tag>
          <Tag color="processing">累计命中 {learningCaseStats.matchCount}</Tag>
        </Space>
        <Space wrap style={{ marginBottom: 16 }}>
          <Input
            allowClear
            placeholder="按代码仓筛选"
            value={learningRepoFilter}
            onChange={(event) => setLearningRepoFilter(event.target.value)}
            style={{ width: 220 }}
          />
          <Input
            allowClear
            placeholder="按问题类型筛选"
            value={learningTypeFilter}
            onChange={(event) => setLearningTypeFilter(event.target.value)}
            style={{ width: 260 }}
          />
          <Select
            value={learningStatusFilter}
            onChange={setLearningStatusFilter}
            style={{ width: 140 }}
            options={[
              { label: "全部状态", value: "all" },
              { label: "已启用", value: "active" },
              { label: "已停用", value: "disabled" },
            ]}
          />
          <Button
            onClick={() => {
              setLearningRepoFilter("");
              setLearningTypeFilter("");
              setLearningStatusFilter("all");
            }}
          >
            清空筛选
          </Button>
          <Tag color="default">
            显示 {filteredLearningCases.length} / {learningCases.length}
          </Tag>
        </Space>
        {filteredLearningCases.length ? (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            {filteredLearningCases.slice(0, 8).map((item) => (
              <div key={item.case_id} className="routing-skip-item">
                <Space wrap size={[8, 8]}>
                  <Tag color="blue">{item.issue_type || "general"}</Tag>
                  <Tag>{item.repo_id || "未标记仓库"}</Tag>
                  <Tag color={item.decision === "approved" ? "success" : item.decision === "conflicted" ? "gold" : "warning"}>
                    {item.decision === "approved" ? "确认样本" : item.decision === "conflicted" ? "冲突样本" : "误报样本"}
                  </Tag>
                  <Tag color={item.reason_category === "context_counterexample" ? "gold" : item.reason_category === "conflicting_feedback" ? "warning" : "default"}>
                    {item.reason_category === "context_counterexample"
                      ? "上下文反例"
                      : item.reason_category === "conflicting_feedback"
                        ? `反馈冲突 ${item.positive_count || 0}/${item.negative_count || 0}`
                      : item.reason_category === "confirmed_risk"
                        ? "真实问题"
                        : "误报边界"}
                  </Tag>
                  <Tag color="default">支持 {item.support_count || 1}</Tag>
                  <Tag color={effectLevelColor(item.effect_level)}>{effectLevelLabel(item.effect_level)}</Tag>
                  <Tag color={item.match_count ? "processing" : "default"}>命中 {item.match_count || 0}</Tag>
                  <Tag color={item.status === "active" ? "success" : "default"}>
                    {item.status === "active" ? "已启用" : "已停用"}
                  </Tag>
                  <Button
                    size="small"
                    loading={updatingLearningCaseId === item.case_id}
                    onClick={() => void updateLearningCaseStatus(item.case_id, item.status === "active" ? "disabled" : "active")}
                  >
                    {item.status === "active" ? "停用" : "启用"}
                  </Button>
                </Space>
                <div style={{ marginTop: 8 }}>
                  <Text strong>{item.learning_summary || "暂无学习摘要"}</Text>
                </div>
                <div style={{ marginTop: 6 }}>
                  <Text type="secondary">
                    {item.file_path || "-"}:{item.line_start || 1}
                    {item.counter_evidence ? ` · ${item.counter_evidence}` : ""}
                  </Text>
                </div>
                {item.last_matched_at ? (
                  <div style={{ marginTop: 6 }}>
                    <Text type="secondary">
                      最近命中：{item.last_matched_at}
                      {item.last_match_action ? ` · ${item.last_match_action}` : ""}
                    </Text>
                  </div>
                ) : null}
              </div>
            ))}
          </Space>
        ) : (
          <Empty description={learningCases.length ? "当前筛选条件下没有学习案例。" : "还没有人工驳回沉淀的学习案例。"} image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Card>

      <Card className="module-card" title="模型调用超时观测" style={{ marginTop: 16 }} loading={loading}>
        <Paragraph>
          这里聚合最近一段时间后端日志里的模型调用超时与耗时分布，优先帮助定位是建连慢、读流慢，还是并发池等待。
        </Paragraph>
        <Row gutter={[16, 16]}>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="Timeout 总数" value={llmTimeoutMetrics?.timeout_count || 0} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="Read Timeout" value={llmTimeoutMetrics?.read_timeout_count || 0} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="Connect Timeout" value={llmTimeoutMetrics?.connect_timeout_count || 0} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="平均成功耗时" value={llmTimeoutMetrics?.avg_success_elapsed_ms || 0} suffix="ms" precision={0} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="最大成功耗时" value={llmTimeoutMetrics?.max_success_elapsed_ms || 0} suffix="ms" precision={0} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={4}>
            <Card className="module-card">
              <Statistic title="成功调用数" value={llmTimeoutMetrics?.success_count || 0} />
            </Card>
          </Col>
        </Row>
        <Card className="module-card" style={{ marginTop: 16 }} title="最近 Timeout 样本">
          {llmTimeoutMetrics?.recent_timeouts?.length ? (
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
              {llmTimeoutMetrics.recent_timeouts.map((item, index) => (
                <div key={`${item.timestamp}-${item.review_id}-${index}`} className="routing-skip-item">
                  <Space wrap size={[8, 8]}>
                    <Tag color="error">{item.timeout_kind || "timeout"}</Tag>
                    {item.phase ? <Tag>{item.phase}</Tag> : null}
                    {item.review_id ? <Tag color="processing">{item.review_id}</Tag> : null}
                    {item.expert_id ? <Tag color="blue">{item.expert_id}</Tag> : null}
                  </Space>
                  <div style={{ marginTop: 6 }}>
                    <Text>
                      {item.timestamp || "-"} · {item.provider || "-"} / {item.model || "-"} · 本次 {Math.round(item.attempt_elapsed_ms || 0)} ms · 累计{" "}
                      {Math.round(item.total_elapsed_ms || 0)} ms
                    </Text>
                  </div>
                </div>
              ))}
            </Space>
          ) : (
            <Empty description="最近没有捕获到模型调用超时。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}
        </Card>
      </Card>
    </div>
  );
};

export default GovernancePage;
