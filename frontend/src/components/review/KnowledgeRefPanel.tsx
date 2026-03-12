import React from "react";
import { Card, Empty, List, Tag, Typography } from "antd";

import type { KnowledgeDocument } from "@/services/api";

const { Paragraph } = Typography;

type KnowledgeRefPanelProps = {
  documents: KnowledgeDocument[];
  loading: boolean;
};

const KnowledgeRefPanel: React.FC<KnowledgeRefPanelProps> = ({ documents, loading }) => {
  return (
    <Card className="module-card process-sidebar-card process-sidebar-card-md" title="知识引用" loading={loading}>
      <div className="process-card-scroll">
        {documents.length === 0 ? (
          <Empty description="当前议题还没有匹配到知识条目。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <List
            dataSource={documents}
            renderItem={(item) => (
              <List.Item>
                <List.Item.Meta
                  title={
                    <div className="review-finding-title">
                      <Tag color="blue">{item.expert_id}</Tag>
                      <span>{item.title}</span>
                    </div>
                  }
                  description={
                    <>
                      <Paragraph ellipsis={{ rows: 3 }} style={{ marginBottom: 8 }}>
                        {item.content}
                      </Paragraph>
                      {item.tags.map((tag) => (
                        <Tag key={tag}>{tag}</Tag>
                      ))}
                    </>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </div>
    </Card>
  );
};

export default KnowledgeRefPanel;
