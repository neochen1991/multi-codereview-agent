import React from "react";
import { Card, Descriptions, Tag } from "antd";

import type { ReviewSummary } from "@/services/api";

type ReviewSubjectPanelProps = {
  review: ReviewSummary | null;
};

const MAX_CHANGED_FILE_PREVIEW = 12;

const formatChangedFilesSummary = (files: string[] | undefined): string => {
  if (!files?.length) return "-";
  if (files.length <= MAX_CHANGED_FILE_PREVIEW) return files.join(", ");
  const preview = files.slice(0, MAX_CHANGED_FILE_PREVIEW).join(", ");
  return `${preview} ... 另 ${files.length - MAX_CHANGED_FILE_PREVIEW} 个文件`;
};

// 审核对象卡用于展示当前 review 对应的仓库、分支和变更文件。
const ReviewSubjectPanel: React.FC<ReviewSubjectPanelProps> = ({ review }) => {
  return (
    <Card className="module-card" title="审核对象">
      <Descriptions column={1} size="small">
        <Descriptions.Item label="类型">
          <Tag color="processing">{review?.subject.subject_type || "-"}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="仓库">{review?.subject.repo_id || "-"}</Descriptions.Item>
        <Descriptions.Item label="项目">{review?.subject.project_id || "-"}</Descriptions.Item>
        <Descriptions.Item label="源分支">{review?.subject.source_ref || "-"}</Descriptions.Item>
        <Descriptions.Item label="目标分支">{review?.subject.target_ref || "-"}</Descriptions.Item>
        <Descriptions.Item label="变更文件">{formatChangedFilesSummary(review?.subject.changed_files)}</Descriptions.Item>
      </Descriptions>
    </Card>
  );
};

export default ReviewSubjectPanel;
