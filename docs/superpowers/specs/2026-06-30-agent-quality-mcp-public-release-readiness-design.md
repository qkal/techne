# Agent Quality MCP Public Release Readiness, Maintainability, And Roadmap Design

## Purpose

This design answers one combined brainstorming request: review the current
Agent Quality MCP codebase and identify concrete, evidence-based ways to (1)
improve maintainability, (2) improve quality of life for the people and
agents who use the server, (3) make the server more powerful and optimized,
and (4) make sure the project is actually ready to be used by external people
as a public release.

The repository is already public on GitHub (`qkal/techne`, topics `mcp`,
`mcp-server`, `python`, `ruff`, `pyright`, `uv`) and has gone through two
implemented phases (Phase 1 shadow-workspace validation, Phase 2 agent
decision contract) plus a Pyright LSP capability upgrade. Eight prior pull
requests already removed dead code, deduplicated workspace-exclusion logic,
hardened error handling, and improved MCP tool error feedback. This design
deliberately looks past that already-completed work for the next layer of
opportunities.

This phase is a brainstorming/audit deliverable, not an implementation. No
source code changes are made as part of this document. The HARD-GATE from the
brainstorming workflow applies: nothing here is implemented until this design
is reviewed and a specific phase is approved.

## Audit Method

The audit combined static reading of every file under `src/agent_quality_mcp`
and `tests`, the existing specs/plans under `docs/superpowers`, the merged PR
history, and live verification in this workspace:

- `uv sync --extra dev` succeeded; the full suite passes: 343 passed, 1
  skipped, **89.50% coverage** (gate is 78%).
- `ruff check .` reports zero issues.
- `pyright --pythonpath .venv/bin/python` reports zero errors/warnings.
- `gh issue list` / `gh pr list` show zero open issues or PRs; all 8 prior PRs
  are merged. There is no existing backlog to reconcile against.
- `gh repo view` confirms the repo is `PUBLIC` but `licenseInfo` is `null`,
  `isSecurityPolicyEnabled` is `false`, and `latestRelease` is `null`.
- Targeted runtime checks (documented inline below) verified specific claims
  instead of assuming them, including a confirmed CLI hang and confirmed dead
  code.

Conclusion: the code that exists is in good shape (tests, lint, and types are
all clean). The gaps are concentrated in (a) a few oversized modules, (b) one
patch of orphaned code, (c) external-facing self-description, and (d) almost
all of the scaffolding a public open-source release needs outside of `src/`.

## Findings By Theme

### 1. Maintainability

- `src/agent_quality_mcp/lsp/pyright.py` is **1,064 lines** (515 statements) —
  more than 4x the next-largest source file. It mixes five separable
  concerns in one module: LSP diagnostic normalization (`normalize_lsp_diagnostics`
  and helpers), raw non-blocking stdin/stdout protocol I/O (`_write_stdin_message`,
  `_read_stdout_chunk`, `_stdin_ready`, `_stdout_ready`), the stateful process
  session (`PyrightLspProcessSession`), the provider/fallback orchestration
  (`PyrightLspProvider`), and process lifecycle plus the reusable manager
  (`_close_process`, `RealPyrightLspManager`). It also has the lowest
  meaningful coverage in the repository (79%, vs. 89.5% average) because the
  raw I/O helpers are hard to exercise without a real subprocess.
- `src/agent_quality_mcp/validators.py` defines `wrap_uv_result` and
  `wrap_ruff_result`. Verified with `grep` across `src/`: neither function is
  called anywhere except their own tests in `tests/unit/test_validators.py`.
  `service.py` still calls `UvAdapter`/`RuffAdapter` directly. This looks like
  a migration that the Pyright-LSP phase started (its own plan only finished
  Pyright) and nobody finished or removed. It is currently dead production
  code with tests that only assert the dead code is internally consistent.
- `service.py` (797 lines) and `patching.py` (781 lines) are large but
  internally well-organized into clear linear phases (parse → validate →
  write → commit → rollback for patching; resolve → shadow → run → respond
  for the service). They are lower-priority maintainability targets than the
  LSP module: splitting them is reasonable but not urgent.
- `cli/ruff.py` contains a self-contained "is this output a safe, scoped
  unified diff" validator (`_looks_like_safe_unified_diff`,
  `_consume_valid_hunk`, and related helpers, roughly 150 lines) that
  duplicates hunk-parsing concepts already implemented in `patching.py`.
  Extracting a shared helper would shrink both call sites' conceptual
  surface area without changing behavior.
- `.gitignore` contains a blanket `docs/` rule, yet `docs/superpowers/specs/`
  and `docs/superpowers/plans/` are tracked in git (verified with
  `git ls-files docs/` and `git check-ignore`, which found no ignore match
  for an existing tracked file but would silently swallow a new one added
  with `git add .`). Every prior spec/plan was force-added. This is a
  reproducible foot-gun: a future contributor (human or agent) who runs a
  normal `git add .` will silently fail to commit new design docs. This
  design document itself had to be added with `git add -f` to verify the
  claim.

### 2. Quality Of Life (for the humans and agents using the server)

- Verified by calling `register_tools` against a fake app and inspecting the
  resulting function signatures: `validate_patch` and `inspect_workspace` are
  registered with one-line docstrings ("Validate a proposed patch and return
  JSON-safe response data.") and **zero** per-argument descriptions anywhere
  in the Pydantic request models (`ValidatePatchRequest`, `InspectWorkspaceRequest`).
  FastMCP surfaces docstrings and Pydantic `Field(description=...)` text as
  the JSON schema an MCP client (and the calling agent) sees. Right now an
  agent calling this server cold, without having read the README, cannot
  learn from the tool schema alone what `mode`, `safety_mode`, or
  `config_overrides` do, or which values are valid. For a server whose
  entire audience is autonomous agents, this is a real first-use usability
  gap, not a cosmetic one.
- Verified by running `agent-quality-mcp --version` and `agent-quality-mcp --help`
  with `timeout 3`: both **hang for the full timeout and exit 124** (killed
  by `timeout`), because `main()` unconditionally calls
  `create_app().run()` and never inspects `sys.argv`. A human evaluating the
  tool for the first time — the single most common first action for any new
  CLI — gets no output and an unexplained hang instead of a version string
  or usage text.
- Structured logging exists (`agent_quality_mcp.audit` logger) but there is no
  documented way to configure its level or destination, and no log
  configuration is wired in `server.py`/`main()`. Operators have no
  documented lever to see what the server is doing.
- The README is thorough about the security model and response contract but
  has no quickstart snippet for adding this server to an actual MCP client
  (Claude Desktop, Cursor, VS Code, etc.), and no badges (CI status, license,
  coverage, PyPI version) that let a visitor assess project health at a
  glance.

### 3. Power And Optimization

- `service.py`'s `_run_adapters` runs `uv`, then `ruff`, then the Pyright
  provider **sequentially**, each a blocking subprocess/LSP round trip. The
  Pyright-LSP design spec explicitly deferred parallelizing this
  ("Parallel validator execution is out of scope because shared timeout
  accounting, LSP lifecycle cleanup, and deterministic command ordering
  matter more than throughput for the light implementation"). That
  deferral was reasonable for that phase, but it is the single largest
  remaining latency lever in `standard`/`strict` mode, where all three tools
  run.
- The installed `mcp` SDK's `FastMCP.run()` already supports
  `transport="stdio" | "sse" | "streamable-http"` (confirmed by inspecting
  `inspect.signature(FastMCP.run)` in this environment). The server only
  ever calls `.run()` with the default, so the existing MVP limitation
  "Stdio transport only" is mostly a missing CLI switch, not missing SDK
  capability. Adding an opt-in HTTP transport would let the server run as a
  shared/remote service instead of only a per-editor spawned subprocess,
  without touching any validation logic.
- `uv`, Ruff, and Pyright capability metadata already has a home
  (`ValidatorResult.metadata`, `ValidatorCapability`) but today only the
  Pyright path is wired through it. Finishing that migration (see Theme 1)
  would also let `evidence` in the public response report scope, skip
  reasons, and completion per tool consistently, which is a real capability
  upgrade for agents deciding what to do next — not just a refactor.
- No caching/memoization exists for repeated validation of the same
  unchanged shadow content. Out of scope to commit to without real usage
  data, but worth tracking as a backlog idea.

### 4. Public Release Readiness

Confirmed directly against GitHub and the tracked files:

- **No `LICENSE` file exists**, even though `pyproject.toml` declares
  `license = { text = "MIT" }`. `gh repo view` confirms GitHub's own license
  detector sees `licenseInfo: null`. Without a `LICENSE` file, the project is
  "all rights reserved" by default in most jurisdictions and in the eyes of
  most tooling and many companies' open-source-usage policies, regardless of
  the metadata string — this is the single highest-impact, lowest-effort
  blocker for external adoption.
- **No `SECURITY.md`** (`isSecurityPolicyEnabled: false`), which is
  conspicuous for a project whose README leads with a detailed security
  model and that runs arbitrary subprocesses and parses external diffs.
  There is no documented way for an external researcher to report a
  vulnerability privately.
- **No `CONTRIBUTING.md`, `CHANGELOG.md`, `CODE_OF_CONDUCT.md`**, and no
  GitHub issue/PR templates (`.github/` only contains `workflows/ci.yml`).
- **No git tags and no GitHub Releases** (`gh release list` and `git tag -l`
  are both empty; `gh repo view` confirms `latestRelease: null`). There is no
  way for an external user to depend on a stable, citable version, even
  though `pyproject.toml` and `src/agent_quality_mcp/__init__.py` already
  agree on `0.1.0` (and a test already pins that agreement — good existing
  practice to build on).
- **Not published anywhere installable.** There is no PyPI package and no
  `server.json` for the official MCP Registry. Today the only way to run
  this server is to clone the repo and run `uv run agent-quality-mcp`. There
  is no `pip install agent-quality-mcp` / `uvx agent-quality-mcp` from a
  registry, and the server is not discoverable from any MCP client's server
  browser.
- **The tools the server exists to run are not installed by a normal
  install.** `ruff` and `pyright` are declared only under
  `[project.optional-dependencies] dev`, alongside `pytest`. A production
  install (`pip install agent-quality-mcp`, no extras) gets `mcp` and
  `pydantic` only — `uv`, `ruff`, and `pyright` would all resolve as
  unavailable, and every validation would degrade to "tool unavailable"
  warnings for two of the three quality tools the product exists to run.
  Verified by reading `pyproject.toml`'s `dependencies` vs. `optional-dependencies`
  directly; both `ruff` and `pyright`'s PyPI wheels vendor their own binaries
  (confirmed by running `.venv/bin/pyright --version` against the synced
  `.venv` and finding the bundled JS bundle under the `pyright` wheel's
  `dist/` folder, no network fetch needed), so bundling them is cheap.
- No `Dockerfile` exists. A container image with `uv`/`ruff`/`pyright`
  preinstalled would be the most friction-free distribution for people who
  do not want to manage a Python toolchain just to run an MCP server.

## Approaches Considered

**Option A — One large "do everything" change.**
Tackle all four themes in a single branch/PR. Rejected: the user's own
repository history shows a strong, consistent preference for small, focused
PRs (eight prior merges, each scoped to one theme — dedup, error handling,
coverage, etc.). A single sprawling change spanning licensing, packaging,
a structural refactor, and new product behavior would be hard to review,
hard to revert piecemeal, and would mix zero-risk file additions with
behavior-affecting refactors in the same diff.

**Option B — Decomposed roadmap, sequenced by leverage and risk, starting
with whichever phase unblocks the others. (Recommended.)**
Split the work into four independent phases that can each get their own
spec → plan → implementation cycle, matching the existing repo convention.
Order them so that the phase with the highest "is this even legally/practically
usable by anyone outside this repo" leverage and the lowest risk to existing
validation logic goes first:

1. **Phase 3a — Public Release Readiness** (this design fully specs it below).
   Almost entirely additive (new files: license, policy docs, CI workflows,
   schema descriptions) plus one small, well-tested dependency/packaging
   change and one small CLI argument-parsing fix. Does not touch the
   validation/decision pipeline. Lowest risk, highest "actually usable by
   external people" leverage, and directly answers the most emphatic clause
   in the request ("make sure it's really gonna work as a public release").
2. **Phase 3b — Maintainability Refactor** (backlog below). Pure internal
   restructuring (split `lsp/pyright.py`, resolve the dead validator-wrapper
   code, optional dedup of diff-validation helpers). No public behavior
   change, but touches code with concurrency and subprocess lifecycle, so it
   needs its own careful plan and full regression run.
3. **Phase 3c — Quality-of-Life And DX** (backlog below). Schema
   self-description, logging configuration, README/quickstart polish that
   is not already folded into 3a.
4. **Phase 3d — Power And Performance** (backlog below). Parallel validator
   execution, optional HTTP transport, finishing the uv/Ruff capability
   migration's evidence richness. Higher risk (touches timeout accounting
   and orchestration), so it should land after 3a/3b prove out the
   lower-risk changes.

**Option C — Skip release engineering, focus only on code-level QoL/power
improvements now.**
Rejected as backwards: every improvement under "quality of life" or "power"
only matters to "external people" who can legally and practically install
the package in the first place. Without Option B's Phase 3a, none of the
other work is actually reachable by anyone outside this repository.

**Recommendation:** Option B. The rest of this document fully specs **Phase
3a** as the first concrete, approvable, implementable unit, and gives enough
detail on 3b/3c/3d to steer before they are each turned into their own
spec.

## Phase 3a: Public Release Readiness (Fully Specified)

### Scope

Phase 3a makes the project something an external person can legally use,
actually install with working validators by default, discover through
standard channels, and safely report problems against. It is intentionally
free of changes to `decision.py`, `grouping.py`, `actions.py`, `risk.py`,
`response.py`, or the LSP/session code — nothing about how a patch is judged
changes in this phase.

In scope:

- Add a real `LICENSE` file matching the existing MIT metadata.
- Add `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, and GitHub
  issue/PR templates.
- Fix `.gitignore` so `docs/superpowers/` is never silently dropped again.
- Move `ruff` and `pyright` from `dev`-only extras into core runtime
  `dependencies` (keep `uv` as an assumed pre-existing tool, since it is
  also how the server itself is typically launched).
- Add `project.urls`, `classifiers`, and `keywords` to `pyproject.toml`.
- Add per-tool and per-field MCP schema descriptions (`validate_patch`,
  `inspect_workspace`, and their Pydantic request fields).
- Fix the CLI entrypoint so `--version` and `--help` return immediately
  instead of hanging, and unknown flags fail fast with a clear message.
- Add a tagged-release pipeline: semantic versioning policy, a GitHub
  Actions workflow that builds and publishes to PyPI via Trusted Publishing
  (OIDC, no stored token) on tag push, and a follow-up job that publishes/
  updates a `server.json` to the official MCP Registry.
- Add an `mcp-name` ownership marker and MCP-client quickstart snippets
  (Claude Desktop, Cursor, VS Code) to the README, plus status/license/PyPI
  badges.
- Add a minimal `Dockerfile` that bundles `uv`, `ruff`, and `pyright` so
  external users have a zero-toolchain-setup option.

Out of scope for 3a (deferred to later phases or explicitly rejected):

- Any change to `validate_patch`'s decision/response contract.
- Any change to the shadow-workspace security model or the "no real
  repository mutation" guarantee — this stays a hard invariant.
- Adding new validators/tools (mypy, bandit, etc.) — a product decision that
  needs its own brainstorming, not bundled into release engineering.
- HTTP/SSE transport — moved to Phase 3d because it is a capability change,
  not a release-engineering change.

### Licensing And Legal

- Add `LICENSE` at the repository root with the standard MIT license text,
  copyright line `Copyright (c) 2026 Agent Quality MCP Maintainers` (matching
  the existing `authors` field in `pyproject.toml` so the two stay
  consistent).
- No change to `pyproject.toml`'s `license` field; it already says MIT. The
  fix is the missing file, not the metadata.
- Add `SECURITY.md` describing: supported versions, how to report a
  vulnerability privately (a contact email or GitHub private security
  advisory flow — enabling "Private vulnerability reporting" in repo
  settings is a one-click action the maintainer should take alongside this
  PR), and an explicit reminder of the existing safety guarantees (shadow-only
  execution, no real-workspace mutation, command allowlisting) so
  researchers know what is already mitigated.
- Add `CODE_OF_CONDUCT.md` (Contributor Covenant is the de facto standard;
  reuse it verbatim) so the repo's "Code of conduct" health-check badge on
  GitHub goes green.

### Packaging And The Runtime-Dependency Gap

This is the one functional (non-doc) change in Phase 3a, and it is the most
important single fix for "really gonna work" — without it, a fresh
`pip install agent-quality-mcp` cannot do the one thing the product is for.

```toml
[project]
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

Trade-off considered and accepted: this grows the default install (both
`ruff` and `pyright` vendor their own prebuilt binaries inside the wheel, so
this is a download-size cost, not a "needs a C compiler" or "needs Node"
cost — verified above). The alternative (a separate `tools` extras group
that the README tells everyone to install anyway) only adds friction for
zero benefit, because there is no realistic scenario where someone wants
this MCP server with deliberately *fewer* working validators by default.
Anyone who wants to point at enterprise-managed tool binaries instead still
can, unchanged, through the existing `command_paths` config and
`AGENT_QUALITY_MCP_UV` / `AGENT_QUALITY_MCP_RUFF` / `AGENT_QUALITY_MCP_PYRIGHT`
/ `AGENT_QUALITY_MCP_PYRIGHT_LANGSERVER` trusted environment variables — this
change does not reduce that flexibility at all.

`uv` is deliberately left alone: it is both a tool this server shells out to
and very likely the tool the user is already using to *launch* the server
(`uv run agent-quality-mcp` / `uvx agent-quality-mcp`), so requiring it as a
PyPI dependency would be circular for the most common install path. The
README should simply state that a working `uv` on `PATH` is a prerequisite,
the same way it already implicitly assumes Python 3.12.

### MCP Schema Self-Description

Add docstrings and `Field(description=...)` text so the tool schema is
self-sufficient for an agent that has never read the README:

- `validate_patch_tool`: expand the docstring to state, in agent-readable
  terms, what the tool does, that it never mutates the real workspace, and
  that the response's `decision` field is the field to act on.
- Every `ValidatePatchRequest` field gets a `Field(description=...)`:
  `workspace_root`, `changed_files`, `patch_unified_diff`, `mode` (with the
  three values and what each proves), `safety_mode` (with the three values
  and that `apply_safe_fixes` is always rejected), `request_id`,
  `config_overrides`.
- Same treatment for `InspectWorkspaceRequest` and `inspect_workspace_tool`.
- Add a unit test that asserts the generated JSON schema (via
  `ValidatePatchRequest.model_json_schema()`) has a non-empty `description`
  for every property, so this cannot silently regress.

### CLI Entrypoint Fix

`main()` currently ignores `sys.argv` entirely; `agent-quality-mcp --version`
and `agent-quality-mcp --help` were verified in this environment to hang
until killed (`timeout 3 ... ; echo $?` → `124`) because the process moves
straight into the blocking stdio read loop. Fix in `server.py`:

- Parse `sys.argv[1:]` with the standard library `argparse` before touching
  `FastMCP`.
- `-V` / `--version` prints `agent-quality-mcp {__version__}` to stdout and
  exits `0` without starting the server.
- `-h` / `--help` prints short usage text (what the tool is, that it speaks
  MCP over stdio, and a pointer to the README) and exits `0`.
- Any unrecognized argument prints a clear error to stderr and exits
  non-zero (argparse gives this for free).
- No arguments (the existing, only-documented usage) behaves exactly as
  today: starts the stdio MCP server.
- Add a unit test that calls the CLI argument parser directly (not through a
  subprocess, to keep tests fast and deterministic) for each of the three
  cases above.

### Versioning And Release Pipeline

- Adopt explicit SemVer rules in `CONTRIBUTING.md` and `CHANGELOG.md`:
  MAJOR for any breaking change to the `validate_patch`/`inspect_workspace`
  response contract (the same kind of change Phase 2 already made once,
  before any release process existed), MINOR for new optional
  fields/tools/capabilities, PATCH for bugfixes and docs. Per SemVer's own
  rules the project may stay below `1.0.0` (it is `0.1.0` today) for as long
  as the response contract is still expected to change; state explicitly in
  `CHANGELOG.md` that `0.y.z` releases may still contain breaking changes
  and that `1.0.0` is the point at which the response contract is declared
  stable.
- Tag format `vX.Y.Z`. Add `.github/workflows/release.yml`:
  - Triggered on `push: tags: ["v*"]`.
  - `build` job: checkout, set up Python 3.12, `uv build` (or
    `python -m build`), upload the `dist/` artifact.
  - `publish-pypi` job (`needs: build`, environment `pypi`,
    `permissions: id-token: write`): `pypa/gh-action-pypi-publish@release/v1`
    with **no stored token** — relies on a PyPI Trusted Publisher binding
    configured once by the maintainer for this repo + this workflow file
    (a manual, one-time PyPI-side setup step that must happen before the
    first tag push; document it in `CONTRIBUTING.md`).
  - `publish-mcp-registry` job (`needs: publish-pypi`): installs
    `mcp-publisher`, authenticates with `login github-oidc` (no secret
    needed), and publishes `server.json` (see below) after confirming the
    just-published PyPI version matches.
- Reuse the existing `ci.yml` checks (lint, type check, test, whitespace) as
  a required check on the release tag's commit before any of the above runs,
  so a release can never publish a version that failed CI.

### MCP Registry Listing And Client Quickstart

- Add `server.json` at the repo root (schema is still evolving in the
  official registry's "preview" state; the implementation plan must
  re-validate field names against the live schema at
  `https://github.com/modelcontextprotocol/registry` before publishing, not
  copy this draft blindly):

```json
{
  "name": "io.github.qkal/agent-quality-mcp",
  "description": "MCP server for secure, shadow-workspace Python patch quality validation (uv, Ruff, Pyright).",
  "version": "0.1.0",
  "packages": [
    {
      "registryType": "pypi",
      "identifier": "agent-quality-mcp",
      "version": "0.1.0",
      "transport": { "type": "stdio" }
    }
  ]
}
```

- Add an `mcp-name: io.github.qkal/agent-quality-mcp` HTML comment near the
  top of `README.md`, which the registry uses to verify package ownership
  against the published PyPI README.
- Add a "Use with an MCP client" section to `README.md` with copy-paste
  config for at least Claude Desktop and Cursor, e.g.:

```json
{
  "mcpServers": {
    "agent-quality": {
      "command": "uvx",
      "args": ["agent-quality-mcp"]
    }
  }
}
```

- Add status badges to the top of `README.md`: CI workflow status, PyPI
  version, license, and Python version support.

### Containerized Option

- Add a minimal `Dockerfile`:
  - `FROM python:3.12-slim`.
  - Install the published `agent-quality-mcp` (or `COPY . .` + editable
    install for a repo-local build) with its now-bundled `ruff`/`pyright`.
  - `ENTRYPOINT ["agent-quality-mcp"]`.
  - No ports exposed (stdio transport); documented as something an MCP
    client launches with `docker run -i --rm <image>`.
- Add a `.github/workflows/docker-publish.yml` that builds and pushes to
  GHCR on the same `v*` tag trigger as the PyPI release, reusing the same
  built `dist/` wheel where practical.
- This is explicitly optional/secondary to the PyPI path in this phase; if
  scope needs to shrink during implementation, drop the Docker image before
  dropping anything else in this section.

### Testing

- Existing full suite (`pytest`, `ruff check .`, `pyright`, `git diff --check`)
  must stay green; none of this phase's functional changes (dependency
  move, CLI parsing, schema descriptions) should require touching
  `decision.py`/`response.py` tests.
- New unit tests:
  - CLI argument parsing for `--version`, `--help`, and an unknown flag,
    asserting exit codes and that the server is not started.
  - JSON schema description completeness for `ValidatePatchRequest` and
    `InspectWorkspaceRequest`.
  - A `pyproject.toml`-reading test (or simple text assertion) that `ruff`
    and `pyright` are present in `dependencies`, not only `dev`, so this
    cannot silently regress back into the gap found here.
- New CI:
  - `release.yml` should be exercised with `workflow_dispatch` plus a dry
    run (e.g. `pypa/gh-action-pypi-publish` against TestPyPI, or simply
    building and checking the artifact with `twine check`) before the first
    real tag, since trusted publishing cannot be fully tested without a
    real tag in the common case.
  - Add a `pre-commit`-style local check or a CI step that fails if
    `pyproject.toml` version, `__init__.py.__version__`, and (once added)
    `server.json` version ever diverge — mirroring the existing
    `test_package_metadata.py` pattern that already pins `__version__`.

### Acceptance Criteria

- `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`,
  `CODE_OF_CONDUCT.md`, and `.github/ISSUE_TEMPLATE/` + pull request template
  exist and are tracked in git (verified with `git ls-files`, not just
  present on disk).
- `.gitignore` no longer has a blanket rule that can silently drop new files
  under `docs/superpowers/`.
- A clean `pip install agent-quality-mcp` (simulated locally with
  `uv pip install .` into a throwaway venv, no extras) results in `ruff` and
  `pyright` both being importable/runnable.
- `agent-quality-mcp --version` and `agent-quality-mcp --help` exit `0`
  immediately (verified with the same `timeout 3 ...; echo $?` check used to
  find the bug; expected exit code is the program's own, not `124`).
- `ValidatePatchRequest.model_json_schema()` and
  `InspectWorkspaceRequest.model_json_schema()` have a non-empty
  `description` for every top-level property.
- `release.yml` exists, is syntactically valid, requires `ci.yml`'s checks to
  pass first, and uses Trusted Publishing (no PyPI token stored as a
  secret).
- `server.json` exists and the README carries the matching `mcp-name`
  comment.
- README has a working MCP-client quickstart snippet and at least a CI/
  license/PyPI badge row.
- Full existing test/lint/type/whitespace verification still passes
  unchanged.

## Phase 3b: Maintainability Refactor (Backlog Outline)

Not fully specified yet; detailed enough to convert into its own design.

- Convert `src/agent_quality_mcp/lsp/pyright.py` into a `lsp/pyright/`
  package: `diagnostics.py` (URI/range/severity normalization),
  `session.py` (`PyrightLspProcessSession` and the raw non-blocking
  stdin/stdout helpers), `provider.py` (`PyrightLspProvider` + fallback),
  `manager.py` (`RealPyrightLspManager` + process lifecycle), `__init__.py`
  re-exporting the current public symbols so every existing
  `from agent_quality_mcp.lsp.pyright import ...` import keeps working
  unchanged. No behavior change; the win is file size, focused test
  targets, and easier review of future LSP changes.
- Resolve the dead `wrap_uv_result`/`wrap_ruff_result` code. Two real
  options, both legitimate, that need a decision before planning:
  - **Delete** them and their tests (simplest, lowest risk, matches "remove
    what's unused").
  - **Finish wiring** `service.py`'s uv/Ruff calls through them, the way
    Pyright already is. More invasive (touches orchestration and the
    public `evidence`/`required_checks` shape's inputs, though not its
    contract), but removes the dead code *and* makes uv/Ruff capability
    metadata (scope, skip reasons, completion) available to the response
    the same way Pyright's already is — a small power upgrade bundled with
    the cleanup. Recommended if Phase 3d's evidence-richness goals are
    wanted; otherwise delete.
- Optional, lower priority: extract the shared "is this a safe scoped
  unified diff" check out of `cli/ruff.py` so it does not conceptually
  duplicate `patching.py`'s hunk parsing; consider splitting `service.py`'s
  `inspect_workspace_service` path out of the `validate_patch_service` file.

## Phase 3c: Quality-Of-Life And DX (Backlog Outline)

- Document and wire a log-level/destination control
  (e.g. `AGENT_QUALITY_MCP_LOG_LEVEL`) for the existing `audit` logger.
- Decide whether additional validators (mypy, bandit/pip-audit, import
  sorters) are wanted at all — this is a product question for the
  maintainer, not something to default into a plan. If yes, it is a new
  brainstorming round of its own (which tools, why, what "required" means
  per mode) before any implementation.
- Consider a `docs` site (e.g. GitHub Pages from the README) once the
  README quickstart from 3a is in place, if discoverability data suggests
  it is needed.

## Phase 3d: Power And Performance (Backlog Outline)

- Parallelize `uv`/Ruff/Pyright execution in `_run_adapters`. The original
  Pyright-LSP spec's deferral reasons (shared timeout accounting, LSP
  lifecycle cleanup ordering, deterministic command ordering for the
  response) still apply and must be designed for explicitly, not waved
  away — likely a bounded thread pool with per-tool timeout budgets that
  still sum to the existing `request_timeout_seconds`, and deterministic
  re-sorting of `commands`/`diagnostics` after the parallel calls return so
  response ordering stays stable for snapshot-style tests.
- Add an opt-in `streamable-http`/`sse` transport using the SDK capability
  that already exists (`FastMCP.run(transport=...)`), gated behind an
  explicit CLI flag/env var, with its own auth/network-exposure
  considerations spelled out before implementation (this is the one item
  in the whole roadmap that meaningfully changes the security posture, so
  it deserves its own focused design once reached).
- Revisit caching/memoization only if real usage shows repeated-validation
  latency is a problem; not worth designing speculatively now.

## Open Questions For The Maintainer

1. Phase 3a's packaging change (bundle `ruff`+`pyright` as core deps) grows
   the default install. Confirmed acceptable, or is a separate `tools`
   extras group preferred despite the friction trade-off above?
2. Phase 3b's dead-code fork: delete `wrap_uv_result`/`wrap_ruff_result`, or
   finish wiring them into `service.py`? This also decides whether 3b and
   3d's "evidence richness" goal are coupled or independent.
3. Is `io.github.qkal/agent-quality-mcp` the intended MCP Registry/PyPI
   project name, or should it differ from the GitHub repo name (`techne`)?
4. Should the Docker image (lowest-priority item in 3a) be kept in scope for
   the first release, or explicitly deferred to keep the first PyPI release
   smaller and faster to ship?

## Self-Review Notes

- Placeholder scan: no `TODO`/`TBD` left in Phase 3a; every Phase
  3b/3c/3d bullet is explicitly labeled "backlog outline," not presented as
  a finished design, so it cannot be mistaken for implementation-ready
  detail.
- Consistency: Phase 3a's scope explicitly excludes any change to the
  decision/response contract (its only functional changes are the
  dependency move and the CLI argument fix, both called out as such), Phase
  3b's LSP-split bullet explicitly says "no behavior change" only for that
  one item, and Phase 3d explicitly flags the one item (HTTP transport)
  that does touch the security posture. None of these scoped claims are
  contradicted elsewhere in this document.
- Scope: this document is intentionally one fully-specified phase (3a) plus
  three backlog outlines, not four full specs, to stay within "focused
  enough for a single implementation plan" for the part that is meant to be
  implemented next.
- Every finding above states how it was verified (command run, file read,
  or both) rather than asserted from assumption.
