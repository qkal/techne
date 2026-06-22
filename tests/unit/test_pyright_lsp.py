from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from agent_quality_mcp.lsp.pyright import (
    lsp_uri_from_path,
    normalize_lsp_diagnostics,
    path_from_lsp_uri,
)
from agent_quality_mcp.models import DiagnosticSeverity


def test_lsp_uri_round_trips_path(tmp_path: Path) -> None:
    path = tmp_path / "pkg" / "space file.py"
    path.parent.mkdir()
    path.write_text("value = 1\n", encoding="utf-8")

    uri = lsp_uri_from_path(path)

    assert uri == path.resolve().as_uri()
    assert path_from_lsp_uri(uri) == path.resolve()


def test_normalize_lsp_diagnostics_maps_file_range_and_severity(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow"
    file_path = shadow_root / "pkg" / "module.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("print(missing)\n", encoding="utf-8")

    diagnostics = normalize_lsp_diagnostics(
        lsp_uri_from_path(file_path),
        [
            {
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 2, "character": 8},
                },
                "severity": 1,
                "code": "reportUndefinedVariable",
                "source": "pyright",
                "message": "Name is not defined",
            }
        ],
        shadow_root,
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "pyright"
    assert diagnostic.raw_source == "pyright_lsp"
    assert diagnostic.metadata == {"transport": "lsp"}
    assert diagnostic.code == "reportUndefinedVariable"
    assert diagnostic.message == "Name is not defined"
    assert diagnostic.file == "pkg/module.py"
    assert diagnostic.severity == DiagnosticSeverity.ERROR
    assert diagnostic.is_blocking is True
    assert diagnostic.range is not None
    assert diagnostic.range.start_line == 1
    assert diagnostic.range.start_column == 5
    assert diagnostic.range.end_line == 3
    assert diagnostic.range.end_column == 9


def test_normalize_lsp_diagnostics_rejects_uri_outside_shadow_root(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow"
    outside_file = tmp_path / "outside" / "module.py"
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("print(missing)\n", encoding="utf-8")

    diagnostics = normalize_lsp_diagnostics(
        lsp_uri_from_path(outside_file),
        [{"message": "Name is not defined", "severity": 1}],
        shadow_root,
    )

    assert diagnostics == []


def test_normalize_lsp_diagnostics_rejects_relative_file_uri(
    tmp_path: Path,
    monkeypatch,
) -> None:
    shadow_root = tmp_path / "shadow"
    shadow_root.mkdir()
    (shadow_root / "module.py").write_text("print(missing)\n", encoding="utf-8")
    monkeypatch.chdir(shadow_root)

    diagnostics = normalize_lsp_diagnostics(
        "file:module.py",
        [{"message": "Name is not defined", "severity": 1}],
        shadow_root,
    )

    assert diagnostics == []


def test_normalize_lsp_diagnostics_rejects_malformed_top_level(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow"
    file_path = shadow_root / "module.py"
    shadow_root.mkdir()
    file_path.write_text("print(missing)\n", encoding="utf-8")

    assert normalize_lsp_diagnostics(lsp_uri_from_path(file_path), None, shadow_root) == []


def test_normalize_lsp_diagnostics_omits_reversed_ranges(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow"
    file_path = shadow_root / "module.py"
    shadow_root.mkdir()
    file_path.write_text("print(missing)\n", encoding="utf-8")

    diagnostics = normalize_lsp_diagnostics(
        lsp_uri_from_path(file_path),
        [
            {
                "range": {
                    "start": {"line": 10, "character": 0},
                    "end": {"line": 0, "character": 0},
                },
                "message": "Name is not defined",
                "severity": 1,
            }
        ],
        shadow_root,
    )

    assert diagnostics[0].range is None


def test_normalize_lsp_diagnostics_rejects_non_string_uri(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow"
    shadow_root.mkdir()

    assert (
        normalize_lsp_diagnostics(cast(Any, 123), [{"message": "ignored"}], shadow_root)
        == []
    )


def test_normalize_lsp_diagnostics_sanitizes_unserializable_text(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    file_path = shadow_root / "module.py"
    shadow_root.mkdir()
    file_path.write_text("print(missing)\n", encoding="utf-8")

    diagnostics = normalize_lsp_diagnostics(
        lsp_uri_from_path(file_path),
        [
            {
                "code": "\ud800",
                "message": "\ud800",
                "severity": 1,
            }
        ],
        shadow_root,
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "pyright_lsp"
    assert diagnostic.message == "Pyright diagnostic"
    assert diagnostic.id.startswith("pyright-lsp-")
    diagnostic.model_dump_json()
