import React from "react";
import { Button, Card, Col, Descriptions, Empty, Row, Space, Tag, Typography } from "antd";

import type { DebateIssue, IssueFilterDecision, ReviewFinding, RuleScreeningMetadata } from "@/services/api";

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
                finding.code_excerpt || `${finding.file_path}:${finding.line_start}`,
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

      <div style={{ marginTop: 16 }}>
        <Button onClick={onJumpToProcess} disabled={!onJumpToProcess}>
          查看对应审查过程
        </Button>
      </div>
    </Card>
  );
};

export default CodeReviewConclusionPanel;
