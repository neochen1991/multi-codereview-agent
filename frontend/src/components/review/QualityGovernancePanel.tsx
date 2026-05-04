import React, { useMemo } from "react";
import { Card, Col, Empty, List, Row, Space, Statistic, Tag, Typography } from "antd";

import type { DebateIssue, IssueFilterDecision, ReviewReport, ReviewSummary } from "@/services/api";
import { humanizeReviewText, humanizeSeverity } from "@/utils/displayText";

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
  const visibleDecisions = issueFilterDecisions.slice(0, 6);

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
            {requiredExperts.map((expertId) => (
              <Tag key={expertId} color="geekblue">
                {expertId}
              </Tag>
            ))}
          </Space>
          {excludedFiles.length ? (
            <Text type="secondary">
              已按仓库策略排除：{excludedFiles.slice(0, 6).join("、")}
              {excludedFiles.length > 6 ? ` 等 ${excludedFiles.length} 个文件` : ""}
            </Text>
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
