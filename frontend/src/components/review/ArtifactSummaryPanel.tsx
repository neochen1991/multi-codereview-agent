import React from "react";
import { Card, Descriptions, Empty, Tag, Typography } from "antd";

import type { ReviewArtifacts } from "@/services/api";

const { Paragraph } = Typography;

type ArtifactSummaryPanelProps = {
  artifacts: ReviewArtifacts | null;
};

const ArtifactSummaryPanel: React.FC<ArtifactSummaryPanelProps> = ({ artifacts }) => {
  const summaryComment = artifacts?.summary_comment;
  const checkRun = artifacts?.check_run;
  const reportSnapshot = artifacts?.report_snapshot;

  return (
    <Card className="module-card" title="产物快照">
      {!summaryComment && !checkRun && !reportSnapshot ? (
        <Empty description="当前审核还没有产物快照。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Descriptions column={1} size="small">
          <Descriptions.Item label="Summary Comment">
            <Paragraph style={{ marginBottom: 0 }}>
              {summaryComment?.summary || "-"}
            </Paragraph>
          </Descriptions.Item>
          <Descriptions.Item label="Check Run">
            {checkRun ? (
              <>
                <Tag color={checkRun.status === "completed" ? "success" : "processing"}>
                  {checkRun.status}
                </Tag>
                <Tag>{checkRun.conclusion}</Tag>
              </>
            ) : (
              "-"
            )}
          </Descriptions.Item>
          <Descriptions.Item label="产物状态">
            {reportSnapshot ? `${reportSnapshot.phase} · ${reportSnapshot.status}` : "-"}
          </Descriptions.Item>
          <Descriptions.Item label="待人工议题">
            {reportSnapshot?.pending_human_issue_ids?.length || 0}
          </Descriptions.Item>
        </Descriptions>
      )}
    </Card>
  );
};

export default ArtifactSummaryPanel;
