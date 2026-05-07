import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const tempDir = mkdtempSync(path.join(tmpdir(), "ai-usage-server-"));
const dbPath = path.join(tempDir, "usage.sqlite");
const baseUrl = "http://127.0.0.1:8890";
const apiKey = "smoke-key";

const server = spawn(process.execPath, ["src/server.js"], {
  cwd: new URL("..", import.meta.url),
  env: {
    ...process.env,
    HOST: "127.0.0.1",
    PORT: "8890",
    AI_USAGE_API_KEY: apiKey,
    AI_USAGE_DB_PATH: dbPath,
  },
  stdio: ["ignore", "pipe", "pipe"],
});

let output = "";
server.stdout.on("data", (chunk) => {
  output += chunk;
});
server.stderr.on("data", (chunk) => {
  output += chunk;
});

try {
  await waitForServer();
  await postEvents();
  await postEvents();
  await assertSummary();
  console.log("smoke ok");
} finally {
  server.kill();
  rmSync(tempDir, { recursive: true, force: true });
}

async function waitForServer() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(`${baseUrl}/health`);
      if (response.ok) return;
    } catch {
      await delay(100);
    }
  }

  throw new Error(`server did not start\n${output}`);
}

async function postEvents() {
  const response = await fetch(`${baseUrl}/api/usage/events`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      events: [
        {
          eventId: "evt_smoke_1",
          ts: "2026-05-07T00:00:00.000Z",
          eventType: "file.edited",
          sessionId: "session-1",
          metric: "ai_code_diff_lines",
          clientId: "client-1",
          project: "project-1",
          user: "alice",
          employeeId: "E10001",
          added: 10,
          deleted: 2,
          net: 8,
          files: 1,
          byLanguage: {
            js: { added: 10, deleted: 2, net: 8, files: 1 },
          },
        },
      ],
    }),
  });

  if (!response.ok) {
    throw new Error(`post failed: ${response.status} ${await response.text()}`);
  }
}

async function assertSummary() {
  const response = await fetch(`${baseUrl}/api/usage/summary`, {
    headers: {
      Authorization: `Bearer ${apiKey}`,
    },
  });
  const summary = await response.json();

  if (
    summary.total.added !== 10 ||
    summary.total.deleted !== 2 ||
    summary.byLanguage.js.added !== 10 ||
    summary.byEmployeeId.E10001.added !== 10
  ) {
    throw new Error(`unexpected summary: ${JSON.stringify(summary)}`);
  }
}
