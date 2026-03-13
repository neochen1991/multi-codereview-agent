import React from "react";
import { Button, Card, Col, Descriptions, Empty, Row, Space, Tag, Typography } from "antd";

import type { DebateIssue, ReviewFinding } from "@/services/api";

const { Paragraph } = Typography;

type Props = {
  finding: ReviewFinding | null;
  issue: DebateIssue | null;
  onJumpToProcess?: () => void;
};

const severityColor = (severity: string): string => {
  if (severity === "blocker" || severity === "critical") return "red";
  if (severity === "high") return "volcano";
  if (severity === "medium") return "gold";
  return "blue";
};

const getMergeImpact = (finding: ReviewFinding, issue: DebateIssue | null): string => {
  if (issue?.needs_human && issue.status !== "resolved") return "阻塞合并";
  if (["blocker", "critical", "high"].includes(finding.severity)) return "建议修复后再合并";
  return "可跟随后续修复计划";
};

const getPriority = (finding: ReviewFinding): string => {
  if (["blocker", "critical"].includes(finding.severity)) return "P0";
  if (finding.severity === "high") return "P1";
  if (finding.severity === "medium") return "P2";
  return "P3";
};

const getFindingTypeLabel = (findingType?: string): string => {
  if (findingType === "direct_defect") return "直接缺陷";
  if (findingType === "test_gap") return "测试缺口";
  if (findingType === "design_concern") return "设计关注";
  return "待验证风险";
};

const renderCodeLines = (
  codeExcerpt: string,
  targetLine: number,
  lineRefs: React.MutableRefObject<Record<number, HTMLDivElement | null>>,
) => {
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
  <div className="review-suggested-code-frame">
    <pre className="review-suggested-code-pre">
      <code>{code}</code>
    </pre>
  </div>
);

const CodeReviewConclusionPanel: React.FC<Props> = ({ finding, issue, onJumpToProcess }) => {
  const lineRefs = React.useRef<Record<number, HTMLDivElement | null>>({});

  React.useEffect(() => {
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
                <Tag color={issue?.status === "resolved" ? "success" : issue?.needs_human ? "error" : "processing"}>
                  {issue?.status || "open"}
                </Tag>
                <Tag>{issue?.resolution || "pending"}</Tag>
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
          {issue?.verified ? <Tag color="success">已通过工具核验</Tag> : <Tag>待进一步核验</Tag>}
          {issue?.needs_human ? <Tag color="error">需要人工确认</Tag> : <Tag color="processing">可由系统直接收敛</Tag>}
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
