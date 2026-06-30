# Agent Quality MCP Maintainability Refactor Design (Phase 3b)

## Purpose

This phase reduces the structural maintainability debt identified in the
[Public Release Readiness design](2026-06-30-agent-quality-mcp-public-release-readiness-design.md)
(Phase 3a) and its own audit: one file four times larger than any other,
one orphaned dead-code path, and several smaller boundary/duplication
issues. It is a pure internal restructuring phase: every change here must
be invisible to `validate_patch`/`inspect_workspace` callers.

This phase does not change `decision.py`, `grouping.py`, `actions.py`,
`risk.py`, `response.py`'s public contract, or any validation outcome for
any existing test fixture. Every step is verified by the full existing
test/lint/type suite staying green with zero behavior assertions changed
(only import paths and, where explicitly noted, dead test removal).

## Source Spec

Builds on:

- `docs/superpowers/specs/2026-06-30-agent-quality-mcp-public-release-readiness-design.md`
  (Phase 3a; Resolved Decision 2 already settled the dead-code question
  this phase implements)
- `docs/superpowers/specs/2026-06-22-agent-quality-mcp-pyright-lsp-validator-capabilities-design.md`
  (original rationale for keeping the Pyright LSP code in one file "for
  now")

## Fresh Audit (Verified In This Session)

Re-measured after Phase 3a landed (Phase 3a touched no files in this list):

```
1064  src/agent_quality_mcp/lsp/pyright.py
 797  src/agent_quality_mcp/service.py
 781  src/agent_quality_mcp/patching.py
 419  src/agent_quality_mcp/diagnostics.py
 371  src/agent_quality_mcp/cli/ruff.py
```

`ruff check --select C901 src/` (cyclomatic complexity, not enabled in this
repo's lint config today) reports 9 functions over the default threshold of
10. Three are inside `lsp/pyright.py`:

- `PyrightLspProcessSession._collect_diagnostics_unlocked` (complexity 12)
- `PyrightLspProcessSession._close_shadow_root_unlocked` (complexity 13)
- `_write_stdin_message` (complexity 11)

The other six violating functions are scattered across five otherwise
reasonably-sized files (`cli/ruff.py` has two: `check` and
`_looks_like_safe_unified_diff`; `grouping.py`, `lsp/protocol.py`,
`patching.py`, and `risk.py` have one each) and are not part of this
phase's scope (see "Complexity Gate" below for the decision on what to do
about them).

`ruff check --select PLR0913 src/` (too-many-arguments) reports 19 hits,
nearly all on functions that intentionally use keyword-only arguments for
call-site clarity (for example `diagnostic_from_message`, `_build_diagnostic`).
This is a deliberate, readable pattern in this codebase, not a
maintainability problem; this phase does **not** propose enabling
`PLR0913`.

`grep` confirms `validators.py`'s `wrap_uv_result`/`wrap_ruff_result` are
still referenced only by their own tests in `tests/unit/test_validators.py`;
`service.py` still calls `UvAdapter`/`RuffAdapter` directly.

## Scope

In scope:

- Split `src/agent_quality_mcp/lsp/pyright.py` into a `lsp/pyright/`
  subpackage.
- Delete the dead `wrap_uv_result`/`wrap_ruff_result` functions and their
  tests (per Phase 3a's Resolved Decision 2).
- Extract `service.py`'s `inspect_workspace_service` and its private
  helpers into a new `inspect.py` module.
- Extract the "is this output a safe, scoped unified diff" preview
  validator out of `cli/ruff.py` into a shared helper.
- Add a scoped cyclomatic-complexity lint gate that does not require
  rewriting unrelated, already-tested functions.

Out of scope (explicitly deferred):

- Splitting `patching.py` (781 lines). It is already organized into clear
  linear phases (parse → validate → write → commit → rollback) with one
  responsibility (apply one unified diff safely) and 89% coverage. Revisit
  only if it grows further or a future change needs to touch one phase
  without the others.
- Finishing the uv/Ruff `ValidatorResult` capability-wrapper migration.
  Phase 3a's Resolved Decision 2 chose deletion over completion; reviving
  richer uv/Ruff capability metadata is a Phase 3d concern (see that
  design's "Evidence Richness" section) if ever prioritized, not part of
  this refactor.
- Fixing the other six pre-existing `C901` complexity hits outside
  `lsp/pyright.py` (across `cli/ruff.py`, `grouping.py`, `lsp/protocol.py`,
  `patching.py`, `risk.py`). They are not part of the file this phase
  splits, each is only modestly over the default threshold (11-13 vs. 10),
  and rewriting six unrelated, already-well-tested functions has a real
  regression-risk cost for a marginal readability gain. They are
  explicitly grandfathered (see "Complexity Gate").
- Any change to `decision.py`, `grouping.py`, `actions.py`, `risk.py`, or
  the public response contract.

## Part 1: Split `lsp/pyright.py` Into A Subpackage

### Current Structure (Verified By Reading The File)

`src/agent_quality_mcp/lsp/pyright.py` (1064 lines, 515 statements, 79%
coverage — the lowest non-trivial coverage in the repo) currently mixes
five concerns in declaration order:

1. **Diagnostic normalization** (lines ~58-219): `lsp_uri_from_path`,
   `path_from_lsp_uri`, `normalize_lsp_diagnostics`, and private severity/
   range/ID helpers. Pure functions, no I/O, easiest to test in isolation.
2. **Protocol-level session** (lines ~222-740, the largest chunk by far):
   `PyrightLspSession`/`PyrightLspManager`/`PyrightCliAdapter` Protocols,
   `PyrightLspProcessSession` (the stateful class that owns
   initialize/workspace-folder/document lifecycle and raw non-blocking
   stdin/stdout I/O: `_write_stdin_message`, `_stdin_ready`,
   `_read_stdout_chunk`, `_stdout_ready`, `_stream_fileno`).
3. **Provider and CLI fallback** (lines ~742-936): `PyrightLspProvider`,
   its `_fallback` method, and small diagnostic-builder helpers
   (`_pyright_langserver_unavailable`, `_pyright_cli_unavailable`).
4. **Process lifecycle helpers** (lines ~939-1004): `_process_is_alive`,
   `_close_process`, `_wait_for_process_exit`, error-reason helpers.
5. **Manager** (lines ~1007-1065): `RealPyrightLspManager`,
   `_start_process_session`.

### Target Structure

Convert the module into a package, preserving every existing public import
path:

```
src/agent_quality_mcp/lsp/
  __init__.py            (unchanged: package marker)
  protocol.py            (unchanged: generic JSON-RPC framing)
  pyright/
    __init__.py           # re-exports every current public name
    diagnostics.py        # concern 1
    session.py            # concern 2 (the protocol-level session)
    provider.py           # concern 3
    process.py            # concern 4
    manager.py            # concern 5
```

`lsp/pyright/__init__.py` re-exports the full existing public surface so
every current import keeps working without a call-site change:

```python
"""Pyright language-server integration (package re-export surface)."""

from __future__ import annotations

from agent_quality_mcp.lsp.pyright.diagnostics import (
    lsp_uri_from_path,
    normalize_lsp_diagnostics,
    path_from_lsp_uri,
)
from agent_quality_mcp.lsp.pyright.manager import RealPyrightLspManager
from agent_quality_mcp.lsp.pyright.provider import (
    PyrightCliAdapter,
    PyrightLspManager,
    PyrightLspProvider,
    PyrightLspSession,
)
from agent_quality_mcp.lsp.pyright.session import PyrightLspProcessSession

__all__ = [
    "PyrightCliAdapter",
    "PyrightLspManager",
    "PyrightLspProcessSession",
    "PyrightLspProvider",
    "PyrightLspSession",
    "RealPyrightLspManager",
    "lsp_uri_from_path",
    "normalize_lsp_diagnostics",
    "path_from_lsp_uri",
]
```

Internal-only helpers (the `_`-prefixed functions) move to whichever new
file owns their concern and do **not** need re-exporting; nothing outside
the old `lsp/pyright.py` imported them (verified with `grep` for each
helper name across `src/` and `tests/` before moving it).

Cross-file dependencies after the split:

- `provider.py` imports from `diagnostics.py` (`normalize_lsp_diagnostics`)
  and depends on `session.py`/`process.py` only through the
  `PyrightLspSession`/`PyrightLspManager` Protocols already defined in
  `provider.py` today — no new coupling.
- `manager.py` imports `PyrightLspProcessSession` from `session.py` and the
  process helpers it needs (`close`, lifecycle) from `process.py`.
- `session.py` imports protocol framing from `agent_quality_mcp.lsp.protocol`
  (unchanged) and diagnostic-adjacent helpers it still needs
  (`lsp_uri_from_path`, `path_from_lsp_uri`) from `diagnostics.py`.

### Test File Split

`tests/unit/test_pyright_lsp.py` (1414 lines, the largest test file in the
repo) splits along the same boundary:

```
tests/unit/test_pyright_lsp_diagnostics.py   # URI/range/severity tests
tests/unit/test_pyright_lsp_session.py       # PyrightLspProcessSession tests
tests/unit/test_pyright_lsp_provider.py      # PyrightLspProvider/fallback tests
tests/unit/test_pyright_lsp_manager.py       # RealPyrightLspManager tests
```

Every existing test moves verbatim (function bodies unchanged) into the
file matching what it tests; this plan does not rewrite any test logic.

### Migration Safety

Because `service.py` already imports from `agent_quality_mcp.lsp.pyright`
as a *module* (`from agent_quality_mcp.lsp.pyright import PyrightLspProvider,
RealPyrightLspManager`), converting it to a *package* with an `__init__.py`
that re-exports those same names is source-compatible by construction: the
import statement does not change, only what resolves it does. The
implementation plan must:

1. Run the full test suite after creating the package skeleton but before
   deleting the original file, to confirm the new package's re-exports
   satisfy every existing import.
2. Move one concern at a time (diagnostics, then process, then session,
   then provider, then manager), running the targeted test file plus
   `tests/unit/test_service.py` after each move.
3. Only delete the original flat `lsp/pyright.py` once all five concerns
   have a new home and the full suite is green.

### Complexity Gate

Add `lint.mccabe.max-complexity = 13` and enable `C901` in
`pyproject.toml`'s `[tool.ruff.lint]` `select` list, with a per-file
`[tool.ruff.lint.per-file-ignores]` entry suppressing `C901` for the six
pre-existing violations outside `lsp/pyright/` that this phase does not
fix:

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S", "C901"]
ignore = ["S101"]

[tool.ruff.lint.mccabe]
max-complexity = 13

[tool.ruff.lint.per-file-ignores]
"src/agent_quality_mcp/cli/ruff.py" = ["C901"]
"src/agent_quality_mcp/grouping.py" = ["C901"]
"src/agent_quality_mcp/lsp/protocol.py" = ["C901"]
"src/agent_quality_mcp/patching.py" = ["C901"]
"src/agent_quality_mcp/risk.py" = ["C901"]
```

Threshold `13` (not the ruff default of `10`) is chosen deliberately, and
was verified by actually applying this exact config to the repository in
this session, not computed by hand: `max-complexity = 12` still failed,
because `PyrightLspProcessSession._close_shadow_root_unlocked`'s measured
complexity is 13, one more than this design's first draft assumed; `13` is
the lowest threshold under which all three split-out
`lsp/pyright/session.py` functions pass with zero unsuppressed violations
and the five named per-file ignores. Splitting the file does not by itself
reduce a function's complexity, only its file's size. This gate's purpose
is to stop the *next* oversized file from forming silently, not to
retroactively force a deeper rewrite of working, tested code as a side
effect of a file-layout change. The maintainer can ratchet the threshold
down later as a separate, explicit decision once the per-file-ignored
functions are revisited on their own merits.

Add a second, simpler guardrail this repo has no native equivalent for:
a CI step that fails if any tracked `src/agent_quality_mcp/**/*.py` file
exceeds 600 lines, with an explicit allowlist for every file this phase
deliberately leaves over that line. Verified by actually running the draft
script against the current tree before finalizing this design (not just
describing it): it flags exactly three files today —
`lsp/pyright.py` (1064 lines, fixed by Part 1), `service.py` (797 lines,
shrunk by Part 3 to roughly 700 — still over 600, so it needs the
allowlist too, not just `patching.py`), and `patching.py` (781 lines,
explicitly out of scope per this phase's Scope section and unchanged by
any part of this design). After Part 1 and Part 3 land, exactly two files
remain over the threshold, both already named in this design as
deliberately deferred, not newly discovered:

```bash
#!/usr/bin/env bash
# scripts/check_file_length.sh
set -euo pipefail
MAX_LINES=600
ALLOWLIST=(
  "src/agent_quality_mcp/service.py"
  "src/agent_quality_mcp/patching.py"
)
status=0
while IFS= read -r -d '' file; do
  if [[ " ${ALLOWLIST[*]} " == *" $file "* ]]; then
    continue
  fi
  lines=$(wc -l < "$file")
  if (( lines > MAX_LINES )); then
    echo "::error file=$file::$file has $lines lines, exceeds $MAX_LINES"
    status=1
  fi
done < <(git ls-files -z ':(glob)src/agent_quality_mcp/**/*.py')
exit "$status"
```

The `:(glob)` pathspec magic is required, not cosmetic: a plain
`'src/agent_quality_mcp/**/*.py'` pathspec only matched 10 of the 32
tracked Python files under `src/agent_quality_mcp/` when verified directly
in this session (`git ls-files` without glob magic does not treat `**` as
"this directory and all subdirectories," so it silently skipped every file
directly inside `src/agent_quality_mcp/` itself — `service.py`,
`actions.py`, `decision.py`, and every other top-level module, which are
exactly the files most likely to need this guardrail). The implementation
plan must keep the `git ls-files -z ':(glob)...'` count test from the
Testing Strategy section below to prevent this exact regression.

Add a `File length` step to `.github/workflows/ci.yml` calling this script.

## Part 2: Delete The Dead Validator-Wrapper Code

Per Phase 3a's Resolved Decision 2:

- Delete `wrap_uv_result` and `wrap_ruff_result` from
  `src/agent_quality_mcp/validators.py`.
- Delete `test_wrap_uv_result_reports_project_and_lock_metadata`,
  `test_wrap_uv_result_records_skipped_lock_check`, and
  `test_wrap_ruff_result_reports_scope_rule_codes_and_safe_fix_preview`
  from `tests/unit/test_validators.py`.
- Keep `ValidatorCapability`, `ValidatorScope`, `SkippedCheck`,
  `ValidatorRequest`, `ValidatorResult`, and `ValidatorProvider` in
  `validators.py` unchanged — these are still the real, used internal
  contract for the Pyright LSP provider (`PyrightLspProvider` returns a
  `ValidatorResult` directly without going through a wrapper function).
- Keep `test_validator_request_keeps_real_and_shadow_roots_separate`
  (the one remaining test in `test_validators.py` after the two `wrap_*`
  tests are removed); it exercises a model that is still in active use.

No other file references the deleted functions (verified with `grep`
before writing this design), so this is a pure deletion with no call-site
updates required.

## Part 3: Extract `inspect_workspace_service`

`service.py` (797 lines) mixes two responsibilities that only share a
config-loading helper: `validate_patch_service` (the large orchestration
flow) and `inspect_workspace_service` (a much smaller, independent read-only
flow). Verified function inventory (`grep '^def \|^class '`):
`inspect_workspace_service` and its four private helpers
(`_inspect_command_availability`, `_inspect_response_config`,
`_safe_list_summary`, `_default_limits`) total roughly 100 lines and have no
call edges into `validate_patch_service`'s helpers other than the shared
`AgentQualityConfig`/`load_config` imports already used by both.

Move `inspect_workspace_service` and those four helpers into a new
`src/agent_quality_mcp/inspect.py`. `service.py` re-exports
`inspect_workspace_service` for any external import that still expects it
on `service` (`from agent_quality_mcp.service import inspect_workspace_service`
must keep working, the same source-compatibility rule as Part 1):

```python
# service.py, after the move
from agent_quality_mcp.inspect import inspect_workspace_service  # noqa: F401
```

`tools.py` should be updated to import directly from the new module
(`from agent_quality_mcp.inspect import inspect_workspace_service`) since
it is an internal call site this phase controls; the re-export in
`service.py` exists for compatibility, not as the preferred internal path
going forward.

This leaves `service.py` at roughly 700 lines, entirely about
`validate_patch_service` orchestration. It does not cross the 600-line
guardrail from Part 1's complexity gate as a hard requirement (this design
explicitly allowlists `service.py` if so); shrinking it further (for
example separating adapter-running from response-building) is left as a
follow-up idea, not committed here, to avoid scope creep in a phase whose
purpose is removing already-identified debt, not searching for new debt
indefinitely.

## Part 4: Deduplicate Safe-Diff Validation

`cli/ruff.py`'s `_looks_like_safe_unified_diff`, `_consume_valid_hunk`,
`_hunk_line_count`, `_hunk_counts_match`, `_hunk_body_line_counts`, and
`_diff_header_path` (roughly lines 187-336, ~150 lines) implement "is this
text a safe, scoped unified diff produced by `ruff --fix --diff`" using the
same hunk-header regex and line-classification concepts as
`patching.py`'s own hunk parser, but as an independent reimplementation
with a narrower purpose (validating Ruff's own preview output, not
applying an arbitrary patch).

Extract this block verbatim into a new
`src/agent_quality_mcp/cli/diff_preview.py` with one public function:

```python
def is_safe_scoped_unified_diff(
    text: str,
    cwd: Path,
    scoped_file_args: list[str],
) -> bool:
    """Validate that text is a safe unified diff scoped to allowed files."""
```

`cli/ruff.py` imports and calls this function instead of defining the
logic inline. This is a pure move (rename `_looks_like_safe_unified_diff`
to the new public name, move its private helpers alongside it), not a
rewrite — it does not attempt to unify this logic with `patching.py`'s
parser in this phase. A deeper unification (one shared hunk-parsing core
used by both the patch-apply path and the safe-fix-preview-validation
path) is a larger, riskier change touching the patch-application security
boundary; it is noted as a possible Phase 3b-follow-up idea, not undertaken
here, because `patching.py`'s parser has stricter security obligations
(rejecting malformed patches that this preview-only validator does not need
to reject as conservatively) and merging them risks weakening one or the
other's guarantees for a code-size win that is, on its own, modest (~150
lines moved, not eliminated).

This change shrinks `cli/ruff.py` from 371 to roughly 220 lines and gives
the safe-fix-preview logic its own test file
(`tests/unit/test_cli_diff_preview.py`, moved verbatim from the relevant
tests in `tests/unit/test_cli_adapters.py`).

## Testing Strategy

- Every move in Parts 1-4 is a refactor: no test's *assertions* change,
  only which file defines the function/class under test and which file
  the test itself lives in.
- After each part, run:

```bash
.venv/bin/python -m pytest -v
.venv/bin/ruff check .
.venv/bin/pyright --pythonpath .venv/bin/python
git diff --check
```

- After Part 1 specifically, additionally run
  `tests/unit/test_service.py` and `tests/integration/test_validate_patch_demo.py`
  in isolation to confirm the Pyright LSP wiring through `service.py` is
  unaffected by the import-path change.
- After Part 1's complexity gate is enabled, run
  `.venv/bin/ruff check --select C901 src/` and confirm exactly zero
  unsuppressed violations (the six pre-existing ones are explicitly
  allowlisted; any new one anywhere else is a real regression to fix
  before merging).
- After the file-length guardrail script is added, run it locally against
  the post-refactor tree and confirm it exits 0, and separately confirm
  `git ls-files -z ':(glob)src/agent_quality_mcp/**/*.py' | tr '\0' '\n' | wc -l`
  matches the true count of tracked Python files under
  `src/agent_quality_mcp/` (32 at the time of this design) — guarding
  against the glob-pathspec mistake found and fixed during this design's
  own self-review.

## Acceptance Criteria

- `lsp/pyright.py` no longer exists as a flat file; `lsp/pyright/` is a
  package with `diagnostics.py`, `session.py`, `provider.py`, `process.py`,
  `manager.py`, and an `__init__.py` re-exporting the prior public surface.
- No file under `src/agent_quality_mcp/lsp/pyright/` exceeds 600 lines.
- `from agent_quality_mcp.lsp.pyright import PyrightLspProvider, RealPyrightLspManager`
  (the exact import `service.py` already uses) continues to work unchanged.
- `validators.py` no longer defines `wrap_uv_result` or `wrap_ruff_result`;
  `tests/unit/test_validators.py` no longer references them.
- `inspect_workspace_service` and its four helpers live in `inspect.py`;
  `service.py` re-exports `inspect_workspace_service` for compatibility;
  `tools.py` imports it from `inspect.py` directly.
- `cli/ruff.py` no longer defines `_looks_like_safe_unified_diff` and its
  hunk helpers inline; they live in `cli/diff_preview.py` as
  `is_safe_scoped_unified_diff`.
- `C901` is enabled with `max-complexity = 13` and exactly the five named
  pre-existing per-file ignores; zero unsuppressed violations anywhere.
- A file-length CI guardrail exists, allowlists exactly `service.py` and
  `patching.py` (both already named in this design as deliberately over
  the threshold), and passes against the refactored tree.
- The full existing test suite passes with no assertion changes; coverage
  does not regress below the existing 78% gate (Pyright LSP module
  coverage may improve simply from smaller, more focused test-to-file
  mapping, but this is not a required acceptance bar for this phase).
- `ruff check .`, `pyright --pythonpath .venv/bin/python`, and
  `git diff --check` all pass.

## Self-Review Notes

- Caught and fixed during this review, by actually applying every
  proposed config/script against this repository rather than only
  describing it, three separate times:
  1. The file-length script's `git ls-files` pathspec was wrong as first
     drafted — a plain `'src/agent_quality_mcp/**/*.py'` pathspec
     (without `:(glob)` magic) matches only 10 of the 32 tracked files,
     silently skipping every file directly inside `src/agent_quality_mcp/`
     itself, including `service.py`; fixed to
     `':(glob)src/agent_quality_mcp/**/*.py'` with a count-based
     regression test.
  2. The claim that the file-length allowlist would be empty after this
     phase was wrong — running the corrected script found `patching.py`
     (781 lines, explicitly out of scope for this phase) would also fail
     the 600-line check, not just `service.py`; the design and its
     acceptance criteria now name both.
  3. The proposed `max-complexity = 12` threshold was arithmetically
     wrong — `PyrightLspProcessSession._close_shadow_root_unlocked`'s
     measured complexity is 13, so a threshold of 12 still failed when
     actually applied with `ruff check --select C901`; corrected to `13`,
     re-verified to produce zero unsuppressed violations with the same
     five per-file ignores, and the original repository's `pyproject.toml`
     was restored unchanged after each experiment (`git diff --stat
     pyproject.toml` confirmed empty before moving on).
- Consistency: the cross-reference to a Phase 3d "Evidence Richness"
  section (for reviving uv/Ruff capability metadata instead of finishing
  the deleted wrapper functions) points at a section that exists in
  `2026-06-30-agent-quality-mcp-power-performance-design.md`, verified by
  re-reading that document after writing this one.
- Scope: every part of this phase is a structural move with no behavior
  change, verified per-part by stating exactly which files/tests are
  touched and why no assertion changes; the one genuinely new mechanism
  (the complexity/file-length lint gates) is scoped to not force unrelated
  rewrites, with an explicit, named allowlist rather than a silent
  threshold change.
- Ambiguity check: the `max-complexity = 12` threshold's rationale (lowest
  value under which the three split-out functions pass without further
  rewriting) is stated explicitly so a future reader cannot mistake it for
  an arbitrary number.
