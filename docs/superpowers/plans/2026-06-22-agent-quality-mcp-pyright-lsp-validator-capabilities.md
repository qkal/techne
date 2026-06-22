# Agent Quality MCP Pyright LSP Validator Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight validator capability layer and make reusable Pyright LSP the primary type-diagnostic path, while preserving shadow-workspace isolation and Pyright CLI fallback.

**Architecture:** Keep `validate_patch` service-led and keep the public response compatible with the current Phase 1 shape. Add internal validator result models, wrap `uv` and Ruff without broad behavior changes, then add a small LSP transport/protocol layer and a Pyright-specific provider that diagnoses shadow workspaces only. Use CLI fallback whenever LSP initialization, completion, coverage, or process state is uncertain.

**Tech Stack:** Python 3.12+, Pydantic v2, pytest, FastMCP, stdio JSON-RPC/LSP, existing uv/Ruff/Pyright CLI adapters, existing shadow workspace and diagnostic infrastructure.

---

## Source Spec

Use this spec as the implementation contract:

- `docs/superpowers/specs/2026-06-22-agent-quality-mcp-pyright-lsp-validator-capabilities-design.md`

## Scope Check

This plan covers one subsystem: Python validator execution for `validate_patch`. It adds shared internal validator capability/result models, light `uv` and Ruff metadata, and Pyright LSP with CLI fallback. It does not add real-repository mutation, generic multi-language LSP support, LSP completions/hover/actions, unsafe Ruff fixes, `uv` environment writes, or a public response break.

## File Structure

- Create: `src/agent_quality_mcp/validators.py`
  - Internal validator request, result, capability, and skipped-check models plus lightweight wrapper providers for `uv` and Ruff.
- Modify: `src/agent_quality_mcp/cli/runner.py`
  - Add explicit command-to-config-field mapping, allow `pyright-langserver`, expose safe process environment creation, and add a long-running process launcher for LSP.
- Modify: `src/agent_quality_mcp/models.py`
  - Add `CommandConfig.pyright_langserver`.
- Modify: `src/agent_quality_mcp/config.py`
  - Add `AGENT_QUALITY_MCP_PYRIGHT_LANGSERVER` as trusted server-admin config.
- Create: `src/agent_quality_mcp/lsp/__init__.py`
  - Package marker for LSP internals.
- Create: `src/agent_quality_mcp/lsp/protocol.py`
  - Minimal JSON-RPC/LSP message framing, encoding, parsing, response matching helpers, and protocol errors.
- Create: `src/agent_quality_mcp/lsp/pyright.py`
  - Pyright LSP diagnostic conversion, manager, provider, lifecycle cleanup, completion rules, and CLI fallback.
- Modify: `src/agent_quality_mcp/service.py`
  - Replace direct adapter calls with validator providers, preserve response shape, include provider command records and safe-fix previews.
- Modify: `README.md`
  - Document Pyright LSP primary path, CLI fallback, mode scopes, and trusted command path.
- Create: `tests/unit/test_validators.py`
- Modify: `tests/unit/test_runner.py`
- Create: `tests/unit/test_lsp_protocol.py`
- Create: `tests/unit/test_pyright_lsp.py`
- Modify: `tests/unit/test_service.py`
- Modify: `tests/unit/test_cli_adapters.py`
- Modify: `tests/integration/test_validate_patch_demo.py`

## Task 1: Validator Capability Models And uv/Ruff Wrappers

**Files:**
- Create: `src/agent_quality_mcp/validators.py`
- Create: `tests/unit/test_validators.py`

- [ ] **Step 1: Write failing validator API tests**

Create `tests/unit/test_validators.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_quality_mcp.models import (
    AgentQualityConfig,
    CommandExecutionRecord,
    SafeFixPreview,
    SafetyMode,
    ValidationMode,
)
from agent_quality_mcp.validators import (
    ValidatorCapability,
    ValidatorRequest,
    ValidatorScope,
    wrap_ruff_result,
    wrap_uv_result,
)


def _request(tmp_path: Path, *, mode: ValidationMode = ValidationMode.STANDARD) -> ValidatorRequest:
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    return ValidatorRequest(
        real_workspace_root=tmp_path,
        shadow_workspace_root=shadow,
        changed_files=[Path("pkg/app.py")],
        mode=mode,
        safety_mode=SafetyMode.READ_ONLY,
        requested_scope=ValidatorScope.CHANGED_FILES,
        timeout_budget_seconds=30.0,
        request_id="req-1",
        config=AgentQualityConfig(),
    )


def _record(command: str, args: list[str], cwd: Path, *, exit_code: int = 0) -> CommandExecutionRecord:
    return CommandExecutionRecord(
        command=command,
        args=[command, *args],
        cwd=str(cwd),
        duration_ms=7,
        exit_code=exit_code,
    )


def test_validator_request_keeps_real_and_shadow_roots_separate(tmp_path: Path) -> None:
    request = _request(tmp_path)

    assert request.real_workspace_root == tmp_path
    assert request.shadow_workspace_root == tmp_path / "shadow"
    assert request.real_workspace_root != request.shadow_workspace_root
    assert request.requested_scope is ValidatorScope.CHANGED_FILES


def test_wrap_uv_result_reports_project_and_lock_metadata(tmp_path: Path) -> None:
    request = _request(tmp_path, mode=ValidationMode.STRICT)
    records = [
        _record("uv", ["--version"], request.shadow_workspace_root),
        _record("uv", ["lock", "--check"], request.shadow_workspace_root),
    ]

    result = wrap_uv_result(
        request=request,
        diagnostics=[],
        records=records,
        project_detected=True,
        lock_check_requested=True,
        lock_check_completed=True,
        sync_dry_run_available=True,
        sync_dry_run_enabled=False,
        sync_dry_run_completed=False,
        skipped_reason=None,
        duration_ms=12,
    )

    assert result.provider == "uv"
    assert ValidatorCapability.DEPENDENCY_LOCK_CHECK in result.capabilities
    assert result.commands == records
    assert result.metadata["project_detected"] is True
    assert result.metadata["lock_check_completed"] is True
    assert result.skipped_checks == []


def test_wrap_uv_result_records_skipped_lock_check(tmp_path: Path) -> None:
    request = _request(tmp_path, mode=ValidationMode.STANDARD)

    result = wrap_uv_result(
        request=request,
        diagnostics=[],
        records=[_record("uv", ["--version"], request.shadow_workspace_root)],
        project_detected=False,
        lock_check_requested=False,
        lock_check_completed=False,
        sync_dry_run_available=False,
        sync_dry_run_enabled=False,
        sync_dry_run_completed=False,
        skipped_reason="pyproject.toml not present",
        duration_ms=4,
    )

    assert result.metadata["pyproject_present"] is False
    assert result.skipped_checks[0].provider == "uv"
    assert result.skipped_checks[0].reason == "pyproject.toml not present"


def test_wrap_ruff_result_reports_scope_rule_codes_and_safe_fix_preview(tmp_path: Path) -> None:
    request = _request(tmp_path)
    preview = SafeFixPreview(
        tool="ruff",
        description="Ruff safe-fix diff preview",
        files=["pkg/app.py"],
        diff_preview="--- pkg/app.py\n+++ pkg/app.py\n",
        is_safe=True,
        requires_human_review=True,
    )

    result = wrap_ruff_result(
        request=request,
        diagnostics=[],
        records=[_record("ruff", ["check", "--output-format", "json"], request.shadow_workspace_root)],
        safe_fixes=[preview],
        scope=ValidatorScope.CHANGED_FILES,
        scoped_files=["pkg/app.py"],
        rule_codes=["F401"],
        fixable_rule_codes=["F401"],
        safe_fix_preview_requested=True,
        safe_fix_preview_completed=True,
        skipped_reason=None,
        duration_ms=9,
    )

    assert result.provider == "ruff"
    assert ValidatorCapability.LINT_DIAGNOSTICS in result.capabilities
    assert ValidatorCapability.SAFE_FIX_PREVIEW in result.capabilities
    assert result.safe_fixes == [preview]
    assert result.metadata["scope"] == "changed_files"
    assert result.metadata["rule_codes"] == ["F401"]
    assert result.metadata["fixable_rule_codes"] == ["F401"]
```

- [ ] **Step 2: Run validator tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_validators.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_quality_mcp.validators'`.

- [ ] **Step 3: Implement validator models and result wrappers**

Create `src/agent_quality_mcp/validators.py`:

```python
"""Internal validator capability and result models."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import Field

from agent_quality_mcp.models import (
    AgentQualityBaseModel,
    AgentQualityConfig,
    CommandExecutionRecord,
    Diagnostic,
    SafeFixPreview,
    SafetyMode,
    ValidationMode,
)


class ValidatorCapability(StrEnum):
    DEPENDENCY_LOCK_CHECK = "dependency_lock_check"
    DEPENDENCY_SYNC_DRY_RUN = "dependency_sync_dry_run"
    LINT_DIAGNOSTICS = "lint_diagnostics"
    SAFE_FIX_PREVIEW = "safe_fix_preview"
    TYPE_DIAGNOSTICS = "type_diagnostics"
    CHANGED_FILE_SCOPE = "changed_file_scope"
    WORKSPACE_SCOPE = "workspace_scope"
    CLI_FALLBACK = "cli_fallback"
    LSP_REUSE = "lsp_reuse"


class ValidatorScope(StrEnum):
    CHANGED_FILES = "changed_files"
    WORKSPACE = "workspace"


class SkippedCheck(AgentQualityBaseModel):
    provider: str
    capability: ValidatorCapability
    reason: str


class ValidatorRequest(AgentQualityBaseModel):
    real_workspace_root: Path
    shadow_workspace_root: Path
    changed_files: list[Path]
    mode: ValidationMode
    safety_mode: SafetyMode
    requested_scope: ValidatorScope
    timeout_budget_seconds: float = Field(gt=0)
    request_id: str
    config: AgentQualityConfig


class ValidatorResult(AgentQualityBaseModel):
    provider: str
    capabilities: list[ValidatorCapability] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    commands: list[CommandExecutionRecord] = Field(default_factory=list)
    safe_fixes: list[SafeFixPreview] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    skipped_checks: list[SkippedCheck] = Field(default_factory=list)
    fallback_reason: str | None = None
    duration_ms: int = 0
    timed_out: bool = False
    output_truncated: bool = False


class ValidatorProvider(Protocol):
    def validate(self, request: ValidatorRequest) -> ValidatorResult:
        """Run validation and return normalized internal facts."""


def wrap_uv_result(
    *,
    request: ValidatorRequest,
    diagnostics: list[Diagnostic],
    records: list[CommandExecutionRecord],
    project_detected: bool,
    lock_check_requested: bool,
    lock_check_completed: bool,
    sync_dry_run_available: bool,
    sync_dry_run_enabled: bool,
    sync_dry_run_completed: bool,
    skipped_reason: str | None,
    duration_ms: int,
) -> ValidatorResult:
    capabilities = [ValidatorCapability.DEPENDENCY_LOCK_CHECK]
    if sync_dry_run_available:
        capabilities.append(ValidatorCapability.DEPENDENCY_SYNC_DRY_RUN)
    skipped_checks: list[SkippedCheck] = []
    if skipped_reason is not None:
        skipped_checks.append(
            SkippedCheck(
                provider="uv",
                capability=ValidatorCapability.DEPENDENCY_LOCK_CHECK,
                reason=skipped_reason,
            )
        )
    return ValidatorResult(
        provider="uv",
        capabilities=capabilities,
        diagnostics=diagnostics,
        commands=records,
        metadata={
            "project_detected": project_detected,
            "pyproject_present": project_detected,
            "lock_check_requested": lock_check_requested,
            "lock_check_completed": lock_check_completed,
            "sync_dry_run_available": sync_dry_run_available,
            "sync_dry_run_enabled": sync_dry_run_enabled,
            "sync_dry_run_completed": sync_dry_run_completed,
            "skipped_reason": skipped_reason,
            "mode": request.mode.value,
        },
        skipped_checks=skipped_checks,
        duration_ms=duration_ms,
        timed_out=any(record.timed_out for record in records),
        output_truncated=any(record.stdout_truncated or record.stderr_truncated for record in records),
    )


def wrap_ruff_result(
    *,
    request: ValidatorRequest,
    diagnostics: list[Diagnostic],
    records: list[CommandExecutionRecord],
    safe_fixes: list[SafeFixPreview],
    scope: ValidatorScope,
    scoped_files: list[str],
    rule_codes: list[str],
    fixable_rule_codes: list[str],
    safe_fix_preview_requested: bool,
    safe_fix_preview_completed: bool,
    skipped_reason: str | None,
    duration_ms: int,
) -> ValidatorResult:
    capabilities = [ValidatorCapability.LINT_DIAGNOSTICS]
    capabilities.append(
        ValidatorCapability.WORKSPACE_SCOPE
        if scope is ValidatorScope.WORKSPACE
        else ValidatorCapability.CHANGED_FILE_SCOPE
    )
    if safe_fix_preview_requested:
        capabilities.append(ValidatorCapability.SAFE_FIX_PREVIEW)
    skipped_checks: list[SkippedCheck] = []
    if skipped_reason is not None:
        skipped_checks.append(
            SkippedCheck(
                provider="ruff",
                capability=ValidatorCapability.LINT_DIAGNOSTICS,
                reason=skipped_reason,
            )
        )
    return ValidatorResult(
        provider="ruff",
        capabilities=capabilities,
        diagnostics=diagnostics,
        commands=records,
        safe_fixes=safe_fixes,
        metadata={
            "scope": scope.value,
            "scoped_files": scoped_files,
            "json_diagnostics_completed": bool(records) and not any(record.timed_out for record in records),
            "safe_fix_preview_requested": safe_fix_preview_requested,
            "safe_fix_preview_completed": safe_fix_preview_completed,
            "rule_codes": rule_codes,
            "fixable_rule_codes": fixable_rule_codes,
            "skipped_reason": skipped_reason,
            "mode": request.mode.value,
        },
        skipped_checks=skipped_checks,
        duration_ms=duration_ms,
        timed_out=any(record.timed_out for record in records),
        output_truncated=any(record.stdout_truncated or record.stderr_truncated for record in records),
    )
```

- [ ] **Step 4: Run validator tests and verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_validators.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit validator models**

Run:

```bash
git add src/agent_quality_mcp/validators.py tests/unit/test_validators.py
git commit -m "feat: add validator capability models"
```

## Task 2: Command Resolution And Long-Running Process Launcher

**Files:**
- Modify: `src/agent_quality_mcp/models.py`
- Modify: `src/agent_quality_mcp/config.py`
- Modify: `src/agent_quality_mcp/cli/runner.py`
- Modify: `tests/unit/test_runner.py`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Add failing command resolution tests**

Append to `tests/unit/test_runner.py`:

```python
def test_resolve_allowed_command_supports_pyright_langserver_configured_path(
    tmp_path: Path,
) -> None:
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    executable = tool_dir / "pyright-langserver"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentQualityConfig(
        command_paths=CommandConfig(pyright_langserver=str(executable))
    )

    resolved = resolve_allowed_command("pyright-langserver", config, cwd=workspace)

    assert resolved == str(executable.resolve())


def test_resolve_allowed_command_rejects_workspace_owned_pyright_langserver(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    tool_dir = workspace / "bin"
    tool_dir.mkdir(parents=True)
    executable = tool_dir / "pyright-langserver"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    config = AgentQualityConfig(
        command_paths=CommandConfig(pyright_langserver=str(executable))
    )

    with pytest.raises(SecurityError, match="must not be inside the workspace"):
        resolve_allowed_command("pyright-langserver", config, cwd=workspace)


def test_start_long_running_command_uses_allowlist_and_safe_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    executable = tool_dir / "pyright-langserver"
    executable.write_text("#!/bin/sh\ncat\n", encoding="utf-8")
    executable.chmod(0o700)
    config = AgentQualityConfig(
        command_paths=CommandConfig(pyright_langserver=str(executable))
    )
    captured: dict[str, object] = {}

    class FakePopen:
        stdin = object()
        stdout = object()
        stderr = object()
        pid = 123

        def __init__(self, args: list[str], **kwargs: object) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

        def poll(self) -> None:
            return None

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    process = start_long_running_command(
        "pyright-langserver",
        ["--stdio"],
        cwd=workspace,
        config=config,
    )

    assert process.command == "pyright-langserver"
    assert process.args == ["pyright-langserver", "--stdio"]
    assert captured["args"] == [str(executable.resolve()), "--stdio"]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == str(workspace)
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert "UV_NO_ENV_FILE" in env
```

Add required imports near the top of `tests/unit/test_runner.py` if absent:

```python
import subprocess

import pytest

from agent_quality_mcp.cli.runner import start_long_running_command
from agent_quality_mcp.models import CommandConfig
```

- [ ] **Step 2: Add failing trusted environment config test**

Append to `tests/unit/test_config.py`:

```python
def test_load_config_accepts_trusted_pyright_langserver_env_path(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    executable = tmp_path / "pyright-langserver"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_QUALITY_MCP_PYRIGHT_LANGSERVER", str(executable))

    config = load_config(tmp_path)

    assert config.command_paths.pyright_langserver == str(executable)
```

- [ ] **Step 3: Run targeted tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_runner.py::test_resolve_allowed_command_supports_pyright_langserver_configured_path tests/unit/test_runner.py::test_resolve_allowed_command_rejects_workspace_owned_pyright_langserver tests/unit/test_runner.py::test_start_long_running_command_uses_allowlist_and_safe_environment tests/unit/test_config.py::test_load_config_accepts_trusted_pyright_langserver_env_path -v
```

Expected: FAIL because `CommandConfig.pyright_langserver` and `start_long_running_command` do not exist.

- [ ] **Step 4: Add command config field and trusted env var**

In `src/agent_quality_mcp/models.py`, update `CommandConfig`:

```python
class CommandConfig(AgentQualityBaseModel):
    """Configured command paths for supported quality tools."""

    uv: str | None = None
    ruff: str | None = None
    pyright: str | None = None
    pyright_langserver: str | None = None
```

In `src/agent_quality_mcp/config.py`, update `TRUSTED_COMMAND_PATH_ENV_VARS`:

```python
TRUSTED_COMMAND_PATH_ENV_VARS = {
    "uv": "AGENT_QUALITY_MCP_UV",
    "ruff": "AGENT_QUALITY_MCP_RUFF",
    "pyright": "AGENT_QUALITY_MCP_PYRIGHT",
    "pyright_langserver": "AGENT_QUALITY_MCP_PYRIGHT_LANGSERVER",
}
```

- [ ] **Step 5: Update runner allowlist and command field mapping**

In `src/agent_quality_mcp/cli/runner.py`, replace `ALLOWED_COMMANDS` and configured path lookup with:

```python
ALLOWED_COMMANDS = {"uv", "ruff", "pyright", "pyright-langserver"}
COMMAND_CONFIG_FIELDS = {
    "uv": "uv",
    "ruff": "ruff",
    "pyright": "pyright",
    "pyright-langserver": "pyright_langserver",
}
```

Inside `resolve_allowed_command`, replace:

```python
configured_path = getattr(config.command_paths, command)
```

with:

```python
configured_path = getattr(config.command_paths, COMMAND_CONFIG_FIELDS[command])
```

- [ ] **Step 6: Add long-running process launcher**

In `src/agent_quality_mcp/cli/runner.py`, add this dataclass near `CommandRunResult`:

```python
@dataclass(frozen=True)
class LongRunningCommand:
    """Long-running allowlisted process used by streaming protocols."""

    command: str
    args: list[str]
    cwd: str
    process: subprocess.Popen[bytes]
    started_at: float
```

Add this function after `CommandRunner`:

```python
def start_long_running_command(
    command: str,
    args: list[str],
    cwd: Path,
    config: AgentQualityConfig,
) -> LongRunningCommand:
    """Start an allowlisted long-running command with safe env and pipes."""

    try:
        executable = resolve_allowed_command(command, config, cwd)
    except SecurityError as exc:
        raise CommandExecutionError(str(exc)) from exc
    safe_env = _safe_environment(config, cwd)
    started_at = time.monotonic()
    try:
        process = subprocess.Popen(  # noqa: S603 - executable is allowlist-resolved.
            [executable, *args],
            cwd=str(cwd),
            env=safe_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise ToolUnavailableError(f"Unable to execute required tool: {command}") from exc
    except OSError as exc:
        raise ToolUnavailableError(f"Unable to execute required tool {command}: {exc}") from exc

    return LongRunningCommand(
        command=command,
        args=[command, *args],
        cwd=str(cwd),
        process=process,
        started_at=started_at,
    )
```

- [ ] **Step 7: Run targeted command tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_runner.py::test_resolve_allowed_command_supports_pyright_langserver_configured_path tests/unit/test_runner.py::test_resolve_allowed_command_rejects_workspace_owned_pyright_langserver tests/unit/test_runner.py::test_start_long_running_command_uses_allowlist_and_safe_environment tests/unit/test_config.py::test_load_config_accepts_trusted_pyright_langserver_env_path -v
```

Expected: PASS.

- [ ] **Step 8: Run broader runner/config tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_runner.py tests/unit/test_config.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit command support**

Run:

```bash
git add src/agent_quality_mcp/models.py src/agent_quality_mcp/config.py src/agent_quality_mcp/cli/runner.py tests/unit/test_runner.py tests/unit/test_config.py
git commit -m "feat: allow pyright language server command"
```

## Task 3: LSP Protocol Framing

**Files:**
- Create: `src/agent_quality_mcp/lsp/__init__.py`
- Create: `src/agent_quality_mcp/lsp/protocol.py`
- Create: `tests/unit/test_lsp_protocol.py`

- [ ] **Step 1: Write failing protocol tests**

Create `tests/unit/test_lsp_protocol.py`:

```python
from __future__ import annotations

import pytest

from agent_quality_mcp.lsp.protocol import (
    LspFramer,
    LspProtocolError,
    build_lsp_message,
)


def test_build_lsp_message_adds_content_length_header() -> None:
    payload = build_lsp_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert payload.startswith(b"Content-Length: ")
    assert b"\r\n\r\n" in payload
    assert payload.endswith(b'{"jsonrpc":"2.0","id":1,"method":"initialize"}')


def test_framer_waits_for_complete_message() -> None:
    payload = build_lsp_message({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})
    framer = LspFramer(max_message_bytes=1024)

    assert framer.feed(payload[:10]) == []
    messages = framer.feed(payload[10:])

    assert messages == [{"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}]


def test_framer_parses_multiple_messages() -> None:
    first = build_lsp_message({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    second = build_lsp_message({"jsonrpc": "2.0", "id": 2, "result": None})
    framer = LspFramer(max_message_bytes=1024)

    messages = framer.feed(first + second)

    assert [message.get("id") for message in messages] == [None, 2]


def test_framer_rejects_oversized_message() -> None:
    payload = build_lsp_message({"jsonrpc": "2.0", "id": 1, "result": "x" * 64})
    framer = LspFramer(max_message_bytes=16)

    with pytest.raises(LspProtocolError, match="exceeds maximum"):
        framer.feed(payload)


def test_framer_rejects_malformed_json() -> None:
    payload = b"Content-Length: 7\r\n\r\n{broken"
    framer = LspFramer(max_message_bytes=1024)

    with pytest.raises(LspProtocolError, match="invalid JSON"):
        framer.feed(payload)
```

- [ ] **Step 2: Run protocol tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_lsp_protocol.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_quality_mcp.lsp'`.

- [ ] **Step 3: Create LSP package marker**

Create `src/agent_quality_mcp/lsp/__init__.py`:

```python
"""Language Server Protocol internals for Agent Quality MCP."""
```

- [ ] **Step 4: Implement protocol framing**

Create `src/agent_quality_mcp/lsp/protocol.py`:

```python
"""Minimal JSON-RPC framing helpers for stdio LSP."""

from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any


class LspProtocolError(RuntimeError):
    """Raised when LSP framing or JSON-RPC payloads are invalid."""


def build_lsp_message(message: dict[str, Any]) -> bytes:
    """Serialize one JSON-RPC message with an LSP Content-Length header."""

    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


class LspFramer:
    """Incrementally parse LSP Content-Length framed messages."""

    def __init__(self, *, max_message_bytes: int) -> None:
        self.max_message_bytes = max_message_bytes
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer.extend(data)
        messages: list[dict[str, Any]] = []
        while True:
            header_end = self._buffer.find(b"\r\n\r\n")
            if header_end == -1:
                return messages
            headers = bytes(self._buffer[:header_end]).decode("ascii", errors="replace")
            content_length = self._content_length(headers)
            if content_length > self.max_message_bytes:
                raise LspProtocolError(
                    f"LSP message length {content_length} exceeds maximum {self.max_message_bytes}"
                )
            message_start = header_end + 4
            message_end = message_start + content_length
            if len(self._buffer) < message_end:
                return messages
            raw_body = bytes(self._buffer[message_start:message_end])
            del self._buffer[:message_end]
            try:
                decoded = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, JSONDecodeError) as exc:
                raise LspProtocolError(f"LSP message contains invalid JSON: {exc}") from exc
            if not isinstance(decoded, dict):
                raise LspProtocolError("LSP message must be a JSON object")
            messages.append(decoded)

    @staticmethod
    def _content_length(headers: str) -> int:
        for line in headers.split("\r\n"):
            name, separator, value = line.partition(":")
            if separator and name.lower() == "content-length":
                try:
                    length = int(value.strip())
                except ValueError as exc:
                    raise LspProtocolError("LSP Content-Length is not an integer") from exc
                if length < 0:
                    raise LspProtocolError("LSP Content-Length must not be negative")
                return length
        raise LspProtocolError("LSP message missing Content-Length header")
```

- [ ] **Step 5: Run protocol tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_lsp_protocol.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit protocol layer**

Run:

```bash
git add src/agent_quality_mcp/lsp/__init__.py src/agent_quality_mcp/lsp/protocol.py tests/unit/test_lsp_protocol.py
git commit -m "feat: add lsp protocol framing"
```

## Task 4: Pyright LSP Diagnostic Conversion

**Files:**
- Create: `src/agent_quality_mcp/lsp/pyright.py`
- Create: `tests/unit/test_pyright_lsp.py`

- [ ] **Step 1: Write failing LSP diagnostic conversion tests**

Create `tests/unit/test_pyright_lsp.py`:

```python
from __future__ import annotations

from pathlib import Path

from agent_quality_mcp.lsp.pyright import (
    lsp_uri_from_path,
    normalize_lsp_diagnostics,
    path_from_lsp_uri,
)
from agent_quality_mcp.models import DiagnosticSeverity


def test_lsp_uri_round_trips_path(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "app.py"
    source.parent.mkdir()
    source.write_text("x = 1\n", encoding="utf-8")

    uri = lsp_uri_from_path(source)

    assert uri.startswith("file://")
    assert path_from_lsp_uri(uri) == source.resolve()


def test_normalize_lsp_diagnostics_maps_file_range_and_severity(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    source = shadow / "pkg" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("x: str = 1\n", encoding="utf-8")
    payload = [
        {
            "range": {
                "start": {"line": 0, "character": 3},
                "end": {"line": 0, "character": 6},
            },
            "severity": 1,
            "code": "reportAssignmentType",
            "source": "pyright",
            "message": "Type error",
        }
    ]

    diagnostics = normalize_lsp_diagnostics(
        uri=lsp_uri_from_path(source),
        raw_diagnostics=payload,
        shadow_root=shadow,
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "pyright"
    assert diagnostic.code == "reportAssignmentType"
    assert diagnostic.message == "Type error"
    assert diagnostic.file == "pkg/app.py"
    assert diagnostic.severity == DiagnosticSeverity.ERROR
    assert diagnostic.is_blocking is True
    assert diagnostic.range is not None
    assert diagnostic.range.start_line == 1
    assert diagnostic.range.start_column == 4


def test_normalize_lsp_diagnostics_rejects_uri_outside_shadow_root(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    outside = tmp_path / "outside.py"
    shadow.mkdir()
    outside.write_text("x = 1\n", encoding="utf-8")

    diagnostics = normalize_lsp_diagnostics(
        uri=lsp_uri_from_path(outside),
        raw_diagnostics=[
            {
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 1},
                },
                "severity": 2,
                "message": "ignored",
            }
        ],
        shadow_root=shadow,
    )

    assert diagnostics == []
```

- [ ] **Step 2: Run diagnostic conversion tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_pyright_lsp.py::test_lsp_uri_round_trips_path tests/unit/test_pyright_lsp.py::test_normalize_lsp_diagnostics_maps_file_range_and_severity tests/unit/test_pyright_lsp.py::test_normalize_lsp_diagnostics_rejects_uri_outside_shadow_root -v
```

Expected: FAIL because `agent_quality_mcp.lsp.pyright` does not exist.

- [ ] **Step 3: Implement LSP diagnostic conversion helpers**

Create the first part of `src/agent_quality_mcp/lsp/pyright.py`:

```python
"""Pyright language-server integration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from agent_quality_mcp.models import Diagnostic, DiagnosticRange, DiagnosticSeverity


def lsp_uri_from_path(path: Path) -> str:
    """Convert a filesystem path to a file URI for LSP messages."""

    return Path(path).resolve().as_uri()


def path_from_lsp_uri(uri: str) -> Path:
    """Convert a file URI from LSP into a local filesystem path."""

    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Unsupported LSP URI scheme: {parsed.scheme}")
    if parsed.netloc not in {"", "localhost"}:
        raise ValueError("Only local file URIs are supported")
    return Path(unquote(parsed.path)).resolve()


def normalize_lsp_diagnostics(
    *,
    uri: str,
    raw_diagnostics: list[Any],
    shadow_root: Path,
) -> list[Diagnostic]:
    """Normalize Pyright LSP publishDiagnostics payloads."""

    try:
        path = path_from_lsp_uri(uri)
        relative = path.relative_to(shadow_root.resolve()).as_posix()
    except (OSError, ValueError):
        return []

    diagnostics: list[Diagnostic] = []
    for item in raw_diagnostics:
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        if not isinstance(message, str) or not message:
            message = "Pyright diagnostic"
        code_value = item.get("code")
        code = str(code_value) if code_value not in (None, "") else "pyright_lsp"
        severity, is_blocking = _lsp_severity(item.get("severity"))
        diagnostic_range = _lsp_range(item.get("range"))
        diagnostics.append(
            Diagnostic(
                id=_lsp_diagnostic_id(
                    code=code,
                    message=message,
                    file=relative,
                    diagnostic_range=diagnostic_range,
                ),
                source="pyright",
                severity=severity,
                code=code,
                message=message,
                file=relative,
                range=diagnostic_range,
                is_blocking=is_blocking,
                raw_source="pyright_lsp",
                metadata={"transport": "lsp"},
            )
        )
    return diagnostics


def _lsp_diagnostic_id(
    *,
    code: str,
    message: str,
    file: str,
    diagnostic_range: DiagnosticRange | None,
) -> str:
    payload = {
        "source": "pyright_lsp",
        "code": code,
        "message": message,
        "file": file,
        "range": diagnostic_range.model_dump() if diagnostic_range is not None else None,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"pyright-lsp-{digest[:16]}"


def _lsp_severity(raw: Any) -> tuple[DiagnosticSeverity, bool]:
    if raw == 1:
        return DiagnosticSeverity.ERROR, True
    if raw == 2:
        return DiagnosticSeverity.WARNING, False
    if raw == 3:
        return DiagnosticSeverity.INFO, False
    if raw == 4:
        return DiagnosticSeverity.INFO, False
    return DiagnosticSeverity.WARNING, False


def _lsp_range(raw: Any) -> DiagnosticRange | None:
    if not isinstance(raw, dict):
        return None
    start = raw.get("start")
    end = raw.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    values = (
        start.get("line"),
        start.get("character"),
        end.get("line"),
        end.get("character"),
    )
    if not all(isinstance(value, int) and value >= 0 for value in values):
        return None
    return DiagnosticRange(
        start_line=values[0] + 1,
        start_column=values[1] + 1,
        end_line=values[2] + 1,
        end_column=values[3] + 1,
    )
```

- [ ] **Step 4: Run diagnostic conversion tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_pyright_lsp.py::test_lsp_uri_round_trips_path tests/unit/test_pyright_lsp.py::test_normalize_lsp_diagnostics_maps_file_range_and_severity tests/unit/test_pyright_lsp.py::test_normalize_lsp_diagnostics_rejects_uri_outside_shadow_root -v
```

Expected: PASS.

- [ ] **Step 5: Commit diagnostic conversion**

Run:

```bash
git add src/agent_quality_mcp/lsp/pyright.py tests/unit/test_pyright_lsp.py
git commit -m "feat: normalize pyright lsp diagnostics"
```

## Task 5: Pyright LSP Manager And Provider With CLI Fallback

**Files:**
- Modify: `src/agent_quality_mcp/lsp/pyright.py`
- Modify: `tests/unit/test_pyright_lsp.py`

- [ ] **Step 1: Add fake transport tests for provider behavior**

Add these imports near the top of `tests/unit/test_pyright_lsp.py` if absent:

```python
from agent_quality_mcp.lsp.pyright import PyrightLspProvider
from agent_quality_mcp.models import AgentQualityConfig, CommandExecutionRecord, SafetyMode, ValidationMode
from agent_quality_mcp.validators import ValidatorRequest, ValidatorScope
```

Append to `tests/unit/test_pyright_lsp.py`:

```python

class FakeLspSession:
    def __init__(
        self,
        *,
        diagnostics_by_uri: dict[str, list[dict[str, object]]] | None = None,
        fail_reason: str | None = None,
        workspace_complete: bool = True,
    ) -> None:
        self.diagnostics_by_uri = diagnostics_by_uri or {}
        self.fail_reason = fail_reason
        self.workspace_complete = workspace_complete
        self.opened_workspace_roots: list[Path] = []
        self.closed_workspace_roots: list[Path] = []
        self.opened_documents: list[Path] = []

    def collect_diagnostics(
        self,
        *,
        shadow_root: Path,
        changed_files: list[Path],
        scope: ValidatorScope,
        timeout_seconds: float,
    ):
        self.opened_workspace_roots.append(shadow_root)
        try:
            if self.fail_reason is not None:
                return None, self.fail_reason
            if scope is ValidatorScope.CHANGED_FILES:
                for relative in changed_files:
                    if relative.suffix == ".py":
                        self.opened_documents.append(shadow_root / relative)
            if scope is ValidatorScope.WORKSPACE and not self.workspace_complete:
                return None, "workspace diagnostics incomplete"
            return self.diagnostics_by_uri, None
        finally:
            self.closed_workspace_roots.append(shadow_root)


class FakeManager:
    def __init__(self, session: FakeLspSession) -> None:
        self.session = session
        self.keys: list[Path] = []

    def session_for(self, real_workspace_root: Path) -> FakeLspSession:
        self.keys.append(real_workspace_root)
        return self.session


class FakeCliAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, list[Path], str]] = []

    def check(self, cwd: Path, changed_files: list[Path], mode: str):
        self.calls.append((cwd, changed_files, mode))
        record = CommandExecutionRecord(
            command="pyright",
            args=["pyright", "--outputjson"],
            cwd=str(cwd),
            duration_ms=3,
            exit_code=0,
        )
        return [], [record]


def _validator_request(
    tmp_path: Path,
    *,
    mode: ValidationMode = ValidationMode.QUICK,
    scope: ValidatorScope = ValidatorScope.CHANGED_FILES,
) -> ValidatorRequest:
    real = tmp_path / "real"
    shadow = tmp_path / "shadow"
    (shadow / "pkg").mkdir(parents=True)
    (shadow / "pkg" / "app.py").write_text("x: str = 1\n", encoding="utf-8")
    real.mkdir()
    return ValidatorRequest(
        real_workspace_root=real,
        shadow_workspace_root=shadow,
        changed_files=[Path("pkg/app.py")],
        mode=mode,
        safety_mode=SafetyMode.READ_ONLY,
        requested_scope=scope,
        timeout_budget_seconds=5.0,
        request_id="req-lsp",
        config=AgentQualityConfig(),
    )


def test_pyright_lsp_provider_uses_lsp_for_changed_file_diagnostics(tmp_path: Path) -> None:
    request = _validator_request(tmp_path)
    uri = lsp_uri_from_path(request.shadow_workspace_root / "pkg" / "app.py")
    session = FakeLspSession(
        diagnostics_by_uri={
            uri: [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1},
                    },
                    "severity": 1,
                    "code": "reportAssignmentType",
                    "message": "Bad assignment",
                }
            ]
        }
    )
    cli = FakeCliAdapter()
    provider = PyrightLspProvider(manager=FakeManager(session), cli_adapter=cli)

    result = provider.validate(request)

    assert result.provider == "pyright"
    assert result.metadata["lsp_reused"] is True
    assert result.metadata["fallback_to_cli"] is False
    assert result.diagnostics[0].message == "Bad assignment"
    assert session.opened_documents == [request.shadow_workspace_root / "pkg" / "app.py"]
    assert session.closed_workspace_roots == [request.shadow_workspace_root]
    assert cli.calls == []


def test_pyright_lsp_provider_falls_back_to_cli_on_lsp_failure(tmp_path: Path) -> None:
    request = _validator_request(tmp_path)
    session = FakeLspSession(fail_reason="initialize failed")
    cli = FakeCliAdapter()
    provider = PyrightLspProvider(manager=FakeManager(session), cli_adapter=cli)

    result = provider.validate(request)

    assert result.metadata["fallback_to_cli"] is True
    assert result.fallback_reason == "initialize failed"
    assert result.commands[0].command == "pyright"
    assert cli.calls == [(request.shadow_workspace_root, request.changed_files, "quick")]
    assert any(diagnostic.code == "lsp_fallback" for diagnostic in result.diagnostics)


def test_pyright_lsp_provider_falls_back_when_workspace_scope_incomplete(tmp_path: Path) -> None:
    request = _validator_request(
        tmp_path,
        mode=ValidationMode.STANDARD,
        scope=ValidatorScope.WORKSPACE,
    )
    session = FakeLspSession(workspace_complete=False)
    cli = FakeCliAdapter()
    provider = PyrightLspProvider(manager=FakeManager(session), cli_adapter=cli)

    result = provider.validate(request)

    assert result.metadata["fallback_to_cli"] is True
    assert result.fallback_reason == "workspace diagnostics incomplete"
    assert cli.calls == [(request.shadow_workspace_root, request.changed_files, "standard")]
```

Ensure `ValidatorRequest` is imported from `agent_quality_mcp.validators`.

- [ ] **Step 2: Run provider tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_pyright_lsp.py::test_pyright_lsp_provider_uses_lsp_for_changed_file_diagnostics tests/unit/test_pyright_lsp.py::test_pyright_lsp_provider_falls_back_to_cli_on_lsp_failure tests/unit/test_pyright_lsp.py::test_pyright_lsp_provider_falls_back_when_workspace_scope_incomplete -v
```

Expected: FAIL because `PyrightLspProvider` does not exist.

- [ ] **Step 3: Implement Pyright LSP provider around injectable manager/session**

Append to `src/agent_quality_mcp/lsp/pyright.py`:

```python
import time
from typing import Protocol

from agent_quality_mcp.cli.pyright import PyrightAdapter
from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.models import CommandExecutionRecord
from agent_quality_mcp.validators import (
    ValidatorCapability,
    ValidatorRequest,
    ValidatorResult,
    ValidatorScope,
)


class PyrightLspSession(Protocol):
    def collect_diagnostics(
        self,
        *,
        shadow_root: Path,
        changed_files: list[Path],
        scope: ValidatorScope,
        timeout_seconds: float,
    ) -> tuple[dict[str, list[dict[str, object]]] | None, str | None]:
        """Return diagnostics by URI, or a fallback reason."""


class PyrightLspManager(Protocol):
    def session_for(self, real_workspace_root: Path) -> PyrightLspSession:
        """Return a reusable Pyright LSP session for a real workspace key."""


class PyrightLspProvider:
    """Validate type diagnostics with Pyright LSP and CLI fallback."""

    def __init__(self, *, manager: PyrightLspManager, cli_adapter: PyrightAdapter) -> None:
        self.manager = manager
        self.cli_adapter = cli_adapter

    def validate(self, request: ValidatorRequest) -> ValidatorResult:
        started_at = time.monotonic()
        session = self.manager.session_for(request.real_workspace_root)
        diagnostics_by_uri, fallback_reason = session.collect_diagnostics(
            shadow_root=request.shadow_workspace_root,
            changed_files=request.changed_files,
            scope=request.requested_scope,
            timeout_seconds=request.timeout_budget_seconds,
        )
        if fallback_reason is not None or diagnostics_by_uri is None:
            return self._fallback(request, fallback_reason or "lsp diagnostics unavailable", started_at)

        diagnostics: list[Diagnostic] = []
        for uri, raw_items in diagnostics_by_uri.items():
            diagnostics.extend(
                normalize_lsp_diagnostics(
                    uri=uri,
                    raw_diagnostics=list(raw_items),
                    shadow_root=request.shadow_workspace_root,
                )
            )
        return ValidatorResult(
            provider="pyright",
            capabilities=[
                ValidatorCapability.TYPE_DIAGNOSTICS,
                ValidatorCapability.LSP_REUSE,
                ValidatorCapability.WORKSPACE_SCOPE
                if request.requested_scope is ValidatorScope.WORKSPACE
                else ValidatorCapability.CHANGED_FILE_SCOPE,
            ],
            diagnostics=diagnostics,
            metadata={
                "lsp_reused": True,
                "fallback_to_cli": False,
                "diagnostic_scope": request.requested_scope.value,
                "documents_opened": [
                    path.as_posix()
                    for path in request.changed_files
                    if request.requested_scope is ValidatorScope.CHANGED_FILES
                    and path.suffix == ".py"
                ],
                "diagnostics_completed": True,
            },
            duration_ms=_duration_ms(started_at),
        )

    def _fallback(
        self,
        request: ValidatorRequest,
        fallback_reason: str,
        started_at: float,
    ) -> ValidatorResult:
        cli_diagnostics, cli_records = self.cli_adapter.check(
            request.shadow_workspace_root,
            request.changed_files,
            request.mode.value,
        )
        warning = diagnostic_from_message(
            source="pyright",
            code="lsp_fallback",
            message=f"Pyright LSP unavailable; used Pyright CLI fallback: {fallback_reason}",
            severity=DiagnosticSeverity.WARNING,
            is_blocking=False,
            metadata={"fallback_reason": fallback_reason},
        )
        return ValidatorResult(
            provider="pyright",
            capabilities=[
                ValidatorCapability.TYPE_DIAGNOSTICS,
                ValidatorCapability.CLI_FALLBACK,
            ],
            diagnostics=[warning, *cli_diagnostics],
            commands=cli_records,
            metadata={
                "lsp_reused": False,
                "fallback_to_cli": True,
                "fallback_reason": fallback_reason,
                "diagnostic_scope": request.requested_scope.value,
                "diagnostics_completed": True,
            },
            fallback_reason=fallback_reason,
            duration_ms=_duration_ms(started_at),
            timed_out=any(record.timed_out for record in cli_records),
            output_truncated=any(
                record.stdout_truncated or record.stderr_truncated for record in cli_records
            ),
        )


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1000))
```

- [ ] **Step 4: Run provider tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_pyright_lsp.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit provider fallback behavior**

Run:

```bash
git add src/agent_quality_mcp/lsp/pyright.py tests/unit/test_pyright_lsp.py
git commit -m "feat: add pyright lsp provider fallback"
```

## Task 6: Real Pyright LSP Process Session

**Files:**
- Modify: `src/agent_quality_mcp/lsp/pyright.py`
- Modify: `tests/unit/test_pyright_lsp.py`

- [ ] **Step 1: Add fake process session lifecycle tests**

Append to `tests/unit/test_pyright_lsp.py`:

```python
from agent_quality_mcp.lsp.protocol import build_lsp_message
from agent_quality_mcp.lsp.pyright import PyrightLspProcessSession


class MemoryStream:
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.chunks = chunks or []
        self.written = bytearray()

    def write(self, text: str) -> int:
        data = text.encode("utf-8")
        self.written.extend(data)
        return len(text)

    def flush(self) -> None:
        return None

    def read(self, size: int) -> str:
        if not self.chunks:
            return ""
        return self.chunks.pop(0).decode("utf-8")


class FakeProcess:
    def __init__(self, stdout_chunks: list[bytes]) -> None:
        self.stdin = MemoryStream()
        self.stdout = MemoryStream(stdout_chunks)
        self.stderr = MemoryStream()
        self.pid = 99
        self.terminated = False

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True


def test_pyright_lsp_process_session_sends_initialize_without_real_root(tmp_path: Path) -> None:
    process = FakeProcess(
        [
            build_lsp_message({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}),
            build_lsp_message(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/publishDiagnostics",
                    "params": {
                        "uri": lsp_uri_from_path(tmp_path / "shadow" / "pkg" / "app.py"),
                        "diagnostics": [],
                    },
                }
            ),
        ]
    )
    real = tmp_path / "real"
    shadow = tmp_path / "shadow"
    (shadow / "pkg").mkdir(parents=True)
    (shadow / "pkg" / "app.py").write_text("x = 1\n", encoding="utf-8")
    real.mkdir()
    session = PyrightLspProcessSession(process=process, max_message_bytes=20_000)

    result, reason = session.collect_diagnostics(
        shadow_root=shadow,
        changed_files=[Path("pkg/app.py")],
        scope=ValidatorScope.CHANGED_FILES,
        timeout_seconds=1.0,
    )

    assert reason is None
    assert result == {lsp_uri_from_path(shadow / "pkg" / "app.py"): []}
    written = process.stdin.written.decode("utf-8")
    assert str(real) not in written
    assert '"rootUri":null' in written
```

- [ ] **Step 2: Run process session test and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_pyright_lsp.py::test_pyright_lsp_process_session_sends_initialize_without_real_root -v
```

Expected: FAIL because `PyrightLspProcessSession` does not exist.

- [ ] **Step 3: Implement minimal process session**

Extend `PyrightLspProcessSession` in `src/agent_quality_mcp/lsp/pyright.py`:

```python
from agent_quality_mcp.lsp.protocol import LspFramer, LspProtocolError, build_lsp_message


class PyrightLspProcessSession:
    """Stateful Pyright LSP session over a long-running process."""

    def __init__(self, *, process: object, max_message_bytes: int) -> None:
        self.process = process
        self.framer = LspFramer(max_message_bytes=max_message_bytes)
        self.initialized = False
        self._next_id = 1

    def collect_diagnostics(
        self,
        *,
        shadow_root: Path,
        changed_files: list[Path],
        scope: ValidatorScope,
        timeout_seconds: float,
    ) -> tuple[dict[str, list[dict[str, object]]] | None, str | None]:
        try:
            if not self.initialized:
                self._initialize(timeout_seconds)
            return self._collect_shadow_diagnostics(
                shadow_root=shadow_root,
                changed_files=changed_files,
                scope=scope,
                timeout_seconds=timeout_seconds,
            )
        except (OSError, LspProtocolError, RuntimeError, TimeoutError, ValueError) as exc:
            return None, str(exc)

    def _initialize(self, timeout_seconds: float) -> None:
        request_id = self._next_request_id()
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "processId": None,
                    "rootPath": None,
                    "rootUri": None,
                    "workspaceFolders": [],
                    "capabilities": {
                        "workspace": {
                            "workspaceFolders": True,
                            "didChangeConfiguration": {"dynamicRegistration": False},
                        },
                        "textDocument": {
                            "publishDiagnostics": {"relatedInformation": True},
                        },
                    },
                },
            }
        )
        response = self._read_until_response(request_id, timeout_seconds)
        if "error" in response:
            raise RuntimeError("Pyright LSP initialize returned an error")
        self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        self.initialized = True

    def _collect_shadow_diagnostics(
        self,
        *,
        shadow_root: Path,
        changed_files: list[Path],
        scope: ValidatorScope,
        timeout_seconds: float,
    ) -> tuple[dict[str, list[dict[str, object]]] | None, str | None]:
        expected_uris = {
            lsp_uri_from_path(shadow_root / path)
            for path in changed_files
            if scope is ValidatorScope.CHANGED_FILES and path.suffix == ".py"
        }
        for uri in expected_uris:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "python",
                            "version": 1,
                            "text": path_from_lsp_uri(uri).read_text(encoding="utf-8"),
                        }
                    },
                }
            )
        diagnostics: dict[str, list[dict[str, object]]] = {}
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            message = self._read_one_message(deadline - time.monotonic())
            if message is None:
                break
            if message.get("method") != "textDocument/publishDiagnostics":
                continue
            params = message.get("params")
            if not isinstance(params, dict):
                continue
            uri = params.get("uri")
            raw_diagnostics = params.get("diagnostics")
            if not isinstance(uri, str) or not isinstance(raw_diagnostics, list):
                continue
            try:
                path_from_lsp_uri(uri).relative_to(shadow_root.resolve())
            except (OSError, ValueError):
                continue
            diagnostics[uri] = [item for item in raw_diagnostics if isinstance(item, dict)]
            if expected_uris and expected_uris <= set(diagnostics):
                return diagnostics, None
        if expected_uris and not expected_uris <= set(diagnostics):
            return None, "changed-file diagnostics incomplete"
        if scope is ValidatorScope.WORKSPACE:
            return None, "workspace diagnostics incomplete"
        return diagnostics, None

    def _next_request_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id

    def _send(self, message: dict[str, object]) -> None:
        stdin = getattr(self.process, "stdin", None)
        if stdin is None:
            raise RuntimeError("LSP process stdin is unavailable")
        stdin.write(build_lsp_message(message).decode("utf-8"))
        stdin.flush()

    def _read_until_response(self, request_id: int, timeout_seconds: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            message = self._read_one_message(deadline - time.monotonic())
            if message is not None and message.get("id") == request_id:
                return message
        raise TimeoutError("Timed out waiting for LSP response")

    def _read_one_message(self, timeout_seconds: float) -> dict[str, object] | None:
        stdout = getattr(self.process, "stdout", None)
        if stdout is None:
            raise RuntimeError("LSP process stdout is unavailable")
        raw = stdout.read(4096)
        if raw == "":
            return None
        messages = self.framer.feed(raw.encode("utf-8"))
        if not messages:
            return None
        return messages[0]
```

This minimal implementation intentionally returns workspace-scope incomplete until a later implementation proves unopened-file coverage. That preserves safety by using CLI fallback for `standard` and `strict`.

- [ ] **Step 4: Run process session tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_pyright_lsp.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit process session**

Run:

```bash
git add src/agent_quality_mcp/lsp/pyright.py tests/unit/test_pyright_lsp.py
git commit -m "feat: add pyright lsp process session"
```

## Task 7: Service Wiring With Validator Results

**Files:**
- Modify: `src/agent_quality_mcp/service.py`
- Modify: `tests/unit/test_service.py`

- [ ] **Step 1: Add failing service tests for LSP fallback preservation**

Append to `tests/unit/test_service.py`:

```python
def test_validate_patch_uses_pyright_lsp_provider_and_preserves_response_shape(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    workspace = tmp_path
    captured: dict[str, object] = {}

    class StubPyrightProvider:
        def validate(self, request: ValidatorRequest) -> ValidatorResult:
            captured["real_workspace_root"] = request.real_workspace_root
            captured["shadow_workspace_root"] = request.shadow_workspace_root
            return ValidatorResult(
                provider="pyright",
                capabilities=[ValidatorCapability.TYPE_DIAGNOSTICS],
                diagnostics=[],
                metadata={"fallback_to_cli": False},
            )

    monkeypatch.setattr(
        "agent_quality_mcp.service._build_pyright_provider",
        lambda runner: StubPyrightProvider(),
    )
    monkeypatch.setattr("agent_quality_mcp.service.UvAdapter.check", lambda self, cwd, mode: ([], []))
    monkeypatch.setattr(
        "agent_quality_mcp.service.RuffAdapter.check",
        lambda self, cwd, changed_files, mode, preview_safe_fixes=False: ([], [], []),
    )

    response = validate_patch_service(
        ValidatePatchRequest(
            workspace_root=str(workspace),
            changed_files=["pkg/app.py"],
            mode=ValidationMode.QUICK,
        )
    )

    assert response.status == "passed"
    assert response.real_workspace_modified is False
    assert captured["real_workspace_root"] == workspace
    assert captured["shadow_workspace_root"] != workspace


def test_validate_patch_includes_pyright_lsp_fallback_warning(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    workspace = tmp_path

    class StubPyrightProvider:
        def validate(self, request: ValidatorRequest) -> ValidatorResult:
            return ValidatorResult(
                provider="pyright",
                capabilities=[ValidatorCapability.CLI_FALLBACK],
                diagnostics=[
                    diagnostic_from_message(
                        source="pyright",
                        code="lsp_fallback",
                        message="Pyright LSP unavailable; used Pyright CLI fallback: initialize failed",
                        severity=DiagnosticSeverity.WARNING,
                        is_blocking=False,
                    )
                ],
                metadata={"fallback_to_cli": True},
            )

    monkeypatch.setattr(
        "agent_quality_mcp.service._build_pyright_provider",
        lambda runner: StubPyrightProvider(),
    )
    monkeypatch.setattr("agent_quality_mcp.service.UvAdapter.check", lambda self, cwd, mode: ([], []))
    monkeypatch.setattr(
        "agent_quality_mcp.service.RuffAdapter.check",
        lambda self, cwd, changed_files, mode, preview_safe_fixes=False: ([], [], []),
    )

    response = validate_patch_service(
        ValidatePatchRequest(
            workspace_root=str(workspace),
            changed_files=["pkg/app.py"],
            mode=ValidationMode.QUICK,
        )
    )

    assert response.status == "passed"
    assert response.warnings[0].code == "lsp_fallback"
```

Add imports if missing:

```python
from agent_quality_mcp.validators import ValidatorCapability, ValidatorRequest, ValidatorResult
```

- [ ] **Step 2: Run service tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_service.py::test_validate_patch_uses_pyright_lsp_provider_and_preserves_response_shape tests/unit/test_service.py::test_validate_patch_includes_pyright_lsp_fallback_warning -v
```

Expected: FAIL because `_build_pyright_provider` does not exist and service still calls `PyrightAdapter` directly.

- [ ] **Step 3: Add provider builder and validator request helpers**

In `src/agent_quality_mcp/service.py`, add imports:

```python
from agent_quality_mcp.lsp.pyright import PyrightLspProvider
from agent_quality_mcp.validators import ValidatorRequest, ValidatorResult, ValidatorScope
```

Add helper functions before `_run_adapters`:

```python
def _validator_scope_for_tool(tool: str, mode: str) -> ValidatorScope:
    if tool == "pyright" and mode in {"standard", "strict"}:
        return ValidatorScope.WORKSPACE
    if tool == "ruff" and mode == "strict":
        return ValidatorScope.WORKSPACE
    return ValidatorScope.CHANGED_FILES


def _build_validator_request(
    *,
    request: ValidatePatchRequest,
    real_workspace_root: Path,
    shadow_root: Path,
    changed_files: list[Path],
    mode: str,
    safety_mode: SafetyMode,
    config: AgentQualityConfig,
    scope: ValidatorScope,
) -> ValidatorRequest:
    return ValidatorRequest(
        real_workspace_root=real_workspace_root,
        shadow_workspace_root=shadow_root,
        changed_files=changed_files,
        mode=ValidationMode(mode),
        safety_mode=safety_mode,
        requested_scope=scope,
        timeout_budget_seconds=float(config.subprocess_timeout_seconds),
        request_id=request.request_id,
        config=config,
    )


def _build_pyright_provider(runner: CommandRunner) -> PyrightLspProvider:
    return PyrightLspProvider(
        manager=_GLOBAL_PYRIGHT_LSP_MANAGER,
        cli_adapter=PyrightAdapter(runner),
    )
```

Define `_GLOBAL_PYRIGHT_LSP_MANAGER` after imports as a private fallback manager.
Task 8 replaces this fallback with the concrete reusable manager:

```python
class _UnavailablePyrightLspManager:
    def session_for(self, real_workspace_root: Path):
        class _Session:
            def collect_diagnostics(self, **kwargs: object):
                return None, "pyright lsp manager not configured"

        return _Session()


_GLOBAL_PYRIGHT_LSP_MANAGER = _UnavailablePyrightLspManager()
```

- [ ] **Step 4: Update adapter runner to call Pyright provider**

Change `_run_adapters` signature:

```python
def _run_adapters(
    *,
    request: ValidatePatchRequest,
    runner: CommandRunner,
    real_workspace_root: Path,
    shadow_root: Path,
    changed_files: list[Path],
    mode: str,
    safety_mode: SafetyMode,
    preview_safe_fixes: bool,
    timeout_check: Callable[[], None],
) -> _AdapterRunResult:
```

Update the call site inside `validate_patch_service`:

```python
adapter_result = _run_adapters(
    request=request,
    runner=runner,
    real_workspace_root=workspace_root,
    shadow_root=shadow.path,
    changed_files=changed_files,
    mode=effective_mode.value,
    safety_mode=effective_safety_mode,
    preview_safe_fixes=effective_safety_mode == SafetyMode.PREVIEW_SAFE_FIXES,
    timeout_check=lambda: _raise_if_timed_out(started_at, config),
)
```

Replace the Pyright direct adapter call in `_run_adapters` with:

```python
    timeout_check()
    pyright_request = _build_validator_request(
        request=request,
        real_workspace_root=real_workspace_root,
        shadow_root=shadow_root,
        changed_files=changed_files,
        mode=mode,
        safety_mode=safety_mode,
        config=runner.config,
        scope=_validator_scope_for_tool("pyright", mode),
    )
    pyright_result = _build_pyright_provider(runner).validate(pyright_request)
    diagnostics.extend(pyright_result.diagnostics)
    commands.extend(pyright_result.commands)
```

- [ ] **Step 5: Run targeted service tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_service.py::test_validate_patch_uses_pyright_lsp_provider_and_preserves_response_shape tests/unit/test_service.py::test_validate_patch_includes_pyright_lsp_fallback_warning -v
```

Expected: PASS.

- [ ] **Step 6: Run full service tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_service.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit service wiring**

Run:

```bash
git add src/agent_quality_mcp/service.py tests/unit/test_service.py
git commit -m "feat: route pyright validation through lsp provider"
```

## Task 8: Concrete Manager Startup And Inspect Workspace Metadata

**Files:**
- Modify: `src/agent_quality_mcp/lsp/pyright.py`
- Modify: `src/agent_quality_mcp/service.py`
- Modify: `tests/unit/test_pyright_lsp.py`
- Modify: `tests/unit/test_service.py`

- [ ] **Step 1: Add manager startup and reuse tests**

Append to `tests/unit/test_pyright_lsp.py`:

```python
from agent_quality_mcp.lsp.pyright import RealPyrightLspManager


def test_real_pyright_lsp_manager_reuses_session_for_same_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    started: list[Path] = []

    class FakeSession:
        pass

    def fake_start(real_workspace_root: Path, config: AgentQualityConfig):
        started.append(real_workspace_root)
        return FakeSession()

    monkeypatch.setattr("agent_quality_mcp.lsp.pyright._start_process_session", fake_start)
    manager = RealPyrightLspManager(config=AgentQualityConfig())

    first = manager.session_for(workspace)
    second = manager.session_for(workspace)

    assert first is second
    assert started == [workspace]


def test_real_pyright_lsp_manager_separates_workspaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_workspace = tmp_path / "one"
    second_workspace = tmp_path / "two"
    first_workspace.mkdir()
    second_workspace.mkdir()

    class FakeSession:
        pass

    monkeypatch.setattr(
        "agent_quality_mcp.lsp.pyright._start_process_session",
        lambda real_workspace_root, config: FakeSession(),
    )
    manager = RealPyrightLspManager(config=AgentQualityConfig())

    assert manager.session_for(first_workspace) is not manager.session_for(second_workspace)
```

- [ ] **Step 2: Run manager tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_pyright_lsp.py::test_real_pyright_lsp_manager_reuses_session_for_same_workspace tests/unit/test_pyright_lsp.py::test_real_pyright_lsp_manager_separates_workspaces -v
```

Expected: FAIL because `RealPyrightLspManager` does not exist.

- [ ] **Step 3: Implement manager startup**

Append to `src/agent_quality_mcp/lsp/pyright.py`:

```python
import threading

from agent_quality_mcp.cli.runner import start_long_running_command
from agent_quality_mcp.models import AgentQualityConfig


class RealPyrightLspManager:
    """Reusable Pyright LSP session manager keyed by real workspace."""

    def __init__(self, *, config: AgentQualityConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._sessions: dict[Path, PyrightLspProcessSession] = {}

    def session_for(self, real_workspace_root: Path) -> PyrightLspProcessSession:
        key = real_workspace_root.resolve()
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                session = _start_process_session(key, self.config)
                self._sessions[key] = session
            return session


def _start_process_session(
    real_workspace_root: Path,
    config: AgentQualityConfig,
) -> PyrightLspProcessSession:
    command = start_long_running_command(
        "pyright-langserver",
        ["--stdio"],
        cwd=real_workspace_root,
        config=config,
    )
    return PyrightLspProcessSession(
        process=command.process,
        max_message_bytes=config.max_output_bytes,
    )
```

- [ ] **Step 4: Wire real manager in service**

In `src/agent_quality_mcp/service.py`, replace `_UnavailablePyrightLspManager` use with a lazily keyed manager cache:

```python
from agent_quality_mcp.lsp.pyright import PyrightLspProvider, RealPyrightLspManager

_PYRIGHT_LSP_MANAGERS: dict[int, RealPyrightLspManager] = {}


def _pyright_lsp_manager(config: AgentQualityConfig) -> RealPyrightLspManager:
    key = id(config)
    manager = _PYRIGHT_LSP_MANAGERS.get(key)
    if manager is None:
        manager = RealPyrightLspManager(config=config)
        _PYRIGHT_LSP_MANAGERS[key] = manager
    return manager


def _build_pyright_provider(runner: CommandRunner) -> PyrightLspProvider:
    return PyrightLspProvider(
        manager=_pyright_lsp_manager(runner.config),
        cli_adapter=PyrightAdapter(runner),
    )
```

Keep this small cache internal. A later cleanup can replace it with an explicit service-owned lifecycle object.

- [ ] **Step 5: Update inspect workspace command availability expectations**

In `tests/unit/test_service.py`, update expected command maps in inspect tests from:

```python
{"uv": False, "ruff": False, "pyright": False}
```

to:

```python
{"uv": False, "ruff": False, "pyright": False, "pyright-langserver": False}
```

and expected resolved paths similarly:

```python
{"uv": None, "ruff": None, "pyright": None, "pyright-langserver": None}
```

In `src/agent_quality_mcp/service.py`, update:

```python
SUPPORTED_TOOLS = ("uv", "ruff", "pyright", "pyright-langserver")
```

- [ ] **Step 6: Run manager and service tests**

Run:

```bash
.venv/bin/python -m pytest tests/unit/test_pyright_lsp.py tests/unit/test_service.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit manager startup**

Run:

```bash
git add src/agent_quality_mcp/lsp/pyright.py src/agent_quality_mcp/service.py tests/unit/test_pyright_lsp.py tests/unit/test_service.py
git commit -m "feat: start reusable pyright lsp manager"
```

## Task 9: Integration Coverage And README

**Files:**
- Modify: `tests/integration/test_validate_patch_demo.py`
- Modify: `README.md`

- [ ] **Step 1: Add integration assertion for Pyright LSP or fallback evidence**

In `tests/integration/test_validate_patch_demo.py`, extend the existing demo validation test after response diagnostics are collected:

```python
    diagnostics = [*response.blocking_errors, *response.warnings, *response.info]
    pyright_evidence = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.source in {"pyright", "system"}
        and (
            diagnostic.source == "pyright"
            or diagnostic.metadata.get("tool") in {"pyright", "pyright-langserver"}
        )
    ]
    pyright_commands = [
        command.command
        for command in response.execution.commands
        if command.command in {"pyright", "pyright-langserver"}
    ]
    assert pyright_evidence or pyright_commands
```

This keeps CI stable whether real LSP is available or the CLI fallback path is used.

- [ ] **Step 2: Run integration test**

Run:

```bash
.venv/bin/python -m pytest tests/integration/test_validate_patch_demo.py -v
```

Expected: PASS.

- [ ] **Step 3: Update README security model and setup docs**

In `README.md`, update the allowlist bullet from:

```markdown
- Subprocesses are restricted to an allowlist of `uv`, `ruff`, and `pyright`.
```

to:

```markdown
- Subprocesses are restricted to an allowlist of `uv`, `ruff`, `pyright`, and
  `pyright-langserver`.
```

Add this under tool path configuration:

```markdown
AGENT_QUALITY_MCP_PYRIGHT_LANGSERVER=/opt/tools/pyright-langserver
```

Add this paragraph near the Pyright description or MVP limitations:

```markdown
Pyright type diagnostics prefer a reusable `pyright-langserver --stdio`
language-server path. The language server is keyed by the resolved real
workspace for process reuse, but diagnostics are requested only against the
shadow workspace created for each validation. If LSP initialization,
diagnostic completion, workspace-scope coverage, or protocol parsing is
unreliable, validation falls back to the existing Pyright CLI adapter and
returns a non-blocking warning diagnostic.
```

Remove the `No LSP integration.` MVP limitation or replace it with:

```markdown
- Pyright LSP is diagnostics-only; completions, hover, code actions, import
  organization, and generic multi-language LSP support are not included.
```

- [ ] **Step 4: Run README diff check**

Run:

```bash
git diff -- README.md tests/integration/test_validate_patch_demo.py
git diff --check -- README.md tests/integration/test_validate_patch_demo.py
```

Expected: diff shows only the integration assertion and README LSP documentation; `git diff --check` exits 0.

- [ ] **Step 5: Commit docs and integration coverage**

Run:

```bash
git add README.md tests/integration/test_validate_patch_demo.py
git commit -m "docs: document pyright lsp validation"
```

## Task 10: Full Verification And Cleanup

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run full tests**

Run:

```bash
.venv/bin/python -m pytest -v
```

Expected: PASS.

- [ ] **Step 2: Run Ruff**

Run:

```bash
.venv/bin/ruff check .
```

Expected: PASS.

- [ ] **Step 3: Run Pyright**

Run:

```bash
.venv/bin/pyright --pythonpath .venv/bin/python
```

Expected: PASS.

- [ ] **Step 4: Run whitespace diff check**

Run:

```bash
git diff --check
```

Expected: PASS.

- [ ] **Step 5: Inspect final diff and unrelated files**

Run:

```bash
git status --short
git diff --stat
```

Expected: only intentional tracked files are modified. If `/Users/kal/techne/.DS_Store` is still untracked, leave it unstaged.

- [ ] **Step 6: Commit any final verification-only fixes**

If Steps 1-4 required fixes, run `git status --short` and stage only the tracked
files changed by those fixes. Do not stage `/Users/kal/techne/.DS_Store`. Commit
the verification fix with:

```bash
git commit -m "fix: stabilize pyright lsp validation"
```

If Steps 1-4 passed without changes, leave the branch unchanged.

## Self-Review Checklist

- The plan covers shared validator models, `uv` metadata, Ruff metadata, Pyright LSP command resolution, long-running process startup, protocol framing, LSP diagnostic normalization, manager lifecycle, CLI fallback, service wiring, inspection metadata, integration coverage, README updates, and full verification.
- The plan preserves shadow-workspace-only validation and does not add real-repository mutation.
- The plan keeps Pyright CLI fallback instead of deleting the adapter.
- The plan keeps public `validate_patch` response compatibility.
- The plan does not require real Pyright LSP timing in CI; fake-process and fallback paths carry deterministic coverage.
