import React, { useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Row,
  Select,
  Space,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import type { UploadFile } from "antd/es/upload/interface";

import { expertApi, knowledgeApi, type ExpertProfile, type KnowledgeDocument } from "@/services/api";

const { Paragraph, Text, Title } = Typography;

const formatOptionalLlmValue = (value?: string | null, fallbackLabel = "继承系统配置") =>
  value && value.trim() ? value.trim() : fallbackLabel;

const docTypeLabelMap: Record<string, string> = {
  review_rule: "审视规范补充",
  domain_reference: "领域参考",
  runbook: "Runbook",
  reference: "绑定参考文档",
};

const formatDocTypeLabel = (value?: string) => docTypeLabelMap[String(value || "reference")] || String(value || "reference");

const getRuntimeToolBindings = (expert: ExpertProfile): string[] =>
  (Array.isArray(expert.runtime_tool_bindings) ? expert.runtime_tool_bindings : []).filter(Boolean);

const getMergedSkillBindings = (expert: ExpertProfile): string[] =>
  (Array.isArray(expert.skill_bindings) ? expert.skill_bindings : []).filter(Boolean);

const getManualSkillBindings = (expert: ExpertProfile): string[] =>
  (Array.isArray(expert.skill_bindings_manual) ? expert.skill_bindings_manual : []).filter(Boolean);

const getExtensionSkillBindings = (expert: ExpertProfile): string[] =>
  (Array.isArray(expert.skill_bindings_extension) ? expert.skill_bindings_extension : []).filter(Boolean);

// 专家中心负责管理专家配置、核心规范和绑定文档，是审核能力治理的主入口。
const ExpertsPage: React.FC = () => {
  const [experts, setExperts] = useState<ExpertProfile[]>([]);
  const [documents, setDocuments] = useState<Record<string, KnowledgeDocument[]>>({});
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadTarget, setUploadTarget] = useState<ExpertProfile | null>(null);
  const [detailTarget, setDetailTarget] = useState<ExpertProfile | null>(null);
  const [form] = Form.useForm();
  const [uploadForm] = Form.useForm();

  const loadPage = async () => {
    const [expertList, groupedDocs] = await Promise.all([expertApi.list(), knowledgeApi.grouped()]);
    setExperts(expertList);
    setDocuments(groupedDocs);
  };

  useEffect(() => {
    void loadPage();
  }, []);

  const builtinExperts = useMemo(() => experts.filter((item) => !item.custom), [experts]);
  const customExperts = useMemo(() => experts.filter((item) => item.custom), [experts]);
  const allExperts = useMemo(() => experts, [experts]);
  const docCountMap = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(documents).map(([expertId, items]) => [
          expertId,
          items.filter((item) => item.doc_type !== "review_spec").length,
        ]),
      ),
    [documents],
  );

  const openUploadModal = (expert: ExpertProfile) => {
    setUploadTarget(expert);
    setSelectedFile(null);
    uploadForm.setFieldsValue({
      expert_id: expert.expert_id,
      doc_type: "review_rule",
    });
  };

  const closeUploadModal = () => {
    setUploadTarget(null);
    setSelectedFile(null);
    uploadForm.resetFields();
  };

  const closeDetailModal = () => {
    setDetailTarget(null);
  };

  return (
    <div>
      <Card className="module-card">
        <Title level={3}>专家配置中心</Title>
        <Paragraph>
          专家中心现在同时承担三件事：维护专家职责与提示词、绑定专家的核心审视规范、上传并绑定多篇 Markdown
          参考文档。审核执行时，每个专家都会完整加载核心规范文档，并结合 MR 片段、源码仓上下文和已绑定文档进行代码审视。
        </Paragraph>
      </Card>

      <Tabs
        style={{ marginTop: 16 }}
        items={[
          {
            key: "existing",
            label: `已有专家 (${allExperts.length})`,
            children: (
              <Card className="module-card" title="已创建专家与绑定文档">
                <List
                  dataSource={allExperts}
                  renderItem={(item) => {
                    const expertDocs = documents[item.expert_id] || [];
                    const runtimeTools = getRuntimeToolBindings(item);
                    const manualSkills = getManualSkillBindings(item);
                    const extensionSkills = getExtensionSkillBindings(item);
                    const mergedSkills = getMergedSkillBindings(item);

                    return (
                      <List.Item>
                        <List.Item.Meta
                          title={
                            <Space wrap>
                              <span>{item.name_zh}</span>
                              <Tag color={item.enabled ? "success" : "default"}>{item.enabled ? "enabled" : "disabled"}</Tag>
                              <Tag color={item.custom ? "gold" : "default"}>{item.custom ? "custom" : "builtin"}</Tag>
                              <Tag color="geekblue">核心规范 1</Tag>
                              <Tag color="purple">绑定文档 {docCountMap[item.expert_id] || 0}</Tag>
                            </Space>
                          }
                          description={
                            <div>
                              <div>{`${item.role} · ${item.focus_areas.join(" / ")}`}</div>
                              <div style={{ marginTop: 6 }}>
                                <Tag color="geekblue">{formatOptionalLlmValue(item.model)}</Tag>
                                <Tag color="volcano">{formatOptionalLlmValue(item.provider)}</Tag>
                                <Tag>{item.api_key ? "已配置 API Key" : formatOptionalLlmValue(item.api_key_env)}</Tag>
                              </div>
                              {item.api_base_url ? (
                                <div style={{ marginTop: 6, color: "var(--text-secondary)" }}>{item.api_base_url}</div>
                              ) : null}
                              <div style={{ marginTop: 8, color: "var(--text-primary)" }}>
                                Skill 绑定：{mergedSkills.length ? `${mergedSkills.length} 个` : "未绑定"}
                              </div>
                              <div style={{ marginTop: 6, color: "var(--text-secondary)" }}>
                                Extension 绑定：{extensionSkills.length ? extensionSkills.join(" / ") : "无"}
                              </div>
                              <div style={{ marginTop: 6, color: "var(--text-secondary)" }}>
                                专家源码绑定：{manualSkills.length ? manualSkills.join(" / ") : "无"}
                              </div>
                              <div style={{ marginTop: 8, color: "var(--text-primary)" }}>
                                必查项：{item.required_checks.length ? item.required_checks.join(" / ") : "未配置"}
                              </div>
                              <div style={{ marginTop: 6, color: "var(--text-secondary)" }}>
                                产物偏好：{item.preferred_artifacts.length ? item.preferred_artifacts.join(" / ") : "未配置"}
                              </div>
                              <div style={{ marginTop: 6, color: "var(--text-secondary)" }}>
                                越界限制：{item.out_of_scope.length ? item.out_of_scope.join(" / ") : "未配置"}
                              </div>
                              <div style={{ marginTop: 6 }}>
                                {item.tool_bindings.map((tool) => (
                                  <Tag key={`${item.expert_id}_${tool}`} color="blue">
                                    {tool}
                                  </Tag>
                                ))}
                                {item.mcp_tools.map((tool) => (
                                  <Tag key={`${item.expert_id}_${tool}`} color="purple">
                                    {tool}
                                  </Tag>
                                ))}
                                {runtimeTools.map((tool) => (
                                  <Tag key={`${item.expert_id}_${tool}`} color="gold">
                                    {tool}
                                  </Tag>
                                ))}
                                {extensionSkills.map((skill) => (
                                  <Tag key={`${item.expert_id}_${skill}_ext`} color="geekblue">
                                    {`skill ${skill} · extension`}
                                  </Tag>
                                ))}
                                {manualSkills.map((skill) => (
                                  <Tag key={`${item.expert_id}_${skill}_manual`} color="default">
                                    {`skill ${skill} · 源码`}
                                  </Tag>
                                ))}
                                {item.agent_bindings.map((tool) => (
                                  <Tag key={`${item.expert_id}_${tool}`} color="cyan">
                                    {tool}
                                  </Tag>
                                ))}
                              </div>
                              {item.system_prompt ? (
                                <Paragraph style={{ marginTop: 10, marginBottom: 10 }} ellipsis={{ rows: 2 }}>
                                  {item.system_prompt}
                                </Paragraph>
                              ) : null}
                              <Space style={{ marginBottom: 10 }}>
                                <Button size="small" onClick={() => setDetailTarget(item)}>
                                  展开文档详情
                                </Button>
                                <Button size="small" onClick={() => openUploadModal(item)}>
                                  上传并绑定 Markdown
                                </Button>
                              </Space>
                              <div style={{ color: "var(--text-secondary)" }}>
                                核心规范默认折叠，已绑定文档 {expertDocs.length} 篇。Skill 推荐通过 extension 目录配置；点击“展开文档详情”后查看具体内容。
                              </div>
                            </div>
                          }
                        />
                      </List.Item>
                    );
                  }}
                />
              </Card>
            ),
          },
          {
            key: "create",
            label: "新建专家",
            children: (
              <Row gutter={[16, 16]}>
                <Col xs={24} xl={10}>
                  <Card className="module-card" title="新建自定义专家">
                    <Form
                      form={form}
                      layout="vertical"
                      onFinish={async (values) => {
                        setCreating(true);
                        try {
                          await expertApi.create({
                            expert_id: values.expert_id,
                            name: values.name,
                            name_zh: values.name_zh,
                            role: values.role,
                            focus_areas: String(values.focus_areas || "")
                              .split(",")
                              .map((item: string) => item.trim())
                              .filter(Boolean),
                            knowledge_sources: String(values.knowledge_sources || "")
                              .split(",")
                              .map((item: string) => item.trim())
                              .filter(Boolean),
                            tool_bindings: String(values.tool_bindings || "")
                              .split(",")
                              .map((item: string) => item.trim())
                              .filter(Boolean),
                            mcp_tools: String(values.mcp_tools || "")
                              .split(",")
                              .map((item: string) => item.trim())
                              .filter(Boolean),
                            runtime_tool_bindings: String(values.runtime_tool_bindings || "")
                              .split(",")
                              .map((item: string) => item.trim())
                              .filter(Boolean),
                            agent_bindings: String(values.agent_bindings || "")
                              .split(",")
                              .map((item: string) => item.trim())
                              .filter(Boolean),
                            max_tool_calls: values.max_tool_calls || 4,
                            max_debate_rounds: values.max_debate_rounds || 2,
                            provider: String(values.provider || "").trim() || undefined,
                            api_base_url: String(values.api_base_url || "").trim() || undefined,
                            api_key: String(values.api_key || "").trim() || undefined,
                            api_key_env: String(values.api_key_env || "").trim() || undefined,
                            model: String(values.model || "").trim() || undefined,
                            system_prompt: values.system_prompt || "",
                            review_spec: values.review_spec || "",
                          });
                          message.success("自定义专家已创建");
                          form.resetFields();
                          await loadPage();
                        } catch (error: any) {
                          message.error(error?.message || "创建专家失败");
                        } finally {
                          setCreating(false);
                        }
                      }}
                    >
                      <Form.Item name="expert_id" label="Expert ID" rules={[{ required: true }]}>
                        <Input placeholder="frontend_accessibility" />
                      </Form.Item>
                      <Form.Item name="name" label="英文名" rules={[{ required: true }]}>
                        <Input placeholder="Frontend Accessibility Reviewer" />
                      </Form.Item>
                      <Form.Item name="name_zh" label="中文名" rules={[{ required: true }]}>
                        <Input placeholder="前端可访问性专家" />
                      </Form.Item>
                      <Form.Item name="role" label="角色描述" rules={[{ required: true }]}>
                        <Input placeholder="frontend ux / a11y" />
                      </Form.Item>
                      <Form.Item name="focus_areas" label="关注点">
                        <Input placeholder="accessibility, rendering, state management" />
                      </Form.Item>
                      <Form.Item name="knowledge_sources" label="知识源">
                        <Input placeholder="wcag, a11y-guidelines, design-system-rules" />
                      </Form.Item>
                      <Form.Item name="tool_bindings" label="工具白名单">
                        <Input placeholder="local_diff, coverage_diff" />
                      </Form.Item>
                      <Form.Item name="mcp_tools" label="MCP 白名单">
                        <Input placeholder="playwright.snapshot" />
                      </Form.Item>
                      <Form.Item name="runtime_tool_bindings" label="运行时工具白名单">
                        <Input placeholder="knowledge_search, repo_context_search" />
                      </Form.Item>
                      <Form.Item name="agent_bindings" label="Agent 白名单">
                        <Input placeholder="judge" />
                      </Form.Item>
                      <Space size={12} style={{ display: "flex" }}>
                        <Form.Item name="max_tool_calls" label="最大工具调用数">
                          <InputNumber min={1} max={12} style={{ width: "100%" }} />
                        </Form.Item>
                        <Form.Item name="max_debate_rounds" label="最大辩论轮次">
                          <InputNumber min={1} max={6} style={{ width: "100%" }} />
                        </Form.Item>
                      </Space>
                      <Form.Item name="provider" label="LLM Provider">
                        <Input placeholder="留空则继承系统配置" />
                      </Form.Item>
                      <Form.Item name="api_base_url" label="LLM Base URL">
                        <Input placeholder="留空则继承系统配置" />
                      </Form.Item>
                      <Space size={12} style={{ display: "flex" }}>
                        <Form.Item name="model" label="模型" style={{ flex: 1 }}>
                          <Input placeholder="留空则继承系统配置" />
                        </Form.Item>
                        <Form.Item name="api_key" label="API Key" style={{ flex: 1 }}>
                          <Input.Password placeholder="留空则继承系统配置" />
                        </Form.Item>
                      </Space>
                      <Form.Item name="system_prompt" label="系统提示词">
                        <Input.TextArea rows={4} placeholder="Focus on accessibility regressions first." />
                      </Form.Item>
                      <Form.Item name="review_spec" label="核心审视规范">
                        <Input.TextArea rows={10} placeholder="为这个自定义专家写一份完整的 Markdown 审视规范。" />
                      </Form.Item>
                      <Button type="primary" htmlType="submit" loading={creating}>
                        创建专家
                      </Button>
                    </Form>
                  </Card>
                </Col>
                <Col xs={24} xl={14}>
                  <Card className="module-card" title="已有自定义专家">
                    {customExperts.length ? (
                      <List
                        dataSource={customExperts}
                        renderItem={(item) => {
                          const expertDocs = documents[item.expert_id] || [];
                          const runtimeTools = getRuntimeToolBindings(item);
                          const manualSkills = getManualSkillBindings(item);
                          const extensionSkills = getExtensionSkillBindings(item);
                          return (
                            <List.Item>
                              <List.Item.Meta
                                title={
                                  <Space wrap>
                                    <span>{item.name_zh}</span>
                                    <Tag color={item.enabled ? "success" : "default"}>{item.enabled ? "enabled" : "disabled"}</Tag>
                                    <Tag color="gold">custom</Tag>
                                    <Tag color="purple">绑定文档 {docCountMap[item.expert_id] || 0}</Tag>
                                  </Space>
                                }
                                description={
                                  <div>
                                    <div>{`${item.role} · ${item.focus_areas.join(" / ")}`}</div>
                                    <div style={{ marginTop: 6, color: "var(--text-secondary)" }}>
                                      Extension 绑定：{extensionSkills.length ? extensionSkills.join(" / ") : "无"}
                                    </div>
                                    <div style={{ marginTop: 6, color: "var(--text-secondary)" }}>
                                      专家源码绑定：{manualSkills.length ? manualSkills.join(" / ") : "无"}
                                    </div>
                                    <div style={{ marginTop: 6 }}>
                                      {item.tool_bindings.map((tool) => (
                                        <Tag key={`${item.expert_id}_${tool}`} color="blue">
                                          {tool}
                                        </Tag>
                                      ))}
                                      {runtimeTools.map((tool) => (
                                        <Tag key={`${item.expert_id}_${tool}`} color="gold">
                                          {tool}
                                        </Tag>
                                      ))}
                                      {extensionSkills.map((skill) => (
                                        <Tag key={`${item.expert_id}_${skill}_ext`} color="geekblue">
                                          {`skill ${skill} · extension`}
                                        </Tag>
                                      ))}
                                      {manualSkills.map((skill) => (
                                        <Tag key={`${item.expert_id}_${skill}_manual`} color="default">
                                          {`skill ${skill} · 源码`}
                                        </Tag>
                                      ))}
                                    </div>
                                    <Space style={{ marginTop: 10 }}>
                                      <Button size="small" onClick={() => setDetailTarget(item)}>
                                        展开文档详情
                                      </Button>
                                      <Button size="small" onClick={() => openUploadModal(item)}>
                                        上传并绑定 Markdown
                                      </Button>
                                    </Space>
                                    <div style={{ marginTop: 8, color: "var(--text-secondary)" }}>
                                      核心规范默认折叠，已绑定文档 {expertDocs.length} 篇。
                                    </div>
                                  </div>
                                }
                              />
                            </List.Item>
                          );
                        }}
                      />
                    ) : (
                      <Empty description="当前还没有自定义专家。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                    )}
                  </Card>
                </Col>
              </Row>
            ),
          },
        ]}
      />

      <Modal
        title={uploadTarget ? `为 ${uploadTarget.name_zh} 绑定 Markdown 文档` : "绑定 Markdown 文档"}
        open={Boolean(uploadTarget)}
        onCancel={closeUploadModal}
        footer={null}
        destroyOnClose
      >
        <Form
          form={uploadForm}
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
                doc_type: values.doc_type,
                content,
                tags: String(values.tags || "")
                  .split(",")
                  .map((item: string) => item.trim())
                  .filter(Boolean),
                source_filename: selectedFile.name,
              });
              message.success("专家文档已上传并绑定");
              closeUploadModal();
              await loadPage();
            } catch (error: any) {
              message.error(error?.message || "上传专家文档失败");
            } finally {
              setUploading(false);
            }
          }}
        >
          <Form.Item name="expert_id" label="绑定专家" rules={[{ required: true }]}>
            <Select
              options={experts.map((item) => ({
                value: item.expert_id,
                label: `${item.name_zh} (${item.expert_id})`,
              }))}
            />
          </Form.Item>
          <Form.Item name="doc_type" label="文档类型" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "review_rule", label: "审视规范补充" },
                { value: "domain_reference", label: "领域参考" },
                { value: "runbook", label: "Runbook / Playbook" },
                { value: "reference", label: "通用参考文档" },
              ]}
            />
          </Form.Item>
          <Form.Item name="title" label="文档标题">
            <Input placeholder="留空时默认取文件名" />
          </Form.Item>
          <Form.Item name="tags" label="标签">
            <Input placeholder="java-review, spring, dto, redis" />
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
            上传并绑定
          </Button>
        </Form>
      </Modal>

      <Modal
        title={detailTarget ? `${detailTarget.name_zh} 的规范与绑定文档` : "专家文档详情"}
        open={Boolean(detailTarget)}
        onCancel={closeDetailModal}
        footer={null}
        width={980}
        destroyOnClose
      >
        {detailTarget ? (
          <Space direction="vertical" style={{ width: "100%" }} size={16}>
            <Card size="small" title="核心审视规范">
              {detailTarget.review_spec ? (
                <Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
                  {detailTarget.review_spec}
                </Paragraph>
              ) : (
                <Empty description="该专家尚未配置核心规范文档。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </Card>
            <Card size="small" title={`已绑定 Markdown 文档 (${(documents[detailTarget.expert_id] || []).length})`}>
              {(documents[detailTarget.expert_id] || []).length ? (
                <Collapse
                  items={(documents[detailTarget.expert_id] || []).map((doc) => ({
                    key: doc.doc_id,
                    label: (
                      <Space wrap>
                        <Text strong>{doc.title}</Text>
                        <Tag color="blue">{formatDocTypeLabel(doc.doc_type)}</Tag>
                        <Tag>{doc.source_filename || doc.doc_id}</Tag>
                        {doc.tags.map((tag) => (
                          <Tag key={`${doc.doc_id}_${tag}`} color="purple">
                            {tag}
                          </Tag>
                        ))}
                      </Space>
                    ),
                    children: (
                      <Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
                        {doc.content}
                      </Paragraph>
                    ),
                  }))}
                />
              ) : (
                <Empty description="该专家暂未绑定额外 Markdown 文档。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </Card>
          </Space>
        ) : null}
      </Modal>
    </div>
  );
};

export default ExpertsPage;
