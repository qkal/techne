# Agent Quality MCP Pyright LSP And Validator Capabilities Design

## Purpose

This phase upgrades Agent Quality MCP's Python validation path without changing
the core safety model.

Phase 1 validates proposed changes in shadow workspaces, uses `uv`, Ruff, and
Pyright CLI adapters, and never mutates the real repository. The next useful
upgrade is to replace Pyright CLI as the primary type-diagnostic source with a
reusable Pyright language server, while also giving all validators a common
internal capability/result API.

The goal is a light implementation: richer type diagnostics and clearer
validator metadata, not a generic language-server platform and not
real-repository mutation.

## Scope

This phase includes:

- Pyright LSP as the primary type diagnostic path for `validate_patch`.
- A reusable Pyright LSP manager keyed by real workspace identity.
- Shadow-workspace-only diagnostics for patched requests.
- Mode-aware diagnostic scope:
  - `quick`: changed Python files only.
  - `standard`: full shadow workspace.
  - `strict`: full shadow workspace.
- Fallback to the existing Pyright CLI adapter when LSP is unavailable, fails,
  times out, or returns unusable protocol data.
- A shared internal validator capability/result API for `uv`, Ruff, and Pyright
  LSP.
- Light `uv` metadata around project detection, lock-check behavior, optional
  sync dry-run behavior, and skipped checks.
- Light Ruff metadata around JSON diagnostics, scope, rule codes, fixability,
  and safe-fix diff previews.
- Tests for the LSP protocol layer, manager lifecycle, fallback behavior, mode
  scoping, and validator capability results.
- README updates for the new Pyright LSP path and fallback semantics.

This phase excludes:

- Real-repository mutation.
- Applying safe fixes to the real workspace.
- Unsafe Ruff fixes.
- `uv` environment writes or dependency changes.
- Generic multi-language LSP abstractions.
- LSP completions, hover, code actions, imports, symbol search, or formatting.
- Removing the Pyright CLI adapter.
- A broad redesign of `inspect_workspace`.
- A public `validate_patch` response break unless the approved Phase 2
  decision-contract switch lands first.

## Source Constraints

The design relies on three external command surfaces:

- Pyright ships both a CLI and a language server. The language server is
  controlled through the Language Server Protocol and is started with a
  transport such as `pyright-langserver --stdio`.
- `uv lock --check` validates whether the lockfile is current with project
  metadata. The existing optional `uv sync --locked --dry-run` remains gated by
  trusted server configuration.
- Ruff supports `ruff check --output-format json` for diagnostics and `ruff
  check --fix --diff` for non-mutating safe-fix previews.

The implementation must treat these tools as untrusted subprocesses despite
their allowlisted names. Output remains redacted and truncated before it reaches
responses.

## Recommended Approach

Use a real-workspace-keyed Pyright LSP manager with request-scoped shadow
workspace folders.

The manager owns one reusable Pyright language-server process per resolved real
workspace. A validation request still creates a fresh shadow workspace, applies
the proposed patch there, and asks the manager to open that shadow workspace as
the active LSP workspace folder for the request. After diagnostics are collected
or the request fails, the manager closes the shadow workspace folder and clears
request-local document state.

This approach preserves the existing isolation guarantee while avoiding a fresh
language-server startup for every request. It is more complex than starting a
short-lived process per request, but it matches the product goal: reusable LSP
performance without treating the real repository as the diagnostic target for a
patched validation.

## Architecture

The service remains the orchestrator. It resolves configuration, validates
paths, creates the shadow workspace, applies patches, runs validators, sanitizes
diagnostics, computes risk, and builds the public response.

Internal modules:

- `validators.py`: shared request/result/capability models and provider
  protocol.
- `lsp/protocol.py`: minimal JSON-RPC framing, request IDs, response matching,
  notification parsing, and byte/time limits.
- `lsp/pyright.py`: the consolidated Pyright LSP implementation, including
  `PyrightLspProvider`, `PyrightLspManager`, `RealPyrightLspManager`,
  process-session lifecycle, health checks, workspace-folder open/close,
  document open/close, diagnostic collection, fallback signaling, and diagnostic
  normalization.

The implementation keeps generic byte framing separate in `lsp/protocol.py`,
but intentionally avoids a separate `lsp/manager.py`. The Pyright manager,
provider, and session code share Pyright-specific lifecycle assumptions, so
keeping them together reduces cross-module coordination and keeps the first LSP
implementation easier to audit. If a second language server is added, the
shared seams can be extracted from the working Pyright implementation then.

Existing adapters become provider implementations:

- `UvValidator` wraps the current `UvAdapter`.
- `RuffValidator` wraps the current `RuffAdapter`.
- `PyrightLspValidator` uses the new LSP manager and falls back to
  `PyrightAdapter`.

The protocol layer is intentionally small. It should support only the messages
needed for initialization, lifecycle, workspace-folder changes, document
open/close, diagnostics, shutdown, and exit.

## Command Resolution

`pyright-langserver` must be resolved through the same command-safety model used
for `uv`, Ruff, and Pyright CLI.

Required changes:

- Add `pyright-langserver` to the command allowlist.
- Add a trusted server-side command path field for the language server, for
  example `CommandConfig.pyright_langserver`.
- Optionally support a trusted environment variable such as
  `AGENT_QUALITY_MCP_PYRIGHT_LANGSERVER`.
- Add an explicit command-to-config-field mapping so the public executable name
  `pyright-langserver` maps to the Python field `pyright_langserver`. The
  resolver must not call `getattr(config.command_paths, command)` directly for
  hyphenated command names.
- Continue rejecting command paths supplied by untrusted workspace config or
  request overrides.
- Keep the existing `pyright` CLI command available for fallback.

The service must not infer an executable from a workspace path, `.venv`, package
script, or project configuration owned by the target workspace.

The existing finite `CommandRunner.run` and `run_with_output` APIs are not the
right process boundary for LSP because they close stdin and wait for process
exit. The implementation should add a small long-running process launcher for
LSP that reuses allowlisted command resolution, safe environment construction,
workspace-owned executable rejection, argument-list execution, and timeout
policy. It should expose only binary stdin/stdout/stderr streams needed by the
protocol layer so LSP `Content-Length` framing preserves CRLF and byte counts.
It should still record response-safe lifecycle metadata for execution evidence.

## Validator Capability API

The shared API gives each validator a consistent shape without forcing all tools
to behave the same way.

`ValidatorCapability` describes what a provider can do:

- `dependency_lock_check`
- `dependency_sync_dry_run`
- `lint_diagnostics`
- `safe_fix_preview`
- `type_diagnostics`
- `changed_file_scope`
- `workspace_scope`
- `cli_fallback`
- `lsp_reuse`

`ValidatorRequest` contains:

- `real_workspace_root`
- `shadow_workspace_root`
- `changed_files`
- `mode`
- `safety_mode`
- `requested_scope`
- `timeout_budget_seconds`
- `request_id`
- `config`

`ValidatorResult` contains:

- `provider`
- `capabilities`
- `diagnostics`
- `commands`
- `safe_fixes`
- `metadata`
- `skipped_checks`
- `fallback_reason`
- `duration_ms`
- `timed_out`
- `output_truncated`

`ValidatorProvider` exposes one method:

```python
def validate(request: ValidatorRequest) -> ValidatorResult: ...
```

This is an internal boundary. The public MCP response may keep the current
Phase 1 shape during this phase. If the approved Phase 2 decision contract lands
first, the capability metadata should feed its evidence and execution sections
instead of becoming a separate public response object.

## uv Capability Behavior

The `uv` provider keeps its current read-only behavior.

It runs:

- `uv --version`
- `uv lock --check` when `mode` is `standard` or `strict` and
  `pyproject.toml` exists.
- `uv sync --locked --dry-run` only when trusted configuration enables
  `uv_sync_dry_run`.

It reports metadata:

- `project_detected`
- `pyproject_present`
- `lock_check_requested`
- `lock_check_completed`
- `sync_dry_run_available`
- `sync_dry_run_enabled`
- `sync_dry_run_completed`
- `skipped_reason`

It must not run commands that write dependency state, modify an environment, or
change project metadata.

## Ruff Capability Behavior

The Ruff provider keeps JSON diagnostics and non-mutating safe-fix previews.

It runs:

- `ruff check --no-cache --output-format json` with changed-file scope for
  `quick` and `standard`.
- `ruff check --no-cache --output-format json` with workspace scope for
  `strict`.
- `ruff check --no-cache --fix --diff` only when `preview_safe_fixes` is
  requested.

It reports metadata:

- `scope`
- `scoped_files`
- `json_diagnostics_completed`
- `safe_fix_preview_requested`
- `safe_fix_preview_completed`
- `rule_codes`
- `fixable_rule_codes`
- `skipped_reason`

It must not apply fixes to the real workspace or the shadow workspace. The
existing safe-fix diff validation remains required before any preview is
returned.

## Pyright LSP Behavior

The Pyright LSP provider replaces Pyright CLI as the first diagnostic path.

It starts the language server as:

```text
pyright-langserver --stdio
```

through allowlisted command resolution and argument-list execution.

The manager initializes the server using LSP initialize/initialized messages and
workspace-folder support. The real workspace path is a manager key only: it
must not be sent as `rootUri`, `rootPath`, or an initial workspace folder. The
preferred initialization is no root plus empty workspace folders. If Pyright LSP
requires an initial root, the implementation may use the first request's shadow
workspace only if that root can be removed during cleanup. It must never
initialize the server against the real workspace. If safe initialization is not
possible, the provider falls back to Pyright CLI.

For each validation request, the provider opens the shadow workspace as the
active workspace folder. When changed-file diagnostics are requested, it opens
the changed Python documents from the shadow workspace and collects diagnostics
for those files. When workspace diagnostics are requested, it waits for
workspace diagnostics associated with the shadow workspace until completion or
timeout.

Mode behavior:

- `quick`: changed Python files only.
- `standard`: whole shadow workspace.
- `strict`: whole shadow workspace.

It reports metadata:

- `lsp_reused`
- `lsp_process_started`
- `shadow_workspace_opened`
- `shadow_workspace_closed`
- `diagnostic_scope`
- `documents_opened`
- `diagnostics_completed`
- `fallback_to_cli`
- `fallback_reason`

The provider must not expose completions, hover, code actions, import
organization, formatting, or symbol features in this phase.

LSP `textDocument/publishDiagnostics` payloads are not the same shape as
Pyright CLI `--outputjson` output. The implementation should add a dedicated
normalizer or conversion layer for LSP diagnostics, including `file://` URI
normalization, range conversion, severity mapping, and shadow-root validation.
It should not pass raw LSP notification payloads into the current Pyright CLI
JSON normalizer.

## Concurrency And Diagnostic Ownership

Reusable LSP processes introduce request state. The first implementation should
serialize Pyright LSP validations per real-workspace manager. A later version
may support concurrent leases, but only after diagnostics can be attributed
unambiguously to independent shadow roots.

During a request, the provider must count only diagnostics whose document URI is
inside the active shadow workspace. Diagnostics for previous shadow workspaces,
the real workspace, or unrelated folders must be ignored and should trigger
process discard if they indicate leaked workspace state after cleanup.

If a second validation request arrives for the same real workspace while an LSP
validation is active, it should wait for the manager lock or fall back to the
Pyright CLI path after a bounded wait. It should not open two shadow workspace
folders concurrently in the same Pyright process for this phase.

## Diagnostic Completion

Standard LSP diagnostics arrive as notifications, so "complete" must be defined
by the provider rather than assumed from a single response.

For changed-file scope, completion means each expected changed Python document
has received at least one `publishDiagnostics` notification or the LSP timeout
has expired. An empty diagnostics list for a file is valid only after receiving
that file's notification.

For workspace scope, completion means the server has produced diagnostics for
the active shadow workspace and then remained quiet for a short bounded
stability window, or the LSP timeout has expired. If Pyright exposes progress or
status notifications that reliably indicate analysis completion, the provider
may use them in addition to the quiet window. It must still enforce the timeout.

The implementation must prove that workspace-scope LSP diagnostics cover the
same project surface that `pyright --outputjson` would cover for the shadow
workspace. If Pyright LSP cannot reliably report diagnostics for unopened files
in `standard` or `strict`, the provider must treat the LSP result as incomplete
and use the Pyright CLI fallback for the authoritative workspace-scope result.
It must not silently return changed-file-only or opened-file-only diagnostics
for a workspace-scope request.

Timeout or ambiguous completion should produce a non-blocking LSP warning and
trigger Pyright CLI fallback. The fallback result, not a partial LSP result,
should be treated as the authoritative Pyright diagnostic result for that
request unless the implementation can prove the partial LSP result is complete
for the requested scope.

## Data Flow

`validate_patch` follows this flow:

1. Parse and validate the request.
2. Resolve the real workspace and trusted configuration.
3. Validate changed-file paths and resource limits.
4. Create a shadow workspace.
5. Apply `patch_unified_diff` to the shadow workspace when provided.
6. Build a `ValidatorRequest` with the real workspace root, shadow workspace
   root, changed files, mode, safety mode, timeout budget, request ID, and
   config.
7. Run the `uv` provider.
8. Run the Ruff provider.
9. Run the Pyright LSP provider.
10. If Pyright LSP fails, append a warning diagnostic and run Pyright CLI
    fallback through the existing adapter.
11. Sanitize diagnostics and safe-fix previews against the shadow workspace.
12. Compress diagnostics, compute risk, build suggestions, and return the
    public response.

Validator providers may run sequentially in this phase. Parallel validator
execution is out of scope because shared timeout accounting, LSP lifecycle
cleanup, and deterministic command ordering matter more than throughput for the
light implementation.

## Error Handling

LSP failures degrade to CLI fallback.

Fallback triggers include:

- `pyright-langserver` cannot be resolved.
- Process startup fails.
- Initialization fails.
- Workspace-folder open fails.
- Diagnostic collection times out.
- JSON-RPC framing is invalid.
- A response ID is unknown or duplicated.
- The server exits unexpectedly.
- The protocol layer detects desynchronized state.

The fallback diagnostic should be non-blocking and should identify the failed
LSP phase without returning raw protocol payloads. The existing Pyright CLI
adapter then runs and contributes diagnostics and command records.

After crash, protocol desync, or failed initialization, the manager must discard
the affected process and start a fresh one for the next request. After ordinary
request failure, it should close the shadow workspace folder when possible and
reuse the process only if state is known to be clean.

## Safety Model

This phase preserves the Phase 1 security posture.

Safety requirements:

- Proposed patches are still applied only in shadow workspaces.
- Pyright LSP receives shadow workspace folders, not patched real workspaces.
- Real workspace identity is used only for manager ownership and lifecycle.
- The real workspace path is never sent as an LSP root, workspace folder, or
  opened document URI during patched validation.
- Commands are executed with argument lists and `shell=False`.
- Workspace-owned executables are not trusted.
- LSP messages are parsed with explicit byte limits and timeouts.
- LSP diagnostics are sanitized and path-normalized before response.
- Raw protocol logs are not returned to callers.
- Source contents from the real workspace are not sent after patching.
- Request config and workspace config cannot grant command authority.
- `apply_safe_fixes` remains unsupported.

When documents are opened through LSP, their contents come from the shadow
workspace. Request cleanup must close opened documents and remove the temporary
workspace folder from the server when the server supports it.

## Response Compatibility

The public `validate_patch` response should remain compatible with the current
Phase 1 shape for this phase:

- `status`
- `blocking_errors`
- `warnings`
- `info`
- `safe_fixes`
- `suggested_actions`
- `risk_score`
- `execution`
- `audit`
- `context_summary`

Capability metadata should initially remain internal or appear only in existing
metadata fields where it is already safe and useful. The spec does not require a
public response break.

If the Phase 2 decision-contract switch is implemented before this phase, the
same validator results should feed Phase 2 `evidence`, `execution`,
`blockers`, and `next_actions` rather than preserving the Phase 1 top-level
fields.

## Testing Strategy

Unit tests should cover:

- JSON-RPC framing for complete, partial, multiple, oversized, and malformed
  messages.
- Request ID matching and notification handling.
- LSP process startup with `pyright-langserver --stdio` through allowlisted
  command resolution.
- Explicit command-to-config-field mapping for `pyright-langserver` to
  `pyright_langserver`.
- The LSP process launcher reusing safe command resolution and safe
  environment construction without using finite `subprocess.run` semantics.
- Initialization never sending the real workspace as `rootUri`, `rootPath`, or
  a workspace folder.
- Manager reuse for the same real workspace.
- Manager isolation across different real workspaces.
- Per-real-workspace LSP request serialization or bounded fallback when the
  manager is already busy.
- Shadow workspace open/close per request.
- Opened document cleanup per request.
- Ignoring or rejecting diagnostics outside the active shadow workspace.
- Changed-file completion requiring diagnostics notifications for all expected
  Python files.
- Workspace-scope completion using progress/status signals when available plus
  a bounded quiet window and hard timeout.
- Workspace-scope LSP coverage proving unopened files are included, otherwise
  falling back to Pyright CLI.
- `quick` changed-file diagnostic scope.
- `standard` workspace diagnostic scope.
- `strict` workspace diagnostic scope.
- Diagnostic normalization from LSP `publishDiagnostics`.
- LSP timeout fallback to Pyright CLI.
- Initialization failure fallback to Pyright CLI.
- Crash fallback to Pyright CLI and process discard.
- Invalid JSON-RPC fallback to Pyright CLI and process discard.
- `uv` capability metadata for project detected, lock check skipped, lock check
  completed, and sync dry-run gated.
- Ruff capability metadata for scope, rule codes, fixability, and safe-fix
  preview completion.
- Existing redaction and shadow-path sanitization still applying to LSP
  diagnostics and fallback warnings.

Integration tests should cover:

- Existing demo validation remains green.
- A fake Pyright LSP process emits deterministic diagnostics over stdio and is
  consumed by the provider.
- Fallback path works when the fake LSP exits early.
- Optional local smoke coverage can exercise real `pyright-langserver`, but CI
  should not depend on timing-sensitive real-language-server behavior until the
  fake-process coverage has proven stable.

Repository verification remains:

```bash
.venv/bin/python -m pytest -v
.venv/bin/ruff check .
.venv/bin/pyright --pythonpath .venv/bin/python
git diff --check
```

## Documentation Updates

The README should be updated to explain:

- Pyright LSP is the primary type diagnostic path.
- Pyright CLI remains the fallback path.
- `quick` uses changed-file diagnostics.
- `standard` and `strict` use whole-shadow-workspace diagnostics.
- `pyright-langserver` must be installed and resolvable through the trusted
  command model.
- The upgrade does not mutate the real repository.

`inspect_workspace` may add command availability for `pyright-langserver` if it
can do so without changing existing fields incompatibly. A broad redesign of
inspection output is not part of this phase.

## Implementation Notes

The implementation plan should keep the work staged:

1. Add shared validator result/capability models and migrate `uv` and Ruff into
   the internal provider shape without changing behavior.
2. Add the LSP protocol parser and fake-process tests.
3. Add command resolution and inspection support for `pyright-langserver`.
4. Add the Pyright LSP manager and provider with fake-LSP tests.
5. Wire the service to use the new provider path with CLI fallback.
6. Update README and representative tests.
7. Run the full repository verification suite.

The key implementation risk is process lifecycle state. The plan should include
explicit cleanup checks before service wiring so failed LSP requests cannot
poison later validations.
