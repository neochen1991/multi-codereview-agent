import React from "react";
import { Alert, Button, Card, Col, Collapse, Descriptions, Form, Input, InputNumber, Row, Select, Space, Switch, Tabs, Tag, Typography, Upload, message } from "antd";
import type { UploadProps } from "antd";
import { CopyOutlined, DownOutlined, FileSearchOutlined, ReloadOutlined, RightOutlined, ToolOutlined } from "@ant-design/icons";

import {
  expertApi,
  settingsApi,
  type CodeGraphIndexStatus,
  type CodeRepositorySettings,
  type ExpertProfile,
  type ExtensionSkill,
  type ExtensionTool,
  type GitNexusIndexStatus,
  type GitNexusPreflightStatus,
  type ImpactReportTemplateAnalysis,
  type ImpactReportTemplate,
  type ImpactReportTemplatePreview,
  type PostgresDataSourceSettings,
  type ProjectSettings,
  type ReviewWorkspaceCleanupResult,
  type RuntimeSettings,
  type SastToolStatusItem,
  type SastToolsStatus,
} from "@/services/api";
import { humanizeExpertId, humanizeReviewText } from "@/utils/displayText";

const { Paragraph, Text, Title } = Typography;

type SettingsSectionCardProps = {
  title: React.ReactNode;
  expanded: boolean;
  onToggle: () => void;
  className?: string;
  style?: React.CSSProperties;
  loading?: boolean;
  extra?: React.ReactNode;
  children: React.ReactNode;
};

const SettingsSectionCard: React.FC<SettingsSectionCardProps> = ({
  title,
  expanded,
  onToggle,
  className = "module-card",
  style,
  loading = false,
  extra,
  children,
}) => {
  const handleCardClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (target.closest(".ant-card-head") && !target.closest(".ant-card-extra")) {
      onToggle();
    }
  };

  return (
    <Card
      className={`${className} settings-section-card${expanded ? " is-expanded" : ""}`}
      title={<span className="settings-section-card-title">{title}</span>}
      style={style}
      loading={expanded && loading}
      onClick={handleCardClick}
      styles={{ body: expanded ? undefined : { display: "none" } }}
      extra={(
        <div className="settings-section-card-extra" onClick={(event) => event.stopPropagation()}>
          <Space wrap>
            {extra}
            <Button
              size="small"
              type="text"
              icon={expanded ? <DownOutlined /> : <RightOutlined />}
              onClick={onToggle}
            >
              {expanded ? "收起" : "展开"}
            </Button>
          </Space>
        </div>
      )}
    >
      {children}
    </Card>
  );
};

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

const stringifyJson = (value: unknown) => JSON.stringify(value ?? [], null, 2);

const parseJsonArray = <T,>(value: string): T[] => {
  const text = String(value || "").trim();
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
};

const normalizeCodeRepositories = (value: unknown): CodeRepositorySettings[] => {
  if (Array.isArray(value)) {
    return value as CodeRepositorySettings[];
  }
  return parseJsonArray<CodeRepositorySettings>(String(value || ""));
};

const currentProjectFromRuntime = (runtime?: Pick<RuntimeSettings, "default_project_id" | "projects"> | null): ProjectSettings | undefined => {
  const projects = runtime?.projects || [];
  const currentProjectId = String(runtime?.default_project_id || "").trim();
  return projects.find((project) => project.project_id === currentProjectId) || projects[0];
};

const currentProjectRepositories = (runtime?: Pick<RuntimeSettings, "default_project_id" | "projects" | "code_repositories"> | null): CodeRepositorySettings[] => {
  const project = currentProjectFromRuntime(runtime);
  if (project?.repositories?.length) return project.repositories;
  return [];
};

const analysisModeDisplay = (mode?: string) =>
  mode === "light"
    ? {
        title: "轻量模式",
        detail: "少量角色串行执行，适合日常小 MR",
      }
    : {
        title: "标准模式",
        detail: "更多上下文和并发，适合常规 MR",
      };

const reviewQualityModeDisplay = (mode?: string) =>
  mode === "thorough_review"
    ? {
        title: "深度检视",
        detail: "固定补入核心质量角色，适合高风险 MR",
        color: "gold",
      }
    : {
        title: "普通模式",
        detail: "只执行主 Agent 选中的角色，适合小变更",
        color: "green",
      };

const buildRuntimeProjectsWithRepositories = (runtime: RuntimeSettings, repositories: CodeRepositorySettings[]): ProjectSettings[] => {
  const projects = runtime.projects || [];
  const currentProjectId = String(runtime.default_project_id || projects[0]?.project_id || "").trim();
  if (!currentProjectId) return projects;
  if (!projects.length) {
    return [
      {
        project_id: currentProjectId,
        name: "默认项目",
        status: "active",
        repositories,
      },
    ];
  }
  return projects.map((project) =>
    project.project_id === currentProjectId
      ? {
          ...project,
          repositories,
        }
      : project,
  );
};

const collapseExpandIconPosition = "end" as const;

const fallbackSastCommandTools: SastToolStatusItem[] = [
  {
    tool: "semgrep",
    kind: "command",
    category: "security",
    purpose: "通用语义规则和安全规则扫描，适合发现 SQL 注入、越权、敏感信息暴露以及项目自定义规则命中。",
    status: "unknown",
    verify_commands: ["semgrep --version", "where semgrep"],
    install: {
      windows: "py -m pip install semgrep",
      macos_linux: "python3 -m pip install semgrep",
    },
  },
  {
    tool: "pmd",
    kind: "command",
    category: "java_quality",
    purpose: "Java 代码质量扫描，适合发现空 catch、复杂度过高、低效循环、重复逻辑和常见坏味道。",
    status: "unknown",
    verify_commands: ["pmd --version", "where pmd"],
    install: {
      windows: "choco install pmd 或下载 PMD 并把 bin 加入 PATH",
      macos_linux: "brew install pmd 或下载 PMD 并把 bin 加入 PATH",
    },
  },
  {
    tool: "checkstyle",
    kind: "command",
    category: "java_quality",
    purpose: "Java 编码规范扫描，适合检查命名、导入、格式、注释和团队约定的风格规则。",
    status: "unknown",
    verify_commands: ["checkstyle --version", "where checkstyle"],
    install: {
      windows: "choco install checkstyle 或下载 checkstyle jar 并配置 PATH 包装命令",
      macos_linux: "brew install checkstyle",
    },
  },
  {
    tool: "eslint",
    kind: "command",
    category: "frontend_quality",
    purpose: "JavaScript/TypeScript 静态检查，适合发现 React Hook 误用、未处理 Promise、可疑依赖和前端质量问题。",
    status: "unknown",
    verify_commands: ["eslint --version", "where eslint"],
    install: {
      windows: "npm install -g eslint",
      macos_linux: "npm install -g eslint",
    },
  },
  {
    tool: "bandit",
    kind: "command",
    category: "python_security",
    purpose: "Python 安全扫描，适合发现危险函数调用、弱加密、硬编码密钥、命令注入和不安全反序列化。",
    status: "unknown",
    verify_commands: ["bandit --version", "where bandit"],
    install: {
      windows: "py -m pip install bandit",
      macos_linux: "python3 -m pip install bandit",
    },
  },
];

const fallbackSastReportTools: SastToolStatusItem[] = [
  {
    tool: "spotbugs",
    kind: "report",
    category: "java_quality",
    purpose: "读取 Java 字节码缺陷报告，适合补充空指针、资源泄漏、并发缺陷和安全 bug pattern 信号。",
    status: "requires_report",
    report_paths: ["target/spotbugsXml.xml", "target/spotbugs.xml", "target/site/spotbugs.xml", "build/reports/spotbugs/main.xml", "build/reports/spotbugs/test.xml", "spotbugs.xml"],
  },
  {
    tool: "archunit",
    kind: "report",
    category: "architecture",
    purpose: "读取架构测试报告，适合发现分层依赖反向、包边界穿透、DDD 边界破坏等架构约束失败。",
    status: "requires_report",
    report_paths: ["target/surefire-reports/*.xml", "target/failsafe-reports/*.xml", "build/test-results/**/*.xml"],
  },
  {
    tool: "jacoco",
    kind: "report",
    category: "test_coverage",
    purpose: "读取测试覆盖率报告，适合提示本次变更附近的未覆盖行，作为补测试和风险复核候选信号。",
    status: "requires_report",
    report_paths: [
      "target/site/jacoco/jacoco.xml",
      "target/site/jacoco-aggregate/jacoco.xml",
      "build/reports/jacoco/test/jacocoTestReport.xml",
      "build/reports/jacoco/testCodeCoverageReport/testCodeCoverageReport.xml",
      "jacoco.xml",
    ],
  },
];

const sastToolIntroductions: Record<string, string> = {
  semgrep: "Semgrep 用规则匹配代码语义模式，偏安全和项目自定义规则；它的命中适合作为专家复核的强候选线索。",
  pmd: "PMD 聚焦 Java 代码质量和可维护性，能补充复杂度、低效实现和坏味道类问题的确定性提示。",
  checkstyle: "Checkstyle 聚焦 Java 团队规范，适合把命名、导入、格式和注释类规则沉淀为可重复检查。",
  eslint: "ESLint 覆盖前端 JavaScript/TypeScript 质量规则，尤其适合 React、异步处理和依赖声明类问题。",
  bandit: "Bandit 面向 Python 安全风险，适合给危险 API、弱加密、注入和密钥暴露提供预扫描线索。",
  spotbugs: "SpotBugs 通过构建产物分析 Java bug pattern，当前设置页读取已有 XML 报告，不直接启动构建。",
  archunit: "ArchUnit 通过测试报告暴露架构约束失败，适合把分层、依赖方向和领域边界问题交给架构专家确认。",
  jacoco: "JaCoCo 通过覆盖率报告提示测试保护缺口，当前只作为风险线索，不会单独升级为正式问题。",
};

const formatBeijingTime = (value?: string) => {
  const text = String(value || "").trim();
  if (!text) return "暂无";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
};

const renderPreviewMarkdown = (markdown: string): React.ReactNode[] => {
  const lines = String(markdown || "").split(/\r?\n/);
  const nodes: React.ReactNode[] = [];
  let bulletBuffer: string[] = [];
  let paragraphBuffer: string[] = [];

  const flushBullets = () => {
    if (!bulletBuffer.length) return;
    nodes.push(
      <ul key={`ul-${nodes.length}`} className="template-preview-list">
        {bulletBuffer.map((item, index) => (
          <li key={`${item}-${index}`}>{item}</li>
        ))}
      </ul>,
    );
    bulletBuffer = [];
  };

  const flushParagraph = () => {
    if (!paragraphBuffer.length) return;
    nodes.push(
      <Paragraph key={`p-${nodes.length}`} className="template-preview-paragraph">
        {paragraphBuffer.join(" ")}
      </Paragraph>,
    );
    paragraphBuffer = [];
  };

  lines.forEach((line, index) => {
    const text = line.trim();
    if (!text) {
      flushBullets();
      flushParagraph();
      return;
    }
    if (text.startsWith("### ")) {
      flushBullets();
      flushParagraph();
      nodes.push(
        <Title key={`h3-${index}`} level={5} className="template-preview-h3">
          {text.slice(4)}
        </Title>,
      );
      return;
    }
    if (text.startsWith("## ")) {
      flushBullets();
      flushParagraph();
      nodes.push(
        <Title key={`h2-${index}`} level={4} className="template-preview-h2">
          {text.slice(3)}
        </Title>,
      );
      return;
    }
    if (text.startsWith("# ")) {
      flushBullets();
      flushParagraph();
      nodes.push(
        <Title key={`h1-${index}`} level={3} className="template-preview-h1">
          {text.slice(2)}
        </Title>,
      );
      return;
    }
    if (text.startsWith("- ")) {
      flushParagraph();
      bulletBuffer.push(text.slice(2));
      return;
    }
    flushBullets();
    paragraphBuffer.push(text);
  });

  flushBullets();
  flushParagraph();
  return nodes;
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
  const [gitnexusStatus, setGitnexusStatus] = React.useState<GitNexusIndexStatus | null>(null);
  const [gitnexusPreflight, setGitnexusPreflight] = React.useState<GitNexusPreflightStatus | null>(null);
  const [repositoryGitnexusStatuses, setRepositoryGitnexusStatuses] = React.useState<Record<string, GitNexusIndexStatus>>({});
  const [repositoryGitnexusPreflights, setRepositoryGitnexusPreflights] = React.useState<Record<string, GitNexusPreflightStatus>>({});
  const [repositoryGitnexusRunning, setRepositoryGitnexusRunning] = React.useState<Record<string, boolean>>({});
  const [repositoryCodeGraphStatuses, setRepositoryCodeGraphStatuses] = React.useState<Record<string, CodeGraphIndexStatus>>({});
  const [repositoryCodeGraphRunning, setRepositoryCodeGraphRunning] = React.useState<Record<string, boolean>>({});
  const [impactTemplate, setImpactTemplate] = React.useState<ImpactReportTemplate | null>(null);
  const [impactTemplateContent, setImpactTemplateContent] = React.useState("");
  const [impactTemplateSchemaContent, setImpactTemplateSchemaContent] = React.useState("");
  const [savingImpactTemplate, setSavingImpactTemplate] = React.useState(false);
  const [analyzingImpactTemplate, setAnalyzingImpactTemplate] = React.useState(false);
  const [previewingImpactTemplate, setPreviewingImpactTemplate] = React.useState(false);
  const [impactTemplatePreview, setImpactTemplatePreview] = React.useState<ImpactReportTemplatePreview | null>(null);
  const [impactTemplateAnalysis, setImpactTemplateAnalysis] = React.useState<ImpactReportTemplateAnalysis | null>(null);
  const [reviewWorkspaceCleanupRunning, setReviewWorkspaceCleanupRunning] = React.useState(false);
  const [reviewWorkspaceCleanupResult, setReviewWorkspaceCleanupResult] = React.useState<ReviewWorkspaceCleanupResult | null>(null);
  const [runtimeSnapshot, setRuntimeSnapshot] = React.useState<RuntimeSettings | null>(null);
  const [sastToolsStatus, setSastToolsStatus] = React.useState<SastToolsStatus | null>(null);
  const [expandedSettingSections, setExpandedSettingSections] = React.useState<Record<string, boolean>>({});
  const [runtimeActiveKeys, setRuntimeActiveKeys] = React.useState<string[]>(["basic"]);

  const isSettingSectionExpanded = React.useCallback(
    (sectionKey: string) => Boolean(expandedSettingSections[sectionKey]),
    [expandedSettingSections],
  );

  const toggleSettingSection = React.useCallback((sectionKey: string) => {
    setExpandedSettingSections((current) => ({
      ...current,
      [sectionKey]: !current[sectionKey],
    }));
  }, []);

  const refreshRepositoryGitNexusStatuses = React.useCallback(async (repositories?: CodeRepositorySettings[]) => {
    const repoList = (repositories || normalizeCodeRepositories(form.getFieldValue("code_repositories"))).filter((repo) =>
      String(repo?.repository_id || "").trim(),
    );
    if (!repoList.length) {
      setRepositoryGitnexusStatuses({});
      setRepositoryGitnexusPreflights({});
      return;
    }
    const [statusResults, preflightResults] = await Promise.all([
      Promise.all(
        repoList.map(async (repo) => {
          const repositoryId = String(repo.repository_id || "").trim();
          try {
            const status = await settingsApi.getRepositoryGitNexusIndexStatus(repositoryId);
            return [repositoryId, status] as const;
          } catch (error: any) {
            return [
              repositoryId,
              {
                repository_id: repositoryId,
                state: "failed",
                message: error?.message || "读取 GitNexus 图谱状态失败",
              } satisfies GitNexusIndexStatus,
            ] as const;
          }
        }),
      ),
      Promise.all(
        repoList.map(async (repo) => {
          const repositoryId = String(repo.repository_id || "").trim();
          try {
            const preflight = await settingsApi.getRepositoryGitNexusPreflight(repositoryId);
            return [repositoryId, preflight] as const;
          } catch (error: any) {
            return [
              repositoryId,
              {
                repository_id: repositoryId,
                status: "failed",
                checks: [],
                recommended_actions: [error?.message || "读取 GitNexus 诊断结果失败"],
              } satisfies GitNexusPreflightStatus,
            ] as const;
          }
        }),
      ),
    ]);
    setRepositoryGitnexusStatuses(Object.fromEntries(statusResults));
    setRepositoryGitnexusPreflights(Object.fromEntries(preflightResults));
  }, [form]);

  const refreshRepositoryCodeGraphStatuses = React.useCallback(async (repositories?: CodeRepositorySettings[]) => {
    const repoList = (repositories || normalizeCodeRepositories(form.getFieldValue("code_repositories"))).filter((repo) =>
      String(repo?.repository_id || "").trim(),
    );
    if (!repoList.length) {
      setRepositoryCodeGraphStatuses({});
      return;
    }
    const statusResults = await Promise.all(
      repoList.map(async (repo) => {
        const repositoryId = String(repo.repository_id || "").trim();
        try {
          const status = await settingsApi.getRepositoryCodeGraphIndexStatus(repositoryId);
          return [repositoryId, status] as const;
        } catch (error: any) {
          return [
            repositoryId,
            {
              repository_id: repositoryId,
              state: "failed",
              message: error?.message || "读取代码结构图谱状态失败",
            } satisfies CodeGraphIndexStatus,
          ] as const;
        }
      }),
    );
    setRepositoryCodeGraphStatuses(Object.fromEntries(statusResults));
  }, [form]);

  const loadPage = React.useCallback(async () => {
    // 系统设置和专家列表要一起加载，才能在一页内完成全局与专家级配置。
    setLoading(true);
    try {
      const [runtime, expertList, skills, tools, gitnexus, gitnexusDiagnostic, sastStatus, impactTemplatePayload] = await Promise.all([
        settingsApi.getRuntime(),
        expertApi.list(),
        settingsApi.listExtensionSkills(),
        settingsApi.listExtensionTools(),
        settingsApi.getGitNexusIndexStatus().catch(() => null),
        settingsApi.getGitNexusPreflight().catch(() => null),
        settingsApi.getSastToolsStatus().catch(() => null),
        settingsApi.getImpactReportTemplate(),
      ]);
      const projectRepositories = currentProjectRepositories(runtime);
      form.setFieldsValue({
        ...runtime,
        code_repositories: projectRepositories,
        default_repository_id: "",
      });
      setRuntimeSnapshot(runtime);
      void refreshRepositoryGitNexusStatuses(projectRepositories);
      void refreshRepositoryCodeGraphStatuses(projectRepositories);
      setExperts(expertList);
      setExtensionSkills(skills);
      setExtensionTools(tools);
      setGitnexusStatus(
        gitnexus || {
          state: "unknown",
          message: "GitNexus 图谱状态暂时不可用，不影响读取已保存设置。",
        },
      );
      setGitnexusPreflight(gitnexusDiagnostic);
      setSastToolsStatus(sastStatus);
      setImpactTemplate(impactTemplatePayload);
      setImpactTemplateContent(impactTemplatePayload.content || "");
      setImpactTemplateSchemaContent(impactTemplatePayload.schema_content || "");
      setImpactTemplateAnalysis({
        schema_content: impactTemplatePayload.schema_content || "",
        schema_variables: impactTemplatePayload.schema_variables || [],
        placeholders: impactTemplatePayload.placeholders || [],
        undefined_placeholders: impactTemplatePayload.undefined_placeholders || [],
      });
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
  }, [form, refreshRepositoryCodeGraphStatuses, refreshRepositoryGitNexusStatuses, skillForm, toolForm]);

  React.useEffect(() => {
    void loadPage();
  }, [loadPage]);

  const handleRefreshRepositoryGitNexusStatus = React.useCallback(async (repositoryId: string) => {
    const id = String(repositoryId || "").trim();
    if (!id) return;
    const [status, diagnostic] = await Promise.all([
      settingsApi.getRepositoryGitNexusIndexStatus(id),
      settingsApi.getRepositoryGitNexusPreflight(id).catch(() => null),
    ]);
    setRepositoryGitnexusStatuses((prev) => ({ ...prev, [id]: status }));
    if (diagnostic) {
      setRepositoryGitnexusPreflights((prev) => ({ ...prev, [id]: diagnostic }));
    }
  }, []);

  const handleRunRepositoryGitNexusIndex = React.useCallback(async (repositoryId: string) => {
    const id = String(repositoryId || "").trim();
    if (!id) {
      message.warning("请先填写仓库 ID");
      return;
    }
    setRepositoryGitnexusRunning((prev) => ({ ...prev, [id]: true }));
    try {
      const status = await settingsApi.runRepositoryGitNexusIndex(id);
      setRepositoryGitnexusStatuses((prev) => ({ ...prev, [id]: status }));
      if (status.state === "blocked") {
        message.warning(status.message || `GitNexus 当前正在处理其他仓库，${id} 暂未启动`);
      } else {
        message.success(`GitNexus 建图任务已触发：${id}`);
      }
      window.setTimeout(() => {
        void handleRefreshRepositoryGitNexusStatus(id);
      }, 1500);
    } catch (error: any) {
      message.error(error?.message || `触发 ${id} GitNexus 建图失败`);
    } finally {
      setRepositoryGitnexusRunning((prev) => ({ ...prev, [id]: false }));
    }
  }, [handleRefreshRepositoryGitNexusStatus]);

  const handleRefreshRepositoryCodeGraphStatus = React.useCallback(async (repositoryId: string) => {
    const id = String(repositoryId || "").trim();
    if (!id) return;
    const status = await settingsApi.getRepositoryCodeGraphIndexStatus(id);
    setRepositoryCodeGraphStatuses((prev) => ({ ...prev, [id]: status }));
  }, []);

  const handleRunRepositoryCodeGraphIndex = React.useCallback(async (repositoryId: string) => {
    const id = String(repositoryId || "").trim();
    if (!id) {
      message.warning("请先填写仓库 ID");
      return;
    }
    setRepositoryCodeGraphRunning((prev) => ({ ...prev, [id]: true }));
    try {
      const status = await settingsApi.runRepositoryCodeGraphIndex(id);
      setRepositoryCodeGraphStatuses((prev) => ({ ...prev, [id]: status }));
      if (status.state === "blocked") {
        message.warning(humanizeReviewText(status.message || `代码结构图谱当前正在处理其他仓库，${id} 暂未启动`));
      } else {
        message.success(`代码结构图谱建图任务已触发：${id}`);
      }
      window.setTimeout(() => {
        void handleRefreshRepositoryCodeGraphStatus(id);
      }, 1500);
    } catch (error: any) {
      message.error(humanizeReviewText(error?.message || `触发 ${id} 代码结构图谱建图失败`));
    } finally {
      setRepositoryCodeGraphRunning((prev) => ({ ...prev, [id]: false }));
    }
  }, [handleRefreshRepositoryCodeGraphStatus]);

  const handleCleanupReviewWorkspaces = React.useCallback(async () => {
    setReviewWorkspaceCleanupRunning(true);
    try {
      const result = await settingsApi.cleanupReviewWorkspaces(7);
      setReviewWorkspaceCleanupResult(result);
      if (result.failed.length > 0) {
        message.warning(`已清理 ${result.removed_count} 个临时检视工作区，${result.failed.length} 个目录清理失败`);
      } else {
        message.success(`已清理 ${result.removed_count} 个临时检视工作区`);
      }
    } catch (error: any) {
      message.error(error?.message || "清理临时检视工作区失败");
    } finally {
      setReviewWorkspaceCleanupRunning(false);
    }
  }, []);

  const handleSaveImpactTemplate = React.useCallback(async () => {
    setSavingImpactTemplate(true);
    try {
      const payload = await settingsApi.updateImpactReportTemplate(impactTemplateContent, impactTemplateSchemaContent);
      setImpactTemplate(payload);
      setImpactTemplateContent(payload.content || "");
      setImpactTemplateSchemaContent(payload.schema_content || "");
      setImpactTemplateAnalysis({
        schema_content: payload.schema_content || "",
        schema_variables: payload.schema_variables || [],
        placeholders: payload.placeholders || [],
        undefined_placeholders: payload.undefined_placeholders || [],
      });
      message.success("关联影响报告模板已更新");
    } catch (error: any) {
      message.error(error?.message || "保存关联影响报告模板失败");
    } finally {
      setSavingImpactTemplate(false);
    }
  }, [impactTemplateContent, impactTemplateSchemaContent]);

  const handleAnalyzeImpactTemplate = React.useCallback(async () => {
    setAnalyzingImpactTemplate(true);
    try {
      const payload = await settingsApi.analyzeImpactReportTemplate(impactTemplateContent);
      setImpactTemplateAnalysis(payload);
      setImpactTemplateSchemaContent(payload.schema_content || "");
      message.success("模板变量已分析完成");
    } catch (error: any) {
      message.error(error?.message || "分析模板变量失败");
    } finally {
      setAnalyzingImpactTemplate(false);
    }
  }, [impactTemplateContent]);

  const handleResetImpactTemplate = React.useCallback(async () => {
    setSavingImpactTemplate(true);
    try {
      const payload = await settingsApi.resetImpactReportTemplate();
      setImpactTemplate(payload);
      setImpactTemplateContent(payload.content || "");
      setImpactTemplateSchemaContent(payload.schema_content || "");
      setImpactTemplateAnalysis({
        schema_content: payload.schema_content || "",
        schema_variables: payload.schema_variables || [],
        placeholders: payload.placeholders || [],
        undefined_placeholders: payload.undefined_placeholders || [],
      });
      message.success("已恢复默认模板");
    } catch (error: any) {
      message.error(error?.message || "恢复默认模板失败");
    } finally {
      setSavingImpactTemplate(false);
    }
  }, []);

  const handlePreviewImpactTemplate = React.useCallback(async () => {
    setPreviewingImpactTemplate(true);
    try {
      const payload = await settingsApi.previewImpactReportTemplate(impactTemplateContent, impactTemplateSchemaContent);
      setImpactTemplatePreview(payload);
      message.success("已生成模板预览");
    } catch (error: any) {
      message.error(error?.message || "生成模板预览失败");
    } finally {
      setPreviewingImpactTemplate(false);
    }
  }, [impactTemplateContent, impactTemplateSchemaContent]);

  const impactTemplateUploadProps: UploadProps = React.useMemo(
    () => ({
      accept: ".md,text/markdown",
      showUploadList: false,
      beforeUpload: async (file) => {
        try {
          const text = await file.text();
          setImpactTemplateContent(text);
          message.success(`已载入模板：${file.name}`);
        } catch {
          message.error("读取 Markdown 模板失败");
        }
        return false;
      },
    }),
    [],
  );

  const renderConfiguredNotice = (
    configuredField: string,
    alertMessage: string,
    alertDescription: string,
  ) => (
    <Form.Item noStyle shouldUpdate>
      {() =>
        Boolean(form.getFieldValue(configuredField as keyof RuntimeSettings)) ? (
          <Alert
            type="success"
            showIcon
            style={{ marginBottom: 16 }}
            message={alertMessage}
            description={alertDescription}
          />
        ) : null
      }
    </Form.Item>
  );

  function gitnexusCommandCheck(diagnostic?: GitNexusPreflightStatus | null) {
    return (diagnostic?.checks || []).find((check) => check.name === "gitnexus_command") || null;
  }

  function gitnexusInstallDisplay(
    status?: GitNexusIndexStatus | null,
    diagnostic?: GitNexusPreflightStatus | null,
  ) {
    const commandCheck = gitnexusCommandCheck(diagnostic);
    if (status?.gitnexus_installed === true) {
      return { color: "success", label: "已预装", path: status.gitnexus_path || "已检测到 gitnexus 命令" };
    }
    if (commandCheck?.status === "passed") {
      return { color: "success", label: "已预装", path: commandCheck.message || "GitNexus 命令可用" };
    }
    if (status?.gitnexus_installed === false) {
      return { color: "error", label: "未安装", path: status.gitnexus_path || "当前机器未发现 gitnexus 可执行命令" };
    }
    if (commandCheck?.status === "failed") {
      return { color: "error", label: "未安装", path: commandCheck.message || "当前机器未发现 gitnexus 可执行命令" };
    }
    return { color: "default", label: "未确认", path: status?.gitnexus_path || "刷新后显示 gitnexus 命令路径" };
  }

  const runtimeOverview = (() => {
    const analysisMode = analysisModeDisplay(String(runtimeSnapshot?.default_analysis_mode || "standard"));
    const qualityMode = reviewQualityModeDisplay(String(runtimeSnapshot?.review_quality_mode || "standard"));
    const targetBranch = String(runtimeSnapshot?.default_target_branch || "main");
    const repositories = currentProjectRepositories(runtimeSnapshot);
    const enabledRepositoryCount = repositories.filter((repo) => repo?.enabled !== false).length;
    const autoReviewEnabled = Boolean(runtimeSnapshot?.auto_review_enabled);
    const sastEnabled = Boolean(runtimeSnapshot?.enable_sast_prescan);
    const currentProjectId = String(runtimeSnapshot?.default_project_id || "").trim();
    const modelName = String(runtimeSnapshot?.default_llm_model || "-");
    const modelProvider = String(runtimeSnapshot?.default_llm_provider || "-");
    const storageBackend = String(runtimeSnapshot?.storage_backend || "sqlite");
    const installDisplay = gitnexusInstallDisplay(gitnexusStatus, gitnexusPreflight);
    return (
      <div className="settings-summary-grid">
        <div className="settings-summary-card">
          <span className="settings-summary-label">项目代码仓</span>
          <strong>{repositories.length ? `${enabledRepositoryCount}/${repositories.length} 个启用` : "未配置"}</strong>
          <span className="settings-summary-meta" title={repositories.map((repo) => repo.repository_id || repo.clone_url).join(", ") || "当前项目尚未绑定代码仓"}>
            {repositories.map((repo) => repo.repository_id || repo.clone_url).join(", ") || `项目 ${currentProjectId || "-"} 尚未绑定代码仓`}
          </span>
          <Tag color={autoReviewEnabled ? "processing" : "default"}>{autoReviewEnabled ? "自动拉取 MR" : "手动提交 MR"}</Tag>
        </div>
        <div className="settings-summary-card">
          <span className="settings-summary-label">默认模型</span>
          <strong title={modelName}>{modelName}</strong>
          <span className="settings-summary-meta" title={modelProvider}>{modelProvider}</span>
          <Tag color={runtimeSnapshot?.default_llm_api_key_configured ? "green" : "warning"}>
            {runtimeSnapshot?.default_llm_api_key_configured ? "API Key 已配置" : "API Key 未配置"}
          </Tag>
        </div>
        <div className="settings-summary-card">
          <span className="settings-summary-label">默认检视配置</span>
          <strong>{`${analysisMode.title} / ${qualityMode.title}`}</strong>
          <span className="settings-summary-meta">{qualityMode.detail}</span>
          <Tag color={qualityMode.color}>{`目标分支 ${targetBranch}`}</Tag>
        </div>
        <div className="settings-summary-card">
          <span className="settings-summary-label">SAST/linter 预扫描</span>
          <strong>{sastEnabled ? "已启用" : "未启用"}</strong>
          <span className="settings-summary-meta">{sastEnabled ? "检视前尝试调用本机静态工具" : "不会主动调用本机静态工具"}</span>
          <Tag color={sastEnabled ? "cyan" : "default"}>{sastEnabled ? "best-effort" : "关闭"}</Tag>
        </div>
        <div className="settings-summary-card">
          <span className="settings-summary-label">GitNexus</span>
          <strong>{installDisplay.label}</strong>
          <span className="settings-summary-meta" title={installDisplay.path}>{installDisplay.path || "未发现安装路径"}</span>
          <Tag color={installDisplay.color}>影响分析图谱</Tag>
        </div>
        <div className="settings-summary-card">
          <span className="settings-summary-label">存储后端</span>
          <strong>{storageBackend === "postgres" ? "PostgreSQL" : "SQLite"}</strong>
          <span className="settings-summary-meta">{storageBackend === "postgres" ? "适合多人共享和长期留存" : "本地单机配置，开箱即用"}</span>
          <Tag color={storageBackend === "postgres" ? "purple" : "default"}>{storageBackend}</Tag>
        </div>
      </div>
    );
  })();

  const runtimeNavItems = [
    {
      key: "basic",
      title: "项目与代码仓",
      description: "仓库、自动拉取、数据源绑定",
      tag: "最常用",
      color: "blue",
    },
    {
      key: "review-strategy",
      title: "检视策略配置",
      description: "默认模式、质量模式、SAST、人工确认",
      tag: "影响 agent 数",
      color: "green",
    },
    {
      key: "runtime",
      title: "存储与系统开关",
      description: "SQLite / PostgreSQL 与基础运行参数",
      tag: "系统级",
      color: "default",
    },
    {
      key: "credentials",
      title: "凭据与密钥",
      description: "平台 token、模型 API Key",
      tag: "敏感配置",
      color: "red",
    },
    {
      key: "governance",
      title: "问题治理与规则筛选",
      description: "正式问题阈值、规则筛选、模型复核",
      tag: "结果口径",
      color: "gold",
    },
    {
      key: "llm",
      title: "模型与执行策略",
      description: "超时、重试、并发、Prompt Profile",
      tag: "性能",
      color: "purple",
    },
    {
      key: "advanced",
      title: "高级网络与白名单",
      description: "工具白名单、网络校验、证书",
      tag: "高级",
      color: "default",
    },
  ];

  const gitnexusStateColor = (state?: string) => {
    if (state === "ready") return "success";
    if (state === "running") return "processing";
    if (state === "failed") return "error";
    if (state === "skipped") return "warning";
    if (state === "blocked") return "warning";
    return "default";
  };

  const gitnexusDiagnosticColor = (state?: string) => {
    if (state === "ready" || state === "passed") return "success";
    if (state === "warning") return "warning";
    if (state === "failed") return "error";
    return "default";
  };

  const renderCodeGraphDependencyChecks = (status?: CodeGraphIndexStatus | null) => {
    const checks = status?.dependency_checks || [];
    if (!checks.length) {
      return <span>暂无依赖检查结果，点击刷新后查看。</span>;
    }
    return (
      <Space direction="vertical" size={4}>
        {checks.map((check) => (
          <Tag key={check.name} color={gitnexusDiagnosticColor(check.status)} style={{ whiteSpace: "normal", lineHeight: 1.6 }}>
            {humanizeReviewText(check.message)}
          </Tag>
        ))}
      </Space>
    );
  };

  const codeGraphCountSummary = (status?: CodeGraphIndexStatus | null) => {
    if (!status?.graph_db_exists) return "暂无图谱数据";
    const fileCount = Number(status.graph_file_count || 0);
    const nodeCount = Number(status.graph_node_count || 0);
    const edgeCount = Number(status.graph_edge_count || 0);
    return `${fileCount} 个文件，${nodeCount} 个节点，${edgeCount} 条关系`;
  };

  const sastStatusColor = (state?: string) => {
    if (state === "available" || state === "enabled") return "success";
    if (state === "requires_report" || state === "disabled") return "warning";
    if (state === "missing") return "error";
    if (state === "unknown") return "default";
    return "default";
  };

  const copySastToolText = async (text: string, label: string) => {
    const value = String(text || "").trim();
    if (!value) return;
    if (!navigator.clipboard) {
      message.warning("当前浏览器不支持自动复制，请手动复制。");
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      message.success(`${label}已复制`);
    } catch {
      message.warning("复制失败，请手动复制。");
    }
  };

  const refreshSastToolsStatus = async () => {
    try {
      const status = await settingsApi.getSastToolsStatus();
      setSastToolsStatus(status);
      message.success("静态工具状态已刷新");
    } catch (error: any) {
      message.error(error?.message || "刷新静态工具状态失败");
    }
  };

  const renderSastToolEntry = (tool: SastToolStatusItem, kindLabel: string) => {
    const verifyCommands = tool.verify_commands || [];
    const reportPaths = tool.report_paths || [];
    const existingReportPaths = tool.existing_report_paths || [];
    const installEntries = Object.entries(tool.install || {}).filter(([, value]) => String(value || "").trim());
    const copyText = verifyCommands.length ? verifyCommands.join("\n") : reportPaths.join("\n");
    return {
      key: `${tool.kind}-${tool.tool}`,
      label: (
        <Space wrap>
          {tool.kind === "command" ? <ToolOutlined /> : <FileSearchOutlined />}
          <strong>{tool.tool}</strong>
          <Tag color={sastStatusColor(tool.status)}>{tool.status}</Tag>
          <Tag>{kindLabel}</Tag>
          {tool.category ? <Tag>{tool.category}</Tag> : null}
        </Space>
      ),
      children: (
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          <Paragraph style={{ marginBottom: 0 }}>{sastToolIntroductions[tool.tool] || tool.purpose}</Paragraph>
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <div>
              <Text type="secondary">用途</Text>
              <Paragraph style={{ marginBottom: 0 }}>{tool.purpose}</Paragraph>
            </div>
            {tool.executable ? (
              <div>
                <Text type="secondary">命令路径</Text>
                <Paragraph code copyable style={{ marginBottom: 0 }}>
                  {tool.executable}
                </Paragraph>
                {tool.detection_method ? (
                  <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                    {`检测方式：${tool.detection_method}`}
                  </Paragraph>
                ) : null}
              </div>
            ) : null}
            {verifyCommands.length ? (
              <div>
                <Text type="secondary">校验命令</Text>
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  {verifyCommands.map((command) => (
                    <Paragraph key={command} code copyable style={{ marginBottom: 0 }}>
                      {command}
                    </Paragraph>
                  ))}
                </Space>
              </div>
            ) : null}
            {installEntries.length ? (
              <div>
                <Text type="secondary">安装方式</Text>
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  {installEntries.map(([platformName, command]) => (
                    <Paragraph key={platformName} style={{ marginBottom: 0 }}>
                      <Text strong>{platformName}</Text>
                      {`: ${command}`}
                    </Paragraph>
                  ))}
                </Space>
              </div>
            ) : null}
            {reportPaths.length ? (
              <div>
                <Text type="secondary">报告路径</Text>
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  {reportPaths.map((path) => (
                    <Paragraph key={path} code copyable style={{ marginBottom: 0 }}>
                      {path}
                    </Paragraph>
                  ))}
                </Space>
              </div>
            ) : null}
            {existingReportPaths.length ? (
              <div>
                <Text type="secondary">已发现报告</Text>
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  {existingReportPaths.map((path) => (
                    <Paragraph key={path} code copyable style={{ marginBottom: 0 }}>
                      {path}
                    </Paragraph>
                  ))}
                </Space>
              </div>
            ) : null}
          </Space>
          {copyText ? (
            <Button icon={<CopyOutlined />} size="small" onClick={() => void copySastToolText(copyText, verifyCommands.length ? "校验命令" : "报告路径")}>
              复制{verifyCommands.length ? "校验命令" : "报告路径"}
            </Button>
          ) : null}
        </Space>
      ),
    };
  };

  const renderSastToolStatus = () => {
    const status = sastToolsStatus;
    const commandTools = status?.command_tools?.length ? status.command_tools : fallbackSastCommandTools;
    const reportTools = status?.report_tools?.length ? status.report_tools : fallbackSastReportTools;
    return (
      <Space direction="vertical" size={10} style={{ width: "100%" }}>
        <Alert
          type={status?.enabled ? "success" : "warning"}
          showIcon
          message={status ? (status.enabled ? "SAST/linter 预扫描已启用" : "SAST/linter 预扫描未启用") : "SAST/linter 工具状态暂不可用"}
          description={
            <Space direction="vertical" size={6}>
              <span>{status?.summary || "下面仍展示已支持的静态工具入口；刷新设置页后会重新检测本机命令和报告类工具要求。"}</span>
              <span>工具输出只作为专家 Agent 的辅助观察点，不会直接生成正式问题。Windows 下必须让后端进程 PATH 能找到命令。</span>
              {status?.limitations?.length ? <span>{`限制：${status.limitations.join(" / ")}`}</span> : null}
            </Space>
          }
          action={
            <Button icon={<ReloadOutlined />} size="small" onClick={() => void refreshSastToolsStatus()}>
              刷新
            </Button>
          }
        />
        <Descriptions size="small" column={1} bordered>
          <Descriptions.Item label="当前平台">{status?.platform || "unknown"}</Descriptions.Item>
          <Descriptions.Item label="状态说明">{status?.notes?.join(" / ") || "命令类工具需要后端 PATH 可识别；报告类工具需要项目先生成 XML 报告。"}</Descriptions.Item>
        </Descriptions>
        <Collapse
          size="small"
          items={[
            {
              key: "command-tools",
              label: (
                <Space wrap>
                  <ToolOutlined />
                  <strong>命令类静态工具设置</strong>
                  <Tag>{commandTools.length}</Tag>
                </Space>
              ),
              children: <Collapse size="small" items={commandTools.map((tool) => renderSastToolEntry(tool, "命令类"))} />,
            },
            {
              key: "report-tools",
              label: (
                <Space wrap>
                  <FileSearchOutlined />
                  <strong>报告类静态工具设置</strong>
                  <Tag>{reportTools.length}</Tag>
                </Space>
              ),
              children: <Collapse size="small" items={reportTools.map((tool) => renderSastToolEntry(tool, "报告类"))} />,
            },
          ]}
        />
      </Space>
    );
  };

  const renderGitNexusPreflight = (diagnostic?: GitNexusPreflightStatus | null) => {
    if (!diagnostic) {
      return <span>暂无诊断结果，点击刷新后查看。</span>;
    }
    return (
      <Space direction="vertical" size={6} style={{ width: "100%" }}>
        <Space wrap>
          <Tag color={gitnexusDiagnosticColor(diagnostic.status)}>{diagnostic.status}</Tag>
          {(diagnostic.checks || []).map((check) => (
            <Tag key={check.name} color={gitnexusDiagnosticColor(check.status)}>
              {check.name}: {check.status}
            </Tag>
          ))}
        </Space>
        {diagnostic.recommended_actions?.length ? (
          <div className="settings-gitnexus-actions">
            {diagnostic.recommended_actions.slice(0, 3).map((action) => (
              <div key={action}>{action}</div>
            ))}
          </div>
        ) : null}
      </Space>
    );
  };

  return (
    <div className="settings-page">
      <Card className="module-card settings-hero-card">
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          <Title level={3} style={{ margin: 0 }}>
            系统设置
          </Title>
          <Paragraph style={{ marginBottom: 0 }}>
            这里统一管理代码仓、模型、自动审核、问题治理和扩展能力。系统启动必需的配置会写入项目根目录
            {" "}
            <code>config.json</code>
            ，设置页治理项会持久化到当前存储后端，并在运行时与系统配置合并生效。
          </Paragraph>
          {runtimeSnapshot?.config_path ? (
            <div className="settings-config-strip">
              <span>统一配置文件</span>
              <code>{String(runtimeSnapshot.config_path)}</code>
              <Text type="secondary">模型、凭据、仓库和网络校验策略以此为准。</Text>
            </div>
          ) : null}
          {runtimeOverview}
        </Space>
      </Card>

      <SettingsSectionCard
        title="当前实现状态"
        style={{ marginTop: 16 }}
        expanded={isSettingSectionExpanded("implementation")}
        onToggle={() => toggleSettingSection("implementation")}
      >
        <Descriptions column={1}>
          <Descriptions.Item label="日志落盘">前后端日志统一输出到项目根目录 logs/</Descriptions.Item>
          <Descriptions.Item label="知识检索">按检查角色绑定 Markdown 文档，并通过 glob / rg 命中片段</Descriptions.Item>
          <Descriptions.Item label="运行时工具调用">每个检查角色按 runtime_tool_bindings 真实调用本地 review tool gateway</Descriptions.Item>
          <Descriptions.Item label="代码仓上下文">所有检查角色可基于配置好的目标代码仓检索目标分支源码上下文</Descriptions.Item>
          <Descriptions.Item label="问题治理">低风险、提示性、常见建议类问题可只保留为检视发现，不升级为正式问题</Descriptions.Item>
        </Descriptions>
      </SettingsSectionCard>

      <SettingsSectionCard
        title="代码结构图谱"
        style={{ marginTop: 16 }}
        expanded={isSettingSectionExpanded("code-graph")}
        onToggle={() => toggleSettingSection("code-graph")}
        extra={
          <Button onClick={() => void refreshRepositoryCodeGraphStatuses()}>刷新当前项目仓库状态</Button>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="代码结构图谱用于代码检视前的结构化上下文检索"
          description="首次使用或代码仓变更较多时，请先建立图谱。建图会在目标仓库生成 .code-review-graph/graph.db；检视 Java 变更时会优先使用该图谱提取调用关系、影响文件、测试缺口和最小审查上下文，未命中时再使用关键词搜索。"
        />
        {(() => {
            const repositories = currentProjectRepositories(runtimeSnapshot);
            if (!repositories.length) {
              return (
                <Alert type="warning" showIcon message="当前项目还没有绑定代码仓" description="请先在下方“当前项目代码仓设置”里新增代码仓，再建立代码结构图谱。" />
              );
            }
            return (
              <div className="settings-gitnexus-grid">
                {repositories.map((repo, index) => {
                  const repositoryId = String(repo.repository_id || "").trim();
                  const status = repositoryId ? repositoryCodeGraphStatuses[repositoryId] : undefined;
                  return (
                    <Card
                      key={repositoryId || `code-graph-repo-${index}`}
                      size="small"
                      className="settings-gitnexus-repo-card"
                      title={
                        <Space wrap>
                          <span>{repo.name || repositoryId || `代码仓 ${index + 1}`}</span>
                          <Tag>{repo.provider || "generic"}</Tag>
                          {repo.enabled === false ? <Tag color="warning">未启用</Tag> : null}
                        </Space>
                      }
                      extra={
                        <Space>
                          <Button size="small" disabled={!repositoryId} onClick={() => void handleRefreshRepositoryCodeGraphStatus(repositoryId)}>
                            刷新
                          </Button>
                          <Button
                            size="small"
                            type="primary"
                            disabled={!repositoryId || repo.enabled === false}
                            loading={Boolean(repositoryCodeGraphRunning[repositoryId])}
                            onClick={() => void handleRunRepositoryCodeGraphIndex(repositoryId)}
                          >
                            建立/刷新图谱
                          </Button>
                        </Space>
                      }
                    >
                      <Descriptions column={1} size="small">
                        <Descriptions.Item label="仓库 ID">{repositoryId || "未填写"}</Descriptions.Item>
                        <Descriptions.Item label="状态">
                          <Space wrap>
                            <Tag color={gitnexusStateColor(status?.state)}>{status?.state || "idle"}</Tag>
                            <span>{humanizeReviewText(status?.message || "尚未读取该仓库的代码结构图谱状态。")}</span>
                          </Space>
                        </Descriptions.Item>
                        <Descriptions.Item label="依赖检查">{renderCodeGraphDependencyChecks(status)}</Descriptions.Item>
                        <Descriptions.Item label="本地路径">{status?.repo_path || repo.local_path || "未配置"}</Descriptions.Item>
                        <Descriptions.Item label="图谱数据库">
                          {status?.graph_db_path || "建图后会生成在代码仓 .code-review-graph/graph.db"}
                          {typeof status?.graph_db_exists === "boolean" ? (
                            <Tag style={{ marginLeft: 8 }} color={status.graph_db_exists ? "success" : "warning"}>
                              {status.graph_db_exists ? "已生成" : "未生成"}
                            </Tag>
                          ) : null}
                        </Descriptions.Item>
                        <Descriptions.Item label="图谱规模">{codeGraphCountSummary(status)}</Descriptions.Item>
                        <Descriptions.Item label="最近更新时间">
                          {formatBeijingTime(status?.indexed_at || status?.graph_db_updated_at || status?.updated_at)}
                        </Descriptions.Item>
                        <Descriptions.Item label="本次建图">
                          {`索引 ${Number(status?.indexed_file_count || 0)} 个，跳过 ${Number(status?.skipped_unchanged_file_count || 0)} 个，失败 ${Number(status?.failed_file_count || 0)} 个`}
                        </Descriptions.Item>
                      </Descriptions>
                    </Card>
                  );
                })}
              </div>
            );
          })()}
      </SettingsSectionCard>

      <SettingsSectionCard
        title="GitNexus 代码图谱"
        style={{ marginTop: 16 }}
        expanded={isSettingSectionExpanded("gitnexus")}
        onToggle={() => toggleSettingSection("gitnexus")}
        extra={
          <Button onClick={() => void refreshRepositoryGitNexusStatuses()}>刷新当前项目仓库状态</Button>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="GitNexus 图谱用于每个 MR 的关联影响分析"
          description="部署机器需要预先安装 GitNexus。后台定时任务和手工入口只负责调用已安装的 gitnexus analyze 建图；建图完成后，结果页“关联影响报告”会直接展示 GitNexus 的影响分析结果。如果建图或调用失败，页面会明确提示失败原因，不再自动降级。"
        />
        {(() => {
          const installDisplay = gitnexusInstallDisplay(gitnexusStatus, gitnexusPreflight);
          return (
            <Descriptions column={1} size="small" style={{ marginBottom: 12 }}>
              <Descriptions.Item label="本机 GitNexus">
                <Space wrap>
                  <Tag color={installDisplay.color}>{installDisplay.label}</Tag>
                  <span>{installDisplay.path}</span>
                </Space>
              </Descriptions.Item>
            </Descriptions>
          );
        })()}
        {(() => {
            const repositories = currentProjectRepositories(runtimeSnapshot);
            if (!repositories.length) {
              return (
                <Alert type="warning" showIcon message="当前项目还没有绑定代码仓" description="请先在下方“当前项目代码仓设置”里新增代码仓，再建立 GitNexus 图谱。" />
              );
            }
            return (
              <div className="settings-gitnexus-grid">
                {repositories.map((repo, index) => {
                  const repositoryId = String(repo.repository_id || "").trim();
                  const status = repositoryId ? repositoryGitnexusStatuses[repositoryId] : undefined;
                  const diagnostic = repositoryId ? repositoryGitnexusPreflights[repositoryId] : undefined;
                  const installDisplay = gitnexusInstallDisplay(status, diagnostic);
                  return (
                    <Card
                      key={repositoryId || `repo-${index}`}
                      size="small"
                      className="settings-gitnexus-repo-card"
                      title={
                        <Space wrap>
                          <span>{repo.name || repositoryId || `代码仓 ${index + 1}`}</span>
                          <Tag>{repo.provider || "generic"}</Tag>
                          {repo.enabled === false ? <Tag color="warning">未启用</Tag> : null}
                        </Space>
                      }
                      extra={
                        <Space>
                          <Button size="small" disabled={!repositoryId} onClick={() => void handleRefreshRepositoryGitNexusStatus(repositoryId)}>
                            刷新
                          </Button>
                          <Button
                            size="small"
                            type="primary"
                            disabled={!repositoryId || repo.gitnexus_enabled === false}
                            loading={Boolean(repositoryGitnexusRunning[repositoryId])}
                            onClick={() => void handleRunRepositoryGitNexusIndex(repositoryId)}
                          >
                            手动建立图谱
                          </Button>
                        </Space>
                      }
                    >
                      <Descriptions column={1} size="small">
                        <Descriptions.Item label="仓库 ID">{repositoryId || "未填写"}</Descriptions.Item>
                        <Descriptions.Item label="状态">
                          <Space wrap>
                            <Tag color={gitnexusStateColor(status?.state)}>{status?.state || "idle"}</Tag>
                            <span>{status?.message || "尚未读取该仓库的 GitNexus 状态。"}</span>
                          </Space>
                        </Descriptions.Item>
                        <Descriptions.Item label="本地路径">{status?.repo_path || repo.local_path || "未配置"}</Descriptions.Item>
                        <Descriptions.Item label="安装状态">
                          <Space wrap>
                            <Tag color={installDisplay.color}>{installDisplay.label}</Tag>
                            <span>{installDisplay.path}</span>
                          </Space>
                        </Descriptions.Item>
                        <Descriptions.Item label="执行命令">{status?.gitnexus_command || "gitnexus analyze"}</Descriptions.Item>
                        <Descriptions.Item label="环境诊断">{renderGitNexusPreflight(diagnostic)}</Descriptions.Item>
                        <Descriptions.Item label="图谱目录">
                          {status?.graph_dir || "建图后会生成在代码仓 .gitnexus/ 目录"}
                          {typeof status?.graph_dir_exists === "boolean" ? (
                            <Tag style={{ marginLeft: 8 }} color={status.graph_dir_exists ? "success" : "warning"}>
                              {status.graph_dir_exists ? "目录存在" : "目录不存在"}
                            </Tag>
                          ) : null}
                        </Descriptions.Item>
                        <Descriptions.Item label="最近更新时间">{formatBeijingTime(status?.indexed_at || status?.updated_at)}</Descriptions.Item>
                        <Descriptions.Item label="当前 commit">{status?.commit || "暂无"}</Descriptions.Item>
                      </Descriptions>
                    </Card>
                  );
                })}
              </div>
            );
          })()}
      </SettingsSectionCard>

      <SettingsSectionCard
        title="临时检视工作区"
        style={{ marginTop: 16 }}
        expanded={isSettingSectionExpanded("review-workspace")}
        onToggle={() => toggleSettingSection("review-workspace")}
        extra={
          <Button danger loading={reviewWorkspaceCleanupRunning} onClick={() => void handleCleanupReviewWorkspaces()}>
            清理 7 天前目录
          </Button>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="MR 检视会先生成合入后的临时代码快照"
          description="代码结构图谱和 GitNexus 会分析这个快照，避免只看本地目标分支导致问题代码不在 MR 变更范围内。快照保存在系统存储目录下，可定期清理，清理动作不会修改真实代码仓。"
        />
        <Descriptions column={1} size="small">
          <Descriptions.Item label="保留策略">默认复用同一 MR 的最新快照，手工清理只删除 7 天前的临时目录。</Descriptions.Item>
          <Descriptions.Item label="上次清理">
            {reviewWorkspaceCleanupResult ? (
              <Space direction="vertical" size={4}>
                <span>
                  {`已删除 ${reviewWorkspaceCleanupResult.removed_count} 个目录，失败 ${reviewWorkspaceCleanupResult.failed.length} 个`}
                </span>
                <span className="settings-gitnexus-actions">{reviewWorkspaceCleanupResult.root}</span>
              </Space>
            ) : (
              "尚未执行清理"
            )}
          </Descriptions.Item>
          {reviewWorkspaceCleanupResult?.failed.length ? (
            <Descriptions.Item label="失败目录">
              <Space direction="vertical" size={4}>
                {reviewWorkspaceCleanupResult.failed.slice(0, 3).map((item) => (
                  <span key={item.path} className="settings-gitnexus-actions">
                    {`${item.path}: ${item.error}`}
                  </span>
                ))}
              </Space>
            </Descriptions.Item>
          ) : null}
        </Descriptions>
      </SettingsSectionCard>

      <SettingsSectionCard
        title="关联影响分析报告模板"
        style={{ marginTop: 16 }}
        expanded={isSettingSectionExpanded("impact-template")}
        onToggle={() => toggleSettingSection("impact-template")}
        extra={
          <Space wrap>
            <Upload {...impactTemplateUploadProps}>
              <Button>上传 Markdown 模板</Button>
            </Upload>
            <Button onClick={() => void handleAnalyzeImpactTemplate()} loading={analyzingImpactTemplate}>
              分析模板变量
            </Button>
            <Button onClick={() => void handlePreviewImpactTemplate()} loading={previewingImpactTemplate}>
              预览模板
            </Button>
            <Button onClick={() => void handleResetImpactTemplate()} loading={savingImpactTemplate}>
              恢复默认模板
            </Button>
            <Button type="primary" loading={savingImpactTemplate} onClick={() => void handleSaveImpactTemplate()}>
              保存模板
            </Button>
          </Space>
        }
      >
        <Collapse
          ghost
          items={[
            {
              key: "impact-report-template-editor",
              label: "展开模板编辑、Schema 配置与预览",
              children: (
                <>
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message="模型会基于 GitNexus 返回的事实，按这里的 Markdown 模板生成最终关联影响分析报告。"
                    description="建议保留章节结构和占位语义，主要调整标题、表达风格和测试建议的展示方式。保存后会更新当前生效模板；如果改坏了，可以随时恢复到系统默认模板。"
                  />
                  <Descriptions column={1} size="small" style={{ marginBottom: 16 }}>
                    <Descriptions.Item label="模板文件">
                      {impactTemplate?.template_path || "暂无"}
                    </Descriptions.Item>
                    <Descriptions.Item label="默认模板文件">
                      {impactTemplate?.default_template_path || "暂无"}
                    </Descriptions.Item>
                    <Descriptions.Item label="变量 Schema 文件">
                      {impactTemplate?.schema_path || "暂无"}
                    </Descriptions.Item>
                    <Descriptions.Item label="最近更新时间">
                      {formatBeijingTime(impactTemplate?.updated_at)}
                    </Descriptions.Item>
                    <Descriptions.Item label="Schema 最近更新时间">
                      {formatBeijingTime(impactTemplate?.schema_updated_at)}
                    </Descriptions.Item>
                  </Descriptions>
                  <Space direction="vertical" size={8} style={{ width: "100%", marginBottom: 16 }}>
                    <Alert
                      type={impactTemplate?.undefined_placeholders?.length ? "warning" : "success"}
                      showIcon
                      message={
                        (impactTemplateAnalysis?.undefined_placeholders || impactTemplate?.undefined_placeholders || []).length
                          ? `模板里有 ${(impactTemplateAnalysis?.undefined_placeholders || impactTemplate?.undefined_placeholders || []).length} 个占位符还没有在 Schema 中定义`
                          : "模板占位符和 Schema 变量定义已对齐"
                      }
                      description={
                        <>
                          <div>模板占位符：{(impactTemplateAnalysis?.placeholders || impactTemplate?.placeholders || []).join(", ") || "暂无"}</div>
                          <div>未定义占位符：{(impactTemplateAnalysis?.undefined_placeholders || impactTemplate?.undefined_placeholders || []).join(", ") || "无"}</div>
                          <div>未使用变量：{(impactTemplate?.unused_variables || []).join(", ") || "无"}</div>
                        </>
                      }
                    />
                  </Space>
                  <Input.TextArea
                    value={impactTemplateContent}
                    onChange={(event) => setImpactTemplateContent(event.target.value)}
                    autoSize={{ minRows: 18, maxRows: 28 }}
                    placeholder="在这里编辑关联影响分析报告 Markdown 模板，或通过右上角上传 .md 文件覆盖。"
                  />
                  <Card size="small" title="模板变量分析结果" style={{ marginTop: 12, background: "#fafafa" }}>
                    <Paragraph type="secondary" style={{ marginBottom: 12 }}>
                      这里的变量定义由系统根据当前模板自动分析生成，不需要手工填写。保存模板时会使用这份分析结果。
                    </Paragraph>
                    <Descriptions column={1} size="small" style={{ marginBottom: 12 }}>
                      <Descriptions.Item label="当前占位符">
                        {(impactTemplateAnalysis?.placeholders || impactTemplate?.placeholders || []).join(", ") || "暂无"}
                      </Descriptions.Item>
                      <Descriptions.Item label="未识别占位符">
                        {(impactTemplateAnalysis?.undefined_placeholders || impactTemplate?.undefined_placeholders || []).join(", ") || "无"}
                      </Descriptions.Item>
                    </Descriptions>
                    <Input.TextArea
                      value={stringifyJson(impactTemplateAnalysis?.schema_variables || impactTemplate?.schema_variables || [])}
                      readOnly
                      autoSize={{ minRows: 10, maxRows: 18 }}
                      placeholder="点击“分析模板变量”后，这里会展示系统自动生成的变量定义。"
                    />
                  </Card>
                  <Card
                    size="small"
                    title="模板预览"
                    style={{ marginTop: 16, background: "#fafafa" }}
                    extra={impactTemplatePreview?.undefined_placeholders?.length ? <Tag color="warning">存在未定义占位符</Tag> : null}
                  >
                    <Paragraph type="secondary" style={{ marginBottom: 12 }}>
                      这里使用一份内置的 Java MR 示例数据渲染模板，方便检查章节结构、占位符和表达效果。
                    </Paragraph>
                    <Tabs
                      size="small"
                      items={[
                        {
                          key: "rendered",
                          label: "渲染预览",
                          children: (
                            <div className="template-preview-rendered">
                              {impactTemplatePreview?.markdown ? (
                                renderPreviewMarkdown(impactTemplatePreview.markdown)
                              ) : (
                                <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                                  点击“预览模板”后，这里会显示一份按当前模板渲染出的示例关联影响分析报告。
                                </Paragraph>
                              )}
                            </div>
                          ),
                        },
                        {
                          key: "raw",
                          label: "Markdown 原文",
                          children: (
                            <Input.TextArea
                              value={impactTemplatePreview?.markdown || ""}
                              readOnly
                              autoSize={{ minRows: 16, maxRows: 28 }}
                              placeholder="点击“预览模板”后，这里会显示一份示例关联影响分析报告。"
                            />
                          ),
                        },
                      ]}
                    />
                  </Card>
                </>
              ),
            },
          ]}
        />
      </SettingsSectionCard>

      <SettingsSectionCard
        title="运行时设置"
        style={{ marginTop: 16 }}
        loading={loading}
        expanded={isSettingSectionExpanded("runtime")}
        onToggle={() => toggleSettingSection("runtime")}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={async (values) => {
            setSaving(true);
            try {
              const normalizedRepositories = normalizeCodeRepositories(values.code_repositories);
              const runtimeSnapshot = form.getFieldsValue(true) as RuntimeSettings;
              const updatedProjects = buildRuntimeProjectsWithRepositories(runtimeSnapshot, normalizedRepositories);
              const updatedRuntime = await settingsApi.updateRuntime({
                default_project_id: runtimeSnapshot.default_project_id || updatedProjects[0]?.project_id || "",
                projects: updatedProjects,
                default_target_branch: values.default_target_branch,
                default_analysis_mode: values.default_analysis_mode || "standard",
                storage_backend: values.storage_backend || "sqlite",
                storage_pg_url: values.storage_pg_url || "",
                storage_pg_schema: values.storage_pg_schema || "public",
                storage_pg_user: values.storage_pg_user || "",
                storage_pg_password: String(values.storage_pg_password || "").trim() || undefined,
                code_repo_access_token: String(values.code_repo_access_token || "").trim() || undefined,
                github_access_token: String(values.github_access_token || "").trim() || undefined,
                gitlab_access_token: String(values.gitlab_access_token || "").trim() || undefined,
                codehub_access_token: String(values.codehub_access_token || "").trim() || undefined,
                auto_review_enabled: Boolean(values.auto_review_enabled),
                auto_review_poll_interval_seconds: Number(values.auto_review_poll_interval_seconds || 120),
                database_sources: parseJsonArray<PostgresDataSourceSettings>(String(values.database_sources || "")),
                tool_allowlist: parseList(String(values.tool_allowlist || "")),
                mcp_allowlist: parseList(String(values.mcp_allowlist || "")),
                runtime_tool_allowlist: parseList(String(values.runtime_tool_allowlist || "")),
                agent_allowlist: parseList(String(values.agent_allowlist || "")),
                allow_human_gate: Boolean(values.allow_human_gate),
                issue_filter_enabled: Boolean(values.issue_filter_enabled),
                issue_min_priority_level: values.issue_min_priority_level || "P2",
                issue_confidence_threshold_p0: Number(values.issue_confidence_threshold_p0 ?? 0.95),
                issue_confidence_threshold_p1: Number(values.issue_confidence_threshold_p1 ?? 0.85),
                issue_confidence_threshold_p2: Number(values.issue_confidence_threshold_p2 ?? 0.8),
                issue_confidence_threshold_p3: Number(values.issue_confidence_threshold_p3 ?? 0.7),
                suppress_low_risk_hint_issues: Boolean(values.suppress_low_risk_hint_issues),
                hint_issue_confidence_threshold: Number(values.hint_issue_confidence_threshold || 0.85),
                hint_issue_evidence_cap: Number(values.hint_issue_evidence_cap || 2),
                enable_llm_evidence_filter: Boolean(values.enable_llm_evidence_filter),
                llm_evidence_filter_confidence_threshold: Number(values.llm_evidence_filter_confidence_threshold || 0.72),
                llm_evidence_filter_timeout_seconds: Number(values.llm_evidence_filter_timeout_seconds || 35),
                enable_llm_issue_judge: Boolean(values.enable_llm_issue_judge),
                llm_issue_judge_confidence_threshold: Number(values.llm_issue_judge_confidence_threshold || 0.78),
                llm_issue_judge_timeout_seconds: Number(values.llm_issue_judge_timeout_seconds || 45),
                rule_screening_mode: values.rule_screening_mode || "llm",
                rule_screening_batch_size: Number(values.rule_screening_batch_size || 12),
                rule_screening_llm_timeout_seconds: Number(values.rule_screening_llm_timeout_seconds || 90),
                review_quality_mode: values.review_quality_mode || "standard",
                enable_llm_targeted_debate: Boolean(values.enable_llm_targeted_debate),
                llm_targeted_debate_timeout_seconds: Number(values.llm_targeted_debate_timeout_seconds || 60),
                enable_sast_prescan: Boolean(values.enable_sast_prescan),
                enable_review_workspace_realtime_graph: Boolean(values.enable_review_workspace_realtime_graph),
                gitnexus_max_targets: Number(values.gitnexus_max_targets || 12),
                gitnexus_max_context_queries: Number(values.gitnexus_max_context_queries || 8),
                gitnexus_max_impact_queries: Number(values.gitnexus_max_impact_queries || 8),
                gitnexus_max_dynamic_targets: Number(values.gitnexus_max_dynamic_targets ?? 6),
                default_max_debate_rounds: Number(values.default_max_debate_rounds || 2),
                standard_llm_timeout_seconds: Number(values.standard_llm_timeout_seconds || 60),
                standard_llm_retry_count: Number(values.standard_llm_retry_count || 3),
                standard_max_parallel_experts: Number(values.standard_max_parallel_experts || 4),
                light_llm_timeout_seconds: Number(values.light_llm_timeout_seconds || 90),
                light_llm_retry_count: Number(values.light_llm_retry_count || 1),
                light_max_parallel_experts: Number(values.light_max_parallel_experts || 1),
                light_max_debate_rounds: Number(values.light_max_debate_rounds || 1),
                light_llm_max_prompt_chars: Number(values.light_llm_max_prompt_chars || 95000),
                light_llm_max_input_tokens: Number(values.light_llm_max_input_tokens || 110000),
                llm_log_truncate_enabled: Boolean(values.llm_log_truncate_enabled),
                llm_log_preview_limit: Number(values.llm_log_preview_limit || 1600),
                review_prompt_profile: values.review_prompt_profile || "rule-guided-standard",
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
              const projectRepositories = currentProjectRepositories(updatedRuntime);
              setRuntimeSnapshot(updatedRuntime);
              form.setFieldsValue({
                ...updatedRuntime,
                code_repositories: projectRepositories,
                default_repository_id: "",
              });
              void refreshRepositoryGitNexusStatuses(projectRepositories);
              void refreshRepositoryCodeGraphStatuses(projectRepositories);
              void settingsApi.getSastToolsStatus().then(setSastToolsStatus).catch(() => null);
              form.setFieldValue("default_llm_api_key", "");
              form.setFieldValue("storage_pg_password", "");
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
          <div className="settings-runtime-nav">
            {runtimeNavItems.map((item) => {
              const active = runtimeActiveKeys.includes(item.key);
              return (
                <button
                  key={item.key}
                  type="button"
                  className={`settings-runtime-nav-item${active ? " is-active" : ""}`}
                  onClick={() => setRuntimeActiveKeys([item.key])}
                >
                  <span className="settings-runtime-nav-main">
                    <strong>{item.title}</strong>
                    <Tag color={item.color}>{item.tag}</Tag>
                  </span>
                  <span>{item.description}</span>
                </button>
              );
            })}
          </div>
          <Collapse
            className="settings-collapse"
            expandIconPosition={collapseExpandIconPosition}
            activeKey={runtimeActiveKeys}
            onChange={(keys) => {
              const nextKeys = Array.isArray(keys) ? keys.map((item) => String(item)) : [String(keys)];
              setRuntimeActiveKeys(nextKeys.filter(Boolean));
            }}
            items={[
              {
                key: "basic",
                label: "项目与代码仓",
                extra: <Tag color="processing">最常用</Tag>,
                children: (
                  <div className="settings-collapse-content">
                    <Paragraph className="settings-section-tip">
                      维护当前项目绑定的代码仓、自动拉取和数据库上下文。不同项目的仓库配置相互隔离。
                    </Paragraph>
                    <Row gutter={[16, 0]}>
                      <Col xs={24} xl={6}>
                        <Form.Item name="auto_review_enabled" label="启用自动审核队列" valuePropName="checked">
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24}>
                        <Alert
                          type="info"
                          showIcon
                          style={{ marginBottom: 16 }}
                          message="代码仓信息跟随当前项目保存"
                          description="新增、删除或调整代码仓时，只会修改当前项目的 repositories；MR 队列、源码上下文、GitNexus 和代码结构图谱都会按项目内仓库取数。"
                        />
                      </Col>
                      <Col xs={24} xl={12}>
                        <Form.Item name="auto_review_poll_interval_seconds" label="自动拉取轮询间隔（秒）">
                          <InputNumber min={15} max={3600} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24}>
                        <div className="settings-form-block">
                          <div className="settings-form-block-header">
                            <div>
                              <strong>多代码仓配置</strong>
                              <Paragraph className="settings-section-tip" style={{ marginBottom: 0 }}>
                                可以添加一个或多个代码仓；自动审核会逐个扫描启用自动审核的仓库。
                              </Paragraph>
                            </div>
                          </div>
                          <Form.List name="code_repositories">
                            {(fields, { add, remove }) => (
                              <Space direction="vertical" size={12} style={{ width: "100%" }}>
                                {fields.map((field, index) => (
                                  <Card
                                    key={field.key}
                                    size="small"
                                    className="settings-repo-edit-card"
                                    title={`代码仓 ${index + 1}`}
                                    extra={
                                      <Button danger size="small" onClick={() => remove(field.name)}>
                                        删除
                                      </Button>
                                    }
                                  >
                                    <Row gutter={[12, 0]}>
                                      <Col xs={24} xl={8}>
                                        <Form.Item name={[field.name, "repository_id"]} label="仓库 ID" rules={[{ required: true, message: "请填写仓库 ID" }]}>
                                          <Input placeholder="ipc-fnd-service" />
                                        </Form.Item>
                                      </Col>
                                      <Col xs={24} xl={8}>
                                        <Form.Item name={[field.name, "name"]} label="展示名称">
                                          <Input placeholder="IPC FND Service" />
                                        </Form.Item>
                                      </Col>
                                      <Col xs={24} xl={8}>
                                        <Form.Item name={[field.name, "provider"]} label="平台类型">
                                          <Select
                                            options={[
                                              { label: "CodeHub", value: "codehub" },
                                              { label: "GitHub", value: "github" },
                                              { label: "GitLab", value: "gitlab" },
                                              { label: "通用 Git", value: "generic" },
                                            ]}
                                          />
                                        </Form.Item>
                                      </Col>
                                      <Col xs={24}>
                                        <Form.Item name={[field.name, "clone_url"]} label="Git 地址" rules={[{ required: true, message: "请填写 Git 地址" }]}>
                                          <Input placeholder="https://codehub.example.com/ipc/ipc-fnd-service.git" />
                                        </Form.Item>
                                      </Col>
                                      <Col xs={24}>
                                        <Form.Item name={[field.name, "web_url_prefixes"]} label="MR/网页 URL 前缀">
                                          <Select mode="tags" tokenSeparators={[",", "\n"]} placeholder="https://codehub.example.com/ipc/ipc-fnd-service" />
                                        </Form.Item>
                                      </Col>
                                      <Col xs={24} xl={12}>
                                        <Form.Item name={[field.name, "local_path"]} label="本地代码仓目录" rules={[{ required: true, message: "请填写本地代码仓目录" }]}>
                                          <Input placeholder="D:/workspace/ipc-fnd-service" />
                                        </Form.Item>
                                      </Col>
                                      <Col xs={24} xl={6}>
                                        <Form.Item name={[field.name, "default_branch"]} label="默认目标分支">
                                          <Input placeholder="master" />
                                        </Form.Item>
                                      </Col>
                                      <Col xs={24} xl={6}>
                                        <Form.Item name={[field.name, "auto_review_poll_interval_seconds"]} label="轮询间隔（秒）">
                                          <InputNumber min={15} max={3600} style={{ width: "100%" }} />
                                        </Form.Item>
                                      </Col>
                                      <Col xs={24}>
                                        <Form.Item name={[field.name, "database_source_ids"]} label="绑定数据源 ID">
                                          <Select mode="tags" tokenSeparators={[",", "\n"]} placeholder="留空则按 repo_url 自动匹配；也可以填写 pg-main 等数据源 ID" />
                                        </Form.Item>
                                      </Col>
                                      <Col xs={24}>
                                        <Space wrap size={[24, 8]}>
                                          <Form.Item name={[field.name, "enabled"]} label="启用仓库" valuePropName="checked">
                                            <Switch />
                                          </Form.Item>
                                          <Form.Item name={[field.name, "auto_review_enabled"]} label="自动拉取 MR" valuePropName="checked">
                                            <Switch />
                                          </Form.Item>
                                          <Form.Item name={[field.name, "auto_sync"]} label="自动同步本地仓" valuePropName="checked">
                                            <Switch />
                                          </Form.Item>
                                          <Form.Item name={[field.name, "gitnexus_enabled"]} label="启用 GitNexus 图谱" valuePropName="checked">
                                            <Switch />
                                          </Form.Item>
                                        </Space>
                                      </Col>
                                    </Row>
                                  </Card>
                                ))}
                                <Button
                                  type="dashed"
                                  onClick={() =>
                                    add({
                                      repository_id: "",
                                      name: "",
                                      provider: "codehub",
                                      clone_url: "",
                                      web_url_prefixes: [],
                                      local_path: "",
                                      default_branch: form.getFieldValue("default_target_branch") || "master",
                                      enabled: true,
                                      auto_review_enabled: true,
                                      auto_review_poll_interval_seconds: Number(form.getFieldValue("auto_review_poll_interval_seconds") || 120),
                                      auto_sync: false,
                                      gitnexus_enabled: true,
                                      database_source_ids: [],
                                    })
                                  }
                                  block
                                >
                                  新增代码仓
                                </Button>
                              </Space>
                            )}
                          </Form.List>
                        </div>
                      </Col>
                      <Col xs={24}>
                        <Form.Item
                          name="database_sources"
                          label="按代码仓绑定 PostgreSQL 数据源"
                          getValueProps={(value) => ({ value: stringifyJson(value as PostgresDataSourceSettings[]) })}
                          extra="数据库分析专家会按代码仓 URL 匹配数据源，并只读拉取命中表的结构、约束、索引与轻量统计信息。建议使用 JSON 数组格式配置。"
                        >
                          <Input.TextArea
                            rows={10}
                            placeholder={`[\n  {\n    "repo_url": "https://github.com/org/repo.git",\n    "provider": "postgres",\n    "enabled": true,\n    "host": "127.0.0.1",\n    "port": 5432,\n    "database": "review_db",\n    "user": "review_user",\n    "password_env": "PG_REVIEW_PASSWORD",\n    "schema_allowlist": ["public"],\n    "ssl_mode": "prefer",\n    "connect_timeout_seconds": 5,\n    "statement_timeout_ms": 3000\n  }\n]`}
                          />
                        </Form.Item>
                      </Col>
                    </Row>
                  </div>
                ),
              },
              {
                key: "review-strategy",
                label: "检视策略配置",
                extra: <Tag color="green">影响 agent 数</Tag>,
                children: (
                  <div className="settings-collapse-content">
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 16 }}
                      message="这里控制一次检视的默认执行策略"
                      description="这些配置会影响默认目标分支、检视模式、质量模式、静态工具预扫描和人工确认。普通模式不会固定补入核心角色；深度检视会提高召回但会调用更多检查角色。"
                    />
                    <Row gutter={[16, 0]}>
                      <Col xs={24} xl={8}>
                        <Form.Item name="default_target_branch" label="默认目标分支">
                          <Input placeholder="main" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="default_analysis_mode"
                          label="默认审核模式"
                          extra="轻量模式会减少并发和复核轮次，适合几十行以内的小 MR。"
                        >
                          <Select
                            options={[
                              { label: "轻量模式：少量角色串行，适合小 MR", value: "light" },
                              { label: "标准模式：上下文更完整，适合常规 MR", value: "standard" },
                            ]}
                          />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="review_quality_mode"
                          label="检视质量模式"
                          extra="普通模式只执行主 Agent 选中的检查角色；深度检视会固定补入核心质量角色。"
                        >
                          <Select
                            options={[
                              { label: "普通模式：只运行主 Agent 选中的角色", value: "standard" },
                              { label: "深度检视：补入核心质量角色，提高召回", value: "thorough_review" },
                            ]}
                          />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="enable_sast_prescan"
                          label="启用 SAST/linter 预扫描"
                          valuePropName="checked"
                          extra="开启后，检视前会 best-effort 调用本机 semgrep、PMD、Checkstyle、eslint、bandit 等静态工具。"
                        >
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="enable_review_workspace_realtime_graph"
                          label="启用 MR 快照实时图谱"
                          valuePropName="checked"
                          extra="默认关闭。只有需要基于本次 MR 快照临时建图时再开启。"
                        >
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="allow_human_gate" label="允许人工确认" valuePropName="checked">
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24}>{renderSastToolStatus()}</Col>
                    </Row>
                  </div>
                ),
              },
              {
                key: "runtime",
                label: "存储与系统开关",
                extra: <Tag>按需配置</Tag>,
                children: (
                  <div className="settings-collapse-content">
                    <Paragraph className="settings-section-tip">
                      配置运行时存储后端和 PostgreSQL 连接。日常调整检视行为时，优先使用“检视策略配置”。
                    </Paragraph>
                    <Row gutter={[16, 0]}>
                      <Col xs={24} xl={12}>
                        <Form.Item name="storage_backend" label="底层存储后端">
                          <Select
                            options={[
                              { label: "SQLite（默认）", value: "sqlite" },
                              { label: "PostgreSQL", value: "postgres" },
                            ]}
                          />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={12}>
                        <Form.Item name="storage_pg_url" label="PG 连接 URL">
                          <Input placeholder="postgresql://127.0.0.1:5432/review_db" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={6}>
                        <Form.Item name="storage_pg_schema" label="PG Schema">
                          <Input placeholder="public" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={6}>
                        <Form.Item name="storage_pg_user" label="PG 用户">
                          <Input placeholder="review_user" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={12}>
                        <Form.Item name="storage_pg_password" label="PG 密码">
                          <Input.Password placeholder="留空则保持当前已配置的 PG 密码" />
                        </Form.Item>
                        {renderConfiguredNotice(
                          "storage_pg_password_configured",
                          "PG 密码已配置",
                          "未填写新密码时，系统会继续使用当前已保存的 PG 密码。",
                        )}
                      </Col>
                    </Row>
                  </div>
                ),
              },
              {
                key: "credentials",
                label: "凭据与密钥",
                extra: <Tag>按需配置</Tag>,
                children: (
                  <div className="settings-collapse-content">
                    <Row gutter={[16, 0]}>
                      <Col xs={24}>
                        <Form.Item name="code_repo_access_token" label="代码仓访问凭据">
                          <Input.Password placeholder="留空则保持当前已配置的代码仓访问凭据" />
                        </Form.Item>
                        {renderConfiguredNotice(
                          "code_repo_access_token_configured",
                          "当前已在配置文件中保存代码仓访问凭据",
                          "已保存的访问凭据不会在页面回显；留空保存会保留现有配置。",
                        )}
                      </Col>
                      <Col xs={24} xl={12}>
                        <Form.Item name="github_access_token" label="GitHub 访问凭据">
                          <Input.Password placeholder="优先用于 github.com 链接" />
                        </Form.Item>
                        {renderConfiguredNotice(
                          "github_access_token_configured",
                          "当前已在配置文件中保存 GitHub 访问凭据",
                          "已保存的访问凭据不会在页面回显；留空保存会保留现有配置。",
                        )}
                      </Col>
                      <Col xs={24} xl={12}>
                        <Form.Item name="gitlab_access_token" label="GitLab 访问凭据">
                          <Input.Password placeholder="优先用于 gitlab 链接" />
                        </Form.Item>
                        {renderConfiguredNotice(
                          "gitlab_access_token_configured",
                          "当前已在配置文件中保存 GitLab 访问凭据",
                          "已保存的访问凭据不会在页面回显；留空保存会保留现有配置。",
                        )}
                      </Col>
                      <Col xs={24} xl={12}>
                        <Form.Item name="codehub_access_token" label="CodeHub 访问凭据">
                          <Input.Password placeholder="优先用于 codehub 链接" />
                        </Form.Item>
                        {renderConfiguredNotice(
                          "codehub_access_token_configured",
                          "当前已在配置文件中保存 CodeHub 访问凭据",
                          "已保存的访问凭据不会在页面回显；留空保存会保留现有配置。",
                        )}
                      </Col>
                      <Col xs={24} xl={12}>
                        <Form.Item name="default_llm_api_key" label="默认 API Key">
                          <Input.Password placeholder="留空则保持当前已配置的 API Key" />
                        </Form.Item>
                        {renderConfiguredNotice(
                          "default_llm_api_key_configured",
                          "当前已在配置文件中保存默认 API Key",
                          "出于安全考虑，已保存的 API Key 不会在页面回显；留空保存会保留现有配置。",
                        )}
                      </Col>
                    </Row>
                  </div>
                ),
              },
              {
                key: "governance",
                label: "问题治理与规则筛选",
                extra: <Tag color="gold">建议优先配置</Tag>,
                children: (
                  <div className="settings-collapse-content">
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 16 }}
                      message="这里只控制发现如何升级为正式问题"
                      description="这组开关不会丢掉原始检视发现，只决定哪些发现进入正式问题列表。检视用多少角色、是否启用静态工具，请到上方“检视策略”调整。"
                    />
                    <Row gutter={[16, 0]}>
                      <Col xs={24} xl={8}>
                        <Form.Item name="issue_filter_enabled" label="启用问题升级治理" valuePropName="checked">
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="issue_min_priority_level"
                          label="正式问题最低 P 级"
                          extra="只有达到该优先级及以上的问题才进入正式问题流程。"
                        >
                          <Select
                            options={[
                              { label: "P0（仅 blocker）", value: "P0" },
                              { label: "P1（high / critical 及以上）", value: "P1" },
                              { label: "P2（medium 及以上）", value: "P2" },
                              { label: "P3（low 及以上）", value: "P3" },
                            ]}
                          />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="default_max_debate_rounds" label="默认复核轮次">
                          <InputNumber min={1} max={6} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={6}>
                        <Form.Item
                          name="issue_confidence_threshold_p0"
                          label="P0 正式问题置信度"
                          extra="blocker 级问题至少达到该置信度才升级为正式问题。"
                        >
                          <InputNumber min={0.1} max={1} step={0.01} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={6}>
                        <Form.Item
                          name="issue_confidence_threshold_p1"
                          label="P1 正式问题置信度"
                          extra="high / critical 级问题至少达到该置信度才升级为正式问题。"
                        >
                          <InputNumber min={0.1} max={1} step={0.01} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={6}>
                        <Form.Item
                          name="issue_confidence_threshold_p2"
                          label="P2 正式问题置信度"
                          extra="medium 级问题至少达到该置信度才升级为正式问题。"
                        >
                          <InputNumber min={0.1} max={1} step={0.01} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={6}>
                        <Form.Item
                          name="issue_confidence_threshold_p3"
                          label="P3 正式问题置信度"
                          extra="low 级问题至少达到该置信度才升级为正式问题。"
                        >
                          <InputNumber min={0.1} max={1} step={0.01} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="suppress_low_risk_hint_issues"
                          label="低风险提示暂不提交"
                          valuePropName="checked"
                        >
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="hint_issue_confidence_threshold" label="提示类问题置信度">
                          <InputNumber min={0.1} max={1} step={0.01} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="hint_issue_evidence_cap" label="提示类问题最大证据条数">
                          <InputNumber min={0} max={10} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="enable_llm_evidence_filter"
                          label="启用证据质量复核"
                          valuePropName="checked"
                          extra="开启后，弱证据问题会在进入最终确认前先做一次模型复核；失败自动回退本地规则。"
                        >
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="llm_evidence_filter_confidence_threshold" label="证据复核触发阈值">
                          <InputNumber min={0} max={1} step={0.01} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="llm_evidence_filter_timeout_seconds" label="证据复核模型超时（秒）">
                          <InputNumber min={10} max={180} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="enable_llm_issue_judge"
                          label="启用模型问题复核"
                          valuePropName="checked"
                          extra="开启后，低置信或薄证据问题会在收敛阶段再次判定；失败自动回退本地规则。"
                        >
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="llm_issue_judge_confidence_threshold" label="问题复核触发阈值">
                          <InputNumber min={0} max={1} step={0.01} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="llm_issue_judge_timeout_seconds" label="问题复核模型超时（秒）">
                          <InputNumber min={10} max={180} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="rule_screening_mode"
                          label="规则筛选模式"
                          extra="模型模式会先做语义筛选，失败时自动回退到启发式。"
                        >
                          <Select
                            options={[
                              { label: "模型语义筛选", value: "llm" },
                              { label: "启发式筛选", value: "heuristic" },
                            ]}
                          />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="rule_screening_batch_size" label="规则筛选批大小">
                          <InputNumber min={4} max={24} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="rule_screening_llm_timeout_seconds" label="规则筛选模型超时（秒）">
                          <InputNumber min={15} max={300} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="enable_llm_targeted_debate"
                          label="启用模型定向复核"
                          valuePropName="checked"
                          extra="开启后，多个检查角色存在分歧或低置信时，会先让模型复核观点再进入收敛；失败会自动回退本地规则。"
                        >
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="llm_targeted_debate_timeout_seconds" label="模型定向复核超时（秒）">
                          <InputNumber min={15} max={300} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="gitnexus_max_targets" label="GitNexus 目标上限">
                          <InputNumber min={1} max={50} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="gitnexus_max_context_queries" label="GitNexus context 查询上限">
                          <InputNumber min={1} max={50} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="gitnexus_max_impact_queries" label="GitNexus impact 查询上限">
                          <InputNumber min={1} max={50} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="gitnexus_max_dynamic_targets"
                          label="GitNexus 动态目标上限"
                          extra="Windows 或低配环境可设为 0，减少二次扩展查询。"
                        >
                          <InputNumber min={0} max={50} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                    </Row>
                  </div>
                ),
              },
              {
                key: "llm",
                label: "模型与执行策略",
                children: (
                  <div className="settings-collapse-content">
                    <Row gutter={[16, 0]}>
                      <Col xs={24} xl={8}>
                        <Form.Item name="standard_llm_timeout_seconds" label="标准模式模型超时（秒）">
                          <InputNumber min={10} max={300} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="standard_llm_retry_count" label="标准模式模型重试次数">
                          <InputNumber min={1} max={5} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="standard_max_parallel_experts" label="标准模式最大并发角色数">
                          <InputNumber min={1} max={8} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="light_llm_timeout_seconds" label="轻量模式模型超时（秒）">
                          <InputNumber min={10} max={600} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="light_llm_retry_count" label="轻量模式模型重试次数">
                          <InputNumber min={1} max={5} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="light_max_parallel_experts" label="轻量模式最大并发角色数">
                          <InputNumber min={1} max={4} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="light_max_debate_rounds" label="轻量模式最大复核轮次">
                          <InputNumber min={1} max={3} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="light_llm_max_input_tokens"
                          label="轻量模式上下文用量上限"
                          extra="智能压缩会以这个上限为准，超过时优先保留规则、变更代码和关键上下文。"
                        >
                          <InputNumber min={16000} step={1000} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="light_llm_max_prompt_chars"
                          label="轻量模式提示字符上限"
                          extra="作为字符级安全上限，防止混合中英文场景下提示过长。"
                        >
                          <InputNumber min={12000} max={200000} step={1000} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="llm_log_truncate_enabled" label="截断模型日志预览" valuePropName="checked">
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="llm_log_preview_limit"
                          label="模型日志预览长度"
                          extra="仅影响日志预览，不影响实际发送给模型的内容。"
                        >
                          <InputNumber min={200} max={20000} step={200} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item
                          name="review_prompt_profile"
                          label="检视 Prompt Profile"
                          extra="默认使用规则驱动标准；紧凑模式适合 minimax 等更偏短上下文的模型。旧版兼容仅用于问题定位。"
                        >
                          <Select
                            options={[
                              { label: "规则驱动标准", value: "rule-guided-standard" },
                              { label: "严格 JSON 小上下文", value: "strict-json-small-context" },
                              { label: "规则驱动紧凑", value: "rule-guided-compact" },
                              { label: "长上下文模型", value: "long-context-capable" },
                              { label: "自动选择", value: "auto" },
                              { label: "旧版兼容", value: "legacy" },
                            ]}
                          />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="default_llm_provider" label="默认模型服务">
                          <Input placeholder="dashscope-openai-compatible" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="default_llm_model" label="默认模型">
                          <Input placeholder="kimi-k2.5" />
                        </Form.Item>
                      </Col>
                      <Col xs={24}>
                        <Form.Item name="default_llm_base_url" label="默认模型服务地址">
                          <Input placeholder="https://coding.dashscope.aliyuncs.com/v1" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={8}>
                        <Form.Item name="allow_llm_fallback" label="允许模型自动降级" valuePropName="checked">
                          <Switch />
                        </Form.Item>
                      </Col>
                    </Row>
                  </div>
                ),
              },
              {
                key: "advanced",
                label: "高级网络与白名单",
                children: (
                  <div className="settings-collapse-content">
                    <Row gutter={[16, 0]}>
                      <Col xs={24}>
                        <Form.Item
                          name="tool_allowlist"
                          label="全局工具白名单"
                          getValueProps={(value) => ({ value: stringifyList(value as string[]) })}
                        >
                          <Input placeholder="local_diff, schema_diff, coverage_diff" />
                        </Form.Item>
                      </Col>
                      <Col xs={24}>
                        <Form.Item
                          name="runtime_tool_allowlist"
                          label="全局运行时工具白名单"
                          getValueProps={(value) => ({ value: stringifyList(value as string[]) })}
                        >
                          <Input placeholder="knowledge_search, diff_inspector, test_surface_locator, dependency_surface_locator, repo_context_search" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={12}>
                        <Form.Item
                          name="mcp_allowlist"
                          label="MCP 白名单"
                          getValueProps={(value) => ({ value: stringifyList(value as string[]) })}
                        >
                          <Input placeholder="github.diff, playwright.snapshot" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={12}>
                        <Form.Item
                          name="agent_allowlist"
                          label="检查角色白名单"
                          getValueProps={(value) => ({ value: stringifyList(value as string[]) })}
                        >
                          <Input placeholder="judge, main_agent" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={4}>
                        <Form.Item name="verify_ssl" label="启用 HTTPS 证书校验" valuePropName="checked">
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={4}>
                        <Form.Item name="use_system_trust_store" label="优先使用系统证书库" valuePropName="checked">
                          <Switch />
                        </Form.Item>
                      </Col>
                      <Col xs={24} xl={16}>
                        <Form.Item name="ca_bundle_path" label="自定义 CA Bundle 路径">
                          <Input placeholder="C:\\certs\\corp-ca.pem" />
                        </Form.Item>
                      </Col>
                    </Row>
                  </div>
                ),
              },
            ]}
          />
          <div className="settings-actions">
            <Button type="primary" htmlType="submit" loading={saving}>
              保存运行时设置
            </Button>
          </div>
        </Form>
      </SettingsSectionCard>

      <SettingsSectionCard
        title="检查角色、工具与知识源配置"
        style={{ marginTop: 16 }}
        loading={loading}
        expanded={isSettingSectionExpanded("expert-bindings")}
        onToggle={() => toggleSettingSection("expert-bindings")}
      >
        <Paragraph className="settings-section-tip">
          每个专家的知识源、工具绑定和运行时工具绑定收拢到单独折叠项里，避免整页展开时信息过载。
        </Paragraph>
        <Collapse
          className="settings-collapse settings-expert-collapse"
          expandIconPosition={collapseExpandIconPosition}
          items={experts.map((expert) => ({
            key: expert.expert_id,
            label: (
              <div className="settings-expert-header">
                <div className="settings-expert-title">
                  <strong>{expert.name_zh}</strong>
                  <span title={expert.expert_id}>{`角色标识：${humanizeExpertId(expert.expert_id)}`}</span>
                </div>
                <Space wrap size={[8, 8]}>
                  <Tag>{`知识 ${expert.knowledge_sources.length}`}</Tag>
                  <Tag color="blue">{`工具 ${expert.tool_bindings.length}`}</Tag>
                  <Tag color="geekblue">{`运行时工具 ${expert.runtime_tool_bindings.length}`}</Tag>
                </Space>
              </div>
            ),
            children: (
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
                    message.error(error?.message || "更新检查角色配置失败");
                  } finally {
                    setSavingExpertId("");
                  }
                }}
              >
                <Row gutter={[16, 0]}>
                  <Col xs={24}>
                    <Form.Item name="knowledge_sources" label="知识源绑定">
                      <Input placeholder="security-review-checklist, auth-guideline" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} xl={12}>
                    <Form.Item name="tool_bindings" label="工具绑定">
                      <Input placeholder="local_diff, schema_diff" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} xl={12}>
                    <Form.Item name="runtime_tool_bindings" label="运行时工具绑定">
                      <Input placeholder="knowledge_search, diff_inspector" />
                    </Form.Item>
                  </Col>
                </Row>
                <Button type="primary" htmlType="submit" loading={savingExpertId === expert.expert_id}>
                  保存该角色配置
                </Button>
              </Form>
            ),
          }))}
        />
      </SettingsSectionCard>

      <SettingsSectionCard
        title="扩展能力与扩展工具编辑"
        style={{ marginTop: 16 }}
        loading={loading}
        expanded={isSettingSectionExpanded("extensions")}
        onToggle={() => toggleSettingSection("extensions")}
      >
        <Paragraph className="settings-section-tip">
          扩展编辑保留页签结构，但只聚焦扩展能力和扩展工具本身，和上面的运行时设置、角色绑定分层展示。
        </Paragraph>
        <Tabs
          defaultActiveKey="skills"
          items={[
            {
              key: "skills",
              label: "扩展能力编辑",
              forceRender: true,
              children: (
                <Form
                  form={skillForm}
                  layout="vertical"
                  onFinish={async (values) => {
                    const skillId = String(values.skill_id || "").trim();
                    if (!skillId) {
                      message.warning("请先填写能力标识");
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
                      message.success(`扩展能力 ${skillId} 已保存`);
                      await loadPage();
                    } catch (error: any) {
                      message.error(error?.message || "保存扩展能力失败");
                    } finally {
                      setSavingSkill(false);
                    }
                  }}
                >
                  <Form.Item label="加载已有扩展能力">
                    <Select
                      allowClear
                      placeholder="选择一个已有扩展能力加载到编辑器"
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
                  <Form.Item name="skill_id" label="能力标识" rules={[{ required: true, message: "请输入能力标识" }]}>
                    <Input placeholder="design-consistency-check" />
                  </Form.Item>
                  <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
                    <Input placeholder="详细设计一致性检查" />
                  </Form.Item>
                  <Form.Item name="description" label="说明">
                    <Input placeholder="该扩展能力在检查流程中的职责说明" />
                  </Form.Item>
                  <Form.Item name="bound_experts_text" label="绑定检查角色（逗号分隔角色标识）">
                    <Input placeholder="correctness_business, architecture_design" />
                  </Form.Item>
                  <Form.Item name="required_tools_text" label="依赖工具（逗号分隔工具标识）">
                    <Input placeholder="design_spec_alignment, repo_context_search" />
                  </Form.Item>
                  <Form.Item name="activation_hints_text" label="激活提示词（逗号分隔）">
                    <Input placeholder="design, api, schema" />
                  </Form.Item>
                  <Form.Item name="allowed_modes" label="可用模式">
                    <Select
                      mode="multiple"
                      options={[
                        { label: "标准模式", value: "standard" },
                        { label: "轻量模式", value: "light" },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="prompt_body" label="能力说明内容">
                    <Input.TextArea rows={14} placeholder="在这里编辑扩展能力说明与执行要求" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={savingSkill}>
                    保存扩展能力
                  </Button>
                </Form>
              ),
            },
            {
              key: "tools",
              label: "工具编辑",
              forceRender: true,
              children: (
                <Form
                  form={toolForm}
                  layout="vertical"
                  onFinish={async (values) => {
                    const toolId = String(values.tool_id || "").trim();
                    if (!toolId) {
                      message.warning("请先填写工具标识");
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
                      message.success(`扩展工具 ${toolId} 已保存`);
                      await loadPage();
                    } catch (error: any) {
                      message.error(error?.message || "保存扩展工具失败");
                    } finally {
                      setSavingTool(false);
                    }
                  }}
                >
                  <Form.Item label="加载已有扩展工具">
                    <Select
                      allowClear
                      placeholder="选择一个已有扩展工具加载到编辑器"
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
                  <Form.Item name="tool_id" label="工具标识" rules={[{ required: true, message: "请输入工具标识" }]}>
                    <Input placeholder="design_spec_alignment" />
                  </Form.Item>
                  <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
                    <Input placeholder="详细设计一致性检查工具" />
                  </Form.Item>
                  <Form.Item name="description" label="说明">
                    <Input placeholder="该扩展工具的执行目的与输出说明" />
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
                  <Form.Item name="allowed_experts_text" label="允许检查角色（逗号分隔角色标识）">
                    <Input placeholder="correctness_business" />
                  </Form.Item>
                  <Form.Item name="bound_skills_text" label="绑定扩展能力（逗号分隔能力标识）">
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
                    保存扩展工具
                  </Button>
                </Form>
              ),
            },
          ]}
        />
      </SettingsSectionCard>
    </div>
  );
};

export default SettingsPage;
