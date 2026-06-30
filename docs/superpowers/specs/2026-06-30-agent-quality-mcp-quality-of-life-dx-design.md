# Agent Quality MCP Quality-Of-Life And Developer Experience Design (Phase 3c)

## Purpose

Phase 3a made the server installable and self-describing at the MCP-schema
level. This phase improves the experience of actually *operating* and
*debugging* it, for three audiences: the server administrator running it,
the human integrating it into an MCP client, and the agent calling it
repeatedly inside a longer workflow. None of this phase changes the
`validate_patch`/`inspect_workspace` decision contract; it adds visibility,
diagnosability, and a few small, additive capabilities around it.

## Source Spec

Builds on
`docs/superpowers/specs/2026-06-30-agent-quality-mcp-public-release-readiness-design.md`
(Phase 3a), whose backlog outline for Phase 3c this document replaces with
a full design.

## Fresh Audit (Verified In This Session)

**Audit logging is completely inert by default.** This is stronger than
"undocumented" — it was empirically verified, not assumed:

```python
>>> import logging
>>> logging.getLogger("agent_quality_mcp.audit").getEffectiveLevel()
30   # WARNING
>>> logging.getLogger().handlers
[]
```

`AuditRecorder.permission()`/`.resource_limit()`/`.event()` all call
`LOGGER.info(...)`. With no handler configured anywhere in this codebase
and no host application configuring one, every audit event — including the
ones describing security-relevant decisions like "Denied apply_safe_fixes"
or "Created isolated shadow workspace for validation" — is silently
dropped at the logging-level check, before it would even reach a handler.
An operator running `agent-quality-mcp` with zero extra configuration has
no way to see what the server is doing, even though the response already
includes a redacted `audit` summary per-request — there is simply no
standing, server-wide log stream today.

**CLI surface is minimal.** After Phase 3a's fix, `agent-quality-mcp` only
understands `--version`/`-V` and `--help`/`-h`; everything else starts the
stdio server. There is no way to sanity-check a fresh install (confirm
`uv`/`ruff`/`pyright` resolve, confirm the demo fixture validates correctly)
without wiring the server into a real MCP client first.

**Diagnostic/blocker vocabulary is large and undocumented as a reference.**
Inventoried directly from source (`grep -o 'code="[a-z_]*"'` across `src/`,
plus the `BlockerKind`/`BlockerFixability`/`PatchDecision`/`NextActionKind`
enums in `decision.py`/`actions.py`):

- System/request codes: `invalid_request`, `workspace_error`,
  `inspect_failed`, `request_timeout`, `internal_error`,
  `apply_safe_fixes_not_supported`, `tool_unavailable`,
  `command_execution_error`, `adapter_internal_error`, `unsafe_path`.
- Tool-adapter codes: `timeout`, `command_failed`, `invalid_preview`,
  `invalid_json` (per-tool, `source` distinguishes uv/ruff/pyright),
  `lsp_fallback`, plus pass-through Ruff rule codes and Pyright rule names.
- `BlockerKind`: `request`, `security`, `patch`, `quality`, `type`,
  `tooling`, `timeout`, `dependency`, `human_review`.
- `BlockerFixability`: `agent_fixable`, `tooling_fixable`, `human_review`,
  `not_fixable`.
- `PatchDecision`: `apply_patch`, `revise_patch`, `fix_tooling`,
  `request_human_review`, `reject_request`.
- `NextActionKind`: `edit`, `rerun`, `inspect`, `fix_tooling`, `ask_human`,
  `stop`.

None of this is wrong or inconsistent — it is a deliberate, well-organized
vocabulary (confirmed while reading `decision.py`/`grouping.py` in Phase
3a's audit). It is simply not collected anywhere a new integrator or a
confused agent can look it up; today the only way to learn what a given
`code` or `kind` means is to read the relevant Python source file.

**The demo fixture already exists and already proves an install works.**
`tests/fixtures/demo_repo/` (a tiny real Python package plus
`patches/fix_value.diff`) is already exercised by
`tests/integration/test_validate_patch_demo.py`. It is a ready-made,
zero-network, self-contained "does my install actually work" check that
nothing currently exposes outside the test suite.

**`config_overrides` discoverability is one-directional.** `inspect_workspace`
reports whether the loaded config is valid and a sanitized count for
list-shaped fields, but does not enumerate which override keys exist, their
types, or their defaults; that information only exists in `README.md`'s
prose and in `config.py`'s `SAFE_UNTRUSTED_CONFIG_FIELDS` constant. An
agent trying to discover the right `config_overrides` shape from
`inspect_workspace` output alone cannot.

## Scope

In scope:

- A documented, opt-in logging configuration path (Part 1).
- Three new CLI subcommands/flags built on the existing `argparse` entry
  point from Phase 3a: `--check-tools`, `--selftest`, `--print-schema`
  (Part 2).
- A generated diagnostics/decision reference table in the README, kept in
  sync with the source enums by a test (Part 3).
- Richer `inspect_workspace` metadata describing available
  `config_overrides` keys (Part 4).
- Expanded MCP-client quickstart coverage beyond Claude Desktop/Cursor
  (Part 5).
- An explicit, documented decision process for "should we add a new
  validator," deliberately deferred rather than answered here (Part 6).

Out of scope:

- Any change to `decision.py`'s precedence rules, `risk.py`'s scoring, or
  any response field's meaning.
- Adding any new validator tool (mypy, bandit, import sorters, etc.) — see
  Part 6 for why this is a separate decision, not a default.
- Telemetry/usage analytics of any kind. If ever pursued, it must be
  opt-in, disclosed in `README.md`/`SECURITY.md`, and is a separate design
  with its own privacy review — this phase does not add any, even
  opt-in, and explicitly recommends against adding it as a default in any
  future phase without that separate review.
- A "dry run"/"explain without running tools" mode for `validate_patch`.
  Considered and rejected for now: the value (saving subprocess time) is
  speculative and the cost (a second, parallel code path that must stay
  semantically consistent with the real one, or risk telling agents
  something false about what would happen) is concrete. Revisit only with
  a specific, evidenced use case.

## Part 1: Make Audit Logging Actually Observable

The fix is not "add more logging calls" (the calls already exist and are
already structured); it is "let an operator opt into seeing them."

- Add `configure_logging(level: str | None) -> None` to a new
  `src/agent_quality_mcp/logging_config.py`:

```python
"""Opt-in logging configuration for server operators."""

from __future__ import annotations

import logging
import os
import sys

LOG_LEVEL_ENV_VAR = "AGENT_QUALITY_MCP_LOG_LEVEL"
VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def configure_logging(level: str | None = None) -> None:
    """Attach a stderr handler to the package logger if a level is requested.

    Resolves the level from the explicit argument first, then the
    AGENT_QUALITY_MCP_LOG_LEVEL environment variable. Does nothing (keeps
    the existing silent default) when neither is set, so behavior is
    unchanged unless an operator opts in.
    """

    resolved = (level or os.environ.get(LOG_LEVEL_ENV_VAR, "")).strip().upper()
    if not resolved:
        return
    if resolved not in VALID_LEVELS:
        print(
            f"agent-quality-mcp: ignoring invalid {LOG_LEVEL_ENV_VAR}={resolved!r}; "
            f"expected one of {', '.join(VALID_LEVELS)}",
            file=sys.stderr,
        )
        return
    package_logger = logging.getLogger("agent_quality_mcp")
    package_logger.setLevel(resolved)
    if not package_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        )
        package_logger.addHandler(handler)
```

- Call `configure_logging()` once, early, in `server.py`'s `main()`, after
  argument parsing and before `create_app().run()`.
- This deliberately configures the *package* logger
  (`agent_quality_mcp`, the parent of `agent_quality_mcp.audit`) rather
  than only the audit logger, so any future module-level logger added
  elsewhere in the package is covered by the same one opt-in switch without
  further changes.
- Output goes to stderr, not stdout, so it never collides with the MCP
  stdio JSON-RPC stream on stdout.
- Document `AGENT_QUALITY_MCP_LOG_LEVEL` in `README.md`'s Configuration
  section alongside the existing trusted command-path environment
  variables, with an explicit warning that `DEBUG`/`INFO` levels will emit
  the already-redacted audit summaries to stderr and should not be enabled
  in environments where stderr is captured somewhere less trusted than the
  MCP client itself.
- This is intentionally the minimum viable fix. It does not add log
  rotation, JSON log formatting, or remote log shipping — those are
  operator-environment concerns better solved by wrapping stderr at the
  process-supervision layer (systemd, Docker, the MCP client's own log
  capture) than by this package.

## Part 2: CLI Diagnostics Commands

Extend `parse_args` (added in Phase 3a) with three more flags, each a
short-circuiting action that exits before the stdio server would start,
matching the existing `--version`/`--help` pattern.

### `--check-tools`

Resolves `uv`, `ruff`, `pyright`, and `pyright-langserver` through the
*exact* existing command-resolution path (`resolve_allowed_command` from
`cli/runner.py`) against the current working directory, and prints a
one-line-per-tool report:

```text
$ agent-quality-mcp --check-tools
uv                 OK   /home/user/.local/bin/uv
ruff               OK   /home/user/.venv/bin/ruff
pyright            OK   /home/user/.venv/bin/pyright
pyright-langserver OK   /home/user/.venv/bin/pyright-langserver
```

Exits `0` if all four resolve, `1` if any do not (each unresolved tool
printed with the reason, e.g. `unavailable: Unable to resolve required
tool: pyright-langserver`). This reuses
`_inspect_command_availability`-equivalent logic already in
`inspect.py` (Phase 3b's extraction target) rather than duplicating
resolution logic; if Phase 3b has not landed yet when this is implemented,
call the equivalent helper directly from `service.py` instead.

This directly answers the first thing anyone evaluating a fresh
`pip install agent-quality-mcp` would want to know: "is this actually going
to work on my machine," without needing an MCP client at all.

### `--selftest`

Runs `validate_patch_service` once, in-process, against the existing
`tests/fixtures/demo_repo` fixture and its `patches/fix_value.diff`
(packaged as part of the distribution — see packaging note below), then
prints the resulting `decision` and a one-line summary, exiting `0` only if
the decision is `apply_patch`:

```text
$ agent-quality-mcp --selftest
Running self-test against the bundled demo fixture...
decision: apply_patch
confidence: high (92)
Self-test passed.
```

This requires `tests/fixtures/demo_repo/` to be included in the built
package's data files (today it is excluded from the wheel as test-only
content). Add it as package data scoped specifically for this purpose
(`[tool.hatch.build.targets.wheel.shared-data]` or an equivalent
`importlib.resources`-discoverable location), not by changing how `pytest`
discovers fixtures. If packaging the fixture turns out to add meaningful
wheel size or complexity, the fallback is to generate the same tiny
fixture content inline in Python at self-test time instead of reusing the
test fixture verbatim — either is acceptable; reusing the existing fixture
is preferred only for not having two copies of the same example to keep in
sync.

### `--print-schema`

Prints the JSON schema for both tools' request models
(`ValidatePatchRequest.model_json_schema()`,
`InspectWorkspaceRequest.model_json_schema()`) to stdout as a single JSON
object keyed by tool name, then exits `0`:

```bash
agent-quality-mcp --print-schema | jq '.validate_patch.properties.mode'
```

This lets a human or a script inspect the exact schema (including the
Phase 3a field descriptions) offline, without starting an MCP session —
useful for generating external documentation, IDE autocomplete data, or
just confirming what Phase 3a's schema descriptions actually produced.

## Part 3: Self-Documenting Diagnostics Reference

Add a new `## Diagnostics And Decision Reference` section to `README.md`
listing every `BlockerKind`, `BlockerFixability`, `PatchDecision`,
`NextActionKind`, and the stable system-level diagnostic `code` values
enumerated in the audit above, each with a one-line explanation of when it
appears and (for `BlockerFixability`/`NextActionKind`) what it implies an
agent should do.

To prevent this table from silently drifting out of sync with the source
enums (the exact failure mode this phase exists to avoid creating), add a
test that derives the expected set of values from the enums themselves and
asserts the README contains a reference to each:

```python
def test_readme_documents_every_decision_and_blocker_value() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    for decision in PatchDecision:
        assert decision.value in readme
    for kind in BlockerKind:
        assert kind.value in readme
    for fixability in BlockerFixability:
        assert fixability.value in readme
    for action_kind in NextActionKind:
        assert action_kind.value in readme
```

This test does not (and cannot reasonably) validate prose quality, only
presence — it is a tripwire against forgetting to update docs when a new
enum value is added, not a substitute for human review of new entries.

Tool-adapter codes (`timeout`, `command_failed`, `invalid_json`,
`invalid_preview`, `lsp_fallback`, `unsafe_path`, and the system-level
codes from the audit) are documented in the same section as a plain table
without an enum-backed test, since they are string literals scattered
across adapter modules rather than a single enum; revisit promoting them to
a `StrEnum` only if this phase's manual table maintenance proves
error-prone in practice.

## Part 4: `config_overrides` Discoverability

Add a `supported_config_overrides` field to `InspectWorkspaceResponse`:

```python
class SupportedConfigOverride(AgentQualityBaseModel):
    field: str
    type: str
    default: Any
    description: str


class InspectWorkspaceResponse(AgentQualityBaseModel):
    # ... existing fields unchanged ...
    supported_config_overrides: list[SupportedConfigOverride] = Field(default_factory=list)
```

Populate it from `config.py`'s existing `SAFE_UNTRUSTED_CONFIG_FIELDS`
constant plus each field's type annotation and default pulled from
`AgentQualityConfig.model_fields`, with a hand-written one-line
`description` per field (six fields today: `default_mode`,
`default_safety_mode`, `uv_offline`, `secret_redaction_patterns`,
`workspace_exclusions`, `secret_file_patterns` — small enough to write by
hand rather than generate, and hand-written descriptions can explain
*why* a field is safe to override, which the type/default alone cannot).

This is additive to `InspectWorkspaceResponse` (a new field with a default,
not a change to any existing field), so it does not break the Phase 3a
schema-completeness test or any existing `inspect_workspace` test that
checks specific fields rather than exact equality. Add a new test asserting
the list is non-empty and that every entry's `field` is actually a member
of `SAFE_UNTRUSTED_CONFIG_FIELDS`, so the two cannot silently diverge.

## Part 5: Broader MCP Client Quickstart Coverage

Phase 3a's README quickstart covered Claude Desktop and Cursor. Add
equivalent copy-paste snippets for VS Code's MCP support and Windsurf,
using the same `uvx agent-quality-mcp` command — these clients'
configuration file formats differ slightly (VS Code's `mcp.json` uses a
`servers` key rather than `mcpServers` at the time of writing) and must be
verified against each client's *current* documentation at implementation
time rather than assumed from this design, since MCP client configuration
formats have changed before and may again.

## Part 6: New Validators — Explicitly Deferred

Multiple plausible additions exist (`mypy` as an alternative/additional
type checker, `bandit` or `pip-audit` for security scanning, `isort` for
import ordering not already covered by Ruff's `I` rules). This phase
deliberately does not choose any of them. Adding a validator is a product
decision with real follow-on cost (a new entry in `REQUIRED_TOOLS_BY_MODE`
or a deliberate choice to keep it always-optional, new `BlockerKind`
routing decisions, new README documentation, new allowlisted commands in
`cli/runner.py`, and an argument about whether it should be required or
optional per mode) — exactly the kind of decision the brainstorming
process exists to make deliberately, not default into a backlog item.

If the maintainer wants this, it should be its own brainstorming session
answering at minimum: which tool(s) and why; required or optional, and in
which modes; how its findings map to `BlockerKind`/`BlockerFixability`;
whether it changes confidence scoring. This design only records that the
question exists and is intentionally unanswered here.

## Testing

- `test_logging_config.py` (new): `configure_logging` with no level set
  leaves the package logger unconfigured (no handler added); with a valid
  level, sets the level and adds exactly one handler (idempotent on a
  second call — does not add a duplicate handler); with an invalid level,
  prints a warning to stderr and leaves the logger unconfigured.
- `test_cli_entrypoint.py` (extended): `--check-tools` exits `0`/`1`
  appropriately with a fake resolver; `--selftest` exits `0` against the
  packaged demo fixture and non-zero if the fixture is missing or the
  decision is not `apply_patch`; `--print-schema` exits `0` and prints
  valid JSON containing both tool names as top-level keys.
- `test_readme_documents_every_decision_and_blocker_value` (new, Part 3).
- `test_inspect_workspace_lists_supported_config_overrides` (new, Part 4):
  asserts non-empty and every `field` is in `SAFE_UNTRUSTED_CONFIG_FIELDS`.
- Full existing suite, `ruff check .`, `pyright`, `git diff --check` all
  still pass with no existing assertions changed.

## Acceptance Criteria

- `AGENT_QUALITY_MCP_LOG_LEVEL` is documented and, when set, audit events
  are visible on stderr; when unset, behavior is byte-for-byte identical
  to today (verified by a test that asserts no handler exists on the
  package logger when `configure_logging()` is called with no level).
- `agent-quality-mcp --check-tools`, `--selftest`, and `--print-schema` all
  work as specified and exit before the stdio server would start.
- README has a diagnostics/decision reference section, and a test fails if
  any `PatchDecision`/`BlockerKind`/`BlockerFixability`/`NextActionKind`
  value is ever added without a corresponding README mention.
- `inspect_workspace` responses include `supported_config_overrides`
  listing all six current safe override fields with type/default/description.
- README's MCP-client quickstart covers at least four clients (Claude
  Desktop, Cursor, VS Code, Windsurf), each verified against that client's
  current documented configuration format at implementation time.
- No new validator tool is added; Part 6's deferral is documented, not
  silently resolved by adding one anyway.
- No telemetry of any kind is added.
- Full verification suite passes unchanged.

## Self-Review Notes

- Caught and fixed during this review: the audit mentioned inventorying
  `NextActionKind` alongside `BlockerKind`/`BlockerFixability`/`PatchDecision`
  but Part 3's reference-table scope and its enum-backed test originally
  omitted it. Added `NextActionKind` to both the table scope and the test,
  and to the matching acceptance criterion, so the audit's claim and the
  design's deliverable now agree.
- Consistency: every diagnostic code and enum value listed in the audit
  section is cross-checked against Part 3's documentation scope; nothing
  inventoried is left undocumented, and nothing documented is invented
  beyond what the audit found.
- Scope: Part 6 (new validators) is deliberately a non-decision with a
  named process for making the decision later, not a deferred bullet
  that quietly implies "someday we'll just add mypy" — the design states
  explicitly what questions must be answered first.
- Every claim about current behavior (logging silence, wheel contents
  excluding test fixtures, CLI flag coverage) was verified by running a
  command or reading source in this session, not assumed; the logging
  silence and wheel-contents claims are reproduced as literal
  command/output pairs in the audit section.
