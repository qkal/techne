import shutil
from pathlib import Path

import pytest  # pyright: ignore[reportMissingImports]

from agent_quality_mcp.exceptions import SecurityError, WorkspaceError
from agent_quality_mcp.models import AgentQualityConfig
from agent_quality_mcp.shadow import create_shadow_workspace
from agent_quality_mcp.workspace import inspect_workspace_files


def test_inspect_workspace_counts_python_and_configs(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    result = inspect_workspace_files(tmp_path, AgentQualityConfig())

    assert result.python_file_count == 1
    assert "pyproject.toml" in result.config_files


def test_inspect_workspace_respects_exclusions(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "ignored.py").write_text("ignored = True\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pyrightconfig.json").write_text("{}", encoding="utf-8")

    result = inspect_workspace_files(tmp_path, AgentQualityConfig())

    assert result.python_file_count == 1
    assert result.config_files == []


def test_shadow_workspace_excludes_secret_and_cache_files(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("token=secret\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "app.pyc").write_bytes(b"binary")

    with create_shadow_workspace(tmp_path, AgentQualityConfig()) as shadow:
        assert (shadow.path / "pkg" / "app.py").exists()
        assert not (shadow.path / ".env").exists()
        assert not (shadow.path / "__pycache__").exists()
        assert shadow.real_workspace_modified is False


def test_shadow_workspace_keeps_builtin_exclusions_when_config_lists_are_empty(
    tmp_path: Path,
) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (tmp_path / ".env").write_text("token=secret\n", encoding="utf-8")
    config = AgentQualityConfig(workspace_exclusions=[], secret_file_patterns=[])

    with create_shadow_workspace(tmp_path, config) as shadow:
        assert (shadow.path / "pkg" / "app.py").exists()
        assert not (shadow.path / ".git").exists()
        assert not (shadow.path / ".env").exists()


def test_shadow_workspace_rejects_file_exceeding_max_file_bytes(tmp_path: Path) -> None:
    (tmp_path / "large.py").write_bytes(b"12345")
    config = AgentQualityConfig(max_changed_file_bytes=4)

    with pytest.raises(WorkspaceError):
        with create_shadow_workspace(tmp_path, config):
            pass


def test_shadow_workspace_rejects_total_copy_exceeding_max_workspace_bytes(
    tmp_path: Path,
) -> None:
    (tmp_path / "one.py").write_bytes(b"123")
    (tmp_path / "two.py").write_bytes(b"456")
    config = AgentQualityConfig(max_workspace_copy_bytes=5)

    with pytest.raises(WorkspaceError):
        with create_shadow_workspace(tmp_path, config):
            pass


def test_shadow_workspace_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside_shadow_target.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    (tmp_path / "link.py").symlink_to(outside)

    with pytest.raises(SecurityError):
        with create_shadow_workspace(tmp_path, AgentQualityConfig()):
            pass


def test_shadow_workspace_cleans_up_by_default(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("value = 1\n", encoding="utf-8")

    with create_shadow_workspace(tmp_path, AgentQualityConfig()) as shadow:
        shadow_path = shadow.path
        assert shadow_path.exists()

    assert not shadow_path.exists()


def test_shadow_workspace_preserve_leaves_shadow_on_disk(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("value = 1\n", encoding="utf-8")
    config = AgentQualityConfig(preserve_shadow_workspace=True)

    with create_shadow_workspace(tmp_path, config) as shadow:
        shadow_path = shadow.path
        temp_root = shadow.path.parent
        assert shadow.preserved is True
        assert shadow_path.exists()

    try:
        assert shadow_path.exists()
        assert (shadow_path / "pkg" / "app.py").exists()
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root)
