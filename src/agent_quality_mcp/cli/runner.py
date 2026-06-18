"""Secure subprocess runner for allowlisted quality tools."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from agent_quality_mcp import audit
from agent_quality_mcp.exceptions import SecurityError, ToolUnavailableError
from agent_quality_mcp.models import AgentQualityConfig, CommandExecutionRecord

ALLOWED_COMMANDS = {"uv", "ruff", "pyright"}


def resolve_allowed_command(command: str, config: AgentQualityConfig) -> str:
    """Resolve an allowlisted command to a safe executable path."""

    if command not in ALLOWED_COMMANDS:
        raise SecurityError(f"Command is not allowlisted: {command}")

    configured_path = getattr(config.command_paths, command)
    if configured_path is not None:
        path = Path(configured_path)
        if not path.is_absolute():
            raise SecurityError(f"Configured path for {command} must be absolute")
        if path.name != command:
            raise SecurityError(
                f"Configured path for {command} must point to executable named {command}"
            )
        if not path.exists():
            raise SecurityError(f"Configured path for {command} does not exist")
        if not path.is_file():
            raise SecurityError(f"Configured path for {command} must be a file")
        if not os.access(path, os.X_OK):
            raise SecurityError(f"Configured path for {command} must be executable")
        return str(path)

    resolved = shutil.which(command)
    if resolved is None:
        raise ToolUnavailableError(f"Unable to resolve required tool: {command}")
    return resolved


class CommandRunner:
    """Run allowlisted quality tools with a minimal, non-shell subprocess boundary."""

    def __init__(self, config: AgentQualityConfig) -> None:
        self.config = config

    def run(self, command: str, args: list[str], cwd: Path) -> CommandExecutionRecord:
        """Run an allowlisted command and return a response-safe execution record."""

        executable = resolve_allowed_command(command, self.config)
        started_at = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - executable is allowlist-resolved.
                [executable, *args],
                cwd=str(cwd),
                env=_safe_environment(self.config),
                text=True,
                capture_output=True,
                shell=False,
                timeout=self.config.subprocess_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = _duration_ms(started_at)
            stdout_preview, stdout_truncated = _preview(_coerce_output(exc.stdout), self.config)
            stderr_preview, stderr_truncated = _preview(_coerce_output(exc.stderr), self.config)
            return CommandExecutionRecord(
                command=command,
                args=[command, *args],
                cwd=str(cwd),
                duration_ms=duration_ms,
                exit_code=None,
                timed_out=True,
                stdout_preview=stdout_preview,
                stderr_preview=stderr_preview,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )
        except FileNotFoundError as exc:
            raise ToolUnavailableError(f"Unable to execute required tool: {command}") from exc
        except OSError as exc:
            raise ToolUnavailableError(f"Unable to execute required tool {command}: {exc}") from exc

        stdout_preview, stdout_truncated = _preview(completed.stdout or "", self.config)
        stderr_preview, stderr_truncated = _preview(completed.stderr or "", self.config)
        return CommandExecutionRecord(
            command=command,
            args=[command, *args],
            cwd=str(cwd),
            duration_ms=_duration_ms(started_at),
            exit_code=completed.returncode,
            timed_out=False,
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )


def _safe_environment(config: AgentQualityConfig) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "UV_NO_ENV_FILE": "1",
        "UV_NO_PROGRESS": "1",
    }
    if config.uv_offline:
        env["UV_OFFLINE"] = "1"
    return env


def _preview(text: str, config: AgentQualityConfig) -> tuple[str, bool]:
    literal_redacted = text
    for literal in config.secret_redaction_patterns:
        literal_redacted = literal_redacted.replace(literal, "[REDACTED]")
    redacted = audit.redact_text(literal_redacted, config)
    return audit.truncate_text(redacted, config.max_output_bytes)


def _duration_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def _coerce_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output
