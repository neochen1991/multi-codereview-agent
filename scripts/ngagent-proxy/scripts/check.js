import { spawnSync } from "node:child_process";

const files = [
  "src/server.js",
  "src/ngagent-session.js",
  "src/config.js",
  "src/prompt.js",
  "src/ansi.js",
  "scripts/smoke.js",
  "scripts/mock-ngagent.js",
];

for (const file of files) {
  const result = spawnSync(process.execPath, ["--check", file], {
    stdio: "inherit",
    shell: false,
  });

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

console.log("check ok");
