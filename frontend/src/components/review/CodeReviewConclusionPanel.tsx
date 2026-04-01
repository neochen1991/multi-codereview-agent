import React from "react";
import { Button, Card, Col, Descriptions, Empty, Row, Space, Tag, Typography } from "antd";

import type {
  DebateIssue,
  FindingCodeContextSnippet,
  IssueFilterDecision,
  ReviewFinding,
  RuleScreeningMetadata,
} from "@/services/api";

const { Paragraph } = Typography;

type Props = {
  finding: ReviewFinding | null;
  issue: DebateIssue | null;
  governanceDecision?: IssueFilterDecision | null;
  ruleScreening?: RuleScreeningMetadata | null;
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
  if (!issue) return "仅作为 finding 保留";
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

const renderContextSnippet = (title: string, snippet: FindingCodeContextSnippet | null | undefined) => {
  if (!snippet?.snippet) return null;
  return (
    <div style={{ marginTop: 16 }}>
      <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>{title}</Paragraph>
      <div className="review-code-panel">
        <div className="review-code-panel-header">
          <span>{snippet.path || "关联代码"}</span>
          {snippet.line_start ? <Tag>{`L${snippet.line_start}`}</Tag> : null}
        </div>
        <div className="review-code-frame">
          {snippet.snippet.split("\n").filter(Boolean).map((line, index) => (
            <div key={`${title}-${index}`} className="review-code-line">
              <code>{line}</code>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

// 结果弹窗负责把单条 finding 渲染成正式 Code Review 详情视图。
const CodeReviewConclusionPanel: React.FC<Props> = ({
  finding,
  issue,
  governanceDecision,
  ruleScreening,
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
  const currentCode =
    codeContext?.problem_source_context?.snippet ||
    codeContext?.source_file_context ||
    codeContext?.primary_context?.snippet ||
    finding.code_excerpt;
  const targetDiff = codeContext?.target_file_full_diff || codeContext?.target_hunk?.excerpt || "";
  const relatedSourceSnippets = codeContext?.related_source_snippets || [];
  const fallbackRelatedContexts = codeContext?.related_contexts || [];
  const relatedContexts = (relatedSourceSnippets.length > 0 ? relatedSourceSnippets : fallbackRelatedContexts).filter(
    (item) => item?.snippet,
  );
  const currentClassContext = codeContext?.current_class_context;
  const parentContractContexts = (codeContext?.parent_contract_contexts || []).filter((item) => item?.snippet);
  const callerContexts = (codeContext?.caller_contexts || []).filter((item) => item?.snippet);
  const calleeContexts = (codeContext?.callee_contexts || []).filter((item) => item?.snippet);
  const domainModelContexts = (codeContext?.domain_model_contexts || []).filter((item) => item?.snippet);
  const persistenceContexts = (codeContext?.persistence_contexts || []).filter((item) => item?.snippet);
  const transactionContext = codeContext?.transaction_context;
  const symbolContexts = (codeContext?.symbol_contexts || []).flatMap((item) => [
    ...((item.definitions || []).map((entry) => ({ title: `符号定义 · ${item.symbol || "unknown"}`, snippet: entry })) || []),
    ...((item.references || []).map((entry) => ({ title: `符号引用 · ${item.symbol || "unknown"}`, snippet: entry })) || []),
  ]);
  const inputCompleteness = codeContext?.input_completeness;
  const reviewInputs = codeContext?.review_inputs;

  return (
    <Card className="module-card" title="问题详情">
      <Descriptions
        column={1}
        size="small"
        items={[
          {
            key: "location",
            label: "代码位置",
            children: `${finding.file_path}:${finding.line_start}`,
          },
          {
            key: "severity",
            label: "问题级别",
            children: <Tag color={severityColor(finding.severity)}>{finding.severity}</Tag>,
          },
          {
            key: "expert",
            label: "提出专家",
            children: <Tag color="geekblue">{finding.expert_id}</Tag>,
          },
          {
            key: "status",
            label: "裁决状态",
            children: (
              <>
                {issue ? (
                  <>
                    <Tag color={issue.status === "resolved" ? "success" : issue.needs_human ? "error" : "processing"}>
                      {issue.status}
                    </Tag>
                    <Tag>{issue.resolution || "pending"}</Tag>
                  </>
                ) : (
                  <>
                    <Tag color="default">仅 finding</Tag>
                    <Tag>未升级为 issue</Tag>
                  </>
                )}
                {governanceDecision ? <Tag color="default">未升级为 issue</Tag> : null}
              </>
            ),
          },
          {
            key: "finding_type",
            label: "问题类型",
            children: <Tag color="purple">{getFindingTypeLabel(finding.finding_type)}</Tag>,
          },
          {
            key: "confidence",
            label: "置信度",
            children: `${(finding.confidence * 100).toFixed(0)}%`,
          },
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
        <Space wrap>
          {issue ? (
            issue.verified ? <Tag color="success">已通过工具核验</Tag> : <Tag>待进一步核验</Tag>
          ) : (
            <Tag color="default">当前未进入 issue 收敛流程</Tag>
          )}
          {issue ? (
            issue.needs_human ? <Tag color="error">需要人工确认</Tag> : <Tag color="processing">可由系统直接收敛</Tag>
          ) : (
            <Tag color="default">仅保留为 finding</Tag>
          )}
          {issue?.tool_name ? <Tag color="cyan">{issue.tool_name}</Tag> : null}
          {issue?.verifier_name ? <Tag color="blue">{issue.verifier_name}</Tag> : null}
          {issue?.participant_expert_ids?.map((expertId) => (
            <Tag key={expertId}>{expertId}</Tag>
          ))}
          {(finding.context_files || []).slice(0, 4).map((path) => (
            <Tag key={path}>{path}</Tag>
          ))}
        </Space>
      </div>

      {inputCompleteness || reviewInputs ? (
        <div style={{ marginTop: 16 }}>
          <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>审查输入回放</Paragraph>
          <Descriptions
            column={1}
            size="small"
            items={[
              {
                key: "expert_spec",
                label: "专家规范",
                children: (
                  <Tag color={inputCompleteness?.review_spec_present ? "success" : "error"}>
                    {inputCompleteness?.review_spec_present ? "已注入" : "缺失"}
                  </Tag>
                ),
              },
              {
                key: "language_guidance",
                label: "语言通用规范提示",
                children: (
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
                ),
              },
              {
                key: "rule_inputs",
                label: "规则与文档",
                children: (
                  <Space wrap>
                    <Tag>{`命中规则 ${inputCompleteness?.matched_rule_count || 0}`}</Tag>
                    <Tag>{`启用规则 ${inputCompleteness?.enabled_rule_count || 0}`}</Tag>
                    <Tag>{`绑定文档 ${inputCompleteness?.bound_document_count || 0}`}</Tag>
                    {(reviewInputs?.matched_rules || []).slice(0, 4).map((rule) => (
                      <Tag key={rule.rule_id || rule.title} color="purple">
                        {rule.rule_id || rule.title}
                      </Tag>
                    ))}
                  </Space>
                ),
              },
              {
                key: "source_inputs",
                label: "代码输入",
                children: (
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
                ),
              },
            ]}
          />
          {inputCompleteness?.missing_sections?.length ? (
            <Paragraph type="warning" style={{ marginBottom: 0 }}>
              缺失输入：{inputCompleteness.missing_sections.join(" / ")}。系统已按低可信审查结果处理。
            </Paragraph>
          ) : null}
        </div>
      ) : null}

      {governanceDecision ? (
        <div style={{ marginTop: 16 }}>
          <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>Issue 治理说明</Paragraph>
          <Space wrap style={{ marginBottom: 8 }}>
            <Tag color="default">{governanceDecision.rule_label}</Tag>
            {governanceDecision.severity ? <Tag>{governanceDecision.severity}</Tag> : null}
          </Space>
          <Paragraph style={{ marginBottom: 0 }}>{governanceDecision.reason}</Paragraph>
        </div>
      ) : null}

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>命中的规范条款</Paragraph>
        <Space wrap>
          {(finding.matched_rules || []).length ? (
            (finding.matched_rules || []).map((rule) => (
              <Tag key={rule} color="blue">
                {rule}
              </Tag>
            ))
          ) : (
            <Tag>当前未返回明确规范条款</Tag>
          )}
        </Space>
      </div>

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>违反的规范要求</Paragraph>
        <Space wrap>
          {(finding.violated_guidelines || []).length ? (
            (finding.violated_guidelines || []).map((rule) => (
              <Tag key={rule} color="volcano">
                {rule}
              </Tag>
            ))
          ) : (
            <Tag>当前未识别到明确违反条款</Tag>
          )}
        </Space>
      </div>

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>规范依据</Paragraph>
        <Paragraph style={{ marginBottom: 0 }}>
          {finding.rule_based_reasoning || "当前还没有返回更详细的规范依据说明。"}
        </Paragraph>
      </div>

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
        <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>问题说明</Paragraph>
        <Paragraph style={{ marginBottom: 0 }}>{finding.summary}</Paragraph>
      </div>

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>修改思路</Paragraph>
        <Paragraph style={{ marginBottom: 0 }}>
          {finding.remediation_strategy || "当前还没有给出更具体的修改思路。"}
        </Paragraph>
      </div>

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 6, fontWeight: 600 }}>修复建议</Paragraph>
        <Paragraph style={{ marginBottom: 0 }}>
          {finding.remediation_suggestion || "当前还没有给出修复建议。"}
        </Paragraph>
      </div>

      <div style={{ marginTop: 16 }}>
        <Paragraph style={{ marginBottom: 10, fontWeight: 600 }}>建议修改步骤</Paragraph>
        <ol className="review-remediation-steps">
          {(finding.remediation_steps || []).length ? (
            (finding.remediation_steps || []).map((step, index) => <li key={`${index}-${step}`}>{step}</li>)
          ) : (
            <li>先补足定位证据，再按建议修复并补回归测试。</li>
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
                <Tag>{finding.file_path}:{finding.line_start}</Tag>
              </div>
              {renderCodeLines(
                currentCode || `${finding.file_path}:${finding.line_start}`,
                finding.line_start,
                lineRefs,
              )}
            </div>
          </Col>
          <Col xs={24} xl={12}>
            <div className="review-code-panel">
              <div className="review-code-panel-header">
                <span>建议修改后代码</span>
                {finding.suggested_code_language ? <Tag color="blue">{finding.suggested_code_language}</Tag> : null}
              </div>
              {renderSuggestedCode(
                finding.suggested_code || "// 当前还没有生成建议修改代码，请先补充更多上下文后重试。"
              )}
            </div>
          </Col>
        </Row>
      </div>

      {targetDiff ? (
        <div style={{ marginTop: 16 }}>
          <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>目标文件完整变更</Paragraph>
          <div className="review-code-panel">
            <div className="review-code-panel-header">
              <span>{finding.file_path}</span>
              <Tag color="purple">diff</Tag>
            </div>
            <div className="review-code-frame">
              {targetDiff.split("\n").filter(Boolean).map((line, index) => (
                <div key={`target-diff-${index}`} className="review-code-line">
                  <code>{line}</code>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {renderContextSnippet("当前类完整问题片段", currentClassContext)}

      {parentContractContexts.slice(0, 3).map((item, index) =>
        renderContextSnippet(`父接口 / 抽象类 ${index + 1}`, item),
      )}

      {callerContexts.slice(0, 3).map((item, index) =>
        renderContextSnippet(`调用方 Controller / ApplicationService ${index + 1}`, item),
      )}

      {calleeContexts.slice(0, 3).map((item, index) =>
        renderContextSnippet(`被调方 Repository / DomainService ${index + 1}`, item),
      )}

      {domainModelContexts.slice(0, 3).map((item, index) =>
        renderContextSnippet(`相关 Aggregate / ValueObject / DomainEvent ${index + 1}`, item),
      )}

      {transactionContext?.transaction_boundary_snippet ? (
        <div style={{ marginTop: 16 }}>
          <Paragraph style={{ marginBottom: 8, fontWeight: 600 }}>事务边界所在方法和调用链</Paragraph>
          <div className="review-code-panel">
            <div className="review-code-panel-header">
              <span>{transactionContext.transactional_path || "事务边界"}</span>
              {transactionContext.transactional_method ? <Tag>{transactionContext.transactional_method}</Tag> : null}
              {transactionContext.contains_remote_call ? <Tag color="volcano">远程调用</Tag> : null}
              {transactionContext.contains_message_publish ? <Tag color="gold">消息发布</Tag> : null}
              {transactionContext.contains_multi_repository_write ? <Tag color="purple">多仓储写入</Tag> : null}
            </div>
            <div className="review-code-frame">
              {transactionContext.transaction_boundary_snippet.split("\n").filter(Boolean).map((line, index) => (
                <div key={`transaction-${index}`} className="review-code-line">
                  <code>{line}</code>
                </div>
              ))}
            </div>
          </div>
          {(transactionContext.call_chain || []).length ? (
            <Paragraph style={{ marginTop: 8, marginBottom: 0 }}>
              调用链：{(transactionContext.call_chain || []).join(" -> ")}
            </Paragraph>
          ) : null}
        </div>
      ) : null}

      {persistenceContexts.slice(0, 3).map((item, index) =>
        renderContextSnippet(`ORM 映射实体 / SQL / Mapper ${index + 1}`, item),
      )}

      {relatedContexts.slice(0, 3).map((item, index) =>
        renderContextSnippet(`关联上下文代码 ${index + 1}`, item),
      )}

      {symbolContexts.slice(0, 4).map((item, index) =>
        renderContextSnippet(item.title || `符号上下文 ${index + 1}`, item.snippet),
      )}

      <div style={{ marginTop: 16 }}>
        <Button onClick={onJumpToProcess} disabled={!onJumpToProcess}>
          查看对应审查过程
        </Button>
      </div>
    </Card>
  );
};

export default CodeReviewConclusionPanel;
