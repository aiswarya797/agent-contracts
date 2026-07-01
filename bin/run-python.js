#!/usr/bin/env node

const { runPython } = require("./python-support");

process.exit(runPython(process.argv.slice(2), { toolName: "agent-contracts" }));
