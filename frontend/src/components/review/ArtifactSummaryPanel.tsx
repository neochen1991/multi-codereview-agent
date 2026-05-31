import React from "react";
import { Card, Descriptions, Empty, Tag, Typography } from "antd";

import type { ReviewArtifacts } from "@/services/api";
import { humanizeReviewText } from "@/utils/displayText";
import { getReviewPhaseLabel, getReviewStatusLabel } from "@/utils/reviewStatus";

const { Paragraph } = Typography;

type ArtifactSummaryPanelProps = {
  artifacts: ReviewArtifacts | null;
};

// 产物快照卡把 summary comment、check run 等外部产物汇总展示。
const ArtifactSummaryPanel: React.FC<ArtifactSummaryPanelProps> = ({ artifacts }) => {
  const summaryComment = artifacts?.summary_comment;
  const checkRun = artifacts?.check_run;
  const reportSnapshot = artifacts?.report_snapshot;
  const checkStatusLabel = checkRun ? getReviewStatusLabel(checkRun.status) : "";
  const checkConclusionLabel = checkRun?.conclusion ? humanizeReviewText(checkRun.conclusion) : "";
  const snapshotPhaseLabel = reportSnapshot ? getReviewPhaseLabel(reportSnapshot.phase) : "";
  const snapshotStatusLabel = reportSnapshot ? getReviewStatusLabel(reportSnapshot.status) : "";
  const artifactStatusLabel =
    snapshotPhaseLabel && snapshotStatusLabel && snapshotPhaseLabel !== snapshotStatusLabel
      ? `${snapshotPhaseLabel} · ${snapshotStatusLabel}`
      : snapshotPhaseLabel || snapshotStatusLabel || "-";
  const shouldShowConclusion = Boolean(
    checkConclusionLabel &&
      checkConclusionLabel !== "-" &&
      checkConclusionLabel !== checkStatusLabel,
  );

  return (
    <Card className="module-card" title="产物快照">
      {!summaryComment && !checkRun && !reportSnapshot ? (
        <Empty description="当前审核还没有产物快照。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Descriptions column={1} size="small">
          <Descriptions.Item label="摘要评论">
            <Paragraph style={{ marginBottom: 0 }}>
              {humanizeReviewText(summaryComment?.summary || "-")}
            </Paragraph>
          </Descriptions.Item>
          <Descriptions.Item label="检查状态">
            {checkRun ? (
              <>
                <Tag color={checkRun.status === "completed" ? "success" : "processing"}>
                  {checkStatusLabel}
                </Tag>
                {shouldShowConclusion ? <Tag>{checkConclusionLabel}</Tag> : null}
              </>
            ) : (
              "-"
            )}
          </Descriptions.Item>
          <Descriptions.Item label="产物状态">
            {reportSnapshot ? artifactStatusLabel : "-"}
          </Descriptions.Item>
          <Descriptions.Item label="待人工确认">
            {reportSnapshot?.pending_human_issue_ids?.length || 0}
          </Descriptions.Item>
        </Descriptions>
      )}
    </Card>
  );
};

export default ArtifactSummaryPanel;
