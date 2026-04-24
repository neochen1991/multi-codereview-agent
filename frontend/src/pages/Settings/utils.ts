import type { ExtensionSkill, ExtensionTool, PostgresDataSourceSettings } from "@/services/api";

export type SkillFormValues = ExtensionSkill & {
  bound_experts_text?: string;
  required_tools_text?: string;
  activation_hints_text?: string;
};

export type ToolFormValues = ExtensionTool & {
  allowed_experts_text?: string;
  bound_skills_text?: string;
  input_schema_text?: string;
  output_schema_text?: string;
};

export const stringifyList = (value?: string[]) => (Array.isArray(value) ? value.join(", ") : "");

export const parseList = (value: string) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

export const parseJsonObject = (value: string) => {
  const text = String(value || "").trim();
  if (!text) return {};
  try {
    const parsed = JSON.parse(text);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
};

export const stringifyJson = (value: unknown) => JSON.stringify(value ?? [], null, 2);

export const parseJsonArray = <T,>(value: string): T[] => {
  const text = String(value || "").trim();
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
};

export const toSkillFormValues = (skill: ExtensionSkill): SkillFormValues => ({
  ...skill,
  bound_experts_text: stringifyList(skill.bound_experts),
  required_tools_text: stringifyList(skill.required_tools),
  activation_hints_text: stringifyList(skill.activation_hints),
});

export const defaultSkillFormValues = (): SkillFormValues => ({
  skill_id: "",
  name: "",
  description: "",
  bound_experts: [],
  applicable_experts: [],
  required_tools: [],
  required_doc_types: [],
  activation_hints: [],
  required_context: [],
  allowed_modes: ["standard", "light"],
  output_contract: {},
  prompt_body: "",
  bound_experts_text: "",
  required_tools_text: "",
  activation_hints_text: "",
});

export const toToolFormValues = (tool: ExtensionTool): ToolFormValues => ({
  ...tool,
  allowed_experts_text: stringifyList(tool.allowed_experts),
  bound_skills_text: stringifyList(tool.bound_skills),
  input_schema_text: JSON.stringify(tool.input_schema || {}, null, 2),
  output_schema_text: JSON.stringify(tool.output_schema || {}, null, 2),
});

export const defaultToolFormValues = (): ToolFormValues => ({
  tool_id: "",
  name: "",
  description: "",
  runtime: "python",
  entry: "run.py",
  timeout_seconds: 60,
  allowed_experts: [],
  bound_skills: [],
  input_schema: {},
  output_schema: {},
  run_script: "",
  allowed_experts_text: "",
  bound_skills_text: "",
  input_schema_text: "",
  output_schema_text: "",
});

export const parseDatabaseSources = (value: string) =>
  parseJsonArray<PostgresDataSourceSettings>(String(value || ""));
