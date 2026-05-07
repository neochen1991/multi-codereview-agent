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

const responsesRequestSchema = z.object({
  model: z.string().optional(),
  input: z.any(),
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
    return completeChatStream(res, parsed.data);
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

app.post("/v1/responses", async (req, res) => {
  const parsed = responsesRequestSchema.safeParse(req.body);
  if (!parsed.success) {
    return res.status(400).json({
      error: {
        message: parsed.error.message,
        type: "invalid_request_error",
      },
    });
  }

  const messages = responsesInputToMessages(parsed.data.input);
  try {
    const content = await session.ask(wrapPrompt(messages));
    const id = `resp_${nanoid()}`;

    if (parsed.data.stream) {
      return writeResponsesStream(res, id, parsed.data.model || "ngagent-builtin", content);
    }

    res.json({
      id,
      object: "response",
      created_at: Math.floor(Date.now() / 1000),
      model: parsed.data.model || "ngagent-builtin",
      status: "completed",
      output: [
        {
          id: `msg_${nanoid()}`,
          type: "message",
          role: "assistant",
          content: [
            {
              type: "output_text",
              text: content,
            },
          ],
        },
      ],
      output_text: content,
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

async function completeChatStream(res, data) {
  try {
    const content = await session.ask(wrapPrompt(data.messages));
    const id = `chatcmpl-${nanoid()}`;
    const created = Math.floor(Date.now() / 1000);

    writeSseHeaders(res);
    writeSse(res, {
      id,
      object: "chat.completion.chunk",
      created,
      model: data.model || "ngagent-builtin",
      choices: [
        {
          index: 0,
          delta: {
            role: "assistant",
            content,
          },
          finish_reason: null,
        },
      ],
    });
    writeSse(res, {
      id,
      object: "chat.completion.chunk",
      created,
      model: data.model || "ngagent-builtin",
      choices: [
        {
          index: 0,
          delta: {},
          finish_reason: "stop",
        },
      ],
    });
    res.write("data: [DONE]\n\n");
    res.end();
  } catch (error) {
    writeSseError(res, error);
  }
}

function responsesInputToMessages(input) {
  if (typeof input === "string") {
    return [{ role: "user", content: input }];
  }

  if (Array.isArray(input)) {
    return input.map((item) => {
      if (typeof item === "string") return { role: "user", content: item };
      return {
        role: item.role || "user",
        content: Array.isArray(item.content)
          ? item.content
              .map((part) => part.text || part.content || "")
              .filter(Boolean)
              .join("\n")
          : item.content ?? item.text ?? "",
      };
    });
  }

  return [{ role: "user", content: JSON.stringify(input) }];
}

function writeResponsesStream(res, id, model, content) {
  writeSseHeaders(res);
  writeSse(res, {
    type: "response.created",
    response: {
      id,
      object: "response",
      created_at: Math.floor(Date.now() / 1000),
      model,
      status: "in_progress",
    },
  });
  writeSse(res, {
    type: "response.output_text.delta",
    item_id: `msg_${nanoid()}`,
    output_index: 0,
    content_index: 0,
    delta: content,
  });
  writeSse(res, {
    type: "response.completed",
    response: {
      id,
      object: "response",
      created_at: Math.floor(Date.now() / 1000),
      model,
      status: "completed",
      output_text: content,
    },
  });
  res.write("data: [DONE]\n\n");
  res.end();
}

function writeSseHeaders(res) {
  res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders?.();
}

function writeSse(res, payload) {
  res.write(`data: ${JSON.stringify(payload)}\n\n`);
}

function writeSseError(res, error) {
  if (!res.headersSent) writeSseHeaders(res);
  writeSse(res, {
    error: {
      message: error instanceof Error ? error.message : "ngagent request failed",
      type: "ngagent_proxy_error",
    },
  });
  res.write("data: [DONE]\n\n");
  res.end();
}

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
