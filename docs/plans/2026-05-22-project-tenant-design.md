# 项目多租户设计方案

## 背景

当前系统已经支持多代码仓、Postgres 存储后端、专家配置、知识库、GitNexus 图谱和自动拉取 MR。但这些能力现在主要还是“全局配置 + 全局数据”的形态。

工具要推广到多个团队使用后，必须把“项目”作为第一层隔离边界。不同团队只能看到自己项目下的代码仓、规范文档、专家配置、审核历史和结果。

## 目标

- 支持一个在线系统服务多个项目团队。
- 每个项目独立管理代码仓、规范文档、专家配置、运行设置和审核历史。
- 审核过程中所有上下文都按项目读取，避免串项目。
- 前端让用户能明确感知“当前正在操作哪个项目”。
- 保留现有单项目使用方式，降低迁移风险。

## 推荐方案

采用“共享数据库 + 业务表增加 `project_id` + 应用层强制项目过滤”的方案。

不建议第一版使用“每项目一个数据库/schema”或“每团队一个实例”。这两种方式会让部署、升级、统计、专家规则复用和运行治理变复杂，不适合当前工具从开发期走向在线系统的阶段。

## 核心模型

### Project

项目是租户隔离的主实体。

字段建议：

| 字段 | 说明 |
|---|---|
| `project_id` | 项目唯一 ID |
| `name` | 项目名称 |
| `description` | 项目说明 |
| `owner_team` | 归属团队 |
| `status` | active / archived |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

### ProjectMember

项目成员和角色。

| 角色 | 权限 |
|---|---|
| `project_admin` | 管理项目、成员、代码仓、知识库、专家和设置 |
| `reviewer` | 创建审核、查看审核、提交 CodeHub 评论 |
| `viewer` | 只读查看审核历史和报告 |

### ProjectRepository

项目下的代码仓配置。

复用当前 `CodeRepositorySettings` 的字段，但归属到项目。

关键字段：

| 字段 | 说明 |
|---|---|
| `project_id` | 所属项目 |
| `repository_id` | 仓库 ID |
| `clone_url` | Git 地址 |
| `web_url_prefixes` | MR URL 匹配前缀 |
| `local_path` | 本地仓路径 |
| `default_branch` | 默认分支 |
| `gitnexus_enabled` | 是否启用 GitNexus |
| `database_source_ids` | 绑定的数据源 |

### ProjectSettings

项目级运行设置。

第一版建议拆出以下配置：

- 审核质量阈值。
- 默认专家选择策略。
- 项目启用的专家列表。
- 项目规范文档绑定。
- GitNexus 参数。
- 影响报告模板。
- 代码平台 Token 引用。
- LLM 模型和超时策略。

## 数据隔离范围

以下数据必须增加或派生 `project_id`：

| 数据 | 隔离方式 |
|---|---|
| 审核任务 `reviews` | 增加 `project_id` 字段，列表和详情都按项目过滤 |
| 消息 `messages` | 增加 `project_id`，或通过 `review_id` 校验归属 |
| 发现 `findings` | 增加 `project_id`，或通过 `review_id` 校验归属 |
| 问题 `issues` | 增加 `project_id`，或通过 `review_id` 校验归属 |
| 反馈 `feedback` | 增加 `project_id`，或通过 `review_id` 校验归属 |
| 知识库 `knowledge_documents` | 增加 `project_id` |
| 结构化规则 `knowledge_review_rules` | 增加 `project_id` |
| 专家配置 `experts` | 内置专家全局，项目覆盖配置增加 `project_id` |
| 运行设置 `runtime_settings` | 拆成系统设置和项目设置 |
| GitNexus 图谱 | 按 `project_id + repository_id` 定位 |
| 文件产物 | 改为 `storage/projects/{project_id}/...` |

## 接口设计

新接口以项目为路径边界：

```text
GET  /api/projects
POST /api/projects
GET  /api/projects/{project_id}
PUT  /api/projects/{project_id}

GET  /api/projects/{project_id}/reviews
POST /api/projects/{project_id}/reviews
GET  /api/projects/{project_id}/reviews/{review_id}

GET  /api/projects/{project_id}/repositories
PUT  /api/projects/{project_id}/repositories

GET  /api/projects/{project_id}/knowledge
POST /api/projects/{project_id}/knowledge

GET  /api/projects/{project_id}/experts
PUT  /api/projects/{project_id}/experts/{expert_id}

GET  /api/projects/{project_id}/settings
PUT  /api/projects/{project_id}/settings
```

兼容期可以保留现有 `/api/reviews`、`/api/settings` 等接口，但必须要求解析当前项目上下文。没有项目上下文时，不能返回全量数据。

## 前端设计

样式稿位置：

`docs/plans/project-tenant-ui-style.html`

设计原则：

- 左侧固定项目切换器，用户始终知道当前项目。
- 导航从“系统功能”转为“项目工作台”。
- 首页展示当前项目的代码仓、成员、规则、专家和审核概况。
- 设置页拆为项目设置，避免不同团队配置互相影响。
- 审核创建页只能选择当前项目绑定的代码仓。
- 历史记录、知识库、专家中心默认只展示当前项目数据。

主要页面调整：

| 页面 | 调整 |
|---|---|
| 首页 | 增加当前项目概览、项目待审核队列 |
| 审核工作台 | 创建任务时自动带 `project_id` |
| 历史记录 | 按当前项目过滤 |
| 专家中心 | 展示系统内置专家 + 项目覆盖配置 |
| 知识库 | 只展示当前项目规范文档 |
| 设置页 | 拆出项目代码仓、数据源、GitNexus、阈值、模板 |

## 后端上下文流转

一次审核必须从创建开始绑定项目：

1. 前端在当前项目下创建审核。
2. 后端校验用户是否有该项目权限。
3. `ReviewTask.subject.project_id` 写入项目 ID。
4. 代码仓解析只在当前项目的仓库列表中执行。
5. 知识库检索只读取当前项目文档。
6. 专家配置先读项目覆盖配置，再回退到内置专家。
7. GitNexus 图谱按 `project_id + repository_id` 读取。
8. 审核结果、消息、问题和报告写入当前项目空间。

## 迁移策略

第一版迁移时创建一个默认项目：

```text
project_id = default
name = 默认项目
```

现有数据全部归入默认项目：

- 历史审核记录写入 `project_id = default`。
- 现有代码仓配置迁移为默认项目代码仓。
- 现有知识库文档迁移到默认项目。
- 现有专家配置作为默认项目覆盖配置。

这样可以保证老数据不丢，老使用方式也能继续跑。

## 实施顺序

第一阶段：项目边界闭环

1. 新增项目模型和项目仓储。
2. 数据表增加 `project_id`。
3. 后端新增项目上下文解析和权限校验。
4. 审核、历史、知识库、专家、设置接口按项目过滤。
5. 前端增加项目切换器和项目级页面入口。

第二阶段：项目配置下沉

1. 多代码仓配置归属项目。
2. GitNexus 图谱和手动建图入口按项目隔离。
3. 规范文档和结构化规则按项目隔离。
4. 专家配置支持项目级覆盖。
5. 自动拉取 MR 按项目调度。

第三阶段：在线系统能力

1. 接入统一登录或网关身份。
2. 支持成员角色和权限控制。
3. 增加项目创建、归档和成员管理。
4. 增加系统管理员视角，用于跨项目运营和排障。

## 风险与约束

- 只在前端做项目筛选是不够的，后端必须强制过滤。
- GitNexus、文件产物、日志和缓存也要带项目维度，否则仍可能串数据。
- 运行设置不能继续只有全局一份，否则不同团队阈值和模型配置会互相覆盖。
- 第一版权限模型不宜过细，否则实现复杂度会拖慢多租户主线。

