import React, { useEffect, useState } from "react";
import { Button, Card, Col, Form, Input, InputNumber, List, Row, Space, Tag, Typography, message } from "antd";

import { expertApi, type ExpertProfile } from "@/services/api";

const { Paragraph, Title } = Typography;

const formatOptionalLlmValue = (value?: string | null, fallbackLabel = "继承系统配置") =>
  value && value.trim() ? value.trim() : fallbackLabel;

const ExpertsPage: React.FC = () => {
  const [experts, setExperts] = useState<ExpertProfile[]>([]);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    void expertApi.list().then(setExperts);
  }, []);

  const reloadExperts = async () => {
    const next = await expertApi.list();
    setExperts(next);
  };

  return (
    <div>
      <Card className="module-card">
        <Title level={3}>专家配置中心</Title>
        <Paragraph>
          页面布局沿用参考项目的模块卡片风格，这里承载代码审核专家的职责、提示词和知识绑定信息。
        </Paragraph>
      </Card>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
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
                    focus_areas: String(values.focus_areas || "").split(",").map((item: string) => item.trim()).filter(Boolean),
                    knowledge_sources: String(values.knowledge_sources || "").split(",").map((item: string) => item.trim()).filter(Boolean),
                    tool_bindings: String(values.tool_bindings || "").split(",").map((item: string) => item.trim()).filter(Boolean),
                    mcp_tools: String(values.mcp_tools || "").split(",").map((item: string) => item.trim()).filter(Boolean),
                    skill_bindings: String(values.skill_bindings || "").split(",").map((item: string) => item.trim()).filter(Boolean),
                    agent_bindings: String(values.agent_bindings || "").split(",").map((item: string) => item.trim()).filter(Boolean),
                    max_tool_calls: values.max_tool_calls || 4,
                    max_debate_rounds: values.max_debate_rounds || 2,
                    provider: String(values.provider || "").trim() || undefined,
                    api_base_url: String(values.api_base_url || "").trim() || undefined,
                    api_key: String(values.api_key || "").trim() || undefined,
                    api_key_env: String(values.api_key_env || "").trim() || undefined,
                    model: String(values.model || "").trim() || undefined,
                    system_prompt: values.system_prompt || "",
                  });
                  message.success("自定义专家已创建");
                  form.resetFields();
                  await reloadExperts();
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
                <Input placeholder="a11y-guidelines, design-system-rules" />
              </Form.Item>
              <Form.Item name="tool_bindings" label="工具白名单">
                <Input placeholder="local_diff, coverage_diff" />
              </Form.Item>
              <Form.Item name="mcp_tools" label="MCP 白名单">
                <Input placeholder="playwright.snapshot" />
              </Form.Item>
              <Form.Item name="skill_bindings" label="Skill 白名单">
                <Input placeholder="frontend-design" />
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
                <Input.TextArea rows={5} placeholder="Focus on accessibility regressions first." />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={creating}>
                创建专家
              </Button>
            </Form>
          </Card>
        </Col>
        <Col xs={24} xl={14}>
          <Card className="module-card" title="内置专家">
            <List
              dataSource={experts}
              renderItem={(item) => (
                <List.Item>
                  <List.Item.Meta
                    title={
                      <>
                        {item.name_zh} <Tag color={item.enabled ? "success" : "default"}>{item.enabled ? "enabled" : "disabled"}</Tag>
                        {item.custom ? <Tag color="processing">custom</Tag> : <Tag>builtin</Tag>}
                      </>
                    }
                    description={
                      <div>
                        <div>{`${item.role} · ${item.focus_areas.join(" / ")}`}</div>
                        <div style={{ marginTop: 6 }}>
                          <Tag color="geekblue">{formatOptionalLlmValue(item.model)}</Tag>
                          <Tag color="volcano">{formatOptionalLlmValue(item.provider)}</Tag>
                          <Tag>{item.api_key ? "已配置 API Key" : formatOptionalLlmValue(item.api_key_env)}</Tag>
                        </div>
                        {item.api_base_url ? <div style={{ marginTop: 6, color: "rgba(255,255,255,0.65)" }}>{item.api_base_url}</div> : null}
                        <div style={{ marginTop: 6 }}>
                          {item.tool_bindings.map((tool) => (
                            <Tag key={`${item.expert_id}_${tool}`} color="blue">{tool}</Tag>
                          ))}
                          {item.mcp_tools.map((tool) => (
                            <Tag key={`${item.expert_id}_${tool}`} color="purple">{tool}</Tag>
                          ))}
                          {item.skill_bindings.map((tool) => (
                            <Tag key={`${item.expert_id}_${tool}`} color="gold">{tool}</Tag>
                          ))}
                          {item.agent_bindings.map((tool) => (
                            <Tag key={`${item.expert_id}_${tool}`} color="cyan">{tool}</Tag>
                          ))}
                        </div>
                      </div>
                    }
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default ExpertsPage;
