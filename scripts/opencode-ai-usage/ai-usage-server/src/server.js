import "dotenv/config";

import Database from "better-sqlite3";
import express from "express";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { z } from "zod";

const host = process.env.HOST || "127.0.0.1";
const port = parsePositiveInt(process.env.PORT, 8790);
const apiKey = process.env.AI_USAGE_API_KEY || "change-me-server-key";
const dbPath = process.env.AI_USAGE_DB_PATH || "./data/ai-usage.sqlite";

mkdirSync(path.dirname(dbPath), { recursive: true });

const db = new Database(dbPath);
db.pragma("journal_mode = WAL");
db.pragma("foreign_keys = ON");
initDb();

const app = express();
app.use(express.json({ limit: "2mb" }));

const usageEventSchema = z.object({
  eventId: z.string().min(1),
  ts: z.string().min(1),
  eventType: z.string().optional().default("unknown"),
  sessionId: z.string().min(1),
  metric: z.literal("ai_code_diff_lines"),
  clientId: z.string().min(1),
  project: z.string().min(1),
  user: z.string().min(1),
  employeeId: z.string().optional().default("unknown-employee-id"),
  added: z.number().int().nonnegative(),
  deleted: z.number().int().nonnegative(),
  net: z.number().int(),
  files: z.number().int().nonnegative(),
  byLanguage: z.record(
    z.object({
      added: z.number().int().nonnegative(),
      deleted: z.number().int().nonnegative(),
      net: z.number().int(),
      files: z.number().int().nonnegative(),
    }),
  ),
});

const batchSchema = z.object({
  events: z.array(usageEventSchema).min(1).max(500),
});

app.get("/health", (_req, res) => {
  res.json({ ok: true });
});

app.use("/api", (req, res, next) => {
  const token = req.headers.authorization?.replace(/^Bearer\s+/i, "");
  if (token !== apiKey) {
    return res.status(401).json({ error: { message: "Unauthorized", type: "auth_error" } });
  }

  next();
});

app.post("/api/usage/events", (req, res) => {
  const parsed = batchSchema.safeParse(req.body);
  if (!parsed.success) {
    return res.status(400).json({
      error: {
        message: parsed.error.message,
        type: "invalid_request_error",
      },
    });
  }

  const result = insertEvents(parsed.data.events);
  res.json({
    ok: true,
    inserted: result.inserted,
    duplicates: result.duplicates,
  });
});

app.get("/api/usage/summary", (req, res) => {
  res.json({
    metric: "ai_code_diff_lines",
    generatedAt: new Date().toISOString(),
    total: queryTotal(req.query),
    byDate: queryGroup("date(ts)", req.query),
    byClient: queryGroup("client_id", req.query),
    byProject: queryGroup("project", req.query),
    byUser: queryGroup("user_name", req.query),
    byEmployeeId: queryGroup("employee_id", req.query),
    byLanguage: queryLanguage(req.query),
  });
});

app.get("/api/usage/events", (req, res) => {
  const limit = Math.min(parsePositiveInt(req.query.limit, 100), 1000);
  const where = whereClause(req.query);
  const rows = db
    .prepare(
      `select event_id as eventId,
              ts,
              event_type as eventType,
              client_id as clientId,
              project,
              user_name as user,
              employee_id as employeeId,
              session_id as sessionId,
              added,
              deleted,
              net,
              files,
              by_language as byLanguage
         from usage_events
        ${where.sql}
        order by ts desc
        limit ?`,
    )
    .all(...where.params, limit)
    .map((row) => ({
      ...row,
      byLanguage: JSON.parse(row.byLanguage || "{}"),
    }));

  res.json({ events: rows });
});

app.listen(port, host, () => {
  console.log(`AI usage server listening on http://${host}:${port}`);
});

function initDb() {
  db.exec(`
    create table if not exists usage_events (
      event_id text primary key,
      ts text not null,
      received_at text not null,
      event_type text not null,
      metric text not null,
      client_id text not null,
      project text not null,
      user_name text not null,
      employee_id text not null default 'unknown-employee-id',
      session_id text not null,
      added integer not null,
      deleted integer not null,
      net integer not null,
      files integer not null,
      by_language text not null,
      raw_json text not null
    );

    create index if not exists idx_usage_events_ts on usage_events(ts);
    create index if not exists idx_usage_events_client on usage_events(client_id);
    create index if not exists idx_usage_events_project on usage_events(project);
    create index if not exists idx_usage_events_user on usage_events(user_name);
    create index if not exists idx_usage_events_employee_id on usage_events(employee_id);
    create index if not exists idx_usage_events_session on usage_events(session_id);
  `);

  ensureColumn("usage_events", "employee_id", "text not null default 'unknown-employee-id'");
}

function insertEvents(events) {
  const insert = db.prepare(`
    insert or ignore into usage_events (
      event_id,
      ts,
      received_at,
      event_type,
      metric,
      client_id,
      project,
      user_name,
      employee_id,
      session_id,
      added,
      deleted,
      net,
      files,
      by_language,
      raw_json
    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  let inserted = 0;
  const tx = db.transaction((items) => {
    for (const event of items) {
      const info = insert.run(
        event.eventId,
        event.ts,
        new Date().toISOString(),
        event.eventType || "unknown",
        event.metric,
        event.clientId,
        event.project,
        event.user,
        event.employeeId || "unknown-employee-id",
        event.sessionId,
        event.added,
        event.deleted,
        event.net,
        event.files,
        JSON.stringify(event.byLanguage),
        JSON.stringify(event),
      );

      inserted += info.changes;
    }
  });

  tx(events);
  return { inserted, duplicates: events.length - inserted };
}

function queryTotal(filters) {
  const where = whereClause(filters);
  return normalizeTotals(
    db
      .prepare(
        `select coalesce(sum(added), 0) as added,
                coalesce(sum(deleted), 0) as deleted,
                coalesce(sum(net), 0) as net,
                coalesce(sum(files), 0) as files
           from usage_events
          ${where.sql}`,
      )
      .get(...where.params),
  );
}

function queryGroup(column, filters) {
  const where = whereClause(filters);
  const rows = db
    .prepare(
      `select ${column} as key,
              coalesce(sum(added), 0) as added,
              coalesce(sum(deleted), 0) as deleted,
              coalesce(sum(net), 0) as net,
              coalesce(sum(files), 0) as files
         from usage_events
        ${where.sql}
        group by ${column}
        order by key asc`,
    )
    .all(...where.params);

  return Object.fromEntries(rows.map((row) => [row.key, normalizeTotals(row)]));
}

function queryLanguage(filters) {
  const where = whereClause(filters);
  const rows = db
    .prepare(`select by_language as byLanguage from usage_events ${where.sql}`)
    .all(...where.params);
  const result = {};

  for (const row of rows) {
    const byLanguage = JSON.parse(row.byLanguage || "{}");
    for (const [language, totals] of Object.entries(byLanguage)) {
      result[language] ??= { added: 0, deleted: 0, net: 0, files: 0 };
      result[language].added += totals.added || 0;
      result[language].deleted += totals.deleted || 0;
      result[language].net += totals.net || 0;
      result[language].files += totals.files || 0;
    }
  }

  return result;
}

function whereClause(filters) {
  const clauses = [];
  const params = [];

  addFilter("client_id", filters.clientId);
  addFilter("project", filters.project);
  addFilter("user_name", filters.user);
  addFilter("employee_id", filters.employeeId);
  addFilter("session_id", filters.sessionId);

  if (filters.from) {
    clauses.push("ts >= ?");
    params.push(String(filters.from));
  }

  if (filters.to) {
    clauses.push("ts <= ?");
    params.push(String(filters.to));
  }

  return {
    sql: clauses.length ? `where ${clauses.join(" and ")}` : "",
    params,
  };

  function addFilter(column, value) {
    if (!value) return;
    clauses.push(`${column} = ?`);
    params.push(String(value));
  }
}

function ensureColumn(table, column, definition) {
  const columns = db.prepare(`pragma table_info(${table})`).all();
  if (columns.some((item) => item.name === column)) return;

  db.exec(`alter table ${table} add column ${column} ${definition}`);
}

function normalizeTotals(row) {
  return {
    added: Number(row?.added || 0),
    deleted: Number(row?.deleted || 0),
    net: Number(row?.net || 0),
    files: Number(row?.files || 0),
  };
}

function parsePositiveInt(value, fallback) {
  const parsed = Number.parseInt(value || "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}
