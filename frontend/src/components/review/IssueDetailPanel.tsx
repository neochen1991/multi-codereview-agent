import React from "react";
import { Alert, Card, Descriptions, Empty, Space, Tag, Typography } from "antd";

import type { DebateIssue, EvidenceChainStep, FindingRuleAttributionDetail, ReviewFinding } from "@/services/api";
import {
  humanizeExpertId,
  humanizeReviewStatus,
  humanizeReviewText,
  humanizeSeverity,
  stripReviewSupplementSections,
} from "@/utils/displayText";
import { evidenceContextSourceLabel, evidenceStepLabel, evidenceStepSummary } from "./evidenceChainDisplay";

const { Paragraph } = Typography;

const uniqueList = (values?: string[]) => Array.from(new Set((values || []).map((item) => String(item || "").trim()).filter(Boolean)));

const normalizeUnknownList = (value: unknown): string[] => {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (typeof item === "string") return item.trim();
      if (!item || typeof item !== "object") return "";
      const payload = item as Record<string, unknown>;
      const title = String(payload.flow || payload.name || payload.qualified_name || payload.symbol || payload.title || "").trim();
      const criticality = String(payload.criticality || payload.risk_level || payload.kind || "").trim();
      const path = String(payload.file_path || payload.path || "").trim();
      const lineStart = typeof payload.line_start === "number" ? payload.line_start : 0;
      const location = path ? `${path}${lineStart ? `:${lineStart}` : ""}` : "";
      return [title, criticality, location].filter(Boolean).join(" · ");
    })
    .filter(Boolean);
};

type IssueDetailPanelProps = {
  issue: DebateIssue | null;
  finding?: ReviewFinding | null;
  findingDetailsLoading?: boolean;
  findingDetailsError?: string;
};

// 议题详情卡主要展示当前选中 issue 的摘要、主责专家和参与专家。
const IssueDetailPanel: React.FC<IssueDetailPanelProps> = ({
  issue,
  finding,
  findingDetailsLoading = false,
  findingDetailsError = "",
}) => {
  const confidenceBreakdown = issue?.confidence_breakdown || {};
  const codeContext = finding?.code_context;
  const codeGraphSourceSummary = codeContext?.code_graph_source_summary || {};
  const codeGraphMinimalContext = codeContext?.code_graph_minimal_context || {};
  const codeGraphImpactAnalysis = codeContext?.code_graph_impact_analysis || {};
  const graphRiskLevel = String(codeGraphMinimalContext.risk_level || codeGraphImpactAnalysis.risk_level || "").trim();
  const graphRiskScore = codeGraphMinimalContext.risk_score ?? codeGraphImpactAnalysis.risk_score;
  const graphContextSource =
    finding?.context_source ||
    String(codeGraphSourceSummary.primary_source || codeGraphSourceSummary.context_source || "").trim();
  const graphSummary = String(codeGraphMinimalContext.summary || codeGraphImpactAnalysis.summary || "").trim();
  const graphReviewPriorities = normalizeUnknownList(
    codeGraphMinimalContext.review_priorities || codeGraphImpactAnalysis.review_priorities,
  );
  const graphAffectedFlows = normalizeUnknownList(
    codeGraphMinimalContext.affected_flows || codeGraphImpactAnalysis.affected_flows,
  );
  const graphEvidenceChain = issue?.evidence_chain?.length
    ? issue.evidence_chain
    : finding?.evidence_chain?.length
      ? finding.evidence_chain
      : codeContext?.code_graph_evidence_chain || [];
  const contextFiles = codeContext?.context_files || finding?.context_files || [];
  const inputCompleteness = codeContext?.input_completeness;
  const reviewInputs = codeContext?.review_inputs;
  const ruleAttribution = codeContext?.rule_attribution || {};
  const generalRules = uniqueList(ruleAttribution.general_rules || []);
  const validCustomRuleIds = uniqueList(ruleAttribution.valid_custom_rule_ids || []);
  const invalidCustomRuleIds = uniqueList(ruleAttribution.invalid_custom_rule_ids || []);
  const customRuleDetails: FindingRuleAttributionDetail[] = Array.isArray(ruleAttribution.custom_rule_details)
    ? ruleAttribution.custom_rule_details
    : [];
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
  const issueDescription = stripReviewSupplementSections(issue?.summary || finding?.summary || "-");
  const issueStrategy = humanizeReviewText(issue?.remediation_strategy || aggregatedStrategies[0] || finding?.remediation_strategy || "-");
  const issueSuggestion = humanizeReviewText(issue?.remediation_suggestion || aggregatedSuggestions[0] || finding?.remediation_suggestion || "-");
  const issueSteps = uniqueList(issue?.remediation_steps).length
    ? uniqueList(issue?.remediation_steps)
    : aggregatedSteps.length
      ? aggregatedSteps
      : uniqueList(finding?.remediation_steps);
  const primaryExpertId = String(issue?.primary_expert_id || issue?.participant_expert_ids?.[0] || "").trim();
  const participantExperts = uniqueList(issue?.participant_expert_ids).filter((item) => item !== primaryExpertId);

  return (
    <Card className="module-card process-sidebar-card process-sidebar-card-md" title="问题详情">
      <div className="process-card-scroll">
        {!issue ? (
          <Empty description="选择一个问题后，这里会展示复核信息、主责角色、证据和参与角色。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <>
            {findingDetailsLoading && finding && !hasFullFindingDetails ? (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="完整关联上下文加载中"
                description="当前先展示问题摘要，完整的代码上下文会在后台补全后自动更新。"
              />
            ) : null}
            {!findingDetailsLoading && findingDetailsError && finding && !hasFullFindingDetails ? (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                message="完整关联上下文暂未加载成功"
                description={`${findingDetailsError}。这不会影响当前问题结论本身。`}
              />
            ) : null}
            <Descriptions column={1} size="small">
              <Descriptions.Item label="问题标题">
                {humanizeReviewText(issue.title || "-")}
              </Descriptions.Item>
              <Descriptions.Item label="问题描述">
                <div>
                  <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                    {issueDescription}
                  </Paragraph>
                </div>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={issue.status === "needs_human" ? "error" : issue.status === "resolved" ? "success" : "processing"}>
                  {humanizeReviewStatus(issue.status)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="严重度">
                <Tag color={issue.severity === "blocker" || issue.severity === "high" ? "error" : "processing"}>
                  {humanizeSeverity(issue.severity)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="复核路径">
                {humanizeReviewStatus(issue.resolution || (issue.needs_human ? "needs_human_review" : "judge_accepted"))}
              </Descriptions.Item>
              <Descriptions.Item label="是否复核">
                <Tag color={issue.needs_debate ? "processing" : "default"}>
                  {issue.needs_debate ? "已复核" : "直接收敛"}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="问题位置">
                {issue.file_path ? `${issue.file_path}:${issue.line_start || 1}` : "-"}
              </Descriptions.Item>
              <Descriptions.Item label="主责角色">
                {humanizeExpertId(primaryExpertId)}
              </Descriptions.Item>
              <Descriptions.Item label="参与角色">
                {participantExperts.map((item) => humanizeExpertId(item)).join("、") || (primaryExpertId ? "仅主责角色参与" : "-")}
              </Descriptions.Item>
              <Descriptions.Item label="证据">
                {issue.evidence.map((item) => humanizeReviewText(item)).join("、") || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="关联发现">
                {issue.finding_ids.join("、") || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="置信度">
                {`${(issue.confidence * 100).toFixed(0)}%`}
              </Descriptions.Item>
              <Descriptions.Item label="问题分类">
                {issue.category_label || issue.normalized_issue_type || issue.finding_type || "-"}
              </Descriptions.Item>
              {issue.confidence_rationale ? (
                <Descriptions.Item label="置信度理由">
                  <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                    {humanizeReviewText(issue.confidence_rationale)}
                  </Paragraph>
                </Descriptions.Item>
              ) : null}
            </Descriptions>

            <div style={{ marginTop: 16 }}>
              <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>证据链</Paragraph>
              {graphEvidenceChain.length ? (
                <Descriptions column={1} size="small">
                  {graphEvidenceChain.slice(0, 8).map((step, index) => (
                    <Descriptions.Item
                      key={`${index}-${step.step || "evidence"}`}
                      label={evidenceStepLabel(step)}
                    >
                      <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                        {humanizeReviewText(evidenceStepSummary(step))}
                      </Paragraph>
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              ) : (
                <Alert
                  type="info"
                  showIcon
                  message="暂无结构化证据链"
                  description="当前问题仍会展示基础证据；后续如果命中 Tree-sitter、工具核验或跨文件关系，会在这里展示 claim、代码锚点、工具核验和置信度变化。"
                />
              )}
            </div>

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
                            {humanizeReviewText(title)}
                          </Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                  ) : null}
                  {aggregatedSummaries.length ? (
                    <Descriptions.Item label="问题说明">
                      <div>
                        {aggregatedSummaries
                          .filter((summary) => summary !== issueDescription)
                          .map((summary) => (
                          <Paragraph key={summary} style={{ marginBottom: 8 }}>
                            {humanizeReviewText(summary)}
                          </Paragraph>
                          ))}
                      </div>
                    </Descriptions.Item>
                  ) : null}
                </Descriptions>
              </div>
            ) : null}

            {issueStrategy !== "-" || issueSuggestion !== "-" || issueSteps.length ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>聚合修复方案</Paragraph>
                <Descriptions column={1} size="small">
                  {issueStrategy !== "-" ? (
                    <Descriptions.Item label="修复思路">
                      <Paragraph style={{ marginBottom: 0 }}>{issueStrategy}</Paragraph>
                    </Descriptions.Item>
                  ) : null}
                  {issueSuggestion !== "-" ? (
                    <Descriptions.Item label="修复建议">
                      <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>{issueSuggestion}</Paragraph>
                    </Descriptions.Item>
                  ) : null}
                  {issueSteps.length ? (
                    <Descriptions.Item label="修复步骤">
                      <div>
                        {issueSteps.map((item, index) => (
                          <Paragraph key={`${index}-${item}`} style={{ marginBottom: 6 }}>
                            {index + 1}. {humanizeReviewText(item)}
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
                  <Descriptions.Item label="检查角色">{humanizeExpertId(finding.expert_id)}</Descriptions.Item>
                  <Descriptions.Item label="路由原因">
                    {codeContext?.routing_reason || "当前未记录路由原因"}
                  </Descriptions.Item>
                  <Descriptions.Item label="目标 hunk">
                    {codeContext?.target_hunk?.hunk_header || "当前未记录 target hunk"}
                  </Descriptions.Item>
                  <Descriptions.Item label="上下文文件">
                    {contextFiles.length ? contextFiles.join("、") : "-"}
                  </Descriptions.Item>
                  <Descriptions.Item label="关联检索方式">
                    <Tag color={graphContextSource === "tree_sitter" ? "success" : graphContextSource === "keyword_search" ? "gold" : "default"}>
                      {evidenceContextSourceLabel(graphContextSource)}
                    </Tag>
                  </Descriptions.Item>
                  {graphRiskLevel || typeof graphRiskScore === "number" ? (
                    <Descriptions.Item label="图谱风险">
                      {[graphRiskLevel || "未分级", typeof graphRiskScore === "number" ? graphRiskScore : ""].filter(Boolean).join(" · ")}
                    </Descriptions.Item>
                  ) : null}
                  {graphSummary ? (
                    <Descriptions.Item label="图谱摘要">
                      <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>{graphSummary}</Paragraph>
                    </Descriptions.Item>
                  ) : null}
                  {graphReviewPriorities.length ? (
                    <Descriptions.Item label="优先检查点">
                      <Space wrap>
                        {graphReviewPriorities.slice(0, 6).map((item, index) => (
                          <Tag key={`${index}-${item}`} color="blue">
                            {humanizeReviewText(item)}
                          </Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                  ) : null}
                  {graphAffectedFlows.length ? (
                    <Descriptions.Item label="候选影响流程">
                      <div>
                        {graphAffectedFlows.slice(0, 5).map((item, index) => (
                          <Paragraph key={`${index}-${item}`} style={{ marginBottom: 6 }}>
                            {humanizeReviewText(item)}
                          </Paragraph>
                        ))}
                      </div>
                    </Descriptions.Item>
                  ) : null}
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
            {finding && (generalRules.length || validCustomRuleIds.length || invalidCustomRuleIds.length) ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>规范与规则依据</Paragraph>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="通用检视依据">
                    {generalRules.length ? (
                      <Space wrap>
                        {generalRules.slice(0, 4).map((rule) => (
                          <Tag key={rule} color="blue">
                            {rule}
                          </Tag>
                        ))}
                      </Space>
                    ) : (
                      <Tag>专家通用规范</Tag>
                    )}
                  </Descriptions.Item>
                  {validCustomRuleIds.length ? (
                    <Descriptions.Item label="附加产品/仓库规则">
                      <Space wrap>
                        {validCustomRuleIds.slice(0, 6).map((ruleId) => {
                          const detail = customRuleDetails.find((item: FindingRuleAttributionDetail) => String(item?.rule_id || "") === ruleId) || {};
                          const label = [ruleId, detail?.title].filter(Boolean).join(" · ");
                          return (
                            <Tag key={ruleId} color="purple">
                              {label}
                            </Tag>
                          );
                        })}
                      </Space>
                    </Descriptions.Item>
                  ) : null}
                  {invalidCustomRuleIds.length ? (
                    <Descriptions.Item label="已清理规则引用">
                      <Space wrap>
                        {invalidCustomRuleIds.slice(0, 4).map((ruleId) => (
                          <Tag key={ruleId} color="gold">
                            {ruleId}
                          </Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                  ) : null}
                  <Descriptions.Item label="规则定位">
                    <Tag color="default">附加规则用于增强产品约束，不作为正式问题唯一准入条件</Tag>
                  </Descriptions.Item>
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
