"""CLI argument-parsing tests for the agent-quality-mcp entrypoint."""

from __future__ import annotations

import pytest

from agent_quality_mcp import __version__
from agent_quality_mcp.server import parse_args


def test_parse_args_version_short_circuits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--version"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert __version__ in captured.out


def test_parse_args_version_short_flag_short_circuits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["-V"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert __version__ in captured.out


def test_parse_args_help_short_circuits(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--help"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "agent-quality-mcp" in captured.out.lower()


def test_parse_args_unknown_flag_fails_fast(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--not-a-real-flag"])
    captured = capsys.readouterr()
    assert exc_info.value.code != 0
    assert captured.err


def test_parse_args_no_arguments_returns_namespace() -> None:
    args = parse_args([])
    assert args is not None
