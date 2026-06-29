from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agent_quality_mcp.exceptions import WorkspaceError
from agent_quality_mcp.models import ValidatePatchRequest, ValidationMode
from agent_quality_mcp.service import inspect_workspace_service, validate_patch_service

FIXTURE_REPO = Path(__file__).resolve().parents[1] / "fixtures" / "demo_repo"


@pytest.fixture
def demo_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "demo_repo"
    shutil.copytree(FIXTURE_REPO, workspace)
    return workspace


def test_inspect_workspace_service_reports_demo_fixture_metadata(
    demo_workspace: Path,
) -> None:
    response = inspect_workspace_service(str(demo_workspace))

    assert response.workspace_root == str(demo_workspace.resolve())
    assert response.python_file_count >= 1
    assert "pyproject.toml" in response.config_files
    assert response.security_decisions
    assert "metadata only" in response.security_decisions[0].lower()


def test_inspect_workspace_service_rejects_missing_directory() -> None:
    missing_root = "/path/that/does/not/exist/for-agent-quality-mcp"

    with pytest.raises(WorkspaceError):
        inspect_workspace_service(missing_root)


def test_validate_patch_rejects_denied_config_override_integration(
    demo_workspace: Path,
) -> None:
    response = validate_patch_service(
        ValidatePatchRequest(
            workspace_root=str(demo_workspace),
            changed_files=["demo_pkg/app.py"],
            mode=ValidationMode.QUICK,
            config_overrides={"max_patch_bytes": 999_999},
        )
    )

    assert response.decision == "request_human_review"
    assert response.evidence.real_workspace_modified is False
    assert response.blockers
    assert response.blockers[0].kind == "human_review"
