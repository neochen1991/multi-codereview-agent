import { execFileSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const USAGE_DIR = path.join(process.cwd(), ".opencode", "ai-usage");
const EVENTS_FILE = path.join(USAGE_DIR, "usage.jsonl");
const PENDING_UPLOAD_FILE = path.join(USAGE_DIR, "pending-upload.json");
const DIAGNOSTICS_FILE = path.join(USAGE_DIR, "diagnostics.json");
const SUMMARY_FILE = path.join(USAGE_DIR, "summary.json");
const STATE_FILE = path.join(USAGE_DIR, "state.json");
const METRIC_NAME = "ai_code_diff_lines";

const CODE_EXTENSIONS = new Set([
  ".c",
  ".cc",
  ".cpp",
  ".cs",
  ".css",
  ".dart",
  ".go",
  ".h",
  ".hpp",
  ".html",
  ".java",
  ".js",
  ".jsx",
  ".kt",
  ".kts",
  ".lua",
  ".mjs",
  ".mm",
  ".php",
  ".py",
  ".rb",
  ".rs",
  ".scala",
  ".scss",
  ".sh",
  ".svelte",
  ".swift",
  ".ts",
  ".tsx",
  ".vue",
]);

const EXCLUDED_PATH_PARTS = new Set([
  ".git",
  ".opencode",
  "node_modules",
  "dist",
  "build",
  "coverage",
  "__pycache__",
  ".venv",
  "venv",
]);

export const AIUsagePlugin = async ({ client, $ }) => {
  ensureUsageDir();

  return {
    event: async ({ event }) => {
      if (event.type === "session.created") {
        await recordSessionStart(event);
        return;
      }

      if (
        event.type === "file.edited" ||
        event.type === "tool.execute.after" ||
        event.type === "session.idle"
      ) {
        await recordUsage(event);
      }
    },
  };
};

async function recordSessionStart(event) {
  const state = readState();
  const sessionId = getSessionId(event);
  const baseline = readGitNumstat();

  state.sessions[sessionId] = {
    sessionId,
    startedAt: new Date().toISOString(),
    baseline,
    lastTotals: emptyTotals(),
  };

  writeState(state);
  writeSummary(state);
  await flushPendingUploads();
}

async function recordUsage(event) {
  const state = readState();
  const sessionId = getSessionId(event);

  if (!state.sessions[sessionId]) {
    state.sessions[sessionId] = {
      sessionId,
      startedAt: new Date().toISOString(),
      baseline: readGitNumstat(),
      lastTotals: emptyTotals(),
    };
  }

  const session = state.sessions[sessionId];
  const current = readGitNumstat();
  const totals = diffNumstat(session.baseline, current);

  if (sameTotals(totals, session.lastTotals)) {
    return;
  }

  session.lastTotals = totals;
  session.updatedAt = new Date().toISOString();

  const entry = {
    eventId: createEventId(session.sessionId, session.updatedAt, totals),
    ts: session.updatedAt,
    eventType: event.type,
    sessionId,
    metric: METRIC_NAME,
    clientId: uploadConfig().clientId,
    project: uploadConfig().project,
    user: uploadConfig().user,
    employeeId: uploadConfig().employeeId,
    added: totals.added,
    deleted: totals.deleted,
    net: totals.net,
    files: totals.files,
    byLanguage: totals.byLanguage,
    note: "Counts code-file git diff lines added/deleted since this OpenCode session baseline.",
  };

  appendJsonl(EVENTS_FILE, entry);
  enqueueUpload(entry);
  writeState(state);
  writeSummary(state);
  await flushPendingUploads();
}

function readGitNumstat() {
  try {
    const output = execFileSync("git", ["diff", "--numstat", "--"], {
      cwd: process.cwd(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });

    const stats = {};
    let trackedDiffFiles = 0;
    for (const line of output.split(/\r?\n/)) {
      if (!line.trim()) continue;

      const [addedRaw, deletedRaw, ...fileParts] = line.split(/\t/);
      const file = fileParts.join("\t");
      if (!isCodeFile(file)) continue;

      const added = parseNumstatValue(addedRaw);
      const deleted = parseNumstatValue(deletedRaw);
      stats[file] = { added, deleted, language: languageForPath(file) };
      trackedDiffFiles += 1;
    }

    const untrackedCodeFiles = readUntrackedCodeFiles();
    for (const file of untrackedCodeFiles) {
      if (stats[file]) continue;
      stats[file] = {
        added: countFileLines(file),
        deleted: 0,
        language: languageForPath(file),
      };
    }

    writeDiagnostics({
      ok: true,
      trackedDiffFiles,
      untrackedCodeFiles,
      countedFiles: Object.keys(stats).length,
      updatedAt: new Date().toISOString(),
    });

    return stats;
  } catch (error) {
    writeDiagnostics({
      ok: false,
      error: error instanceof Error ? error.message : "git diff failed",
      updatedAt: new Date().toISOString(),
    });
    return {};
  }
}

function readUntrackedCodeFiles() {
  try {
    const output = execFileSync("git", ["ls-files", "--others", "--exclude-standard"], {
      cwd: process.cwd(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });

    return output
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .filter(isCodeFile);
  } catch {
    return [];
  }
}

function countFileLines(file) {
  try {
    const content = readFileSync(path.join(process.cwd(), file), "utf8");
    if (content.length === 0) return 0;
    return content.endsWith("\n") ? content.split(/\r?\n/).length - 1 : content.split(/\r?\n/).length;
  } catch {
    return 0;
  }
}

function diffNumstat(baseline, current) {
  const totals = emptyTotals();
  const files = new Set([...Object.keys(baseline), ...Object.keys(current)]);

  for (const file of files) {
    const before = baseline[file] ?? { added: 0, deleted: 0, language: languageForPath(file) };
    const after = current[file] ?? { added: 0, deleted: 0, language: languageForPath(file) };
    const added = Math.max(0, after.added - before.added);
    const deleted = Math.max(0, after.deleted - before.deleted);
    if (added === 0 && deleted === 0) continue;

    const language = after.language || before.language || languageForPath(file);
    totals.added += added;
    totals.deleted += deleted;
    totals.net += added - deleted;
    totals.files += 1;

    totals.byLanguage[language] ??= { added: 0, deleted: 0, net: 0, files: 0 };
    totals.byLanguage[language].added += added;
    totals.byLanguage[language].deleted += deleted;
    totals.byLanguage[language].net += added - deleted;
    totals.byLanguage[language].files += 1;
  }

  return totals;
}

function writeSummary(state) {
  const byDate = {};
  const bySession = {};
  const total = emptyTotals();

  for (const session of Object.values(state.sessions)) {
    const totals = session.lastTotals ?? emptyTotals();
    const date = (session.updatedAt || session.startedAt || new Date().toISOString()).slice(0, 10);

    bySession[session.sessionId] = {
      startedAt: session.startedAt,
      updatedAt: session.updatedAt,
      ...totals,
    };

    byDate[date] ??= emptyTotals();
    mergeTotals(byDate[date], totals);
    mergeTotals(total, totals);
  }

  writeJson(SUMMARY_FILE, {
    metric: METRIC_NAME,
    generatedAt: new Date().toISOString(),
    total,
    byDate,
    bySession,
  });
}

function mergeTotals(target, source) {
  target.added += source.added;
  target.deleted += source.deleted;
  target.net += source.net;
  target.files += source.files;

  for (const [language, totals] of Object.entries(source.byLanguage ?? {})) {
    target.byLanguage[language] ??= { added: 0, deleted: 0, net: 0, files: 0 };
    target.byLanguage[language].added += totals.added;
    target.byLanguage[language].deleted += totals.deleted;
    target.byLanguage[language].net += totals.net;
    target.byLanguage[language].files += totals.files;
  }
}

function emptyTotals() {
  return {
    added: 0,
    deleted: 0,
    net: 0,
    files: 0,
    byLanguage: {},
  };
}

function sameTotals(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function getSessionId(event) {
  return (
    event.sessionID ||
    event.sessionId ||
    event.session?.id ||
    event.properties?.sessionID ||
    event.properties?.sessionId ||
    "unknown-session"
  );
}

function isCodeFile(file) {
  if (!file || file === "/dev/null") return false;

  const normalized = file.split(path.sep).join("/");
  const parts = normalized.split("/");
  if (parts.some((part) => EXCLUDED_PATH_PARTS.has(part))) return false;

  return CODE_EXTENSIONS.has(path.extname(normalized).toLowerCase());
}

function languageForPath(file) {
  const ext = path.extname(file).toLowerCase().replace(".", "");
  return ext || "unknown";
}

function parseNumstatValue(value) {
  if (value === "-") return 0;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function createEventId(sessionId, ts, totals) {
  const hash = createHash("sha256")
    .update(JSON.stringify({ sessionId, ts, totals }))
    .digest("hex")
    .slice(0, 24);
  return `evt_${hash || randomUUID()}`;
}

function uploadConfig() {
  return {
    serverUrl: trimTrailingSlash(process.env.AI_USAGE_SERVER_URL || ""),
    apiKey: process.env.AI_USAGE_API_KEY || "",
    clientId: process.env.AI_USAGE_CLIENT_ID || machineClientId(),
    project: process.env.AI_USAGE_PROJECT || path.basename(process.cwd()),
    user: process.env.AI_USAGE_USER || process.env.USERNAME || process.env.USER || "unknown-user",
    employeeId: process.env.AI_USAGE_EMPLOYEE_ID || "unknown-employee-id",
    batchSize: parsePositiveInt(process.env.AI_USAGE_UPLOAD_BATCH_SIZE, 50),
  };
}

function machineClientId() {
  const source = `${process.env.COMPUTERNAME || ""}:${process.cwd()}`;
  return `client_${createHash("sha256").update(source).digest("hex").slice(0, 16)}`;
}

function enqueueUpload(entry) {
  const config = uploadConfig();
  if (!config.serverUrl || !config.apiKey) return;

  const pending = readPendingUploads();
  if (pending.some((item) => item.eventId === entry.eventId)) return;

  pending.push(entry);
  writeJson(PENDING_UPLOAD_FILE, pending);
}

async function flushPendingUploads() {
  const config = uploadConfig();
  if (!config.serverUrl || !config.apiKey) return;

  const pending = readPendingUploads();
  if (pending.length === 0) return;

  const batch = pending.slice(0, config.batchSize);
  try {
    const response = await fetch(`${config.serverUrl}/api/usage/events`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${config.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ events: batch }),
    });

    if (!response.ok) return;

    const uploaded = new Set(batch.map((item) => item.eventId));
    writeJson(
      PENDING_UPLOAD_FILE,
      pending.filter((item) => !uploaded.has(item.eventId)),
    );
  } catch {
    // Keep pending events for the next OpenCode event.
  }
}

function readPendingUploads() {
  if (!existsSync(PENDING_UPLOAD_FILE)) return [];

  try {
    const parsed = JSON.parse(readFileSync(PENDING_UPLOAD_FILE, "utf8"));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function trimTrailingSlash(value) {
  return value.replace(/\/+$/g, "");
}

function parsePositiveInt(value, fallback) {
  const parsed = Number.parseInt(value || "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function ensureUsageDir() {
  mkdirSync(USAGE_DIR, { recursive: true });
}

function readState() {
  if (!existsSync(STATE_FILE)) {
    return { version: 1, sessions: {} };
  }

  try {
    return JSON.parse(readFileSync(STATE_FILE, "utf8"));
  } catch {
    return { version: 1, sessions: {} };
  }
}

function writeState(state) {
  writeJson(STATE_FILE, state);
}

function writeDiagnostics(value) {
  writeJson(DIAGNOSTICS_FILE, value);
}

function writeJson(file, value) {
  writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

function appendJsonl(file, value) {
  writeFileSync(file, `${JSON.stringify(value)}\n`, { flag: "a" });
}
