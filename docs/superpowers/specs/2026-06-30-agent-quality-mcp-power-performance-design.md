# Agent Quality MCP Power And Performance Design (Phase 3d)

## Purpose

This phase makes `validate_patch` faster in its slowest configurations and
makes the server deployable as a shared/remote service instead of only a
per-editor spawned stdio subprocess, without weakening the security model
that every prior phase has preserved. It is the highest-risk phase of the
roadmap from the [Public Release Readiness design](2026-06-30-agent-quality-mcp-public-release-readiness-design.md):
two of its three parts touch concurrency, timeout accounting, or network
exposure, so each part is designed to be implemented and shipped
independently, not as one inseparable change.

## Source Spec

Builds on
`docs/superpowers/specs/2026-06-30-agent-quality-mcp-public-release-readiness-design.md`
(Phase 3a) and explicitly revisits two items the original Phase 1/2 and
Pyright-LSP specs deferred on purpose:

- "Parallel validator execution is out of scope because shared timeout
  accounting, LSP lifecycle cleanup, and deterministic command ordering
  matter more than throughput for the light implementation." (Pyright LSP
  design, "Concurrency And Diagnostic Ownership")
- "Broad Python project-shape detection beyond what is needed for clearer
  evidence." (Phase 2 design, "Scope")
- "Stdio transport only." (README, MVP Limitations, present since Phase 1)

## Scope

In scope:

- Parallel execution of the `uv`, Ruff, and Pyright validator calls within
  one `validate_patch` request (Part 1).
- An opt-in `streamable-http` transport, gated behind mandatory bearer-token
  authentication (Part 2).
- A scoped, informational-only Python project-shape detection added to
  `inspect_workspace` (Part 3).
- A resolution of the uv/Ruff evidence-richness question Phase 3a's dead-code
  decision raised but did not answer: recorded and explicitly **not** built
  in this phase, for lack of evidenced need (Part 4).
- An explicit, reasoned decision **not** to build response/diagnostic
  caching in this phase (Part 5), with a safe design sketched for later if
  ever justified by real evidence.

Out of scope:

- Any change to `decision.py`'s precedence rules or `risk.py`'s scoring
  formula. Parallelizing *when* tools run must not change *what* they
  report.
- SSE transport. `streamable-http` is the modern, recommended HTTP
  transport in the current `mcp` SDK; SSE is kept available at the SDK
  level but this phase does not add server-side wiring or documentation
  for it, to avoid maintaining two HTTP transport stories at once.
- Full OAuth 2.0 authorization-server integration
  (`mcp.server.auth.settings.AuthSettings`/`auth_server_provider`). A
  single shared bearer token is the right scope for this tool's likely
  deployment shape (one team, one or a few trusted callers); a full OAuth
  flow is a separate, larger design if ever needed for multi-tenant use.
- Incremental/copy-on-write shadow workspace creation. Mentioned under
  "Considered And Rejected" below with the reasons it is not pursued now.
- Multi-language support beyond Python. Unchanged from every prior phase's
  scope statement.

## Part 1: Parallel Validator Execution

### Current Behavior (Verified By Reading `service.py`)

`_run_adapters` calls `uv`, then Ruff, then the Pyright provider, strictly
sequentially, with a `timeout_check()` call before each:

```python
timeout_check()
uv_diagnostics, uv_records = _adapter_call("uv", lambda: UvAdapter(runner).check(...), ...)
timeout_check()
ruff_diagnostics, ruff_records, ruff_fixes = _adapter_call("ruff", lambda: RuffAdapter(runner).check(...), ...)
timeout_check()
pyright_result = _adapter_call("pyright", lambda: _run_pyright_provider(...), ...)
timeout_check()
```

`timeout_check` raises `_RequestTimeoutError` if
`time.monotonic() - started_at > config.request_timeout_seconds`. Each
individual subprocess call is separately bounded by
`config.subprocess_timeout_seconds` inside `CommandRunner.run_with_output`.
Total wall-clock time today is `uv_time + ruff_time + pyright_time` (plus
fixed overhead); in `standard`/`strict` mode, where all three always run,
this is the dominant cost of a `validate_patch` call after the shadow-copy
step.

### Why This Is Safe To Parallelize

Verified directly from the adapter implementations, not assumed:

- `UvAdapter.check`, `RuffAdapter.check`, and `_run_pyright_provider` each
  only **read** the shadow workspace that was fully copied and patched
  *before* `_run_adapters` is called; none of the three writes to it.
  (`ruff check --fix --diff` is the one command that could sound like a
  write; `--diff` mode does not write to disk — it only prints what would
  change. This must be re-confirmed with a targeted test before this phase
  ships, not just asserted from this design, since it is the one place a
  mistake here would violate the "shadow workspace is the only thing that
  could move" invariant.)
- `CommandRunner.run`/`run_with_output` reads `self.config` and otherwise
  uses only locally-scoped variables (`executable`, `safe_env`,
  `started_at`) per call; the same `CommandRunner` instance can safely
  handle three concurrent calls from different threads.
- `PyrightLspProvider`/`RealPyrightLspManager` already serialize Pyright
  LSP access *per real workspace* with their own internal
  `threading.RLock` — but that lock guards against two *different*
  `validate_patch` requests for the same real workspace overlapping, which
  is an existing concern orthogonal to this phase. Within one request there
  is exactly one Pyright call, so parallelizing it against uv/Ruff within
  that same request introduces no new lock contention.
- Python's GIL is released during a blocking `subprocess.run`/pipe wait, so
  a `concurrent.futures.ThreadPoolExecutor` (not multiprocessing) is the
  right tool: no GIL contention for this I/O-bound workload, and no
  serialization/IPC cost for passing `Diagnostic`/`CommandExecutionRecord`
  Pydantic models back across a process boundary.

### Design

Replace the three sequential `_adapter_call` invocations in `_run_adapters`
with a bounded thread pool, while preserving every existing per-tool error
boundary (`_adapter_call`'s `ToolUnavailableError`/`CommandExecutionError`/
`Exception` handling is unchanged — only *when* each callable runs changes):

```python
def _run_adapters(...) -> _AdapterRunResult:
    deadline = time.monotonic() + _remaining_budget(started_at, config)
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="aqm-validator") as pool:
        futures = {
            "uv": pool.submit(_adapter_call, "uv", lambda: UvAdapter(runner).check(shadow_root, mode), ([],)),
            "ruff": pool.submit(_adapter_call, "ruff", lambda: RuffAdapter(runner).check(...), ([], [])),
            "pyright": pool.submit(_adapter_call, "pyright", lambda: _run_pyright_provider(...), ([],)),
        }
        results = {}
        for tool, future in futures.items():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                results[tool] = future.result(timeout=remaining)
            except FuturesTimeoutError:
                results[tool] = _timeout_fallback_result(tool)
    return _combine_in_canonical_order(results)
```

Key design points, each addressing a concern the original deferral named
explicitly:

- **Timeout accounting**: a single `deadline` is computed once from the
  *remaining* request budget (not a fresh per-tool budget), and each
  `future.result(timeout=remaining)` call shrinks the allowance for the
  next tool by however much wall-clock time has already elapsed. The total
  time this function can take is still bounded by the same
  `request_timeout_seconds` the sequential version respected — parallelism
  changes *how much work fits* inside that budget, not the budget itself.
- **A future that times out is not a crashed adapter**: `future.result(timeout=...)`
  raises `concurrent.futures.TimeoutError` without cancelling the
  underlying thread (Python's `ThreadPoolExecutor` cannot forcibly cancel a
  running thread). The submitted callable keeps running in the background
  until its own internal `subprocess_timeout_seconds` bound stops it; this
  design accepts that a slow tool's thread may still be alive when the
  pool's `with` block exits (the `with` block's implicit `shutdown(wait=True)`
  on exit means **the function will block until all three callables finish
  or hit their own internal subprocess timeout**, even if one future's
  *result* was already treated as a timeout for response purposes). This is
  a deliberate, bounded trade-off: it can make a single timed-out request
  take slightly longer in wall-clock terms than the strict
  `request_timeout_seconds` budget under worst-case conditions (a hung
  subprocess ignoring its own timeout), exactly as today's sequential code
  already can if a single `subprocess.run(timeout=...)` call's OS-level
  kill is delayed. This phase does not change that pre-existing bound; it
  only changes how many such calls can overlap. If this trade-off is
  unacceptable, the alternative is `pool.shutdown(wait=False, cancel_futures=True)`
  immediately after collecting results, which cancels not-yet-started
  futures but still cannot kill an already-running subprocess — the
  implementation plan should pick one explicitly and test the timeout path
  with a deliberately slow fake adapter rather than leaving this implicit.
- **Deterministic response ordering**: `_combine_in_canonical_order`
  concatenates `results["uv"]`, then `results["ruff"]`, then
  `results["pyright"]`, regardless of which future actually completed
  first. This guarantees `diagnostics`/`commands` list order is identical
  to today's sequential order for the same inputs, so no downstream code
  (including `grouping.py`'s "first evidence" selection, which picks
  `diagnostics[0]` within a group) observes any difference from
  parallelizing. This is the concrete mechanism for the original spec's
  "deterministic command ordering" concern — not avoided, satisfied by
  construction.
- **Per-tool errors stay isolated**: `_adapter_call`'s existing
  try/except boundaries run *inside* each submitted callable, so one
  tool's unexpected exception cannot propagate into another tool's future
  or crash the pool; this is unchanged from today's behavior, just now
  happening on a worker thread instead of the main thread.

### Expected Benefit And How To Measure It

Qualitatively: total wall-clock time approaches
`max(uv_time, ruff_time, pyright_time)` instead of their sum. Pyright is
typically the slowest of the three on any non-trivial codebase, so the
realistic benefit is "save most of `uv_time + ruff_time`," which matters
most in `standard`/`strict` mode where all three always run (in `quick`
mode, `uv` does not run at all, so the benefit is smaller).

This design does not assert a specific percentage, because it depends on
the target workspace. The implementation plan must add a benchmark
(timing `validate_patch_service` against `tests/fixtures/demo_repo` and
against a larger synthetic fixture, sequential vs. parallel, with results
recorded in the PR description) as part of its acceptance criteria, rather
than relying on this design's qualitative claim alone.

## Part 2: Opt-In `streamable-http` Transport

### SDK Capability (Verified By Inspecting The Installed `mcp` Package)

`FastMCP.run()` already accepts
`transport: Literal["stdio", "sse", "streamable-http"]`. `FastMCP.__init__`
already accepts `host`, `port`, `streamable_http_path`, `auth: AuthSettings | None`,
`token_verifier: TokenVerifier | None`, and
`transport_security: TransportSecuritySettings | None`. `TokenVerifier` is a
one-method Protocol:

```python
class TokenVerifier(Protocol):
    async def verify_token(self, token: str) -> AccessToken | None: ...
```

`TransportSecuritySettings` defaults to
`enable_dns_rebinding_protection=True` with empty `allowed_hosts`/
`allowed_origins` lists. None of this requires the full OAuth
`AuthSettings`/`auth_server_provider` machinery to use safely — passing
`token_verifier` directly to `FastMCP(...)` is sufficient for a simple
shared-secret bearer token model.

### Design

- Add `--transport {stdio,streamable-http}` (default `stdio`, preserving
  current behavior exactly) and `--port`/`--host` to `parse_args`.
- Add a trusted-environment-variable-only bearer token, following the
  existing `AGENT_QUALITY_MCP_*` naming convention used for command paths:
  `AGENT_QUALITY_MCP_HTTP_BEARER_TOKEN`. This is deliberately **not**
  accepted via `config_overrides` or workspace `pyproject.toml` — it must
  come from the trusted server-admin process environment, the same trust
  tier as `AGENT_QUALITY_MCP_PYRIGHT` and friends.
- **Fail closed**: if `--transport streamable-http` is requested and
  `AGENT_QUALITY_MCP_HTTP_BEARER_TOKEN` is not set, `main()` exits non-zero
  with a clear error before binding any socket. There is no "HTTP mode
  with no auth" code path, not even for local testing — local testing uses
  stdio, which is unaffected by any of this.
- Implement a minimal `TokenVerifier`:

```python
class StaticBearerTokenVerifier:
    """Verify a single trusted bearer token from server-admin configuration."""

    def __init__(self, expected_token: str) -> None:
        self._expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._expected_token):
            return None
        return AccessToken(token=token, client_id="agent-quality-mcp-http", scopes=[])
```

  Using `hmac.compare_digest` (constant-time comparison) rather than `==`
  to avoid a timing side-channel on token comparison — small but
  appropriate given this codebase's existing security-conscious patterns
  elsewhere (redaction, allowlisting, fail-closed defaults).
- Require `--allowed-host`/`--allowed-origin` (repeatable flags) when
  `streamable-http` is selected, mapped directly to
  `TransportSecuritySettings.allowed_hosts`/`allowed_origins`. Do not
  default these to `["*"]` or otherwise widen them implicitly — an empty
  default combined with `enable_dns_rebinding_protection=True` is the
  SDK's own safe default, and this design keeps it rather than loosening
  it for convenience.
- Document in `README.md`, prominently, that `streamable-http` mode:
  - Is for trusted-network or properly-fronted (TLS-terminating reverse
    proxy, VPN, etc.) deployments only; this server does not terminate TLS
    itself.
  - Still only ever validates inside shadow workspaces and never mutates
    `workspace_root` — the transport change does not touch that guarantee.
  - Does change the *exposure* model: any caller holding the bearer token
    can ask `inspect_workspace`/`validate_patch` about **any
    `workspace_root` path the server process can read**, which is a wider
    blast radius than a locally-spawned stdio subprocess that only ever
    has the single local user invoking it. This is the one place in the
    entire roadmap where this document recommends the maintainer
    explicitly accept a stated, written-down risk rather than treating it
    as fully mitigated by the bearer token alone — operators running HTTP
    mode should run one server instance per trust boundary (for example,
    per repository or per team), not one shared instance pointed at
    arbitrary paths for multiple untrusted tenants.

## Part 3: Scoped Python Project-Shape Detection

The original Phase 2 design deferred "broad" project-shape detection. This
phase adds a narrow, informational-only slice: detect and report, without
changing any validation behavior, whether the workspace looks like a
src-layout (`src/<package>/`) or flat-layout project, and whether more than
one `pyproject.toml` exists under the workspace (a monorepo signal).

- Add `project_layout: Literal["src", "flat", "unknown"]` and
  `additional_pyproject_files: list[str]` to `InspectWorkspaceResponse`.
- Detection is a simple, explicit heuristic, not a generalized build-system
  parser: `src` if `src/` exists and contains at least one directory with
  an `__init__.py`; `flat` if a directory at the workspace root (excluding
  configured exclusions) contains an `__init__.py`; `unknown` otherwise.
  `additional_pyproject_files` lists any `pyproject.toml` found by
  `inspect_workspace_files`'s existing `rglob` walk other than the one at
  `workspace_root` itself.
- This phase does **not** use this information to change `uv`/Ruff/Pyright
  invocation scope, required-tool routing, or confidence scoring. It is
  reported for the calling agent's/human's own judgment only. Using it to
  change validator behavior (for example, pointing Pyright at `src/`
  specifically, or running per-package checks in a detected monorepo) is a
  legitimate future idea but is a *behavior* change to validation, which
  this design's "informational only" framing deliberately keeps separate
  and out of scope here.

## Part 4: Evidence Richness — Reviving uv/Ruff Capability Metadata

Phase 3a's Resolved Decision 2 chose to delete the orphaned
`wrap_uv_result`/`wrap_ruff_result` functions (implemented by Phase 3b)
rather than finish wiring `service.py`'s `uv`/Ruff calls through the
`ValidatorResult` capability contract that the Pyright LSP provider already
uses. That decision was about removing dead code safely, not about whether
richer `uv`/Ruff evidence is wanted — this part is where that question
actually belongs, since it is a *capability* (more powerful evidence for an
agent's decision-making), not a refactor.

If pursued, the shape mirrors what `PyrightLspProvider` already returns
today: `UvAdapter.check`/`RuffAdapter.check` would be wrapped to populate
`ValidatorResult.metadata` with the same kind of scope/skip/completion
detail the original Pyright-LSP design specified for them (`project_detected`,
`lock_check_completed`, `sync_dry_run_*` for `uv`; `scope`, `scoped_files`,
`rule_codes`, `fixable_rule_codes`, `safe_fix_preview_completed` for Ruff —
see the Pyright-LSP design's "uv Capability Behavior" and "Ruff Capability
Behavior" sections for the exact fields already specified once before).
This metadata would feed `ResponseEvidence`/`required_checks` so an agent
can see *why* a check was skipped or incomplete for `uv`/Ruff with the same
granularity it already gets for Pyright (`fallback_to_cli`,
`diagnostic_scope`, etc.).

This design explicitly does **not** commit to building this now: nothing in
this phase's audit shows current `uv`/Ruff evidence (which already reports
tool availability and pass/fail per `ResponseEvidence.tool_availability`
and `required_checks`) is insufficient for any observed decision an agent
needs to make. Recorded here only so the question this phase's own
predecessor decision raised has one canonical place to be picked up,
instead of staying an unresolved cross-reference between two other specs.

## Part 5: Caching — Considered And Explicitly Not Built Now

A content-hash-keyed cache (`(tool, tool_version, sorted file content
hashes, relevant rule config hash) -> cached diagnostics`) would eliminate
the most dangerous class of cache bug (time-based staleness) since
invalidation is automatic whenever any relevant content changes. This is
sketched here for completeness, not committed:

```python
cache_key = (
    "ruff",
    ruff_version,
    tuple(sorted((path, sha256_of(content)) for path, content in scanned_files)),
)
```

This phase recommends **against** building this now, for two reasons that
are about more than effort:

1. **No evidenced need.** Nothing in this audit or in Part 1's
   parallelization work suggests caching is the next bottleneck; Part 1
   already captures the bulk of the available latency win by overlapping
   independent work instead of skipping work.
2. **Correctness stakes are asymmetric.** This product's entire value is
   telling an agent the truth about the *current* state of a patch. A
   caching bug that returns a stale `apply_patch` decision is qualitatively
   worse than the latency it would have saved, because the failure is
   silent (a wrong "looks fine" rather than a slow but correct one). Any
   future caching proposal should come with its own dedicated correctness
   test suite (proving cache hits and misses agree with a non-cached run
   across a representative diagnostic-producing fixture set) before being
   considered for implementation, not as an afterthought.

If real usage data later shows repeated near-identical validations are a
measured bottleneck, revisit this as its own phase with that evidence
attached, using the cache-key sketch above as a starting point.

## Considered And Rejected: Incremental Shadow Workspace Copying

`shadow.py` copies the entire workspace (respecting size limits) on every
request. For very large repositories this copy, not tool execution, could
become the dominant cost. An incremental or copy-on-write approach
(hardlinking unchanged files, or an overlay filesystem) was considered and
is explicitly **not** part of this phase:

- Hardlinking is unsafe by default: a hardlinked file is the *same inode*
  as the original, so any in-place write through the shadow path (which
  this design has not audited for being write-free the way it audited the
  three validators in Part 1) would corrupt the real workspace — directly
  violating the project's foundational guarantee. Making this safe would
  require either copy-on-write semantics at the filesystem level (not
  portable across the platforms this server already supports without
  assuming a specific filesystem) or copying any file before its first
  write inside the shadow, which reintroduces most of the complexity this
  idea was meant to avoid.
- No evidence in this audit suggests workspace copying, rather than tool
  execution, is actually the bottleneck for realistic target repositories
  (most are well under the existing `max_workspace_copy_bytes` default of
  50 MB, which copies in well under a second on typical CI/dev hardware).

This is recorded here so the idea is not silently re-proposed without
acknowledging why it was set aside; revisit only with a measured workload
where copying time, not tool time, dominates.

## Testing

- **Part 1**: a unit test with three fake adapters with artificial
  `time.sleep` delays asserts total wall-clock time is closer to
  `max(delays)` than `sum(delays)`. A test with a deliberately
  never-returning fake adapter asserts the overall request still produces
  a `request_human_review`-routed timeout response within
  `request_timeout_seconds` plus a small bounded grace period (not
  hanging indefinitely). A test asserts `diagnostics`/`commands` ordering
  is identical between a sequential-order fixture and a parallel run
  where completion order is deliberately reversed (inject artificial
  delays so Pyright "finishes" before uv in the test). A targeted test
  confirms `ruff check --fix --diff` does not modify shadow workspace file
  contents (read file bytes before and after the call).
- **Part 2**: `StaticBearerTokenVerifier` tests for matching/non-matching/
  empty tokens. A `main()`/`parse_args` test asserts `--transport
  streamable-http` without the trusted env var set exits non-zero before
  any server/socket setup (mock `FastMCP.run` to assert it is never
  called). A test asserts `--transport streamable-http` with the env var
  set constructs `FastMCP` with a non-`None` `token_verifier` and a
  `TransportSecuritySettings` reflecting the provided `--allowed-host`/
  `--allowed-origin` flags.
- **Part 3**: unit tests for `src`/`flat`/`unknown` layout detection against
  constructed temp-directory fixtures, and for `additional_pyproject_files`
  detection in a constructed two-`pyproject.toml` fixture.
- Full existing suite, `ruff check .`, `pyright`, `git diff --check` all
  pass with zero changes to existing decision/response-contract test
  assertions.

## Acceptance Criteria

- `validate_patch` in `standard`/`strict` mode runs `uv`, Ruff, and Pyright
  concurrently; a benchmark against at least two fixtures (the existing
  demo fixture and one larger synthetic one) is recorded in the
  implementation PR showing wall-clock improvement, not just claimed.
- `diagnostics`/`commands` ordering is identical to pre-parallelization
  behavior for the same inputs, proven by a test with artificially reversed
  completion order.
- The overall request timeout bound is unchanged from the maintainer's
  perspective: a hung tool still cannot make a request exceed
  `request_timeout_seconds` by more than the same bounded grace period
  today's sequential per-subprocess timeout already allows.
- `--transport streamable-http` is unusable without
  `AGENT_QUALITY_MCP_HTTP_BEARER_TOKEN` set; this is enforced before any
  socket is opened, not just documented.
- `--transport stdio` (the default, unchanged) behavior is byte-for-byte
  identical to today.
- `inspect_workspace` reports `project_layout` and
  `additional_pyproject_files` without altering any validator's scope,
  required-tool routing, or confidence score for any existing test
  fixture.
- No uv/Ruff capability-metadata revival is added; Part 4's deferral is
  documented, not silently resolved.
- No caching/memoization is added; Part 5's deferral is documented, not
  silently resolved.
- No incremental/hardlink-based shadow workspace copying is added.
- Full verification suite passes unchanged for every existing assertion.

## Self-Review Notes

- Caught and fixed during this review: Part 1's "out of scope" reference
  to a uv/Ruff evidence-richness follow-up, and Phase 3b's cross-reference
  to a Phase 3d "Evidence Richness" section, both pointed at content that
  did not yet exist in this document when first drafted. Added Part 4
  to resolve the question explicitly (and explicitly not build it, for
  the stated reasons) rather than leaving a dangling reference; renumbered
  the original Part 4 (caching) to Part 5 and updated every in-document
  reference and the Scope/Acceptance Criteria bullets to match.
- Consistency: Part 1's safety argument depends on `ruff check --fix --diff`
  never writing to the shadow workspace; this is flagged explicitly as
  "must be re-confirmed with a targeted test before this phase ships," not
  asserted as already proven, because this design only re-read the
  existing implementation's behavior and its own description of itself as
  non-mutating — it did not execute a byte-level before/after test in this
  session. The Testing section requires exactly that test before this part
  can be considered verified, not just designed.
- Scope: every part states explicitly what it does *not* change
  (`decision.py`'s precedence, `risk.py`'s scoring, the timeout bound's
  meaning even though its accounting mechanism changes) so the "more
  powerful" framing cannot be mistaken for license to also change what a
  given input means.
- Ambiguity check: Part 1's timeout-cancellation trade-off (a hung
  subprocess's thread can outlive the future that gave up waiting on it)
  is stated as an explicit, bounded, pre-existing-in-spirit trade-off with
  a named alternative, rather than glossed over — this is the kind of
  detail that is easy to leave implicit in a design and only discover
  during implementation; it is made explicit here on purpose.
