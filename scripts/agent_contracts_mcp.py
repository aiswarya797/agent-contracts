#!/usr/bin/env python3
"""MCP adapter for the local agent-contracts progressive context primitives."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, BinaryIO

try:
    from scripts import agent_contracts
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import agent_contracts  # type: ignore[no-redef]


MCP_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_MAX_FILES = 80
DEFAULT_MAX_BYTES = 700_000


def package_version() -> str:
    package_path = Path(__file__).resolve().parents[1] / "package.json"
    try:
        data = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    version = data.get("version")
    return version if isinstance(version, str) else "unknown"


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "context_discover",
            "description": "Return the compact progressive context module catalog for a repository.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "default": "."},
                    "format": {"type": "string", "enum": ["json"], "default": "json"},
                },
            },
        },
        {
            "name": "context_read",
            "description": "Read one progressive context section for a target module.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "default": "."},
                    "target": {"type": "string"},
                    "section": {
                        "type": "string",
                        "enum": sorted(agent_contracts.CONTEXT_READ_SECTIONS),
                    },
                },
                "required": ["target", "section"],
            },
        },
        {
            "name": "context_pack",
            "description": "Build a bounded context pack and return its selected file manifest.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "default": "."},
                    "task": {"type": "string"},
                    "max_files": {"type": "integer", "minimum": 1, "default": DEFAULT_MAX_FILES},
                    "max_bytes": {"type": "integer", "minimum": 1, "default": DEFAULT_MAX_BYTES},
                },
                "required": ["task"],
            },
        },
        {
            "name": "context_intent",
            "description": "Classify a task and recommend the next progressive context tools.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                },
                "required": ["task"],
            },
        },
        {
            "name": "context_localize",
            "description": "Rank issue-specific modules, files, and bounded line regions.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "default": "."},
                    "task": {"type": "string"},
                    "max_candidate_files": {"type": "integer", "minimum": 1, "default": 24},
                    "max_regions": {"type": "integer", "minimum": 1, "default": 12},
                    "line_window": {"type": "integer", "minimum": 1, "default": 80},
                    "max_bytes": {"type": "integer", "minimum": 1, "default": DEFAULT_MAX_BYTES},
                    "model_profile": {"type": "string", "enum": list(agent_contracts.MODEL_PROFILE_NAMES), "default": "unknown"},
                },
                "required": ["task"],
            },
        },
        {
            "name": "context_read_region",
            "description": "Read a bounded repository file window by path and line range.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "default": "."},
                    "path": {"type": "string"},
                    "start": {"type": "integer", "minimum": 1},
                    "end": {"type": "integer", "minimum": 1},
                    "context_lines": {"type": "integer", "minimum": 0, "default": 0},
                },
                "required": ["path", "start", "end"],
            },
        },
        {
            "name": "context_expand",
            "description": "Expand from a localized file or region to related tests, imports, and module neighbors.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "default": "."},
                    "path": {"type": "string"},
                    "start": {"type": "integer", "minimum": 1},
                    "end": {"type": "integer", "minimum": 1},
                    "reason": {"type": "string", "default": ""},
                },
                "required": ["path"],
            },
        },
        {
            "name": "context_gate",
            "description": "Explain whether localized context is safe to inject, advisory only, tool-only, or should abstain.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "default": "."},
                    "task": {"type": "string"},
                    "model_profile": {"type": "string", "enum": list(agent_contracts.MODEL_PROFILE_NAMES), "default": "unknown"},
                    "max_bytes": {"type": "integer", "minimum": 1, "default": DEFAULT_MAX_BYTES},
                },
                "required": ["task"],
            },
        },
        {
            "name": "context_pack_v2",
            "description": "Build an issue-localized, gate-aware schema v2 evidence pack.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "default": "."},
                    "task": {"type": "string"},
                    "model_profile": {"type": "string", "enum": list(agent_contracts.MODEL_PROFILE_NAMES), "default": "unknown"},
                    "max_regions": {"type": "integer", "minimum": 1, "default": 8},
                    "line_window": {"type": "integer", "minimum": 1, "default": 80},
                    "max_bytes": {"type": "integer", "minimum": 1, "default": DEFAULT_MAX_BYTES},
                    "output": {"type": "string"},
                },
                "required": ["task"],
            },
        },
        {
            "name": "context_explain",
            "description": "Return the audit trail for included and omitted localized context evidence.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "default": "."},
                    "task": {"type": "string"},
                    "model_profile": {"type": "string", "enum": list(agent_contracts.MODEL_PROFILE_NAMES), "default": "unknown"},
                },
                "required": ["task"],
            },
        },
        {
            "name": "context_verify",
            "description": "Run the deterministic context-selection verifier against a manifest.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "default": "."},
                    "manifest": {"type": "string"},
                    "max_files": {"type": "integer", "minimum": 1, "default": DEFAULT_MAX_FILES},
                    "max_bytes": {"type": "integer", "minimum": 1, "default": DEFAULT_MAX_BYTES},
                },
                "required": ["manifest"],
            },
        },
    ]


def require_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"`{key}` must be a non-empty string")
    return value.strip()


def optional_int(arguments: dict[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"`{key}` must be a positive integer")
    return value


def optional_nonnegative_int(arguments: dict[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"`{key}` must be a non-negative integer")
    return value


def repo_path(arguments: dict[str, Any]) -> Path:
    repo = arguments.get("repo", ".")
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("`repo` must be a non-empty string")
    return Path(repo).resolve()


def context_pack_summary(repo: Path, task: str, max_files: int, max_bytes: int) -> dict[str, Any]:
    plan = agent_contracts.plan_context_pack_files(repo, task, max_files=max_files, max_bytes=max_bytes)
    pack_path = agent_contracts.write_context_pack_from_plan(repo, task, plan, None, max_files, max_bytes)
    module = plan["module"]
    return {
        "product": agent_contracts.PRODUCT,
        "repo_root": repo.as_posix(),
        "task": task,
        "module": {"name": module.name, "root": module.root, "kind": module.kind},
        "pack_path": pack_path.as_posix(),
        "manifest_path": (pack_path / "manifest.json").as_posix(),
        "selected_files": [
            {
                "path": item.path,
                "role": item.role,
                "bytes": agent_contracts.verifier_file_size(repo, item.path),
            }
            for item in plan["files"]
        ],
        "selected_file_count": len(plan["files"]),
        "selected_bytes": plan["selected_bytes"],
        "omitted_files": plan["omitted_files"],
        "limits": {"max_files": max_files, "max_bytes": max_bytes},
    }


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    args = arguments or {}
    if not isinstance(args, dict):
        raise ValueError("tool arguments must be an object")

    if name == "context_discover":
        output_format = args.get("format", "json")
        if output_format != "json":
            raise ValueError("context_discover only supports JSON output")
        return agent_contracts.build_context_catalog(repo_path(args))

    if name == "context_read":
        repo = repo_path(args)
        target = require_string(args, "target")
        section = require_string(args, "section")
        if section not in agent_contracts.CONTEXT_READ_SECTIONS:
            raise ValueError(f"unknown context_read section: {section}")
        map_data = agent_contracts.build_module_map(repo)
        modules = agent_contracts.modules_from_map(map_data)
        module = agent_contracts.resolve_context_module(modules, target, allow_fallback=False)
        if module is None:
            raise ValueError(f"unknown module or target: {target}")
        return agent_contracts.build_context_read_payload(repo, module, modules, section)

    if name == "context_pack":
        repo = repo_path(args)
        task = require_string(args, "task")
        max_files = optional_int(args, "max_files", DEFAULT_MAX_FILES)
        max_bytes = optional_int(args, "max_bytes", DEFAULT_MAX_BYTES)
        return context_pack_summary(repo, task, max_files, max_bytes)

    if name == "context_intent":
        task = require_string(args, "task")
        return agent_contracts.context_intent_payload(task)

    if name == "context_localize":
        repo = repo_path(args)
        task = require_string(args, "task")
        return agent_contracts.localize_issue_context(
            repo,
            task,
            max_candidate_files=optional_int(args, "max_candidate_files", 24),
            max_regions=optional_int(args, "max_regions", 12),
            line_window=optional_int(args, "line_window", 80),
            max_bytes=optional_int(args, "max_bytes", DEFAULT_MAX_BYTES),
            model_profile=agent_contracts.model_profile_payload(args.get("model_profile", "unknown")),
        )

    if name == "context_read_region":
        repo = repo_path(args)
        path = require_string(args, "path")
        start = optional_int(args, "start", 1)
        end = optional_int(args, "end", start)
        if end < start:
            raise ValueError("`end` must be greater than or equal to `start`")
        context_lines = optional_nonnegative_int(args, "context_lines", 0)
        return agent_contracts.read_region_payload(repo, path, start, end, context_lines=context_lines)

    if name == "context_expand":
        repo = repo_path(args)
        path = require_string(args, "path")
        raw_start = args.get("start")
        raw_end = args.get("end")
        start = optional_int(args, "start", 1) if raw_start is not None else None
        end = optional_int(args, "end", start or 1) if raw_end is not None else None
        if (start is None) != (end is None):
            raise ValueError("`start` and `end` must be provided together")
        if start is not None and end is not None and end < start:
            raise ValueError("`end` must be greater than or equal to `start`")
        reason = args.get("reason", "")
        if not isinstance(reason, str):
            raise ValueError("`reason` must be a string")
        return agent_contracts.context_expand_payload(repo, path, start=start, end=end, reason=reason)

    if name == "context_gate":
        repo = repo_path(args)
        task = require_string(args, "task")
        return agent_contracts.context_gate_payload(
            repo,
            task,
            model_profile=args.get("model_profile", "unknown"),
            max_bytes=optional_int(args, "max_bytes", DEFAULT_MAX_BYTES),
        )

    if name == "context_pack_v2":
        repo = repo_path(args)
        task = require_string(args, "task")
        output_value = args.get("output")
        output = None
        if output_value is not None:
            if not isinstance(output_value, str) or not output_value.strip():
                raise ValueError("`output` must be a non-empty string")
            output = Path(output_value)
            if not output.is_absolute():
                output = repo / output
        return agent_contracts.build_context_pack_v2(
            repo,
            task,
            output.resolve() if output is not None else None,
            max_regions=optional_int(args, "max_regions", 8),
            max_bytes=optional_int(args, "max_bytes", DEFAULT_MAX_BYTES),
            line_window=optional_int(args, "line_window", 80),
            model_profile=args.get("model_profile", "unknown"),
        )

    if name == "context_explain":
        repo = repo_path(args)
        task = require_string(args, "task")
        return agent_contracts.context_explain_payload(repo, task, model_profile=args.get("model_profile", "unknown"))

    if name == "context_verify":
        repo = repo_path(args)
        manifest = Path(require_string(args, "manifest"))
        if not manifest.is_absolute():
            manifest = repo / manifest
        max_files = optional_int(args, "max_files", DEFAULT_MAX_FILES)
        max_bytes = optional_int(args, "max_bytes", DEFAULT_MAX_BYTES)
        return agent_contracts.run_context_verification(manifest.resolve(), repo, max_files=max_files, max_bytes=max_bytes)

    raise ValueError(f"unknown tool: {name}")


def mcp_tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
        "structuredContent": payload,
    }


def response(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error_response(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    message_id = request.get("id")
    params = request.get("params") if isinstance(request.get("params"), dict) else {}

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return response(
            message_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-contracts", "version": package_version()},
            },
        )
    if method == "tools/list":
        return response(message_id, {"tools": tool_definitions()})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return error_response(message_id, -32602, "`name` must be a string")
        try:
            payload = call_tool(name, params.get("arguments") if isinstance(params, dict) else {})
        except Exception as exc:  # pragma: no cover - exercised through call_tool tests
            return response(message_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        return response(message_id, mcp_tool_result(payload))
    if message_id is None:
        return None
    return error_response(message_id, -32601, f"unknown method: {method}")


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if line == b"":
            return None
        if line in {b"\r\n", b"\n"}:
            break
        key, _, value = line.decode("ascii", errors="replace").partition(":")
        headers[key.lower()] = value.strip()
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError:
        return None
    if length <= 0:
        return None
    body = stream.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    stream.write(body)
    stream.flush()


def serve() -> int:
    while True:
        request = read_message(sys.stdin.buffer)
        if request is None:
            return 0
        reply = handle_request(request)
        if reply is not None:
            write_message(sys.stdout.buffer, reply)


if __name__ == "__main__":
    raise SystemExit(serve())
