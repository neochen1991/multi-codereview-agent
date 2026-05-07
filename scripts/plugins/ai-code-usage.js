import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const USAGE_DIR = path.join(process.cwd(), ".opencode", "ai-usage");
const EVENTS_FILE = path.join(USAGE_DIR, "usage.jsonl");
const SUMMARY_FILE = path.join(USAGE_DIR, "summary.json");
const STATE_FILE = path.join(USAGE_DIR, "state.json");

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
        recordSessionStart(event);
        return;
      }

      if (
        event.type === "file.edited" ||
        event.type === "tool.execute.after" ||
        event.type === "session.idle"
      ) {
        recordUsage(event);
      }
    },
  };
};

function recordSessionStart(event) {
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
}

function recordUsage(event) {
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
    ts: session.updatedAt,
    eventType: event.type,
    sessionId,
    metric: "ai_code_diff_lines",
    added: totals.added,
    deleted: totals.deleted,
    net: totals.net,
    files: totals.files,
    byLanguage: totals.byLanguage,
    note: "Counts code-file git diff lines added/deleted since this OpenCode session baseline.",
  };

  appendJsonl(EVENTS_FILE, entry);
  writeState(state);
  writeSummary(state);
}

function readGitNumstat() {
  try {
    const output = execFileSync("git", ["diff", "--numstat", "--"], {
      cwd: process.cwd(),
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    });

    const stats = {};
    for (const line of output.split(/\r?\n/)) {
      if (!line.trim()) continue;

      const [addedRaw, deletedRaw, ...fileParts] = line.split(/\t/);
      const file = fileParts.join("\t");
      if (!isCodeFile(file)) continue;

      const added = parseNumstatValue(addedRaw);
      const deleted = parseNumstatValue(deletedRaw);
      stats[file] = { added, deleted, language: languageForPath(file) };
    }
    return stats;
  } catch {
    return {};
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
    metric: "ai_code_diff_lines",
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

function writeJson(file, value) {
  writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

function appendJsonl(file, value) {
  writeFileSync(file, `${JSON.stringify(value)}\n`, { flag: "a" });
}
