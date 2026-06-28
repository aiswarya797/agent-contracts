# Security

agent-contracts is designed for private enterprise repositories.

## Execution Model

- The analyzer reads text files and metadata.
- It does not execute target repository code.
- It does not install dependencies.
- It does not run tests unless a user explicitly asks.
- It excludes dependency, vendor, build, generated, and cache directories from analysis.

## Reporting Issues

If you find a safety issue, document:
- The command you ran.
- The repository layout needed to reproduce it.
- The generated file or drift finding involved.
- The expected safer behavior.

Avoid sharing private source when reporting an issue.
