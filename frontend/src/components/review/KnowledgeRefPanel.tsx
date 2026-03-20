import React from "react";
import { Card, Empty, List, Space, Tag, Typography } from "antd";

import type { KnowledgeDocument } from "@/services/api";

const { Paragraph, Text } = Typography;

type KnowledgeRefPanelProps = {
  documents: KnowledgeDocument[];
  loading: boolean;
};

// 知识引用卡用于展示当前审核上下文命中的专家知识文档。
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
                    <Space direction="vertical" style={{ width: "100%" }} size={8}>
                      <Paragraph ellipsis={{ rows: item.matched_sections.length ? 2 : 3 }} style={{ marginBottom: 0 }}>
                        {item.content}
                      </Paragraph>
                      {item.matched_sections.length ? (
                        <div className="knowledge-match-group">
                          <Text className="knowledge-match-title">命中章节</Text>
                          <div className="knowledge-match-list">
                            {item.matched_sections.map((section) => (
                              <div key={`${item.doc_id}-${section.node_id}`} className="knowledge-match-item">
                                <div className="knowledge-match-path">{section.path || section.title}</div>
                                {section.summary ? <div className="knowledge-match-summary">{section.summary}</div> : null}
                                {section.matched_terms?.length ? (
                                  <div className="knowledge-match-keywords">
                                    {section.matched_terms.map((term) => (
                                      <Tag key={`${section.node_id}-${term}`} color="geekblue">
                                        {term}
                                      </Tag>
                                    ))}
                                  </div>
                                ) : null}
                                {section.matched_signals?.length ? (
                                  <div className="knowledge-match-signals">
                                    {section.matched_signals.map((signal) => (
                                      <Tag key={`${section.node_id}-${signal}`} color="cyan">
                                        {signal}
                                      </Tag>
                                    ))}
                                  </div>
                                ) : null}
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : item.indexed_outline.length ? (
                        <div className="knowledge-match-group">
                          <Text className="knowledge-match-title">章节索引</Text>
                          <div className="knowledge-outline-list">
                            {item.indexed_outline.slice(0, 8).map((outline) => (
                              <div key={`${item.doc_id}-${outline}`} className="knowledge-outline-item">
                                {outline}
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}
                      <Space wrap size={[6, 6]}>
                        {item.tags.map((tag) => (
                          <Tag key={tag}>{tag}</Tag>
                        ))}
                      </Space>
                    </Space>
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
