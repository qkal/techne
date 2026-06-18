# Agent Quality MCP

Agent Quality MCP is a Python 3.12+ MCP server for validating Python workspace
changes in temporary shadow copies. It accepts proposed file changes, applies
text unified diffs away from the real repository, runs a small quality toolchain,
and returns structured diagnostics for agents before they mutate production
workspace state.

Real repository files are not modified by default. `validate_patch` reports
`real_workspace_modified: false`, uses an isolated shadow workspace, and rejects
the currently unsupported `apply_safe_fixes` safety mode.

## Security Model

- The real workspace is read-only for validation. Proposed patches are applied
  only inside a temporary shadow workspace.
- Workspace paths and patch targets must be relative, normalized, and remain
  inside the workspace. Path traversal, absolute paths, drive prefixes, null
  bytes, symlink targets, hard-link targets, and target collisions are rejected.
- The patch parser supports a conservative text unified-diff subset. Binary
  patch data, malformed hunks, renames, copies, Git file mode changes, and other
  advanced patch headers are rejected.
- `apply_safe_fixes` is rejected. `preview_safe_fixes` may return proposed fix
  previews without mutating the real repository.
- Subprocesses are restricted to an allowlist of `uv`, `ruff`, and `pyright`.
  Commands are invoked with argument lists and `shell=False`.
- Executables are resolved from safe absolute paths outside the workspace.
  Workspace-owned executables are excluded from command resolution.
- Subprocesses run with a minimal environment: safe `PATH`, locale variables,
  `UV_NO_ENV_FILE=1`, `UV_NO_PROGRESS=1`, and `UV_OFFLINE=1` by default.
- Command output is redacted for common secret patterns and configured literal
  redaction tokens, then truncated to configured byte limits. Audit summaries
  are redacted before they are returned.
- Workspace copying excludes common build/cache directories and configured
  secret file patterns.

## Setup

Install dependencies with uv:

```bash
uv sync --extra dev
```

This creates the local virtual environment used by the repository. The package
requires Python 3.12 or newer.

## Start The MCP Server

Run the stdio MCP server through uv:

```bash
uv run agent-quality-mcp
```

Or run the installed console script from the synced virtual environment:

```bash
.venv/bin/agent-quality-mcp
```

The server registers two tools: `validate_patch` and `inspect_workspace`.

## Tools

### `validate_patch`

Validates a proposed patch without modifying the real repository.

Inputs:

- `workspace_root`: existing workspace directory.
- `changed_files`: non-empty list of relative file paths the patch is allowed
  to touch.
- `patch_unified_diff`: optional text unified diff. When present, patch targets
  must exactly match `changed_files`.
- `mode`: `quick`, `standard`, or `strict`; defaults to `standard`.
- `safety_mode`: `read_only`, `preview_safe_fixes`, or `apply_safe_fixes`.
  `apply_safe_fixes` is rejected.
- `request_id`: optional caller-provided request identifier.
- `config_overrides`: optional safe overrides such as `default_mode`,
  `default_safety_mode`, `uv_offline`, `workspace_exclusions`,
  `secret_file_patterns`, and `secret_redaction_patterns`.

Example request:

```json
{
  "workspace_root": "/path/to/python-project",
  "changed_files": ["src/example.py"],
  "patch_unified_diff": "--- a/src/example.py\n+++ b/src/example.py\n@@ -1,2 +1,2 @@\n def value():\n-    return 1\n+    return 2\n",
  "mode": "quick",
  "safety_mode": "preview_safe_fixes",
  "request_id": "demo-1"
}
```

Example response excerpt:

```json
{
  "request_id": "demo-1",
  "status": "passed",
  "workspace_root": "/path/to/python-project",
  "mode": "quick",
  "safety_mode": "preview_safe_fixes",
  "real_workspace_modified": false,
  "shadow_workspace_used": true,
  "blocking_errors": [],
  "warnings": [],
  "risk_score": {
    "score": 0,
    "level": "low",
    "factors": []
  },
  "execution": {
    "commands": [
      {
        "command": "ruff",
        "args": ["ruff", "check", "--no-cache", "--output-format", "json", "--", "src/example.py"],
        "cwd": "/tmp/agent-quality-mcp-.../workspace",
        "exit_code": 0,
        "stdout_truncated": false,
        "stderr_truncated": false
      }
    ],
    "output_truncated": false
  }
}
```

### `inspect_workspace`

Returns safe workspace metadata without reading or returning source contents.

Inputs:

- `workspace_root`: existing workspace directory.
- `config_overrides`: optional safe configuration overrides.

The response includes command availability, resolved safe command paths, default
limits, Python file counts, discovered config file names, excluded directory
summaries, and security decisions. Source contents are not included. Config
string lists that may expose sensitive local details are sanitized where
relevant, for example as `<workspace_exclusions:count=N>` or
`<secret_file_patterns:count=N>`, and `secret_redaction_patterns` is returned
as an empty list.

## Tests And Checks

Use the repository virtual environment for verification:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m pytest tests/integration/test_validate_patch_demo.py -v
.venv/bin/ruff check .
.venv/bin/pyright --pythonpath .venv/bin/python
git diff --check
```

For a narrower unit-only pass:

```bash
.venv/bin/python -m pytest tests/unit -v
```

## Configuration

Workspace configuration is read from `[tool.agent_quality_mcp]` in
`pyproject.toml`, then combined with validated request overrides. Untrusted
configuration can only set fields that do not expand authority or resource use.
`uv_offline=false`, command path overrides, timeout increases, workspace
preservation, and other authority-expanding settings are rejected from untrusted
workspace config or request overrides.

## MVP Limitations

- Text unified-diff subset only.
- Stdio transport only.
- Minimal uv, Ruff, and Pyright adapters.
- No real-repository mutation.
- No LSP integration.
- No advanced patch formats such as binary patches, renames, copies, or mode
  changes.
- No production-grade safe-fix grouping.
