import express from "express";
import { nanoid } from "nanoid";
import { z } from "zod";
import { config } from "./config.js";
import { NgAgentSession } from "./ngagent-session.js";
import { wrapPrompt } from "./prompt.js";

const app = express();
const session = new NgAgentSession(config);

const chatRequestSchema = z.object({
  model: z.string().optional(),
  messages: z.array(
    z.object({
      role: z.string(),
      content: z.any(),
    }),
  ),
  stream: z.boolean().optional().default(false),
});

app.use(express.json({ limit: "2mb" }));

app.use((req, res, next) => {
  if (req.path === "/health") return next();

  const token = req.headers.authorization?.replace(/^Bearer\s+/i, "");
  if (token !== config.localProxyKey) {
    return res.status(401).json({
      error: {
        message: "Unauthorized",
        type: "auth_error",
      },
    });
  }

  next();
});

app.get("/health", (_req, res) => {
  res.json({ ok: true });
});

app.get("/v1/models", (_req, res) => {
  res.json({
    object: "list",
    data: [
      {
        id: "ngagent-builtin",
        object: "model",
        created: 0,
        owned_by: "local-ngagent",
      },
    ],
  });
});

app.post("/v1/chat/completions", async (req, res) => {
  const parsed = chatRequestSchema.safeParse(req.body);
  if (!parsed.success) {
    return res.status(400).json({
      error: {
        message: parsed.error.message,
        type: "invalid_request_error",
      },
    });
  }

  if (parsed.data.stream) {
    return res.status(400).json({
      error: {
        message: "Streaming is not enabled in the PTY bridge yet. Send stream=false.",
        type: "unsupported_feature",
      },
    });
  }

  try {
    const content = await session.ask(wrapPrompt(parsed.data.messages));
    const created = Math.floor(Date.now() / 1000);

    res.json({
      id: `chatcmpl-${nanoid()}`,
      object: "chat.completion",
      created,
      model: parsed.data.model || "ngagent-builtin",
      choices: [
        {
          index: 0,
          message: {
            role: "assistant",
            content,
          },
          finish_reason: "stop",
        },
      ],
      usage: {
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
      },
    });
  } catch (error) {
    res.status(502).json({
      error: {
        message: error instanceof Error ? error.message : "ngagent request failed",
        type: "ngagent_proxy_error",
      },
    });
  }
});

process.on("SIGINT", () => {
  session.dispose();
  process.exit(0);
});

process.on("SIGTERM", () => {
  session.dispose();
  process.exit(0);
});

app.listen(config.port, config.host, () => {
  console.log(`ngagent OpenAI proxy listening on http://${config.host}:${config.port}`);
});
