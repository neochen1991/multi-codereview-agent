import React from "react";
import { Card, Empty, List, Tag, Typography } from "antd";

import type { DebateIssue } from "@/services/api";

const { Paragraph, Text } = Typography;

type ResultIssuePanelProps = {
  issues: DebateIssue[];
};

const ResultIssuePanel: React.FC<ResultIssuePanelProps> = ({ issues }) => {
  return (
    <Card
      className="module-card review-result-issue-card"
      title={`正式议题清单 (${issues.length})`}
      extra={<Text type="secondary">这里只展示真正进入议题收敛流程的问题</Text>}
    >
      {issues.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="当前没有正式议题。若发现项未达到阈值，会保留在审核发现清单或阈值过滤清单中。"
        />
      ) : (
        <List
          dataSource={issues}
          rowKey={(item) => item.issue_id}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <div className="review-finding-title">
                    <Tag color={item.status === "resolved" ? "success" : item.needs_human ? "error" : "processing"}>
                      {item.status}
                    </Tag>
                    <Tag color="volcano">{item.severity}</Tag>
                    {item.needs_human ? <Tag color="error">需人工</Tag> : null}
                    <span>{item.title}</span>
                  </div>
                }
                description={
                  <>
                    <Paragraph ellipsis={{ rows: 2 }} style={{ marginBottom: 8 }}>
                      {item.summary}
                    </Paragraph>
                    <Text type="secondary">
                      {item.file_path ? `${item.file_path}:${item.line_start || "-"}` : "未返回定位"} · 置信度{" "}
                      {(item.confidence * 100).toFixed(0)}% · 关联发现 {item.finding_ids.length}
                    </Text>
                  </>
                }
              />
            </List.Item>
          )}
        />
      )}
    </Card>
  );
};

export default ResultIssuePanel;
