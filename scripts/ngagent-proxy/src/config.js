function intFromEnv(name, fallback) {
  const raw = process.env[name];
  if (!raw) return fallback;

  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return parsed;
}

function splitArgs(raw) {
  if (!raw || !raw.trim()) return [];
  return raw.match(/(?:[^\s"]+|"[^"]*")+/g)?.map((part) => part.replace(/^"|"$/g, "")) ?? [];
}

export const config = {
  host: process.env.HOST || "127.0.0.1",
  port: intFromEnv("PORT", 8787),
  localProxyKey: process.env.LOCAL_PROXY_KEY || "local-dev-key",
  ngagentCommand: process.env.NGAGENT_COMMAND || "ngagent",
  ngagentArgs: splitArgs(process.env.NGAGENT_ARGS),
  readyPattern: new RegExp(process.env.NGAGENT_READY_PATTERN || "(^|\\n|\\r)(>|ngagent>|❯)\\s*$", "m"),
  bootstrapInput: process.env.NGAGENT_BOOTSTRAP_INPUT || "",
  timeoutMs: intFromEnv("NGAGENT_TIMEOUT_MS", 180000),
  startupGraceMs: intFromEnv("NGAGENT_STARTUP_GRACE_MS", 1500),
  maxOutputChars: intFromEnv("NGAGENT_MAX_OUTPUT_CHARS", 200000),
};
