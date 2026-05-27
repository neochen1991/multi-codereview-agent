import React from "react";
import { Alert, Button, Card, Col, Descriptions, Empty, Row, Space, Tag, Typography } from "antd";

import type {
  DebateIssue,
  EvidenceChainStep,
  IssueFilterDecision,
  ReviewFinding,
  RuleScreeningMetadata,
} from "@/services/api";
import { humanizeExpertId, humanizeReviewStatus, humanizeReviewText, humanizeSeverity } from "@/utils/displayText";
import { buildIssueCallChainGraph } from "./callChainGraph";
import { evidenceStepLabel, evidenceStepSummary } from "./evidenceChainDisplay";
import { cleanUserFacingList, cleanUserFacingText, isConcreteDisplayCode, pickUserFacingText } from "./issueDisplayQuality";
import MermaidBlock from "./MermaidBlock";

const { Paragraph } = Typography;

type Props = {
  finding: ReviewFinding | null;
  issue: DebateIssue | null;
  governanceDecision?: IssueFilterDecision | null;
  ruleScreening?: RuleScreeningMetadata | null;
  findingDetailsLoading?: boolean;
  findingDetailsError?: string;
  onJumpToProcess?: () => void;
};

const severityColor = (severity: string): string => {
  // 结果弹窗里沿用 severity 到颜色的固定映射，降低视觉判断成本。
  if (severity === "blocker" || severity === "critical") return "red";
  if (severity === "high") return "volcano";
  if (severity === "medium") return "gold";
  return "blue";
};

const getMergeImpact = (finding: ReviewFinding, issue: DebateIssue | null): string => {
  // 合并影响会结合 severity 和人工裁决需求共同判断。
  if (!issue) return "仅作为检视发现保留";
  if (issue?.needs_human && issue.status !== "resolved") return "阻塞合并";
  if (["blocker", "critical", "high"].includes(finding.severity)) return "建议修复后再合并";
  return "可跟随后续修复计划";
};

const getPriority = (finding: ReviewFinding): string => {
  // 单条 finding 的优先级取决于其严重等级。
  if (["blocker", "critical"].includes(finding.severity)) return "P0";
  if (finding.severity === "high") return "P1";
  if (finding.severity === "medium") return "P2";
  return "P3";
};

const getFindingTypeLabel = (findingType?: string): string => {
  // 将结构化 finding_type 转换成结果页展示使用的中文标签。
  if (findingType === "direct_defect") return "直接缺陷";
  if (findingType === "test_gap") return "测试缺口";
  if (findingType === "design_concern") return "设计关注";
  return "待验证风险";
};

const getDesignAlignmentLabel = (status?: string): string => {
  if (status === "aligned") return "与设计一致";
  if (status === "partially_aligned") return "部分偏离设计";
  if (status === "misaligned") return "与设计冲突";
  return "设计上下文不足";
};

const getDesignAlignmentColor = (status?: string): string => {
  if (status === "aligned") return "success";
  if (status === "partially_aligned") return "gold";
  if (status === "misaligned") return "error";
  return "default";
};

const hasDesignEvidence = (finding: ReviewFinding): boolean =>
  Boolean(
    (finding.design_doc_titles || []).length ||
      (finding.matched_design_points || []).length ||
      (finding.missing_design_points || []).length ||
      (finding.extra_implementation_points || []).length ||
      (finding.design_conflicts || []).length,
  );

const evidenceChainFor = (finding: ReviewFinding, issue: DebateIssue | null): EvidenceChainStep[] => {
  const codeContext = finding.code_context || {};
  if (issue?.evidence_chain?.length) return issue.evidence_chain;
  if (finding.evidence_chain?.length) return finding.evidence_chain;
  return codeContext.code_graph_evidence_chain || [];
};

const renderCodeLines = (
  codeExcerpt: string,
  targetLine: number,
  lineRefs: React.MutableRefObject<Record<number, HTMLDivElement | null>>,
) => {
  // 问题代码需要高亮目标行，并兼容增删行配色。
  const lines = codeExcerpt.split("\n").filter(Boolean);
  return (
    <div className="review-code-frame">
      {lines.map((line, index) => {
        const match = line.match(/^\s*(\d+)\s+\|/);
        const lineNumber = match ? Number(match[1]) : null;
        const isTarget = lineNumber === targetLine;
        const isAdded = line.includes("| +");
        const isRemoved = line.includes("| -");
        return (
          <div
            key={`${line}-${index}`}
            ref={(node) => {
              if (lineNumber !== null) {
                lineRefs.current[lineNumber] = node;
              }
            }}
            className={`review-code-line ${isTarget ? "review-code-line-target" : ""} ${isAdded ? "review-code-line-added" : ""} ${isRemoved ? "review-code-line-removed" : ""}`}
          >
            <code>{line}</code>
          </div>
        );
      })}
    </div>
  );
};

const renderSuggestedCode = (code: string) => (
  // 建议代码单独使用深色面板，和“当前代码”形成明确视觉区分。
  <div className="review-suggested-code-frame">
    <pre className="review-suggested-code-pre">
      <code>{code}</code>
    </pre>
  </div>
);

// 结果弹窗负责把单条 finding 渲染成正式 Code Review 详情视图。
const CodeReviewConclusionPanel: React.FC<Props> = ({
  finding,
  issue,
  ruleScreening,
  findingDetailsLoading = false,
  findingDetailsError = "",
  onJumpToProcess,
}) => {
  const lineRefs = React.useRef<Record<number, HTMLDivElement | null>>({});

  React.useEffect(() => {
    // 打开详情后自动滚到目标代码行，减少手动查找成本。
    if (!finding) return;
    const targetNode = lineRefs.current[finding.line_start];
    if (targetNode) {
      targetNode.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [finding]);

  if (!finding) {
    return (
      <Card className="module-card" title="问题详情">
        <Empty description="从左侧问题清单中选择一条结论，查看对应代码与修复建议。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      </Card>
    );
  }

  const codeContext = finding.code_context;
  const displayFilePath = String(issue?.file_path || finding.file_path || "").trim();
  const displayLineStart = Number(issue?.line_start || finding.line_start || 1);
  const displaySeverity = String(issue?.severity || finding.severity || "medium");
  const displayConfidence = typeof issue?.confidence === "number" ? issue.confidence : finding.confidence;
  const displayExpertId = String(issue?.primary_expert_id || issue?.participant_expert_ids?.[0] || finding.expert_id || "").trim();
  const displayCategory = issue?.category_label || issue?.normalized_issue_type || finding.category_label || finding.normalized_issue_type || finding.finding_type;
  const issueSummary = pickUserFacingText(
    [issue?.summary, finding.summary, issue?.title, finding.title],
    "当前问题已定位到代码改动，请结合下方代码锚点和证据处理。",
  );
  const issueStrategy = pickUserFacingText([issue?.remediation_strategy, finding.remediation_strategy]);
  const issueSuggestion = pickUserFacingText([issue?.remediation_suggestion, finding.remediation_suggestion]);
  const issueSteps = cleanUserFacingList(issue?.remediation_steps?.length ? issue.remediation_steps : finding.remediation_steps);
  const currentCode =
    String(issue?.current_code || "").trim() ||
    finding.code_excerpt ||
    codeContext?.target_hunk?.excerpt ||
    codeContext?.problem_source_context?.snippet ||
    codeContext?.source_file_context ||
    codeContext?.primary_context?.snippet;
  const suggestedCode = (() => {
    const value = String(issue?.suggested_code || finding.suggested_code || "").trim();
    return isConcreteDisplayCode(value) ? value : "";
  })();
  const hasFullDetails = Boolean(
    finding.code_excerpt ||
      finding.suggested_code ||
      (finding.code_context && Object.keys(finding.code_context).length > 0),
  );
  const evidenceChain = evidenceChainFor(finding, issue);
  const callChainGraph = buildIssueCallChainGraph(finding, issue, evidenceChain);
  const displayConfidenceRationale = cleanUserFacingText(finding.confidence_rationale || issue?.confidence_rationale || "");
  const displayMatchedRules = cleanUserFacingList(finding.matched_rules || []);
  const displayViolatedGuidelines = cleanUserFacingList(finding.violated_guidelines || []);
  const displayRuleBasis = pickUserFacingText([
    finding.rule_based_reasoning,
    issue?.evidence?.join("；"),
    issue?.consistency_check_summary,
  ]);
  const displayEvidenceChain = evidenceChain
    .map((step, index) => ({
      key: `${index}-${step.step || "evidence"}`,
      label: evidenceStepLabel(step),
      summary: cleanUserFacingText(evidenceStepSummary(step)),
    }))
    .filter((step) => step.summary);

  return (
    <Card className="module-card" title="问题详情">
      {findingDetailsLoading && !hasFullDetails ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="完整代码上下文加载中"
          description="首屏先展示轻量结果，完整代码片段、源码上下文和建议代码会在后台补全后自动出现。"
        />
      ) : null}
      {!findingDetailsLoading && findingDetailsError && !hasFullDetails ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="完整代码上下文暂未加载成功"
          description={`${findingDetailsError}。当前先展示问题结论，不影响已完成任务的审核结果。`}
        />
      ) : null}
      <Descriptions
        column={1}
        size="small"
        items={[
          {
            key: "location",
            label: "代码位置",
            children: `${displayFilePath}:${displayLineStart}`,
          },
          {
            key: "severity",
            label: "问题级别",
            children: <Tag color={severityColor(displaySeverity)}>{humanizeSeverity(displaySeverity)}</Tag>,
          },
          {
            key: "expert",
            label: "检查角色",
            children: <Tag color="geekblue">{humanizeExpertId(displayExpertId)}</Tag>,
          },
          {
            key: "status",
            label: "裁决状态",
            children: (
              <>
                {issue ? (
                  <>
                    <Tag color={issue.status === "resolved" ? "success" : issue.needs_human ? "error" : "processing"}>
                      {humanizeReviewStatus(issue.status)}
                    </Tag>
                    <Tag>{humanizeReviewStatus(issue.resolution || "pending")}</Tag>
                  </>
                ) : (
                  <>
                    <Tag color="default">仅保留发现</Tag>
                    <Tag>未升级为正式问题</Tag>
                  </>
                )}
              </>
            ),
          },
          {
            key: "finding_type",
            label: "问题类型",
            children: (
              <Space wrap>
                <Tag color="purple">{getFindingTypeLabel(finding.finding_type)}</Tag>
                {displayCategory ? <Tag color="cyan">{humanizeReviewText(displayCategory)}</Tag> : null}
              </Space>
            ),
          },
          {
            key: "confidence",
            label: "置信度",
            children: `${(displayConfidence * 100).toFixed(0)}%`,
          },
          ...(displayConfidenceRationale
            ? [
                {
                  key: "confidence_rationale",
                  label: "置信度理由",
                  children: (
                    <Paragraph style={{ marginBottom: 0 }} ellipsis={{ rows: 3, expandable: true, symbol: "展开" }}>
                      {displayConfidenceRationale}
                    </Paragraph>
                  ),
                },
              ]
            : []),
          {
            key: "merge_impact",
            label: "合并影响",
            children: getMergeImpact(finding, issue),
          },
          {
            key: "priority",
            label: "建议优先级",
            children: getPriority(finding),
          },
        ]}
      />

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>证据链</Paragraph>
        {displayEvidenceChain.length ? (
          <Descriptions
            column={1}
            size="small"
            items={displayEvidenceChain.slice(0, 10).map((step) => ({
              key: step.key,
              label: step.label,
              children: (
                <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                  {step.summary}
                </Paragraph>
              ),
            }))}
          />
        ) : (
          <Alert
            type="info"
            showIcon
            message="暂无结构化证据链"
            description="当前问题仍保留基础证据；如果命中 Tree-sitter、工具核验或跨文件关系，会在这里展示问题主张、代码锚点、工具核验和置信度变化。"
          />
        )}
      </div>

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>调用关系依据</Paragraph>
        {callChainGraph ? (
          <div className="issue-call-chain-panel">
            <MermaidBlock chart={callChainGraph.chart} />
            <Paragraph className="issue-call-chain-summary">
              {callChainGraph.summary}
            </Paragraph>
            <Space wrap>
              <Tag color="green">{callChainGraph.sourceLabel}</Tag>
              {callChainGraph.nodes.slice(0, 6).map((node) => (
                <Tag key={node}>{node}</Tag>
              ))}
            </Space>
          </div>
        ) : (
          <Alert
            type="info"
            showIcon
            message="暂无可视化调用链"
            description="当前证据尚未形成稳定的上游到下游调用关系；如 Tree-sitter 或 GitNexus 后续命中调用链，会在这里以 Mermaid 图展示。"
          />
        )}
      </div>

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>问题说明</Paragraph>
        <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>{humanizeReviewText(issueSummary)}</Paragraph>
      </div>

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>命中的规范条款</Paragraph>
        <Space wrap>
          {displayMatchedRules.length ? (
            displayMatchedRules.map((rule) => (
              <Tag key={rule} color="blue">
                {rule}
              </Tag>
            ))
          ) : (
            <Tag color="default">未命中具体条款</Tag>
          )}
        </Space>
      </div>

      {displayViolatedGuidelines.length ? (
        <div style={{ marginTop: 16 }}>
          <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>违反的规范要求</Paragraph>
          <Space wrap>
            {displayViolatedGuidelines.map((rule) => (
              <Tag key={rule} color="volcano">
                {rule}
              </Tag>
            ))}
          </Space>
        </div>
      ) : null}

      {displayRuleBasis ? (
        <div style={{ marginTop: 16 }}>
          <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>规范依据</Paragraph>
          <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
            {displayRuleBasis}
          </Paragraph>
        </div>
      ) : null}

      {ruleScreening && ruleScreening.total_rules > 0 ? (
        <div style={{ marginTop: 16 }}>
          <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>规则覆盖报告</Paragraph>
          <Space wrap style={{ marginBottom: 10 }}>
            <Tag color="purple">{`总规则 ${ruleScreening.total_rules}`}</Tag>
            <Tag>{`启用 ${ruleScreening.enabled_rules || ruleScreening.total_rules}`}</Tag>
            <Tag color="magenta">{`带入审查 ${ruleScreening.matched_rule_count}`}</Tag>
            <Tag color="volcano">{`强命中 ${ruleScreening.must_review_count}`}</Tag>
            <Tag color="blue">{`候选 ${ruleScreening.possible_hit_count}`}</Tag>
          </Space>
          {ruleScreening.matched_rules_for_llm?.length ? (
            <>
              <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>本轮带入审查的规则</Paragraph>
              <ul className="review-remediation-steps">
                {ruleScreening.matched_rules_for_llm.map((item) => (
                  <li key={item.rule_id || item.title}>
                    <strong>{`${item.priority ? `[${item.priority}] ` : ""}${item.title || item.rule_id}`}</strong>
                    {item.reason ? `：${item.reason}` : ""}
                    {item.matched_terms?.length ? ` · 关键词: ${item.matched_terms.join(" / ")}` : ""}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <Paragraph style={{ marginBottom: 0 }}>本轮未命中需要带入深审的规则卡。</Paragraph>
          )}
        </div>
      ) : null}

      {hasDesignEvidence(finding) ? (
        <div style={{ marginTop: 16 }}>
          <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>详细设计一致性</Paragraph>
          <Space wrap style={{ marginBottom: 10 }}>
            <Tag color={getDesignAlignmentColor(finding.design_alignment_status)}>
              {getDesignAlignmentLabel(finding.design_alignment_status)}
            </Tag>
            {(finding.design_doc_titles || []).map((title) => (
              <Tag key={title} color="purple">
                {title}
              </Tag>
            ))}
          </Space>
          {(finding.matched_design_points || []).length ? (
            <>
              <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>已实现设计点</Paragraph>
              <ul className="review-remediation-steps">
                {(finding.matched_design_points || []).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          ) : null}
          {(finding.missing_design_points || []).length ? (
            <>
              <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>缺失设计点</Paragraph>
              <ul className="review-remediation-steps">
                {(finding.missing_design_points || []).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          ) : null}
          {(finding.extra_implementation_points || []).length ? (
            <>
              <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>超出设计的实现</Paragraph>
              <ul className="review-remediation-steps">
                {(finding.extra_implementation_points || []).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          ) : null}
          {(finding.design_conflicts || []).length ? (
            <>
              <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>设计冲突点</Paragraph>
              <ul className="review-remediation-steps">
                {(finding.design_conflicts || []).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>修改思路</Paragraph>
        <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
          {issueStrategy || "按当前代码锚点修正问题，并补充覆盖该风险的回归测试。"}
        </Paragraph>
      </div>

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>修复建议</Paragraph>
        <Paragraph style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
          {issueSuggestion || "优先修复问题说明指向的代码片段，避免引入与当前问题无关的改动。"}
        </Paragraph>
      </div>

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 10, fontWeight: 600 }}>建议修改步骤</Paragraph>
        <ol className="review-remediation-steps">
          {issueSteps.length ? (
            issueSteps.map((step, index) => <li key={`${index}-${step}`}>{humanizeReviewText(step)}</li>)
          ) : (
            <li>先按当前代码锚点修复问题，再补充对应的单元或集成回归测试。</li>
          )}
        </ol>
      </div>

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 10, fontWeight: 600 }}>建议代码修改方案</Paragraph>
        <Row gutter={[16, 16]}>
          <Col xs={24} xl={12}>
            <div className="review-code-panel">
              <div className="review-code-panel-header">
                <span>当前代码</span>
                <Tag>{displayFilePath}:{displayLineStart}</Tag>
              </div>
              {currentCode ? (
                renderCodeLines(currentCode, displayLineStart, lineRefs)
              ) : (
                <Alert type="warning" showIcon message="当前代码片段缺失" description="后端未返回稳定代码锚点，本条问题不应直接提交，请先补齐证据。" />
              )}
            </div>
          </Col>
          <Col xs={24} xl={12}>
            <div className="review-code-panel">
              <div className="review-code-panel-header">
                <span>建议修改后代码</span>
                {finding.suggested_code_language ? <Tag color="blue">{finding.suggested_code_language}</Tag> : null}
              </div>
              {suggestedCode ? (
                renderSuggestedCode(suggestedCode)
              ) : (
                <Alert type="warning" showIcon message="建议修改后代码缺失" description="系统没有生成可直接落地的代码片段，本条问题需要回到裁决链路补全后再提交。" />
              )}
            </div>
          </Col>
        </Row>
      </div>

      <div style={{ marginTop: 16 }}>
        <Button onClick={onJumpToProcess} disabled={!onJumpToProcess}>
          查看对应审查过程
        </Button>
      </div>
    </Card>
  );
};

export default CodeReviewConclusionPanel;
