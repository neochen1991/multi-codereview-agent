import React from "react";
import { Alert, Button, Card, Col, Descriptions, Form, Input, InputNumber, Row, Select, Switch, Typography, message } from "antd";

import { expertApi, settingsApi, type ExpertProfile, type RuntimeSettings } from "@/services/api";

const { Paragraph, Title } = Typography;

const stringifyList = (value?: string[]) => (Array.isArray(value) ? value.join(", ") : "");
const parseList = (value: string) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

// 设置页负责维护 config.json 对应的系统级运行参数和专家治理配置。
const SettingsPage: React.FC = () => {
  const [form] = Form.useForm<RuntimeSettings>();
  const [loading, setLoading] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [experts, setExperts] = React.useState<ExpertProfile[]>([]);
  const [savingExpertId, setSavingExpertId] = React.useState("");

  const loadPage = React.useCallback(async () => {
    // 系统设置和专家列表要一起加载，才能在一页内完成全局与专家级配置。
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
          这里统一管理运行时默认配置，以及每个专家 agent 可真实调用的工具、运行时工具和知识源绑定。设置页改动会直接影响审核 runtime，并同步写入项目根目录 config.json。
        </Paragraph>
        <Form.Item noStyle shouldUpdate>
          {() =>
            form.getFieldValue("config_path") ? (
              <Alert
                type="info"
                showIcon
                style={{ marginTop: 12 }}
                message={`当前统一配置文件：${String(form.getFieldValue("config_path"))}`}
                description="默认 LLM 配置、Git Access Token 和代码仓配置都统一保存在这份 config.json 中。"
              />
            ) : null
          }
        </Form.Item>
      </Card>

      <Card className="module-card" title="当前实现状态" style={{ marginTop: 16 }}>
        <Descriptions column={1}>
          <Descriptions.Item label="日志落盘">前后端日志统一输出到项目根目录 logs/</Descriptions.Item>
          <Descriptions.Item label="知识检索">按专家绑定 Markdown 文档，并通过 glob / rg 命中片段</Descriptions.Item>
          <Descriptions.Item label="运行时工具调用">每个专家按 runtime_tool_bindings 真实调用本地 review tool gateway</Descriptions.Item>
          <Descriptions.Item label="代码仓上下文">所有专家可基于配置好的目标代码仓检索目标分支源码上下文</Descriptions.Item>
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
                default_analysis_mode: values.default_analysis_mode || "standard",
                code_repo_clone_url: values.code_repo_clone_url || "",
                code_repo_local_path: values.code_repo_local_path || "",
                code_repo_default_branch: values.code_repo_default_branch || values.default_target_branch || "main",
                code_repo_access_token: String(values.code_repo_access_token || "").trim() || undefined,
                github_access_token: String(values.github_access_token || "").trim() || undefined,
                gitlab_access_token: String(values.gitlab_access_token || "").trim() || undefined,
                codehub_access_token: String(values.codehub_access_token || "").trim() || undefined,
                code_repo_auto_sync: Boolean(values.code_repo_auto_sync),
                tool_allowlist: parseList(String(values.tool_allowlist || "")),
                mcp_allowlist: parseList(String(values.mcp_allowlist || "")),
                runtime_tool_allowlist: parseList(String(values.runtime_tool_allowlist || "")),
                agent_allowlist: parseList(String(values.agent_allowlist || "")),
                allow_human_gate: Boolean(values.allow_human_gate),
                default_max_debate_rounds: Number(values.default_max_debate_rounds || 2),
                standard_llm_timeout_seconds: Number(values.standard_llm_timeout_seconds || 60),
                standard_llm_retry_count: Number(values.standard_llm_retry_count || 3),
                standard_max_parallel_experts: Number(values.standard_max_parallel_experts || 4),
                light_llm_timeout_seconds: Number(values.light_llm_timeout_seconds || 120),
                light_llm_retry_count: Number(values.light_llm_retry_count || 2),
                light_max_parallel_experts: Number(values.light_max_parallel_experts || 1),
                light_max_debate_rounds: Number(values.light_max_debate_rounds || 1),
                default_llm_provider: values.default_llm_provider || "dashscope-openai-compatible",
                default_llm_base_url: values.default_llm_base_url || "https://coding.dashscope.aliyuncs.com/v1",
                default_llm_model: values.default_llm_model || "kimi-k2.5",
                default_llm_api_key_env: String(values.default_llm_api_key_env || "").trim() || undefined,
                default_llm_api_key: String(values.default_llm_api_key || "").trim() || undefined,
                allow_llm_fallback: Boolean(values.allow_llm_fallback),
                verify_ssl: Boolean(values.verify_ssl),
                use_system_trust_store: Boolean(values.use_system_trust_store),
                ca_bundle_path: values.ca_bundle_path || "",
              });
              message.success("运行时设置已更新");
              form.setFieldValue("default_llm_api_key", "");
              form.setFieldValue("code_repo_access_token", "");
              form.setFieldValue("github_access_token", "");
              form.setFieldValue("gitlab_access_token", "");
              form.setFieldValue("codehub_access_token", "");
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
          <Form.Item name="default_analysis_mode" label="默认审核模式">
            <Select
              options={[
                { label: "标准模式", value: "standard" },
                { label: "轻量模式", value: "light" },
              ]}
            />
          </Form.Item>
          <Form.Item name="code_repo_clone_url" label="代码仓 Git 地址">
            <Input placeholder="https://github.com/org/repo.git" />
          </Form.Item>
          <Form.Item name="code_repo_local_path" label="本地代码仓目录">
            <Input placeholder="/Users/neochen/code/repo" />
          </Form.Item>
          <Form.Item name="code_repo_default_branch" label="代码仓默认分支">
            <Input placeholder="main" />
          </Form.Item>
          <Form.Item name="code_repo_access_token" label="代码仓 Access Token">
            <Input.Password placeholder="留空则保持当前已配置的代码仓 token" />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {() =>
              Boolean(form.getFieldValue("code_repo_access_token_configured")) ? (
                <Alert
                  type="success"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="当前已在配置文件中保存代码仓 Access Token"
                  description="已保存的 token 不会在页面回显；留空保存会保留现有配置。"
                />
              ) : null
            }
          </Form.Item>
          <Form.Item name="github_access_token" label="GitHub Token">
            <Input.Password placeholder="优先用于 github.com 链接" />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {() =>
              Boolean(form.getFieldValue("github_access_token_configured")) ? (
                <Alert
                  type="success"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="当前已在配置文件中保存 GitHub Token"
                  description="已保存的 token 不会在页面回显；留空保存会保留现有配置。"
                />
              ) : null
            }
          </Form.Item>
          <Form.Item name="gitlab_access_token" label="GitLab Token">
            <Input.Password placeholder="优先用于 gitlab 链接" />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {() =>
              Boolean(form.getFieldValue("gitlab_access_token_configured")) ? (
                <Alert
                  type="success"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="当前已在配置文件中保存 GitLab Token"
                  description="已保存的 token 不会在页面回显；留空保存会保留现有配置。"
                />
              ) : null
            }
          </Form.Item>
          <Form.Item name="codehub_access_token" label="CodeHub Token">
            <Input.Password placeholder="优先用于 codehub 链接" />
          </Form.Item>
          <Form.Item noStyle shouldUpdate>
            {() =>
              Boolean(form.getFieldValue("codehub_access_token_configured")) ? (
                <Alert
                  type="success"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="当前已在配置文件中保存 CodeHub Token"
                  description="已保存的 token 不会在页面回显；留空保存会保留现有配置。"
                />
              ) : null
            }
          </Form.Item>
          <Form.Item name="code_repo_auto_sync" label="自动同步目标分支代码仓" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item
            name="tool_allowlist"
            label="全局工具白名单"
            getValueProps={(value) => ({ value: stringifyList(value as string[]) })}
          >
            <Input placeholder="local_diff, schema_diff, coverage_diff" />
          </Form.Item>
          <Form.Item
            name="runtime_tool_allowlist"
            label="全局运行时工具白名单"
            getValueProps={(value) => ({ value: stringifyList(value as string[]) })}
          >
            <Input placeholder="knowledge_search, diff_inspector, test_surface_locator, dependency_surface_locator, repo_context_search" />
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
          <Form.Item name="standard_llm_timeout_seconds" label="标准模式 LLM 超时（秒）">
            <InputNumber min={10} max={300} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="standard_llm_retry_count" label="标准模式 LLM 重试次数">
            <InputNumber min={1} max={5} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="standard_max_parallel_experts" label="标准模式最大并发专家数">
            <InputNumber min={1} max={8} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="light_llm_timeout_seconds" label="轻量模式 LLM 超时（秒）">
            <InputNumber min={10} max={600} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="light_llm_retry_count" label="轻量模式 LLM 重试次数">
            <InputNumber min={1} max={5} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="light_max_parallel_experts" label="轻量模式最大并发专家数">
            <InputNumber min={1} max={4} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="light_max_debate_rounds" label="轻量模式最大辩论轮次">
            <InputNumber min={1} max={3} style={{ width: "100%" }} />
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
          <Form.Item name="verify_ssl" label="启用 HTTPS 证书校验" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="use_system_trust_store" label="优先使用系统证书库" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="ca_bundle_path" label="自定义 CA Bundle 路径">
            <Input placeholder="C:\\certs\\corp-ca.pem" />
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
                    runtime_tool_bindings: stringifyList(expert.runtime_tool_bindings),
                  }}
                  onFinish={async (values) => {
                    setSavingExpertId(expert.expert_id);
                    try {
                      await expertApi.update(expert.expert_id, {
                        ...expert,
                        knowledge_sources: parseList(values.knowledge_sources || ""),
                        tool_bindings: parseList(values.tool_bindings || ""),
                        runtime_tool_bindings: parseList(values.runtime_tool_bindings || ""),
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
                  <Form.Item name="runtime_tool_bindings" label="运行时工具绑定">
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
