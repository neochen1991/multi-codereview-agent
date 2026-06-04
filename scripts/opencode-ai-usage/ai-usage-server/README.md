# AI 使用量统计服务端

这是 OpenCode AI 代码使用量统计的服务端，负责接收各个客户端插件上报的数据，并存入 SQLite。

服务端会按工号、用户、项目、客户端、日期等维度汇总 AI 代码生成/修改数据。

服务端也支持 token 使用量排行榜。当前没有统一网关时，token 数据由 OpenCode 客户端探针插件上报。

## 启动服务端

Windows PowerShell：

```powershell
cd .\ai-usage-server
npm install
copy .env.example .env
notepad .env
npm start
```

默认服务地址：

```text
http://127.0.0.1:8790
```

`.env` 示例：

```env
HOST=127.0.0.1
PORT=8790
AI_USAGE_API_KEY=change-me-server-key
AI_USAGE_DB_PATH=./data/ai-usage.sqlite
```

`AI_USAGE_API_KEY` 是客户端上报时使用的鉴权 key，客户端和服务端必须保持一致。

## Windows 依赖说明

服务端使用 `better-sqlite3` 写 SQLite。它是原生依赖。

如果 Windows 下 `npm install` 失败，请安装：

- Node 20 LTS 或 Node 22 LTS
- Visual Studio Build Tools
- `Desktop development with C++` 工作负载

## 客户端怎么配置工号

每个客户端启动 OpenCode 前配置环境变量：

```powershell
$env:AI_USAGE_SERVER_URL="http://127.0.0.1:8790"
$env:AI_USAGE_API_KEY="change-me-server-key"
$env:AI_USAGE_CLIENT_ID="windows-dev-01"
$env:AI_USAGE_PROJECT="my-project"
$env:AI_USAGE_USER="alice"
$env:AI_USAGE_EMPLOYEE_ID="E10001"
opencode
```

工号字段是：

```powershell
$env:AI_USAGE_EMPLOYEE_ID="E10001"
```

上报到服务端后会保存为 SQLite 字段：

```text
employee_id
```

服务端汇总接口会返回：

```text
byEmployeeId
```

## 客户端插件怎么使用

在每个需要统计的项目里放置插件：

```text
你的项目\
  .opencode\
    plugins\
      ai-code-usage.js
      token-usage-probe.js
```

然后在项目根目录启动 OpenCode：

```powershell
opencode
```

插件会自动统计并上报。

其中：

```text
ai-code-usage.js      统计 AI 生成/修改代码行数
token-usage-probe.js  探测并上报 token usage
```

## 查询汇总数据

查询全部汇总：

```powershell
curl.exe http://127.0.0.1:8790/api/usage/summary `
  -H "Authorization: Bearer change-me-server-key"
```

按工号查询：

```powershell
curl.exe "http://127.0.0.1:8790/api/usage/summary?employeeId=E10001" `
  -H "Authorization: Bearer change-me-server-key"
```

按项目和工号查询：

```powershell
curl.exe "http://127.0.0.1:8790/api/usage/summary?project=my-project&employeeId=E10001" `
  -H "Authorization: Bearer change-me-server-key"
```

查询最近事件：

```powershell
curl.exe "http://127.0.0.1:8790/api/usage/events?limit=20" `
  -H "Authorization: Bearer change-me-server-key"
```

## 查询 Token 使用排行榜

查询全部 token 排行榜：

```powershell
curl.exe http://127.0.0.1:8790/api/token-usage/ranking `
  -H "Authorization: Bearer change-me-server-key"
```

按时间查询：

```powershell
curl.exe "http://127.0.0.1:8790/api/token-usage/ranking?from=2026-06-01&to=2026-06-30" `
  -H "Authorization: Bearer change-me-server-key"
```

按项目查询：

```powershell
curl.exe "http://127.0.0.1:8790/api/token-usage/ranking?project=my-project" `
  -H "Authorization: Bearer change-me-server-key"
```

按工号查询：

```powershell
curl.exe "http://127.0.0.1:8790/api/token-usage/ranking?employeeId=E10001" `
  -H "Authorization: Bearer change-me-server-key"
```

Token 汇总接口：

```powershell
curl.exe http://127.0.0.1:8790/api/token-usage/summary `
  -H "Authorization: Bearer change-me-server-key"
```

## API

```text
GET  /health
POST /api/usage/events
GET  /api/usage/summary
GET  /api/usage/events
POST /api/token-usage/events
GET  /api/token-usage/ranking
GET  /api/token-usage/summary
```

所有 `/api/*` 接口都需要请求头：

```text
Authorization: Bearer <AI_USAGE_API_KEY>
```

## SQLite 数据库

默认数据库路径：

```text
ai-usage-server\data\ai-usage.sqlite
```

事件会按 `eventId` 去重，客户端重复上报不会重复计数。

核心字段：

```text
event_id
ts
client_id
project
user_name
employee_id
session_id
added
deleted
net
files
by_language
```

Token 表核心字段：

```text
event_id
ts
source
client_id
project
user_name
employee_id
session_id
message_id
model
provider
prompt_tokens
completion_tokens
total_tokens
raw_usage
```
