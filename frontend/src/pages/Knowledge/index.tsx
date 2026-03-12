import React, { useEffect, useMemo, useState } from "react";
import { Button, Card, Collapse, Empty, Form, Input, Select, Space, Tag, Typography, Upload, message } from "antd";
import type { UploadFile } from "antd/es/upload/interface";

import { expertApi, knowledgeApi, type ExpertProfile, type KnowledgeDocument } from "@/services/api";

const { Paragraph, Text, Title } = Typography;

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
            <Space direction="vertical" style={{ width: "100%" }} size={12}>
              {docs.map((item) => (
                <Card key={item.doc_id} size="small" className="module-card">
                  <Space wrap>
                    <Text strong>{item.title}</Text>
                    <Tag>{item.source_filename || item.doc_id}</Tag>
                    {item.tags.map((tag) => (
                      <Tag key={`${item.doc_id}_${tag}`} color="blue">
                        {tag}
                      </Tag>
                    ))}
                  </Space>
                  <Paragraph style={{ marginTop: 12, marginBottom: 0, whiteSpace: "pre-wrap" }}>
                    {item.content}
                  </Paragraph>
                </Card>
              ))}
            </Space>
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

      <Card className="module-card" title="上传 Markdown 知识文档" style={{ marginTop: 16 }}>
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

      <Card className="module-card" title="按专家分组的知识文档" style={{ marginTop: 16 }}>
        {groupedItems.length ? (
          <Collapse items={groupedItems} defaultActiveKey={groupedItems[0]?.key ? [groupedItems[0].key] : []} />
        ) : (
          <Empty description="当前还没有导入知识文档。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Card>
    </div>
  );
};

export default KnowledgePage;
