# Agent Quality MCP Public Release Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agent Quality MCP something an external person can legally use, install with working validators by default, discover through the standard MCP/PyPI channels, and safely report problems against — without changing how a patch is judged.

**Architecture:** No change to the validation/decision pipeline (`decision.py`, `grouping.py`, `actions.py`, `risk.py`, `response.py`, `service.py`'s orchestration, or any LSP/session code). All changes are either new files (license/policy/CI/registry) or small, additive changes to `pyproject.toml`, `server.py`, `models.py`, and `tools.py`.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, FastMCP/`mcp` SDK, `argparse` (stdlib), GitHub Actions, PyPI Trusted Publishing (`pypa/gh-action-pypi-publish`), official MCP Registry (`mcp-publisher`).

---

## Source Spec

Use this spec as the implementation contract for this plan (Phase 3a only;
Phases 3b-3d are explicitly out of scope for this plan):

- `docs/superpowers/specs/2026-06-30-agent-quality-mcp-public-release-readiness-design.md`

## Scope Check

This plan covers one subsystem: making the project releasable and usable by
external people. It adds licensing/policy/community files, fixes the
`ruff`/`pyright` runtime-dependency gap, adds MCP schema descriptions, fixes
the CLI entrypoint hang, adds a tag-driven PyPI + MCP Registry release
pipeline, and overhauls the README for discoverability. It does **not**
touch `decision.py`, `grouping.py`, `actions.py`, `risk.py`, `response.py`,
the LSP/session code, `patching.py`, or any validator behavior. It does
**not** add a `Dockerfile`, rename the PyPI distribution, add new
validators, or change the MCP transport — those are deferred per the
spec's Resolved Decisions.

## File Structure

- Create: `LICENSE`
- Create: `SECURITY.md`
- Create: `CONTRIBUTING.md`
- Create: `CHANGELOG.md`
- Create: `CODE_OF_CONDUCT.md`
- Create: `.github/ISSUE_TEMPLATE/bug_report.md`
- Create: `.github/ISSUE_TEMPLATE/feature_request.md`
- Create: `.github/ISSUE_TEMPLATE/config.yml`
- Create: `.github/pull_request_template.md`
- Modify: `.gitignore`
- Modify: `pyproject.toml`
- Modify: `src/agent_quality_mcp/models.py`
- Modify: `src/agent_quality_mcp/tools.py`
- Modify: `src/agent_quality_mcp/server.py`
- Create: `tests/unit/test_cli_entrypoint.py`
- Create: `tests/unit/test_schema_descriptions.py`
- Modify: `tests/unit/test_package_metadata.py`
- Create: `.github/workflows/release.yml`
- Create: `server.json`
- Modify: `README.md`

## Task 1: Licensing And Community Files

**Files:**
- Create: `LICENSE`
- Create: `SECURITY.md`
- Create: `CONTRIBUTING.md`
- Create: `CHANGELOG.md`
- Create: `CODE_OF_CONDUCT.md`
- Create: `.github/ISSUE_TEMPLATE/bug_report.md`
- Create: `.github/ISSUE_TEMPLATE/feature_request.md`
- Create: `.github/ISSUE_TEMPLATE/config.yml`
- Create: `.github/pull_request_template.md`

- [ ] **Step 1: Add the LICENSE file**

Create `LICENSE` at the repository root with the standard MIT license text
and copyright line `Copyright (c) 2026 Agent Quality MCP Maintainers`
(matching `pyproject.toml`'s existing `authors` field). Do not change
`pyproject.toml`'s `license` field; it already says MIT.

- [ ] **Step 2: Add SECURITY.md**

Create `SECURITY.md` covering: supported versions (currently `0.x`, latest
release only), how to report a vulnerability privately (point at GitHub's
"Report a vulnerability" private advisory flow for this repo — note in the
PR description that the maintainer should enable "Private vulnerability
reporting" in repo settings, since that is a repo-settings action this plan
cannot perform from a branch), expected response handling, and a short
recap of the existing safety guarantees from `README.md`'s Security Model
section (shadow-only execution, no real-workspace mutation, command
allowlisting, redaction) so researchers know what is already mitigated
before reporting.

- [ ] **Step 3: Add CONTRIBUTING.md**

Create `CONTRIBUTING.md` covering: how to set up the dev environment
(`uv sync --extra dev`), how to run the verification suite (reuse the exact
commands from `README.md`'s "Tests And Checks" section), commit/PR
conventions (small focused changes, matching this repo's existing history),
the SemVer policy from the spec's "Versioning And Release Pipeline" section
(including the explicit `0.y.z` may-break-until-`1.0.0` note), and the
one-time PyPI Trusted Publisher setup step a maintainer must do before the
first tag push (documented as a maintainer-only setup note, not something
a contributor needs to do).

- [ ] **Step 4: Add CHANGELOG.md**

Create `CHANGELOG.md` using the Keep a Changelog format. Add an
`## [Unreleased]` section listing this phase's changes (license, packaging
fix, CLI fix, schema descriptions, release pipeline, MCP registry listing,
README overhaul) and an explicit policy note: "Versions below `1.0.0` may
contain breaking changes to the `validate_patch`/`inspect_workspace`
response contract without a major-version bump; `1.0.0` is the point at
which that contract is declared stable."

- [ ] **Step 5: Add CODE_OF_CONDUCT.md**

Create `CODE_OF_CONDUCT.md` using the Contributor Covenant v2.1 text
verbatim, with the contact method pointed at the same channel documented in
`SECURITY.md`.

- [ ] **Step 6: Add GitHub issue and PR templates**

Create `.github/ISSUE_TEMPLATE/bug_report.md` (sections: environment,
`validate_patch`/`inspect_workspace` request used if applicable, expected
vs. actual `decision`/diagnostics, redaction reminder telling reporters not
to paste secrets), `.github/ISSUE_TEMPLATE/feature_request.md` (problem,
proposed behavior, alternatives considered), `.github/ISSUE_TEMPLATE/config.yml`
(`blank_issues_enabled: false`, a contact link pointing at `SECURITY.md` for
vulnerabilities instead of a public issue), and `.github/pull_request_template.md`
(summary, findings addressed, changes, test plan — matching the shape this
repo's own prior PR descriptions already use).

- [ ] **Step 7: Verify and commit**

Run:

```bash
git status --short
git diff --check
```

Expected: only the new files above are added; no whitespace errors.

```bash
git add LICENSE SECURITY.md CONTRIBUTING.md CHANGELOG.md CODE_OF_CONDUCT.md .github/ISSUE_TEMPLATE .github/pull_request_template.md
git commit -m "docs: add license, security policy, and community files"
```

## Task 2: Fix The `.gitignore` Foot-Gun

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Narrow the blanket `docs/` ignore rule**

In `.gitignore`, replace the bare `docs/` line with rules that keep
ignoring whatever the original rule was meant for while never silently
dropping tracked design docs again:

```gitignore
docs/*
!docs/superpowers/
```

Confirm intent with `git log -p -- .gitignore` before editing if the
original author's purpose for ignoring all of `docs/` is unclear; if `docs/`
was meant to exclude some other untracked local notes directory, keep that
specific exclusion alongside the `!docs/superpowers/` negation rather than
removing the line outright.

- [ ] **Step 2: Verify previously-force-added docs are now tracked normally**

Run:

```bash
git check-ignore -v docs/superpowers/specs/2026-06-30-agent-quality-mcp-public-release-readiness-design.md
```

Expected: no output (not ignored). Run `git status --short` after touching
an unrelated file under `docs/superpowers/` to confirm a plain `git add .`
would now pick it up without `-f`.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "fix: stop gitignore from silently dropping design docs"
```

## Task 3: Packaging Fix — Runtime Dependencies And Metadata

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/unit/test_package_metadata.py`

- [ ] **Step 1: Write a failing regression test for the dependency gap**

Append to `tests/unit/test_package_metadata.py`:

```python
import tomllib
from pathlib import Path


def _pyproject_data() -> dict:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject_path.read_text(encoding="utf-8"))


def test_runtime_dependencies_include_ruff_and_pyright() -> None:
    dependencies = _pyproject_data()["project"]["dependencies"]
    names = {dependency.split(">=")[0].split("==")[0].strip() for dependency in dependencies}
    assert {"ruff", "pyright"} <= names


def test_project_metadata_has_urls_and_classifiers() -> None:
    project = _pyproject_data()["project"]
    assert project.get("urls", {}).get("Repository")
    assert any(classifier.startswith("License ::") for classifier in project.get("classifiers", []))
```

- [ ] **Step 2: Run the new tests and verify they fail**

```bash
.venv/bin/python -m pytest tests/unit/test_package_metadata.py -v
```

Expected: FAIL — `ruff`/`pyright` are only under `dev`, and `urls`/
`classifiers` do not exist yet.

- [ ] **Step 3: Move `ruff` and `pyright` into core dependencies**

In `pyproject.toml`, change:

```toml
dependencies = [
  "mcp>=1.9.0",
  "pydantic>=2.8.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2.0",
  "pytest-cov>=5.0.0",
  "ruff>=0.5.0",
  "pyright>=1.1.0",
]
```

to:

```toml
dependencies = [
  "mcp>=1.9.0",
  "pydantic>=2.8.0",
  "ruff>=0.5.0",
  "pyright>=1.1.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2.0",
  "pytest-cov>=5.0.0",
]
```

- [ ] **Step 4: Add project metadata for discoverability**

In `pyproject.toml`, add under `[project]` (alongside the existing
`authors`/`license`/`readme` fields):

```toml
keywords = ["mcp", "mcp-server", "ruff", "pyright", "uv", "code-quality", "agent-tools"]
classifiers = [
  "Development Status :: 3 - Alpha",
  "Intended Audience :: Developers",
  "License :: OSI Approved :: MIT License",
  "Programming Language :: Python :: 3",
  "Programming Language :: Python :: 3.12",
  "Topic :: Software Development :: Quality Assurance",
  "Topic :: Software Development :: Testing",
]

[project.urls]
Homepage = "https://github.com/qkal/techne"
Repository = "https://github.com/qkal/techne"
Issues = "https://github.com/qkal/techne/issues"
Changelog = "https://github.com/qkal/techne/blob/master/CHANGELOG.md"
```

- [ ] **Step 5: Resync and verify**

```bash
uv sync --extra dev
.venv/bin/python -m pytest tests/unit/test_package_metadata.py -v
```

Expected: PASS. Also confirm the bundled tools still resolve:

```bash
.venv/bin/ruff --version
.venv/bin/pyright --version
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/unit/test_package_metadata.py
git commit -m "fix: bundle ruff and pyright as runtime dependencies"
```

## Task 4: MCP Schema Self-Description

**Files:**
- Modify: `src/agent_quality_mcp/models.py`
- Modify: `src/agent_quality_mcp/tools.py`
- Create: `tests/unit/test_schema_descriptions.py`

- [ ] **Step 1: Write failing schema-completeness tests**

Create `tests/unit/test_schema_descriptions.py`:

```python
from __future__ import annotations

from agent_quality_mcp.models import InspectWorkspaceRequest, ValidatePatchRequest


def _assert_all_properties_described(schema: dict) -> None:
    properties = schema.get("properties", {})
    assert properties, "schema must declare properties"
    for name, definition in properties.items():
        assert definition.get("description"), f"{name} is missing a description"


def test_validate_patch_request_schema_is_self_describing() -> None:
    _assert_all_properties_described(ValidatePatchRequest.model_json_schema())


def test_inspect_workspace_request_schema_is_self_describing() -> None:
    _assert_all_properties_described(InspectWorkspaceRequest.model_json_schema())
```

- [ ] **Step 2: Run and verify the tests fail**

```bash
.venv/bin/python -m pytest tests/unit/test_schema_descriptions.py -v
```

Expected: FAIL — no field currently has a `description`.

- [ ] **Step 3: Add field descriptions to `ValidatePatchRequest` and `InspectWorkspaceRequest`**

In `src/agent_quality_mcp/models.py`, change the field declarations (keep
existing types/defaults/validators unchanged, add `Field(description=...)`):

```python
class ValidatePatchRequest(AgentQualityBaseModel):
    """Input accepted by the validate_patch MCP tool."""

    workspace_root: str = Field(
        description=(
            "Absolute path to the real workspace directory to validate against. "
            "This directory is never modified; validation runs in an isolated "
            "shadow copy."
        )
    )
    changed_files: list[str] = Field(
        description=(
            "Non-empty list of relative file paths the patch is allowed to "
            "touch. Required even if patch_unified_diff is omitted."
        )
    )
    patch_unified_diff: str | None = Field(
        default=None,
        description=(
            "Optional text unified diff to apply inside the shadow workspace "
            "before running checks. When present, patch targets must exactly "
            "match changed_files. Binary patches, renames, copies, and file "
            "mode changes are rejected."
        ),
    )
    mode: ValidationMode | None = Field(
        default=None,
        description=(
            "Validation depth: 'quick' (changed files only, fastest, reduced "
            "confidence), 'standard' (default; full shadow-workspace checks), "
            "or 'strict' (broadest checks, strictest routing to "
            "request_human_review when incomplete). Defaults to server "
            "configuration when omitted."
        ),
    )
    safety_mode: SafetyMode | None = Field(
        default=None,
        description=(
            "Permission mode: 'read_only' (default; no fixes), "
            "'preview_safe_fixes' (returns redacted fix previews without "
            "mutating anything), or 'apply_safe_fixes' (always rejected — "
            "real-workspace mutation is not supported). Defaults to server "
            "configuration when omitted."
        ),
    )
    request_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Caller-provided request identifier echoed back in the response; generated when omitted.",
    )
    config_overrides: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional safe configuration overrides (e.g. default_mode, "
            "workspace_exclusions). Fields that would expand authority or "
            "resource limits are rejected; see README Configuration section."
        ),
    )
```

```python
class InspectWorkspaceRequest(AgentQualityBaseModel):
    """Input accepted by the inspect_workspace MCP tool."""

    workspace_root: str = Field(
        description="Absolute path to an existing workspace directory to inspect."
    )
    config_overrides: dict[str, Any] | None = Field(
        default=None,
        description="Optional safe configuration overrides; same rules as validate_patch.",
    )
```

Keep the existing `require_changed_files` validator on `ValidatePatchRequest`
unchanged.

- [ ] **Step 4: Expand tool docstrings in `tools.py`**

In `src/agent_quality_mcp/tools.py`, expand the two tool docstrings so an
agent reading the tool list alone understands the contract:

```python
def validate_patch_tool(
    workspace_root: str,
    changed_files: list[str],
    patch_unified_diff: str | None = None,
    mode: str | None = None,
    safety_mode: str | None = None,
    request_id: str | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a proposed patch in an isolated shadow workspace and return
    an agent decision contract.

    Never mutates workspace_root. Runs uv/Ruff/Pyright in a temporary copy,
    then returns a response whose top-level `decision` field
    (apply_patch / revise_patch / fix_tooling / request_human_review /
    reject_request) tells the caller what to do next; `blockers`,
    `next_actions`, `fix_plan`, and `evidence` explain why.
    """
```

```python
def inspect_workspace_tool(
    workspace_root: str,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return safe workspace metadata (tool availability, file counts,
    discovered config files) without reading or returning source contents.

    Does not run uv/Ruff/Pyright and does not validate a patch; use
    validate_patch for that.
    """
```

- [ ] **Step 5: Run the schema tests and the full tool/server test files**

```bash
.venv/bin/python -m pytest tests/unit/test_schema_descriptions.py tests/unit/test_tools_server.py tests/unit/test_models.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent_quality_mcp/models.py src/agent_quality_mcp/tools.py tests/unit/test_schema_descriptions.py
git commit -m "feat: add MCP schema descriptions for agent self-discovery"
```

## Task 5: CLI Entrypoint Fix

**Files:**
- Modify: `src/agent_quality_mcp/server.py`
- Create: `tests/unit/test_cli_entrypoint.py`

- [ ] **Step 1: Write failing CLI argument-parsing tests**

Create `tests/unit/test_cli_entrypoint.py`:

```python
from __future__ import annotations

import pytest

from agent_quality_mcp import __version__
from agent_quality_mcp.server import parse_args


def test_parse_args_version_short_circuits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--version"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert __version__ in captured.out


def test_parse_args_help_short_circuits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--help"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "agent-quality-mcp" in captured.out.lower()


def test_parse_args_unknown_flag_fails_fast(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--not-a-real-flag"])
    captured = capsys.readouterr()
    assert exc_info.value.code != 0
    assert captured.err


def test_parse_args_no_arguments_returns_namespace() -> None:
    args = parse_args([])
    assert args is not None
```

- [ ] **Step 2: Run and verify the tests fail**

```bash
.venv/bin/python -m pytest tests/unit/test_cli_entrypoint.py -v
```

Expected: FAIL — `parse_args` does not exist yet.

- [ ] **Step 3: Add argument parsing to `server.py`**

In `src/agent_quality_mcp/server.py`, add:

```python
"""FastMCP server entrypoint for Agent Quality MCP."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from mcp.server.fastmcp import FastMCP

from agent_quality_mcp import __version__
from agent_quality_mcp.service import close_pyright_lsp_manager
from agent_quality_mcp.tools import register_tools


def create_app() -> FastMCP:
    """Create the FastMCP app."""

    app = FastMCP("agent-quality-mcp")
    register_tools(app)
    return app


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse CLI arguments; exits via SystemExit for --version/--help/errors."""

    parser = argparse.ArgumentParser(
        prog="agent-quality-mcp",
        description=(
            "Agent Quality MCP server. Speaks MCP over stdio with no "
            "arguments. See https://github.com/qkal/techne for the "
            "validate_patch / inspect_workspace tool contract."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"agent-quality-mcp {__version__}",
    )
    return parser.parse_args(argv)


def main() -> None:
    """Run the MCP server over stdio."""

    parse_args(sys.argv[1:])
    try:
        create_app().run()
    finally:
        close_pyright_lsp_manager()
```

`argparse.ArgumentParser` already provides `-h`/`--help` and fails fast with
a usage message and exit code `2` on unrecognized arguments, so no extra
code is needed for those two cases beyond defining the parser and the
explicit `--version`/`-V` action.

- [ ] **Step 4: Run the CLI tests**

```bash
.venv/bin/python -m pytest tests/unit/test_cli_entrypoint.py -v
```

Expected: PASS.

- [ ] **Step 5: Manually verify the original bug is fixed**

```bash
timeout 3 .venv/bin/agent-quality-mcp --version; echo "exit:$?"
timeout 3 .venv/bin/agent-quality-mcp --help; echo "exit:$?"
```

Expected: both print output immediately and exit `0` (not `124`).

- [ ] **Step 6: Run the full server/tools test files**

```bash
.venv/bin/python -m pytest tests/unit/test_tools_server.py -v
```

Expected: PASS (existing `main`/`create_app` behavior for the no-argument
case is unchanged).

- [ ] **Step 7: Commit**

```bash
git add src/agent_quality_mcp/server.py tests/unit/test_cli_entrypoint.py
git commit -m "fix: stop --version/--help from hanging the stdio server"
```

## Task 6: Versioning Policy And Release Pipeline

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Add the release workflow**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags:
      - "v*"
  workflow_dispatch: {}

permissions:
  contents: read

jobs:
  verify:
    name: Verify (reuse CI checks)
    uses: ./.github/workflows/ci.yml

  build:
    name: Build distribution
    needs: verify
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - uses: astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39
        with:
          enable-cache: true
      - name: Build sdist and wheel
        run: uv build
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish-pypi:
    name: Publish to PyPI
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/p/agent-quality-mcp
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v5
        with:
          name: dist
          path: dist/
      - name: Publish distribution to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1

  publish-mcp-registry:
    name: Publish to MCP Registry
    needs: publish-pypi
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v6
      - name: Install mcp-publisher
        run: |
          curl -L "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_$(uname -s | tr '[:upper:]' '[:lower:]')_$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz" | tar xz mcp-publisher
      - name: Sync server.json version with the release tag
        run: |
          VERSION=${GITHUB_REF#refs/tags/v}
          jq --arg v "$VERSION" '.version = $v | .packages[0].version = $v' server.json > server.json.tmp
          mv server.json.tmp server.json
      - name: Authenticate to MCP Registry
        run: ./mcp-publisher login github-oidc
      - name: Publish server to MCP Registry
        run: ./mcp-publisher publish
```

Note: `ci.yml` must be a reusable workflow (`on: workflow_call` added
alongside its existing triggers) for the `verify` job's `uses:` reference to
work; add `workflow_call: {}` to `ci.yml`'s `on:` block as part of this
step rather than duplicating its steps here.

- [ ] **Step 2: Make `ci.yml` reusable**

In `.github/workflows/ci.yml`, change:

```yaml
on:
  push:
  pull_request:
  workflow_dispatch:
```

to:

```yaml
on:
  push:
  pull_request:
  workflow_dispatch:
  workflow_call:
```

- [ ] **Step 3: Validate workflow syntax**

```bash
.venv/bin/python -c "import yaml, sys; yaml.safe_load(open('.github/workflows/release.yml'))" 2>&1 || python3 -c "import yaml, sys; yaml.safe_load(open('.github/workflows/release.yml'))"
```

If `PyYAML` is unavailable, at minimum visually confirm indentation and run
`gh workflow view release.yml` after pushing, or use an online/CI-side
Actions linter. Do not skip this check — a malformed workflow file would
otherwise only be discovered on the first real tag push.

- [ ] **Step 4: Document the one-time PyPI Trusted Publisher setup**

Confirm `CONTRIBUTING.md` (from Task 1) already documents: before the first
tag push, the maintainer must add a pending Trusted Publisher on
https://pypi.org for project name `agent-quality-mcp`, repository
`qkal/techne`, workflow `release.yml`, environment `pypi`. This is a
manual, one-time, PyPI-side action this plan cannot perform from a branch.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml .github/workflows/ci.yml
git commit -m "feat: add tag-driven PyPI release pipeline"
```

## Task 7: MCP Registry Listing

**Files:**
- Create: `server.json`
- Modify: `README.md` (the `mcp-name` marker only; the rest of the README
  is Task 8)

- [ ] **Step 1: Re-validate the server.json schema before writing it**

Fetch the current schema/examples from
`https://github.com/modelcontextprotocol/registry` (`docs/reference/server-json/`
and `docs/guides/publishing/`) and confirm field names match what is used
below. The registry was in active "preview" at spec time; do not skip this
re-check.

- [ ] **Step 2: Add `server.json`**

Create `server.json` at the repository root:

```json
{
  "name": "io.github.qkal/techne",
  "description": "MCP server for secure, shadow-workspace Python patch quality validation (uv, Ruff, Pyright).",
  "version": "0.1.0",
  "repository": {
    "url": "https://github.com/qkal/techne",
    "source": "github"
  },
  "packages": [
    {
      "registryType": "pypi",
      "identifier": "agent-quality-mcp",
      "version": "0.1.0",
      "transport": {
        "type": "stdio"
      }
    }
  ]
}
```

Adjust field names/structure to match whatever the live schema from Step 1
actually requires; this is a draft, not a copy-exactly instruction.

- [ ] **Step 3: Add the `mcp-name` ownership marker to `README.md`**

Add this as the first line of `README.md`, above the `# Agent Quality MCP`
heading:

```markdown
<!-- mcp-name: io.github.qkal/techne -->
```

- [ ] **Step 4: Commit**

```bash
git add server.json README.md
git commit -m "feat: add MCP Registry server.json and ownership marker"
```

## Task 8: README Overhaul

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add status badges below the title**

Below the `# Agent Quality MCP` heading (and below the `mcp-name` comment
from Task 7), add:

```markdown
[![CI](https://github.com/qkal/techne/actions/workflows/ci.yml/badge.svg)](https://github.com/qkal/techne/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agent-quality-mcp.svg)](https://pypi.org/project/agent-quality-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)
```

- [ ] **Step 2: Add a "Use With An MCP Client" section**

Add a new section after "Start The MCP Server" with copy-paste config for
at least Claude Desktop and Cursor:

```markdown
## Use With An MCP Client

Add this to your client's MCP server configuration (Claude Desktop's
`claude_desktop_config.json`, Cursor's `.cursor/mcp.json`, or equivalent):

\`\`\`json
{
  "mcpServers": {
    "agent-quality": {
      "command": "uvx",
      "args": ["agent-quality-mcp"]
    }
  }
}
\`\`\`

`uvx` resolves and runs the published PyPI package without a separate
install step. A working `uv` installation is the only prerequisite; `ruff`
and `pyright` are bundled as runtime dependencies.
```

- [ ] **Step 3: Update the Setup section to drop the implied "dev extras
  required" framing**

Update the existing "Setup" section so the primary documented install path
is a plain install (matching the new bundled dependencies), with
`--extra dev` called out specifically as the contributor/test path:

```markdown
## Setup

Install with uv:

\`\`\`bash
uv tool install agent-quality-mcp
\`\`\`

This installs a working server with `ruff` and `pyright` already bundled.
A working `uv` installation on `PATH` is the only external prerequisite.

For local development (running the test suite, linting, type checking),
install the `dev` extra instead from a repository checkout:

\`\`\`bash
uv sync --extra dev
\`\`\`
```

- [ ] **Step 4: Update the MVP Limitations section**

In the "MVP Limitations" section, remove or qualify "Stdio transport only"
only if Phase 3d has not landed yet — for this plan, leave it as-is (still
accurate; HTTP transport is explicitly deferred to Phase 3d), but add one
line noting that `ruff`/`pyright` are now bundled dependencies rather than
external prerequisites, replacing any text that implied otherwise.

- [ ] **Step 5: Proofread and check whitespace**

```bash
git diff --check -- README.md
```

Expected: exits 0.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: add badges, MCP client quickstart, and updated setup instructions"
```

## Task 9: Full Verification

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run the full test suite with coverage**

```bash
uv sync --extra dev
.venv/bin/python -m pytest --cov=agent_quality_mcp --cov-report=term-missing -v
```

Expected: all tests pass; coverage stays at or above the existing 78% gate
(no production logic changed, so it should stay close to 89.5%).

- [ ] **Step 2: Run Ruff and Pyright**

```bash
.venv/bin/ruff check .
.venv/bin/pyright --pythonpath .venv/bin/python
```

Expected: zero issues.

- [ ] **Step 3: Run the whitespace check**

```bash
git diff --check
```

Expected: exits 0.

- [ ] **Step 4: Confirm tracked status of every new file**

```bash
git status --short
git ls-files LICENSE SECURITY.md CONTRIBUTING.md CHANGELOG.md CODE_OF_CONDUCT.md server.json
```

Expected: working tree clean; all listed files appear in `git ls-files`
output (proving Task 2's `.gitignore` fix did not accidentally exclude
anything and nothing was force-added).

- [ ] **Step 5: Re-run the originally-reported CLI bug check**

```bash
timeout 3 .venv/bin/agent-quality-mcp --version; echo "exit:$?"
timeout 3 .venv/bin/agent-quality-mcp --help; echo "exit:$?"
timeout 3 .venv/bin/agent-quality-mcp --not-a-real-flag; echo "exit:$?"
```

Expected: first two exit `0` with output; third exits non-zero with a
stderr message; none take the full 3 seconds or print `124`.

- [ ] **Step 6: Commit any final verification-only fixes**

If Steps 1-3 required fixes, stage only the tracked files changed by those
fixes and commit with `fix: address verification feedback for public
release readiness`. If everything passed without changes, leave the branch
as-is.

## Self-Review Checklist

- The plan covers licensing/policy/community files, the `.gitignore`
  foot-gun, the `ruff`/`pyright` runtime-dependency fix, MCP schema
  descriptions, the CLI entrypoint hang, the PyPI + MCP Registry release
  pipeline, and the README overhaul — matching Phase 3a's full in-scope
  list from the spec.
- The plan does not touch `decision.py`, `grouping.py`, `actions.py`,
  `risk.py`, `response.py`, `patching.py`, or any LSP/session file.
- The plan does not add a `Dockerfile`, rename the PyPI distribution, or
  change the MCP transport, matching the spec's Resolved Decisions.
- `server.json`'s `name` (`io.github.qkal/techne`) and
  `packages[0].identifier` (`agent-quality-mcp`) are deliberately different
  fields, matching the Resolved Decisions; the plan calls this out at the
  point of use so it is not "fixed" into matching by a future editor.
- Every code-touching task (packaging metadata, schema descriptions, CLI
  parsing) follows write-failing-test-first; pure documentation/policy
  tasks do not, consistent with how this repo's existing plans treat docs.
- The release workflow depends on a manual, one-time PyPI-side Trusted
  Publisher configuration step that this plan cannot perform itself; this
  is called out explicitly in Task 6 and documented for the maintainer in
  `CONTRIBUTING.md` rather than silently assumed.
