#!/usr/bin/env node

const { existsSync } = require("node:fs");
const { join } = require("node:path");
const { runPython } = require("./python-support");

const root = join(__dirname, "..");
const script = join(root, "scripts", "agent_contracts_mcp.py");

if (!existsSync(script)) {
  console.error(`agent-contracts-mcp could not find MCP server at ${script}`);
  process.exit(1);
}

process.exit(runPython([script], { toolName: "agent-contracts-mcp" }));
