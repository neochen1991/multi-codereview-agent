# OpenCode AI 代码使用量统计插件

这个目录由 OpenCode 插件 `.opencode/plugins/ai-code-usage.js` 使用。

插件会统计 OpenCode 使用 AI 修改代码后，最终落到工作区里的代码改动量，并可以把统计数据上报到服务端。

## 统计口径

插件统计的是 git diff 里的代码行数，不是 token 数。

- `added`：本次 OpenCode session 开始后新增的代码行数
- `deleted`：本次 OpenCode session 开始后删除的代码行数
- `net`：净增代码行数，等于 `added - deleted`
- `files`：发生变化的代码文件数
- `byLanguage`：按文件扩展名汇总，例如 `js`、`ts`、`py`

插件会在 session 开始时记录一次 git diff baseline，所以 session 开始前已经存在的脏改动不会被算进去。

## 插件怎么使用

在需要统计的项目根目录里放置插件文件：

```text
你的项目\
  .opencode\
    plugins\
      ai-code-usage.js
      token-usage-probe.js
    ai-usage\
      README.md
```

也就是必须存在这个文件：

```text
.opencode\plugins\ai-code-usage.js
.opencode\plugins\token-usage-probe.js
```

然后在这个项目根目录里正常运行 OpenCode：

```powershell
opencode
```

OpenCode 运行过程中，插件会自动监听文件修改事件并统计数据。

`ai-code-usage.js` 负责统计 AI 生成/修改的代码行数。

`token-usage-probe.js` 负责探测 OpenCode 事件里是否包含 token usage，并在能提取到 token 时上报服务端。

## 工号怎么配置

工号通过客户端环境变量配置：

```powershell
$env:AI_USAGE_EMPLOYEE_ID="E10001"
```

建议每个使用者在启动 OpenCode 前都配置自己的工号。

完整客户端配置示例：

```powershell
$env:AI_USAGE_SERVER_URL="http://127.0.0.1:8790"
$env:AI_USAGE_API_KEY="change-me-server-key"
$env:AI_USAGE_CLIENT_ID="windows-dev-01"
$env:AI_USAGE_PROJECT="my-project"
$env:AI_USAGE_USER="alice"
$env:AI_USAGE_EMPLOYEE_ID="E10001"
opencode
```

字段说明：

```text
AI_USAGE_SERVER_URL    服务端地址
AI_USAGE_API_KEY       上报鉴权 key，必须和服务端一致
AI_USAGE_CLIENT_ID     客户端机器或环境标识，例如 windows-dev-01
AI_USAGE_PROJECT       项目名，例如 order-service
AI_USAGE_USER          用户名或账号名，例如 alice
AI_USAGE_EMPLOYEE_ID   工号，例如 E10001
```

如果没有配置 `AI_USAGE_EMPLOYEE_ID`，插件会使用：

```text
unknown-employee-id
```

## 本地输出文件

插件会生成这些文件：

```text
.opencode\ai-usage\usage.jsonl
.opencode\ai-usage\summary.json
.opencode\ai-usage\state.json
.opencode\ai-usage\pending-upload.json
.opencode\ai-usage\diagnostics.json
.opencode\ai-usage\token-event-probe.jsonl
.opencode\ai-usage\token-usage.jsonl
.opencode\ai-usage\pending-token-upload.json
```

说明：

```text
usage.jsonl           本地事件流水
summary.json          本地汇总结果
state.json            session baseline 和状态
pending-upload.json   未成功上报的事件队列
diagnostics.json      最近一次 git 统计诊断信息
token-event-probe.jsonl   token 探针事件记录，不包含完整消息内容
token-usage.jsonl         已提取到的 token 使用事件
pending-token-upload.json 未成功上报的 token 事件队列
```

查看本地汇总：

```powershell
type .opencode\ai-usage\summary.json
```

## 上报机制

插件会先把统计事件写到本地，再异步上报服务端。

如果服务端不可用或网络失败，事件会保留在：

```text
.opencode\ai-usage\pending-upload.json
```

下次 OpenCode 产生事件时会继续重试上报。

## Token 排行榜采集

当前没有统一模型网关时，token 排行榜先通过 `token-usage-probe.js` 探针插件采集。

探针会监听这些 OpenCode 事件：

```text
message.updated
session.updated
session.idle
session.status
session.compacted
```

它会递归查找事件里的字段，例如：

```text
usage
token
tokens
prompt_tokens
completion_tokens
input_tokens
output_tokens
total_tokens
```

如果事件里能找到 token usage，会写入：

```text
.opencode\ai-usage\token-usage.jsonl
```

并上报到服务端：

```text
POST /api/token-usage/events
```

如果事件里没有 token usage，则只会写探针记录：

```text
.opencode\ai-usage\token-event-probe.jsonl
```

这用于判断 OpenCode 当前版本是否暴露 token 字段。

查看 token 探针结果：

```powershell
type .opencode\ai-usage\token-event-probe.jsonl
type .opencode\ai-usage\token-usage.jsonl
```

## 前置要求

项目必须是 git 仓库，因为插件依赖：

```powershell
git diff --numstat --
git ls-files --others --exclude-standard
```

插件会统计两类文件：

```text
已跟踪文件的改动：来自 git diff --numstat --
未跟踪的新代码文件：来自 git ls-files --others --exclude-standard
```

如果 `git diff --numstat --` 是空的，但 AI 新建了未跟踪代码文件，插件也会把这些新文件按实际行数计入 `added`。

如果统计仍然是 0，请在项目根目录检查：

```powershell
git status --short
git diff --numstat --
git ls-files --others --exclude-standard
type .opencode\ai-usage\diagnostics.json
```
