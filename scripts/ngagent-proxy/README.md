# ngagent OpenAI Proxy

Local OpenAI-compatible proxy for an interactive `ngagent` CLI session.

This bridge does not read or expose OAuth tokens. It starts the already logged-in `ngagent` CLI in a pseudo terminal and sends prompts through that terminal.

## Install

```bash
npm install
cp .env.example .env
```

Edit `.env`:

```bash
LOCAL_PROXY_KEY=your-local-key
NGAGENT_COMMAND=ngagent
NGAGENT_ARGS=
NGAGENT_READY_PATTERN=(^|\\n|\\r)(>|ngagent>|❯)\\s*$
```

`NGAGENT_READY_PATTERN` is the important part. Set it to the prompt that appears after ngagent finishes an answer and is ready for the next input.

## Start

```bash
npm start
```

Server:

```text
http://127.0.0.1:8787
```

## Test

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer your-local-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ngagent-builtin",
    "messages": [
      { "role": "user", "content": "Say hello in one short sentence." }
    ]
  }'
```

## Connect Another Agent

Use:

```text
base_url: http://127.0.0.1:8787/v1
api_key: your-local-key
model: ngagent-builtin
```

## Notes

- Keep the server bound to `127.0.0.1` unless you have a separate network security layer.
- Start with one request at a time. The proxy serializes all requests into the same PTY session.
- Streaming is intentionally disabled in this first version because interactive terminal output is harder to delimit safely.
- Do not configure this proxy to print, return, or persist OAuth tokens.
