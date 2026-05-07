# ngagent OpenAI Proxy

Windows-local OpenAI-compatible proxy for an interactive `ngagent` CLI session.

This bridge does not read or expose OAuth tokens. It starts the already logged-in `ngagent` CLI in a pseudo terminal and sends prompts through that terminal.

## Windows Setup

Use Node 20 LTS on Windows. `node-pty` is a native dependency, and Node 20 is usually the least painful target.

From PowerShell or Windows Terminal:

```powershell
cd .\ngagent-proxy
npm install
copy .env.windows.example .env
notepad .env
npm start
```

Or use the bundled cmd launcher:

```powershell
cd .\ngagent-proxy
.\start-windows.cmd
```

Server:

```text
http://127.0.0.1:8787
```

## Windows .env

Important settings:

```env
HOST=127.0.0.1
PORT=8787
LOCAL_PROXY_KEY=your-local-key
NGAGENT_SESSION_DRIVER=pty
NGAGENT_USE_SHELL=false
NGAGENT_COMMAND=ngagent
NGAGENT_ARGS=
NGAGENT_READY_PATTERN=(^|\\n|\\r)(>|ngagent>|❯)\\s*$
```

If `ngagent` is not on `PATH`, use a full path:

```env
NGAGENT_COMMAND=C:\Users\your-name\AppData\Roaming\npm\ngagent.cmd
```

or:

```env
NGAGENT_COMMAND=C:\path\to\ngagent.exe
```

If `NGAGENT_COMMAND` points to a `.cmd` or `.bat` file, or startup fails with spawn-related errors, try:

```env
NGAGENT_USE_SHELL=true
```

If ngagent needs startup input, such as selecting a built-in model, set:

```env
NGAGENT_BOOTSTRAP_INPUT=/model builtin
```

## Routes

```text
GET  /health
GET  /v1/models
POST /v1/chat/completions
POST /v1/responses
```

## Test on Windows

PowerShell:

```powershell
curl.exe http://127.0.0.1:8787/v1/chat/completions `
  -H "Authorization: Bearer your-local-key" `
  -H "Content-Type: application/json" `
  -d "{""model"":""ngagent-builtin"",""messages"":[{""role"":""user"",""content"":""Say hello in one short sentence.""}]}"
```

Responses API style:

```powershell
curl.exe http://127.0.0.1:8787/v1/responses `
  -H "Authorization: Bearer your-local-key" `
  -H "Content-Type: application/json" `
  -d "{""model"":""ngagent-builtin"",""input"":""Say hello in one short sentence.""}"
```

## Connect Another Agent

Use:

```text
base_url: http://127.0.0.1:8787/v1
api_key: your-local-key
model: ngagent-builtin
```

## Tuning the Ready Pattern

Run ngagent manually in Windows Terminal and look at the prompt shown after an answer is complete. Examples:

```text
>
ngagent>
❯
```

Then set `NGAGENT_READY_PATTERN` in `.env`.

Example:

```env
NGAGENT_READY_PATTERN=(^|\\n|\\r)ngagent>\\s*$
```

If requests time out even though ngagent answered, the ready pattern is probably not matching the final prompt.

## Smoke Test

This validates the HTTP layer with a mock CLI:

```powershell
npm run smoke
```

The smoke test uses `NGAGENT_SESSION_DRIVER=pipe` internally. Real interactive ngagent should normally use:

```env
NGAGENT_SESSION_DRIVER=pty
```

## Notes

- Keep `HOST=127.0.0.1` unless you have a separate network security layer.
- Start with one request at a time. The proxy serializes all requests into the same ngagent session.
- `stream=true` is accepted, but this version emits the complete answer as one SSE delta after the CLI finishes.
- Do not configure this proxy to print, return, or persist OAuth tokens.
- If `node-pty` install fails on Windows, install Node 20 LTS and the Visual Studio Build Tools with the Desktop development with C++ workload.
