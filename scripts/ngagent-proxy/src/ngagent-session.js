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

    this.term = pty.spawn(this.config.ngagentCommand, this.config.ngagentArgs, {
      name: "xterm-256color",
      cols: 140,
      rows: 40,
      cwd: process.cwd(),
      env: process.env,
    });

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
