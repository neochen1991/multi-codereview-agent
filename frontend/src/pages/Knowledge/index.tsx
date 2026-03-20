import React, { useEffect, useMemo, useState } from "react";
import { Button, Card, Collapse, Empty, Form, Input, Popconfirm, Select, Space, Tabs, Tag, Typography, Upload, message } from "antd";
import type { UploadFile } from "antd/es/upload/interface";

import { expertApi, knowledgeApi, type ExpertProfile, type KnowledgeDocument } from "@/services/api";

const { Paragraph, Text, Title } = Typography;

// 知识库页分成“上传新文档”和“按专家管理现有文档”两条主路径。
const KnowledgePage: React.FC = () => {
  const [documents, setDocuments] = useState<Record<string, KnowledgeDocument[]>>({});
  const [experts, setExperts] = useState<ExpertProfile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [form] = Form.useForm();

  const loadPage = async () => {
    const [docs, expertList] = await Promise.all([knowledgeApi.grouped(), expertApi.list()]);
    setDocuments(docs);
    setExperts(expertList);
  };

  useEffect(() => {
    void loadPage();
  }, []);

  const groupedItems = useMemo(
    () =>
      Object.entries(documents).map(([expertId, docs]) => {
        const expert = experts.find((item) => item.expert_id === expertId);
        return {
          key: expertId,
          label: `${expert?.name_zh || expertId} (${docs.length})`,
          children: docs.length ? (
            <Collapse
              items={docs.map((item) => ({
                key: item.doc_id,
                label: (
                  <Space wrap>
                    <Text strong>{item.title}</Text>
                    <Tag>{item.source_filename || item.doc_id}</Tag>
                    {item.tags.map((tag) => (
                      <Tag key={`${item.doc_id}_${tag}`} color="blue">
                        {tag}
                      </Tag>
                    ))}
                  </Space>
                ),
                children: (
                  <Space direction="vertical" style={{ width: "100%" }} size={12}>
                    <Space wrap>
                      <Tag color="purple">{item.doc_type || "reference"}</Tag>
                      <Text type="secondary">{new Date(item.created_at).toLocaleString()}</Text>
                      <Popconfirm
                        title="解绑并删除这篇文档？"
                        description="删除后将从当前专家下解绑，并从知识库移除。"
                        okText="删除"
                        cancelText="取消"
                        onConfirm={async () => {
                          try {
                            await knowledgeApi.remove(item.doc_id);
                            message.success("文档已删除");
                            await loadPage();
                          } catch (error: any) {
                            message.error(error?.message || "删除知识文档失败");
                          }
                        }}
                      >
                        <Button danger size="small">
                          解绑并删除
                        </Button>
                      </Popconfirm>
                    </Space>
                    {item.indexed_outline.length ? (
                      <div className="knowledge-outline-block">
                        <Text strong>章节索引</Text>
                        <div className="knowledge-outline-list">
                          {item.indexed_outline.map((outline) => (
                            <div key={`${item.doc_id}-${outline}`} className="knowledge-outline-item">
                              {outline}
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    <Paragraph
                      style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}
                      ellipsis={{ rows: 10, expandable: "collapsible", symbol: "展开全文" }}
                    >
                      {item.content}
                    </Paragraph>
                  </Space>
                ),
              }))}
            />
          ) : (
            <Empty description="该专家暂未绑定知识文档。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ),
        };
      }),
    [documents, experts],
  );

  return (
    <div>
      <Card className="module-card">
        <Title level={3}>知识库管理</Title>
        <Paragraph>
          这里按专家维护知识文档。每份知识都以 Markdown 文档上传，并在审核时按专家绑定关系和检索命中结果注入对应 agent。
        </Paragraph>
      </Card>

      <Tabs
        style={{ marginTop: 16 }}
        items={[
          {
            key: "upload",
            label: "上传新文档",
            children: (
              <Card className="module-card" title="上传 Markdown 知识文档">
                <Form
                  form={form}
                  layout="vertical"
                  onFinish={async (values) => {
                    if (!selectedFile) {
                      message.error("请先选择一个 Markdown 文件");
                      return;
                    }
                    setUploading(true);
                    try {
                      const content = await selectedFile.text();
                      await knowledgeApi.uploadMarkdown({
                        title: String(values.title || selectedFile.name.replace(/\.md$/i, "")),
                        expert_id: values.expert_id,
                        content,
                        tags: String(values.tags || "")
                          .split(",")
                          .map((item: string) => item.trim())
                          .filter(Boolean),
                        source_filename: selectedFile.name,
                      });
                      message.success("知识文档已上传");
                      form.resetFields();
                      setSelectedFile(null);
                      await loadPage();
                    } catch (error: any) {
                      message.error(error?.message || "上传知识文档失败");
                    } finally {
                      setUploading(false);
                    }
                  }}
                >
                  <Form.Item name="expert_id" label="绑定专家" rules={[{ required: true, message: "请选择专家" }]}>
                    <Select
                      placeholder="选择需要绑定知识的专家"
                      options={experts.map((item) => ({
                        value: item.expert_id,
                        label: `${item.name_zh} (${item.expert_id})`,
                      }))}
                    />
                  </Form.Item>
                  <Form.Item name="title" label="文档标题">
                    <Input placeholder="留空时默认取文件名" />
                  </Form.Item>
                  <Form.Item name="tags" label="标签">
                    <Input placeholder="migration, auth, redis" />
                  </Form.Item>
                  <Form.Item label="Markdown 文件">
                    <Upload
                      accept=".md,text/markdown"
                      beforeUpload={(file) => {
                        setSelectedFile(file);
                        return false;
                      }}
                      onRemove={() => {
                        setSelectedFile(null);
                      }}
                      fileList={
                        selectedFile
                          ? ([{ uid: selectedFile.name, name: selectedFile.name, status: "done" }] as UploadFile[])
                          : []
                      }
                      maxCount={1}
                    >
                      <Button>选择 .md 文件</Button>
                    </Upload>
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={uploading}>
                    上传并绑定专家
                  </Button>
                </Form>
              </Card>
            ),
          },
          {
            key: "browse",
            label: "已有专家与文档",
            children: (
              <Card className="module-card" title="按专家分组的知识文档">
                {groupedItems.length ? (
                  <Collapse items={groupedItems} />
                ) : (
                  <Empty description="当前还没有导入知识文档。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}
              </Card>
            ),
          },
        ]}
      />
    </div>
  );
};

export default KnowledgePage;
