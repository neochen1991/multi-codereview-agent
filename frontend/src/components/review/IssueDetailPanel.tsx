import React from "react";
import { Alert, Card, Descriptions, Empty, Space, Tag, Typography } from "antd";

import type { DebateIssue, EvidenceChainStep, FindingRuleAttributionDetail, ReviewFinding } from "@/services/api";
import {
  humanizeExpertId,
  humanizeReviewStatus,
  humanizeReviewText,
  humanizeSeverity,
} from "@/utils/displayText";
import { evidenceContextSourceLabel, evidenceStepLabel, evidenceStepSummary } from "./evidenceChainDisplay";
import {
  buildReadableFixSummary,
  buildReadableIssueSummary,
  buildReadableIssueTitle,
  cleanUserFacingList,
  cleanUserFacingText,
  issueTypeDisplayLabel,
  pickUserFacingText,
} from "./issueDisplayQuality";

const { Paragraph } = Typography;

const uniqueList = (values?: string[]) => Array.from(new Set((values || []).map((item) => String(item || "").trim()).filter(Boolean)));

const normalizePath = (value?: string) => String(value || "").replace(/\\/g, "/").trim().toLowerCase();

const isSameReviewPath = (left?: string, right?: string) => {
  const normalizedLeft = normalizePath(left);
  const normalizedRight = normalizePath(right);
  if (!normalizedLeft || !normalizedRight) return true;
  return (
    normalizedLeft === normalizedRight ||
    normalizedLeft.endsWith(`/${normalizedRight}`) ||
    normalizedRight.endsWith(`/${normalizedLeft}`)
  );
};

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
  const issueFilePath = String(issue?.file_path || "");
  const findingFilePath = String(finding?.file_path || "");
  const issueFileName = issueFilePath.split(/[\\/]/).pop() || "";
  const issueClassName = issueFileName.replace(/\.[^.]+$/, "");
  const rawFindingRuleReasoning = String(finding?.rule_based_reasoning || "");
  const changedClassMarkers = [
    { fileName: "CourseCreator.java", className: "CourseCreator" },
    { fileName: "BulkEnrollmentService.java", className: "BulkEnrollmentService" },
    { fileName: "PaymentSettlementService.java", className: "PaymentSettlementService" },
  ];
  const otherChangedFileMentioned = changedClassMarkers.some(
    ({ fileName, className }) =>
      className !== issueClassName &&
      fileName !== issueFileName &&
      (rawFindingRuleReasoning.includes(fileName) || rawFindingRuleReasoning.includes(`${className}.`) || rawFindingRuleReasoning.includes(`${className} `)),
  );
  const alignedFinding = finding && isSameReviewPath(issueFilePath, findingFilePath) && !otherChangedFileMentioned ? finding : null;
  const codeContext = alignedFinding?.code_context;
  const codeGraphSourceSummary = codeContext?.code_graph_source_summary || {};
  const codeGraphMinimalContext = codeContext?.code_graph_minimal_context || {};
  const codeGraphImpactAnalysis = codeContext?.code_graph_impact_analysis || {};
  const graphRiskLevel = String(codeGraphMinimalContext.risk_level || codeGraphImpactAnalysis.risk_level || "").trim();
  const graphRiskScore = codeGraphMinimalContext.risk_score ?? codeGraphImpactAnalysis.risk_score;
  const graphContextSource =
    alignedFinding?.context_source ||
    String(codeGraphSourceSummary.primary_source || codeGraphSourceSummary.context_source || "").trim();
  const graphSummary = String(codeGraphMinimalContext.summary || codeGraphImpactAnalysis.summary || "").trim();
  const graphReviewPriorities = normalizeUnknownList(
    codeGraphMinimalContext.review_priorities || codeGraphImpactAnalysis.review_priorities,
  );
  const graphAffectedFlows = normalizeUnknownList(
    codeGraphMinimalContext.affected_flows || codeGraphImpactAnalysis.affected_flows,
  );
  const graphEvidenceChain = issue?.evidence_chain?.length ? issue.evidence_chain : [];
  const contextFiles = codeContext?.context_files || alignedFinding?.context_files || [];
  const inputCompleteness = codeContext?.input_completeness;
  const reviewInputs = codeContext?.review_inputs;
  const ruleAttribution = codeContext?.rule_attribution || {};
  const generalRules = uniqueList(ruleAttribution.general_rules || []);
  const validCustomRuleIds = uniqueList(ruleAttribution.valid_custom_rule_ids || []);
  const invalidCustomRuleIds = uniqueList(ruleAttribution.invalid_custom_rule_ids || []);
  const customRuleDetails: FindingRuleAttributionDetail[] = Array.isArray(ruleAttribution.custom_rule_details)
    ? ruleAttribution.custom_rule_details
    : [];
  const aggregatedTitles = cleanUserFacingList(issue?.aggregated_titles);
  const aggregatedSummaries = cleanUserFacingList(issue?.aggregated_summaries);
  const aggregatedStrategies = cleanUserFacingList(issue?.aggregated_remediation_strategies);
  const aggregatedSuggestions = cleanUserFacingList(issue?.aggregated_remediation_suggestions);
  const aggregatedSteps = cleanUserFacingList(issue?.aggregated_remediation_steps);
  const findingMatchedRules = uniqueList(alignedFinding?.matched_rules);
  const findingViolatedGuidelines = uniqueList(alignedFinding?.violated_guidelines);
  const issueType = String(issue?.normalized_issue_type || "").trim().toLowerCase();
  const preferIssueEvidenceForBasis = [
    "n_plus_one",
    "loop_call_amplification",
    "bulk_processing_boundary_missing",
    "comment_contract_unimplemented",
    "course_creation_semantics",
    "aggregate_factory_bypass",
    "aggregate_factory_bypassed",
    "exception_swallowed",
    "exception_semantics_weakened",
    "lock_guard_removed",
    "query_bound_removed",
    "query_boundary_missing",
  ].includes(issueType);
  const findingRuleReasoning = cleanUserFacingText(alignedFinding?.rule_based_reasoning || "");
  const displayFindingRuleReasoning = preferIssueEvidenceForBasis ? "" : findingRuleReasoning;
  const hasFullFindingDetails = Boolean(
    alignedFinding?.code_excerpt ||
      alignedFinding?.suggested_code ||
      (alignedFinding?.code_context && Object.keys(alignedFinding.code_context).length > 0),
  );
  const issueTitle = issue
    ? buildReadableIssueTitle({
        ...issue,
        summary: issue.summary || alignedFinding?.summary,
      })
    : "";
  const issueDescription = issue
    ? buildReadableIssueSummary({
        ...issue,
        summary: pickUserFacingText([issue.summary, alignedFinding?.summary, issue.title]),
      })
    : "";
  const issueStrategy = pickUserFacingText([
    issue?.remediation_strategy,
    aggregatedStrategies[0],
    alignedFinding?.remediation_strategy,
  ]);
  const issueSuggestion = pickUserFacingText([
    issue?.remediation_suggestion,
    aggregatedSuggestions[0],
    alignedFinding?.remediation_suggestion,
  ]);
  const issueSteps = cleanUserFacingList(issue?.remediation_steps).length
    ? cleanUserFacingList(issue?.remediation_steps)
    : aggregatedSteps.length
      ? aggregatedSteps
      : cleanUserFacingList(alignedFinding?.remediation_steps);
  const primaryExpertId = String(issue?.primary_expert_id || issue?.participant_expert_ids?.[0] || "").trim();
  const participantExperts = uniqueList(issue?.participant_expert_ids).filter((item) => item !== primaryExpertId);
  const showFindingRuleDiagnostics = false;
  const displayAggregatedTitles = aggregatedTitles.filter((title) => humanizeReviewText(title) !== humanizeReviewText(issue?.title || ""));
  const displayAggregatedSummaries = aggregatedSummaries.filter(
    (summary) => humanizeReviewText(summary) !== humanizeReviewText(issueDescription),
  );
  const displayEvidence = cleanUserFacingList(issue?.evidence);
  const displayConfidenceRationale = cleanUserFacingText(issue?.confidence_rationale || "");
  const displayGraphSummary = cleanUserFacingText(graphSummary);
  const evidenceIssueContext = JSON.stringify({ issue, alignedFinding });
  const displayEvidenceChain = graphEvidenceChain
    .map((step, index) => ({
      key: `${index}-${step.step || "evidence"}`,
      label: evidenceStepLabel(step),
      summary: cleanUserFacingText(evidenceStepSummary(step, evidenceIssueContext)),
    }))
    .filter((step) => step.summary);
  const issueTypeLabel = issue
    ? issueTypeDisplayLabel(issue.category_label, issue.normalized_issue_type, issue.finding_type)
    : "-";
  const fixSummary = issue
    ? buildReadableFixSummary({
        ...issue,
        remediation_strategy: issueStrategy,
        remediation_suggestion: issueSuggestion,
        remediation_steps: issueSteps,
      })
    : "";

  return (
    <Card className="module-card process-sidebar-card process-sidebar-card-md" title="问题详情">
      <div className="process-card-scroll">
        {!issue ? (
          <Empty description="选择一个问题后，这里会展示复核信息、主责角色、证据和参与角色。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <>
            {findingDetailsLoading && alignedFinding && !hasFullFindingDetails ? (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="完整关联上下文加载中"
                description="当前先展示问题摘要，完整的代码上下文会在后台补全后自动更新。"
              />
            ) : null}
            {!findingDetailsLoading && findingDetailsError && alignedFinding && !hasFullFindingDetails ? (
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
                {issueTitle || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="问题说明">
                <div>
                  <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                    {issueDescription}
                  </Paragraph>
                </div>
              </Descriptions.Item>
              <Descriptions.Item label="处理状态">
                <Tag color={issue.status === "needs_human" ? "error" : issue.status === "resolved" ? "success" : "processing"}>
                  {humanizeReviewStatus(issue.status)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="严重度">
                <Tag color={issue.severity === "blocker" || issue.severity === "high" ? "error" : "processing"}>
                  {humanizeSeverity(issue.severity)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="复核结果">
                {humanizeReviewStatus(issue.resolution || (issue.needs_human ? "needs_human_review" : "judge_accepted"))}
              </Descriptions.Item>
              <Descriptions.Item label="收敛方式">
                <Tag color={issue.needs_debate ? "processing" : "default"}>
                  {issue.needs_debate ? "多角色复核后确认" : "单角色证据直接确认"}
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
              <Descriptions.Item label="主要证据">
                {displayEvidence.join("、") || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="来源发现">
                {issue.finding_ids.join("、") || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="置信度">
                {`${(issue.confidence * 100).toFixed(0)}%`}
              </Descriptions.Item>
              <Descriptions.Item label="问题类型">
                {issueTypeLabel}
              </Descriptions.Item>
              {findingMatchedRules.length || findingViolatedGuidelines.length || displayFindingRuleReasoning || displayEvidence.length ? (
                <Descriptions.Item label="为什么认为这是问题">
                  <div>
                    {findingMatchedRules.length ? (
                      <Space wrap style={{ marginBottom: 6 }}>
                        {findingMatchedRules.map((rule) => (
                          <Tag key={rule} color="blue">
                            {rule}
                          </Tag>
                        ))}
                      </Space>
                    ) : null}
                    {findingViolatedGuidelines.length ? (
                      <div style={{ marginBottom: displayFindingRuleReasoning ? 6 : 0 }}>
                        {findingViolatedGuidelines.map((rule) => (
                          <Tag key={rule} color="volcano">
                            {humanizeReviewText(rule)}
                          </Tag>
                        ))}
                      </div>
                    ) : null}
                    {displayFindingRuleReasoning ? (
                      <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                        {displayFindingRuleReasoning}
                      </Paragraph>
                    ) : null}
                    {!displayFindingRuleReasoning && displayEvidence.length ? (
                      <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                        {displayEvidence.join("；")}
                      </Paragraph>
                    ) : null}
                  </div>
                </Descriptions.Item>
              ) : null}
              {displayConfidenceRationale ? (
                <Descriptions.Item label="判断依据">
                  <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                    {displayConfidenceRationale}
                  </Paragraph>
                </Descriptions.Item>
              ) : null}
            </Descriptions>

            <div style={{ marginTop: 16 }}>
              <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>系统核对过的证据</Paragraph>
              {displayEvidenceChain.length ? (
                <Descriptions column={1} size="small">
                  {displayEvidenceChain.slice(0, 8).map((step) => (
                    <Descriptions.Item
                      key={step.key}
                      label={step.label}
                    >
                      <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                        {step.summary}
                      </Paragraph>
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              ) : (
                <Alert
                  type="info"
                  showIcon
                  message="暂无结构化证据链"
                  description="当前先展示基础证据；如果后续补充到调用链、工具核验或跨文件关系，会继续在这里展开。"
                />
              )}
            </div>

            {Object.keys(confidenceBreakdown).length ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>可信度来源</Paragraph>
                <Descriptions column={1} size="small">
                  {"base_weighted_confidence" in confidenceBreakdown ? (
                    <Descriptions.Item label="基础判断">
                      {String(confidenceBreakdown.base_weighted_confidence)}
                    </Descriptions.Item>
                  ) : null}
                  {"consensus_bonus" in confidenceBreakdown ? (
                    <Descriptions.Item label="多角色一致性">{String(confidenceBreakdown.consensus_bonus)}</Descriptions.Item>
                  ) : null}
                  {"evidence_bonus" in confidenceBreakdown ? (
                    <Descriptions.Item label="证据充分度">{String(confidenceBreakdown.evidence_bonus)}</Descriptions.Item>
                  ) : null}
                  {"verification_bonus" in confidenceBreakdown ? (
                    <Descriptions.Item label="工具核验">{String(confidenceBreakdown.verification_bonus)}</Descriptions.Item>
                  ) : null}
                  {"hypothesis_penalty" in confidenceBreakdown ? (
                    <Descriptions.Item label="推测成分扣减">{String(confidenceBreakdown.hypothesis_penalty)}</Descriptions.Item>
                  ) : null}
                </Descriptions>
              </div>
            ) : null}

            {displayAggregatedTitles.length || displayAggregatedSummaries.length ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>合并在一起的相近发现</Paragraph>
                <Descriptions column={1} size="small">
                  {displayAggregatedTitles.length ? (
                    <Descriptions.Item label="问题标题">
                      <Space wrap>
                        {displayAggregatedTitles.map((title) => (
                          <Tag key={title} color="blue">
                            {humanizeReviewText(title)}
                          </Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                  ) : null}
                  {displayAggregatedSummaries.length ? (
                    <Descriptions.Item label="问题说明">
                      <div>
                        {displayAggregatedSummaries.map((summary) => (
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

            {issueStrategy || issueSuggestion || issueSteps.length ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>建议怎么改</Paragraph>
                <Descriptions column={1} size="small">
                  {fixSummary ? (
                    <Descriptions.Item label="处理重点">
                      <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>{fixSummary}</Paragraph>
                    </Descriptions.Item>
                  ) : null}
                  {issueStrategy ? (
                    <Descriptions.Item label="修复思路">
                      <Paragraph style={{ marginBottom: 0 }}>{issueStrategy}</Paragraph>
                    </Descriptions.Item>
                  ) : null}
                  {issueSuggestion ? (
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

            {alignedFinding ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>系统参考的代码上下文</Paragraph>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="检查角色">{humanizeExpertId(alignedFinding.expert_id)}</Descriptions.Item>
                  {cleanUserFacingText(codeContext?.routing_reason || "") ? (
                    <Descriptions.Item label="为什么由该角色检查">
                      {cleanUserFacingText(codeContext?.routing_reason || "")}
                    </Descriptions.Item>
                  ) : null}
                  {cleanUserFacingText(codeContext?.target_hunk?.hunk_header || "") ? (
                    <Descriptions.Item label="命中的代码片段">
                      {cleanUserFacingText(codeContext?.target_hunk?.hunk_header || "")}
                    </Descriptions.Item>
                  ) : null}
                  <Descriptions.Item label="上下文文件">
                    {contextFiles.length ? contextFiles.join("、") : "-"}
                  </Descriptions.Item>
                  <Descriptions.Item label="上下文来源">
                    <Tag color={graphContextSource === "tree_sitter" ? "success" : graphContextSource === "keyword_search" ? "gold" : "default"}>
                      {evidenceContextSourceLabel(graphContextSource)}
                    </Tag>
                  </Descriptions.Item>
                  {graphRiskLevel || typeof graphRiskScore === "number" ? (
                    <Descriptions.Item label="关联影响等级">
                      {[graphRiskLevel || "未分级", typeof graphRiskScore === "number" ? graphRiskScore : ""].filter(Boolean).join(" · ")}
                    </Descriptions.Item>
                  ) : null}
                  {displayGraphSummary ? (
                    <Descriptions.Item label="关联影响说明">
                      <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>{displayGraphSummary}</Paragraph>
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
                    <Descriptions.Item label="可能受影响的流程">
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

            {alignedFinding && (inputCompleteness || reviewInputs) ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>本次审查使用的输入</Paragraph>
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
            {showFindingRuleDiagnostics && alignedFinding &&
            (generalRules.length ||
              validCustomRuleIds.length ||
              invalidCustomRuleIds.length ||
              findingMatchedRules.length ||
              findingViolatedGuidelines.length ||
              findingRuleReasoning) ? (
              <div style={{ marginTop: 16 }}>
                <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>规范与规则依据</Paragraph>
                <Descriptions column={1} size="small">
                  {findingMatchedRules.length ? (
                    <Descriptions.Item label="命中的规范条款">
                      <Space wrap>
                        {findingMatchedRules.map((rule) => (
                          <Tag key={rule} color="blue">
                            {rule}
                          </Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                  ) : null}
                  {findingViolatedGuidelines.length ? (
                    <Descriptions.Item label="违反的规范要求">
                      <Space wrap>
                        {findingViolatedGuidelines.map((rule) => (
                          <Tag key={rule} color="volcano">
                            {humanizeReviewText(rule)}
                          </Tag>
                        ))}
                      </Space>
                    </Descriptions.Item>
                  ) : null}
                  {findingRuleReasoning ? (
                    <Descriptions.Item label="规范依据说明">
                      <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>{findingRuleReasoning}</Paragraph>
                    </Descriptions.Item>
                  ) : null}
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
