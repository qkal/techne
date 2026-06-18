from pathlib import Path

import pytest  # pyright: ignore[reportMissingImports]

from agent_quality_mcp.exceptions import SecurityError, WorkspaceError
from agent_quality_mcp.paths import resolve_workspace_root, validate_changed_files


def test_resolve_workspace_root_requires_existing_directory(tmp_path: Path) -> None:
    root = resolve_workspace_root(tmp_path)

    assert root == tmp_path.resolve()


def test_resolve_workspace_root_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        resolve_workspace_root(tmp_path / "missing")


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        ".",
        "./file.py",
        "/abs.py",
        "pkg/.",
        "../escape.py",
        "pkg/./app.py",
        "pkg/../escape.py",
        "bad\0name.py",
        "C:/x.py",
        "C:foo.py",
        "C:\\x.py",
        "pkg/C:/x.py",
        "pkg/C:foo.py",
        "pkg/D:/x.py",
        "pkg\\..\\x.py",
    ],
)
def test_validate_changed_files_rejects_unsafe_relative_paths(
    tmp_path: Path,
    bad_path: str,
) -> None:
    with pytest.raises(SecurityError):
        validate_changed_files(tmp_path, [bad_path])


def test_validate_changed_files_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside_target.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    (tmp_path / "link.py").symlink_to(outside)

    with pytest.raises(SecurityError):
        validate_changed_files(tmp_path, ["link.py"])


def test_validate_changed_files_returns_normalized_paths(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("value = 1\n", encoding="utf-8")

    result = validate_changed_files(tmp_path, ["pkg/app.py"])

    assert result == [Path("pkg/app.py")]
