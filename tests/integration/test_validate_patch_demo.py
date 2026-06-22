from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, cast

import agent_quality_mcp.service as service_module
from agent_quality_mcp.models import (
    AgentQualityConfig,
    SafetyMode,
    ValidatePatchRequest,
    ValidatePatchResponse,
    ValidationMode,
)
from agent_quality_mcp.shadow import ShadowWorkspaceContext
from agent_quality_mcp.shadow import create_shadow_workspace as real_create_shadow_workspace

FIXTURE_REPO = Path(__file__).resolve().parents[1] / "fixtures" / "demo_repo"


def test_validate_patch_runs_demo_fixture_in_shadow_workspace(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = tmp_path / "demo_repo"
    shutil.copytree(FIXTURE_REPO, workspace)

    def preserving_create_shadow_workspace(
        source_root: Path, config: AgentQualityConfig
    ) -> ShadowWorkspaceContext:
        preserved_config = config.model_copy(update={"preserve_shadow_workspace": True})
        return real_create_shadow_workspace(source_root, preserved_config)

    monkeypatch.setattr(
        service_module, "create_shadow_workspace", preserving_create_shadow_workspace
    )

    app_path = workspace / "demo_pkg" / "app.py"
    original_app = app_path.read_text(encoding="utf-8")
    original_fixture_app = (FIXTURE_REPO / "demo_pkg" / "app.py").read_text(encoding="utf-8")
    patch = (workspace / "patches" / "fix_value.diff").read_text(encoding="utf-8")

    response = service_module.validate_patch_service(
        ValidatePatchRequest(
            workspace_root=str(workspace),
            changed_files=["demo_pkg/app.py"],
            patch_unified_diff=patch,
            safety_mode=cast(SafetyMode, "preview_safe_fixes"),
            mode=cast(ValidationMode, "quick"),
        )
    )

    shadow_path_text = response.execution.shadow_workspace_path
    try:
        assert response.real_workspace_modified is False
        assert response.shadow_workspace_used is True
        assert response.execution.shadow_workspace_preserved is True
        assert shadow_path_text is not None
        shadow_path = Path(shadow_path_text)
        shadow_app = (shadow_path / "demo_pkg" / "app.py").read_text(encoding="utf-8")
        assert "return 2" in shadow_app
        assert response.execution.duration_ms >= 0
        assert response.risk_score.score >= 0
        assert (
            response.suggested_actions
            or response.warnings
            or response.info
            or response.blocking_errors
        )
        real_app = app_path.read_text(encoding="utf-8")
        assert real_app == original_app
        assert "return 1" in real_app
        assert (FIXTURE_REPO / "demo_pkg" / "app.py").read_text(
            encoding="utf-8"
        ) == original_fixture_app

        if response.execution.commands:
            assert all(Path(record.cwd) == shadow_path for record in response.execution.commands)
            assert all(Path(record.cwd) != workspace for record in response.execution.commands)

        for tool in ("uv", "ruff"):
            _assert_tool_recorded_or_structured_unavailable(response, tool)
        _assert_pyright_evidence_or_structured_unavailable(response)
    finally:
        if shadow_path_text is not None:
            shutil.rmtree(Path(shadow_path_text).parent)


def _assert_tool_recorded_or_structured_unavailable(
    response: ValidatePatchResponse, tool: str
) -> None:
    commands = response.execution.commands
    if any(command.command == tool for command in commands):
        return

    diagnostics = [*response.blocking_errors, *response.warnings, *response.info]
    assert any(
        diagnostic.source == "system"
        and diagnostic.code == "tool_unavailable"
        and diagnostic.metadata.get("tool") == tool
        for diagnostic in diagnostics
    ), f"{tool} produced neither a command record nor a structured unavailable diagnostic"


def _assert_pyright_evidence_or_structured_unavailable(
    response: ValidatePatchResponse,
) -> None:
    commands = response.execution.commands
    if any(command.command in {"pyright", "pyright-langserver"} for command in commands):
        return

    diagnostics = [*response.blocking_errors, *response.warnings, *response.info]
    if any(diagnostic.source == "pyright" for diagnostic in diagnostics):
        return

    assert any(
        diagnostic.source == "system"
        and diagnostic.code == "tool_unavailable"
        and diagnostic.metadata.get("tool") in {"pyright", "pyright-langserver"}
        for diagnostic in diagnostics
    ), "Pyright produced neither diagnostic evidence nor structured unavailable diagnostics"
