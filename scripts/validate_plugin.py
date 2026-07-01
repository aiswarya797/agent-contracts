#!/usr/bin/env python3
"""Validate the agent-contracts plugin checkout."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_FILES = [
    ".claude-plugin/plugin.json",
    ".npmignore",
    "README.md",
    "package.json",
    "bin/agent-contracts.js",
    "bin/agent-contracts-mcp.js",
    "assets/agent-contracts-wordmark-dark.svg",
    "assets/agent-contracts-wordmark-light.svg",
    "commands/contract-init.md",
    "commands/contract-map.md",
    "commands/contract-check.md",
    "commands/contract-refresh.md",
    "commands/contract-doctor.md",
    "commands/context-pack.md",
    "skills/contract-init/SKILL.md",
    "skills/contract-map/SKILL.md",
    "skills/contract-check/SKILL.md",
    "skills/contract-refresh/SKILL.md",
    "skills/contract-doctor/SKILL.md",
    "skills/context-pack/SKILL.md",
    "skills/shared/safety.md",
    "skills/shared/analyzer-contract.md",
    "skills/shared/generated-files.md",
    "skills/shared/script-resolution.md",
    "scripts/agent_contracts.py",
    "scripts/agent_contracts_mcp.py",
    "scripts/agent_trial_runner_template.py",
    "validation/context-selection/manifest.jsonl",
]

EXPECTED_WORKFLOWS = {
    "contract-init",
    "contract-map",
    "contract-check",
    "contract-refresh",
    "contract-doctor",
    "context-pack",
}

EXPECTED_CLI_COMMANDS = {
    "init",
    "map",
    "check",
    "refresh",
    "doctor",
    "context-discover",
    "context-read",
    "context-pack",
    "verify-context",
    "benchmark-context",
    "trial-context",
    "agent-trial-context",
}


def run(command: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def validate_required_files() -> None:
    for relative in REQUIRED_FILES:
        require((ROOT / relative).exists(), f"Missing required file: {relative}")


def validate_manifest() -> None:
    manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    require(manifest.get("name") == "agent-contracts", "Plugin name must be agent-contracts")
    require("version" in manifest, "Plugin manifest needs a version")
    require("description" in manifest, "Plugin manifest needs a description")


def validate_package() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    require(package.get("name") == "agent-contracts-cli", "npm package name must be agent-contracts-cli")
    require(package.get("bin", {}).get("agent-contracts") == "bin/agent-contracts.js", "package bin must expose agent-contracts")
    require(
        package.get("bin", {}).get("agent-contracts-mcp") == "bin/agent-contracts-mcp.js",
        "package bin must expose agent-contracts-mcp",
    )
    require(package.get("publishConfig", {}).get("access") == "public", "package must publish publicly")
    for path in [
        ".claude-plugin",
        "commands",
        "skills",
        "scripts/agent_contracts.py",
        "scripts/agent_contracts_mcp.py",
        "scripts/agent_trial_runner_template.py",
        "bin",
        "assets",
        "fixtures",
        "validation",
    ]:
        require(path in package.get("files", []), f"package files should include {path}")

    if shutil.which("node"):
        result = run(["node", "bin/agent-contracts.js", "--help"])
        require(result.returncode == 0, result.stderr)
        require("context-pack" in result.stdout, "bin help should proxy analyzer help")


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    require(text.startswith("---\n"), f"{path} needs frontmatter")
    end = text.find("\n---", 4)
    require(end != -1, f"{path} has unterminated frontmatter")
    data: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip()
    return data


def validate_markdown_entries() -> None:
    skill_names: set[str] = set()
    for path in sorted((ROOT / "skills").glob("*/SKILL.md")):
        data = frontmatter(path)
        require(data.get("name"), f"{path} needs a skill name")
        require(data.get("description"), f"{path} needs a skill description")
        skill_names.add(data["name"])

    command_names: set[str] = set()
    for path in sorted((ROOT / "commands").glob("*.md")):
        data = frontmatter(path)
        require(data.get("name"), f"{path} needs a command name")
        require(data.get("description"), f"{path} needs a command description")
        command_names.add(data["name"])

    require(command_names == EXPECTED_WORKFLOWS, f"Command set mismatch: {sorted(command_names)}")
    require(skill_names == EXPECTED_WORKFLOWS, f"Skill set mismatch: {sorted(skill_names)}")


def validate_readme() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for command in sorted(EXPECTED_CLI_COMMANDS):
        require(f"agent-contracts {command}" in text or command == "doctor", f"README should document agent-contracts {command}")
    require("agent-contracts doctor" in text, "README should document agent-contracts doctor")
    for phrase in ["Why This Is Needed", "What It Generates", "How To Install", "Commands"]:
        require(phrase in text, f"README should include {phrase}")
    require("Repository Layout" not in text, "README should not expose repository layout as a main section")
    require("Fixture Walkthrough" not in text, "README should not include fixture walkthrough")
    require("module-level" not in text.lower(), "README should avoid module-level phrasing")


def validate_gitignore() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    required_entries = [
        ".DS_Store",
        "__pycache__/",
        ".agent-contracts/cache/",
        ".agent-contracts/context-packs/",
        "/benchmark-results/",
        "/docs/",
        "/examples/generated/",
    ]
    for entry in required_entries:
        require(entry in text, f".gitignore should include {entry}")


def validate_generated_examples() -> None:
    marker = "Generated by agent-contracts"
    for path in sorted((ROOT / "examples" / "generated").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        require(marker in text or "drift report" in text.lower(), f"{path} should look like generated output")


def validate_cli() -> None:
    compile_result = run([sys.executable, "-m", "py_compile", "scripts/agent_contracts.py"])
    require(compile_result.returncode == 0, compile_result.stderr)
    mcp_compile_result = run([sys.executable, "-m", "py_compile", "scripts/agent_contracts_mcp.py"])
    require(mcp_compile_result.returncode == 0, mcp_compile_result.stderr)
    runner_compile_result = run([sys.executable, "-m", "py_compile", "scripts/agent_trial_runner_template.py"])
    require(runner_compile_result.returncode == 0, runner_compile_result.stderr)

    help_result = run([sys.executable, "scripts/agent_contracts.py", "--help"])
    require(help_result.returncode == 0, help_result.stderr)
    for command in sorted(EXPECTED_CLI_COMMANDS):
        require(command in help_result.stdout, f"CLI help should mention {command}")

    doctor = run([sys.executable, "scripts/agent_contracts.py", "doctor", "--repo", ".", "--format", "json"])
    require(doctor.returncode == 0, doctor.stderr or doctor.stdout)
    require(json.loads(doctor.stdout)["summary"]["blockers"] == 0, "doctor should report no blockers for plugin repo")

    fixtures = [
        "python-service",
        "typescript-app",
        "mixed-monorepo",
        "existing-docs",
        "ambiguous-modules",
        "noisy-context",
        "contract-guided-context",
    ]
    for fixture in fixtures:
        result = run([sys.executable, "scripts/agent_contracts.py", "map", "--repo", f"fixtures/{fixture}", "--format", "json"])
        require(result.returncode == 0, result.stderr)
        data = json.loads(result.stdout)
        require(data["summary"]["module_count"] >= 1, f"{fixture} should produce at least one module")
        if fixture == "python-service":
            billing = next(module for module in data["modules"] if module["name"] == "billing")
            require("tests/test_billing.py" in billing["test_files"], "billing should include its root-level test")
        if fixture == "mixed-monorepo":
            web = next(module for module in data["modules"] if module["name"] == "web")
            require("shared" in web["dependencies"], "web should depend on shared")
        if fixture == "ambiguous-modules":
            names = [module["name"] for module in data["modules"]]
            require(len(names) == len(set(names)), "ambiguous modules should receive unique names")

    drift = run([sys.executable, "scripts/agent_contracts.py", "check", "--repo", "fixtures/broken-drift", "--format", "json"])
    require(drift.returncode == 0, drift.stderr)
    codes = {item["code"] for item in json.loads(drift.stdout)["findings"]}
    require("internal-import" in codes, "broken-drift should report an internal import")
    require("undeclared-dependency" in codes, "broken-drift should report an undeclared dependency")
    require("undeclared-public-surface" in codes, "broken-drift should report an undeclared public surface")


def main() -> int:
    validate_required_files()
    validate_manifest()
    validate_package()
    validate_markdown_entries()
    validate_readme()
    validate_gitignore()
    validate_generated_examples()
    validate_cli()
    print("agent-contracts validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
