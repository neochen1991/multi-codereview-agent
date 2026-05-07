import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";

const baseUrl = "http://127.0.0.1:8877";
const apiKey = "smoke-key";

const server = spawn(process.execPath, ["src/server.js"], {
  cwd: new URL("..", import.meta.url),
  env: {
    ...process.env,
    HOST: "127.0.0.1",
    PORT: "8877",
    LOCAL_PROXY_KEY: apiKey,
    NGAGENT_SESSION_DRIVER: "pipe",
    NGAGENT_COMMAND: process.platform === "win32" ? "cmd.exe" : "/bin/sh",
    NGAGENT_ARGS: process.platform === "win32" ? "/c node scripts/mock-ngagent.js" : "-lc \"node scripts/mock-ngagent.js\"",
    NGAGENT_READY_PATTERN: "(^|\\n|\\r)ngagent>\\s*$",
    NGAGENT_STARTUP_GRACE_MS: "100",
  },
  stdio: ["ignore", "pipe", "pipe"],
});

let serverOutput = "";
server.stdout.on("data", (chunk) => {
  serverOutput += chunk;
});
server.stderr.on("data", (chunk) => {
  serverOutput += chunk;
});

try {
  await waitForServer();
  await postChat(false);
  await postChat(true);
  await postResponses();
  console.log("smoke ok");
} finally {
  server.kill();
}

async function waitForServer() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`${baseUrl}/health`);
      if (response.ok) return;
    } catch {
      await delay(100);
    }
  }

  throw new Error(`server did not start\n${serverOutput}`);
}

async function postChat(stream) {
  const response = await fetch(`${baseUrl}/v1/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "ngagent-builtin",
      stream,
      messages: [{ role: "user", content: "hello" }],
    }),
  });

  const text = await response.text();
  if (!response.ok || !text.includes("mock answer")) {
    throw new Error(`chat smoke failed: ${response.status} ${text}`);
  }
}

async function postResponses() {
  const response = await fetch(`${baseUrl}/v1/responses`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "ngagent-builtin",
      input: "hello responses",
    }),
  });

  const body = await response.json();
  if (!response.ok || !body.output_text?.includes("mock answer")) {
    throw new Error(`responses smoke failed: ${response.status} ${JSON.stringify(body)}`);
  }
}
