#!/usr/bin/env python3
"""SWE-Explore adapter for agent-contracts context selection.

This bridge reads public SWE-Explore benchmark rows, resolves base repository
snapshots, selects context with agent-contracts primitives, and emits ranked
file/line regions instead of full files.
"""

from __future__ import annotations

import argparse
import __future__
import dataclasses
import json
import math
import re
import shlex
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

try:  # Importable both as `python scripts/...` and from unittest.
    from scripts import agent_contracts
except ImportError:  # pragma: no cover - direct script execution path
    import agent_contracts  # type: ignore[no-redef]


STATIC_STRATEGIES = ("module", "graph-like", "progressive-mcp", "contract-ranked", "context-localized", "beat-sota1")
AGENT_STRATEGIES = (
    "codex-baseline",
    "codex-agent-contracts",
    "codex-context-localized",
    "codex-beat-sota1",
    "codex-beat-sota2",
)
STRATEGIES = (*STATIC_STRATEGIES, *AGENT_STRATEGIES)
DEFAULT_MAX_FILES = 80
DEFAULT_MAX_BYTES = 700_000
DEFAULT_CODEX_COMMAND = (
    "codex exec --sandbox read-only --output-last-message {response_file} -C {repo} -"
)
DEFAULT_CODEX_TIMEOUT = 900
DEFAULT_BEAT_SOTA1_REGIONS_PER_FILE = 3
DEFAULT_BEAT_SOTA1_EXPANSION_ROUNDS = 2
DEFAULT_BEAT_SOTA1_PRECONTEXT_CANDIDATES = 12
DEFAULT_BEAT_SOTA1_PRECONTEXT_MAX_CHARS = 24_000
DEFAULT_BEAT_SOTA1_PAIR_MATCH_LIMIT = 5
DEFAULT_BEAT_SOTA2_PRECONTEXT_CANDIDATES = 6
DEFAULT_BEAT_SOTA2_PRECONTEXT_MAX_CHARS = 12_000
BEAT_SOTA1_ABLATIONS = (
    "no-contracts",
    "no-graph",
    "no-symbols",
    "no-tests",
    "no-active",
    "no-codex",
)
SELECTABLE_ROLES = {
    "source",
    "test",
    "contract",
    "agent-instructions",
    "architecture",
    "docs",
    "manifest",
}
ROLE_PRIORITY = {
    "source": 0,
    "test": 1,
    "contract": 2,
    "agent-instructions": 3,
    "architecture": 4,
    "docs": 5,
    "manifest": 6,
}
ROLE_REGION_BASE_SCORE = {
    "source": 120,
    "test": 100,
    "contract": 40,
    "agent-instructions": 35,
    "architecture": 30,
    "docs": 20,
    "manifest": 15,
}
SWE_METRICS = [
    "precision",
    "recall",
    "f1_score",
    "hit_file_rate",
    "noise_file_rate",
    "hit_region_rate",
    "noise_region_rate",
    "weighted_core_coverage",
    "context_efficiency",
    "optional_coverage",
    "ndcg_at_100",
    "ndcg_at_300",
    "ndcg_at_500",
    "recall_at_100",
    "recall_at_300",
    "recall_at_500",
    "first_useful_hit",
]
STOP_WORDS = agent_contracts.CONTEXT_TARGET_STOP_WORDS | {
    "able",
    "actual",
    "after",
    "all",
    "also",
    "are",
    "because",
    "been",
    "before",
    "being",
    "bug",
    "but",
    "can",
    "case",
    "does",
    "doesn",
    "during",
    "each",
    "expected",
    "from",
    "get",
    "have",
    "how",
    "into",
    "issue",
    "make",
    "more",
    "not",
    "only",
    "please",
    "should",
    "than",
    "that",
    "this",
    "use",
    "using",
    "when",
    "where",
    "while",
    "will",
    "would",
}
TOKEN_RE = re.compile(r"[a-z0-9]+")
SYMBOL_RE = re.compile(
    r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"|^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var|interface|type)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    r"|^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=",
    re.M,
)
PYTHON_SYMBOL_RE = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
PATH_LIKE_RE = re.compile(r"\b(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]+\b")
QUOTED_TERM_RE = re.compile(r"['\"]([^'\"\n]{3,120})['\"]")
TRACEBACK_LINE_RE = re.compile(r'File "([^"\n]+)", line (\d+)')
MINIFIED_RE = re.compile(r"\.min\.(?:js|css)$")
TFIDF_TOKEN_RE = re.compile(r"(?u)\b[A-Za-z_][A-Za-z0-9_]{1,}\b")
TFIDF_CHUNK_EXTS = {".py", ".md", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".rst"}
SOTA2_CODE_PRECONTEXT_EXTS = {
    ".c",
    ".cc",
    ".cpp",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".py",
    ".pyi",
    ".rb",
    ".rs",
    ".ts",
    ".tsx",
}
SOTA2_NOISY_PRECONTEXT_PARTS = {
    ".github",
    "_static",
    "build",
    "changelog",
    "changes",
    "dist",
    "doc",
    "docs",
    "example",
    "examples",
    "fixtures",
    "roots",
    "static",
    "themes",
    "vendor",
    "vendors",
}
SOTA2_NOISY_PRECONTEXT_NAMES = {"__init__.py", "conf.py"}
SOTA2_GENERIC_EVIDENCE_TOKENS = {
    "api",
    "build",
    "config",
    "conf",
    "doc",
    "docs",
    "ext",
    "extensions",
    "html",
    "index",
    "lint",
    "py",
    "pylint",
    "pytest",
    "python",
    "python3",
    "request",
    "requests",
    "sphinx",
    "src",
    "test",
    "tests",
}


@dataclasses.dataclass
class RankedFile:
    path: str
    role: str
    score: int
    reasons: list[str]
    module_name: str | None
    matched_tokens: list[str]


@dataclasses.dataclass
class RankedRegion:
    path: str
    start: int
    end: int
    score: int
    file_score: int
    window_score: int
    reasons: list[str]
    matched_tokens: list[str]


@dataclasses.dataclass
class QuerySignals:
    text: str
    tokens: set[str]
    path_hints: set[str]
    quoted_terms: list[str]
    traceback_lines: dict[str, set[int]]
    symbol_terms: set[str]


@dataclasses.dataclass
class EvidenceSymbol:
    name: str
    kind: str
    start: int
    end: int
    tokens: set[str]


@dataclasses.dataclass
class EvidenceFile:
    path: str
    role: str
    module_name: str | None
    tokens: set[str]
    content_tokens: set[str]
    symbols: list[EvidenceSymbol]
    imports: set[str]
    imported_by: set[str]
    paired_paths: set[str]
    line_count: int
    bytes: int
    generated: bool


@dataclasses.dataclass
class BeatSOTA1Config:
    max_files: int
    max_bytes: int
    top_k: int
    line_window: int
    line_overlap: int
    regions_per_file: int
    expansion_rounds: int
    ablations: set[str]

    def not_ablated(self, ablation_name: str) -> bool:
        return ablation_name not in self.ablations


@dataclasses.dataclass
class BeatSOTA1CandidateFile:
    path: str
    role: str
    score: int
    reasons: list[str]
    matched_tokens: list[str]
    module_name: str | None
    cost_bytes: int


@dataclasses.dataclass
class RetrievalChunk:
    path: str
    start: int
    end: int
    content: str
    tokens: list[str]


def split_identifier_text(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = value.replace("_", " ").replace("-", " ")
    return value.lower()


def tokenize(value: str) -> list[str]:
    tokens = []
    for token in TOKEN_RE.findall(split_identifier_text(value)):
        if len(token) > 1 and token not in STOP_WORDS:
            tokens.append(token)
    return tokens


def token_set(value: str) -> set[str]:
    return set(tokenize(value))


def tfidf_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for token in TFIDF_TOKEN_RE.findall(value):
        lowered = token.lower()
        if len(lowered) > 1 and lowered not in STOP_WORDS:
            tokens.append(lowered)
    return tokens


def iter_tfidf_chunks(
    repo: Path,
    *,
    chunk_size: int,
    chunk_overlap: int,
    max_chunks: int,
) -> list[RetrievalChunk]:
    step = max(1, chunk_size - chunk_overlap)
    chunks: list[RetrievalChunk] = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TFIDF_CHUNK_EXTS:
            continue
        rel = path.relative_to(repo).as_posix()
        if agent_contracts.is_generated_path(rel) or MINIFIED_RE.search(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        if not lines:
            continue
        for index in range(0, len(lines), step):
            end_index = min(index + chunk_size, len(lines))
            content = "\n".join(lines[index:end_index])
            tokens = tfidf_tokens(content)
            if tokens:
                chunks.append(RetrievalChunk(path=rel, start=index + 1, end=end_index, content=content, tokens=tokens))
            if end_index >= len(lines) or len(chunks) >= max_chunks:
                break
        if len(chunks) >= max_chunks:
            break
    return chunks


def unique_ordered(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def fuzzy_token_matches(tokens: set[str], evidence_tokens: set[str]) -> list[str]:
    if not tokens or not evidence_tokens:
        return []
    matches = []
    exact = tokens & evidence_tokens
    buckets: dict[str, list[str]] | None = None
    if len(evidence_tokens) > 2_000:
        buckets = {}
        for candidate in evidence_tokens:
            buckets.setdefault(candidate[:1], []).append(candidate)
    for token in sorted(tokens):
        if token in exact:
            matches.append(token)
            continue
        if buckets is not None and len(token) < 4:
            continue
        candidates = buckets.get(token[:1], [])[:300] if buckets is not None else evidence_tokens
        for candidate in candidates:
            if candidate.startswith(token) or token.startswith(candidate):
                matches.append(token)
                break
    return matches


def safe_text(repo: Path, path: str, *, limit: int = 200_000) -> str:
    return agent_contracts.safe_read(repo / path, limit=limit)


def line_count(repo: Path, path: str) -> int:
    text = safe_text(repo, path, limit=5_000_000)
    if not text:
        return 0
    return len(text.splitlines())


def module_contract_text(repo: Path, module: agent_contracts.ModuleInfo) -> str:
    parts = []
    for path in [
        "ARCHITECTURE.md",
        "AGENTS.md",
        agent_contracts.module_contract_path(module),
        agent_contracts.module_agents_path(module),
    ]:
        if (repo / path).is_file():
            parts.append(safe_text(repo, path, limit=120_000))
    return "\n".join(parts)


def module_evidence_tokens(repo: Path, module: agent_contracts.ModuleInfo) -> set[str]:
    evidence = " ".join(
        [
            module.name,
            module.root,
            *module.package_names,
            *module.public_surfaces,
            *module.capabilities,
            *module.source_files,
            *module.test_files,
            *module.docs,
            *module.commands,
            *module.dependencies,
            module_contract_text(repo, module),
        ]
    )
    return token_set(evidence)


def score_module(
    repo: Path,
    module: agent_contracts.ModuleInfo,
    issue_text: str,
    issue_tokens: set[str],
) -> tuple[int, list[str], list[str]]:
    score = 0
    reasons: list[str] = []
    matched: list[str] = []

    target_score = agent_contracts.module_target_score(module, issue_text)
    if target_score:
        score += target_score
        reasons.append(f"module-target-score:{target_score}")

    identity_tokens = token_set(" ".join([module.name, module.root, *module.package_names]))
    identity_matches = fuzzy_token_matches(issue_tokens, identity_tokens)
    if identity_matches:
        score += 12 * len(identity_matches)
        reasons.append(f"module-identity:{','.join(identity_matches)}")
        matched.extend(identity_matches)

    surface_tokens = token_set(" ".join(module.public_surfaces))
    surface_matches = fuzzy_token_matches(issue_tokens, surface_tokens)
    if surface_matches:
        score += 10 * len(surface_matches)
        reasons.append(f"public-surface:{','.join(surface_matches)}")
        matched.extend(surface_matches)

    capability_tokens = token_set(" ".join(module.capabilities))
    capability_matches = fuzzy_token_matches(issue_tokens, capability_tokens)
    if capability_matches:
        score += 6 * len(capability_matches)
        reasons.append(f"capability:{','.join(capability_matches)}")
        matched.extend(capability_matches)

    contract_tokens = token_set(module_contract_text(repo, module))
    contract_matches = fuzzy_token_matches(issue_tokens, contract_tokens)
    if contract_matches:
        score += min(30, 3 * len(contract_matches))
        reasons.append(f"local-contract:{','.join(contract_matches[:8])}")
        matched.extend(contract_matches[:8])

    return score, unique_ordered(reasons), unique_ordered(matched)


def best_module_for_issue(
    repo: Path,
    modules: list[agent_contracts.ModuleInfo],
    issue_text: str,
    issue_tokens: set[str],
) -> tuple[agent_contracts.ModuleInfo | None, list[dict[str, Any]]]:
    scored = []
    for index, module in enumerate(modules):
        score, reasons, matched = score_module(repo, module, issue_text, issue_tokens)
        scored.append((score, index, module, reasons, matched))
    scored.sort(key=lambda item: (-item[0], item[1], item[2].root, item[2].name))
    trace = [
        {
            "module": module.name,
            "root": module.root,
            "score": score,
            "reasons": reasons,
            "matched_tokens": matched,
        }
        for score, _index, module, reasons, matched in scored[:8]
    ]
    if not scored:
        return None, trace
    if scored[0][0] <= 0:
        fallback = agent_contracts.resolve_context_module(modules, issue_text, allow_fallback=True)
        return fallback, trace
    return scored[0][2], trace


def module_for_path(modules: list[agent_contracts.ModuleInfo], path: str) -> agent_contracts.ModuleInfo | None:
    return agent_contracts.module_for_context_path(modules, path)


def import_evidence_by_path(
    repo: Path,
    files: list[agent_contracts.FileInfo],
    modules: list[agent_contracts.ModuleInfo],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    package_to_module = agent_contracts.module_alias_map(modules)
    outgoing: dict[str, set[str]] = {}
    incoming: dict[str, set[str]] = {}
    for edge in agent_contracts.graph_import_edges(repo, files, package_to_module):
        source = edge["source"]
        resolved = edge["resolved"]
        raw = edge["raw"]
        outgoing.setdefault(source, set()).update(tokenize(f"{raw} {resolved}"))
        incoming.setdefault(resolved, set()).update(tokenize(f"{raw} {source}"))
    return outgoing, incoming


def file_public_surfaces(repo: Path, file: agent_contracts.FileInfo) -> list[str]:
    if file.role != "source":
        return []
    return agent_contracts.extract_public_surfaces(repo, file)


def symbols_in_text(text: str) -> set[str]:
    symbols: set[str] = set()
    for groups in SYMBOL_RE.findall(text):
        for value in groups:
            if value:
                symbols.update(tokenize(value))
    return symbols


def extract_query_signals(issue_text: str) -> QuerySignals:
    path_hints = {
        agent_contracts.normalize_context_path(match)
        for match in PATH_LIKE_RE.findall(issue_text)
        if agent_contracts.normalize_context_path(match)
    }
    traceback_lines: dict[str, set[int]] = {}
    for path, line_value in TRACEBACK_LINE_RE.findall(issue_text):
        normalized = agent_contracts.normalize_context_path(path)
        if normalized:
            traceback_lines.setdefault(normalized, set()).add(int(line_value))
            path_hints.add(normalized)
    quoted_terms = unique_ordered(
        [term.strip() for term in QUOTED_TERM_RE.findall(issue_text) if term.strip()]
    )
    tokens = set(tokenize(issue_text))
    symbol_terms = {
        token
        for token in tokens
        if "_" in token or any(char.isdigit() for char in token)
    }
    symbol_terms.update(tokenize(" ".join(quoted_terms)))
    return QuerySignals(
        text=issue_text,
        tokens=tokens,
        path_hints=path_hints,
        quoted_terms=quoted_terms,
        traceback_lines=traceback_lines,
        symbol_terms=symbol_terms,
    )


def symbol_spans(text: str, *, max_window: int) -> list[EvidenceSymbol]:
    lines = text.splitlines()
    starts: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines, start=1):
        py_match = PYTHON_SYMBOL_RE.match(line)
        generic_match = SYMBOL_RE.match(line)
        value = None
        if py_match:
            value = py_match.group(1)
        elif generic_match:
            value = next((group for group in generic_match.groups() if group), None)
        if value:
            kind = "class" if re.match(r"^\s*(?:export\s+)?class\b", line) else "symbol"
            starts.append((index, value, kind))
    spans: list[EvidenceSymbol] = []
    for offset, (start, name, kind) in enumerate(starts):
        next_start = starts[offset + 1][0] if offset + 1 < len(starts) else len(lines) + 1
        end = min(next_start - 1, start + max_window - 1, len(lines))
        spans.append(
            EvidenceSymbol(
                name=name,
                kind=kind,
                start=start,
                end=max(start, end),
                tokens=set(tokenize(name)),
            )
        )
    return spans


def import_relationships_by_path(
    repo: Path,
    files: list[agent_contracts.FileInfo],
    modules: list[agent_contracts.ModuleInfo],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    package_to_module = agent_contracts.module_alias_map(modules)
    outgoing: dict[str, set[str]] = {}
    incoming: dict[str, set[str]] = {}
    for edge in agent_contracts.graph_import_edges(repo, files, package_to_module):
        source = edge["source"]
        resolved = edge["resolved"]
        if source and resolved:
            outgoing.setdefault(source, set()).add(resolved)
            incoming.setdefault(resolved, set()).add(source)
    return outgoing, incoming


def likely_paired_paths(files: list[agent_contracts.FileInfo], modules: list[agent_contracts.ModuleInfo]) -> dict[str, set[str]]:
    by_path = {file.path: file for file in files}
    pairs: dict[str, set[str]] = {}

    def add_pair(left: str, right: str) -> None:
        if left == right or left not in by_path or right not in by_path:
            return
        pairs.setdefault(left, set()).add(right)
        pairs.setdefault(right, set()).add(left)

    def pair_tokens(path: str, *, stem_only: bool = False) -> set[str]:
        value = Path(path).stem if stem_only else path
        tokens = set(tokenize(value))
        tokens.difference_update({"init", "test", "tests"})
        return tokens

    source_files = [file.path for file in files if file.role == "source"]
    source_tokens_by_path = {path: pair_tokens(path) for path in source_files}
    source_stem_tokens_by_path = {path: pair_tokens(path, stem_only=True) for path in source_files}
    source_index: dict[str, set[str]] = {}
    for source, tokens in source_tokens_by_path.items():
        for token in tokens:
            source_index.setdefault(token, set()).add(source)

    def ranked_source_matches(
        tokens: set[str],
        index: dict[str, set[str]],
        token_map: dict[str, set[str]],
        *,
        limit: int = DEFAULT_BEAT_SOTA1_PAIR_MATCH_LIMIT,
    ) -> list[str]:
        if not tokens:
            return []
        scores: dict[str, int] = {}
        for token in tokens:
            for source in index.get(token, ()):
                scores[source] = scores.get(source, 0) + 1
        ranked = sorted(
            scores,
            key=lambda source: (
                -scores[source],
                abs(len(token_map.get(source, set())) - len(tokens)),
                source,
            ),
        )
        return ranked[:limit]

    for module in modules:
        if len(module.source_files) == 1 and len(module.test_files) == 1:
            add_pair(module.source_files[0], module.test_files[0])
            continue
        module_sources = [source for source in module.source_files if source in by_path]
        if not module_sources:
            continue
        module_index: dict[str, set[str]] = {}
        module_source_tokens = {
            source: source_stem_tokens_by_path.get(source, pair_tokens(source, stem_only=True))
            for source in module_sources
        }
        for source, tokens in module_source_tokens.items():
            for token in tokens:
                module_index.setdefault(token, set()).add(source)
        for test in module.test_files:
            test_tokens = pair_tokens(test, stem_only=True)
            for source in ranked_source_matches(test_tokens, module_index, module_source_tokens):
                add_pair(source, test)

    for file in files:
        if file.role != "test":
            continue
        test_tokens = pair_tokens(file.path)
        for source in ranked_source_matches(test_tokens, source_index, source_tokens_by_path, limit=1):
            add_pair(file.path, source)
    return pairs


def build_evidence_index(repo: Path, *, line_window: int) -> tuple[dict[str, EvidenceFile], list[agent_contracts.ModuleInfo]]:
    files = agent_contracts.inventory(repo)
    map_data = agent_contracts.build_module_map(repo)
    modules = agent_contracts.modules_from_map(map_data)
    outgoing, incoming = import_relationships_by_path(repo, files, modules)
    paired = likely_paired_paths(files, modules)
    evidence: dict[str, EvidenceFile] = {}
    for file in files:
        if file.role not in SELECTABLE_ROLES:
            continue
        path = repo / file.path
        text = safe_text(repo, file.path, limit=5_000_000)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        module = module_for_path(modules, file.path)
        evidence[file.path] = EvidenceFile(
            path=file.path,
            role=file.role,
            module_name=module.name if module else None,
            tokens=token_set(file.path),
            content_tokens=token_set(text),
            symbols=symbol_spans(text, max_window=line_window),
            imports=outgoing.get(file.path, set()),
            imported_by=incoming.get(file.path, set()),
            paired_paths=paired.get(file.path, set()),
            line_count=len(text.splitlines()) if text else 0,
            bytes=size,
            generated=agent_contracts.is_generated_path(file.path) or bool(MINIFIED_RE.search(file.path)),
        )
    return evidence, modules


def build_content_idf(evidence: dict[str, EvidenceFile]) -> dict[str, float]:
    document_count = len(evidence)
    if not document_count:
        return {}
    document_frequency: dict[str, int] = {}
    for evidence_file in evidence.values():
        for token in evidence_file.content_tokens | evidence_file.tokens:
            document_frequency[token] = document_frequency.get(token, 0) + 1
    return {
        token: math.log((document_count + 1) / (frequency + 0.5)) + 1.0
        for token, frequency in document_frequency.items()
    }


def beat_sota1_score_file(
    repo: Path,
    evidence_file: EvidenceFile,
    modules_by_name: dict[str, agent_contracts.ModuleInfo],
    module_evidence_cache: dict[str, set[str]],
    content_idf: dict[str, float],
    best_module: agent_contracts.ModuleInfo | None,
    signals: QuerySignals,
    config: BeatSOTA1Config,
) -> BeatSOTA1CandidateFile | None:
    score = 0
    reasons: list[str] = []
    matched: list[str] = []

    if evidence_file.generated:
        score -= 40
        reasons.append("generated-or-minified-penalty")

    for hinted in signals.path_hints:
        if hinted == evidence_file.path or hinted.endswith(evidence_file.path) or evidence_file.path.endswith(hinted):
            score += 120
            reasons.append(f"exact-path:{hinted}")
        elif Path(hinted).name == Path(evidence_file.path).name:
            score += 40
            reasons.append(f"path-basename:{Path(hinted).name}")

    if evidence_file.path in signals.traceback_lines:
        score += 140
        reasons.append("traceback-path")

    path_matches = fuzzy_token_matches(signals.tokens, evidence_file.tokens)
    if path_matches:
        score += 10 * len(path_matches)
        reasons.append(f"path:{','.join(path_matches[:8])}")
        matched.extend(path_matches[:8])

    basename_matches = fuzzy_token_matches(signals.tokens, token_set(Path(evidence_file.path).stem))
    if basename_matches:
        score += 14 * len(basename_matches)
        reasons.append(f"basename:{','.join(basename_matches[:8])}")
        matched.extend(basename_matches[:8])

    direct_content_matches = sorted(
        signals.tokens & evidence_file.content_tokens,
        key=lambda token: (-content_idf.get(token, 0.0), token),
    )
    if direct_content_matches:
        content_weight = sum(content_idf.get(token, 1.0) for token in direct_content_matches[:12])
        score += min(90, int(6 * content_weight))
        reasons.append(f"idf-content:{','.join(direct_content_matches[:8])}")
        matched.extend(direct_content_matches[:8])
    fuzzy_content_matches = [
        token
        for token in fuzzy_token_matches(signals.tokens - set(direct_content_matches), evidence_file.content_tokens)
        if token not in direct_content_matches
    ]
    if fuzzy_content_matches:
        score += min(18, 2 * len(fuzzy_content_matches))
        reasons.append(f"fuzzy-content:{','.join(fuzzy_content_matches[:6])}")
        matched.extend(fuzzy_content_matches[:6])

    for term in signals.quoted_terms:
        term_tokens = set(tokenize(term))
        if term_tokens and term_tokens.issubset(evidence_file.content_tokens):
            score += 18
            reasons.append(f"quoted-term:{','.join(sorted(term_tokens)[:4])}")
            matched.extend(sorted(term_tokens)[:4])

    if config.not_ablated("no-symbols"):
        symbol_tokens = set().union(*(symbol.tokens for symbol in evidence_file.symbols)) if evidence_file.symbols else set()
        symbol_matches = fuzzy_token_matches(signals.tokens | signals.symbol_terms, symbol_tokens)
        if symbol_matches:
            score += 26 * len(symbol_matches)
            reasons.append(f"symbol:{','.join(symbol_matches[:8])}")
            matched.extend(symbol_matches[:8])

    if best_module and evidence_file.module_name:
        if evidence_file.module_name == best_module.name:
            score += 28 if evidence_file.role in {"source", "test"} else 8
            reasons.append(f"inside-best-module:{best_module.name}")
        elif config.not_ablated("no-graph"):
            file_module = modules_by_name.get(evidence_file.module_name)
            if file_module and evidence_file.module_name in best_module.dependencies:
                score += 16
                reasons.append(f"dependency-module:{best_module.name}->{evidence_file.module_name}")
            elif file_module and best_module.name in file_module.dependencies:
                score += 10
                reasons.append(f"reverse-dependency:{evidence_file.module_name}->{best_module.name}")

    if config.not_ablated("no-contracts") and evidence_file.module_name:
        module = modules_by_name.get(evidence_file.module_name)
        if module:
            evidence_tokens = module_evidence_cache.get(module.name)
            if evidence_tokens is None:
                evidence_tokens = module_evidence_tokens(repo, module)
                module_evidence_cache[module.name] = evidence_tokens
            contract_matches = fuzzy_token_matches(signals.tokens, evidence_tokens)
            if contract_matches:
                score += min(24, 2 * len(contract_matches))
                reasons.append(f"contract-evidence:{','.join(contract_matches[:8])}")
                matched.extend(contract_matches[:8])

    if config.not_ablated("no-graph"):
        graph_tokens = token_set(" ".join([*evidence_file.imports, *evidence_file.imported_by]))
        graph_matches = fuzzy_token_matches(signals.tokens, graph_tokens)
        if graph_matches:
            score += 7 * len(graph_matches)
            reasons.append(f"graph-edge:{','.join(graph_matches[:8])}")
            matched.extend(graph_matches[:8])
        if evidence_file.imports or evidence_file.imported_by:
            score += min(8, len(evidence_file.imports) + len(evidence_file.imported_by))
            reasons.append("connected-file")

    if config.not_ablated("no-tests") and evidence_file.role == "test":
        if matched or "test" in evidence_file.path.lower():
            score += 18
            reasons.append("test-evidence")

    if evidence_file.role in {"docs", "manifest"} and not any(
        reason.startswith(("exact-path:", "quoted-term:", "traceback-path")) for reason in reasons
    ):
        score -= 18
        reasons.append(f"{evidence_file.role}-penalty")

    if score <= 0:
        return None
    return BeatSOTA1CandidateFile(
        path=evidence_file.path,
        role=evidence_file.role,
        score=score,
        reasons=unique_ordered(reasons),
        matched_tokens=unique_ordered(matched),
        module_name=evidence_file.module_name,
        cost_bytes=evidence_file.bytes,
    )


def score_file(
    repo: Path,
    file: agent_contracts.FileInfo,
    file_module: agent_contracts.ModuleInfo | None,
    best_module: agent_contracts.ModuleInfo | None,
    modules_by_name: dict[str, agent_contracts.ModuleInfo],
    issue_tokens: set[str],
    outgoing_import_tokens: dict[str, set[str]],
    incoming_import_tokens: dict[str, set[str]],
) -> RankedFile | None:
    if file.role not in SELECTABLE_ROLES:
        return None

    score = 0
    evidence_score = 0
    reasons: list[str] = []
    matched: list[str] = []

    path_matches = fuzzy_token_matches(issue_tokens, token_set(file.path))
    if path_matches:
        points = 10 * len(path_matches)
        score += points
        evidence_score += points
        reasons.append(f"path:{','.join(path_matches)}")
        matched.extend(path_matches)

    basename_matches = fuzzy_token_matches(issue_tokens, token_set(Path(file.path).stem))
    if basename_matches:
        points = 8 * len(basename_matches)
        score += points
        evidence_score += points
        reasons.append(f"basename:{','.join(basename_matches)}")
        matched.extend(basename_matches)

    text = safe_text(repo, file.path)
    content_matches = fuzzy_token_matches(issue_tokens, token_set(text))
    if content_matches:
        points = min(30, 2 * len(content_matches))
        score += points
        evidence_score += points
        reasons.append(f"content:{','.join(content_matches[:8])}")
        matched.extend(content_matches[:8])

    symbol_matches = fuzzy_token_matches(issue_tokens, symbols_in_text(text))
    if symbol_matches:
        points = 14 * len(symbol_matches)
        score += points
        evidence_score += points
        reasons.append(f"symbol:{','.join(symbol_matches)}")
        matched.extend(symbol_matches)

    surface_matches = fuzzy_token_matches(issue_tokens, token_set(" ".join(file_public_surfaces(repo, file))))
    if surface_matches:
        points = 16 * len(surface_matches)
        score += points
        evidence_score += points
        reasons.append(f"public-surface:{','.join(surface_matches)}")
        matched.extend(surface_matches)

    import_matches = fuzzy_token_matches(
        issue_tokens,
        outgoing_import_tokens.get(file.path, set()) | incoming_import_tokens.get(file.path, set()),
    )
    if import_matches:
        points = 6 * len(import_matches)
        score += points
        evidence_score += points
        reasons.append(f"import-hint:{','.join(import_matches)}")
        matched.extend(import_matches)

    if best_module and file_module:
        if file_module.name == best_module.name:
            bonus = 24 if file.role in {"source", "test"} else 8
            score += bonus
            reasons.append(f"inside-best-module:{best_module.name}")
        elif file_module.name in best_module.dependencies:
            score += 14
            reasons.append(f"direct-dependency:{best_module.name}->{file_module.name}")
        elif best_module.name in file_module.dependencies:
            score += 8
            reasons.append(f"reverse-dependency:{file_module.name}->{best_module.name}")

        if file.path in best_module.test_files:
            score += 10
            reasons.append(f"module-test:{best_module.name}")

        contract_matches = fuzzy_token_matches(issue_tokens, module_evidence_tokens(repo, file_module))
        if contract_matches:
            points = min(18, 2 * len(contract_matches))
            score += points
            if evidence_score:
                reasons.append(f"module-contract-evidence:{','.join(contract_matches[:8])}")
            matched.extend(contract_matches[:8])

    if file.role == "test" and evidence_score:
        score += 8
        reasons.append("test-evidence")

    if Path(file.path).name in {"__init__.py", "__main__.py", "__version__.py"} and not evidence_score:
        score -= 12
        reasons.append("generic-module-file-penalty")
    if Path(file.path).name in {"setup.py", "setup.cfg", "pyproject.toml", "package.json"} and not evidence_score:
        score -= 8
        reasons.append("manifest-penalty")

    if score <= 0:
        return None
    return RankedFile(
        path=file.path,
        role=file.role,
        score=score,
        reasons=unique_ordered(reasons),
        module_name=file_module.name if file_module else None,
        matched_tokens=unique_ordered(matched),
    )


def apply_ranked_file_limits(
    repo: Path,
    ranked_files: list[RankedFile],
    *,
    max_files: int,
    max_bytes: int,
) -> tuple[list[RankedFile], list[dict[str, str]], int]:
    included: list[RankedFile] = []
    omitted: list[dict[str, str]] = []
    selected_bytes = 0
    for item in ranked_files:
        path = repo / item.path
        try:
            size = path.stat().st_size
        except OSError:
            omitted.append({"path": item.path, "reason": "unreadable"})
            continue
        if len(included) >= max_files:
            omitted.append({"path": item.path, "reason": "file limit"})
            continue
        if selected_bytes + size > max_bytes:
            omitted.append({"path": item.path, "reason": "byte limit"})
            continue
        included.append(item)
        selected_bytes += size
    return included, omitted, selected_bytes


def contract_ranked_context_files(
    repo: Path,
    issue_text: str,
    *,
    max_files: int,
    max_bytes: int,
) -> dict[str, Any]:
    files = agent_contracts.inventory(repo)
    file_by_path = {file.path: file for file in files}
    map_data = agent_contracts.build_module_map(repo)
    modules = agent_contracts.modules_from_map(map_data)
    modules_by_name = {module.name: module for module in modules}
    issue_tokens = set(tokenize(issue_text))
    best_module, module_trace = best_module_for_issue(repo, modules, issue_text, issue_tokens)
    outgoing_import_tokens, incoming_import_tokens = import_evidence_by_path(repo, files, modules)

    ranked: list[RankedFile] = []
    for file in files:
        file_module = module_for_path(modules, file.path)
        scored = score_file(
            repo,
            file,
            file_module,
            best_module,
            modules_by_name,
            issue_tokens,
            outgoing_import_tokens,
            incoming_import_tokens,
        )
        if scored:
            ranked.append(scored)

    if not ranked and best_module:
        for path in [*best_module.source_files, *best_module.test_files]:
            file = file_by_path.get(path)
            if file:
                ranked.append(
                    RankedFile(
                        path=path,
                        role=file.role,
                        score=1,
                        reasons=[f"fallback-best-module:{best_module.name}"],
                        module_name=best_module.name,
                        matched_tokens=[],
                    )
                )

    ranked.sort(
        key=lambda item: (
            -item.score,
            ROLE_PRIORITY.get(item.role, 9),
            item.path,
        )
    )
    included, omitted, selected_bytes = apply_ranked_file_limits(
        repo,
        ranked,
        max_files=max_files,
        max_bytes=max_bytes,
    )
    trace: list[dict[str, Any]] = [
        {
            "rank": 1,
            "strategy": "contract-ranked",
            "operation": "module_resolution",
            "selected_module": best_module.name if best_module else None,
            "top_modules": module_trace,
        }
    ]
    for index, item in enumerate(included, start=1):
        trace.append(
            {
                "rank": index,
                "strategy": "contract-ranked",
                "operation": "file_rank",
                "path": item.path,
                "role": item.role,
                "module": item.module_name,
                "score": item.score,
                "reasons": item.reasons,
                "matched_tokens": item.matched_tokens,
            }
        )
    return {
        "files": [agent_contracts.ContextFile(item.path, item.role) for item in included],
        "file_scores": {item.path: item for item in included},
        "included_files": [item.path for item in included],
        "omitted_files": omitted,
        "selected_bytes": selected_bytes,
        "resolved_module": best_module.name if best_module else None,
        "trace": trace,
    }


def apply_candidate_file_limits(
    repo: Path,
    candidates: list[BeatSOTA1CandidateFile],
    *,
    max_files: int,
    max_bytes: int,
) -> tuple[list[BeatSOTA1CandidateFile], list[dict[str, str]], int]:
    included: list[BeatSOTA1CandidateFile] = []
    omitted: list[dict[str, str]] = []
    selected_bytes = 0
    for item in candidates:
        try:
            size = (repo / item.path).stat().st_size
        except OSError:
            omitted.append({"path": item.path, "reason": "unreadable"})
            continue
        if len(included) >= max_files:
            omitted.append({"path": item.path, "reason": "file limit"})
            continue
        if selected_bytes + size > max_bytes:
            omitted.append({"path": item.path, "reason": "byte limit"})
            continue
        included.append(item)
        selected_bytes += size
    return included, omitted, selected_bytes


def beat_sota1_expand_candidates(
    candidates_by_path: dict[str, BeatSOTA1CandidateFile],
    evidence: dict[str, EvidenceFile],
    *,
    rounds: int,
    enabled_graph: bool,
    enabled_tests: bool,
) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    if rounds <= 0:
        return trace
    for round_index in range(1, rounds + 1):
        before = [item.path for item in sorted(candidates_by_path.values(), key=lambda item: (-item.score, item.path))[:8]]
        bonuses: dict[str, tuple[int, list[str]]] = {}
        for seed_path in before[:6]:
            seed = evidence.get(seed_path)
            if not seed:
                continue
            related: list[tuple[str, int, str]] = []
            if enabled_tests:
                related.extend((path, 22, f"paired-with:{seed_path}") for path in seed.paired_paths)
            if enabled_graph:
                related.extend((path, 14, f"imports-from:{seed_path}") for path in seed.imports)
                related.extend((path, 10, f"imported-by:{seed_path}") for path in seed.imported_by)
            for path, points, reason in related:
                if path not in candidates_by_path or path not in evidence:
                    continue
                current_points, current_reasons = bonuses.get(path, (0, []))
                bonuses[path] = (current_points + points, [*current_reasons, reason])
        for path, (points, reasons) in bonuses.items():
            candidate = candidates_by_path[path]
            candidate.score += points
            candidate.reasons = unique_ordered([*candidate.reasons, *reasons])
        after = [item.path for item in sorted(candidates_by_path.values(), key=lambda item: (-item.score, item.path))[:8]]
        trace.append(
            {
                "rank": round_index,
                "strategy": "beat-sota1",
                "operation": "active_expansion",
                "round": round_index,
                "top_before": before,
                "top_after": after,
                "bonus_paths": sorted(bonuses),
            }
        )
        if before == after:
            break
    return trace


def beat_sota1_context_files(
    repo: Path,
    issue_text: str,
    *,
    max_files: int,
    max_bytes: int,
    top_k: int,
    line_window: int,
    line_overlap: int,
    regions_per_file: int,
    expansion_rounds: int,
    ablations: set[str],
) -> dict[str, Any]:
    del line_overlap
    config = BeatSOTA1Config(
        max_files=max_files,
        max_bytes=max_bytes,
        top_k=top_k,
        line_window=line_window,
        line_overlap=0,
        regions_per_file=regions_per_file,
        expansion_rounds=expansion_rounds,
        ablations=ablations,
    )
    evidence, modules = build_evidence_index(repo, line_window=line_window)
    modules_by_name = {module.name: module for module in modules}
    content_idf = build_content_idf(evidence)
    signals = extract_query_signals(issue_text)
    best_module, module_trace = best_module_for_issue(repo, modules, issue_text, signals.tokens)
    candidates: list[BeatSOTA1CandidateFile] = []
    module_evidence_cache: dict[str, set[str]] = {}
    for evidence_file in evidence.values():
        candidate = beat_sota1_score_file(
            repo,
            evidence_file,
            modules_by_name,
            module_evidence_cache,
            content_idf,
            best_module,
            signals,
            config,
        )
        if candidate:
            candidates.append(candidate)

    if not candidates and best_module:
        for path in [*best_module.source_files, *best_module.test_files]:
            file = evidence.get(path)
            if file:
                candidates.append(
                    BeatSOTA1CandidateFile(
                        path=path,
                        role=file.role,
                        score=1,
                        reasons=[f"fallback-best-module:{best_module.name}"],
                        matched_tokens=[],
                        module_name=best_module.name,
                        cost_bytes=file.bytes,
                    )
                )

    candidates_by_path = {candidate.path: candidate for candidate in candidates}
    expansion_trace = []
    if config.not_ablated("no-active"):
        expansion_trace = beat_sota1_expand_candidates(
            candidates_by_path,
            evidence,
            rounds=expansion_rounds,
            enabled_graph=config.not_ablated("no-graph"),
            enabled_tests=config.not_ablated("no-tests"),
        )
    ranked = sorted(
        candidates_by_path.values(),
        key=lambda item: (
            -item.score,
            item.cost_bytes,
            ROLE_PRIORITY.get(item.role, 9),
            item.path,
        ),
    )
    included, omitted, selected_bytes = apply_candidate_file_limits(
        repo,
        ranked,
        max_files=max_files,
        max_bytes=max_bytes,
    )
    trace: list[dict[str, Any]] = [
        {
            "rank": 1,
            "strategy": "beat-sota1",
            "operation": "module_resolution",
            "selected_module": best_module.name if best_module else None,
            "top_modules": module_trace,
            "ablations": sorted(ablations),
        },
        *expansion_trace,
    ]
    for index, item in enumerate(included, start=1):
        trace.append(
            {
                "rank": index,
                "strategy": "beat-sota1",
                "operation": "file_rank",
                "path": item.path,
                "role": item.role,
                "module": item.module_name,
                "score": item.score,
                "cost_bytes": item.cost_bytes,
                "reasons": item.reasons,
                "matched_tokens": item.matched_tokens,
            }
        )
    return {
        "files": [agent_contracts.ContextFile(item.path, item.role) for item in included],
        "file_scores": {item.path: item for item in included},
        "included_files": [item.path for item in included],
        "omitted_files": omitted,
        "selected_bytes": selected_bytes,
        "resolved_module": best_module.name if best_module else None,
        "trace": trace,
        "beat_sota1": {
            "signals": {
                "tokens": sorted(signals.tokens),
                "path_hints": sorted(signals.path_hints),
                "quoted_terms": signals.quoted_terms,
                "traceback_lines": {path: sorted(lines) for path, lines in signals.traceback_lines.items()},
            },
            "candidate_files": [
                {
                    "path": item.path,
                    "role": item.role,
                    "score": item.score,
                    "cost_bytes": item.cost_bytes,
                    "reasons": item.reasons,
                    "matched_tokens": item.matched_tokens,
                }
                for item in included
            ],
            "config": {
                "regions_per_file": regions_per_file,
                "expansion_rounds": expansion_rounds,
                "ablations": sorted(ablations),
            },
        },
        "_beat_sota1_evidence": evidence,
        "_beat_sota1_modules": modules,
        "_beat_sota1_content_idf": content_idf,
    }


def windows_for_line_count(total_lines: int, *, line_window: int, line_overlap: int) -> list[tuple[int, int]]:
    if total_lines <= 0:
        return []
    if total_lines <= line_window:
        return [(1, total_lines)]
    step = max(1, line_window - line_overlap)
    windows = []
    start = 1
    while start <= total_lines:
        end = min(total_lines, start + line_window - 1)
        windows.append((start, end))
        if end >= total_lines:
            break
        start += step
    final_start = max(1, total_lines - line_window + 1)
    if windows[-1][0] != final_start:
        windows.append((final_start, total_lines))
    return unique_region_windows(windows)


def unique_region_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    seen: set[tuple[int, int]] = set()
    result: list[tuple[int, int]] = []
    for window in windows:
        if window in seen:
            continue
        seen.add(window)
        result.append(window)
    return result


def score_window(
    text: str,
    *,
    issue_tokens: set[str],
    file: agent_contracts.ContextFile,
    module: agent_contracts.ModuleInfo | None,
    repo: Path,
    content_idf: dict[str, float] | None = None,
    module_evidence_cache: dict[str, set[str]] | None = None,
) -> tuple[int, list[str], list[str]]:
    score = 0
    reasons: list[str] = []
    matched: list[str] = []

    window_tokens = token_set(text)
    if content_idf:
        direct_matches = sorted(
            issue_tokens & window_tokens,
            key=lambda token: (-content_idf.get(token, 0.0), token),
        )
        if direct_matches:
            weight = sum(content_idf.get(token, 1.0) for token in direct_matches[:10])
            score += min(70, int(7 * weight))
            reasons.append(f"window-idf:{','.join(direct_matches[:8])}")
            matched.extend(direct_matches[:8])
    content_matches = fuzzy_token_matches(issue_tokens, window_tokens)
    if content_matches:
        score += 5 * len(content_matches)
        reasons.append(f"window-token:{','.join(content_matches[:8])}")
        matched.extend(content_matches[:8])

    symbol_matches = fuzzy_token_matches(issue_tokens, symbols_in_text(text))
    if symbol_matches:
        score += 18 * len(symbol_matches)
        reasons.append(f"window-symbol:{','.join(symbol_matches)}")
        matched.extend(symbol_matches)

    if module:
        surface_matches = fuzzy_token_matches(issue_tokens, token_set(" ".join(module.public_surfaces)) & window_tokens)
        if surface_matches:
            score += 8 * len(surface_matches)
            reasons.append(f"near-public-surface:{','.join(surface_matches)}")
            matched.extend(surface_matches)

        if module_evidence_cache is not None:
            module_tokens = module_evidence_cache.get(module.name)
            if module_tokens is None:
                module_tokens = module_evidence_tokens(repo, module)
                module_evidence_cache[module.name] = module_tokens
        else:
            module_tokens = module_evidence_tokens(repo, module)
        contract_matches = fuzzy_token_matches(issue_tokens, module_tokens & window_tokens)
        if contract_matches:
            score += min(12, 2 * len(contract_matches))
            reasons.append(f"near-contract-term:{','.join(contract_matches[:6])}")
            matched.extend(contract_matches[:6])

    if file.role == "test" and re.search(r"^\s*(?:def\s+)?test[_A-Za-z0-9]*", text, re.M):
        score += 5
        reasons.append("test-window")

    return score, unique_ordered(reasons), unique_ordered(matched)


def best_region_for_file(
    repo: Path,
    context_file: agent_contracts.ContextFile,
    file_score: int,
    issue_tokens: set[str],
    modules: list[agent_contracts.ModuleInfo],
    *,
    line_window: int,
    line_overlap: int,
) -> RankedRegion | None:
    text = safe_text(repo, context_file.path, limit=5_000_000)
    if not text:
        return None
    lines = text.splitlines()
    module = module_for_path(modules, context_file.path)
    best: RankedRegion | None = None
    for start, end in windows_for_line_count(len(lines), line_window=line_window, line_overlap=line_overlap):
        window_text = "\n".join(lines[start - 1 : end])
        window_score, reasons, matched = score_window(
            window_text,
            issue_tokens=issue_tokens,
            file=context_file,
            module=module,
            repo=repo,
        )
        total_score = file_score * 3 + window_score
        region = RankedRegion(
            path=context_file.path,
            start=start,
            end=end,
            score=total_score,
            file_score=file_score,
            window_score=window_score,
            reasons=reasons if reasons else ["fallback-first-window"],
            matched_tokens=matched,
        )
        if best is None or (
            region.score,
            -region.start,
            -region.end,
        ) > (
            best.score,
            -best.start,
            -best.end,
        ):
            best = region
    if best and best.window_score <= 0:
        end = min(len(lines), line_window)
        return RankedRegion(
            path=context_file.path,
            start=1,
            end=end,
            score=file_score * 3,
            file_score=file_score,
            window_score=0,
            reasons=["fallback-first-window"],
            matched_tokens=[],
        )
    return best


def centered_window(line: int, total_lines: int, *, line_window: int) -> tuple[int, int]:
    half = max(0, line_window // 2)
    start = max(1, line - half)
    end = min(total_lines, start + line_window - 1)
    start = max(1, end - line_window + 1)
    return start, end


def candidate_windows_for_evidence_file(
    evidence_file: EvidenceFile,
    signals: QuerySignals,
    *,
    line_window: int,
    line_overlap: int,
) -> list[tuple[int, int, str]]:
    windows: list[tuple[int, int, str]] = []
    for line in sorted(signals.traceback_lines.get(evidence_file.path, set())):
        start, end = centered_window(line, evidence_file.line_count, line_window=line_window)
        windows.append((start, end, "traceback-line"))
    for symbol in evidence_file.symbols:
        if fuzzy_token_matches(signals.tokens | signals.symbol_terms, symbol.tokens):
            windows.append((symbol.start, min(symbol.end, symbol.start + line_window - 1), f"symbol:{symbol.name}"))
    for start, end in windows_for_line_count(
        evidence_file.line_count,
        line_window=line_window,
        line_overlap=line_overlap,
    ):
        windows.append((start, end, "sliding-window"))

    seen: set[tuple[int, int]] = set()
    unique: list[tuple[int, int, str]] = []
    for start, end, reason in windows:
        if evidence_file.line_count <= 0:
            continue
        start = max(1, min(start, evidence_file.line_count))
        end = max(start, min(end, evidence_file.line_count))
        if end - start + 1 > line_window:
            end = min(evidence_file.line_count, start + line_window - 1)
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        unique.append((start, end, reason))
    return unique


def ranked_regions_for_file(
    repo: Path,
    context_file: agent_contracts.ContextFile,
    evidence_file: EvidenceFile,
    file_score: int,
    signals: QuerySignals,
    modules: list[agent_contracts.ModuleInfo],
    *,
    content_idf: dict[str, float] | None = None,
    module_evidence_cache: dict[str, set[str]] | None = None,
    line_window: int,
    line_overlap: int,
    regions_per_file: int,
) -> list[RankedRegion]:
    text = safe_text(repo, context_file.path, limit=5_000_000)
    if not text:
        return []
    lines = text.splitlines()
    module = module_for_path(modules, context_file.path)
    quoted_term_tokens: list[set[str]] = []
    for term in signals.quoted_terms:
        term_tokens = set(tokenize(term))
        if term_tokens:
            quoted_term_tokens.append(term_tokens)
    regions: list[RankedRegion] = []
    for start, end, source_reason in candidate_windows_for_evidence_file(
        evidence_file,
        signals,
        line_window=line_window,
        line_overlap=line_overlap,
    ):
        window_text = "\n".join(lines[start - 1 : end])
        window_score, reasons, matched = score_window(
            window_text,
            issue_tokens=signals.tokens,
            file=context_file,
            module=module,
            repo=repo,
            content_idf=content_idf,
            module_evidence_cache=module_evidence_cache,
        )
        if source_reason == "traceback-line":
            window_score += 80
        elif source_reason.startswith("symbol:"):
            window_score += 32
        if quoted_term_tokens:
            window_tokens = token_set(window_text)
        else:
            window_tokens = set()
        for term_tokens in quoted_term_tokens:
            if term_tokens.issubset(window_tokens):
                window_score += 16
                reasons.append(f"window-quoted-term:{','.join(sorted(term_tokens)[:4])}")
                matched.extend(sorted(term_tokens)[:4])
        reasons = unique_ordered([source_reason, *reasons])
        total_score = file_score * 3 + window_score
        regions.append(
            RankedRegion(
                path=context_file.path,
                start=start,
                end=end,
                score=total_score,
                file_score=file_score,
                window_score=window_score,
                reasons=reasons,
                matched_tokens=unique_ordered(matched),
            )
    )
    regions.sort(key=lambda item: (-item.score, item.start, item.end))
    if regions and all(item.window_score <= 0 for item in regions):
        return regions[:1]
    selected: list[RankedRegion] = []
    for region in regions:
        redundant = False
        for existing in selected:
            overlap = max(0, min(region.end, existing.end) - max(region.start, existing.start) + 1)
            region_len = region.end - region.start + 1
            if overlap == region_len or overlap / max(region_len, 1) >= 0.75:
                redundant = True
                break
        if redundant:
            continue
        selected.append(region)
        if len(selected) >= regions_per_file:
            break
    return selected


def global_retrieval_regions(
    repo: Path,
    evidence: dict[str, EvidenceFile],
    modules: list[agent_contracts.ModuleInfo],
    signals: QuerySignals,
    content_idf: dict[str, float],
    file_scores: dict[str, Any],
    module_evidence_cache: dict[str, set[str]],
    *,
    line_window: int,
    line_overlap: int,
    limit: int,
) -> list[RankedRegion]:
    ranked: list[RankedRegion] = []
    candidate_roles = {"source", "test"}
    for evidence_file in evidence.values():
        if evidence_file.generated or evidence_file.role not in candidate_roles:
            continue
        if Path(evidence_file.path).suffix not in {".py", ".pyi"}:
            continue
        direct_signal = signals.tokens & (evidence_file.content_tokens | evidence_file.tokens)
        symbol_signal = (
            fuzzy_token_matches(signals.tokens | signals.symbol_terms, set().union(*(symbol.tokens for symbol in evidence_file.symbols)))
            if evidence_file.symbols
            else []
        )
        if not direct_signal and not symbol_signal and evidence_file.path not in signals.path_hints:
            continue
        text = safe_text(repo, evidence_file.path, limit=5_000_000)
        if not text:
            continue
        context_file = agent_contracts.ContextFile(evidence_file.path, evidence_file.role)
        module = module_for_path(modules, evidence_file.path)
        lines = text.splitlines()
        file_prior = file_scores.get(evidence_file.path)
        prior_score = int(file_prior.score) if file_prior is not None else 0
        for start, end in windows_for_line_count(
            len(lines),
            line_window=line_window,
            line_overlap=line_overlap,
        ):
            window_text = "\n".join(lines[start - 1 : end])
            window_score, reasons, matched = score_window(
                window_text,
                issue_tokens=signals.tokens,
                file=context_file,
                module=module,
                repo=repo,
                content_idf=content_idf,
                module_evidence_cache=module_evidence_cache,
            )
            if window_score <= 0:
                continue
            role_bonus = ROLE_REGION_BASE_SCORE.get(evidence_file.role, 1)
            total_score = window_score * 4 + min(prior_score, 120) + role_bonus
            ranked.append(
                RankedRegion(
                    path=evidence_file.path,
                    start=start,
                    end=end,
                    score=total_score,
                    file_score=prior_score,
                    window_score=window_score,
                    reasons=unique_ordered(["global-retrieval", *reasons]),
                    matched_tokens=unique_ordered(matched),
                )
            )
    ranked.sort(key=lambda item: (-item.score, item.path, item.start, item.end))
    selected: list[RankedRegion] = []
    for region in ranked:
        redundant = False
        for existing in selected:
            if region.path != existing.path:
                continue
            overlap = max(0, min(region.end, existing.end) - max(region.start, existing.start) + 1)
            region_len = region.end - region.start + 1
            if overlap == region_len or overlap / max(region_len, 1) >= 0.75:
                redundant = True
                break
        if redundant:
            continue
        selected.append(region)
        if len(selected) >= limit:
            break
    return selected


def tfidf_retrieval_regions(
    repo: Path,
    query: str,
    file_scores: dict[str, Any],
    *,
    chunk_size: int = 80,
    chunk_overlap: int = 20,
    max_features: int = 10_000,
    max_chunks: int = 3_000,
    limit: int,
) -> list[RankedRegion]:
    query_terms = tfidf_tokens(query)
    if not query_terms:
        return []
    chunks = iter_tfidf_chunks(
        repo,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        max_chunks=max_chunks,
    )
    if not chunks:
        return []

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore[import-not-found]
        from sklearn.metrics.pairwise import cosine_similarity  # type: ignore[import-not-found]

        vectorizer = TfidfVectorizer(
            max_features=max_features,
            sublinear_tf=True,
            stop_words="english",
            token_pattern=r"(?u)\b[A-Za-z_][A-Za-z0-9_]{1,}\b",
        )
        chunk_matrix = vectorizer.fit_transform([chunk.content for chunk in chunks])
        query_vector = vectorizer.transform([query])
        scores = cosine_similarity(query_vector, chunk_matrix)[0]
        top_indices = scores.argsort()[-limit:][::-1]
        ranked: list[RankedRegion] = []
        for index in top_indices:
            score_value = float(scores[index])
            if score_value <= 0:
                continue
            chunk = chunks[int(index)]
            file_prior = file_scores.get(chunk.path)
            prior_score = int(file_prior.score) if file_prior is not None else 0
            ranked.append(
                RankedRegion(
                    path=chunk.path,
                    start=chunk.start,
                    end=chunk.end,
                    score=int(score_value * 100_000) + min(prior_score, 160),
                    file_score=prior_score,
                    window_score=int(score_value * 100_000),
                    reasons=["sklearn-tfidf-retrieval"],
                    matched_tokens=sorted(set(query_terms)),
                )
            )
        return ranked
    except (ImportError, ValueError):
        pass

    term_frequency: dict[str, int] = {}
    document_frequency: dict[str, int] = {}
    chunk_counts: list[dict[str, int]] = []
    for chunk in chunks:
        counts: dict[str, int] = {}
        for token in chunk.tokens:
            counts[token] = counts.get(token, 0) + 1
            term_frequency[token] = term_frequency.get(token, 0) + 1
        for token in counts:
            document_frequency[token] = document_frequency.get(token, 0) + 1
        chunk_counts.append(counts)

    if len(term_frequency) > max_features:
        vocabulary = {
            token
            for token, _count in sorted(term_frequency.items(), key=lambda item: (-item[1], item[0]))[:max_features]
        }
    else:
        vocabulary = set(term_frequency)
    query_counts: dict[str, int] = {}
    for token in query_terms:
        if token in vocabulary:
            query_counts[token] = query_counts.get(token, 0) + 1
    if not query_counts:
        return []

    document_count = len(chunks)
    idf = {
        token: math.log((1 + document_count) / (1 + document_frequency.get(token, 0))) + 1.0
        for token in vocabulary
    }

    def weighted_vector(counts: dict[str, int]) -> tuple[dict[str, float], float]:
        weighted: dict[str, float] = {}
        norm_sq = 0.0
        for token, count in counts.items():
            if token not in vocabulary:
                continue
            weight = (1.0 + math.log(count)) * idf.get(token, 1.0)
            weighted[token] = weight
            norm_sq += weight * weight
        return weighted, math.sqrt(norm_sq)

    query_vector, query_norm = weighted_vector(query_counts)
    if query_norm == 0:
        return []

    ranked: list[RankedRegion] = []
    for chunk, counts in zip(chunks, chunk_counts):
        file_prior = file_scores.get(chunk.path)
        if file_prior is None:
            continue
        chunk_vector, chunk_norm = weighted_vector(counts)
        if chunk_norm == 0:
            continue
        dot = sum(query_vector[token] * chunk_vector.get(token, 0.0) for token in query_vector)
        if dot <= 0:
            continue
        cosine = dot / (query_norm * chunk_norm)
        if cosine <= 0:
            continue
        prior_score = int(file_prior.score)
        score = int(cosine * 2_000) + min(prior_score, 240)
        ranked.append(
            RankedRegion(
                path=chunk.path,
                start=chunk.start,
                end=chunk.end,
                score=score,
                file_score=prior_score,
                window_score=int(cosine * 100_000),
                reasons=["tfidf-retrieval"],
                matched_tokens=sorted(query_counts),
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.path, item.start, item.end))
    return ranked[:limit]


def selection_to_regions(
    repo: Path,
    selection: dict[str, Any],
    issue_text: str,
    *,
    top_k: int,
    line_window: int,
    line_overlap: int,
) -> tuple[list[dict[str, int | str]], list[dict[str, Any]]]:
    map_data = agent_contracts.build_module_map(repo)
    modules = agent_contracts.modules_from_map(map_data)
    issue_tokens = set(tokenize(issue_text))
    file_scores = selection.get("file_scores") or {}
    ranked_regions: list[RankedRegion] = []

    for index, context_file in enumerate(selection.get("files", []), start=1):
        ranked_file = file_scores.get(context_file.path)
        if ranked_file is not None:
            file_score = int(ranked_file.score)
        else:
            file_score = ROLE_REGION_BASE_SCORE.get(context_file.role, 1) + max(
                0,
                len(selection.get("files", [])) - index,
            )
        region = best_region_for_file(
            repo,
            context_file,
            file_score,
            issue_tokens,
            modules,
            line_window=line_window,
            line_overlap=line_overlap,
        )
        if region is not None:
            ranked_regions.append(region)

    ranked_regions.sort(key=lambda item: (-item.score, item.path, item.start, item.end))
    first_pass: list[RankedRegion] = []
    seen_paths: set[str] = set()
    for region in ranked_regions:
        if region.path in seen_paths:
            continue
        first_pass.append(region)
        seen_paths.add(region.path)
        if len(first_pass) >= top_k:
            break
    if len(first_pass) < top_k:
        selected_keys = {(item.path, item.start, item.end) for item in first_pass}
        for region in ranked_regions:
            key = (region.path, region.start, region.end)
            if key in selected_keys:
                continue
            first_pass.append(region)
            selected_keys.add(key)
            if len(first_pass) >= top_k:
                break
    top_regions = first_pass[:top_k]
    region_dicts = [
        {"path": item.path, "start": item.start, "end": item.end}
        for item in top_regions
    ]
    trace = [
        {
            "rank": index,
            "strategy": "line-window",
            "operation": "region_rank",
            "path": item.path,
            "start": item.start,
            "end": item.end,
            "score": item.score,
            "file_score": item.file_score,
            "window_score": item.window_score,
            "reasons": item.reasons,
            "matched_tokens": item.matched_tokens,
        }
        for index, item in enumerate(top_regions, start=1)
    ]
    return region_dicts, trace


def selection_to_regions_beat_sota1(
    repo: Path,
    selection: dict[str, Any],
    issue_text: str,
    *,
    top_k: int,
    line_window: int,
    line_overlap: int,
    regions_per_file: int,
) -> tuple[list[dict[str, int | str]], list[dict[str, Any]]]:
    evidence = selection.get("_beat_sota1_evidence")
    modules = selection.get("_beat_sota1_modules")
    content_idf = selection.get("_beat_sota1_content_idf")
    if not isinstance(evidence, dict) or not isinstance(modules, list) or not isinstance(content_idf, dict):
        evidence, modules = build_evidence_index(repo, line_window=line_window)
        content_idf = build_content_idf(evidence)
    signals = extract_query_signals(issue_text)
    file_scores = selection.get("file_scores") or {}
    module_evidence_cache: dict[str, set[str]] = {}
    ranked_regions: list[RankedRegion] = []
    for index, context_file in enumerate(selection.get("files", []), start=1):
        evidence_file = evidence.get(context_file.path)
        if not evidence_file:
            continue
        ranked_file = file_scores.get(context_file.path)
        if ranked_file is not None:
            file_score = int(ranked_file.score)
        else:
            file_score = ROLE_REGION_BASE_SCORE.get(context_file.role, 1) + max(0, len(selection.get("files", [])) - index)
        ranked_regions.extend(
            ranked_regions_for_file(
                repo,
                context_file,
                evidence_file,
                file_score,
                signals,
                modules,
                content_idf=content_idf,
                module_evidence_cache=module_evidence_cache,
                line_window=line_window,
                line_overlap=line_overlap,
                regions_per_file=regions_per_file,
            )
        )
    ranked_regions.extend(
        global_retrieval_regions(
            repo,
            evidence,
            modules,
            signals,
            content_idf,
            file_scores,
            module_evidence_cache,
            line_window=line_window,
            line_overlap=line_overlap,
            limit=max(top_k * 8, 40),
        )
    )
    ranked_regions.extend(
        tfidf_retrieval_regions(
            repo,
            issue_text,
            file_scores,
            limit=max(top_k * 8, 40),
        )
    )
    ranked_regions.sort(key=lambda item: (-item.score, item.path, item.start, item.end))
    top_regions: list[RankedRegion] = []
    selected_keys: set[tuple[str, int, int]] = set()
    for region in ranked_regions:
        key = (region.path, region.start, region.end)
        if key in selected_keys:
            continue
        top_regions.append(region)
        selected_keys.add(key)
        if len(top_regions) >= top_k:
            break
    region_dicts = [
        {"path": item.path, "start": item.start, "end": item.end}
        for item in top_regions
    ]
    trace = [
        {
            "rank": index,
            "strategy": "beat-sota1-line-window",
            "operation": "region_rank",
            "path": item.path,
            "start": item.start,
            "end": item.end,
            "score": item.score,
            "file_score": item.file_score,
            "window_score": item.window_score,
            "reasons": item.reasons,
            "matched_tokens": item.matched_tokens,
        }
        for index, item in enumerate(top_regions, start=1)
    ]
    return region_dicts, trace


def sanitized_selection_row(issue_text: str) -> dict[str, Any]:
    return {"task": issue_text, "graph_seed_hints": []}


def context_candidate_risk_counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    noisy_path_count = 0
    vendored_noisy_path_count = 0
    generated_or_minified_count = 0
    docs_or_config_count = 0
    for candidate in candidates:
        flags = set(candidate.get("risk_flags", []))
        if flags:
            noisy_path_count += 1
        if "vendor_generated_static" in flags:
            vendored_noisy_path_count += 1
        if flags & {"generated", "generated_or_minified"}:
            generated_or_minified_count += 1
        if flags & {"docs", "config"}:
            docs_or_config_count += 1
    return {
        "noisy_path_count": noisy_path_count,
        "vendored_noisy_path_count": vendored_noisy_path_count,
        "generated_or_minified_count": generated_or_minified_count,
        "docs_or_config_count": docs_or_config_count,
    }


def context_localized_context_files(
    repo: Path,
    issue_text: str,
    *,
    max_files: int,
    max_bytes: int,
    top_k: int,
    line_window: int,
    model_profile: str | dict[str, Any] | None = "unknown",
) -> dict[str, Any]:
    profile = agent_contracts.model_profile_payload(model_profile)
    localization = agent_contracts.localize_issue_context(
        repo,
        issue_text,
        max_candidate_files=max(max_files * 2, max_files + top_k),
        max_regions=max(top_k * 4, top_k),
        line_window=line_window,
        max_bytes=max_bytes,
        model_profile=profile,
        include_internal_indexes=True,
    )
    baseline = agent_contracts.baseline_retrieve_context(
        repo,
        issue_text,
        search_index=localization.get("_search_index"),
        max_files=max_files,
    )
    gate = agent_contracts.evaluate_context_quality(
        localization,
        baseline,
        estimate=agent_contracts.estimate_candidate_bytes(repo, localization.get("file_candidates", [])[:max_files]),
        model_profile=profile,
    )
    all_candidates = localization.get("file_candidates", [])
    risk_counts = context_candidate_risk_counts(all_candidates)
    candidate_context_files = [
        agent_contracts.ContextFile(candidate["path"], candidate.get("role", "source"))
        for candidate in all_candidates
    ]
    limited_files, limit_omissions, selected_bytes = agent_contracts.apply_context_limits(
        repo,
        candidate_context_files,
        max_files=max_files,
        max_bytes=max_bytes,
    )
    included_paths = {item.path for item in limited_files}
    candidates = [candidate for candidate in all_candidates if candidate["path"] in included_paths]
    bounded_localization = dict(localization)
    bounded_localization["file_candidates"] = candidates
    bounded_regions = [
        region
        for region in localization.get("regions", [])
        if region.get("path") in included_paths
    ]
    if gate["decision"] in {"tool_only", "abstain"}:
        bounded_regions = []
    else:
        bounded_regions = bounded_regions[: int(gate.get("limits", {}).get("max_preloaded_regions", top_k))]
    bounded_localization["regions"] = bounded_regions
    trace = [
        {
            "rank": index,
            "strategy": "context-localized",
            "operation": "file_rank",
            "path": candidate["path"],
            "role": candidate.get("role"),
            "score": candidate.get("score"),
            "confidence": candidate.get("confidence"),
            "evidence": candidate.get("evidence", []),
            "risk_flags": candidate.get("risk_flags", []),
        }
        for index, candidate in enumerate(candidates, start=1)
    ]
    trace.extend(
        {
            "rank": index,
            "strategy": "context-localized",
            "operation": "region_rank",
            "path": region["path"],
            "start": region["start"],
            "end": region["end"],
            "score": region.get("score"),
            "file_score": region.get("file_score"),
            "window_score": region.get("window_score"),
            "strength": region.get("strength"),
            "evidence": region.get("evidence", []),
        }
        for index, region in enumerate(bounded_regions, start=1)
    )
    return {
        "files": [agent_contracts.ContextFile(candidate["path"], candidate.get("role", "source")) for candidate in candidates],
        "included_files": [candidate["path"] for candidate in candidates],
        "omitted_files": limit_omissions,
        "selected_bytes": selected_bytes,
        "resolved_module": localization.get("module_candidates", [{}])[0].get("name") if localization.get("module_candidates") else None,
        "trace": trace,
        "context_localized": {
            "model_profile": profile,
            "localization": bounded_localization,
            "gate": gate,
            "baseline": baseline,
            "all_candidate_count": len(all_candidates),
            "risk_counts": risk_counts,
        },
    }


def select_context(
    repo: Path,
    issue_text: str,
    strategy: str,
    *,
    max_files: int,
    max_bytes: int,
    top_k: int,
    line_window: int,
    line_overlap: int,
    beat_sota1_regions_per_file: int,
    beat_sota1_expansion_rounds: int,
    ablations: set[str],
    model_profile: str | dict[str, Any] | None = "unknown",
) -> dict[str, Any]:
    if strategy == "context-localized":
        return context_localized_context_files(
            repo,
            issue_text,
            max_files=max_files,
            max_bytes=max_bytes,
            top_k=top_k,
            line_window=line_window,
            model_profile=model_profile,
        )
    if strategy == "beat-sota1":
        return beat_sota1_context_files(
            repo,
            issue_text,
            max_files=max_files,
            max_bytes=max_bytes,
            top_k=top_k,
            line_window=line_window,
            line_overlap=line_overlap,
            regions_per_file=beat_sota1_regions_per_file,
            expansion_rounds=beat_sota1_expansion_rounds,
            ablations=ablations,
        )
    if strategy == "contract-ranked":
        return contract_ranked_context_files(
            repo,
            issue_text,
            max_files=max_files,
            max_bytes=max_bytes,
        )
    selection = agent_contracts.select_context_files_for_strategy(
        repo,
        sanitized_selection_row(issue_text),
        strategy,
        max_files=max_files,
        max_bytes=max_bytes,
    )
    if "trace" not in selection:
        selection["trace"] = agent_contracts.selected_file_trace(selection.get("files", []), strategy)
    return selection


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_issue_map(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return {str(key): str(value) for key, value in data.items()}


def resolve_repo_dir(record: dict[str, Any], repos_root: Path) -> Path | None:
    instance_id = str(record.get("instance_id", ""))
    repo_dir_value = record.get("repo_dir")
    if isinstance(repo_dir_value, str) and repo_dir_value:
        candidate = Path(repo_dir_value)
        if not candidate.is_absolute():
            candidate = repos_root / candidate
        if candidate.is_dir():
            return candidate

    candidates = [
        repos_root / "repos" / instance_id,
        repos_root / instance_id,
    ]
    if "__" in instance_id:
        org, rest = instance_id.split("__", 1)
        repo = rest.rsplit("-", 1)[0] if "-" in rest else rest
        candidates.extend(
            [
                repos_root / "repos" / f"{org}__{repo}",
                repos_root / "repos" / f"{org}-{repo}",
                repos_root / "repos" / repo,
                repos_root / f"{org}__{repo}",
                repos_root / repo,
            ]
        )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def completed_instance_ids(output_path: Path) -> set[str]:
    if not output_path.is_file():
        return set()
    ids = set()
    with output_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            instance_id = row.get("instance_id")
            if isinstance(instance_id, str):
                ids.add(instance_id)
    return ids


def append_jsonl_row(output_path: Path, row: dict[str, Any]) -> None:
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def load_swe_evaluator(bench_path: Path) -> type[Any]:
    eval_path = bench_path.parent / "eval.py"
    if not eval_path.is_file():
        raise RuntimeError(f"SWE-Explore eval.py not found next to bench: {eval_path}")
    module = types.ModuleType("swe_explore_eval")
    module.__file__ = eval_path.as_posix()
    source = eval_path.read_text(encoding="utf-8")
    code = compile(
        source,
        eval_path.as_posix(),
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )
    exec(code, module.__dict__)
    return module.ExploreEvaluator


def iter_ground_truth_paths(ground_truth: dict[str, Any]) -> set[str]:
    paths = set()
    for region in ground_truth.get("read_core_regions") or []:
        path = region.get("path") if isinstance(region, dict) else None
        if isinstance(path, str):
            paths.add(path)
    for regions in (ground_truth.get("read_optional_regions_map") or {}).values():
        for region in regions:
            path = region.get("path") if isinstance(region, dict) else None
            if isinstance(path, str):
                paths.add(path)
    return paths


def build_file_line_counts(
    records: list[dict[str, Any]],
    repos_root: Path,
    output_rows: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    predictions_by_id = {
        row["instance_id"]: {region["path"] for region in row.get("regions", [])}
        for row in output_rows
    }
    counts: dict[str, dict[str, int]] = {}
    for record in records:
        instance_id = str(record.get("instance_id", ""))
        repo = resolve_repo_dir(record, repos_root)
        if repo is None:
            continue
        paths = iter_ground_truth_paths(record.get("ground_truth") or {})
        paths.update(predictions_by_id.get(instance_id, set()))
        per_instance = {}
        for path in sorted(paths):
            count = line_count(repo, path)
            if count:
                per_instance[path] = count
        if per_instance:
            counts[instance_id] = per_instance
    return counts


def add_metrics(
    bench_path: Path,
    records: list[dict[str, Any]],
    repos_root: Path,
    output_rows: list[dict[str, Any]],
) -> dict[str, float]:
    evaluator_cls = load_swe_evaluator(bench_path)
    file_line_counts = build_file_line_counts(records, repos_root, output_rows)
    evaluator = evaluator_cls(bench_path, file_line_counts=file_line_counts)
    record_by_id = {record["instance_id"]: record for record in records}
    totals = {metric: 0.0 for metric in SWE_METRICS}
    evaluated = 0
    for row in output_rows:
        instance_id = row["instance_id"]
        record = record_by_id.get(instance_id)
        if not record:
            continue
        evaluator._current_instance_id = instance_id
        evaluator._current_file_line_counts = file_line_counts.get(instance_id, {})
        ground_truth = record.get("ground_truth") or {}
        preds = [(item["path"], item["start"], item["end"]) for item in row.get("regions", [])]
        metrics = {}
        for metric in SWE_METRICS:
            value = getattr(evaluator, f"evaluate_{metric}")(preds, ground_truth)
            metrics[metric] = value
            totals[metric] += value
        row["metrics"] = metrics
        evaluated += 1
    return {metric: (totals[metric] / evaluated if evaluated else 0.0) for metric in SWE_METRICS}


def build_output_row(
    record: dict[str, Any],
    issue_text: str,
    repo: Path,
    strategy: str,
    *,
    top_k: int,
    max_files: int,
    max_bytes: int,
    line_window: int,
    line_overlap: int,
    beat_sota1_regions_per_file: int,
    beat_sota1_expansion_rounds: int,
    ablations: set[str],
    model_profile: str | dict[str, Any] | None = "unknown",
) -> dict[str, Any]:
    profile = agent_contracts.model_profile_payload(model_profile)
    selection = select_context(
        repo,
        issue_text,
        strategy,
        max_files=max_files,
        max_bytes=max_bytes,
        top_k=top_k,
        line_window=line_window,
        line_overlap=0,
        beat_sota1_regions_per_file=beat_sota1_regions_per_file,
        beat_sota1_expansion_rounds=beat_sota1_expansion_rounds,
        ablations=ablations,
        model_profile=profile,
    )
    if strategy == "beat-sota1":
        regions, region_trace = selection_to_regions_beat_sota1(
            repo,
            selection,
            issue_text,
            top_k=top_k,
            line_window=line_window,
            line_overlap=line_overlap,
            regions_per_file=beat_sota1_regions_per_file,
        )
    elif strategy == "context-localized":
        localized = selection.get("context_localized", {}).get("localization", {})
        top_regions = localized.get("regions", [])[:top_k]
        regions = [
            {"path": item["path"], "start": int(item["start"]), "end": int(item["end"])}
            for item in top_regions
        ]
        region_trace = [
            {
                "rank": index,
                "strategy": "context-localized",
                "operation": "region_emit",
                "path": item["path"],
                "start": int(item["start"]),
                "end": int(item["end"]),
                "score": item.get("score"),
                "strength": item.get("strength"),
                "evidence": item.get("evidence", []),
            }
            for index, item in enumerate(top_regions, start=1)
        ]
    else:
        regions, region_trace = selection_to_regions(
            repo,
            selection,
            issue_text,
            top_k=top_k,
            line_window=line_window,
            line_overlap=line_overlap,
        )
    metadata = {
        "resolved_module": selection.get("resolved_module"),
        "included_files": selection.get("included_files", []),
        "omitted_files": selection.get("omitted_files", []),
        "selected_bytes": selection.get("selected_bytes", 0),
        "model_profile": profile,
        "limits": {
            "top_k": top_k,
            "max_files": max_files,
            "max_bytes": max_bytes,
            "line_window": line_window,
            "line_overlap": line_overlap,
            "beat_sota1_regions_per_file": beat_sota1_regions_per_file,
            "beat_sota1_expansion_rounds": beat_sota1_expansion_rounds,
            "ablations": sorted(ablations),
        },
        "allowed_inputs": [
            "base_repo_snapshot",
            "issue_text",
            "agent_contracts_analyzer_outputs",
            "base_repo_local_contracts",
        ],
        "trace": [*selection.get("trace", []), *region_trace],
    }
    if "beat_sota1" in selection:
        metadata["beat_sota1"] = selection["beat_sota1"]
    if "context_localized" in selection:
        localized_candidates = selection["context_localized"]["localization"].get("file_candidates", [])
        risk_counts = selection["context_localized"].get("risk_counts") or context_candidate_risk_counts(localized_candidates)
        metadata["context_localized"] = {
            "model_profile": selection["context_localized"].get("model_profile", profile),
            "confidence": selection["context_localized"]["localization"].get("confidence"),
            "gate": selection["context_localized"].get("gate"),
            "signals": selection["context_localized"]["localization"].get("signals"),
            "candidate_count": selection["context_localized"].get("all_candidate_count", len(localized_candidates)),
            "region_count": len(selection["context_localized"]["localization"].get("regions", [])),
            **risk_counts,
        }
    return {
        "instance_id": record["instance_id"],
        "explorer": f"agent-contracts:{strategy}",
        "regions": regions,
        "num_regions": len(regions),
        "metadata": metadata,
    }


def snippet_for_region(repo: Path, region: dict[str, int | str], *, max_chars: int = 4_000) -> str:
    path = str(region["path"])
    start = int(region["start"])
    end = int(region["end"])
    text = safe_text(repo, path, limit=5_000_000)
    if not text:
        return ""
    lines = text.splitlines()
    selected = lines[max(0, start - 1) : min(len(lines), end)]
    snippet = "\n".join(f"{start + index}: {line}" for index, line in enumerate(selected))
    if len(snippet) > max_chars:
        return snippet[:max_chars] + "\n...[truncated]"
    return snippet


def build_agent_contracts_precontext(
    repo: Path,
    issue_text: str,
    *,
    top_k: int,
    max_files: int,
    max_bytes: int,
    line_window: int,
    line_overlap: int,
) -> tuple[str, dict[str, Any]]:
    selection = select_context(
        repo,
        issue_text,
        "contract-ranked",
        max_files=max_files,
        max_bytes=max_bytes,
        top_k=top_k,
        line_window=line_window,
        line_overlap=line_overlap,
        beat_sota1_regions_per_file=DEFAULT_BEAT_SOTA1_REGIONS_PER_FILE,
        beat_sota1_expansion_rounds=DEFAULT_BEAT_SOTA1_EXPANSION_ROUNDS,
        ablations=set(),
    )
    regions, region_trace = selection_to_regions(
        repo,
        selection,
        issue_text,
        top_k=top_k,
        line_window=line_window,
        line_overlap=line_overlap,
    )
    metadata = {
        "resolved_module": selection.get("resolved_module"),
        "included_files": selection.get("included_files", []),
        "selected_bytes": selection.get("selected_bytes", 0),
        "regions": regions,
        "trace": [*selection.get("trace", []), *region_trace],
    }

    lines = [
        "## agent-contracts pre-context",
        "",
        f"Resolved module: {selection.get('resolved_module') or 'unknown'}",
        "",
        "Candidate files:",
        *[f"- {path}" for path in selection.get("included_files", [])[:max_files]],
        "",
        "Candidate line regions:",
    ]
    for index, region in enumerate(regions, start=1):
        lines.append(f"{index}. {region['path']}:{region['start']}-{region['end']}")
        snippet = snippet_for_region(repo, region)
        if snippet:
            lines.extend(["```", snippet, "```"])
    return "\n".join(lines), metadata


def build_context_localized_precontext(
    repo: Path,
    issue_text: str,
    *,
    top_k: int,
    max_files: int,
    max_bytes: int,
    line_window: int,
    precontext_candidates: int,
    precontext_max_chars: int,
    model_profile: str | dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    profile = agent_contracts.model_profile_payload(model_profile)
    selection = select_context(
        repo,
        issue_text,
        "context-localized",
        max_files=max_files,
        max_bytes=max_bytes,
        top_k=top_k,
        line_window=line_window,
        line_overlap=0,
        beat_sota1_regions_per_file=DEFAULT_BEAT_SOTA1_REGIONS_PER_FILE,
        beat_sota1_expansion_rounds=DEFAULT_BEAT_SOTA1_EXPANSION_ROUNDS,
        ablations=set(),
        model_profile=profile,
    )
    localized = selection.get("context_localized", {})
    localization = localized.get("localization", {})
    gate = localized.get("gate", {})
    regions = [
        {"path": item["path"], "start": int(item["start"]), "end": int(item["end"])}
        for item in localization.get("regions", [])[:precontext_candidates]
    ]
    risk_counts = localized.get("risk_counts") or context_candidate_risk_counts(localization.get("file_candidates", []))
    metadata = {
        "strategy": "context-localized",
        "model_profile": profile,
        "included_files": selection.get("included_files", []),
        "selected_bytes": selection.get("selected_bytes", 0),
        "regions": regions,
        "gate": gate,
        "confidence": localization.get("confidence"),
        "signals": localization.get("signals"),
        "candidate_count": localized.get("all_candidate_count", len(localization.get("file_candidates", []))),
        "region_count": len(localization.get("regions", [])),
        "risk_counts": risk_counts,
        "trace": selection.get("trace", []),
    }

    decision = gate.get("decision", "unknown")
    confidence = gate.get("confidence", localization.get("confidence", "unknown"))
    lines = [
        "## context-localized Agent Contracts evidence",
        "",
        "This context is advisory. Form an independent hypothesis from the issue and repository before relying on these hints.",
        "",
        f"Model profile: {profile['name']}",
        f"Gate decision: {decision}",
        f"Gate confidence: {confidence}",
        f"Fallback: {gate.get('fallback', 'unknown')}",
        f"Selected bytes: {selection.get('selected_bytes', 0)}",
        f"Noisy path count: {risk_counts.get('noisy_path_count', 0)}",
        f"Vendored/noisy path count: {risk_counts.get('vendored_noisy_path_count', 0)}",
        "",
    ]
    if decision in {"tool_only", "abstain"} or not regions:
        lines.extend(
            [
                "No file names or snippets are preloaded for this gate decision.",
                "Use repository tools directly and treat Agent Contracts as an audit trail only.",
            ]
        )
        return "\n".join(lines), metadata

    lines.extend(
        [
            "Selected files:",
            *[f"- {path}" for path in selection.get("included_files", [])[:max_files]],
            "",
        ]
    )
    lines.append("Selected line regions:")
    used_chars = len("\n".join(lines))
    for index, region in enumerate(regions, start=1):
        header = f"{index}. {region['path']}:{region['start']}-{region['end']}"
        snippet = snippet_for_region(repo, region, max_chars=2_400 if profile["name"] == "spark" else 4_000)
        block = "\n".join([header, "```", snippet, "```", ""])
        if used_chars + len(block) > precontext_max_chars:
            lines.append("[localized snippets truncated by precontext budget]")
            break
        lines.append(block)
        used_chars += len(block)
    return "\n".join(lines), metadata


def build_beat_sota1_precontext(
    repo: Path,
    issue_text: str,
    *,
    top_k: int,
    max_files: int,
    max_bytes: int,
    line_window: int,
    line_overlap: int,
    regions_per_file: int,
    expansion_rounds: int,
    precontext_candidates: int,
    precontext_max_chars: int,
    ablations: set[str],
) -> tuple[str, dict[str, Any]]:
    selection = select_context(
        repo,
        issue_text,
        "beat-sota1",
        max_files=max_files,
        max_bytes=max_bytes,
        top_k=top_k,
        line_window=line_window,
        line_overlap=line_overlap,
        beat_sota1_regions_per_file=regions_per_file,
        beat_sota1_expansion_rounds=expansion_rounds,
        ablations=ablations,
    )
    regions, region_trace = selection_to_regions_beat_sota1(
        repo,
        selection,
        issue_text,
        top_k=max(top_k, precontext_candidates),
        line_window=line_window,
        line_overlap=line_overlap,
        regions_per_file=regions_per_file,
    )
    metadata = {
        "resolved_module": selection.get("resolved_module"),
        "included_files": selection.get("included_files", []),
        "selected_bytes": selection.get("selected_bytes", 0),
        "regions": regions[:precontext_candidates],
        "trace": [*selection.get("trace", []), *region_trace],
        "beat_sota1": selection.get("beat_sota1", {}),
    }

    trace_by_region = {
        (item.get("path"), item.get("start"), item.get("end")): item
        for item in region_trace
        if item.get("operation") == "region_rank"
    }
    lines = [
        "## beatSOTA1 candidates",
        "",
        "These are compact, scored candidate regions. Rerank them, drop weak ones, or replace only when repository evidence supports the replacement.",
        "",
        f"Resolved module: {selection.get('resolved_module') or 'unknown'}",
        f"Ablations: {', '.join(sorted(ablations)) if ablations else 'none'}",
        "",
    ]
    used_chars = len("\n".join(lines))
    for index, region in enumerate(regions[:precontext_candidates], start=1):
        key = (region["path"], region["start"], region["end"])
        trace = trace_by_region.get(key, {})
        header = (
            f"{index}. {region['path']}:{region['start']}-{region['end']} "
            f"score={trace.get('score', 'n/a')} reasons={','.join(trace.get('reasons', [])[:5])}"
        )
        snippet = snippet_for_region(repo, region, max_chars=2_400)
        block = "\n".join([header, "```", snippet, "```", ""])
        if used_chars + len(block) > precontext_max_chars:
            lines.append("[candidate snippets truncated by precontext budget]")
            break
        lines.append(block)
        used_chars += len(block)
    return "\n".join(lines), metadata


def beat_sota2_is_noisy_precontext_path(path: str) -> bool:
    path_obj = Path(path)
    lowered_parts = {part.lower() for part in path_obj.parts}
    lowered_name = path_obj.name.lower()
    if agent_contracts.context_path_risk_flags(path):
        return True
    if lowered_name in SOTA2_NOISY_PRECONTEXT_NAMES:
        return True
    if MINIFIED_RE.search(path) or "jquery" in lowered_name:
        return True
    if lowered_parts & SOTA2_NOISY_PRECONTEXT_PARTS:
        return True
    return False


def beat_sota2_is_code_precontext_path(path: str) -> bool:
    return Path(path).suffix.lower() in SOTA2_CODE_PRECONTEXT_EXTS


def beat_sota2_reason_tokens(reason: str) -> set[str]:
    if ":" not in reason:
        return set()
    return {token for token in tokenize(reason.split(":", 1)[1]) if token not in SOTA2_GENERIC_EVIDENCE_TOKENS}


def beat_sota2_has_direct_candidate_evidence(candidate: dict[str, Any]) -> bool:
    for reason in candidate.get("reasons", []):
        if reason.startswith(("exact-path:", "traceback-path", "quoted-term:")):
            return True
        if reason.startswith(("basename:", "path:", "path-basename:")):
            if beat_sota2_reason_tokens(reason):
                return True
    return False


def beat_sota2_has_supporting_candidate_evidence(candidate: dict[str, Any]) -> bool:
    if beat_sota2_has_direct_candidate_evidence(candidate):
        return True
    for reason in candidate.get("reasons", []):
        if reason.startswith(("symbol:", "contract-evidence:")) and beat_sota2_reason_tokens(reason):
            return True
    return False


def beat_sota2_candidate_level(candidate: dict[str, Any], top_score: int) -> str:
    path = str(candidate.get("path", ""))
    if beat_sota2_is_noisy_precontext_path(path) or not beat_sota2_is_code_precontext_path(path):
        return "weak"
    score = int(candidate.get("score", 0) or 0)
    has_direct_evidence = beat_sota2_has_direct_candidate_evidence(candidate)
    has_supporting_evidence = beat_sota2_has_supporting_candidate_evidence(candidate)
    if any(str(reason).startswith(("exact-path:", "traceback-path")) for reason in candidate.get("reasons", [])):
        return "strong"
    if has_direct_evidence and score >= max(650, int(top_score * 0.72)):
        return "strong"
    if has_supporting_evidence and score >= max(420, int(top_score * 0.45)):
        return "medium"
    return "weak"


def beat_sota2_region_level(
    region_trace: dict[str, Any],
    candidate_level_by_path: dict[str, str],
) -> str:
    path = str(region_trace.get("path", ""))
    candidate_level = candidate_level_by_path.get(path)
    if candidate_level in {"strong", "medium"}:
        return candidate_level
    reasons = [str(reason) for reason in region_trace.get("reasons", [])]
    file_score = int(region_trace.get("file_score", 0) or 0)
    tfidf_only = bool(reasons) and all("tfidf" in reason for reason in reasons)
    if file_score > 0 and not tfidf_only:
        return "medium"
    if file_score > 0:
        return "medium"
    return "weak"


def build_beat_sota2_precontext(
    repo: Path,
    issue_text: str,
    *,
    top_k: int,
    max_files: int,
    max_bytes: int,
    line_window: int,
    line_overlap: int,
    regions_per_file: int,
    expansion_rounds: int,
    precontext_candidates: int,
    precontext_max_chars: int,
    ablations: set[str],
) -> tuple[str, dict[str, Any]]:
    selection = select_context(
        repo,
        issue_text,
        "beat-sota1",
        max_files=max_files,
        max_bytes=max_bytes,
        top_k=top_k,
        line_window=line_window,
        line_overlap=line_overlap,
        beat_sota1_regions_per_file=regions_per_file,
        beat_sota1_expansion_rounds=expansion_rounds,
        ablations=ablations,
    )
    requested_candidates = min(precontext_candidates, DEFAULT_BEAT_SOTA2_PRECONTEXT_CANDIDATES)
    requested_chars = min(precontext_max_chars, DEFAULT_BEAT_SOTA2_PRECONTEXT_MAX_CHARS)
    regions, region_trace = selection_to_regions_beat_sota1(
        repo,
        selection,
        issue_text,
        top_k=max(top_k * 12, requested_candidates * 8),
        line_window=line_window,
        line_overlap=line_overlap,
        regions_per_file=regions_per_file,
    )
    candidate_files = list(selection.get("beat_sota1", {}).get("candidate_files", []))
    top_score = int(candidate_files[0].get("score", 0) or 0) if candidate_files else 0
    candidate_level_by_path = {
        str(candidate.get("path", "")): beat_sota2_candidate_level(candidate, top_score)
        for candidate in candidate_files
    }
    trace_by_region = {
        (item.get("path"), item.get("start"), item.get("end")): item
        for item in region_trace
        if item.get("operation") == "region_rank"
    }

    classified: dict[str, list[tuple[dict[str, int | str], dict[str, Any]]]] = {
        "strong": [],
        "medium": [],
        "weak": [],
    }
    filtered_counts = {"non_code": 0, "noisy_path": 0, "missing_trace": 0}
    for region in regions:
        path = str(region.get("path", ""))
        trace = trace_by_region.get((region.get("path"), region.get("start"), region.get("end")))
        if trace is None:
            filtered_counts["missing_trace"] += 1
            continue
        if not beat_sota2_is_code_precontext_path(path):
            filtered_counts["non_code"] += 1
            continue
        if beat_sota2_is_noisy_precontext_path(path):
            filtered_counts["noisy_path"] += 1
            continue
        level = beat_sota2_region_level(trace, candidate_level_by_path)
        classified[level].append((region, trace))

    usable_count = sum(len(items) for items in classified.values())
    confidence = "low"
    if classified["strong"]:
        confidence = "high" if len(classified["strong"]) >= 2 else "medium"
    elif classified["medium"]:
        confidence = "medium"

    metadata: dict[str, Any] = {
        "strategy": "beat-sota2",
        "resolved_module": selection.get("resolved_module"),
        "included_files": selection.get("included_files", []),
        "selected_bytes": selection.get("selected_bytes", 0),
        "candidate_files": candidate_files[:requested_candidates],
        "candidate_levels": {
            path: level
            for path, level in candidate_level_by_path.items()
            if path in {str(candidate.get("path", "")) for candidate in candidate_files[:requested_candidates]}
        },
        "confidence": confidence,
        "filtered_counts": filtered_counts,
        "usable_region_count": usable_count,
        "trace": [*selection.get("trace", []), *region_trace],
        "beat_sota1": selection.get("beat_sota1", {}),
    }
    if usable_count < 2:
        metadata["omitted"] = "low-confidence-no-usable-code-regions"
        metadata["regions"] = []
        return "", metadata

    selected: list[tuple[str, dict[str, int | str], dict[str, Any]]] = []
    seen_paths: set[str] = set()
    per_level_limits = {"strong": requested_candidates, "medium": max(2, requested_candidates - 1), "weak": 2}
    for level in ("strong", "medium", "weak"):
        level_count = 0
        for region, trace in classified[level]:
            path = str(region.get("path", ""))
            if path in seen_paths:
                continue
            if level_count >= per_level_limits[level]:
                break
            selected.append((level, region, trace))
            seen_paths.add(path)
            level_count += 1
            if len(selected) >= requested_candidates:
                break
        if len(selected) >= requested_candidates:
            break

    if len(selected) < min(2, usable_count):
        for level in ("strong", "medium", "weak"):
            for region, trace in classified[level]:
                key = (str(region.get("path", "")), int(region.get("start", 0)), int(region.get("end", 0)))
                existing_keys = {
                    (str(item[1].get("path", "")), int(item[1].get("start", 0)), int(item[1].get("end", 0)))
                    for item in selected
                }
                if key in existing_keys:
                    continue
                selected.append((level, region, trace))
                if len(selected) >= min(requested_candidates, usable_count):
                    break
            if len(selected) >= min(requested_candidates, usable_count):
                break

    metadata["regions"] = [region for _level, region, _trace in selected]
    lines = [
        "## beatSOTA2 advisory evidence",
        "",
        "Candidate evidence is advisory, not authoritative.",
        "",
        "Independent-first workflow:",
        "1. Inspect the issue and repository structure before trusting these hints.",
        "2. Form your own likely file and region hypotheses.",
        "3. Compare those hypotheses against the evidence groups below.",
        "4. Ignore weak or conflicting candidates when repository evidence points elsewhere.",
        "",
        f"Deterministic confidence: {confidence}",
        f"Resolved module: {selection.get('resolved_module') or 'unknown'}",
        f"Ablations: {', '.join(sorted(ablations)) if ablations else 'none'}",
        "",
    ]
    used_chars = len("\n".join(lines))
    current_level = None
    for index, (level, region, trace) in enumerate(selected, start=1):
        if current_level != level:
            lines.extend(["", f"### {level.title()} evidence"])
            current_level = level
        reason_text = ",".join(str(reason) for reason in trace.get("reasons", [])[:4])
        header = (
            f"{index}. {region['path']}:{region['start']}-{region['end']} "
            f"score={trace.get('score', 'n/a')} file_prior={trace.get('file_score', 'n/a')} "
            f"evidence={reason_text}"
        )
        snippet = snippet_for_region(repo, region, max_chars=1_600)
        block = "\n".join([header, "```", snippet, "```", ""])
        if used_chars + len(block) > requested_chars:
            lines.append("[candidate snippets truncated by precontext budget]")
            break
        lines.append(block)
        used_chars += len(block)
    return "\n".join(lines), metadata


def build_codex_prompt(
    *,
    instance_id: str,
    issue_text: str,
    strategy: str,
    top_k: int,
    line_window: int,
    precontext: str | None,
) -> str:
    precontext_block = ""
    if precontext:
        guidance = "Use the following agent-contracts context as a starting map. You may inspect the repository further before deciding the final ranked regions."
        if strategy == "codex-beat-sota1":
            guidance = (
                "Use the following beatSOTA1 scored candidates as the primary evidence set. "
                "Prefer reranking or narrowing these regions; replace a region only when repository evidence points elsewhere."
            )
        elif strategy == "codex-context-localized":
            guidance = (
                "Use the following context-localized Agent Contracts evidence only after forming independent hypotheses "
                "from the issue and repository. Respect tool-only or abstain gates by inspecting the repository directly."
            )
        elif strategy == "codex-beat-sota2":
            guidance = (
                "Use the following beatSOTA2 advisory evidence only after forming independent hypotheses from the issue "
                "and repository. Treat weak candidates as possible retrieval noise, and prefer repository evidence over "
                "the precontext whenever they disagree."
            )
        precontext_block = (
            f"\n{guidance}\n\n"
            f"{precontext}\n"
        )
    return f"""You are a SWE-Explore repository context explorer.

Task:
Given the issue below and the current repository snapshot, return the most useful code context as ranked file/line regions.

Instance: {instance_id}
Condition: {strategy}

Issue:
{issue_text}
{precontext_block}
Rules:
- Do not edit files.
- Inspect only this repository snapshot.
- Return at most {top_k} regions.
- Each region must be a repository-relative path with 1-based inclusive start/end lines.
- Prefer windows of at most {line_window} lines.
- Rank the most useful region first.
- Return only valid JSON, with this exact shape:
  {{"regions":[{{"path":"relative/path.py","start":1,"end":40,"reason":"short reason"}}]}}
"""


def format_codex_command(template: str, *, repo: Path, response_file: Path, instance_id: str) -> list[str]:
    values = {
        "repo": repo.as_posix(),
        "response_file": response_file.as_posix(),
        "instance_id": instance_id,
    }
    return shlex.split(template.format(**values))


def decode_first_json(value: str) -> Any:
    stripped = value.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.S | re.I)
    candidates = [stripped]
    if fence:
        candidates.insert(0, fence.group(1).strip())
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for index, char in enumerate(candidate):
            if char not in "[{":
                continue
            try:
                payload, _end = decoder.raw_decode(candidate[index:])
                return payload
            except json.JSONDecodeError:
                continue
    raise ValueError("No JSON object or array found in Codex response.")


def validate_agent_regions(repo: Path, payload: Any, *, top_k: int, line_window: int) -> list[dict[str, int | str]]:
    raw_regions = payload
    if isinstance(payload, dict):
        raw_regions = payload.get("regions", [])
    if not isinstance(raw_regions, list):
        raise ValueError("Codex response must contain a regions list.")

    regions: list[dict[str, int | str]] = []
    seen: set[tuple[str, int, int]] = set()
    repo_root = repo.resolve()
    for raw in raw_regions:
        if not isinstance(raw, dict):
            continue
        raw_path = raw.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        path = agent_contracts.normalize_context_path(raw_path)
        if not path or path.startswith("../") or Path(path).is_absolute():
            continue
        file_path = (repo / path).resolve()
        try:
            file_path.relative_to(repo_root)
        except ValueError:
            continue
        if not file_path.is_file():
            continue
        try:
            start = int(raw.get("start", 1))
            end = int(raw.get("end", start))
        except (TypeError, ValueError):
            continue
        total_lines = line_count(repo, path)
        if total_lines <= 0:
            continue
        start = max(1, min(start, total_lines))
        end = max(start, min(end, total_lines))
        if end - start + 1 > line_window:
            end = min(total_lines, start + line_window - 1)
        key = (path, start, end)
        if key in seen:
            continue
        seen.add(key)
        regions.append({"path": path, "start": start, "end": end})
        if len(regions) >= top_k:
            break
    return regions


def run_codex_explorer(
    record: dict[str, Any],
    issue_text: str,
    repo: Path,
    strategy: str,
    *,
    top_k: int,
    max_files: int,
    max_bytes: int,
    line_window: int,
    line_overlap: int,
    codex_command: str,
    codex_timeout: int,
    beat_sota1_regions_per_file: int,
    beat_sota1_expansion_rounds: int,
    beat_sota1_precontext_candidates: int,
    beat_sota1_precontext_max_chars: int,
    ablations: set[str],
    model_profile: str | dict[str, Any] | None = "unknown",
) -> dict[str, Any]:
    profile = agent_contracts.model_profile_payload(model_profile)
    if strategy in {"codex-beat-sota1", "codex-beat-sota2"} and "no-codex" in ablations:
        row = build_output_row(
            record,
            issue_text,
            repo,
            "beat-sota1",
            top_k=top_k,
            max_files=max_files,
            max_bytes=max_bytes,
            line_window=line_window,
            line_overlap=line_overlap,
            beat_sota1_regions_per_file=beat_sota1_regions_per_file,
            beat_sota1_expansion_rounds=beat_sota1_expansion_rounds,
            ablations=ablations,
            model_profile=profile,
        )
        row["explorer"] = f"agent-contracts:{strategy}"
        row["metadata"]["condition"] = strategy
        row["metadata"]["runner_error"] = None
        row["metadata"]["codex_skipped"] = "no-codex-ablation"
        return row

    precontext = None
    precontext_metadata = None
    if strategy == "codex-agent-contracts":
        precontext, precontext_metadata = build_agent_contracts_precontext(
            repo,
            issue_text,
            top_k=top_k,
            max_files=max_files,
            max_bytes=max_bytes,
            line_window=line_window,
            line_overlap=line_overlap,
        )
    elif strategy == "codex-context-localized":
        precontext, precontext_metadata = build_context_localized_precontext(
            repo,
            issue_text,
            top_k=top_k,
            max_files=max_files,
            max_bytes=max_bytes,
            line_window=line_window,
            precontext_candidates=beat_sota1_precontext_candidates,
            precontext_max_chars=beat_sota1_precontext_max_chars,
            model_profile=profile,
        )
    elif strategy == "codex-beat-sota1":
        precontext, precontext_metadata = build_beat_sota1_precontext(
            repo,
            issue_text,
            top_k=top_k,
            max_files=max_files,
            max_bytes=max_bytes,
            line_window=line_window,
            line_overlap=line_overlap,
            regions_per_file=beat_sota1_regions_per_file,
            expansion_rounds=beat_sota1_expansion_rounds,
            precontext_candidates=beat_sota1_precontext_candidates,
            precontext_max_chars=beat_sota1_precontext_max_chars,
            ablations=ablations,
        )
    elif strategy == "codex-beat-sota2":
        precontext, precontext_metadata = build_beat_sota2_precontext(
            repo,
            issue_text,
            top_k=top_k,
            max_files=max_files,
            max_bytes=max_bytes,
            line_window=line_window,
            line_overlap=line_overlap,
            regions_per_file=beat_sota1_regions_per_file,
            expansion_rounds=beat_sota1_expansion_rounds,
            precontext_candidates=beat_sota1_precontext_candidates,
            precontext_max_chars=beat_sota1_precontext_max_chars,
            ablations=ablations,
        )

    instance_id = str(record["instance_id"])
    prompt = build_codex_prompt(
        instance_id=instance_id,
        issue_text=issue_text,
        strategy=strategy,
        top_k=top_k,
        line_window=line_window,
        precontext=precontext,
    )
    completed = None
    response_text = ""
    with tempfile.TemporaryDirectory(prefix="agent-contracts-codex-swe-") as tmp:
        response_file = Path(tmp) / "codex-response.txt"
        try:
            command = format_codex_command(
                codex_command,
                repo=repo,
                response_file=response_file,
                instance_id=instance_id,
            )
            completed = subprocess.run(
                command,
                cwd=repo,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=codex_timeout,
                check=False,
            )
            response_text = response_file.read_text(encoding="utf-8") if response_file.is_file() else ""
            if not response_text.strip():
                response_text = completed.stdout
            payload = decode_first_json(response_text)
            regions = validate_agent_regions(repo, payload, top_k=top_k, line_window=line_window)
            runner_error = None
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            regions = []
            runner_error = str(exc)

    metadata = {
        "condition": strategy,
        "model_profile": profile,
        "codex_command": codex_command,
        "codex_returncode": completed.returncode if completed is not None else None,
        "runner_error": runner_error,
        "limits": {
            "top_k": top_k,
            "max_files": max_files,
            "max_bytes": max_bytes,
            "line_window": line_window,
            "line_overlap": line_overlap,
            "codex_timeout": codex_timeout,
            "beat_sota1_regions_per_file": beat_sota1_regions_per_file,
            "beat_sota1_expansion_rounds": beat_sota1_expansion_rounds,
            "beat_sota1_precontext_candidates": beat_sota1_precontext_candidates,
            "beat_sota1_precontext_max_chars": beat_sota1_precontext_max_chars,
            "ablations": sorted(ablations),
        },
        "allowed_inputs": [
            "base_repo_snapshot",
            "issue_text",
            *(
                ["agent_contracts_precontext"]
                if strategy == "codex-agent-contracts"
                else []
            ),
            *(
                ["context_localized_precontext"]
                if strategy == "codex-context-localized"
                else []
            ),
            *(
                ["beat_sota1_precontext"]
                if strategy == "codex-beat-sota1"
                else []
            ),
            *(
                ["beat_sota2_precontext"]
                if strategy == "codex-beat-sota2" and precontext
                else []
            ),
        ],
        "precontext": precontext_metadata,
        "stdout_excerpt": (completed.stdout[-4_000:] if completed is not None else ""),
        "stderr_excerpt": (completed.stderr[-4_000:] if completed is not None else ""),
        "response_excerpt": response_text[-4_000:],
    }
    return {
        "instance_id": instance_id,
        "explorer": f"agent-contracts:{strategy}",
        "regions": regions,
        "num_regions": len(regions),
        "metadata": metadata,
    }


def run(args: argparse.Namespace) -> int:
    bench_path = args.bench.resolve()
    repos_root = args.repos.resolve()
    output_path = args.output.resolve()
    issue_map = load_issue_map(args.issue_map.resolve())
    records = load_jsonl(bench_path)
    if args.limit is not None:
        records = records[: args.limit]

    completed = completed_instance_ids(output_path) if args.resume else set()
    output_rows: list[dict[str, Any]] = []
    skipped_missing_repo = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not args.evaluate and not args.resume:
        output_path.write_text("", encoding="utf-8")
    written = 0

    for record in records:
        instance_id = str(record.get("instance_id", ""))
        if not instance_id:
            continue
        if instance_id in completed:
            continue
        repo = resolve_repo_dir(record, repos_root)
        if repo is None:
            skipped_missing_repo += 1
            continue
        issue_text = issue_map.get(instance_id) or str(record.get("problem_statement", ""))
        if args.strategy in AGENT_STRATEGIES:
            row = run_codex_explorer(
                record,
                issue_text,
                repo,
                args.strategy,
                top_k=args.top_k,
                max_files=args.max_files,
                max_bytes=args.max_bytes,
                line_window=args.line_window,
                line_overlap=args.line_overlap,
                codex_command=args.codex_command,
                codex_timeout=args.codex_timeout,
                beat_sota1_regions_per_file=args.beat_sota1_regions_per_file,
                beat_sota1_expansion_rounds=args.beat_sota1_expansion_rounds,
                beat_sota1_precontext_candidates=args.beat_sota1_precontext_candidates,
                beat_sota1_precontext_max_chars=args.beat_sota1_precontext_max_chars,
                ablations=set(args.ablation or []),
                model_profile=args.model_profile,
            )
            if args.evaluate:
                output_rows.append(row)
            else:
                append_jsonl_row(output_path, row)
                written += 1
            continue
        row = build_output_row(
            record,
            issue_text,
            repo,
            args.strategy,
            top_k=args.top_k,
            max_files=args.max_files,
            max_bytes=args.max_bytes,
            line_window=args.line_window,
            line_overlap=args.line_overlap,
            beat_sota1_regions_per_file=args.beat_sota1_regions_per_file,
            beat_sota1_expansion_rounds=args.beat_sota1_expansion_rounds,
            ablations=set(args.ablation or []),
            model_profile=args.model_profile,
        )
        if args.evaluate:
            output_rows.append(row)
        else:
            append_jsonl_row(output_path, row)
            written += 1

    aggregate_metrics = None
    if args.evaluate and output_rows:
        aggregate_metrics = add_metrics(bench_path, records, repos_root, output_rows)

    if args.evaluate:
        mode = "a" if args.resume else "w"
        with output_path.open(mode, encoding="utf-8") as handle:
            for row in output_rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        written = len(output_rows)

    print(
        json.dumps(
            {
                "output": output_path.as_posix(),
                "strategy": args.strategy,
                "model_profile": args.model_profile,
                "written": written,
                "resumed": len(completed),
                "skipped_missing_repo": skipped_missing_repo,
                "aggregate_metrics": aggregate_metrics,
            },
            sort_keys=True,
        )
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit SWE-Explore ranked line regions using agent-contracts context selection."
    )
    parser.add_argument("--bench", required=True, type=Path, help="SWE-Explore benchmark JSONL.")
    parser.add_argument("--repos", required=True, type=Path, help="Repository snapshot root.")
    parser.add_argument("--issue-map", required=True, type=Path, help="JSON {instance_id: issue_text}.")
    parser.add_argument("--output", required=True, type=Path, help="Output JSONL path.")
    parser.add_argument("--top-k", type=int, default=5, help="Maximum ranked regions per instance.")
    parser.add_argument("--strategy", choices=STRATEGIES, default="contract-ranked")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--model-profile", choices=agent_contracts.MODEL_PROFILE_NAMES, default="unknown")
    parser.add_argument("--line-window", type=int, default=80)
    parser.add_argument("--line-overlap", type=int, default=20)
    parser.add_argument("--beat-sota1-regions-per-file", type=int, default=DEFAULT_BEAT_SOTA1_REGIONS_PER_FILE)
    parser.add_argument("--beat-sota1-expansion-rounds", type=int, default=DEFAULT_BEAT_SOTA1_EXPANSION_ROUNDS)
    parser.add_argument("--beat-sota1-precontext-candidates", type=int, default=DEFAULT_BEAT_SOTA1_PRECONTEXT_CANDIDATES)
    parser.add_argument("--beat-sota1-precontext-max-chars", type=int, default=DEFAULT_BEAT_SOTA1_PRECONTEXT_MAX_CHARS)
    parser.add_argument("--ablation", action="append", choices=BEAT_SOTA1_ABLATIONS, help="Disable one beatSOTA1 component; repeat for multiple ablations.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true", help="Append only instances not already present in output.")
    parser.add_argument("--evaluate", action="store_true", help="Attach metrics using SWE-Explore eval.py if available.")
    parser.add_argument(
        "--codex-command",
        default=DEFAULT_CODEX_COMMAND,
        help=(
            "Command template for codex-backed strategies. Supports {repo}, "
            "{response_file}, and {instance_id} placeholders."
        ),
    )
    parser.add_argument("--codex-timeout", type=int, default=DEFAULT_CODEX_TIMEOUT)
    args = parser.parse_args(argv)
    if args.top_k < 1:
        parser.error("--top-k must be >= 1")
    if args.max_files < 1:
        parser.error("--max-files must be >= 1")
    if args.max_bytes < 1:
        parser.error("--max-bytes must be >= 1")
    if args.line_window < 1:
        parser.error("--line-window must be >= 1")
    if args.line_overlap < 0:
        parser.error("--line-overlap must be >= 0")
    if args.line_overlap >= args.line_window:
        parser.error("--line-overlap must be smaller than --line-window")
    if args.beat_sota1_regions_per_file < 1:
        parser.error("--beat-sota1-regions-per-file must be >= 1")
    if args.beat_sota1_expansion_rounds < 0:
        parser.error("--beat-sota1-expansion-rounds must be >= 0")
    if args.beat_sota1_precontext_candidates < 1:
        parser.error("--beat-sota1-precontext-candidates must be >= 1")
    if args.beat_sota1_precontext_max_chars < 1:
        parser.error("--beat-sota1-precontext-max-chars must be >= 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    if args.codex_timeout < 1:
        parser.error("--codex-timeout must be >= 1")
    return args


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
