import React, { useMemo } from "react";
import { Card, Col, Empty, List, Row, Space, Statistic, Tag, Typography } from "antd";

import type { DebateIssue, IssueFilterDecision, ReviewReport, ReviewSummary } from "@/services/api";
import { humanizeExpertId, humanizeReviewText, humanizeSeverity } from "@/utils/displayText";

const { Text } = Typography;

type QualityGovernancePanelProps = {
  report: ReviewReport | null;
  review: ReviewSummary | null;
  issues: DebateIssue[];
  issueFilterDecisions: IssueFilterDecision[];
};

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

const asStringList = (value: unknown): string[] =>
  Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];

const asNumber = (value: unknown, fallback = 0): number =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

const environmentStatusLabel = (value: string): string => {
  if (value === "passed") return "通过";
  if (value === "failed") return "失败";
  if (value === "degraded") return "部分能力不可用";
  if (value === "warning") return "有提醒";
  return humanizeReviewText(value);
};

const verificationStatusLabel = (value: string): string => {
  if (!value || value === "unknown") return "证据待核验";
  if (value === "accepted") return "证据已确认";
  if (value === "rejected") return "证据未采纳";
  if (value === "verified") return "工具已核验";
  return humanizeReviewText(value);
};

const contextReasonLabel = (value: string): string =>
  humanizeReviewText(value)
    .replace(/代码结构图谱降级/g, "已改用轻量上下文")
    .replace(/代码结构图谱未就绪/g, "代码结构图谱尚未准备好")
    .replace(/降级/g, "改用备用方案");

const QualityGovernancePanel: React.FC<QualityGovernancePanelProps> = ({
  report,
  review,
  issues,
  issueFilterDecisions,
}) => {
  const reviewPolicy = useMemo(() => {
    const metadata = asRecord(review?.subject?.metadata);
    return asRecord(metadata.review_policy);
  }, [review]);
  const environmentPreflight = useMemo(() => {
    const metadata = asRecord(review?.subject?.metadata);
    return asRecord(metadata.review_environment_preflight);
  }, [review]);

  const summary = report?.confidence_summary;
  const fallbackEvidenceChainCount = issues.filter((issue) => (issue.evidence_chain || []).length > 0).length;
  const evidenceCoverage = Math.round(
    asNumber(
      summary?.evidence_chain_coverage,
      issues.length > 0 ? fallbackEvidenceChainCount / issues.length : 0,
    ) * 100,
  );
  const qualityFilteredCount = asNumber(summary?.quality_filtered_issue_count, issueFilterDecisions.length);
  const budgetFilteredCount = asNumber(
    summary?.policy_comment_budget_filtered_count,
    issueFilterDecisions.filter((item) => item.rule_code === "repo_policy_comment_budget").length,
  );
  const excludedFiles = asStringList(reviewPolicy.excluded_changed_files);
  const reviewableFiles = asStringList(reviewPolicy.reviewable_changed_files);
  const requiredExperts = asStringList(reviewPolicy.required_experts);
  const maxComments = Number(reviewPolicy.max_comments_per_review || 0);
  const pathRules = Array.isArray(reviewPolicy.path_rules) ? reviewPolicy.path_rules : [];
  const excludedFileCount = asNumber(summary?.review_policy_excluded_file_count, excludedFiles.length);
  const reviewableFileCount = asNumber(summary?.review_policy_reviewable_file_count, reviewableFiles.length);
  const pathRuleCount = asNumber(summary?.review_policy_path_rule_count, pathRules.length);
  const qualityGateMissingCount = asNumber(summary?.quality_gate_missing_count, 0);
  const findingIssueMismatchCount = asNumber(summary?.finding_issue_family_mismatch_count, 0);
  const todoAnchorFailureCount = asNumber(summary?.todo_anchor_failure_count, 0);
  const duplicateTextCount = asNumber(summary?.cross_anchor_duplicate_text_count, 0);
  const fallbackTextCount = asNumber(summary?.fallback_text_failure_count, 0);
  const visibleDecisions = issueFilterDecisions.slice(0, 6);
  const ruleIds = useMemo(() => {
    const ids = new Set<string>();
    for (const finding of report?.findings || []) {
      for (const ruleId of finding.matched_rules || []) {
        if (ruleId) ids.add(ruleId);
      }
    }
    return Array.from(ids);
  }, [report?.findings]);
  const verificationStatusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const finding of report?.findings || []) {
      const verification = asRecord(finding.code_context?.candidate_verification);
      const status = String(verification.status || "unknown");
      counts[status] = (counts[status] || 0) + 1;
    }
    return counts;
  }, [report?.findings]);
  const environmentStatus = String(environmentPreflight.status || "");
  const degradedContextReasons = asStringList(environmentPreflight.degraded_context_reasons);
  const pathResolutionFailures = asStringList(environmentPreflight.path_resolution_failures);

  return (
    <Card className="module-card" title="质量治理与仓库策略">
      {!report && !review ? (
        <Empty description="运行审核后，这里会展示证据链覆盖率、降噪和仓库策略命中情况。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Row gutter={[12, 12]}>
            <Col xs={12} xl={6}>
              <Statistic title="证据链覆盖" value={evidenceCoverage} suffix="%" />
            </Col>
            <Col xs={12} xl={6}>
              <Statistic title="保留观察" value={qualityFilteredCount} />
            </Col>
            <Col xs={12} xl={6}>
              <Statistic title="提交上限保留" value={budgetFilteredCount} />
            </Col>
            <Col xs={12} xl={6}>
              <Statistic title="问题提交上限" value={maxComments || "-"} />
            </Col>
          </Row>
          <Space wrap>
            <Tag color={excludedFileCount ? "warning" : "default"}>{`排除文件 ${excludedFileCount}`}</Tag>
            <Tag color={reviewableFileCount ? "processing" : "default"}>{`参与检视文件 ${reviewableFileCount}`}</Tag>
            <Tag color={pathRuleCount ? "blue" : "default"}>{`路径规则 ${pathRuleCount}`}</Tag>
            {environmentStatus ? (
              <Tag color={environmentStatus === "passed" ? "success" : "warning"}>
                {`环境预检 ${environmentStatusLabel(environmentStatus)}`}
              </Tag>
            ) : null}
            {summary ? (
              <Tag color={summary.quality_gate_passed === false ? "error" : "success"}>
                {summary.quality_gate_passed === false ? `质量门禁异常 ${qualityGateMissingCount}` : "质量门禁通过"}
              </Tag>
            ) : null}
            {summary?.security_expert_activated === false ? <Tag color="error">安全专家未执行</Tag> : null}
            {summary?.business_expert_activated === false ? <Tag color="error">业务专家未执行</Tag> : null}
            {findingIssueMismatchCount ? <Tag color="error">{`发现转问题错配 ${findingIssueMismatchCount}`}</Tag> : null}
            {todoAnchorFailureCount ? <Tag color="error">{`TODO 锚点异常 ${todoAnchorFailureCount}`}</Tag> : null}
            {duplicateTextCount ? <Tag color="error">{`重复描述 ${duplicateTextCount}`}</Tag> : null}
            {fallbackTextCount ? <Tag color="error">{`兜底文案 ${fallbackTextCount}`}</Tag> : null}
            {ruleIds.length ? <Tag color="purple">{`命中规则 ${ruleIds.length}`}</Tag> : null}
            {requiredExperts.map((expertId) => (
              <Tag key={expertId} color="geekblue">
                {humanizeExpertId(expertId)}
              </Tag>
            ))}
          </Space>
          {excludedFiles.length ? (
            <Text type="secondary">
              已按仓库策略排除：{excludedFiles.slice(0, 6).join("、")}
              {excludedFiles.length > 6 ? ` 等 ${excludedFiles.length} 个文件` : ""}
            </Text>
          ) : null}
          {ruleIds.length || Object.keys(verificationStatusCounts).length ? (
            <Space wrap>
              {ruleIds.slice(0, 8).map((ruleId) => (
                <Tag key={ruleId} color="purple">
                  {ruleId}
                </Tag>
              ))}
              {Object.entries(verificationStatusCounts).map(([status, count]) => (
                <Tag key={status} color={status === "accepted" ? "success" : "warning"}>
                  {`${verificationStatusLabel(status)} ${count}`}
                </Tag>
              ))}
            </Space>
          ) : null}
          {degradedContextReasons.length || pathResolutionFailures.length ? (
            <Space direction="vertical" size={4}>
              {degradedContextReasons.length ? (
                <Text type="secondary">关联上下文提示：{degradedContextReasons.slice(0, 6).map(contextReasonLabel).join("、")}</Text>
              ) : null}
              {pathResolutionFailures.length ? (
                <Text type="secondary">未定位到代码路径：{pathResolutionFailures.slice(0, 4).map(contextReasonLabel).join("、")}</Text>
              ) : null}
            </Space>
          ) : null}
          {visibleDecisions.length ? (
            <List
              size="small"
              header={<Text strong>保留观察记录</Text>}
              dataSource={visibleDecisions}
              renderItem={(item) => (
                <List.Item>
                  <Space direction="vertical" size={4} style={{ width: "100%" }}>
                    <Space wrap>
                      <Text strong>{humanizeReviewText(item.topic || item.rule_label || item.rule_code || "未命名记录")}</Text>
                      {item.rule_label || item.rule_code ? (
                        <Tag color={item.rule_code === "repo_policy_comment_budget" ? "gold" : "blue"}>
                          {humanizeReviewText(item.rule_label || item.rule_code)}
                        </Tag>
                      ) : null}
                      {item.severity ? <Tag>{humanizeSeverity(item.severity)}</Tag> : null}
                    </Space>
                    {item.reason ? <Text type="secondary">{humanizeReviewText(item.reason)}</Text> : null}
                    {item.finding_titles?.length ? (
                      <Text type="secondary">关联发现：{item.finding_titles.slice(0, 3).map(humanizeReviewText).join("、")}</Text>
                    ) : null}
                  </Space>
                </List.Item>
              )}
            />
          ) : null}
        </Space>
      )}
    </Card>
  );
};

export default QualityGovernancePanel;
