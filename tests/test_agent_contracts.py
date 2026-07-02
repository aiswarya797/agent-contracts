from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from pathlib import Path

from scripts import agent_contracts, agent_contracts_mcp, quick_swe_spark_eval, swe_explore_agent_contracts


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agent_contracts.py"
SWE_SCRIPT = ROOT / "scripts" / "swe_explore_agent_contracts.py"
QUICK_SPARK_SCRIPT = ROOT / "scripts" / "quick_swe_spark_eval.py"
REPAIR_SCRIPT = ROOT / "scripts" / "swe_restricted_repair.py"
VEXP_AGENT_CONTRACTS_ADAPTER = (
    ROOT
    / "benchmark-results"
    / "vexp-swebench-agent-contracts"
    / "vexp-swe-bench"
    / "src"
    / "agents"
    / "codex-agent-contracts.ts"
)
VEXP_AGENT_REGISTRY = (
    ROOT
    / "benchmark-results"
    / "vexp-swebench-agent-contracts"
    / "vexp-swe-bench"
    / "src"
    / "agents"
    / "registry.ts"
)

REQUESTS_NO_HARM_CASES = [
    {
        "instance_id": "psf__requests-1142",
        "expected": "requests/models.py",
        "problem": """\
requests.get is ALWAYS sending content length
Hi,

It seems like that request.get always adds 'content-length' header to the request.
I think that the right behavior is not to add this header automatically in GET requests or add the possibility to not send it.

For example http://amazon.com returns 503 for every get request that contains 'content-length' header.

Thanks,

Oren
""",
    },
    {
        "instance_id": "psf__requests-1921",
        "expected": "requests/sessions.py",
        "problem": """\
Removing a default header of a session
[The docs](http://docs.python-requests.org/en/latest/user/advanced/#session-objects) say that you can prevent sending a session header by setting the headers value to None in the method's arguments. You would expect (as [discussed on IRC](https://botbot.me/freenode/python-requests/msg/10788170/)) that this would work for session's default headers, too:

``` python
session = requests.Session()
# Do not send Accept-Encoding
session.headers['Accept-Encoding'] = None
```

What happens is that "None"  gets sent as the value of header.

```
Accept-Encoding: None
```

For the reference, here is a way that works:

``` python
del session.headers['Accept-Encoding']
```
""",
    },
    {
        "instance_id": "psf__requests-5414",
        "expected": "requests/models.py",
        "problem": """\
Getting http://.example.com raises UnicodeError
Attempting to get e.g. `http://.example.com` results in a `UnicodeError`. It seems like the intention so far has been to raise `InvalidUrl` instead (see e.g. [this line](https://github.com/psf/requests/blob/ca6f9af5dba09591007b15a7368bc0f006b7cc50/requests/models.py#L401)).

I see there was some hesitation in fixing a similar issue (#4168) and would like to add that even catching the error just to rethrow as a requests exception would be beneficial.

## Expected Result

Based on PR #774: `InvalidUrl: URL has an invalid label.`

## Actual Result

`UnicodeError: encoding with 'idna' codec failed (UnicodeError: label empty or too long)`

## Reproduction Steps

```python3
import requests
requests.get("http://.example.com")
```

## System Information

    $ python -m requests.help

```
{
  "chardet": {
    "version": "3.0.4"
  },
  "cryptography": {
    "version": "2.8"
  },
  "idna": {
    "version": "2.8"
  },
  "implementation": {
    "name": "CPython",
    "version": "3.8.0"
  },
  "platform": {
    "release": "5.3.0-40-generic",
    "system": "Linux"
  },
  "pyOpenSSL": {
    "openssl_version": "1010104f",
    "version": "19.1.0"
  },
  "requests": {
    "version": "2.23.0"
  },
  "system_ssl": {
    "version": "1010103f"
  },
  "urllib3": {
    "version": "1.25.8"
  },
  "using_pyopenssl": true
}
```
""",
    },
]

REQUESTS_NO_HARM_GATE_EXPECTATIONS = {
    "psf__requests-1142": ("advisory", "medium"),
    "psf__requests-1921": ("advisory", "medium"),
    "psf__requests-5414": ("inject", "high"),
}


def run_cli(*args: str, repo: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), *args]
    if repo is not None:
        command.extend(["--repo", str(repo)])
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def fake_agent_command(name: str) -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'tests' / 'fixtures' / name))}"


def write_tiny_swe_explore_fixture(base: Path) -> tuple[Path, Path, Path, Path]:
    repo = base / "repos" / "demo__repo-1"
    source = repo / "src" / "billing" / "api.py"
    tests = repo / "tests" / "test_billing.py"
    source.parent.mkdir(parents=True)
    tests.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                "from auth.token import validate_token",
                "",
                "class PaymentStatus:",
                "    pass",
                "",
                "def helper():",
                "    return 'unchanged'",
                "",
                "def payment_status(user_token):",
                "    token_state = validate_token(user_token)",
                "    if token_state.renewal_required:",
                "        return 'renewal-required'",
                "    return 'paid'",
                "",
                "def unrelated_report():",
                "    return 'ok'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "src" / "auth").mkdir(parents=True)
    (repo / "src" / "auth" / "token.py").write_text(
        "def validate_token(value):\n    return value\n",
        encoding="utf-8",
    )
    tests.write_text(
        "\n".join(
            [
                "from billing.api import payment_status",
                "",
                "def test_payment_status_renewal_required():",
                "    assert payment_status('needs-renewal') == 'renewal-required'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "README.md").write_text("Billing service demo.\n", encoding="utf-8")

    bench = base / "bench.jsonl"
    bench.write_text(
        json.dumps(
            {
                "instance_id": "demo__repo-1",
                "repo_dir": "repos/demo__repo-1",
                "ground_truth": {
                    "read_core_files": ["src/billing/ground_truth_only.py"],
                    "read_core_regions": [{"path": "src/billing/ground_truth_only.py", "start": 1, "end": 2}],
                    "read_optional_files_map": {},
                    "read_optional_regions_map": {},
                    "main_files": ["src/billing/ground_truth_only.py"],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    issue_map = base / "issue_map.json"
    issue_map.write_text(
        json.dumps(
            {
                "demo__repo-1": "Payment status should validate the user token and report renewal-required state."
            }
        ),
        encoding="utf-8",
    )
    output = base / "results.jsonl"
    return bench, base, issue_map, output


def write_context_localization_fixture(base: Path) -> Path:
    repo = base / "repo"
    (repo / "src" / "billing").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    (repo / "vendor" / "billing").mkdir(parents=True)
    (repo / "requests" / "packages" / "urllib3").mkdir(parents=True)
    (repo / "requests" / "packages" / "chardet").mkdir(parents=True)
    (repo / "src" / "billing" / "api.py").write_text(
        "\n".join(
            [
                "class PaymentStatus:",
                "    pass",
                "",
                "def helper():",
                "    return 'unchanged'",
                "",
                "def payment_status(user_token):",
                "    token_state = validate_token(user_token)",
                "    if token_state.renewal_required:",
                "        return 'renewal-required'",
                "    return 'paid'",
                "",
                "def validate_hostname(hostname):",
                "    if hostname == 'localhost':",
                "        return True",
                "    return hostname.endswith('.example.test')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_billing.py").write_text(
        "\n".join(
            [
                "from billing.api import payment_status, validate_hostname",
                "",
                "def test_payment_status_renewal_required():",
                "    assert payment_status('needs-renewal') == 'renewal-required'",
                "",
                "def test_validate_hostname_localhost():",
                "    assert validate_hostname('localhost')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "docs" / "billing.md").write_text(
        "payment status renewal required hostname validation billing billing billing\n" * 12,
        encoding="utf-8",
    )
    (repo / "vendor" / "billing" / "legacy.py").write_text(
        "def payment_status(user_token):\n    return 'vendor renewal-required'\n",
        encoding="utf-8",
    )
    (repo / "requests" / "packages" / "urllib3" / "connectionpool.py").write_text(
        "def payment_status(user_token):\n"
        "    # vendored dependency copy mentioning renewal required hostname validation\n"
        "    return 'urllib3 renewal-required hostname validation'\n",
        encoding="utf-8",
    )
    (repo / "requests" / "packages" / "chardet" / "compat.py").write_text(
        "def validate_hostname(hostname):\n"
        "    return hostname.endswith('.vendored.test')\n",
        encoding="utf-8",
    )
    return repo


def write_requests_noisy_html_fixture(base: Path) -> Path:
    repo = base / "requests-repo"
    (repo / "requests").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "docs" / "_templates").mkdir(parents=True)
    (repo / "requests" / "models.py").write_text(
        "\n".join(
            [
                "class InvalidURL(Exception):",
                "    pass",
                "",
                "class PreparedRequest:",
                "    def prepare_url(self, url, params):",
                "        host = url.split('://', 1)[-1].split('/', 1)[0]",
                "        if not host:",
                "            raise InvalidURL('No host supplied')",
                "        if host.startswith('*'):",
                "            raise InvalidURL('URL has an invalid label.')",
                "        try:",
                "            return self._get_idna_encoded_host(host)",
                "        except UnicodeError:",
                "            raise InvalidURL('URL has an invalid label.')",
                "",
                "    def _get_idna_encoded_host(self, host):",
                "        return host.encode('idna').decode('ascii')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "tests" / "test_requests.py").write_text(
        "\n".join(
            [
                "from requests.models import InvalidURL, PreparedRequest",
                "",
                "def test_invalid_url_leading_dot():",
                "    with pytest.raises(InvalidURL):",
                "        PreparedRequest().prepare_url('http://.example.com', None)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "docs" / "_templates" / "sidebarintro.html").write_text(
        ("<p>requests requests request session adapter response url invalid label help</p>\n" * 40),
        encoding="utf-8",
    )
    return repo


def write_requests_no_harm_fixture(base: Path) -> Path:
    repo = base / "requests-no-harm"
    (repo / "requests" / "packages" / "urllib3").mkdir(parents=True)
    (repo / "requests" / "packages" / "chardet").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "docs" / "html").mkdir(parents=True)
    (repo / "requests" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "requests" / "models.py").write_text(
        textwrap.dedent(
            """
            class InvalidURL(Exception):
                pass


            class PreparedRequest:
                def __init__(self):
                    self.headers = {}
                    self.method = "GET"

                def prepare_method(self, method):
                    self.method = (method or "GET").upper()

                def prepare_url(self, url, params):
                    host = url.split("://", 1)[-1].split("/", 1)[0]
                    if not host:
                        raise InvalidURL("No host supplied")
                    try:
                        return self._get_idna_encoded_host(host)
                    except UnicodeError:
                        raise InvalidURL("URL has an invalid label.")

                def _get_idna_encoded_host(self, host):
                    return host.encode("idna").decode("ascii")

                def prepare_headers(self, headers):
                    self.headers.update(headers or {})

                def prepare_body(self, data, files, json=None):
                    body = data or files or json
                    self.prepare_content_length(body)

                def prepare_content_length(self, body):
                    if body is not None:
                        self.headers["Content-Length"] = str(len(body))
                    elif self.method not in ("GET", "HEAD") and "Content-Length" not in self.headers:
                        self.headers["Content-Length"] = "0"
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (repo / "requests" / "sessions.py").write_text(
        textwrap.dedent(
            """
            from .models import PreparedRequest
            from .structures import CaseInsensitiveDict


            def default_headers():
                return CaseInsensitiveDict({"Accept-Encoding": "gzip, deflate", "User-Agent": "python-requests"})


            def merge_setting(request_setting, session_setting, dict_class=CaseInsensitiveDict):
                if session_setting is None:
                    return request_setting
                if request_setting is None:
                    return session_setting
                merged_setting = dict_class(session_setting)
                merged_setting.update(request_setting)
                none_keys = [key for key, value in merged_setting.items() if value is None]
                for key in none_keys:
                    del merged_setting[key]
                return merged_setting


            class Session:
                def __init__(self):
                    self.headers = default_headers()

                def prepare_request(self, request):
                    prepared = PreparedRequest()
                    headers = merge_setting(request.headers, self.headers, dict_class=CaseInsensitiveDict)
                    prepared.prepare_headers(headers)
                    return prepared
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (repo / "requests" / "structures.py").write_text(
        textwrap.dedent(
            """
            class CaseInsensitiveDict(dict):
                def __setitem__(self, key, value):
                    dict.__setitem__(self, key.lower(), value)

                def __getitem__(self, key):
                    return dict.__getitem__(self, key.lower())

                def copy(self):
                    return CaseInsensitiveDict(self)


            HEADER_NAMES = ["Content-Length", "Accept-Encoding", "User-Agent"]
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (repo / "requests" / "help.py").write_text(
        "SYSTEM_INFO_KEYS = ['chardet', 'idna', 'urllib3', 'platform', 'python', 'cryptography']\n",
        encoding="utf-8",
    )
    (repo / "requests" / "packages" / "urllib3" / "connectionpool.py").write_text(
        "DEFAULT_ACCEPT_ENCODING = 'gzip, deflate'\n# urllib3 connection pool mentions Content-Length and headers repeatedly.\n",
        encoding="utf-8",
    )
    (repo / "requests" / "packages" / "chardet" / "compat.py").write_text(
        "def detect(value):\n    return {'encoding': 'utf-8', 'confidence': 0.99}\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_requests.py").write_text(
        textwrap.dedent(
            """
            import pytest
            from requests.models import InvalidURL, PreparedRequest
            from requests.sessions import Session


            def test_get_does_not_send_content_length():
                request = PreparedRequest()
                request.prepare_method("GET")
                request.prepare_content_length(None)
                assert "Content-Length" not in request.headers


            def test_session_header_none_removes_default():
                session = Session()
                session.headers["Accept-Encoding"] = None
                assert "Accept-Encoding" not in session.headers


            def test_invalid_url_label_raises_invalid_url():
                with pytest.raises(InvalidURL):
                    PreparedRequest().prepare_url("http://.example.com", None)
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (repo / "docs" / "html" / "api.html").write_text(
        ("requests session headers Accept-Encoding Content-Length InvalidUrl UnicodeError help urllib3 idna\n" * 35),
        encoding="utf-8",
    )
    (repo / "setup.cfg").write_text("[metadata]\nname = requests\n", encoding="utf-8")
    return repo


class AgentContractsTests(unittest.TestCase):
    def test_doctor_reports_plugin_ready(self) -> None:
        result = run_cli("doctor", "--format", "json", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["summary"]["blockers"], 0)
        self.assertTrue(data["local_only"])

    def test_utc_now_uses_python_310_compatible_timezone(self) -> None:
        self.assertRegex(agent_contracts.utc_now(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_map_detects_python_modules(self) -> None:
        result = run_cli("map", "--format", "json", repo=ROOT / "fixtures" / "python-service")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        modules = {module["name"]: module for module in data["modules"]}
        names = set(modules)
        self.assertIn("billing", names)
        self.assertIn("auth", names)
        self.assertIn("tests/test_billing.py", modules["billing"]["test_files"])

    def test_map_detects_mixed_monorepo_dependency(self) -> None:
        result = run_cli("map", "--format", "json", repo=ROOT / "fixtures" / "mixed-monorepo")
        self.assertEqual(result.returncode, 0, result.stderr)
        modules = {module["name"]: module for module in json.loads(result.stdout)["modules"]}
        self.assertIn("shared", modules["web"]["dependencies"])
        self.assertIn("tests/web.test.ts", modules["web"]["test_files"])

    def test_map_deduplicates_ambiguous_module_names(self) -> None:
        result = run_cli("map", "--format", "json", repo=ROOT / "fixtures" / "ambiguous-modules")
        self.assertEqual(result.returncode, 0, result.stderr)
        modules = json.loads(result.stdout)["modules"]
        names = [module["name"] for module in modules]
        roots = {module["root"] for module in modules}
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("apps/api", roots)
        self.assertIn("packages/api", roots)

    def test_check_reports_broken_fixture_drift(self) -> None:
        result = run_cli("check", "--format", "json", repo=ROOT / "fixtures" / "broken-drift")
        self.assertEqual(result.returncode, 0, result.stderr)
        codes = {finding["code"] for finding in json.loads(result.stdout)["findings"]}
        self.assertIn("internal-import", codes)
        self.assertIn("undeclared-dependency", codes)
        self.assertIn("undeclared-public-surface", codes)

    def test_init_writes_new_contract_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            result = run_cli("init", "--write", "--yes", repo=target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / "ARCHITECTURE.md").exists())
            self.assertTrue((target / "AGENTS.md").exists())
            self.assertTrue((target / "src" / "billing" / "SPEC.md").exists())
            self.assertTrue((target / ".agent-contracts" / "module-map.json").exists())

    def test_init_preserves_existing_docs_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "existing-docs", target)
            original_agents = (target / "AGENTS.md").read_text(encoding="utf-8")
            original_architecture = (target / "ARCHITECTURE.md").read_text(encoding="utf-8")

            result = run_cli("init", "--write", "--yes", repo=target)
            self.assertEqual(result.returncode, 0, result.stderr)

            self.assertEqual((target / "AGENTS.md").read_text(encoding="utf-8"), original_agents)
            self.assertEqual((target / "ARCHITECTURE.md").read_text(encoding="utf-8"), original_architecture)
            self.assertTrue((target / "src" / "catalog" / "SPEC.md").exists())
            self.assertTrue((target / "src" / "catalog" / "AGENTS.md").exists())

    def test_init_overwrites_existing_docs_only_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "existing-docs", target)

            result = run_cli("init", "--write", "--yes", "--overwrite-existing", repo=target)
            self.assertEqual(result.returncode, 0, result.stderr)

            self.assertIn("Generated by agent-contracts", (target / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertIn("Generated by agent-contracts", (target / "ARCHITECTURE.md").read_text(encoding="utf-8"))

    def test_context_pack_writes_bounded_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            init_result = run_cli("init", "--write", "--yes", repo=target)
            self.assertEqual(init_result.returncode, 0, init_result.stderr)
            output = Path(tmp) / "pack"
            result = run_cli("context-pack", "billing", "--output", str(output), repo=target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "README.md").exists())
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["module"]["name"], "billing")
            self.assertIn("src/billing/api.py", manifest["included_files"])

    def test_issue_signal_extractor_captures_paths_tracebacks_symbols_and_terms(self) -> None:
        signals = agent_contracts.extract_issue_signals(
            "Fix `renewal-required` in src/billing/api.py:9. Traceback File \"src/billing/api.py\", "
            "line 9, in payment_status. test_payment_status_renewal_required should not regress."
        )

        self.assertEqual(signals.intent, "bugfix")
        self.assertIn("src/billing/api.py", signals.path_hints)
        self.assertEqual(signals.traceback_lines["src/billing/api.py"], [9])
        self.assertIn("payment_status", signals.symbols)
        self.assertIn("test_payment_status_renewal_required", signals.test_names)
        self.assertIn("renewal-required", signals.quoted_terms)
        self.assertIn("should not", signals.negative_terms)

    def test_context_localizer_prioritizes_exact_path_symbol_and_traceback_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            result = agent_contracts.localize_issue_context(
                repo,
                "Fix src/billing/api.py:9 payment_status returning `renewal-required`.",
                max_candidate_files=8,
                max_regions=4,
                line_window=4,
            )

        self.assertEqual(result["file_candidates"][0]["path"], "src/billing/api.py")
        evidence_kinds = {item["kind"] for item in result["file_candidates"][0]["evidence"]}
        self.assertIn("exact_path", evidence_kinds)
        self.assertIn("traceback", evidence_kinds)
        self.assertIn("symbol", evidence_kinds)
        self.assertEqual(result["confidence"], "high")
        self.assertTrue(any(region["path"] == "src/billing/api.py" and region["start"] <= 9 <= region["end"] for region in result["regions"]))

    def test_context_localizer_keeps_exact_path_region_without_line_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            result = agent_contracts.localize_issue_context(
                repo,
                "Fix src/billing/api.py",
                max_candidate_files=4,
                max_regions=1,
                line_window=4,
            )

        self.assertEqual(result["regions"][0]["path"], "src/billing/api.py")
        self.assertEqual(result["regions"][0]["strength"], "strong")
        self.assertIn("exact_path", {item["kind"] for item in result["regions"][0]["evidence"]})

    def test_context_localizer_demotes_noisy_paths_unless_directly_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            result = agent_contracts.localize_issue_context(
                repo,
                "Payment status renewal required hostname validation bug.",
                max_candidate_files=8,
                max_regions=4,
                line_window=6,
            )
            direct_vendor = agent_contracts.localize_issue_context(
                repo,
                "Fix vendor/billing/legacy.py payment_status renewal-required.",
                max_candidate_files=8,
                max_regions=4,
                line_window=6,
            )

        self.assertEqual(result["file_candidates"][0]["path"], "src/billing/api.py")
        doc_candidate = next((item for item in result["file_candidates"] if item["path"] == "docs/billing.md"), None)
        if doc_candidate is not None:
            self.assertIn("docs", doc_candidate["risk_flags"])
            self.assertLess(doc_candidate["score"], result["file_candidates"][0]["score"])
        self.assertEqual(direct_vendor["file_candidates"][0]["path"], "vendor/billing/legacy.py")
        self.assertIn("vendor_generated_static", direct_vendor["file_candidates"][0]["risk_flags"])

    def test_context_localizer_demotes_requests_style_vendored_dependencies_unless_directly_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            result = agent_contracts.localize_issue_context(
                repo,
                "payment_status renewal required hostname validation bug.",
                max_candidate_files=20,
                max_regions=6,
                line_window=6,
            )
            direct_dependency = agent_contracts.localize_issue_context(
                repo,
                "Fix requests/packages/urllib3/connectionpool.py payment_status renewal-required.",
                max_candidate_files=20,
                max_regions=6,
                line_window=6,
            )

        self.assertEqual(result["file_candidates"][0]["path"], "src/billing/api.py")
        dependency_candidate = next(
            item for item in result["file_candidates"] if item["path"] == "requests/packages/urllib3/connectionpool.py"
        )
        self.assertIn("vendor_generated_static", dependency_candidate["risk_flags"])
        self.assertLess(dependency_candidate["score"], result["file_candidates"][0]["score"])
        self.assertTrue(any(item["kind"] == "risk_penalty" for item in dependency_candidate["evidence"]))
        self.assertEqual(direct_dependency["file_candidates"][0]["path"], "requests/packages/urllib3/connectionpool.py")
        self.assertIn("vendor_generated_static", direct_dependency["file_candidates"][0]["risk_flags"])

    def test_context_signal_extraction_ignores_environment_paths(self) -> None:
        issue = """\
Fix src/billing/api.py renewal behavior.

## System Information

File "/tmp/site-packages/urllib3/connectionpool.py", line 12
See requests/packages/chardet/compat.py:8
https://github.com/psf/requests/blob/main/requests/packages/urllib3/util.py#L9
"""

        signals = agent_contracts.extract_issue_signals(issue)

        self.assertEqual(signals.path_hints, ["src/billing/api.py"])
        self.assertEqual(signals.traceback_lines, {})
        self.assertNotIn("urllib3", signals.behavior_terms)
        self.assertNotIn("chardet", signals.behavior_terms)

    def test_context_localizer_prefers_longest_matching_github_blob_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "requests").mkdir()
            (repo / "models.py").write_text("def wrong():\n    pass\n", encoding="utf-8")
            (repo / "requests" / "models.py").write_text(
                "def prepare_url():\n    return 'ok'\n",
                encoding="utf-8",
            )

            result = agent_contracts.localize_issue_context(
                repo,
                "See https://github.com/org/repo/blob/bugfix/url-parser/requests/models.py#L1 for prepare_url.",
                max_candidate_files=4,
                max_regions=2,
                line_window=4,
                model_profile="spark",
            )

        self.assertEqual(result["file_candidates"][0]["path"], "requests/models.py")
        self.assertLess(
            next(item["score"] for item in result["file_candidates"] if item["path"] == "models.py"),
            result["file_candidates"][0]["score"],
        )
        self.assertTrue(any(region["path"] == "requests/models.py" for region in result["regions"]))

    def test_context_pack_v2_treats_source_root_test_prefixed_files_as_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "src").mkdir()
            (repo / "src" / "test_utils.py").write_text(
                "def normalize_user_input(value):\n    return value.strip()\n",
                encoding="utf-8",
            )
            (repo / "test_root.py").write_text("def test_root():\n    pass\n", encoding="utf-8")

            pack = agent_contracts.build_context_pack_v2(
                repo,
                "Fix src/test_utils.py normalize_user_input whitespace bug.",
                model_profile="spark",
                max_regions=3,
                line_window=4,
            )

        self.assertEqual(agent_contracts.context_file_role_for_path(repo, "src/test_utils.py"), "source")
        self.assertEqual(agent_contracts.context_file_role_for_path(repo, "test_root.py"), "test")
        self.assertEqual(pack["gate"]["decision"], "inject")
        self.assertIn("src/test_utils.py", [item["path"] for item in pack["evidence"]])
        self.assertIn("src/test_utils.py", pack["boundaries"]["safe_to_edit"])
        self.assertIn("test_root.py", pack["boundaries"]["read_only"])

    def test_context_pack_v2_keeps_direct_risky_paths_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            vendor_pack = agent_contracts.build_context_pack_v2(
                repo,
                "Fix requests/packages/urllib3/connectionpool.py payment_status renewal-required.",
                model_profile="spark",
                max_regions=4,
                line_window=6,
            )
            test_pack = agent_contracts.build_context_pack_v2(
                repo,
                "Fix tests/test_billing.py test_payment_status_renewal_required.",
                model_profile="spark",
                max_regions=4,
                line_window=6,
            )

        self.assertNotIn("requests/packages/urllib3/connectionpool.py", vendor_pack["boundaries"]["safe_to_edit"])
        self.assertIn("requests/packages/urllib3/connectionpool.py", vendor_pack["boundaries"]["read_only"])
        self.assertNotIn("tests/test_billing.py", test_pack["boundaries"]["safe_to_edit"])
        self.assertIn("tests/test_billing.py", test_pack["boundaries"]["read_only"])
        self.assertFalse(set(vendor_pack["boundaries"]["safe_to_edit"]) & set(vendor_pack["boundaries"]["read_only"]))
        self.assertFalse(set(test_pack["boundaries"]["safe_to_edit"]) & set(test_pack["boundaries"]["read_only"]))

    def test_context_pack_v2_suppresses_weak_docs_html_snippets_for_spark(self) -> None:
        issue = (
            "Getting http://.example.com raises UnicodeError in requests. "
            "Expected InvalidURL from PreparedRequest.prepare_url."
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_requests_noisy_html_fixture(Path(tmp))
            localization = agent_contracts.localize_issue_context(
                repo,
                issue,
                max_candidate_files=12,
                max_regions=8,
                line_window=6,
                model_profile=agent_contracts.model_profile_payload("spark"),
            )
            pack = agent_contracts.build_context_pack_v2(
                repo,
                issue,
                model_profile="spark",
                max_regions=6,
                line_window=6,
            )
            direct_docs = agent_contracts.localize_issue_context(
                repo,
                "Fix docs/_templates/sidebarintro.html requests sidebar copy.",
                max_candidate_files=4,
                max_regions=2,
                line_window=6,
            )

        self.assertEqual(
            agent_contracts.context_file_role_for_path(repo, "docs/_templates/sidebarintro.html"),
            "docs",
        )
        self.assertIn(
            "docs",
            agent_contracts.context_path_risk_flags("docs/_templates/sidebarintro.html", "docs"),
        )
        html_candidate = next(
            (item for item in localization["file_candidates"] if item["path"] == "docs/_templates/sidebarintro.html"),
            None,
        )
        if html_candidate is not None:
            self.assertIn("docs", html_candidate["risk_flags"])
            self.assertEqual(html_candidate["confidence"], "weak")
        self.assertIn(
            agent_contracts.spark_preload_omission_reason(
                {
                    "path": "docs/_templates/sidebarintro.html",
                    "role": "docs",
                    "strength": "medium",
                },
                {
                    "docs/_templates/sidebarintro.html": {
                        "path": "docs/_templates/sidebarintro.html",
                        "role": "docs",
                        "risk_flags": ["docs"],
                        "evidence": [{"kind": "behavior_term", "value": "requests", "weight": 18}],
                    }
                },
                "spark",
            ),
            {
                "non-source evidence omitted for spark profile without explicit edit-target evidence",
                "weak non-code evidence omitted for spark profile without direct issue evidence",
            },
        )
        self.assertNotIn("docs/_templates/sidebarintro.html", {item["path"] for item in pack["evidence"]})
        self.assertEqual(direct_docs["file_candidates"][0]["path"], "docs/_templates/sidebarintro.html")
        self.assertTrue(agent_contracts.context_candidate_has_direct_issue_evidence(direct_docs["file_candidates"][0]))

    def test_requests_known_bad_cases_pass_no_harm_localization_gate_and_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_requests_no_harm_fixture(Path(tmp))
            for case in REQUESTS_NO_HARM_CASES:
                with self.subTest(case=case["instance_id"]):
                    issue = case["problem"]
                    expected = case["expected"]
                    localization = agent_contracts.localize_issue_context(
                        repo,
                        issue,
                        max_candidate_files=16,
                        max_regions=10,
                        line_window=8,
                        model_profile=agent_contracts.model_profile_payload("spark"),
                    )
                    gate_payload = agent_contracts.context_gate_payload(repo, issue, model_profile="spark")
                    pack = agent_contracts.build_context_pack_v2(
                        repo,
                        issue,
                        model_profile="spark",
                        max_regions=6,
                        line_window=8,
                    )

                    source_paths = [
                        item["path"]
                        for item in localization["file_candidates"]
                        if item.get("role") == "source" and not set(item.get("risk_flags", [])) & agent_contracts.EDIT_TARGET_RISK_FLAGS
                    ]
                    candidates_by_path = agent_contracts.context_candidates_by_path(localization["file_candidates"])
                    expected_candidate = candidates_by_path[expected]
                    expected_strong = agent_contracts.context_candidate_has_strong_source_evidence(expected_candidate)
                    emitted_paths = {item["path"] for item in pack["evidence"]}
                    safe_to_edit = set(pack["boundaries"]["safe_to_edit"])
                    read_only = set(pack["boundaries"]["read_only"])
                    expected_decision, expected_confidence = REQUESTS_NO_HARM_GATE_EXPECTATIONS[case["instance_id"]]

                    self.assertIn(expected, source_paths[:3])
                    self.assertTrue(expected_strong, expected_candidate["evidence"])
                    self.assertEqual(gate_payload["gate"]["decision"], expected_decision)
                    self.assertEqual(gate_payload["gate"]["confidence"], expected_confidence)
                    if gate_payload["gate"]["decision"] in {"inject", "advisory"}:
                        self.assertIn(expected, emitted_paths)
                    if gate_payload["gate"]["decision"] == "inject":
                        self.assertTrue(expected_strong)

                    self.assertFalse(safe_to_edit & read_only)
                    for safe_path in safe_to_edit:
                        safe_candidate = candidates_by_path[safe_path]
                        self.assertEqual(safe_candidate["role"], "source")
                        self.assertFalse(set(safe_candidate.get("risk_flags", [])) & agent_contracts.EDIT_TARGET_RISK_FLAGS)
                    for path in [
                        "docs/html/api.html",
                        "requests/packages/urllib3/connectionpool.py",
                        "requests/packages/chardet/compat.py",
                        "setup.cfg",
                    ]:
                        self.assertNotIn(path, safe_to_edit)
                    self.assertNotEqual(localization["file_candidates"][0]["path"], "requests/help.py")

                    if case["instance_id"] == "psf__requests-5414":
                        self.assertIn("requests/models.py", localization["signals"]["path_hints"])
                        self.assertNotIn("chardet", localization["signals"]["behavior_terms"])
                        self.assertNotIn("urllib3", localization["signals"]["behavior_terms"])

    def test_requests_no_harm_cli_exercises_localize_gate_and_pack_v2(self) -> None:
        case = REQUESTS_NO_HARM_CASES[-1]
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_requests_no_harm_fixture(Path(tmp))
            localize = run_cli(
                "context-localize",
                case["problem"],
                "--model-profile",
                "spark",
                "--max-candidate-files",
                "12",
                "--max-regions",
                "8",
                "--line-window",
                "8",
                "--format",
                "json",
                repo=repo,
            )
            gate = run_cli(
                "context-gate",
                case["problem"],
                "--model-profile",
                "spark",
                "--format",
                "json",
                repo=repo,
            )
            pack = run_cli(
                "context-pack-v2",
                case["problem"],
                "--model-profile",
                "spark",
                "--max-regions",
                "6",
                "--line-window",
                "8",
                "--format",
                "json",
                repo=repo,
            )

        self.assertEqual(localize.returncode, 0, localize.stderr)
        self.assertEqual(gate.returncode, 0, gate.stderr)
        self.assertEqual(pack.returncode, 0, pack.stderr)
        self.assertIn(case["expected"], [item["path"] for item in json.loads(localize.stdout)["file_candidates"][:5]])
        pack_data = json.loads(pack.stdout)
        self.assertFalse(set(pack_data["boundaries"]["safe_to_edit"]) & set(pack_data["boundaries"]["read_only"]))

    def test_context_localizer_pairs_source_and_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            result = agent_contracts.localize_issue_context(
                repo,
                "test_payment_status_renewal_required fails around payment_status.",
                max_candidate_files=8,
                max_regions=4,
                line_window=5,
            )

        paths = [item["path"] for item in result["file_candidates"]]
        self.assertIn("tests/test_billing.py", paths)
        self.assertIn("src/billing/api.py", paths)
        source = next(item for item in result["file_candidates"] if item["path"] == "src/billing/api.py")
        self.assertIn("test_pair", {item["kind"] for item in source["evidence"]})

    def test_no_harm_gate_abstains_or_tool_only_for_broad_module_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            localization = agent_contracts.localize_issue_context(repo, "Update the billing module behavior.", include_internal_indexes=True)
            baseline = agent_contracts.baseline_retrieve_context(
                repo,
                "Update the billing module behavior.",
                search_index=localization.get("_search_index"),
            )
            gate = agent_contracts.evaluate_context_quality(
                localization,
                baseline,
                estimate=agent_contracts.estimate_candidate_bytes(repo, localization["file_candidates"][:12]),
                model_profile="spark",
            )

        self.assertEqual(gate["decision"], "tool_only")
        self.assertEqual(gate["confidence"], "low")
        self.assertEqual(gate["fallback"], "progressive_tools")
        self.assertEqual(gate["limits"]["max_preloaded_regions"], 0)

    def test_no_harm_gate_injects_or_advises_exact_path_symbol_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            localization = agent_contracts.localize_issue_context(
                repo,
                "Fix src/billing/api.py payment_status renewal-required.",
                include_internal_indexes=True,
            )
            baseline = agent_contracts.baseline_retrieve_context(
                repo,
                "Fix src/billing/api.py payment_status renewal-required.",
                search_index=localization.get("_search_index"),
            )
            gate = agent_contracts.evaluate_context_quality(
                localization,
                baseline,
                estimate=agent_contracts.estimate_candidate_bytes(repo, localization["file_candidates"][:12]),
                model_profile="frontier",
            )

        self.assertEqual(gate["decision"], "inject")
        self.assertEqual(gate["confidence"], "high")
        self.assertEqual(gate["fallback"], "none")
        self.assertEqual(gate["limits"]["max_preloaded_regions"], 6)
        self.assertIn("direct issue evidence present", gate["reasons"])

    def test_no_harm_gate_uses_preloaded_snippet_budget_not_candidate_bytes(self) -> None:
        localization = {
            "file_candidates": [
                {
                    "path": "src/billing/api.py",
                    "role": "source",
                    "score": 900,
                    "evidence": [{"kind": "symbol", "value": "payment_status", "weight": 560}],
                    "risk_flags": [],
                }
            ],
            "regions": [
                {
                    "path": "src/billing/api.py",
                    "start": 8,
                    "end": 14,
                    "strength": "strong",
                    "evidence": [{"kind": "symbol", "value": "payment_status", "weight": 560}],
                }
            ],
            "confidence": "high",
            "limits": {"max_bytes": 1_000},
        }

        gate = agent_contracts.evaluate_context_quality(
            localization,
            {"candidates": []},
            estimate={"selected_bytes": 50_000, "preloaded_chars": 900, "preload_char_budget": 24_000},
            model_profile="mini",
        )

        self.assertEqual(gate["decision"], "inject")
        self.assertFalse(gate["metrics"]["budget_risk"])
        self.assertTrue(gate["metrics"]["candidate_byte_risk"])
        self.assertIn("candidate files exceed byte budget but snippets are bounded", gate["reasons"])

    def test_no_harm_gate_abstains_without_candidates(self) -> None:
        gate = agent_contracts.evaluate_context_quality(
            {"file_candidates": [], "regions": [], "confidence": "low", "limits": {"max_bytes": 1}},
            {"candidates": []},
            model_profile="unknown",
        )

        self.assertEqual(gate["decision"], "abstain")
        self.assertEqual(gate["fallback"], "baseline_search")

    def test_context_pack_v2_schema_contains_guardrails_and_advisory_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            pack = agent_contracts.build_context_pack_v2(
                repo,
                "Fix src/billing/api.py payment_status renewal-required.",
                model_profile="spark",
                max_regions=4,
                line_window=5,
            )

        self.assertEqual(pack["schema_version"], 2)
        self.assertIn(pack["gate"]["decision"], {"inject", "advisory"})
        self.assertIn("First form an independent hypothesis", pack["prompt_contract"])
        self.assertIn("safe_to_edit", pack["boundaries"])
        self.assertTrue(pack["verification"]["tests"])
        self.assertTrue(all(item["strength"] in {"strong", "medium"} for item in pack["evidence"]))

    def test_context_pack_v2_no_preload_and_output_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            broad_pack = agent_contracts.build_context_pack_v2(
                repo,
                "Payment status renewal required hostname validation bug.",
                model_profile="unknown",
                max_regions=4,
                line_window=5,
            )
            output = Path(tmp) / "pack-v2"
            written_pack = agent_contracts.build_context_pack_v2(
                repo,
                "Fix src/billing/api.py payment_status renewal-required.",
                output,
                model_profile="frontier",
                max_regions=4,
                line_window=5,
            )
            self.assertTrue((output / "manifest.json").exists())
            self.assertTrue((output / "README.md").exists())
            self.assertEqual(json.loads((output / "manifest.json").read_text(encoding="utf-8"))["schema_version"], 2)
            self.assertEqual(written_pack["manifest_path"], (output / "manifest.json").as_posix())

        self.assertEqual(broad_pack["gate"]["decision"], "tool_only")
        self.assertEqual(broad_pack["evidence"], [])
        self.assertEqual(broad_pack["hypotheses"][0]["confidence"], "low")
        self.assertEqual(broad_pack["localization"]["file_candidates"], [])
        self.assertEqual(broad_pack["localization"]["regions"], [])
        self.assertIn("redaction_reason", broad_pack["localization"]["audit_only"])
        self.assertTrue(any("gate decision tool_only" in item["reason"] for item in broad_pack["omissions"]))

    def test_context_v2_cli_and_region_read_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            localize = run_cli(
                "context-localize",
                "Fix src/billing/api.py payment_status renewal-required.",
                "--max-regions",
                "3",
                "--line-window",
                "5",
                "--format",
                "json",
                repo=repo,
            )
            read_region = run_cli(
                "context-read-region",
                "src/billing/api.py",
                "7",
                "10",
                "--format",
                "json",
                repo=repo,
            )

        self.assertEqual(localize.returncode, 0, localize.stderr)
        self.assertEqual(json.loads(localize.stdout)["file_candidates"][0]["path"], "src/billing/api.py")
        self.assertEqual(read_region.returncode, 0, read_region.stderr)
        self.assertIn("renewal-required", json.loads(read_region.stdout)["content"])

    def test_context_read_region_guardrails_and_context_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            (repo / "src" / "billing" / "empty.py").write_text("", encoding="utf-8")

            expanded = agent_contracts.read_region_payload(repo, "src/billing/api.py", 9, 9, context_lines=2)
            empty = agent_contracts.read_region_payload(repo, "src/billing/empty.py", 1, 1)

            with self.assertRaises(ValueError):
                agent_contracts.read_region_payload(repo, "../secret.py", 1, 1)
            with self.assertRaises(ValueError):
                agent_contracts.read_region_payload(repo, "/tmp/secret.py", 1, 1)
            with self.assertRaises(ValueError):
                agent_contracts.read_region_payload(repo, "src/billing/missing.py", 1, 1)
            with self.assertRaises(ValueError):
                agent_contracts_mcp.call_tool(
                    "context_read_region",
                    {"repo": str(repo), "path": "src/billing/api.py", "start": 1, "end": 1, "context_lines": -1},
                )

        self.assertEqual(expanded["start"], 7)
        self.assertEqual(expanded["end"], 11)
        self.assertIn("payment_status", expanded["content"])
        self.assertTrue(expanded["nearby_tests"])
        self.assertEqual(empty["content"], "")
        self.assertEqual(empty["end"], 0)

    def test_context_v2_cli_rejects_invalid_numeric_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            bad_window = run_cli(
                "context-localize",
                "Fix src/billing/api.py",
                "--line-window",
                "0",
                repo=repo,
            )
            bad_region_order = run_cli(
                "context-read-region",
                "src/billing/api.py",
                "10",
                "2",
                repo=repo,
            )
            bad_context_lines = run_cli(
                "context-read-region",
                "src/billing/api.py",
                "1",
                "2",
                "--context-lines",
                "-1",
                repo=repo,
            )

        self.assertEqual(bad_window.returncode, 2)
        self.assertEqual(bad_region_order.returncode, 2)
        self.assertEqual(bad_context_lines.returncode, 2)

    def test_context_discover_json_includes_compact_catalog(self) -> None:
        result = run_cli("context-discover", "--format", "json", repo=ROOT / "fixtures" / "python-service")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        modules = {module["name"]: module for module in data["modules"]}
        billing = modules["billing"]
        self.assertEqual(billing["root"], "src/billing")
        self.assertEqual(billing["kind"], "component")
        self.assertIn("billing-module", billing["capabilities"])
        self.assertIn("auth", billing["dependencies"])
        self.assertIn("tests/test_billing.py", billing["tests"])
        self.assertIn("confidence", billing)
        self.assertIn("estimated_context_size", billing)
        self.assertIsInstance(billing["estimated_context_size"]["bytes"], int)
        self.assertNotIn("owned_files", billing)

    def test_module_map_cache_is_reused_and_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            agent_contracts.clear_module_map_cache()

            first = agent_contracts.build_module_map(target)
            cache_path = agent_contracts.module_map_cache_path(target)
            self.assertTrue(cache_path.exists())

            with mock.patch.object(
                agent_contracts,
                "build_module_map_from_files",
                side_effect=AssertionError("expected cached module map"),
            ):
                cached = agent_contracts.build_module_map(target)
            self.assertEqual(cached["modules"], first["modules"])

            api_path = target / "src" / "billing" / "api.py"
            api_path.write_text(api_path.read_text(encoding="utf-8") + "\n# cache invalidation\n", encoding="utf-8")

            with mock.patch.object(
                agent_contracts,
                "build_module_map_from_files",
                wraps=agent_contracts.build_module_map_from_files,
            ) as builder:
                refreshed = agent_contracts.build_module_map(target)
            self.assertGreater(builder.call_count, 0)
            self.assertGreaterEqual({module["name"] for module in refreshed["modules"]}, {"auth", "billing"})

    def test_module_map_cache_stays_out_of_git_worktree_status(self) -> None:
        if not agent_contracts.git_available():
            self.skipTest("git is not available")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            self.assertTrue(agent_contracts.initialize_trial_git_repo(target))
            agent_contracts.clear_module_map_cache()
            agent_contracts.clear_localization_index_cache()

            agent_contracts.build_module_map(target)
            agent_contracts.build_repo_search_index(target)

            cache_path = agent_contracts.module_map_cache_path(target)
            search_cache_path = agent_contracts.localization_index_cache_path(target, "search_index")
            self.assertTrue(cache_path.is_file())
            self.assertTrue(search_cache_path.is_file())
            self.assertTrue(cache_path.is_relative_to(target.resolve() / ".git"))
            self.assertTrue(search_cache_path.is_relative_to(target.resolve() / ".git"))
            self.assertEqual(agent_contracts.git_changed_files(target), [])

    def test_localization_index_cache_is_reused_and_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            agent_contracts.clear_localization_index_cache()

            first_indexes, first_freshness = agent_contracts.build_context_localization_indexes(target)
            self.assertEqual(first_freshness["search_index"], "recomputed")
            self.assertEqual(first_freshness["symbol_index"], "recomputed")
            self.assertEqual(first_freshness["test_index"], "recomputed")
            self.assertEqual(
                first_indexes["search_index"]["fingerprint"]["extra"]["schema_version"],
                agent_contracts.CONTEXT_LOCALIZATION_INDEX_SCHEMA_VERSION,
            )
            for index_name in ("search_index", "symbol_index", "test_index"):
                self.assertTrue(agent_contracts.localization_index_cache_path(target, index_name).is_file())

            with (
                mock.patch.object(
                    agent_contracts,
                    "build_repo_search_index_from_files",
                    side_effect=AssertionError("expected cached search index"),
                ),
                mock.patch.object(
                    agent_contracts,
                    "build_symbol_index_from_files",
                    side_effect=AssertionError("expected cached symbol index"),
                ),
                mock.patch.object(
                    agent_contracts,
                    "build_test_index_from_files",
                    side_effect=AssertionError("expected cached test index"),
                ),
            ):
                cached_indexes, cached_freshness = agent_contracts.build_context_localization_indexes(target)
            self.assertEqual(cached_indexes["search_index"]["fingerprint"], first_indexes["search_index"]["fingerprint"])
            self.assertEqual(cached_freshness["search_index"], "memory_hit")
            self.assertEqual(cached_freshness["symbol_index"], "memory_hit")
            self.assertEqual(cached_freshness["test_index"], "memory_hit")

            api_path = target / "src" / "billing" / "api.py"
            api_path.write_text(
                api_path.read_text(encoding="utf-8") + "\ndef cache_invalidation_marker():\n    return True\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(
                    agent_contracts,
                    "build_repo_search_index_from_files",
                    wraps=agent_contracts.build_repo_search_index_from_files,
                ) as search_builder,
                mock.patch.object(
                    agent_contracts,
                    "build_symbol_index_from_files",
                    wraps=agent_contracts.build_symbol_index_from_files,
                ) as symbol_builder,
                mock.patch.object(
                    agent_contracts,
                    "build_test_index_from_files",
                    wraps=agent_contracts.build_test_index_from_files,
                ) as test_builder,
            ):
                refreshed_indexes, refreshed_freshness = agent_contracts.build_context_localization_indexes(target)
            self.assertEqual(search_builder.call_count, 1)
            self.assertEqual(symbol_builder.call_count, 1)
            self.assertEqual(test_builder.call_count, 1)
            self.assertEqual(refreshed_freshness["search_index"], "recomputed")
            self.assertNotEqual(
                refreshed_indexes["search_index"]["fingerprint"]["digest"],
                first_indexes["search_index"]["fingerprint"]["digest"],
            )

    def test_localization_index_cache_respects_disable_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            agent_contracts.clear_localization_index_cache()

            with (
                mock.patch.dict(os.environ, {agent_contracts.MODULE_MAP_CACHE_ENV: "1"}),
                mock.patch.object(
                    agent_contracts,
                    "build_repo_search_index_from_files",
                    wraps=agent_contracts.build_repo_search_index_from_files,
                ) as search_builder,
                mock.patch.object(
                    agent_contracts,
                    "build_symbol_index_from_files",
                    wraps=agent_contracts.build_symbol_index_from_files,
                ) as symbol_builder,
                mock.patch.object(
                    agent_contracts,
                    "build_test_index_from_files",
                    wraps=agent_contracts.build_test_index_from_files,
                ) as test_builder,
            ):
                _first_indexes, first_freshness = agent_contracts.build_context_localization_indexes(target)
                _second_indexes, second_freshness = agent_contracts.build_context_localization_indexes(target)

            self.assertEqual(search_builder.call_count, 2)
            self.assertEqual(symbol_builder.call_count, 2)
            self.assertEqual(test_builder.call_count, 2)
            self.assertEqual(first_freshness["search_index"], "disabled")
            self.assertEqual(second_freshness["symbol_index"], "disabled")
            self.assertEqual(second_freshness["test_index"], "disabled")

    def test_common_v2_flow_reuses_warmed_localization_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            agent_contracts.clear_module_map_cache()
            agent_contracts.clear_localization_index_cache()
            task = "Fix src/billing/api.py payment_status renewal-required."
            agent_contracts.localize_issue_context(repo, task, include_internal_indexes=True)

            with (
                mock.patch.object(
                    agent_contracts,
                    "build_module_map_from_files",
                    side_effect=AssertionError("expected cached module map"),
                ),
                mock.patch.object(
                    agent_contracts,
                    "build_repo_search_index_from_files",
                    side_effect=AssertionError("expected cached search index"),
                ),
                mock.patch.object(
                    agent_contracts,
                    "build_symbol_index_from_files",
                    side_effect=AssertionError("expected cached symbol index"),
                ),
                mock.patch.object(
                    agent_contracts,
                    "build_test_index_from_files",
                    side_effect=AssertionError("expected cached test index"),
                ),
            ):
                gate = agent_contracts.context_gate_payload(repo, task, model_profile="spark")
                pack = agent_contracts.build_context_pack_v2(repo, task, model_profile="spark")
                region = agent_contracts.read_region_payload(repo, "src/billing/api.py", 7, 11, context_lines=1)
                expansion = agent_contracts.context_expand_payload(repo, "src/billing/api.py")

            self.assertIn("gate", gate)
            self.assertEqual(pack["model_profile"]["name"], "spark")
            self.assertTrue(region["symbols"])
            self.assertTrue(any(item["path"] == "tests/test_billing.py" for item in expansion["neighbors"]))

    def test_mcp_context_read_reuses_discover_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            agent_contracts.clear_module_map_cache()

            discover = agent_contracts_mcp.call_tool("context_discover", {"repo": str(target)})
            self.assertGreaterEqual(discover["summary"]["module_count"], 2)

            with mock.patch.object(
                agent_contracts,
                "build_module_map_from_files",
                side_effect=AssertionError("expected MCP context_read to reuse cached map"),
            ):
                payload = agent_contracts_mcp.call_tool(
                    "context_read",
                    {"repo": str(target), "target": "billing", "section": "summary"},
                )
            self.assertEqual(payload["module"], "billing")

    def test_context_read_json_sections(self) -> None:
        repo = ROOT / "fixtures" / "python-service"

        summary = run_cli("context-read", "billing", "--section", "summary", "--format", "json", repo=repo)
        self.assertEqual(summary.returncode, 0, summary.stderr)
        summary_data = json.loads(summary.stdout)
        self.assertEqual(summary_data["module"], "billing")
        self.assertIn("function payment_status", summary_data["public_surfaces"])

        tests = run_cli("context-read", "billing", "--section", "tests", "--format", "json", repo=repo)
        self.assertEqual(tests.returncode, 0, tests.stderr)
        self.assertEqual(json.loads(tests.stdout)["tests"], ["tests/test_billing.py"])

        dependencies = run_cli("context-read", "billing", "--section", "dependencies", "--format", "json", repo=repo)
        self.assertEqual(dependencies.returncode, 0, dependencies.stderr)
        deps = json.loads(dependencies.stdout)["dependencies"]
        self.assertEqual([dep["name"] for dep in deps], ["auth"])
        self.assertEqual(deps[0]["imports"][0]["source"], "src/billing/api.py")

        source_list = run_cli("context-read", "billing", "--section", "source-list", "--format", "json", repo=repo)
        self.assertEqual(source_list.returncode, 0, source_list.stderr)
        sources = json.loads(source_list.stdout)["source_files"]
        self.assertIn("src/billing/api.py", sources)
        self.assertNotIn("src/auth/token.py", sources)

    def test_context_read_reports_unknown_module_and_section(self) -> None:
        repo = ROOT / "fixtures" / "python-service"

        unknown_module = run_cli("context-read", "missing-module", "--section", "summary", repo=repo)
        self.assertEqual(unknown_module.returncode, 2)
        self.assertIn("Unknown module or target", unknown_module.stderr)

        unknown_section = run_cli("context-read", "billing", "--section", "not-a-section", repo=repo)
        self.assertEqual(unknown_section.returncode, 2)
        self.assertIn("invalid choice", unknown_section.stderr)

    def test_context_pack_excludes_unrelated_sibling_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            analytics_dir = target / "src" / "analytics"
            analytics_dir.mkdir()
            (analytics_dir / "metrics.py").write_text("def summarize() -> int:\n    return 1\n", encoding="utf-8")

            init_result = run_cli("init", "--write", "--yes", repo=target)
            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            output = Path(tmp) / "pack"
            result = run_cli("context-pack", "billing", "--output", str(output), repo=target)
            self.assertEqual(result.returncode, 0, result.stderr)

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            included = set(manifest["included_files"])
            self.assertIn("src/billing/api.py", included)
            self.assertIn("src/auth/SPEC.md", included)
            self.assertNotIn("src/analytics/metrics.py", included)
            self.assertNotIn("src/analytics/SPEC.md", included)

    def test_context_selection_manifest_loads(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        self.assertGreaterEqual(len(rows), 4)
        first = rows[0]
        self.assertEqual(first["task_id"], "python-service-billing-status")
        self.assertEqual(first["repo_path"], "fixtures/python-service")
        self.assertIn("src/billing/api.py", first["target_files"])
        contract_guided = next(row for row in rows if row["task_id"] == "contract-guided-payments-refund")
        self.assertIn("src/payments/SPEC.md", contract_guided["required_context"])
        self.assertIn("src/ledger/SPEC.md", contract_guided["required_context"])

    def test_readme_describes_verifier_scope_accurately(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("shared context-pack planning path", text)
        self.assertIn("does not directly invoke `context-discover` or `context-read`", text)
        self.assertNotIn("checks whether `context-discover`, `context-read`", text)

    def test_scores_selected_files_against_manifest_sets(self) -> None:
        repo = ROOT / "fixtures" / "noisy-context"
        row = {
            "task_id": "score-test",
            "expected_module": "payments",
            "target_files": ["src/payments/refund.py"],
            "relevant_tests": ["tests/test_payments_refund.py"],
            "allowed_context": ["src/payments/__init__.py"],
            "misleading_files": ["docs/refunds.md"],
        }
        selected = [
            agent_contracts.ContextFile("src/payments/refund.py", "source"),
            agent_contracts.ContextFile("tests/test_payments_refund.py", "test"),
            agent_contracts.ContextFile("src/payments/__init__.py", "source"),
            agent_contracts.ContextFile("docs/refunds.md", "docs"),
            agent_contracts.ContextFile("docs/deploy.md", "docs"),
        ]
        selection = {
            "files": selected,
            "included_files": [item.path for item in selected],
            "omitted_files": [],
            "selected_bytes": sum((repo / item.path).stat().st_size for item in selected),
            "resolved_module": "payments",
        }

        score = agent_contracts.score_selected_context_files(repo, row, "unit", selection)

        classifications = {item["path"]: item["classification"] for item in score["file_classifications"]}
        self.assertEqual(classifications["src/payments/refund.py"], "target")
        self.assertEqual(classifications["tests/test_payments_refund.py"], "relevant_test")
        self.assertEqual(classifications["src/payments/__init__.py"], "allowed_context")
        self.assertEqual(classifications["docs/refunds.md"], "misleading")
        self.assertEqual(classifications["docs/deploy.md"], "irrelevant")
        self.assertEqual(score["target_file_recall"], 1.0)
        self.assertEqual(score["relevant_tests_found"], 1.0)
        self.assertEqual(score["misleading_files_included"], 1)
        self.assertEqual(score["irrelevant_files_read"], 1)
        self.assertFalse(score["passed"])

    def test_expected_module_match_affects_module_strategy_pass_fail(self) -> None:
        repo = ROOT / "fixtures" / "noisy-context"
        row = {
            "task_id": "module-match-test",
            "expected_module": "payments",
            "target_files": ["src/payments/refund.py"],
            "relevant_tests": ["tests/test_payments_refund.py"],
            "allowed_context": [],
            "misleading_files": [],
        }
        selected = [
            agent_contracts.ContextFile("src/payments/refund.py", "source"),
            agent_contracts.ContextFile("tests/test_payments_refund.py", "test"),
        ]
        selection = {
            "files": selected,
            "included_files": [item.path for item in selected],
            "omitted_files": [],
            "selected_bytes": sum((repo / item.path).stat().st_size for item in selected),
            "resolved_module": "legacy",
        }

        module_score = agent_contracts.score_selected_context_files(repo, row, "module", selection)
        self.assertFalse(module_score["expected_module_match"])
        self.assertFalse(module_score["passed"])

        naive_selection = dict(selection)
        naive_selection["resolved_module"] = None
        naive_score = agent_contracts.score_selected_context_files(repo, row, "naive", naive_selection)
        self.assertIsNone(naive_score["expected_module_match"])
        self.assertTrue(naive_score["passed"])

    def test_verify_context_json_cli(self) -> None:
        result = run_cli("verify-context", "validation/context-selection/manifest.jsonl", "--format", "json", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["schema_version"], agent_contracts.CONTEXT_VERIFIER_SCHEMA_VERSION)
        self.assertIn("module", data["aggregate"])
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        self.assertEqual(data["aggregate"]["module"]["task_count"], len(rows))
        self.assertEqual(data["aggregate"]["module"]["pass_rate"], 1.0)
        self.assertLess(data["aggregate"]["module-no-contracts"]["pass_rate"], data["aggregate"]["module"]["pass_rate"])
        contract_guided = next(task for task in data["tasks"] if task["task_id"] == "contract-guided-payments-refund")
        self.assertFalse(contract_guided["strategies"]["module-no-contracts"]["passed"])

    def test_module_strategy_beats_naive_on_noisy_fixture(self) -> None:
        report = agent_contracts.run_context_verification(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        noisy = next(task for task in report["tasks"] if task["task_id"] == "noisy-payments-refund")
        naive = noisy["strategies"]["naive"]
        module = noisy["strategies"]["module"]

        self.assertGreater(naive["misleading_files_included"], module["misleading_files_included"])
        self.assertGreater(naive["irrelevant_files_read"], module["irrelevant_files_read"])
        self.assertGreater(naive["context_bloat"], module["context_bloat"])

    def test_contract_guided_fixture_proves_contract_context_beats_source_only_module(self) -> None:
        report = agent_contracts.run_context_verification(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        task = next(item for item in report["tasks"] if item["task_id"] == "contract-guided-payments-refund")
        naive = task["strategies"]["naive"]
        module = task["strategies"]["module"]
        no_contracts = task["strategies"]["module-no-contracts"]

        self.assertTrue(module["passed"])
        self.assertFalse(no_contracts["passed"])
        self.assertEqual(module["required_context_found"], 1.0)
        self.assertEqual(no_contracts["required_context_found"], 0.0)
        self.assertIn("src/payments/SPEC.md", module["required_context_files_found"])
        self.assertIn("src/payments/AGENTS.md", module["required_context_files_found"])
        self.assertIn("src/ledger/SPEC.md", module["required_context_files_found"])
        self.assertIn("src/legacy/refunds.py", naive["misleading_file_paths"])
        self.assertGreater(naive["misleading_files_included"], module["misleading_files_included"])

    def test_graph_like_strategy_returns_plausible_bounded_selection_and_trace(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        row = next(item for item in rows if item["task_id"] == "noisy-payments-refund")
        repo = ROOT / row["repo_path"]

        selection = agent_contracts.select_context_files_for_strategy(
            repo,
            row,
            "graph-like",
            max_files=80,
            max_bytes=700_000,
        )

        self.assertLessEqual(len(selection["included_files"]), 80)
        self.assertLessEqual(selection["selected_bytes"], 700_000)
        self.assertIn("src/payments/refund.py", selection["included_files"])
        self.assertIn("tests/test_payments_refund.py", selection["included_files"])
        self.assertTrue(selection["trace"])
        self.assertTrue(all(item["strategy"] == "graph-like" for item in selection["trace"]))
        self.assertTrue(any("task-keyword-match" in ",".join(item["reasons"]) for item in selection["trace"]))

    def test_progressive_mcp_strategy_emits_discover_read_pack_steps(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        row = next(item for item in rows if item["task_id"] == "contract-guided-payments-refund")
        repo = ROOT / row["repo_path"]

        selection = agent_contracts.select_context_files_for_strategy(
            repo,
            row,
            "progressive-mcp",
            max_files=80,
            max_bytes=700_000,
        )

        tool_steps = [item for item in selection["trace"] if "tool" in item]
        tools = [item["tool"] for item in tool_steps]
        self.assertEqual(tools[0], "context_discover")
        self.assertIn("context_read", tools)
        self.assertEqual(tools[-1], "context_pack")
        read_sections = {item["section"] for item in tool_steps if item["tool"] == "context_read"}
        self.assertGreaterEqual(read_sections, {"summary", "contract", "tests", "dependencies"})

    def test_benchmark_context_json_cli_includes_all_strategies(self) -> None:
        result = run_cli("benchmark-context", "validation/context-selection/manifest.jsonl", "--format", "json", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        expected = set(agent_contracts.CONTEXT_BENCHMARK_STRATEGIES)
        self.assertEqual(data["schema_version"], agent_contracts.CONTEXT_BENCHMARK_SCHEMA_VERSION)
        self.assertEqual({item["name"] for item in data["strategies"]}, expected)
        self.assertEqual(set(data["aggregate"]), expected)
        self.assertTrue(all(set(task["strategies"]) == expected for task in data["tasks"]))

    def test_benchmark_comparison_metrics_are_calculated_correctly(self) -> None:
        report = agent_contracts.run_context_benchmark(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        module = report["aggregate"]["module"]
        naive = report["aggregate"]["naive"]
        deltas = report["comparisons"]["aggregate"]["module_vs_naive"]["deltas"]

        self.assertEqual(deltas["pass_rate"], round(module["pass_rate"] - naive["pass_rate"], 6))
        self.assertEqual(deltas["selected_bytes_savings"], round(naive["selected_bytes"] - module["selected_bytes"], 6))
        self.assertEqual(
            deltas["misleading_files_reduction"],
            round(naive["misleading_files_included"] - module["misleading_files_included"], 6),
        )

    def test_benchmark_module_still_beats_naive_on_noisy_fixture(self) -> None:
        report = agent_contracts.run_context_benchmark(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        noisy = next(task for task in report["tasks"] if task["task_id"] == "noisy-payments-refund")
        naive = noisy["strategies"]["naive"]
        module = noisy["strategies"]["module"]

        self.assertFalse(naive["passed"])
        self.assertTrue(module["passed"])
        self.assertGreater(naive["misleading_files_included"], module["misleading_files_included"])
        self.assertGreater(naive["context_bloat"], module["context_bloat"])

    def test_benchmark_module_beats_no_contracts_on_contract_guided_fixture(self) -> None:
        report = agent_contracts.run_context_benchmark(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        task = next(item for item in report["tasks"] if item["task_id"] == "contract-guided-payments-refund")
        module = task["strategies"]["module"]
        no_contracts = task["strategies"]["module-no-contracts"]

        self.assertTrue(module["passed"])
        self.assertFalse(no_contracts["passed"])
        self.assertGreater(module["required_context_recall"], no_contracts["required_context_recall"])

    def test_benchmark_compares_module_and_progressive_against_graph_like(self) -> None:
        report = agent_contracts.run_context_benchmark(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        aggregate = report["comparisons"]["aggregate"]

        self.assertIn("module_vs_graph_like", aggregate)
        self.assertIn("progressive_mcp_vs_graph_like", aggregate)
        self.assertGreater(aggregate["module_vs_graph_like"]["deltas"]["pass_rate"], 0)
        self.assertGreater(aggregate["progressive_mcp_vs_graph_like"]["deltas"]["required_context_recall"], 0)

    def test_benchmark_json_output_has_stable_machine_readable_shape(self) -> None:
        result = run_cli("benchmark-context", "validation/context-selection/manifest.jsonl", "--format", "json", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertRegex(data["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(data["limits"], {"max_files": 80, "max_bytes": 700_000})
        self.assertEqual([item["name"] for item in data["strategies"]], list(agent_contracts.CONTEXT_BENCHMARK_STRATEGIES))
        self.assertIn("aggregate", data["comparisons"])
        self.assertIn("tasks", data["comparisons"])
        self.assertIn("module_vs_naive", data["comparisons"]["aggregate"])

    def test_trial_context_json_cli_includes_all_strategies(self) -> None:
        result = run_cli("trial-context", "validation/context-selection/manifest.jsonl", "--format", "json", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        expected = set(agent_contracts.CONTEXT_TRIAL_STRATEGIES)

        self.assertEqual(data["schema_version"], agent_contracts.CONTEXT_TRIAL_SCHEMA_VERSION)
        self.assertEqual(data["trial_mode"], "deterministic-simulated-agent-trial")
        self.assertFalse(data["limits"]["live_llm_calls"])
        self.assertEqual({item["name"] for item in data["strategies"]}, expected)
        self.assertEqual(set(data["aggregate"]), expected)
        self.assertTrue(all(set(task["strategies"]) == expected for task in data["tasks"]))
        self.assertIn("module_vs_naive", data["comparisons"]["aggregate"])

    def test_trial_context_text_output_summarizes_outcomes_and_deltas(self) -> None:
        result = run_cli("trial-context", "validation/context-selection/manifest.jsonl", "--format", "text", repo=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deterministic simulated agent trial", result.stdout)
        self.assertIn("## Aggregate By Strategy", result.stdout)
        self.assertIn("## Per Task Trial Outcomes", result.stdout)
        self.assertIn("## Key Deltas", result.stdout)
        self.assertIn("progressive-mcp vs graph-like", result.stdout)

    def test_trial_rules_capture_misleading_edit_and_required_context_failures(self) -> None:
        report = agent_contracts.run_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        noisy = next(task for task in report["tasks"] if task["task_id"] == "noisy-payments-refund")
        contract_guided = next(task for task in report["tasks"] if task["task_id"] == "contract-guided-payments-refund")

        self.assertFalse(noisy["strategies"]["naive"]["trial_success"])
        self.assertIn("misleading-file-edited", noisy["strategies"]["naive"]["failure_reason"])
        self.assertGreater(noisy["strategies"]["graph-like"]["misleading_files_edited"], 0)

        no_contracts = contract_guided["strategies"]["module-no-contracts"]
        self.assertFalse(no_contracts["trial_success"])
        self.assertIn("missing-required-context", no_contracts["failure_reason"])
        self.assertEqual(no_contracts["required_context_read"], 0)
        self.assertGreater(no_contracts["misleading_files_edited"], 0)

        self.assertFalse(contract_guided["strategies"]["graph-like"]["trial_success"])
        self.assertTrue(contract_guided["strategies"]["progressive-mcp"]["trial_success"])
        self.assertEqual(contract_guided["strategies"]["progressive-mcp"]["required_context_read"], 3)

    def test_trial_aggregate_comparisons_show_progressive_beats_graph_like(self) -> None:
        report = agent_contracts.run_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            max_files=80,
            max_bytes=700_000,
        )
        aggregate = report["aggregate"]
        comparison = report["comparisons"]["aggregate"]["progressive_mcp_vs_graph_like"]["deltas"]

        self.assertEqual(aggregate["module"]["trial_success_rate"], 1.0)
        self.assertEqual(aggregate["progressive-mcp"]["trial_success_rate"], 1.0)
        self.assertGreater(comparison["trial_success_rate"], 0)
        self.assertGreater(comparison["misleading_files_edited_reduction"], 0)

    def test_agent_trial_context_mock_json_output_includes_all_strategies(self) -> None:
        result = run_cli(
            "agent-trial-context",
            "validation/context-selection/manifest.jsonl",
            "--mode",
            "mock",
            "--format",
            "json",
            repo=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        expected = set(agent_contracts.AGENT_CONTEXT_TRIAL_STRATEGIES)

        self.assertEqual(data["schema_version"], agent_contracts.AGENT_CONTEXT_TRIAL_SCHEMA_VERSION)
        self.assertEqual(data["mode"], "mock")
        self.assertEqual(data["runs"], 1)
        self.assertEqual({item["name"] for item in data["strategies"]}, expected)
        self.assertEqual(set(data["aggregate"]), expected)
        self.assertTrue(all(set(task["strategies"]) == expected for task in data["tasks"]))
        self.assertEqual(len(data["raw_runs"]), len(data["tasks"]) * len(expected))
        self.assertIn("progressive_mcp_vs_naive", data["comparisons"]["aggregate"])
        self.assertIn("statistical_analysis", data)

    def test_agent_trial_context_mock_text_output_summarizes_results(self) -> None:
        result = run_cli(
            "agent-trial-context",
            "validation/context-selection/manifest.jsonl",
            "--mode",
            "mock",
            "--format",
            "text",
            repo=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("real/simulated agent context-recall trial", result.stdout)
        self.assertIn("Warning: mock mode", result.stdout)
        self.assertIn("## Aggregate Success By Strategy", result.stdout)
        self.assertIn("## Statistical Significance", result.stdout)
        self.assertIn("## Per Task Result Summary", result.stdout)
        self.assertIn("## Key Deltas", result.stdout)
        self.assertIn("progressive-mcp vs graph-like", result.stdout)
        self.assertIn("module vs module-no-contracts", result.stdout)
        self.assertIn("directional_only", result.stdout)

    def test_agent_trial_runs_two_repeated_isolated_runs_per_task_strategy(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        report = agent_contracts.run_agent_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            strategies=("module", "progressive-mcp"),
            runs=2,
            max_files=80,
            max_bytes=700_000,
            timeout_seconds=120,
            mode="mock",
            agent_command=None,
        )

        self.assertEqual(len(report["raw_runs"]), len(rows) * 2 * 2)
        self.assertEqual({run["run_index"] for run in report["raw_runs"]}, {1, 2})
        self.assertEqual(len({run["run_id"] for run in report["raw_runs"]}), len(report["raw_runs"]))
        self.assertTrue(all(run["run_id"].endswith(f"::{run['run_index']}") for run in report["raw_runs"]))
        by_task_strategy: dict[tuple[str, str], list[int]] = {}
        temp_paths: set[str] = set()
        for run in report["raw_runs"]:
            by_task_strategy.setdefault((run["task_id"], run["strategy"]), []).append(run["run_index"])
            temp_paths.add(run["temp_repo_path"])
            self.assertIn("selected_context", run)
            self.assertIn("agent_reported_files_read_paths", run)
            self.assertIn("git_diff", run)
        self.assertTrue(all(sorted(indices) == [1, 2] for indices in by_task_strategy.values()))
        self.assertEqual(len(temp_paths), len(report["raw_runs"]))

    def test_agent_trial_statistical_analysis_json_shape_for_runs_two(self) -> None:
        result = run_cli(
            "agent-trial-context",
            "validation/context-selection/manifest.jsonl",
            "--mode",
            "mock",
            "--runs",
            "2",
            "--format",
            "json",
            repo=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        stats = data["statistical_analysis"]["comparisons"]

        self.assertIn("module_vs_naive", stats)
        progressive = stats["progressive_mcp_vs_naive"]
        self.assertEqual(progressive["n_pairs"], len(data["tasks"]) * 2)
        self.assertIn("success_rate_delta", progressive)
        self.assertIn("misleading_read_delta", progressive)
        self.assertIn("misleading_edit_delta", progressive)
        self.assertIn("files_read_delta", progressive)
        self.assertIn("read_bytes_delta", progressive)
        self.assertIn("required_context_recall_delta", progressive)
        self.assertIn("target_read_recall_delta", progressive)
        self.assertIn("confidence_intervals", progressive)
        self.assertIn("success_rate_delta", progressive["confidence_intervals"])
        self.assertIn(progressive["sample_label"], {"directional_only", "statistical_test_ready"})

    def test_agent_trial_bootstrap_ci_is_deterministic(self) -> None:
        first = agent_contracts.bootstrap_mean_ci([1.0, 0.0, -1.0, 1.0], seed=12345, iterations=200)
        second = agent_contracts.bootstrap_mean_ci([1.0, 0.0, -1.0, 1.0], seed=12345, iterations=200)

        self.assertEqual(first, second)
        self.assertEqual(first["seed"], 12345)
        self.assertLessEqual(first["lower"], first["upper"])

    def test_agent_trial_sign_test_and_small_sample_labels(self) -> None:
        self.assertEqual(agent_contracts.exact_two_sided_sign_test_p_value(3, 1), 0.625)
        self.assertIsNone(agent_contracts.exact_two_sided_sign_test_p_value(0, 0))
        self.assertEqual(agent_contracts.sample_strength_label(1, 1), "insufficient_sample")
        self.assertEqual(agent_contracts.sample_strength_label(4, 4), "directional_only")
        self.assertEqual(agent_contracts.significance_label(0.01, 4, 4), "directional_only")

    def test_agent_trial_does_not_mutate_original_fixtures(self) -> None:
        fixture_file = ROOT / "fixtures" / "noisy-context" / "src" / "payments" / "refund.py"
        before = fixture_file.read_text(encoding="utf-8")

        result = run_cli(
            "agent-trial-context",
            "validation/context-selection/manifest.jsonl",
            "--mode",
            "mock",
            "--strategies",
            "naive,module",
            "--runs",
            "2",
            "--format",
            "json",
            repo=ROOT,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fixture_file.read_text(encoding="utf-8"), before)
        self.assertNotIn("agent-trial mock edit", before)

    def test_agent_trial_input_omits_manifest_ground_truth_keys(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        row = next(item for item in rows if item["task_id"] == "contract-guided-payments-refund")
        repo = ROOT / row["repo_path"]
        selection = agent_contracts.select_context_files_for_strategy(
            repo,
            row,
            "progressive-mcp",
            max_files=80,
            max_bytes=700_000,
        )

        payload = agent_contracts.build_agent_trial_input(
            repo,
            row,
            "progressive-mcp",
            selection,
            run_index=1,
            max_files=80,
            max_bytes=700_000,
        )
        serialized = json.dumps(payload)

        for forbidden in [
            "target_files",
            "relevant_tests",
            "required_context",
            "misleading_files",
            "expected_module",
            "verification_command",
        ]:
            self.assertNotIn(f'"{forbidden}"', serialized)

    def test_agent_trial_runner_template_echo_mode_returns_valid_json(self) -> None:
        payload = {
            "repo_path": str(ROOT / "fixtures" / "python-service"),
            "task": "Update billing payment status behavior and the billing tests.",
            "strategy": "module",
            "available_context": {
                "selected_files": [
                    {"path": "src/billing/api.py", "role": "source"},
                    {"path": "tests/test_billing.py", "role": "test"},
                ]
            },
        }
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "agent_trial_runner_template.py"), "--mode", "echo"],
            cwd=ROOT,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["files_read"], ["src/billing/api.py", "tests/test_billing.py"])
        self.assertEqual(data["files_edited"], [])
        self.assertEqual(data["final_status"], "success")
        self.assertTrue(data["commands_run"])

    def test_agent_trial_progressive_trace_contains_mcp_equivalent_steps(self) -> None:
        report = agent_contracts.run_agent_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            strategies=("progressive-mcp",),
            runs=1,
            max_files=80,
            max_bytes=700_000,
            timeout_seconds=120,
            mode="mock",
            agent_command=None,
        )
        run = next(item for item in report["raw_runs"] if item["task_id"] == "contract-guided-payments-refund")
        operations = [step["operation"] for step in run["trace"]]

        self.assertIn("context_discover", operations)
        self.assertIn("context_read", operations)
        self.assertIn("context_pack", operations)
        self.assertLess(operations.index("context_discover"), operations.index("context_read"))
        self.assertLess(operations.index("context_read"), operations.index("context_pack"))

    def test_agent_trial_contract_guided_mock_scores_contract_strategies(self) -> None:
        report = agent_contracts.run_agent_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            strategies=agent_contracts.AGENT_CONTEXT_TRIAL_STRATEGIES,
            runs=1,
            max_files=80,
            max_bytes=700_000,
            timeout_seconds=120,
            mode="mock",
            agent_command=None,
        )
        runs = {
            item["strategy"]: item
            for item in report["raw_runs"]
            if item["task_id"] == "contract-guided-payments-refund"
        }

        self.assertTrue(runs["module"]["agent_success"])
        self.assertTrue(runs["progressive-mcp"]["agent_success"])
        self.assertFalse(runs["module-no-contracts"]["agent_success"])
        self.assertIn("missing-required-context", runs["module-no-contracts"]["failure_reason"])
        self.assertGreater(
            runs["module"]["required_context_recall_from_reads"],
            runs["module-no-contracts"]["required_context_recall_from_reads"],
        )

    def test_agent_trial_broad_mock_strategies_read_and_edit_misleading_files(self) -> None:
        report = agent_contracts.run_agent_context_trial(
            ROOT / "validation" / "context-selection" / "manifest.jsonl",
            ROOT,
            strategies=agent_contracts.AGENT_CONTEXT_TRIAL_STRATEGIES,
            runs=1,
            max_files=80,
            max_bytes=700_000,
            timeout_seconds=120,
            mode="mock",
            agent_command=None,
        )
        noisy_naive = next(
            item
            for item in report["raw_runs"]
            if item["task_id"] == "noisy-payments-refund" and item["strategy"] == "naive"
        )
        contract_graph = next(
            item
            for item in report["raw_runs"]
            if item["task_id"] == "contract-guided-payments-refund" and item["strategy"] == "graph-like"
        )

        self.assertGreater(noisy_naive["misleading_files_read"], 0)
        self.assertGreater(noisy_naive["misleading_files_edited"], 0)
        self.assertGreater(contract_graph["misleading_files_read"], 0)
        self.assertGreater(contract_graph["misleading_files_edited"], 0)

    def test_agent_trial_subprocess_valid_json_mode(self) -> None:
        result = run_cli(
            "agent-trial-context",
            "validation/context-selection/manifest.jsonl",
            "--mode",
            "subprocess",
            "--agent-command",
            fake_agent_command("fake_agent_valid.py"),
            "--strategies",
            "module",
            "--format",
            "json",
            repo=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["mode"], "subprocess")
        self.assertEqual(set(data["aggregate"]), {"module"})
        self.assertTrue(all(run["returncode"] == 0 for run in data["raw_runs"]))
        self.assertTrue(all(run["agent_success"] for run in data["raw_runs"]))

    def test_agent_trial_subprocess_invalid_json_is_scored_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            one_row_manifest = Path(tmp) / "manifest.jsonl"
            first_row = (ROOT / "validation" / "context-selection" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0]
            one_row_manifest.write_text(first_row + "\n", encoding="utf-8")

            result = run_cli(
                "agent-trial-context",
                str(one_row_manifest),
                "--mode",
                "subprocess",
                "--agent-command",
                fake_agent_command("fake_agent_invalid.py"),
                "--strategies",
                "module",
                "--format",
                "json",
                repo=ROOT,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        run = json.loads(result.stdout)["raw_runs"][0]
        self.assertFalse(run["agent_success"])
        self.assertIn("invalid-json-output", run["runner_failure_reason"])
        self.assertIn("not-json", run["stdout"])

    def test_agent_trial_subprocess_timeout_is_scored_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            one_row_manifest = Path(tmp) / "manifest.jsonl"
            first_row = (ROOT / "validation" / "context-selection" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0]
            one_row_manifest.write_text(first_row + "\n", encoding="utf-8")

            result = run_cli(
                "agent-trial-context",
                str(one_row_manifest),
                "--mode",
                "subprocess",
                "--agent-command",
                fake_agent_command("fake_agent_sleep.py"),
                "--strategies",
                "module",
                "--timeout-seconds",
                "1",
                "--format",
                "json",
                repo=ROOT,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        run = json.loads(result.stdout)["raw_runs"][0]
        self.assertFalse(run["agent_success"])
        self.assertEqual(run["runner_failure_reason"], "agent-timeout")

    def test_context_pack_and_verifier_use_same_file_planning_helper(self) -> None:
        rows = agent_contracts.load_context_selection_manifest(ROOT / "validation" / "context-selection" / "manifest.jsonl")
        row = next(item for item in rows if item["task_id"] == "python-service-billing-status")
        repo = ROOT / row["repo_path"]

        verifier_selection = agent_contracts.select_context_files_for_strategy(
            repo,
            row,
            "module",
            max_files=80,
            max_bytes=700_000,
        )
        pack_plan = agent_contracts.plan_context_pack_files(
            repo,
            row["task"],
            max_files=80,
            max_bytes=700_000,
        )

        self.assertEqual(verifier_selection["included_files"], pack_plan["included_files"])

    def test_mcp_tool_registration(self) -> None:
        tools = agent_contracts_mcp.tool_definitions()
        names = {tool["name"] for tool in tools}
        self.assertEqual(
            names,
            {
                "context_discover",
                "context_read",
                "context_pack",
                "context_verify",
                "context_intent",
                "context_localize",
                "context_read_region",
                "context_expand",
                "context_gate",
                "context_pack_v2",
                "context_explain",
            },
        )
        self.assertNotIn("read_whole_repo", names)

        response = agent_contracts_mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertIsNotNone(response)
        self.assertEqual(response["result"]["tools"][0]["name"], "context_discover")

    def test_mcp_context_discover_smoke(self) -> None:
        payload = agent_contracts_mcp.call_tool(
            "context_discover",
            {"repo": str(ROOT / "fixtures" / "python-service")},
        )
        modules = {module["name"] for module in payload["modules"]}
        self.assertIn("billing", modules)

    def test_mcp_context_read_smoke(self) -> None:
        payload = agent_contracts_mcp.call_tool(
            "context_read",
            {"repo": str(ROOT / "fixtures" / "python-service"), "target": "billing", "section": "tests"},
        )
        self.assertEqual(payload["module"], "billing")
        self.assertEqual(payload["tests"], ["tests/test_billing.py"])

    def test_mcp_context_pack_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "fixtures" / "python-service", target)
            payload = agent_contracts_mcp.call_tool(
                "context_pack",
                {"repo": str(target), "task": "billing", "max_files": 20, "max_bytes": 100_000},
            )

            self.assertEqual(payload["module"]["name"], "billing")
            self.assertTrue(Path(payload["manifest_path"]).exists())
            selected_paths = {item["path"] for item in payload["selected_files"]}
            self.assertIn("src/billing/api.py", selected_paths)
            self.assertNotIn("content", payload["selected_files"][0])

    def test_mcp_context_localize_and_pack_v2_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))
            output = Path(tmp) / "mcp-pack-v2"
            intent = agent_contracts_mcp.call_tool(
                "context_intent",
                {"task": "Fix src/billing/api.py payment_status renewal-required."},
            )
            localize = agent_contracts_mcp.call_tool(
                "context_localize",
                {"repo": str(repo), "task": "Fix src/billing/api.py payment_status renewal-required.", "max_regions": 3},
            )
            expand = agent_contracts_mcp.call_tool(
                "context_expand",
                {"repo": str(repo), "path": "src/billing/api.py"},
            )
            gate = agent_contracts_mcp.call_tool(
                "context_gate",
                {"repo": str(repo), "task": "Fix src/billing/api.py payment_status renewal-required.", "model_profile": "frontier"},
            )
            pack = agent_contracts_mcp.call_tool(
                "context_pack_v2",
                {
                    "repo": str(repo),
                    "task": "Fix src/billing/api.py payment_status renewal-required.",
                    "model_profile": "spark",
                    "output": str(output),
                },
            )
            explain = agent_contracts_mcp.call_tool(
                "context_explain",
                {"repo": str(repo), "task": "Fix src/billing/api.py payment_status renewal-required."},
            )
            self.assertTrue((output / "manifest.json").exists())
            self.assertEqual(Path(pack["manifest_path"]).resolve(), (output / "manifest.json").resolve())

        self.assertEqual(intent["intent"], "bugfix")
        self.assertIn("context_localize", {item["name"] for item in intent["recommended_tools"]})
        self.assertEqual(localize["file_candidates"][0]["path"], "src/billing/api.py")
        self.assertTrue(any(item["path"] == "tests/test_billing.py" for item in expand["neighbors"]))
        self.assertEqual(gate["gate"]["decision"], "inject")
        self.assertEqual(pack["schema_version"], 2)
        self.assertIn("gate", pack)
        self.assertIn("boundaries", pack)
        self.assertIn("included", explain)
        self.assertIn("boundaries", explain)

    def test_mcp_context_verify_smoke(self) -> None:
        payload = agent_contracts_mcp.call_tool(
            "context_verify",
            {
                "repo": str(ROOT),
                "manifest": "validation/context-selection/manifest.jsonl",
                "max_files": 80,
                "max_bytes": 700_000,
            },
        )
        self.assertEqual(payload["aggregate"]["module"]["pass_rate"], 1.0)
        contract_guided = next(task for task in payload["tasks"] if task["task_id"] == "contract-guided-payments-refund")
        self.assertTrue(contract_guided["strategies"]["module"]["passed"])
        self.assertFalse(contract_guided["strategies"]["module-no-contracts"]["passed"])

    def test_verify_context_reports_bad_manifest_and_unknown_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            malformed = Path(tmp) / "malformed.jsonl"
            malformed.write_text('{"task_id": "bad"}\n', encoding="utf-8")
            malformed_result = run_cli("verify-context", str(malformed), "--format", "json", repo=ROOT)
            self.assertEqual(malformed_result.returncode, 2)
            self.assertIn("missing required field", malformed_result.stderr)

            unknown = Path(tmp) / "unknown.jsonl"
            unknown.write_text(
                json.dumps(
                    {
                        "task_id": "missing-fixture",
                        "repo_path": "fixtures/does-not-exist",
                        "task": "Find missing fixture files.",
                        "target_files": ["src/missing.py"],
                        "relevant_tests": [],
                        "allowed_context": [],
                        "misleading_files": [],
                        "expected_module": "missing",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            unknown_result = run_cli("verify-context", str(unknown), "--format", "json", repo=ROOT)
            self.assertEqual(unknown_result.returncode, 2)
            self.assertIn("repo_path", unknown_result.stderr)

    def test_swe_explore_adapter_contract_ranked_outputs_valid_regions(self) -> None:
        self.assertEqual(
            swe_explore_agent_contracts.windows_for_line_count(10, line_window=6, line_overlap=2),
            [(1, 6), (5, 10)],
        )
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(Path(tmp))
            issue_map.write_text(
                json.dumps({"demo__repo-1": "Fix src/billing/api.py payment_status renewal-required."}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "contract-ranked",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["written"], 1)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["instance_id"], "demo__repo-1")
            self.assertEqual(row["explorer"], "agent-contracts:contract-ranked")
            self.assertLessEqual(row["num_regions"], 2)
            self.assertEqual(row["num_regions"], len(row["regions"]))
            self.assertIn("metadata", row)
            self.assertIn("included_files", row["metadata"])
            self.assertIn("trace", row["metadata"])
            self.assertTrue(any(item.get("operation") == "region_rank" for item in row["metadata"]["trace"]))

            repo = repos / "repos" / "demo__repo-1"
            for region in row["regions"]:
                path = repo / region["path"]
                self.assertTrue(path.is_file(), region)
                total_lines = len(path.read_text(encoding="utf-8").splitlines())
                self.assertGreaterEqual(region["start"], 1)
                self.assertLessEqual(region["start"], region["end"])
                self.assertLessEqual(region["end"], total_lines)
                self.assertLessEqual(region["end"] - region["start"] + 1, 6)

            serialized = json.dumps(row)
            self.assertNotIn("ground_truth_only.py", serialized)
            self.assertTrue(
                any(region["path"] == "src/billing/api.py" for region in row["regions"]),
                row["regions"],
            )

    def test_swe_explore_adapter_context_localized_outputs_gate_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(Path(tmp))
            issue_map.write_text(
                json.dumps({"demo__repo-1": "Fix src/billing/api.py payment_status renewal-required."}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "context-localized",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["explorer"], "agent-contracts:context-localized")
            self.assertLessEqual(row["num_regions"], 2)
            self.assertTrue(any(region["path"] == "src/billing/api.py" for region in row["regions"]))
            self.assertTrue(all(region["path"] in set(row["metadata"]["included_files"]) for region in row["regions"]))
            self.assertFalse(
                set(row["metadata"]["included_files"])
                & {item["path"] for item in row["metadata"]["omitted_files"] if "path" in item}
            )
            self.assertIn("context_localized", row["metadata"])
            self.assertIn(row["metadata"]["context_localized"]["gate"]["decision"], {"inject", "advisory", "tool_only", "abstain"})
            self.assertTrue(any(item.get("operation") == "region_emit" for item in row["metadata"]["trace"]))

    def test_swe_explore_context_localized_model_profile_spark_uses_spark_gate_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(Path(tmp))
            issue_map.write_text(
                json.dumps({"demo__repo-1": "Fix src/billing/api.py payment_status renewal-required."}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "context-localized",
                    "--model-profile",
                    "spark",
                    "--top-k",
                    "5",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            row = json.loads(output.read_text(encoding="utf-8"))
            gate = row["metadata"]["context_localized"]["gate"]

            self.assertEqual(summary["model_profile"], "spark")
            self.assertEqual(row["metadata"]["model_profile"]["name"], "spark")
            self.assertEqual(row["metadata"]["context_localized"]["model_profile"]["name"], "spark")
            self.assertEqual(gate["model_profile"]["name"], "spark")
            self.assertEqual(gate["limits"]["max_preloaded_chars"], 12_000)
            self.assertLessEqual(gate["limits"]["max_preloaded_regions"], 3)

    def test_swe_explore_context_localized_precontext_records_gate_and_noise_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, _output = write_tiny_swe_explore_fixture(Path(tmp))
            issue_text = json.loads(issue_map.read_text(encoding="utf-8"))["demo__repo-1"]
            repo = repos / "demo__repo-1"

            precontext, metadata = swe_explore_agent_contracts.build_context_localized_precontext(
                repo,
                issue_text,
                top_k=3,
                max_files=8,
                max_bytes=100_000,
                line_window=6,
                precontext_candidates=4,
                precontext_max_chars=8_000,
                model_profile="spark",
            )

        self.assertIn("Gate decision:", precontext)
        self.assertEqual(metadata["model_profile"]["name"], "spark")
        self.assertIn("gate", metadata)
        self.assertIn(metadata["gate"]["decision"], {"inject", "advisory", "tool_only", "abstain"})
        self.assertIn("selected_bytes", metadata)
        self.assertIn("risk_counts", metadata)
        self.assertIn("noisy_path_count", metadata["risk_counts"])

    def test_swe_explore_context_localized_precontext_hides_paths_for_tool_only_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repos = Path(tmp) / "repos"
            repo = write_context_localization_fixture(repos)
            empty_repo = Path(tmp) / "empty-repo"
            empty_repo.mkdir()

            precontext, metadata = swe_explore_agent_contracts.build_context_localized_precontext(
                repo,
                "Update the billing module behavior.",
                top_k=3,
                max_files=8,
                max_bytes=100_000,
                line_window=6,
                precontext_candidates=4,
                precontext_max_chars=8_000,
                model_profile="spark",
            )
            abstain_precontext, abstain_metadata = swe_explore_agent_contracts.build_context_localized_precontext(
                empty_repo,
                "qzqxj unrelated issue with no matching repository terms",
                top_k=3,
                max_files=8,
                max_bytes=100_000,
                line_window=6,
                precontext_candidates=4,
                precontext_max_chars=8_000,
                model_profile="spark",
            )

        self.assertEqual(metadata["gate"]["decision"], "tool_only")
        self.assertIn("No file names or snippets are preloaded", precontext)
        self.assertNotIn("src/billing/api.py", precontext)
        self.assertNotIn("tests/test_billing.py", precontext)
        self.assertEqual(abstain_metadata["gate"]["decision"], "abstain")
        self.assertEqual(abstain_metadata["regions"], [])
        self.assertIn("audit trail only", abstain_precontext)

    def test_swe_explore_context_localized_precontext_hides_omitted_spark_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = write_context_localization_fixture(Path(tmp))

            precontext, metadata = swe_explore_agent_contracts.build_context_localized_precontext(
                repo,
                "Fix src/billing/api.py payment_status renewal-required and docs/billing.md billing docs.",
                top_k=3,
                max_files=8,
                max_bytes=100_000,
                line_window=6,
                precontext_candidates=4,
                precontext_max_chars=8_000,
                model_profile="spark",
            )

        self.assertEqual(metadata["gate"]["decision"], "inject")
        self.assertIn("src/billing/api.py", metadata["included_files"])
        self.assertIn("docs/billing.md", metadata["included_files"])
        self.assertEqual(metadata["preloaded_files"], ["src/billing/api.py"])
        self.assertIn("src/billing/api.py", precontext)
        self.assertNotIn("- docs/billing.md", precontext)
        self.assertNotIn("- requests/packages/urllib3/connectionpool.py", precontext)
        self.assertTrue(
            any(item["path"] == "docs/billing.md" for item in metadata["omitted_regions"]),
            metadata["omitted_regions"],
        )
        self.assertTrue(
            any(item["path"] == "requests/packages/urllib3/connectionpool.py" for item in metadata["omitted_regions"]),
            metadata["omitted_regions"],
        )

    def test_swe_explore_context_localized_regions_respect_file_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "context-localized",
                    "--top-k",
                    "3",
                    "--max-files",
                    "1",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(output.read_text(encoding="utf-8"))
            included = set(row["metadata"]["included_files"])
            self.assertLessEqual(len(included), 1)
            self.assertTrue(all(region["path"] in included for region in row["regions"]))
            self.assertTrue(row["metadata"]["omitted_files"])

    def test_swe_explore_context_localized_honors_tool_only_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(Path(tmp))
            issue_map.write_text(
                json.dumps({"demo__repo-1": "Update the billing module behavior."}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "context-localized",
                    "--top-k",
                    "3",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["metadata"]["context_localized"]["gate"]["decision"], "tool_only")
            self.assertEqual(row["regions"], [])
            self.assertEqual(row["num_regions"], 0)
            self.assertFalse(any(item.get("operation") == "region_rank" for item in row["metadata"]["trace"]))

    def test_swe_beat_sota2_noisy_filter_flags_requests_style_vendored_dependencies(self) -> None:
        self.assertTrue(
            swe_explore_agent_contracts.beat_sota2_is_noisy_precontext_path(
                "requests/packages/urllib3/connectionpool.py"
            )
        )
        self.assertTrue(
            swe_explore_agent_contracts.beat_sota2_is_noisy_precontext_path(
                "requests/packages/chardet/compat.py"
            )
        )
        self.assertFalse(
            swe_explore_agent_contracts.beat_sota2_is_noisy_precontext_path(
                "packages/api/src/server.py"
            )
        )
        self.assertFalse(
            swe_explore_agent_contracts.beat_sota2_is_noisy_precontext_path(
                "src/packages/api/server.py"
            )
        )
        self.assertFalse(
            swe_explore_agent_contracts.beat_sota2_is_noisy_precontext_path(
                "project/packages/api/server.py"
            )
        )

    def test_vendored_dependency_layout_path_only_flags_requests_style_packages(self) -> None:
        positive_paths = [
            "requests/packages/urllib3/connectionpool.py",
            "requests/packages/chardet/compat.py",
        ]
        ordinary_package_paths = [
            "packages/api/src/server.py",
            "src/packages/api/server.py",
            "project/packages/api/server.py",
            "src/external/server.py",
            "src/deps/server.py",
        ]
        root_dependency_paths = [
            "external/urllib3/connectionpool.py",
            "deps/chardet/compat.py",
        ]

        for path in positive_paths:
            with self.subTest(path=path):
                self.assertTrue(agent_contracts.is_vendored_dependency_layout_path(path))
                self.assertIn("vendor_generated_static", agent_contracts.context_path_risk_flags(path))

        for path in root_dependency_paths:
            with self.subTest(path=path):
                self.assertTrue(agent_contracts.is_vendored_dependency_layout_path(path))
                self.assertIn("vendor_generated_static", agent_contracts.context_path_risk_flags(path))

        for path in ordinary_package_paths:
            with self.subTest(path=path):
                self.assertFalse(agent_contracts.is_vendored_dependency_layout_path(path))
                self.assertNotIn("vendor_generated_static", agent_contracts.context_path_risk_flags(path))

    def test_swe_explore_adapter_beat_sota1_outputs_scored_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "beat-sota1",
                    "--top-k",
                    "3",
                    "--line-window",
                    "4",
                    "--line-overlap",
                    "1",
                    "--beat-sota1-regions-per-file",
                    "2",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["explorer"], "agent-contracts:beat-sota1")
            self.assertLessEqual(row["num_regions"], 3)
            self.assertTrue(any(region["path"] == "src/billing/api.py" for region in row["regions"]))
            self.assertIn("beat_sota1", row["metadata"])
            self.assertTrue(row["metadata"]["beat_sota1"]["candidate_files"])
            self.assertTrue(
                any(item.get("operation") == "active_expansion" for item in row["metadata"]["trace"]),
                row["metadata"]["trace"],
            )

    def test_swe_explore_adapter_phase1_hybrid_outputs_provider_fusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "phase1-hybrid",
                    "--model-profile",
                    "mini",
                    "--top-k",
                    "3",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--beat-sota1-regions-per-file",
                    "2",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["explorer"], "agent-contracts:phase1-hybrid")
            self.assertLessEqual(row["num_regions"], 3)
            self.assertTrue(any(region["path"] == "src/billing/api.py" for region in row["regions"]))
            self.assertIn("phase1_hybrid", row["metadata"])
            self.assertIn("beat_sota1", row["metadata"]["phase1_hybrid"]["providers"])
            self.assertTrue(any(item.get("operation") == "region_fusion" for item in row["metadata"]["trace"]))
            fused_regions = row["metadata"]["phase1_hybrid"]["regions"]
            self.assertTrue(fused_regions)
            self.assertTrue(any("issue_localizer" in item["providers"] or "beat_sota1" in item["providers"] for item in fused_regions))
            self.assertIn(
                row["metadata"]["phase1_hybrid"]["gate"]["decision"],
                {"advisory_paths", "advisory_regions", "advisory_snippets", "tool_only", "abstain"},
            )

    def test_swe_explore_phase1_fuses_near_overlapping_provider_regions(self) -> None:
        fused: dict[tuple[str, int, int], dict[str, Any]] = {}

        swe_explore_agent_contracts.phase1_add_fused_region(
            fused,
            {"path": "src/billing/api.py", "start": 10, "end": 32},
            provider="issue_localizer",
            rank=1,
            score_hint=900,
            strength="strong",
            role="source",
            risk_flags=[],
            evidence=[{"kind": "symbol", "value": "payment_status", "weight": 560}],
            reasons=["symbol:payment_status"],
            direct=True,
        )
        swe_explore_agent_contracts.phase1_add_fused_region(
            fused,
            {"path": "src/billing/api.py", "start": 28, "end": 48},
            provider="beat_sota1",
            rank=2,
            score_hint=700,
            strength="strong",
            role="source",
            risk_flags=[],
            evidence=[{"kind": "beat_sota1_reason", "value": "symbol:payment_status"}],
            reasons=["symbol:payment_status"],
            direct=True,
        )
        swe_explore_agent_contracts.phase1_add_fused_region(
            fused,
            {"path": "src/billing/api.py", "start": 180, "end": 210},
            provider="beat_sota1",
            rank=3,
            score_hint=300,
            strength="medium",
            role="source",
            risk_flags=[],
            evidence=[{"kind": "information_scent", "value": "billing"}],
            reasons=["behavior-token:billing"],
            direct=False,
        )

        regions = swe_explore_agent_contracts.phase1_finalize_fused_regions(fused, top_k=3)
        merged = next(region for region in regions if region["start"] == 10)

        self.assertEqual(merged["end"], 48)
        self.assertEqual(merged["providers"], ["issue_localizer", "beat_sota1"])
        self.assertEqual(merged["provider_count"], 2)
        self.assertTrue(any(region["start"] == 180 for region in regions))

    def test_swe_explore_phase1_ranks_source_direct_before_broad_test_scent(self) -> None:
        fused: dict[tuple[str, int, int], dict[str, Any]] = {}

        swe_explore_agent_contracts.phase1_add_fused_region(
            fused,
            {"path": "testing/test_billing.py", "start": 100, "end": 140},
            provider="issue_localizer",
            rank=1,
            score_hint=1_200,
            strength="strong",
            role="test",
            risk_flags=[],
            evidence=[{"kind": "information_scent", "value": "billing"}],
            reasons=["behavior-token:billing"],
            direct=False,
        )
        swe_explore_agent_contracts.phase1_add_fused_region(
            fused,
            {"path": "testing/test_billing.py", "start": 102, "end": 142},
            provider="beat_sota1",
            rank=2,
            score_hint=1_000,
            strength="strong",
            role="test",
            risk_flags=[],
            evidence=[{"kind": "information_scent", "value": "billing"}],
            reasons=["behavior-token:billing"],
            direct=False,
        )
        swe_explore_agent_contracts.phase1_add_fused_region(
            fused,
            {"path": "src/billing/api.py", "start": 20, "end": 42},
            provider="issue_localizer",
            rank=3,
            score_hint=650,
            strength="strong",
            role="source",
            risk_flags=[],
            evidence=[{"kind": "symbol", "value": "payment_status", "weight": 560}],
            reasons=["symbol:payment_status"],
            direct=True,
        )

        regions = swe_explore_agent_contracts.phase1_finalize_fused_regions(fused, top_k=2)

        self.assertEqual(regions[0]["path"], "src/billing/api.py")
        self.assertTrue(regions[1]["broad_scent"])

    def test_swe_explore_phase1_demotes_docs_config_and_test_fixtures_without_direct_evidence(self) -> None:
        fused: dict[tuple[str, int, int], dict[str, Any]] = {}

        swe_explore_agent_contracts.phase1_add_fused_region(
            fused,
            {"path": "doc/conf.py", "start": 1, "end": 80},
            provider="issue_localizer",
            rank=1,
            score_hint=2_000,
            strength="strong",
            role="source",
            risk_flags=["docs"],
            evidence=[{"kind": "information_scent", "value": "html"}],
            reasons=["behavior-token:html"],
            direct=False,
        )
        swe_explore_agent_contracts.phase1_add_fused_region(
            fused,
            {"path": "src/billing/api.py", "start": 20, "end": 42},
            provider="issue_localizer",
            rank=2,
            score_hint=450,
            strength="strong",
            role="source",
            risk_flags=[],
            evidence=[{"kind": "symbol", "value": "payment_status", "weight": 560}],
            reasons=["symbol:payment_status"],
            direct=True,
        )

        regions = swe_explore_agent_contracts.phase1_finalize_fused_regions(fused, top_k=2)

        self.assertEqual(regions[0]["path"], "src/billing/api.py")
        self.assertEqual(next(region for region in regions if region["path"] == "doc/conf.py")["role"], "docs")
        self.assertTrue(next(region for region in regions if region["path"] == "doc/conf.py")["broad_scent"])

    def test_swe_explore_phase1_hybrid_gate_downgrades_inject_to_advisory(self) -> None:
        gate = swe_explore_agent_contracts.phase1_hybrid_gate(
            {
                "decision": "inject",
                "confidence": "high",
                "fallback": "preloaded_context",
                "limits": {"max_preloaded_regions": 8, "max_preloaded_chars": 24_000},
                "reasons": ["localizer high confidence"],
            },
            [
                {
                    "path": "src/billing/api.py",
                    "start": 9,
                    "end": 12,
                    "role": "source",
                    "strength": "strong",
                    "provider_count": 2,
                    "risk_flags": [],
                    "direct": True,
                }
            ],
            agent_contracts.model_profile_payload("mini"),
        )

        self.assertEqual(gate["decision"], "advisory_snippets")
        self.assertIn("phase1 hybrid downgrades inject to advisory snippets to avoid anchoring", gate["reasons"])

    def test_swe_explore_phase1_hybrid_gate_uses_region_hints_for_medium_direct_context(self) -> None:
        gate = swe_explore_agent_contracts.phase1_hybrid_gate(
            {
                "decision": "advisory",
                "confidence": "medium",
                "fallback": "progressive_tools",
                "limits": {"max_preloaded_regions": 4, "max_preloaded_chars": 16_000},
                "reasons": ["localizer medium confidence"],
            },
            [
                {
                    "path": "src/billing/api.py",
                    "start": 9,
                    "end": 12,
                    "role": "source",
                    "strength": "medium",
                    "provider_count": 2,
                    "risk_flags": [],
                    "direct": True,
                }
            ],
            agent_contracts.model_profile_payload("mini"),
        )

        self.assertEqual(gate["decision"], "advisory_regions")
        self.assertEqual(gate["limits"]["max_preloaded_regions"], 4)
        self.assertEqual(gate["limits"]["max_preloaded_chars"], 0)
        self.assertIn("phase1 balanced source evidence allows line hints without snippets", gate["reasons"])

    def test_swe_explore_phase1_hybrid_gate_uses_path_hints_for_weak_source_context(self) -> None:
        gate = swe_explore_agent_contracts.phase1_hybrid_gate(
            {
                "decision": "tool_only",
                "confidence": "low",
                "fallback": "baseline_search",
                "limits": {"max_preloaded_regions": 0, "max_preloaded_chars": 0},
                "reasons": ["localizer low confidence"],
            },
            [
                {
                    "path": "src/billing/api.py",
                    "start": 20,
                    "end": 42,
                    "role": "source",
                    "strength": "medium",
                    "provider_count": 2,
                    "risk_flags": [],
                    "direct": False,
                },
            ],
            agent_contracts.model_profile_payload("frontier"),
        )

        self.assertEqual(gate["decision"], "advisory_paths")
        self.assertEqual(gate["limits"]["max_preloaded_regions"], 0)
        self.assertIn("phase1 frontier profile allows provider-backed file-path hints only", gate["reasons"])

    def test_swe_explore_phase1_region_precontext_has_no_snippets(self) -> None:
        selection = {
            "included_files": ["src/billing/api.py"],
            "selected_bytes": 1200,
            "trace": [],
            "phase1_hybrid": {
                "model_profile": agent_contracts.model_profile_payload("mini"),
                "gate": {
                    "decision": "advisory_regions",
                    "confidence": "medium",
                    "fallback": "progressive_tools",
                    "limits": {"max_preloaded_regions": 1, "max_preloaded_chars": 0},
                },
                "localizer_gate": {},
                "localizer_confidence": "medium",
                "providers": ["baseline", "issue_localizer"],
                "candidate_files": [{"path": "src/billing/api.py", "role": "source"}],
                "regions": [
                    {
                        "path": "src/billing/api.py",
                        "start": 20,
                        "end": 42,
                        "score": 900,
                        "strength": "strong",
                        "providers": ["baseline", "issue_localizer"],
                        "provider_count": 2,
                        "risk_flags": [],
                        "reasons": ["symbol:payment_status"],
                    }
                ],
            },
        }

        text, metadata = swe_explore_agent_contracts.phase1_format_precontext(
            Path("/tmp/unused"),
            "Payment status fails",
            selection=selection,
            top_k=2,
            precontext_candidates=2,
            precontext_max_chars=8_000,
        )

        self.assertIn("src/billing/api.py:20-42", text)
        self.assertNotIn("```", text)
        self.assertEqual(metadata["exposure_level"], "advisory_regions")

    def test_swe_explore_adapter_beat_sota1_ablation_disables_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(Path(tmp))

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "beat-sota1",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--ablation",
                    "no-active",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["metadata"]["limits"]["ablations"], ["no-active"])
            self.assertFalse(any(item.get("operation") == "active_expansion" for item in row["metadata"]["trace"]))

    def test_quick_swe_spark_eval_dry_run_prepares_subset_and_paired_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, _output = write_tiny_swe_explore_fixture(tmp_path)
            output_dir = tmp_path / "quick"

            result = subprocess.run(
                [
                    sys.executable,
                    str(QUICK_SPARK_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--output-dir",
                    str(output_dir),
                    "--fallback-limit",
                    "1",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["mode"], "dry-run")
            self.assertEqual(summary["instances"], ["demo__repo-1"])
            self.assertTrue(Path(summary["subset_bench"]).is_file())
            self.assertTrue(Path(summary["subset_issue_map"]).is_file())
            condition_names = {item["condition"] for item in summary["conditions"]}
            self.assertIn("spark_baseline", condition_names)
            self.assertIn("context_localized_preflight", condition_names)
            self.assertIn("spark_context_localized", condition_names)
            commands = "\n".join(item["command"] for item in summary["conditions"])
            self.assertIn("--strategy codex-baseline", commands)
            self.assertIn("--strategy context-localized", commands)
            self.assertIn("--strategy codex-context-localized", commands)
            self.assertIn("--eval-bench", commands)
            self.assertIn(str(bench.resolve()), commands)
            self.assertTrue(any("metadata.context_localized.gate" in command for command in summary["inspect"]))

    def test_quick_swe_spark_eval_run_evaluate_uses_original_eval_harness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, _output = write_tiny_swe_explore_fixture(tmp_path)
            output_dir = tmp_path / "quick"
            (tmp_path / "eval.py").write_text(
                "\n".join(
                    [
                        "class ExploreEvaluator:",
                        "    def __init__(self, bench_path, file_line_counts=None):",
                        "        self.bench_path = bench_path",
                        "        self.file_line_counts = file_line_counts or {}",
                        "    def __getattr__(self, name):",
                        "        if name.startswith('evaluate_'):",
                        "            return lambda preds, ground_truth: 1.0",
                        "        raise AttributeError(name)",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(QUICK_SPARK_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--output-dir",
                    str(output_dir),
                    "--condition",
                    "context_localized_preflight",
                    "--fallback-limit",
                    "1",
                    "--run",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((output_dir / "eval.py").exists())
            row = json.loads((output_dir / "context_localized_preflight" / "top5.jsonl").read_text(encoding="utf-8"))
            self.assertIn("metrics", row)
            self.assertEqual(row["metrics"]["precision"], 1.0)

    def test_quick_swe_spark_eval_times_out_hung_condition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, _output = write_tiny_swe_explore_fixture(tmp_path)
            output_dir = tmp_path / "quick"

            result = subprocess.run(
                [
                    sys.executable,
                    str(QUICK_SPARK_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--output-dir",
                    str(output_dir),
                    "--condition",
                    "spark_baseline",
                    "--spark-codex-command",
                    f"{shlex.quote(sys.executable)} -c \"import time; time.sleep(30)\"",
                    "--condition-timeout",
                    "1",
                    "--fallback-limit",
                    "1",
                    "--run",
                    "--no-evaluate",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, quick_swe_spark_eval.TIMEOUT_EXIT_CODE)
        self.assertIn("Condition spark_baseline timed out after 1s", result.stderr)

    def test_vexp_codex_agent_contracts_adapter_uses_v2_and_registers_legacy(self) -> None:
        if not VEXP_AGENT_CONTRACTS_ADAPTER.exists() or not VEXP_AGENT_REGISTRY.exists():
            self.skipTest("local ignored Vexp SWE-bench harness copy is not present")
        adapter = VEXP_AGENT_CONTRACTS_ADAPTER.read_text(encoding="utf-8")
        registry = VEXP_AGENT_REGISTRY.read_text(encoding="utf-8")
        v2_segment, legacy_segment = adapter.split("async function buildLegacyAgentContractsPrompt", 1)

        self.assertIn('"context-pack-v2"', v2_segment)
        self.assertIn('"--model-profile"', v2_segment)
        self.assertIn('?? "spark"', v2_segment)
        self.assertNotIn('"context-read"', v2_segment)
        self.assertNotIn('"context-pack",', v2_segment)
        self.assertNotIn('"context-discover"', v2_segment)
        self.assertIn('"context-pack"', legacy_segment)
        self.assertIn('"context-read"', legacy_segment)
        self.assertIn('"codex-agent-contracts": () => new CodexAgentContractsAdapter()', registry)
        self.assertIn(
            '"codex-agent-contracts-legacy": () => new CodexAgentContractsLegacyAdapter()',
            registry,
        )

    def test_swe_restricted_repair_mock_consumes_prediction_regions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, predictions = write_tiny_swe_explore_fixture(tmp_path)
            repair_output = tmp_path / "repair.jsonl"
            predictions.write_text(
                json.dumps(
                    {
                        "instance_id": "demo__repo-1",
                        "explorer": "agent-contracts:beat-sota1",
                        "regions": [{"path": "src/billing/api.py", "start": 9, "end": 12}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(REPAIR_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--predictions",
                    str(predictions),
                    "--output",
                    str(repair_output),
                    "--mode",
                    "mock",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["written"], 1)
            self.assertEqual(summary["resolved"], 1)
            row = json.loads(repair_output.read_text(encoding="utf-8"))
            self.assertEqual(row["prediction_explorer"], "agent-contracts:beat-sota1")
            self.assertTrue(row["resolved"])
            self.assertEqual(row["region_count"], 1)
            self.assertGreater(row["context_bytes"], 0)
            self.assertEqual(row["patch_bytes"], 0)
            self.assertEqual(row["patch_lines"], 0)
            self.assertEqual(row["files_edited_count"], 1)

    def test_swe_explore_adapter_output_order_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench, repos, issue_map, first_output = write_tiny_swe_explore_fixture(Path(tmp))
            second_output = Path(tmp) / "results-second.jsonl"
            common_args = [
                sys.executable,
                str(SWE_SCRIPT),
                "--bench",
                str(bench),
                "--repos",
                str(repos),
                "--issue-map",
                str(issue_map),
                "--strategy",
                "contract-ranked",
                "--top-k",
                "3",
                "--line-window",
                "6",
                "--line-overlap",
                "2",
            ]

            first = subprocess.run(
                [*common_args, "--output", str(first_output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            second = subprocess.run(
                [*common_args, "--output", str(second_output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                first_output.read_text(encoding="utf-8"),
                second_output.read_text(encoding="utf-8"),
            )

    def test_swe_explore_codex_agent_contracts_uses_precontext_and_parses_regions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(tmp_path)
            prompt_log = tmp_path / "prompt.txt"
            fake_codex = tmp_path / "fake_codex.py"
            fake_codex.write_text(
                "\n".join(
                    [
                        "import json",
                        "import pathlib",
                        "import sys",
                        "prompt = sys.stdin.read()",
                        "pathlib.Path(sys.argv[1]).write_text(prompt, encoding='utf-8')",
                        "print('```json')",
                        "print(json.dumps({'regions': [{'path': 'src/billing/api.py', 'start': 9, 'end': 12, 'reason': 'payment status logic'}]}))",
                        "print('```')",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(fake_codex))} {shlex.quote(str(prompt_log))}"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "codex-agent-contracts",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--codex-command",
                    command,
                    "--codex-timeout",
                    "5",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["written"], 1)
            prompt = prompt_log.read_text(encoding="utf-8")
            self.assertIn("agent-contracts pre-context", prompt)
            self.assertIn("Payment status should validate", prompt)

            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["explorer"], "agent-contracts:codex-agent-contracts")
            self.assertEqual(row["regions"], [{"path": "src/billing/api.py", "start": 9, "end": 12}])
            self.assertEqual(row["metadata"]["condition"], "codex-agent-contracts")
            self.assertIn("agent_contracts_precontext", row["metadata"]["allowed_inputs"])
            self.assertIsNone(row["metadata"]["runner_error"])
            self.assertTrue(row["metadata"]["precontext"]["regions"])

    def test_swe_explore_codex_command_format_preserves_paths_with_spaces(self) -> None:
        repo = Path("/tmp/repo with spaces")
        response_file = Path("/tmp/result path/response.json")
        unquoted = swe_explore_agent_contracts.format_codex_command(
            "codex exec --output-last-message {response_file} -C {repo} --instance {instance_id}",
            repo=repo,
            response_file=response_file,
            instance_id="psf__requests-5414",
        )
        quoted = swe_explore_agent_contracts.format_codex_command(
            "codex exec --output-last-message '{response_file}' -C \"{repo}\" --instance {instance_id}",
            repo=repo,
            response_file=response_file,
            instance_id="psf__requests-5414",
        )

        expected = [
            "codex",
            "exec",
            "--output-last-message",
            "/tmp/result path/response.json",
            "-C",
            "/tmp/repo with spaces",
            "--instance",
            "psf__requests-5414",
        ]
        self.assertEqual(unquoted, expected)
        self.assertEqual(quoted, expected)

    def test_swe_explore_codex_beat_sota1_uses_compact_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(tmp_path)
            prompt_log = tmp_path / "prompt.txt"
            fake_codex = tmp_path / "fake_codex.py"
            fake_codex.write_text(
                "\n".join(
                    [
                        "import json",
                        "import pathlib",
                        "import sys",
                        "prompt = sys.stdin.read()",
                        "pathlib.Path(sys.argv[1]).write_text(prompt, encoding='utf-8')",
                        "print(json.dumps({'regions': [{'path': 'src/billing/api.py', 'start': 9, 'end': 12, 'reason': 'reranked candidate'}]}))",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(fake_codex))} {shlex.quote(str(prompt_log))}"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "codex-beat-sota1",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--beat-sota1-precontext-candidates",
                    "4",
                    "--codex-command",
                    command,
                    "--codex-timeout",
                    "5",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = prompt_log.read_text(encoding="utf-8")
            self.assertIn("beatSOTA1 candidates", prompt)
            self.assertIn("score=", prompt)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["explorer"], "agent-contracts:codex-beat-sota1")
            self.assertEqual(row["regions"], [{"path": "src/billing/api.py", "start": 9, "end": 12}])
            self.assertIn("beat_sota1_precontext", row["metadata"]["allowed_inputs"])
            self.assertTrue(row["metadata"]["precontext"]["beat_sota1"]["candidate_files"])

    def test_swe_explore_codex_beat_sota2_uses_advisory_gated_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(tmp_path)
            prompt_log = tmp_path / "prompt.txt"
            fake_codex = tmp_path / "fake_codex.py"
            fake_codex.write_text(
                "\n".join(
                    [
                        "import json",
                        "import pathlib",
                        "import sys",
                        "prompt = sys.stdin.read()",
                        "pathlib.Path(sys.argv[1]).write_text(prompt, encoding='utf-8')",
                        "print(json.dumps({'regions': [{'path': 'src/billing/api.py', 'start': 9, 'end': 12, 'reason': 'independent hypothesis checked'}]}))",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(fake_codex))} {shlex.quote(str(prompt_log))}"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "codex-beat-sota2",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--beat-sota1-precontext-candidates",
                    "4",
                    "--codex-command",
                    command,
                    "--codex-timeout",
                    "5",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = prompt_log.read_text(encoding="utf-8")
            self.assertIn("beatSOTA2 advisory evidence", prompt)
            self.assertIn("Independent-first workflow", prompt)
            self.assertIn("Candidate evidence is advisory", prompt)
            self.assertNotIn("primary evidence set", prompt)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["explorer"], "agent-contracts:codex-beat-sota2")
            self.assertEqual(row["regions"], [{"path": "src/billing/api.py", "start": 9, "end": 12}])
            self.assertIn("beat_sota2_precontext", row["metadata"]["allowed_inputs"])
            self.assertEqual(row["metadata"]["precontext"]["strategy"], "beat-sota2")
            self.assertIn("confidence", row["metadata"]["precontext"])

    def test_swe_explore_codex_phase1_hybrid_uses_provider_fused_precontext(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, output = write_tiny_swe_explore_fixture(tmp_path)
            issue_map.write_text(
                json.dumps(
                    {
                        "demo__repo-1": (
                            "User token validation returns the wrong renewal state."
                        )
                    }
                ),
                encoding="utf-8",
            )
            prompt_log = tmp_path / "prompt.txt"
            fake_codex = tmp_path / "fake_codex.py"
            fake_codex.write_text(
                "\n".join(
                    [
                        "import json",
                        "import pathlib",
                        "import sys",
                        "prompt = sys.stdin.read()",
                        "pathlib.Path(sys.argv[1]).write_text(prompt, encoding='utf-8')",
                        "print(json.dumps({'regions': [{'path': 'src/billing/api.py', 'start': 9, 'end': 12, 'reason': 'provider-fused evidence checked'}]}))",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(fake_codex))} {shlex.quote(str(prompt_log))}"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SWE_SCRIPT),
                    "--bench",
                    str(bench),
                    "--repos",
                    str(repos),
                    "--issue-map",
                    str(issue_map),
                    "--strategy",
                    "codex-phase1-hybrid",
                    "--model-profile",
                    "mini",
                    "--top-k",
                    "2",
                    "--line-window",
                    "6",
                    "--line-overlap",
                    "2",
                    "--beat-sota1-precontext-candidates",
                    "4",
                    "--codex-command",
                    command,
                    "--codex-timeout",
                    "5",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            prompt = prompt_log.read_text(encoding="utf-8")
            self.assertIn("Phase 1 hybrid advisory evidence", prompt)
            self.assertIn("Provider agreement", prompt)
            row = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(row["explorer"], "agent-contracts:codex-phase1-hybrid")
            self.assertEqual(row["regions"], [{"path": "src/billing/api.py", "start": 9, "end": 12}])
            self.assertIn("phase1_hybrid_precontext", row["metadata"]["allowed_inputs"])
            self.assertEqual(row["metadata"]["precontext"]["strategy"], "phase1-hybrid")
            self.assertIn("providers", row["metadata"]["precontext"])

    def test_swe_explore_codex_phase1_hybrid_tool_only_uses_baseline_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bench, repos, issue_map, _output = write_tiny_swe_explore_fixture(tmp_path)
            record = swe_explore_agent_contracts.load_jsonl(bench)[0]
            issue_text = json.loads(issue_map.read_text(encoding="utf-8"))[record["instance_id"]]
            prompt_log = tmp_path / "prompt.txt"
            fake_codex = tmp_path / "fake_codex.py"
            fake_codex.write_text(
                "\n".join(
                    [
                        "import json",
                        "import pathlib",
                        "import sys",
                        "prompt = sys.stdin.read()",
                        "pathlib.Path(sys.argv[1]).write_text(prompt, encoding='utf-8')",
                        "print(json.dumps({'regions': [{'path': 'src/billing/api.py', 'start': 9, 'end': 12, 'reason': 'baseline fallback'}]}))",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            command = f"{shlex.quote(sys.executable)} {shlex.quote(str(fake_codex))} {shlex.quote(str(prompt_log))}"

            with mock.patch.object(
                swe_explore_agent_contracts,
                "build_phase1_hybrid_precontext",
                return_value=(
                    "",
                    {
                        "strategy": "phase1-hybrid",
                        "gate": {"decision": "tool_only"},
                        "regions": [],
                        "providers": ["issue_localizer", "beat_sota1"],
                    },
                ),
            ):
                row = swe_explore_agent_contracts.run_codex_explorer(
                    record,
                    issue_text,
                    repos / "repos" / record["instance_id"],
                    "codex-phase1-hybrid",
                    top_k=2,
                    max_files=80,
                    max_bytes=700_000,
                    line_window=6,
                    line_overlap=2,
                    codex_command=command,
                    codex_timeout=5,
                    beat_sota1_regions_per_file=3,
                    beat_sota1_expansion_rounds=2,
                    beat_sota1_precontext_candidates=4,
                    beat_sota1_precontext_max_chars=8_000,
                    ablations=set(),
                    model_profile="mini",
                )

            prompt = prompt_log.read_text(encoding="utf-8")
            expected_prompt = swe_explore_agent_contracts.build_codex_prompt(
                instance_id=record["instance_id"],
                issue_text=issue_text,
                strategy="codex-baseline",
                top_k=2,
                line_window=6,
                precontext=None,
            )
            self.assertEqual(prompt, expected_prompt)
            self.assertIn("Condition: codex-baseline", prompt)
            self.assertNotIn("Phase 1 hybrid advisory evidence", prompt)
            self.assertNotIn("phase1_hybrid_precontext", row["metadata"]["allowed_inputs"])
            self.assertEqual(row["metadata"]["prompt_strategy"], "codex-baseline")
            self.assertEqual(row["metadata"]["precontext"]["gate"]["decision"], "tool_only")

    def test_node_launcher_skips_unsupported_python3_when_supported_python_exists(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_log = tmp_path / "old-python-ran.txt"
            selected_log = tmp_path / "selected-python-args.txt"
            fake_python3 = tmp_path / "python3"
            fake_python = tmp_path / "python"

            fake_python3.write_text(
                "\n".join(
                    [
                        f"#!{sys.executable}",
                        "import pathlib",
                        "import sys",
                        "if sys.argv[1:2] == ['-c']:",
                        "    print('3.9.10')",
                        "    raise SystemExit(0)",
                        f"pathlib.Path({str(old_log)!r}).write_text('unsupported python3 ran\\n', encoding='utf-8')",
                        "raise SystemExit(77)",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            fake_python.write_text(
                "\n".join(
                    [
                        f"#!{sys.executable}",
                        "import pathlib",
                        "import sys",
                        "if sys.argv[1:2] == ['-c']:",
                        "    print('3.10.14')",
                        "    raise SystemExit(0)",
                        f"pathlib.Path({str(selected_log)!r}).write_text('\\n'.join(sys.argv[1:]) + '\\n', encoding='utf-8')",
                        "print('selected-python')",
                        "raise SystemExit(0)",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            fake_python3.chmod(0o755)
            fake_python.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = str(tmp_path)
            result = subprocess.run(
                [node, str(ROOT / "bin" / "agent-contracts.js"), "--help"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("selected-python", result.stdout)
            self.assertFalse(old_log.exists(), "unsupported python3 should be probed but not used to run the CLI")
            selected_args = selected_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(selected_args[0], str(SCRIPT))
            self.assertEqual(selected_args[1], "--help")


if __name__ == "__main__":
    unittest.main()
