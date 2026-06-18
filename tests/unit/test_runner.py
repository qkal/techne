from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agent_quality_mcp.cli.runner import CommandRunner, resolve_allowed_command
from agent_quality_mcp.exceptions import SecurityError
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


def test_command_runner_uses_safe_argument_subprocess_and_records_previews(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fake_uv = tmp_path / "uv"
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

    record = CommandRunner(config).run("uv", ["--version"], tmp_path)

    assert captured["argv"] == [str(fake_uv), "--version"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["timeout"] == config.subprocess_timeout_seconds
    assert captured["kwargs"]["check"] is False

    env = captured["kwargs"]["env"]
    assert env == {
        "PATH": "/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "UV_NO_ENV_FILE": "1",
        "UV_NO_PROGRESS": "1",
        "UV_OFFLINE": "1",
    }
    assert "SECRET_TOKEN" not in env

    assert record.command == "uv"
    assert record.args == ["uv", "--version"]
    assert record.cwd == str(tmp_path)
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
    fake_ruff = tmp_path / "ruff"
    fake_ruff.write_text("", encoding="utf-8")
    fake_ruff.chmod(0o700)
    config = AgentQualityConfig(command_paths=CommandConfig(ruff=str(fake_ruff)))

    def fake_run(argv: list[str], **kwargs: Any) -> CompletedProcessStub:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    record = CommandRunner(config).run("ruff", ["check"], tmp_path)

    assert record.command == "ruff"
    assert record.args == ["ruff", "check"]
    assert record.exit_code is None
    assert record.timed_out is True
