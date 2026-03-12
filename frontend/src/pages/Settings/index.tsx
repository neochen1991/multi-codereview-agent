import React from "react";
import { Alert, Button, Card, Col, Descriptions, Form, Input, InputNumber, Row, Switch, Typography, message } from "antd";

import { expertApi, settingsApi, type ExpertProfile, type RuntimeSettings } from "@/services/api";

const { Paragraph, Title } = Typography;

const stringifyList = (value?: string[]) => (Array.isArray(value) ? value.join(", ") : "");
const parseList = (value: string) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

const SettingsPage: React.FC = () => {
  const [form] = Form.useForm<RuntimeSettings>();
  const [loading, setLoading] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [experts, setExperts] = React.useState<ExpertProfile[]>([]);
  const [savingExpertId, setSavingExpertId] = React.useState("");

  const loadPage = React.useCallback(async () => {
    setLoading(true);
    try {
      const [runtime, expertList] = await Promise.all([settingsApi.getRuntime(), expertApi.list()]);
      form.setFieldsValue(runtime);
      setExperts(expertList);
    } finally {
      setLoading(false);
    }
  }, [form]);

  React.useEffect(() => {
    void loadPage();
  }, [loadPage]);

  return (
    <div className="settings-page">
      <Card className="module-card">
        <Title level={3}>系统设置</Title>
        <Paragraph>
          这里统一管理运行时默认配置，以及每个专家 agent 可真实调用的工具、skill 和知识源绑定。设置页改动会直接影响审核 runtime。
        </Paragraph>
      </Card>

      <Card className="module-card" title="当前实现状态" style={{ marginTop: 16 }}>
        <Descriptions column={1}>
          <Descriptions.Item label="日志落盘">前后端日志统一输出到项目根目录 logs/</Descriptions.Item>
          <Descriptions.Item label="知识检索">按专家绑定 Markdown 文档，并通过 glob / rg 命中片段</Descriptions.Item>
          <Descriptions.Item label="Skill 调用">每个专家按 skill_bindings 真实调用本地 skill gateway</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card className="module-card" title="运行时治理配置" style={{ marginTop: 16 }} loading={loading}>
        <Form
          form={form}
          layout="vertical"
          onFinish={async (values) => {
            setSaving(true);
            try {
              await settingsApi.updateRuntime({
                default_target_branch: values.default_target_branch,
                tool_allowlist: parseList(String(values.tool_allowlist || "")),
                mcp_allowlist: parseList(String(values.mcp_allowlist || "")),
                skill_allowlist: parseList(String(values.skill_allowlist || "")),
                agent_allowlist: parseList(String(values.agent_allowlist || "")),
                allow_human_gate: Boolean(values.allow_human_gate),
                default_max_debate_rounds: Number(values.default_max_debate_rounds || 2),
                default_llm_provider: values.default_llm_provider || "dashscope-openai-compatible",
                default_llm_base_url: values.default_llm_base_url || "https://coding.dashscope.aliyuncs.com/v1",
                default_llm_model: values.default_llm_model || "kimi-k2.5",
                default_llm_api_key_env: String(values.default_llm_api_key_env || "").trim() || undefined,
                default_llm_api_key: String(values.default_llm_api_key || "").trim() || undefined,
                allow_llm_fallback: Boolean(values.allow_llm_fallback),
              });
              message.success("运行时设置已更新");
              form.setFieldValue("default_llm_api_key", "");
            } catch (error: any) {
              message.error(error?.message || "更新设置失败");
            } finally {
              setSaving(false);
            }
          }}
        >
          <Form.Item name="default_target_branch" label="默认目标分支">
            <Input placeholder="main" />
          </Form.Item>
          <Form.Item
            name="tool_allowlist"
            label="全局工具白名单"
            getValueProps={(value) => ({ value: stringifyList(value as string[]) })}
          >
            <Input placeholder="local_diff, schema_diff, coverage_diff" />
          </Form.Item>
          <Form.Item
            name="skill_allowlist"
            label="全局 Skill 白名单"
            getValueProps={(value) => ({ value: stringifyList(value as string[]) })}
          >
            <Input placeholder="knowledge_search, diff_inspector, test_surface_locator, dependency_surface_locator" />
          </Form.Item>
          <Form.Item
            name="mcp_allowlist"
            label="MCP 白名单"
            getValueProps={(value) => ({ value: stringifyList(value as string[]) })}
          >
            <Input placeholder="github.diff, playwright.snapshot" />
          </Form.Item>
          <Form.Item
            name="agent_allowlist"
            label="Agent 白名单"
            getValueProps={(value) => ({ value: stringifyList(value as string[]) })}
          >
            <Input placeholder="judge, main_agent" />
          </Form.Item>
          <Form.Item name="default_max_debate_rounds" label="默认辩论轮次">
            <InputNumber min={1} max={6} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="default_llm_provider" label="默认 LLM Provider">
            <Input placeholder="dashscope-openai-compatible" />
          </Form.Item>
          <Form.Item name="default_llm_base_url" label="默认 LLM Base URL">
            <Input placeholder="https://coding.dashscope.aliyuncs.com/v1" />
          </Form.Item>
          <Form.Item name="default_llm_model" label="默认模型">
            <Input placeholder="kimi-k2.5" />
          </Form.Item>
          <Form.Item name="default_llm_api_key" label="默认 API Key">
            <Input.Password placeholder="留空则保持当前已配置的 API Key" />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {() =>
              Boolean(form.getFieldValue("default_llm_api_key_configured")) ? (
                <Alert
                  type="success"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="当前已在配置文件中保存默认 API Key"
                  description="出于安全考虑，已保存的 API Key 不会在页面回显；留空保存会保留现有配置。"
                />
              ) : null
            }
          </Form.Item>
          <Form.Item name="allow_llm_fallback" label="允许 LLM Fallback" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="allow_human_gate" label="允许人工 Gate" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saving}>
            保存运行时设置
          </Button>
        </Form>
      </Card>

      <Card className="module-card" title="专家 Tool / Skill / 知识源配置" style={{ marginTop: 16 }} loading={loading}>
        <Row gutter={[16, 16]}>
          {experts.map((expert) => (
            <Col xs={24} xl={12} key={expert.expert_id}>
              <Card size="small" className="module-card" title={`${expert.name_zh} (${expert.expert_id})`}>
                <Form
                  layout="vertical"
                  initialValues={{
                    knowledge_sources: stringifyList(expert.knowledge_sources),
                    tool_bindings: stringifyList(expert.tool_bindings),
                    skill_bindings: stringifyList(expert.skill_bindings),
                  }}
                  onFinish={async (values) => {
                    setSavingExpertId(expert.expert_id);
                    try {
                      await expertApi.update(expert.expert_id, {
                        ...expert,
                        knowledge_sources: parseList(values.knowledge_sources || ""),
                        tool_bindings: parseList(values.tool_bindings || ""),
                        skill_bindings: parseList(values.skill_bindings || ""),
                      });
                      message.success(`${expert.name_zh} 配置已更新`);
                      await loadPage();
                    } catch (error: any) {
                      message.error(error?.message || "更新专家配置失败");
                    } finally {
                      setSavingExpertId("");
                    }
                  }}
                >
                  <Form.Item name="knowledge_sources" label="知识源绑定">
                    <Input placeholder="security-review-checklist, auth-guideline" />
                  </Form.Item>
                  <Form.Item name="tool_bindings" label="工具绑定">
                    <Input placeholder="local_diff, schema_diff" />
                  </Form.Item>
                  <Form.Item name="skill_bindings" label="Skill 绑定">
                    <Input placeholder="knowledge_search, diff_inspector" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={savingExpertId === expert.expert_id}>
                    保存该专家配置
                  </Button>
                </Form>
              </Card>
            </Col>
          ))}
        </Row>
      </Card>
    </div>
  );
};

export default SettingsPage;
