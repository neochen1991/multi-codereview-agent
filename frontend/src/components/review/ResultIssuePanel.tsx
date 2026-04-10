import React, { useMemo, useState } from "react";
import { App as AntdApp, Button, Modal, Space, Tag, Typography } from "antd";

import type { CodehubExportResponse, DebateIssue, ReviewFinding } from "@/services/api";
import { reviewApi } from "@/services/api";
import ReviewResultListTable, { classifySpecificIssueType, type ReviewResultListRow } from "./ReviewResultListTable";

const { Paragraph, Text } = Typography;

type ResultIssuePanelProps = {
  reviewId: string;
  issues: DebateIssue[];
  findings: ReviewFinding[];
  selectedIssueId?: string;
  onSelectIssue?: (issueId: string) => void;
};

const getPriority = (severity: string): string => {
  if (["blocker", "critical"].includes(severity)) return "P0";
  if (severity === "high") return "P1";
  if (severity === "medium") return "P2";
  return "P3";
};

const getMergeImpact = (issue: DebateIssue): string => {
  if (issue.needs_human && issue.status !== "resolved") return "Blocking";
  if (["blocker", "critical", "high"].includes(issue.severity)) return "Should fix before merge";
  return "Non-blocking";
};

const buildRecommendedAction = (issue: DebateIssue): string => {
  if (issue.needs_human && issue.status !== "resolved") return "提交人工复核";
  if (issue.resolution === "human_approved" || issue.resolution === "judge_accepted") return "进入修复清单";
  if (issue.resolution === "human_rejected") return "关闭议题并补证据";
  if (issue.needs_debate && issue.status !== "resolved") return "继续专家辩论";
  if (issue.verified) return "按核验证据整改";
  return "补充证据后再裁决";
};

const hasDesignEvidence = (finding?: ReviewFinding): boolean =>
  Boolean(
    finding &&
      ((finding.design_doc_titles?.length || 0) > 0 ||
        (finding.matched_design_points?.length || 0) > 0 ||
        (finding.missing_design_points?.length || 0) > 0 ||
        (finding.extra_implementation_points?.length || 0) > 0 ||
        (finding.design_conflicts?.length || 0) > 0),
  );

const getDesignAlignmentStatus = (relatedFindings: ReviewFinding[]): string | undefined => {
  const misaligned = relatedFindings.find(
    (finding) =>
      hasDesignEvidence(finding) &&
      (["misaligned", "partially_aligned"].includes(String(finding.design_alignment_status || "").trim()) ||
        (finding.missing_design_points?.length || 0) > 0 ||
        (finding.design_conflicts?.length || 0) > 0),
  );
  if (misaligned) return "design_misaligned";
  return relatedFindings.find((finding) => finding.design_alignment_status)?.design_alignment_status;
};

const buildIssueTypeLabels = (issue: DebateIssue, findings: ReviewFinding[]): string[] => {
  const values = [
    issue.title,
    issue.summary,
    ...(issue.aggregated_titles || []),
    ...(issue.aggregated_summaries || []),
    ...findings.flatMap((finding) => [...(finding.matched_rules || []), ...(finding.violated_guidelines || []), finding.title]),
  ];
  return Array.from(new Set(values.map((item) => classifySpecificIssueType(String(item || ""))).filter(Boolean) as string[]));
};

const ResultIssuePanel: React.FC<ResultIssuePanelProps> = ({
  reviewId,
  issues,
  findings,
  selectedIssueId,
  onSelectIssue,
}) => {
  const { message } = AntdApp.useApp();
  const [selectedIssueIds, setSelectedIssueIds] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [exportResult, setExportResult] = useState<CodehubExportResponse | null>(null);

  const findingById = useMemo(() => {
    const map = new Map<string, ReviewFinding>();
    for (const finding of findings) {
      map.set(finding.finding_id, finding);
    }
    return map;
  }, [findings]);

  const rows = useMemo<ReviewResultListRow[]>(
    () =>
      issues.map((issue) => {
        const relatedFindings = issue.finding_ids
          .map((findingId) => findingById.get(findingId))
          .filter(Boolean) as ReviewFinding[];
        const primaryFinding = relatedFindings[0];
        const distinctFiles = Array.from(
          new Set(
            relatedFindings
              .map((finding) => String(finding.file_path || "").trim())
              .filter(Boolean),
          ),
        );
        const filePath =
          issue.file_path ||
          (distinctFiles.length === 1
            ? distinctFiles[0]
            : distinctFiles.length > 1
              ? `跨 ${distinctFiles.length} 个文件`
              : "");
        const lineStart =
          issue.line_start ||
          (distinctFiles.length <= 1 ? primaryFinding?.line_start : undefined);
        const metaSummaryParts = [
          "议题聚合",
          `关联发现 ${issue.finding_ids.length}`,
          `参与专家 ${issue.participant_expert_ids.length}`,
        ];
        if ((issue.aggregated_titles || []).length > 1) {
          metaSummaryParts.push(`聚合问题 ${issue.aggregated_titles?.length || 0}`);
        }
        if (distinctFiles.length > 1) {
          metaSummaryParts.push(`涉及文件 ${distinctFiles.length}`);
        }
        return {
          id: issue.issue_id,
          file_path: filePath,
          line_start: lineStart,
          title: issue.title,
          summary: issue.summary,
          metaSummary: metaSummaryParts.join(" · "),
          finding_types:
            issue.aggregated_finding_types && issue.aggregated_finding_types.length > 0
              ? issue.aggregated_finding_types
              : Array.from(
                  new Set(
                    relatedFindings
                      .map((finding) => String(finding.finding_type || "").trim())
                      .filter(Boolean),
                  ),
                ),
          finding_type: issue.finding_type || primaryFinding?.finding_type || "risk_hypothesis",
          finding_type_labels: buildIssueTypeLabels(issue, relatedFindings),
          severity: issue.severity,
          confidence: issue.confidence,
          expert_labels: issue.participant_expert_ids || [],
          mergeImpact: getMergeImpact(issue),
          priority: getPriority(issue.severity),
          issueStatus: issue.status,
          resolution: issue.resolution || "pending",
          recommendedAction: buildRecommendedAction(issue),
          needsHuman: issue.needs_human,
          verified: issue.verified,
          hasIssue: true,
          governanceDecision: null,
          designAlignmentStatus: getDesignAlignmentStatus(relatedFindings),
          hasDesignEvidence: relatedFindings.some((finding) => hasDesignEvidence(finding)),
        };
      }),
    [findingById, issues],
  );

  const submitSelectedIssues = async () => {
    if (!reviewId || selectedIssueIds.length === 0) return;
    setSubmitting(true);
    try {
      const result = await reviewApi.exportIssuesToCodehub(reviewId, { issue_ids: selectedIssueIds });
      setExportResult(result);
      setPreviewOpen(true);
      message.success(`已模拟提交 ${result.submitted_count} 条正式议题到 CodeHub`);
    } catch (error: any) {
      message.error(error?.message || "模拟提交到 CodeHub 失败");
    } finally {
      setSubmitting(false);
    }
  };

  const selectedCount = selectedIssueIds.length;

  return (
    <>
      <ReviewResultListTable
        cardClassName="review-result-issue-card"
        title={`正式议题清单 (${issues.length})`}
        extra={<Text type="secondary">这里只展示真正进入议题收敛流程的问题</Text>}
        toolbarExtra={
          <Space wrap>
            <Tag color={selectedCount > 0 ? "processing" : "default"}>{selectedCount > 0 ? `已选 ${selectedCount} 条` : "未选择议题"}</Tag>
            <Button onClick={() => setSelectedIssueIds(rows.map((item) => item.id))} disabled={rows.length === 0}>
              全选
            </Button>
            <Button onClick={() => setSelectedIssueIds([])} disabled={selectedCount === 0}>
              清空选择
            </Button>
            <Button type="primary" onClick={() => void submitSelectedIssues()} disabled={selectedCount === 0 || !reviewId} loading={submitting}>
              提交到 CodeHub
            </Button>
          </Space>
        }
        rows={rows}
        selectedRowId={selectedIssueId}
        onSelectRow={onSelectIssue}
        selectedRowIds={selectedIssueIds}
        onSelectedRowIdsChange={setSelectedIssueIds}
        emptyText="当前没有正式议题。若发现项未达到阈值，会保留在审核发现清单或阈值过滤清单中。"
      />
      <Modal
        title="CodeHub 模拟提交结果"
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={960}
      >
        {exportResult ? (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Text type="secondary">
              本次仅为 mock 提交，当前返回的是后端组装后的 payload，后续你可以直接把这条接口替换成真实 CodeHub 能力。
            </Text>
            {exportResult.items.map((item) => (
              <div key={item.issue_id} className="review-summary-cell">
                <div className="review-finding-title">
                  <Tag color="volcano">{item.severity}</Tag>
                  <span>{item.title}</span>
                </div>
                <Paragraph strong style={{ marginBottom: 8 }}>
                  问题描述
                </Paragraph>
                <Paragraph style={{ whiteSpace: "pre-wrap" }}>{item.problem_description}</Paragraph>
                <Paragraph strong style={{ marginBottom: 8 }}>
                  修改建议
                </Paragraph>
                <Paragraph style={{ whiteSpace: "pre-wrap" }}>{item.remediation_suggestion}</Paragraph>
                <Paragraph strong style={{ marginBottom: 8 }}>
                  修改后代码
                </Paragraph>
                <pre className="review-code-block">
                  <code>{item.patched_code}</code>
                </pre>
                <Text type="secondary">{item.mock_ticket_url}</Text>
              </div>
            ))}
          </Space>
        ) : null}
      </Modal>
    </>
  );
};

export default ResultIssuePanel;
