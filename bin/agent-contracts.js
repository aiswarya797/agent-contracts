#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const { existsSync } = require("node:fs");
const { join } = require("node:path");

const root = join(__dirname, "..");
const script = join(root, "scripts", "agent_contracts.py");
const args = process.argv.slice(2);

if (!existsSync(script)) {
  console.error(`agent-contracts could not find analyzer script at ${script}`);
  process.exit(1);
}

const candidates =
  process.platform === "win32"
    ? [
        ["py", ["-3", script]],
        ["python3", [script]],
        ["python", [script]],
      ]
    : [
        ["python3", [script]],
        ["python", [script]],
      ];

for (const [command, baseArgs] of candidates) {
  const result = spawnSync(command, [...baseArgs, ...args], { stdio: "inherit" });
  if (result.error && result.error.code === "ENOENT") {
    continue;
  }
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

console.error("agent-contracts requires Python 3.10+ on PATH.");
process.exit(1);
