import readline from "node:readline";

process.stdout.write("ngagent mock ready\nngagent> ");

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: true,
});

rl.on("line", (line) => {
  process.stdout.write(`mock answer: ${line.trim()}\nngagent> `);
});
