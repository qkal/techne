from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_quality_mcp.cli.runner import (
    CommandRunner,
    resolve_allowed_command,
    start_long_running_command,
)
from agent_quality_mcp.exceptions import CommandExecutionError, SecurityError, ToolUnavailableError
from agent_quality_mcp.models import AgentQualityConfig, CommandConfig


class CompletedProcessStub:
    def __init__(self, *, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_resolve_allowed_command_rejects_unknown_tools() -> None:
    try:
        resolve_allowed_command("python", AgentQualityConfig())
    except SecurityError:
        pass
    else:
        raise AssertionError("unknown tools should be rejected")


def test_resolve_allowed_command_rejects_relative_configured_paths() -> None:
    config = AgentQualityConfig(command_paths=CommandConfig(ruff="bin/ruff"))

    try:
        resolve_allowed_command("ruff", config)
    except SecurityError:
        pass
    else:
        raise AssertionError("relative configured command paths should be rejected")


def test_resolve_allowed_command_rejects_configured_paths_with_wrong_basename(
    tmp_path: Path,
) -> None:
    config = AgentQualityConfig(command_paths=CommandConfig(pyright=str(tmp_path / "node")))

    try:
        resolve_allowed_command("pyright", config)
    except SecurityError:
        pass
    else:
        raise AssertionError("configured command basename should match the tool")


def test_resolve_allowed_command_rejects_nonexistent_configured_paths(tmp_path: Path) -> None:
    config = AgentQualityConfig(command_paths=CommandConfig(uv=str(tmp_path / "uv")))

    try:
        resolve_allowed_command("uv", config)
    except SecurityError:
        pass
    else:
        raise AssertionError("nonexistent configured command paths should be rejected")


def test_resolve_allowed_command_rejects_non_executable_configured_paths(
    tmp_path: Path,
) -> None:
    fake_ruff = tmp_path / "ruff"
    fake_ruff.write_text("", encoding="utf-8")
    fake_ruff.chmod(0o600)
    config = AgentQualityConfig(command_paths=CommandConfig(ruff=str(fake_ruff)))

    try:
        resolve_allowed_command("ruff", config)
    except SecurityError:
        pass
    else:
        raise AssertionError("non-executable configured command paths should be rejected")


def test_resolve_allowed_command_rejects_configured_symlink_to_wrong_tool(
    tmp_path: Path,
) -> None:
    fake_ruff = tmp_path / "ruff"
    fake_ruff.symlink_to("/bin/sh")
    config = AgentQualityConfig(command_paths=CommandConfig(ruff=str(fake_ruff)))

    try:
        resolve_allowed_command("ruff", config)
    except SecurityError:
        pass
    else:
        raise AssertionError("configured symlinks must resolve to the requested tool")


def test_resolve_allowed_command_ignores_relative_and_empty_path_entries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    relative_bin = workspace / "tools"
    relative_bin.mkdir()
    for executable in [workspace / "uv", relative_bin / "uv"]:
        executable.write_text("", encoding="utf-8")
        executable.chmod(0o700)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("PATH", ":tools")

    try:
        resolve_allowed_command("uv", AgentQualityConfig())
    except ToolUnavailableError:
        pass
    else:
        raise AssertionError("relative and empty PATH entries should not resolve tools")


def test_resolve_allowed_command_uses_absolute_path_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    absolute_bin = tmp_path / "bin"
    absolute_bin.mkdir()
    malicious_local = workspace / "ruff"
    malicious_relative = workspace / "tools" / "ruff"
    malicious_relative.parent.mkdir()
    absolute_ruff = absolute_bin / "ruff"
    for executable in [malicious_local, malicious_relative, absolute_ruff]:
        executable.write_text("", encoding="utf-8")
        executable.chmod(0o700)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("PATH", f":tools:{absolute_bin}")

    resolved = resolve_allowed_command("ruff", AgentQualityConfig())

    assert resolved == str(absolute_ruff.resolve(strict=True))


def test_resolve_allowed_command_rejects_path_symlink_to_wrong_tool(
    monkeypatch,
    tmp_path: Path,
) -> None:
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    fake_ruff = tool_dir / "ruff"
    fake_ruff.symlink_to("/bin/sh")
    monkeypatch.setenv("PATH", str(tool_dir))

    try:
        resolve_allowed_command("ruff", AgentQualityConfig())
    except SecurityError:
        pass
    else:
        raise AssertionError("PATH symlinks must resolve to the requested tool")


def test_resolve_allowed_command_rejects_workspace_symlink_path_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    external_bin = tmp_path / "external-bin"
    workspace.mkdir()
    external_bin.mkdir()
    fake_ruff = external_bin / "ruff"
    fake_ruff.write_text("", encoding="utf-8")
    fake_ruff.chmod(0o700)
    workspace_toolchain = workspace / "toolchain"
    workspace_toolchain.symlink_to(external_bin, target_is_directory=True)
    monkeypatch.setenv("PATH", str(workspace_toolchain))

    try:
        resolve_allowed_command("ruff", AgentQualityConfig(), cwd=workspace)
    except ToolUnavailableError:
        pass
    else:
        raise AssertionError("workspace-owned PATH symlink directories should be rejected")


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


def test_command_runner_rejects_project_bound_absolute_path_entries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    venv_bin = workspace / ".venv" / "bin"
    node_bin = workspace / "node_modules" / ".bin"
    venv_bin.mkdir(parents=True)
    node_bin.mkdir(parents=True)
    for executable in [venv_bin / "ruff", node_bin / "pyright"]:
        executable.write_text("", encoding="utf-8")
        executable.chmod(0o700)
    monkeypatch.setenv("PATH", f"{venv_bin}{os.pathsep}{node_bin}")

    def fake_run(argv: list[str], **kwargs: Any) -> CompletedProcessStub:
        raise AssertionError("project-bound PATH executable should not run")

    monkeypatch.setattr(subprocess, "run", fake_run)

    for command in ["ruff", "pyright"]:
        try:
            CommandRunner(AgentQualityConfig()).run(command, ["--version"], workspace)
        except ToolUnavailableError:
            pass
        else:
            raise AssertionError(f"{command} should not resolve from project PATH entries")


def test_command_runner_passes_sanitized_child_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project_bin = workspace / ".venv" / "bin"
    project_bin.mkdir(parents=True)
    trusted_bin = tmp_path / "trusted-bin"
    trusted_bin.mkdir()
    for executable in [project_bin / "ruff", trusted_bin / "ruff"]:
        executable.write_text("", encoding="utf-8")
        executable.chmod(0o700)
    monkeypatch.setenv("PATH", f"{project_bin}{os.pathsep}{trusted_bin}")
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> CompletedProcessStub:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return CompletedProcessStub(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    CommandRunner(AgentQualityConfig()).run("ruff", ["--version"], workspace)

    assert captured["argv"][0] == str((trusted_bin / "ruff").resolve(strict=True))
    assert captured["kwargs"]["env"]["PATH"] == str(trusted_bin.resolve(strict=True))


def test_command_runner_rejects_configured_workspace_bound_executable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_ruff = tmp_path / "ruff"
    fake_ruff.write_text("", encoding="utf-8")
    fake_ruff.chmod(0o700)
    config = AgentQualityConfig(command_paths=CommandConfig(ruff=str(fake_ruff)))

    def fake_run(argv: list[str], **kwargs: Any) -> CompletedProcessStub:
        raise AssertionError("workspace-bound configured executable should not run")

    monkeypatch.setattr(subprocess, "run", fake_run)

    try:
        CommandRunner(config).run("ruff", ["check"], tmp_path)
    except (CommandExecutionError, ToolUnavailableError):
        pass
    else:
        raise AssertionError("workspace-bound configured executable should be rejected")


def test_command_runner_rejects_workspace_symlink_configured_executable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    external_bin = tmp_path / "external-bin"
    workspace.mkdir()
    external_bin.mkdir()
    external_ruff = external_bin / "ruff"
    external_ruff.write_text("", encoding="utf-8")
    external_ruff.chmod(0o700)
    workspace_ruff = workspace / "ruff"
    workspace_ruff.symlink_to(external_ruff)
    config = AgentQualityConfig(command_paths=CommandConfig(ruff=str(workspace_ruff)))

    def fake_run(argv: list[str], **kwargs: Any) -> CompletedProcessStub:
        raise AssertionError("workspace-owned configured symlink should not run")

    monkeypatch.setattr(subprocess, "run", fake_run)

    try:
        CommandRunner(config).run("ruff", ["check"], workspace)
    except CommandExecutionError:
        pass
    else:
        raise AssertionError("workspace-owned configured symlink should be rejected")


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


def test_command_runner_uses_safe_argument_subprocess_and_records_previews(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    fake_uv = tool_dir / "uv"
    fake_uv.write_text("", encoding="utf-8")
    fake_uv.chmod(0o700)
    config = AgentQualityConfig(
        command_paths=CommandConfig(uv=str(fake_uv)),
        max_output_bytes=24,
        secret_redaction_patterns=["internal-secret"],
    )
    captured: dict[str, Any] = {}
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("SECRET_TOKEN", "must-not-leak")

    def fake_run(argv: list[str], **kwargs: Any) -> CompletedProcessStub:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return CompletedProcessStub(
            returncode=3,
            stdout="before internal-secret after",
            stderr="x" * 80,
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    record = CommandRunner(config).run("uv", ["--version"], workspace)

    assert captured["argv"] == [str(fake_uv), "--version"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == str(workspace)
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
    assert captured["kwargs"]["timeout"] == config.subprocess_timeout_seconds
    assert captured["kwargs"]["check"] is False

    expected_path = str(Path("/bin").resolve(strict=True))
    env = captured["kwargs"]["env"]
    assert env == {
        "PATH": expected_path,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "UV_NO_ENV_FILE": "1",
        "UV_NO_PROGRESS": "1",
        "UV_OFFLINE": "1",
    }
    assert "SECRET_TOKEN" not in env

    assert record.command == "uv"
    assert record.args == ["uv", "--version"]
    assert record.cwd == str(workspace)
    assert record.exit_code == 3
    assert record.timed_out is False
    assert "internal-secret" not in record.stdout_preview
    assert "[REDACTED]" in record.stdout_preview
    assert record.stderr_truncated is True
    assert record.stderr_preview.endswith("[TRUNCATED]")


def test_command_runner_records_timeout_without_raising(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    fake_ruff = tool_dir / "ruff"
    fake_ruff.write_text("", encoding="utf-8")
    fake_ruff.chmod(0o700)
    config = AgentQualityConfig(command_paths=CommandConfig(ruff=str(fake_ruff)))

    def fake_run(argv: list[str], **kwargs: Any) -> CompletedProcessStub:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    record = CommandRunner(config).run("ruff", ["check"], workspace)

    assert record.command == "ruff"
    assert record.args == ["ruff", "check"]
    assert record.exit_code is None
    assert record.timed_out is True


def test_command_runner_wraps_oserror_without_raw_exception(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    fake_pyright = tool_dir / "pyright"
    fake_pyright.write_text("", encoding="utf-8")
    fake_pyright.chmod(0o700)
    config = AgentQualityConfig(command_paths=CommandConfig(pyright=str(fake_pyright)))

    def fake_run(argv: list[str], **kwargs: Any) -> CompletedProcessStub:
        raise OSError("spawn failed")

    monkeypatch.setattr(subprocess, "run", fake_run)

    try:
        CommandRunner(config).run("pyright", ["--outputjson"], workspace)
    except ToolUnavailableError as exc:
        assert "pyright" in str(exc)
        assert "spawn failed" in str(exc)
    else:
        raise AssertionError("OSError should be wrapped as ToolUnavailableError")
