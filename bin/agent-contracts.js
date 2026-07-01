#!/usr/bin/env node

const { existsSync } = require("node:fs");
const { join } = require("node:path");
const { runPython } = require("./python-support");

const root = join(__dirname, "..");
const script = join(root, "scripts", "agent_contracts.py");
const args = process.argv.slice(2);

if (!existsSync(script)) {
  console.error(`agent-contracts could not find analyzer script at ${script}`);
  process.exit(1);
}

process.exit(runPython([script, ...args], { toolName: "agent-contracts" }));
