import React from "react";
import { Alert, Card, Descriptions, Empty, Space, Tag, Typography } from "antd";

import type { DebateIssue, ReviewFinding } from "@/services/api";

const { Paragraph } = Typography;

const uniqueList = (values?: string[]) => Array.from(new Set((values || []).map((item) => String(item || "").trim()).filter(Boolean)));

type IssueDetailPanelProps = {
  issue: DebateIssue | null;
  finding?: ReviewFinding | null;
  findingDetailsLoading?: boolean;
  findingDetailsError?: string;
};

// 议题详情卡主要展示当前选中 issue 的摘要和参与专家。
const IssueDetailPanel: React.FC<IssueDetailPanelProps> = ({
  issue,
  finding,
  findingDetailsLoading = false,
  findingDetailsError = "",
}) => {
  const confidenceBreakdown = issue?.confidence_breakdown || {};
  const codeContext = finding?.code_context;
  const contextFiles = codeContext?.context_files || finding?.context_files || [];
  const inputCompleteness = codeContext?.input_completeness;
  const reviewInputs = codeContext?.review_inputs;
  const aggregatedTitles = uniqueList(issue?.aggregated_titles);
  const aggregatedSummaries = uniqueList(issue?.aggregated_summaries);
  const aggregatedStrategies = uniqueList(issue?.aggregated_remediation_strategies);
  const aggregatedSuggestions = uniqueList(issue?.aggregated_remediation_suggestions);
  const aggregatedSteps = uniqueList(issue?.aggregated_remediation_steps);
  const hasFullFindingDetails = Boolean(
    finding?.code_excerpt ||
      finding?.suggested_code ||
      (finding?.code_context && Object.keys(finding.code_context).length > 0),
  );

  return (
    <Card className="module-card process-sidebar-card process-sidebar-card-md" title="议题详情">
      <div className="process-card-scroll">
        {!issue ? (
          <Empty description="选择一个议题后，这里会展示裁决信息、证据和参与专家。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <>
            {findingDetailsLoading && finding && !hasFullFindingDetails ? (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="完整关联上下文加载中"
                description="当前先展示议题摘要，完整的代码上下文会在后台补全后自动更新。"
              />
            ) : null}
            {!findingDetailsLoading && findingDetailsError && finding && !hasFullFindingDetails ? (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                message="完整关联上下文暂未加载成功"
                description={`${findingDetailsError}。这不会影响当前议题结论本身。`}
              />
            ) : null}
            <Descriptions column={1} size="small">
              <Descriptions.Item label="问题标题">
                {issue.title || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="问题描述">
                <div>
                  <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                    {issue.summary || "-"}
                  </Paragraph>
                </div>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={issue.status === "needs_human" ? "error" : issue.status === "resolved" ? "success" : "processing"}>
                  {issue.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="严重度">
                <Tag color={issue.severity === "blocker" || issue.severity === "high" ? "error" : "processing"}>
                  {issue.severity}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="裁决路径">
                {issue.resolution || (issue.needs_human ? "human_gate" : "judge_merge")}
              </Descriptions.Item>
              <Descriptions.Item label="是否辩论">
                <Tag color={issue.needs_debate ? "processing" : "default"}>
                  {issue.needs_debate ? "debated" : "direct-merge"}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="问题位置">
                {issue.file_path ? `${issue.file_path}:${issue.line_start || 1}` : "-"}
              </Descriptions.Item>
              <Descriptions.Item label="参与专家">
                {issue.participant_expert_ids.join("、") || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="证据">
                {issue.evidence.join("、") || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="关联 findings">
                {issue.finding_ids.join("、") || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="置信度">
                {`${(issue.confidence * 100).toFixed(0)}%`}
              </Descriptions.Item>
            </Descriptions>

            {Object.keys(confidenceBreakdown).length ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>置信度分解</Paragraph>
                <Descriptions column={1} size="small">
                  {"base_weighted_confidence" in confidenceBreakdown ? (
                    <Descriptions.Item label="基础加权分">
                      {String(confidenceBreakdown.base_weighted_confidence)}
                    </Descriptions.Item>
                  ) : null}
                  {"consensus_bonus" in confidenceBreakdown ? (
                    <Descriptions.Item label="一致性加分">{String(confidenceBreakdown.consensus_bonus)}</Descriptions.Item>
                  ) : null}
                  {"evidence_bonus" in confidenceBreakdown ? (
                    <Descriptions.Item label="证据加分">{String(confidenceBreakdown.evidence_bonus)}</Descriptions.Item>
                  ) : null}
                  {"verification_bonus" in confidenceBreakdown ? (
                    <Descriptions.Item label="核验加分">{String(confidenceBreakdown.verification_bonus)}</Descriptions.Item>
                  ) : null}
                  {"hypothesis_penalty" in confidenceBreakdown ? (
                    <Descriptions.Item label="推测扣分">{String(confidenceBreakdown.hypothesis_penalty)}</Descriptions.Item>
                  ) : null}
                </Descriptions>
              </div>
            ) : null}

            {aggregatedTitles.length || aggregatedSummaries.length ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>聚合子问题</Paragraph>
                <Descriptions column={1} size="small">
                  {aggregatedTitles.length ? (
                    <Descriptions.Item label="问题标题">
                      <Space wrap>
                        {aggregatedTitles.map((title) => (
                          <Tag key={title} color="blue">
                            {title}
                          </Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                  ) : null}
                  {aggregatedSummaries.length ? (
                    <Descriptions.Item label="问题说明">
                      <div>
                        {aggregatedSummaries
                          .filter((summary) => summary !== issue.summary)
                          .map((summary) => (
                          <Paragraph key={summary} style={{ marginBottom: 8 }}>
                            {summary}
                          </Paragraph>
                          ))}
                      </div>
                    </Descriptions.Item>
                  ) : null}
                </Descriptions>
              </div>
            ) : null}

            {aggregatedStrategies.length || aggregatedSuggestions.length || aggregatedSteps.length ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>聚合修复方案</Paragraph>
                <Descriptions column={1} size="small">
                  {aggregatedStrategies.length ? (
                    <Descriptions.Item label="修复思路">
                      <div>
                        {aggregatedStrategies.map((item) => (
                          <Paragraph key={item} style={{ marginBottom: 8 }}>
                            {item}
                          </Paragraph>
                        ))}
                      </div>
                    </Descriptions.Item>
                  ) : null}
                  {aggregatedSuggestions.length ? (
                    <Descriptions.Item label="修复建议">
                      <div>
                        {aggregatedSuggestions.map((item) => (
                          <Paragraph key={item} style={{ marginBottom: 8 }}>
                            {item}
                          </Paragraph>
                        ))}
                      </div>
                    </Descriptions.Item>
                  ) : null}
                  {aggregatedSteps.length ? (
                    <Descriptions.Item label="修复步骤">
                      <div>
                        {aggregatedSteps.map((item, index) => (
                          <Paragraph key={`${index}-${item}`} style={{ marginBottom: 6 }}>
                            {index + 1}. {item}
                          </Paragraph>
                        ))}
                      </div>
                    </Descriptions.Item>
                  ) : null}
                </Descriptions>
              </div>
            ) : null}

            {finding ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>关联代码上下文</Paragraph>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="提出专家">{finding.expert_id}</Descriptions.Item>
                  <Descriptions.Item label="路由原因">
                    {codeContext?.routing_reason || "当前未记录路由原因"}
                  </Descriptions.Item>
                  <Descriptions.Item label="目标 hunk">
                    {codeContext?.target_hunk?.hunk_header || "当前未记录 target hunk"}
                  </Descriptions.Item>
                  <Descriptions.Item label="上下文文件">
                    {contextFiles.length ? contextFiles.join("、") : "-"}
                  </Descriptions.Item>
                </Descriptions>
              </div>
            ) : null}

            {finding && (inputCompleteness || reviewInputs) ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>审查输入质量</Paragraph>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="专家规范">
                    <Tag color={inputCompleteness?.review_spec_present ? "success" : "error"}>
                      {inputCompleteness?.review_spec_present ? "已注入" : "缺失"}
                    </Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="语言通用规范提示">
                    <Space wrap>
                      <Tag color={inputCompleteness?.language_guidance_present ? "success" : "error"}>
                        {inputCompleteness?.language_guidance_present ? "已注入" : "缺失"}
                      </Tag>
                      {reviewInputs?.language_guidance_language ? <Tag>{reviewInputs.language_guidance_language}</Tag> : null}
                      {(reviewInputs?.language_guidance_topics || []).slice(0, 4).map((topic) => (
                        <Tag key={topic} color="blue">
                          {topic}
                        </Tag>
                      ))}
                    </Space>
                  </Descriptions.Item>
                  <Descriptions.Item label="规则与文档">
                    <Space wrap>
                      <Tag>{`命中规则 ${inputCompleteness?.matched_rule_count || 0}`}</Tag>
                      <Tag>{`启用规则 ${inputCompleteness?.enabled_rule_count || 0}`}</Tag>
                      <Tag>{`绑定文档 ${inputCompleteness?.bound_document_count || 0}`}</Tag>
                    </Space>
                  </Descriptions.Item>
                  <Descriptions.Item label="代码输入">
                    <Space wrap>
                      <Tag color={inputCompleteness?.target_file_diff_present ? "success" : "error"}>
                        {inputCompleteness?.target_file_diff_present ? "变更代码已注入" : "变更代码缺失"}
                      </Tag>
                      <Tag color={inputCompleteness?.source_context_present ? "success" : "error"}>
                        {inputCompleteness?.source_context_present ? "当前源码已注入" : "当前源码缺失"}
                      </Tag>
                      <Tag color={(inputCompleteness?.related_context_count || 0) > 0 ? "success" : "error"}>
                        {`关联源码 ${(inputCompleteness?.related_context_count || 0) > 0 ? "已注入" : "缺失"}`}
                      </Tag>
                    </Space>
                  </Descriptions.Item>
                  {inputCompleteness?.missing_sections?.length ? (
                    <Descriptions.Item label="缺失输入">
                      <Tag color="gold">{inputCompleteness.missing_sections.join(" / ")}</Tag>
                    </Descriptions.Item>
                  ) : null}
                </Descriptions>
              </div>
            ) : null}
          </>
        )}
      </div>
    </Card>
  );
};

export default IssueDetailPanel;
