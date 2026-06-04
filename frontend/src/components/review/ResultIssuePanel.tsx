import React, { useMemo, useState } from "react";
import { Alert, App as AntdApp, Button, Modal, Space, Tag, Typography } from "antd";

import type { CodehubExportResponse, DebateIssue, ReviewFinding } from "@/services/api";
import { reviewApi } from "@/services/api";
import { humanizeExpertId, humanizeSeverity } from "@/utils/displayText";
import {
  cleanUserFacingText,
  hasCrossContextPollution,
  issueTextMatchesIssueType,
  issueTypeDisplayLabel,
} from "./issueDisplayQuality";
import { getEffectiveIssues } from "./effectiveIssues";
import ReviewResultListTable, { classifySpecificIssueType, type ReviewResultListRow } from "./ReviewResultListTable";

const { Paragraph, Text } = Typography;

type ResultIssuePanelProps = {
  reviewId: string;
  issues: DebateIssue[];
  findings: ReviewFinding[];
  selectedIssueId?: string;
  expectedIssueCount?: number;
  onSelectIssue?: (issueId: string) => void;
};

const getPriority = (severity: string): string => {
  if (["blocker", "critical"].includes(severity)) return "P0";
  if (severity === "high") return "P1";
  if (severity === "medium") return "P2";
  return "P3";
};

const getMergeImpact = (issue: DebateIssue): string => {
  if (issue.needs_human && issue.status !== "resolved") return "阻塞合并，等待人工确认";
  if (["blocker", "critical", "high"].includes(issue.severity)) return "建议合并前修复";
  return "不阻塞合并";
};

const buildRecommendedAction = (issue: DebateIssue): string => {
  if (issue.needs_human && issue.status !== "resolved") return "提交人工复核";
  if (issue.resolution === "human_approved" || issue.resolution === "judge_accepted") return "进入修复清单";
  if (issue.resolution === "human_rejected") return "关闭问题并补证据";
  if (issue.needs_debate && issue.status !== "resolved") return "继续复核";
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
  const explicitLabels = [issue.normalized_issue_type, issue.finding_type, issue.category_label]
    .map((item) => issueTypeDisplayLabel(item))
    .filter((item) => item && item !== "代码风险");
  if (explicitLabels.length > 0) {
    return [explicitLabels[0]];
  }
  const values = [
    issue.title,
    issue.summary,
    ...(issue.aggregated_titles || []),
    ...(issue.aggregated_summaries || []),
    ...findings.flatMap((finding) => [finding.normalized_issue_type, finding.finding_type, finding.title]),
  ];
  const labels = [...explicitLabels, ...values.map((item) => classifySpecificIssueType(String(item || ""))).filter(Boolean)];
  const deduped = Array.from(new Set(labels as string[]));
  return deduped.slice(0, 1);
};

const INTERNAL_SUMMARY_PATTERNS = [
  /问题聚合/i,
  /关联发现\s*\d+/i,
  /主责角色/i,
  /参与角色/i,
  /合并问题\s*\d+/i,
  /Static diff signals/i,
  /baseline/i,
  /related_findings/i,
  /consistency/i,
  /validation/i,
  /Judge/i,
  /结果复核/,
  /审核调度/,
  /当前 issue 来自/i,
  /当前问题来自一条有代码证据的检视发现/,
];

const compactReadableText = (value?: string | null): string => {
  const text = cleanUserFacingText(value || "")
    .replace(/^[-*]\s*/gm, "")
    .replace(/^[\s:：,，;；。]+|[\s:：,，;；。]+$/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return text === "-" ? "" : text;
};

const isReadableIssueSummary = (value: string): boolean => {
  const text = value.trim();
  if (text.length < 8) return false;
  if (/^[\[{]/.test(text)) return false;
  if (INTERNAL_SUMMARY_PATTERNS.some((pattern) => pattern.test(text))) return false;
  if (/^[a-z_]+[:：]/i.test(text)) return false;
  return /[\u4e00-\u9fa5]/.test(text) || text.split(/\s+/).length >= 6;
};

const pickReadableSentence = (value: string): string => {
  const sentences = value
    .split(/(?<=[。！？!?])|\n|；|;/)
    .map((item) => item.trim())
    .filter(Boolean);
  const preferred = sentences.find((item) => isReadableIssueSummary(item));
  return preferred || value.trim();
};

const clipListSummary = (value: string): string => {
  const text = value.trim();
  if (text.length <= 150) return text;
  return `${text.slice(0, 148).replace(/[，,、；;。\s]+$/g, "")}…`;
};

const normalizeIssueSummaryKey = (value?: string): string =>
  String(value || "")
    .replace(/\s+/g, "")
    .toLowerCase();

const basename = (value?: string): string => {
  const normalized = String(value || "").replace(/\\/g, "/").trim();
  return normalized.split("/").filter(Boolean).pop() || normalized;
};

const normalizePathKey = (value?: string): string =>
  String(value || "")
    .replace(/\\/g, "/")
    .trim()
    .toLowerCase();

const buildRowLocationPrefix = (row: ReviewResultListRow): string => {
  const fileName = basename(row.file_path) || "当前文件";
  const line = row.line_start ? `L${row.line_start}` : "";
  return [fileName, line].filter(Boolean).join(" ");
};

const disambiguateDuplicateSummaries = (rows: ReviewResultListRow[]): ReviewResultListRow[] => {
  const summaryGroups = new Map<string, ReviewResultListRow[]>();
  const titleGroups = new Map<string, ReviewResultListRow[]>();
  for (const row of rows) {
    const summaryKey = normalizeIssueSummaryKey(row.summary);
    if (summaryKey) summaryGroups.set(summaryKey, [...(summaryGroups.get(summaryKey) || []), row]);
    const titleKey = normalizeIssueSummaryKey(row.title);
    if (titleKey) titleGroups.set(titleKey, [...(titleGroups.get(titleKey) || []), row]);
  }
  const duplicateSummaryIds = new Set<string>();
  const duplicateTitleIds = new Set<string>();
  const collectDuplicateAnchors = (groups: Map<string, ReviewResultListRow[]>, target: Set<string>) => {
    for (const group of groups.values()) {
      const anchors = new Set(group.map((row) => `${normalizePathKey(row.file_path)}:${row.line_start || ""}`));
      if (group.length > 1 && anchors.size > 1) {
        group.forEach((row) => target.add(row.id));
      }
    }
  };
  collectDuplicateAnchors(summaryGroups, duplicateSummaryIds);
  collectDuplicateAnchors(titleGroups, duplicateTitleIds);
  const duplicateIds = new Set([...duplicateSummaryIds, ...duplicateTitleIds]);
  if (duplicateIds.size === 0) return rows;
  return rows.map((row) => {
    if (!duplicateIds.has(row.id)) return row;
    const prefix = buildRowLocationPrefix(row);
    if (!prefix) return row;
    const title = duplicateTitleIds.has(row.id) && !row.title.includes(prefix) ? `${prefix}：${row.title}` : row.title;
    const summary =
      duplicateSummaryIds.has(row.id) && !row.summary.includes(prefix)
        ? clipListSummary(`${prefix}：${row.summary}`)
        : row.summary;
    return {
      ...row,
      title,
      summary,
    };
  });
};

const buildIssueListSummary = (
  issue: DebateIssue,
  relatedFindings: ReviewFinding[],
  filePath: string,
  lineStart?: number,
): string => {
  const issueType = issue.normalized_issue_type || issue.finding_type || issue.category_label || issue.title;
  const alignedFindings = relatedFindings.filter((finding) =>
    issueTextMatchesIssueType(
      issueType,
      [
        finding.normalized_issue_type,
        finding.finding_type,
        finding.title,
        finding.summary,
        finding.rule_based_reasoning,
      ].filter(Boolean).join("\n"),
    ),
  );
  const candidates = [
    issueTextMatchesIssueType(issueType, issue.problem_description) ? issue.problem_description : "",
    issue.problem_description,
    issueTextMatchesIssueType(issueType, issue.summary) ? issue.summary : "",
    issue.summary,
    ...(issue.aggregated_summaries || []).filter((summary) => issueTextMatchesIssueType(issueType, summary)),
    ...alignedFindings.map((finding) => finding.summary),
    ...alignedFindings.map((finding) => finding.rule_based_reasoning),
    ...relatedFindings.map((finding) => finding.summary),
    ...(issue.evidence || []).filter((item) => issueTextMatchesIssueType(issueType, item)),
  ];
  const safeCandidates = candidates.filter((candidate) => !hasCrossContextPollution(candidate, filePath));
  for (const candidate of safeCandidates) {
    const readable = pickReadableSentence(compactReadableText(candidate));
    if (isReadableIssueSummary(readable)) return clipListSummary(readable);
  }
  const title = compactReadableText(issue.title) || "当前改动存在风险";
  const location = [filePath, lineStart ? `L${lineStart}` : ""].filter(Boolean).join(":");
  return clipListSummary(location ? `${title}，请优先查看 ${location} 附近的改动。` : title);
};

const ResultIssuePanel: React.FC<ResultIssuePanelProps> = ({
  reviewId,
  issues,
  findings,
  selectedIssueId,
  expectedIssueCount = 0,
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

  const formalIssues = useMemo(() => getEffectiveIssues(issues), [issues]);

  const rows = useMemo<ReviewResultListRow[]>(
    () => {
      const builtRows = formalIssues.map((issue) => {
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
        const metaSummaryParts = [`定位 ${filePath || "-"}`];
        if (lineStart) metaSummaryParts.push(`L${lineStart}`);
        const owner = humanizeExpertId(issue.primary_expert_id || issue.participant_expert_ids[0]);
        if (owner && owner !== "-") metaSummaryParts.push(`主责 ${owner}`);
        if (distinctFiles.length > 1) {
          metaSummaryParts.push(`涉及 ${distinctFiles.length} 个文件`);
        }
        return {
          id: issue.issue_id,
          file_path: filePath,
          line_start: lineStart,
          title: issue.title,
          summary: buildIssueListSummary(issue, relatedFindings, filePath, lineStart),
          fixSummary: compactReadableText(
            issue.remediation_suggestion ||
              issue.remediation_strategy ||
              (issue.remediation_steps || []).join("；"),
          ),
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
          expert_labels: Array.from(
            new Set(
              [issue.primary_expert_id, ...(issue.participant_expert_ids || [])]
                .map((item) => humanizeExpertId(item))
                .filter(Boolean),
            ),
          ),
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
      });
      return disambiguateDuplicateSummaries(builtRows);
    },
    [findingById, formalIssues],
  );

  const submitSelectedIssues = async () => {
    if (!reviewId || selectedIssueIds.length === 0) return;
    setSubmitting(true);
    try {
      const result = await reviewApi.exportIssuesToCodehub(reviewId, { issue_ids: selectedIssueIds });
      setExportResult(result);
      setPreviewOpen(true);
      message.success(`已模拟提交 ${result.submitted_count} 条有效问题到缺陷平台`);
    } catch (error: any) {
      message.error(error?.message || "模拟提交到缺陷平台失败");
    } finally {
      setSubmitting(false);
    }
  };

  const selectedCount = selectedIssueIds.length;
  const detailsMissing = expectedIssueCount > formalIssues.length && formalIssues.length === 0;

  return (
    <>
      {detailsMissing ? (
        <Alert
          showIcon
          type="warning"
          style={{ marginBottom: 12 }}
          message="问题明细没有恢复出来"
          description={`产物快照里记录了 ${expectedIssueCount} 个有效问题，但当前任务没有返回每个问题的详情。为避免误导，这里不会展示成“没有有效问题”，也暂不允许提交到缺陷平台。`}
        />
      ) : null}
      <ReviewResultListTable
        cardClassName="review-result-issue-card"
        title={detailsMissing ? `有效问题清单 (${expectedIssueCount}，明细待恢复)` : `有效问题清单 (${formalIssues.length})`}
        extra={<Text type="secondary">{detailsMissing ? "当前只恢复到数量，尚未恢复每条问题的详情" : "这里只展示已确认需要进入处理流程的问题"}</Text>}
        toolbarExtra={
          <Space wrap>
            <Tag color={selectedCount > 0 ? "processing" : "default"}>{selectedCount > 0 ? `已选 ${selectedCount} 条` : "未选择问题"}</Tag>
            <Button onClick={() => setSelectedIssueIds(rows.map((item) => item.id))} disabled={rows.length === 0}>
              全选
            </Button>
            <Button onClick={() => setSelectedIssueIds([])} disabled={selectedCount === 0}>
              清空选择
            </Button>
            <Button type="primary" onClick={() => void submitSelectedIssues()} disabled={selectedCount === 0 || !reviewId || detailsMissing} loading={submitting}>
              提交到缺陷平台
            </Button>
          </Space>
        }
        rows={rows}
        selectedRowId={selectedIssueId}
        onSelectRow={onSelectIssue}
        selectedRowIds={selectedIssueIds}
        onSelectedRowIdsChange={setSelectedIssueIds}
        emptyText={
          detailsMissing
            ? "产物快照显示本次审核有有效问题，但问题详情没有恢复出来。请重新生成结果或恢复明细文件后再查看。"
            : "当前没有有效问题。若发现项未达到升级条件，会保留在审核发现清单或保留观察清单中。"
        }
        disableHorizontalScroll
      />
      <Modal
        title="缺陷平台模拟提交结果"
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={960}
      >
        {exportResult ? (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Text type="secondary">
              本次仅为模拟提交，当前返回的是后端组装后的提交内容，后续可以把这条接口替换成真实缺陷平台能力。
            </Text>
            {exportResult.items.map((item) => (
              <div key={item.issue_id} className="review-summary-cell">
                <div className="review-finding-title">
                  <Tag color="volcano">{humanizeSeverity(item.severity)}</Tag>
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
                {item.patched_code ? (
                  <>
                    <Paragraph strong style={{ marginBottom: 8 }}>
                      修改后代码
                    </Paragraph>
                    <pre className="review-code-block">
                      <code>{item.patched_code}</code>
                    </pre>
                  </>
                ) : null}
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
