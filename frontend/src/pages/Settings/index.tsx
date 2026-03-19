import React from "react";
import { Alert, Button, Card, Col, Descriptions, Form, Input, InputNumber, Row, Select, Switch, Tabs, Typography, message } from "antd";

import {
  expertApi,
  settingsApi,
  type ExpertProfile,
  type ExtensionSkill,
  type ExtensionTool,
  type RuntimeSettings,
} from "@/services/api";

const { Paragraph, Title } = Typography;

const stringifyList = (value?: string[]) => (Array.isArray(value) ? value.join(", ") : "");
const parseList = (value: string) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

const parseJsonObject = (value: string) => {
  const text = String(value || "").trim();
  if (!text) return {};
  try {
    const parsed = JSON.parse(text);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
};

// 设置页负责维护 config.json 对应的系统级运行参数和专家治理配置。
const SettingsPage: React.FC = () => {
  const [form] = Form.useForm<RuntimeSettings>();
  const [skillForm] = Form.useForm<ExtensionSkill & { bound_experts_text?: string; required_tools_text?: string; activation_hints_text?: string }>();
  const [toolForm] = Form.useForm<
    ExtensionTool & {
      allowed_experts_text?: string;
      bound_skills_text?: string;
      input_schema_text?: string;
      output_schema_text?: string;
    }
  >();
  const [loading, setLoading] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [experts, setExperts] = React.useState<ExpertProfile[]>([]);
  const [savingExpertId, setSavingExpertId] = React.useState("");
  const [extensionSkills, setExtensionSkills] = React.useState<ExtensionSkill[]>([]);
  const [extensionTools, setExtensionTools] = React.useState<ExtensionTool[]>([]);
  const [savingSkill, setSavingSkill] = React.useState(false);
  const [savingTool, setSavingTool] = React.useState(false);

  const loadPage = React.useCallback(async () => {
    // 系统设置和专家列表要一起加载，才能在一页内完成全局与专家级配置。
    setLoading(true);
    try {
      const [runtime, expertList, skills, tools] = await Promise.all([
        settingsApi.getRuntime(),
        expertApi.list(),
        settingsApi.listExtensionSkills(),
        settingsApi.listExtensionTools(),
      ]);
      form.setFieldsValue(runtime);
      setExperts(expertList);
      setExtensionSkills(skills);
      setExtensionTools(tools);
      if (skills.length > 0) {
        const first = skills[0];
        skillForm.setFieldsValue({
          ...first,
          bound_experts_text: stringifyList(first.bound_experts),
          required_tools_text: stringifyList(first.required_tools),
          activation_hints_text: stringifyList(first.activation_hints),
        });
      } else {
        skillForm.resetFields();
        skillForm.setFieldsValue({ allowed_modes: ["standard", "light"], prompt_body: "" });
      }
      if (tools.length > 0) {
        const first = tools[0];
        toolForm.setFieldsValue({
          ...first,
          allowed_experts_text: stringifyList(first.allowed_experts),
          bound_skills_text: stringifyList(first.bound_skills),
          input_schema_text: JSON.stringify(first.input_schema || {}, null, 2),
          output_schema_text: JSON.stringify(first.output_schema || {}, null, 2),
        });
      } else {
        toolForm.resetFields();
        toolForm.setFieldsValue({ runtime: "python", entry: "run.py", timeout_seconds: 60, run_script: "" });
      }
    } finally {
      setLoading(false);
    }
  }, [form, skillForm, toolForm]);

  React.useEffect(() => {
    void loadPage();
  }, [loadPage]);

  return (
    <div className="settings-page">
      <Card className="module-card">
        <Title level={3}>系统设置</Title>
        <Paragraph>
          这里统一管理运行时默认配置，以及每个专家 agent 可真实调用的工具、运行时工具和知识源绑定。系统启动必需的配置会写入项目根目录
          {" "}
          <code>config.json</code>
          ，设置页治理项会持久化到 SQLite，并在运行时与系统配置合并生效。
        </Paragraph>
        <Form.Item noStyle shouldUpdate>
          {() =>
            form.getFieldValue("config_path") ? (
              <Alert
                type="info"
                showIcon
                style={{ marginTop: 12 }}
                message={`当前统一配置文件：${String(form.getFieldValue("config_path"))}`}
                description="默认 LLM、Git/CodeHub Token、代码仓地址、自动审核开关与网络校验策略都以这份 config.json 为准；审核治理参数仍可通过设置页持久化到 SQLite。"
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
                auto_review_enabled: Boolean(values.auto_review_enabled),
                auto_review_repo_url: values.code_repo_clone_url || "",
                auto_review_poll_interval_seconds: Number(values.auto_review_poll_interval_seconds || 120),
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
          <Form.Item name="auto_review_enabled" label="启用系统启动后自动拉取 MR 并排队审核" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="自动审核会直接复用上面的“代码仓地址”"
            description="系统启动后拉取开放中的 MR/PR 时，不再单独维护自动审核仓库地址，统一使用 config.json 中已经配置的代码仓地址。"
          />
          <Form.Item name="auto_review_poll_interval_seconds" label="自动拉取轮询间隔（秒）">
            <InputNumber min={15} max={3600} style={{ width: "100%" }} />
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

      <Card className="module-card" title="扩展 Skill / Tool 编辑（extensions）" style={{ marginTop: 16 }} loading={loading}>
        <Tabs
          defaultActiveKey="skills"
          items={[
            {
              key: "skills",
              label: "Skill 编辑",
              children: (
                <Form
                  form={skillForm}
                  layout="vertical"
                  onFinish={async (values) => {
                    const skillId = String(values.skill_id || "").trim();
                    if (!skillId) {
                      message.warning("请先填写 skill_id");
                      return;
                    }
                    setSavingSkill(true);
                    try {
                      await settingsApi.upsertExtensionSkill(skillId, {
                        skill_id: skillId,
                        name: String(values.name || skillId).trim(),
                        description: String(values.description || "").trim(),
                        bound_experts: parseList(String(values.bound_experts_text || "")),
                        applicable_experts: [],
                        required_tools: parseList(String(values.required_tools_text || "")),
                        required_doc_types: [],
                        activation_hints: parseList(String(values.activation_hints_text || "")),
                        required_context: ["diff"],
                        allowed_modes:
                          Array.isArray(values.allowed_modes) && values.allowed_modes.length > 0
                            ? values.allowed_modes
                            : ["standard", "light"],
                        output_contract: {},
                        prompt_body: String(values.prompt_body || ""),
                      });
                      message.success(`Skill ${skillId} 已保存`);
                      await loadPage();
                    } catch (error: any) {
                      message.error(error?.message || "保存 Skill 失败");
                    } finally {
                      setSavingSkill(false);
                    }
                  }}
                >
                  <Form.Item label="加载已有 Skill">
                    <Select
                      allowClear
                      placeholder="选择一个已有 skill 加载到编辑器"
                      options={extensionSkills.map((item) => ({ label: `${item.name} (${item.skill_id})`, value: item.skill_id }))}
                      onChange={(value) => {
                        const selected = extensionSkills.find((item) => item.skill_id === value);
                        if (!selected) {
                          skillForm.resetFields();
                          skillForm.setFieldsValue({ allowed_modes: ["standard", "light"], prompt_body: "" });
                          return;
                        }
                        skillForm.setFieldsValue({
                          ...selected,
                          bound_experts_text: stringifyList(selected.bound_experts),
                          required_tools_text: stringifyList(selected.required_tools),
                          activation_hints_text: stringifyList(selected.activation_hints),
                        });
                      }}
                    />
                  </Form.Item>
                  <Form.Item name="skill_id" label="skill_id" rules={[{ required: true, message: "请输入 skill_id" }]}>
                    <Input placeholder="design-consistency-check" />
                  </Form.Item>
                  <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
                    <Input placeholder="详细设计一致性检查" />
                  </Form.Item>
                  <Form.Item name="description" label="说明">
                    <Input placeholder="该 skill 在专家审查中的职责说明" />
                  </Form.Item>
                  <Form.Item name="bound_experts_text" label="绑定专家（逗号分隔 expert_id）">
                    <Input placeholder="correctness_business, architecture_design" />
                  </Form.Item>
                  <Form.Item name="required_tools_text" label="依赖工具（逗号分隔 tool_id）">
                    <Input placeholder="design_spec_alignment, repo_context_search" />
                  </Form.Item>
                  <Form.Item name="activation_hints_text" label="激活提示词（逗号分隔）">
                    <Input placeholder="design, api, schema" />
                  </Form.Item>
                  <Form.Item name="allowed_modes" label="可用模式">
                    <Select
                      mode="multiple"
                      options={[
                        { label: "standard", value: "standard" },
                        { label: "light", value: "light" },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="prompt_body" label="SKILL.md 内容">
                    <Input.TextArea rows={14} placeholder="在这里编辑 SKILL.md 内容" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={savingSkill}>
                    保存 Skill
                  </Button>
                </Form>
              ),
            },
            {
              key: "tools",
              label: "Tool 编辑",
              children: (
                <Form
                  form={toolForm}
                  layout="vertical"
                  onFinish={async (values) => {
                    const toolId = String(values.tool_id || "").trim();
                    if (!toolId) {
                      message.warning("请先填写 tool_id");
                      return;
                    }
                    setSavingTool(true);
                    try {
                      await settingsApi.upsertExtensionTool(toolId, {
                        tool_id: toolId,
                        name: String(values.name || toolId).trim(),
                        description: String(values.description || "").trim(),
                        runtime: String(values.runtime || "python").trim() || "python",
                        entry: String(values.entry || "run.py").trim() || "run.py",
                        timeout_seconds: Number(values.timeout_seconds || 60),
                        allowed_experts: parseList(String(values.allowed_experts_text || "")),
                        bound_skills: parseList(String(values.bound_skills_text || "")),
                        input_schema: parseJsonObject(String(values.input_schema_text || "")),
                        output_schema: parseJsonObject(String(values.output_schema_text || "")),
                        run_script: String(values.run_script || ""),
                      });
                      message.success(`Tool ${toolId} 已保存`);
                      await loadPage();
                    } catch (error: any) {
                      message.error(error?.message || "保存 Tool 失败");
                    } finally {
                      setSavingTool(false);
                    }
                  }}
                >
                  <Form.Item label="加载已有 Tool">
                    <Select
                      allowClear
                      placeholder="选择一个已有 tool 加载到编辑器"
                      options={extensionTools.map((item) => ({ label: `${item.name} (${item.tool_id})`, value: item.tool_id }))}
                      onChange={(value) => {
                        const selected = extensionTools.find((item) => item.tool_id === value);
                        if (!selected) {
                          toolForm.resetFields();
                          toolForm.setFieldsValue({ runtime: "python", entry: "run.py", timeout_seconds: 60, run_script: "" });
                          return;
                        }
                        toolForm.setFieldsValue({
                          ...selected,
                          allowed_experts_text: stringifyList(selected.allowed_experts),
                          bound_skills_text: stringifyList(selected.bound_skills),
                          input_schema_text: JSON.stringify(selected.input_schema || {}, null, 2),
                          output_schema_text: JSON.stringify(selected.output_schema || {}, null, 2),
                        });
                      }}
                    />
                  </Form.Item>
                  <Form.Item name="tool_id" label="tool_id" rules={[{ required: true, message: "请输入 tool_id" }]}>
                    <Input placeholder="design_spec_alignment" />
                  </Form.Item>
                  <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
                    <Input placeholder="详细设计一致性检查工具" />
                  </Form.Item>
                  <Form.Item name="description" label="说明">
                    <Input placeholder="该 tool 的执行目的与输出说明" />
                  </Form.Item>
                  <Form.Item name="runtime" label="运行时">
                    <Input placeholder="python" />
                  </Form.Item>
                  <Form.Item name="entry" label="入口文件">
                    <Input placeholder="run.py" />
                  </Form.Item>
                  <Form.Item name="timeout_seconds" label="超时（秒）">
                    <InputNumber min={5} max={600} style={{ width: "100%" }} />
                  </Form.Item>
                  <Form.Item name="allowed_experts_text" label="允许专家（逗号分隔 expert_id）">
                    <Input placeholder="correctness_business" />
                  </Form.Item>
                  <Form.Item name="bound_skills_text" label="绑定 Skill（逗号分隔 skill_id）">
                    <Input placeholder="design-consistency-check" />
                  </Form.Item>
                  <Form.Item name="input_schema_text" label="输入 Schema（JSON）">
                    <Input.TextArea rows={6} placeholder='{"type":"object","properties":{}}' />
                  </Form.Item>
                  <Form.Item name="output_schema_text" label="输出 Schema（JSON）">
                    <Input.TextArea rows={6} placeholder='{"type":"object","properties":{}}' />
                  </Form.Item>
                  <Form.Item name="run_script" label="入口脚本内容">
                    <Input.TextArea rows={14} placeholder="在这里编辑 run.py 内容" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={savingTool}>
                    保存 Tool
                  </Button>
                </Form>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
};

export default SettingsPage;
