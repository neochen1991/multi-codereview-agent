import { spawn as spawnChildProcess } from "node:child_process";
import pty from "node-pty";
import { normalizeTerminalText } from "./ansi.js";

export class NgAgentSession {
  constructor(config) {
    this.config = config;
    this.term = null;
    this.queue = Promise.resolve();
    this.bootOutput = "";
  }

  async ask(prompt) {
    const run = () => this.#askNow(prompt);
    const next = this.queue.then(run, run);
    this.queue = next.catch(() => {});
    return next;
  }

  async #askNow(prompt) {
    this.#ensureStarted();
    await this.#startupGrace();

    return await new Promise((resolve, reject) => {
      let raw = "";
      let settled = false;
      let sawNonEmptyOutput = false;

      const cleanup = () => {
        settled = true;
        clearTimeout(timer);
        disposable?.dispose?.();
      };

      const timer = setTimeout(() => {
        if (settled) return;
        cleanup();
        reject(new Error(`ngagent timed out after ${this.config.timeoutMs}ms`));
      }, this.config.timeoutMs);

      const disposable = this.term.onData((chunk) => {
        raw += chunk;
        if (raw.length > this.config.maxOutputChars) {
          cleanup();
          reject(new Error(`ngagent output exceeded ${this.config.maxOutputChars} characters`));
          return;
        }

        const normalized = normalizeTerminalText(raw);
        if (normalized.length > 0) sawNonEmptyOutput = true;

        if (sawNonEmptyOutput && this.config.readyPattern.test(normalized)) {
          cleanup();
          resolve(this.#extractAnswer(normalized));
        }
      });

      this.term.write(this.#toTerminalInput(prompt));
    });
  }

  #ensureStarted() {
    if (this.term) return;

    this.term = this.#spawnTerminal();

    this.term.onData((chunk) => {
      this.bootOutput += chunk;
      if (this.bootOutput.length > this.config.maxOutputChars) {
        this.bootOutput = this.bootOutput.slice(-this.config.maxOutputChars);
      }
    });

    this.term.onExit(({ exitCode }) => {
      this.term = null;
      this.bootOutput = "";
      console.error(`ngagent exited with code ${exitCode}`);
    });

    if (this.config.bootstrapInput) {
      setTimeout(() => {
        this.term?.write(this.#toTerminalInput(this.config.bootstrapInput));
      }, this.config.startupGraceMs);
    }
  }

  #startupGrace() {
    return new Promise((resolve) => setTimeout(resolve, this.config.startupGraceMs));
  }

  #spawnTerminal() {
    const spawnSpec = this.#commandSpec();

    if (this.config.sessionDriver === "pipe") {
      const child = spawnChildProcess(spawnSpec.command, spawnSpec.args, {
        cwd: process.cwd(),
        env: process.env,
        stdio: ["pipe", "pipe", "pipe"],
      });

      return {
        onData(callback) {
          const onStdout = (chunk) => callback(chunk.toString("utf8"));
          const onStderr = (chunk) => callback(chunk.toString("utf8"));
          child.stdout.on("data", onStdout);
          child.stderr.on("data", onStderr);
          return {
            dispose() {
              child.stdout.off("data", onStdout);
              child.stderr.off("data", onStderr);
            },
          };
        },
        onExit(callback) {
          child.on("exit", (exitCode) => callback({ exitCode }));
        },
        write(input) {
          child.stdin.write(input);
        },
        kill() {
          child.kill();
        },
      };
    }

    return pty.spawn(spawnSpec.command, spawnSpec.args, {
      name: "xterm-256color",
      cols: 140,
      rows: 40,
      cwd: process.cwd(),
      env: process.env,
    });
  }

  #commandSpec() {
    if (!this.config.useShell) {
      return {
        command: this.config.ngagentCommand,
        args: this.config.ngagentArgs,
      };
    }

    const commandLine = [this.config.ngagentCommand, ...this.config.ngagentArgs]
      .map((part) => quoteShellArg(part))
      .join(" ");

    if (process.platform === "win32") {
      return {
        command: "cmd.exe",
        args: ["/d", "/s", "/c", commandLine],
      };
    }

    return {
      command: "/bin/sh",
      args: ["-lc", commandLine],
    };
  }

  #toTerminalInput(text) {
    return `${String(text).replace(/\r?\n/g, "\n")}\r`;
  }

  #extractAnswer(normalized) {
    const withoutPrompt = normalized.replace(this.config.readyPattern, "").trim();
    return withoutPrompt || normalized;
  }

  dispose() {
    this.term?.kill();
    this.term = null;
  }
}

function quoteShellArg(value) {
  const raw = String(value);
  if (process.platform === "win32") {
    if (!/[\s"&|<>^]/.test(raw)) return raw;
    return `"${raw.replace(/"/g, '\\"')}"`;
  }

  if (!/[\s"'\\$`]/.test(raw)) return raw;
  return `'${raw.replace(/'/g, "'\\''")}'`;
}
