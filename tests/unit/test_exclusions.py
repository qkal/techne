from __future__ import annotations

from pathlib import Path

from agent_quality_mcp.exclusions import is_workspace_path_excluded, matches_secret_filename
from agent_quality_mcp.models import AgentQualityConfig


def test_is_workspace_path_excluded_uses_builtin_and_configured_exclusions(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("ignored = True\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    config = AgentQualityConfig(workspace_exclusions=["custom"])

    assert is_workspace_path_excluded(tmp_path / ".venv" / "ignored.py", tmp_path, config)
    assert is_workspace_path_excluded(tmp_path / "custom" / "file.py", tmp_path, config)
    assert not is_workspace_path_excluded(tmp_path / "src" / "app.py", tmp_path, config)


def test_is_workspace_path_excluded_skips_secret_files(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("SECRET=1\n", encoding="utf-8")
    config = AgentQualityConfig()

    assert is_workspace_path_excluded(secret, tmp_path, config)
    assert matches_secret_filename(secret, config)


def test_is_workspace_path_excluded_can_skip_secret_file_matching(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("SECRET=1\n", encoding="utf-8")
    config = AgentQualityConfig()

    assert not is_workspace_path_excluded(
        secret,
        tmp_path,
        config,
        include_secret_files=False,
    )
