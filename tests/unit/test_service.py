from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import agent_quality_mcp.service as service_module
from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.exceptions import ConfigurationError
from agent_quality_mcp.models import (
    AgentQualityConfig,
    CommandExecutionRecord,
    Diagnostic,
    DiagnosticSeverity,
    SafeFixPreview,
    SafetyMode,
    ValidatePatchRequest,
    ValidationMode,
)
from agent_quality_mcp.service import inspect_workspace_service, validate_patch_service
from agent_quality_mcp.validators import (
    ValidatorCapability,
    ValidatorRequest,
    ValidatorResult,
)


class CleanUvAdapter:
    def __init__(self, runner: Any) -> None:
        self.runner = runner

    def check(self, cwd: Path, mode: str) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
        return [], []


class CleanRuffAdapter:
    def __init__(self, runner: Any) -> None:
        self.runner = runner

    def check(
        self,
        cwd: Path,
        changed_files: list[Path],
        mode: str,
        preview_safe_fixes: bool = False,
    ) -> tuple[list[Diagnostic], list[CommandExecutionRecord], list[SafeFixPreview]]:
        return [], [], []


class CleanPyrightAdapter:
    def __init__(self, runner: Any) -> None:
        self.runner = runner

    def check(
        self,
        cwd: Path,
        changed_files: list[Path],
        mode: str,
    ) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
        return [], []


def _write_python_file(root: Path, relative_path: str = "pkg/app.py") -> Path:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("value = 1\n", encoding="utf-8")
    return target


def _install_clean_adapters(monkeypatch: Any) -> None:
    monkeypatch.setattr(service_module, "UvAdapter", CleanUvAdapter)
    monkeypatch.setattr(service_module, "RuffAdapter", CleanRuffAdapter)
    monkeypatch.setattr(service_module, "PyrightAdapter", CleanPyrightAdapter)


def _fail_if_tools_run(monkeypatch: Any) -> None:
    class FailingAdapter:
        def __init__(self, runner: Any) -> None:
            raise AssertionError("tool adapters should not be constructed")

    monkeypatch.setattr(service_module, "UvAdapter", FailingAdapter)
    monkeypatch.setattr(service_module, "RuffAdapter", FailingAdapter)
    monkeypatch.setattr(service_module, "PyrightAdapter", FailingAdapter)


def _tool_unavailable(tool: str) -> Diagnostic:
    return diagnostic_from_message(
        source="system",
        code="tool_unavailable",
        message=f"{tool} is not available",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        metadata={"tool": tool},
    )


def test_validate_patch_rejects_apply_safe_fixes(tmp_path: Path, monkeypatch: Any) -> None:
    _write_python_file(tmp_path)
    _fail_if_tools_run(monkeypatch)
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        safety_mode=SafetyMode.APPLY_SAFE_FIXES,
    )

    response = validate_patch_service(request)

    assert response.status == "error"
    assert response.real_workspace_modified is False
    assert response.shadow_workspace_used is False
    assert response.blocking_errors[0].code == "apply_safe_fixes_not_supported"


def test_validate_patch_applies_patch_in_shadow_only(tmp_path: Path, monkeypatch: Any) -> None:
    target = _write_python_file(tmp_path)
    _install_clean_adapters(monkeypatch)
    patch = """--- a/pkg/app.py
+++ b/pkg/app.py
@@ -1 +1 @@
-value = 1
+value = 2
"""
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        patch_unified_diff=patch,
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert response.status == "passed"
    assert response.real_workspace_modified is False
    assert response.shadow_workspace_used is True


def test_validate_patch_uses_configured_defaults_for_omitted_mode_and_safety(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    captured: dict[str, Any] = {}

    class CaptureUvAdapter(CleanUvAdapter):
        def check(
            self,
            cwd: Path,
            mode: str,
        ) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
            captured["uv_mode"] = mode
            return [], []

    class CaptureRuffAdapter(CleanRuffAdapter):
        def check(
            self,
            cwd: Path,
            changed_files: list[Path],
            mode: str,
            preview_safe_fixes: bool = False,
        ) -> tuple[list[Diagnostic], list[CommandExecutionRecord], list[SafeFixPreview]]:
            captured["ruff_mode"] = mode
            captured["preview_safe_fixes"] = preview_safe_fixes
            return [], [], []

    class CapturePyrightAdapter(CleanPyrightAdapter):
        def check(
            self,
            cwd: Path,
            changed_files: list[Path],
            mode: str,
        ) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
            captured["pyright_mode"] = mode
            return [], []

    monkeypatch.setattr(service_module, "UvAdapter", CaptureUvAdapter)
    monkeypatch.setattr(service_module, "RuffAdapter", CaptureRuffAdapter)
    monkeypatch.setattr(service_module, "PyrightAdapter", CapturePyrightAdapter)
    monkeypatch.setattr(
        service_module,
        "load_config",
        lambda workspace_root, overrides=None: AgentQualityConfig(
            default_mode=ValidationMode.QUICK,
            default_safety_mode=SafetyMode.PREVIEW_SAFE_FIXES,
        ),
    )
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
    )

    response = validate_patch_service(request)

    assert response.mode == ValidationMode.QUICK
    assert response.safety_mode == SafetyMode.PREVIEW_SAFE_FIXES
    assert captured == {
        "uv_mode": "quick",
        "ruff_mode": "quick",
        "preview_safe_fixes": True,
        "pyright_mode": "quick",
    }


def test_validate_patch_enforces_request_timeout(tmp_path: Path, monkeypatch: Any) -> None:
    _write_python_file(tmp_path)
    _fail_if_tools_run(monkeypatch)
    monkeypatch.setattr(
        service_module,
        "load_config",
        lambda workspace_root, overrides=None: AgentQualityConfig(request_timeout_seconds=1),
    )
    monotonic_calls = {"count": 0}

    def fake_monotonic() -> float:
        monotonic_calls["count"] += 1
        if monotonic_calls["count"] == 1:
            return 0.0
        return 2.0

    monkeypatch.setattr("agent_quality_mcp.service.time.monotonic", fake_monotonic)

    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    assert response.status == "error"
    assert response.real_workspace_modified is False
    assert response.blocking_errors[0].code == "request_timeout"


def test_validate_patch_invalid_config_does_not_fallback_to_cwd(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_python_file(workspace)
    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()
    (unrelated_cwd / "pyproject.toml").write_text(
        """
[tool.agent_quality_mcp]
default_mode = "quick"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(unrelated_cwd)
    _fail_if_tools_run(monkeypatch)
    calls: list[Path] = []

    def fail_config(workspace_root: str | Path, overrides: dict[str, Any] | None = None) -> Any:
        calls.append(Path(workspace_root).resolve())
        raise ConfigurationError("invalid override")

    monkeypatch.setattr(service_module, "load_config", fail_config)
    request = ValidatePatchRequest(
        workspace_root=str(workspace),
        changed_files=["pkg/app.py"],
        config_overrides={"command_paths": {"ruff": str(workspace / "ruff")}},
    )

    response = validate_patch_service(request)

    assert response.status == "error"
    assert response.blocking_errors[0].code == "configuration_error"
    assert calls == [workspace.resolve()]


def test_validate_patch_config_rejection_does_not_leak_raw_error(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    _fail_if_tools_run(monkeypatch)
    raw_value = "raw-sk-review-token"
    raw_error = f"invalid config contains {raw_value}"

    def rejected_config(
        workspace_root: str | Path,
        overrides: dict[str, Any] | None = None,
    ) -> AgentQualityConfig:
        raise ConfigurationError(raw_error)

    monkeypatch.setattr(service_module, "load_config", rejected_config)
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        config_overrides={"secret_redaction_patterns": [raw_value]},
    )

    response = validate_patch_service(request)
    serialized = json.dumps(response.model_dump(mode="json"), allow_nan=False)

    assert response.status == "error"
    assert response.blocking_errors[0].code == "configuration_error"
    assert response.execution.commands == []
    assert raw_value not in serialized
    assert raw_error not in serialized
    assert "invalid config contains" not in serialized


def test_validate_patch_patch_error_does_not_run_tools(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    target = _write_python_file(tmp_path)
    _fail_if_tools_run(monkeypatch)
    patch = """--- a/pkg/app.py
+++ b/pkg/app.py
@@ -1 +1 @@
-different = 1
+value = 2
"""
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        patch_unified_diff=patch,
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert response.status == "error"
    assert response.real_workspace_modified is False
    assert response.shadow_workspace_used is True
    assert response.blocking_errors[0].source == "patch"


def test_validate_patch_path_validation_error_does_not_run_tools(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    _fail_if_tools_run(monkeypatch)
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["../escape.py"],
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    assert response.status == "error"
    assert response.real_workspace_modified is False
    assert response.shadow_workspace_used is False
    assert response.blocking_errors[0].source == "security"
    assert response.blocking_errors[0].code == "security_error"


def test_validate_patch_preserves_tool_unavailable_diagnostics(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)

    class UnavailableUvAdapter(CleanUvAdapter):
        def check(
            self,
            cwd: Path,
            mode: str,
        ) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
            return [_tool_unavailable("uv")], []

    class UnavailableRuffAdapter(CleanRuffAdapter):
        def check(
            self,
            cwd: Path,
            changed_files: list[Path],
            mode: str,
            preview_safe_fixes: bool = False,
        ) -> tuple[list[Diagnostic], list[CommandExecutionRecord], list[SafeFixPreview]]:
            return [_tool_unavailable("ruff")], [], []

    class UnavailablePyrightProvider:
        def validate(self, request: ValidatorRequest) -> ValidatorResult:
            del request
            return ValidatorResult(
                provider="pyright",
                capabilities=[ValidatorCapability.TYPE_DIAGNOSTICS],
                diagnostics=[_tool_unavailable("pyright")],
            )

    monkeypatch.setattr(service_module, "UvAdapter", UnavailableUvAdapter)
    monkeypatch.setattr(service_module, "RuffAdapter", UnavailableRuffAdapter)
    monkeypatch.setattr(
        service_module,
        "_build_pyright_provider",
        lambda runner: UnavailablePyrightProvider(),
    )
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    assert response.status == "passed"
    assert {warning.metadata["tool"] for warning in response.warnings} == {
        "uv",
        "ruff",
        "pyright",
    }
    assert response.execution.tool_availability == {
        "uv": False,
        "ruff": False,
        "pyright": False,
    }


def test_validate_patch_uses_pyright_lsp_provider_and_preserves_response_shape(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    captured: dict[str, object] = {}

    class StubPyrightProvider:
        def validate(self, request: ValidatorRequest) -> ValidatorResult:
            captured["real_workspace_root"] = request.real_workspace_root
            captured["shadow_workspace_root"] = request.shadow_workspace_root
            captured["mode"] = request.mode
            captured["scope"] = request.requested_scope
            return ValidatorResult(
                provider="pyright",
                capabilities=[ValidatorCapability.TYPE_DIAGNOSTICS],
                diagnostics=[],
                metadata={"fallback_to_cli": False},
            )

    monkeypatch.setattr(
        service_module,
        "_build_pyright_provider",
        lambda runner: StubPyrightProvider(),
    )
    monkeypatch.setattr(
        service_module,
        "UvAdapter",
        lambda runner: CleanUvAdapter(runner),
    )
    monkeypatch.setattr(
        service_module,
        "RuffAdapter",
        lambda runner: CleanRuffAdapter(runner),
    )

    response = validate_patch_service(
        ValidatePatchRequest(
            workspace_root=str(tmp_path),
            changed_files=["pkg/app.py"],
            mode=ValidationMode.QUICK,
        )
    )

    assert response.status == "passed"
    assert response.real_workspace_modified is False
    assert captured["real_workspace_root"] == tmp_path.resolve()
    assert captured["shadow_workspace_root"] != tmp_path.resolve()
    assert captured["mode"] == ValidationMode.QUICK


def test_validate_patch_includes_pyright_lsp_fallback_warning(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)

    class StubPyrightProvider:
        def validate(self, request: ValidatorRequest) -> ValidatorResult:
            return ValidatorResult(
                provider="pyright",
                capabilities=[ValidatorCapability.CLI_FALLBACK],
                diagnostics=[
                    diagnostic_from_message(
                        source="pyright",
                        code="lsp_fallback",
                        message=(
                            "Pyright LSP unavailable; falling back to CLI: "
                            "initialize failed"
                        ),
                        severity=DiagnosticSeverity.WARNING,
                        is_blocking=False,
                    )
                ],
                metadata={"fallback_to_cli": True},
                fallback_reason="initialize failed",
            )

    monkeypatch.setattr(
        service_module,
        "_build_pyright_provider",
        lambda runner: StubPyrightProvider(),
    )
    monkeypatch.setattr(
        service_module,
        "UvAdapter",
        lambda runner: CleanUvAdapter(runner),
    )
    monkeypatch.setattr(
        service_module,
        "RuffAdapter",
        lambda runner: CleanRuffAdapter(runner),
    )

    response = validate_patch_service(
        ValidatePatchRequest(
            workspace_root=str(tmp_path),
            changed_files=["pkg/app.py"],
            mode=ValidationMode.QUICK,
        )
    )

    assert response.status == "passed"
    assert response.warnings[0].code == "lsp_fallback"


def test_validate_patch_response_is_json_serializable(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    _install_clean_adapters(monkeypatch)
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    json.dumps(response.model_dump(mode="json"), allow_nan=False)


def test_inspect_workspace_service_returns_safe_metadata(tmp_path: Path) -> None:
    _write_python_file(tmp_path)

    response = inspect_workspace_service(str(tmp_path))

    assert response.workspace_root == str(tmp_path.resolve())
    assert response.python_file_count == 1
    assert response.config_files == []


def test_inspect_workspace_service_resolves_unavailable_commands_without_source(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")

    def unavailable(command: str, config: AgentQualityConfig, cwd: Path | None = None) -> str:
        raise service_module.ToolUnavailableError(f"{command} missing")

    monkeypatch.setattr(service_module, "resolve_allowed_command", unavailable)

    response = inspect_workspace_service(str(tmp_path))
    dumped = response.model_dump(mode="json")
    serialized = json.dumps(dumped, allow_nan=False)

    assert response.command_availability == {"uv": False, "ruff": False, "pyright": False}
    assert response.resolved_command_paths == {"uv": None, "ruff": None, "pyright": None}
    assert "value = 1" not in serialized
    assert "source_contents" not in serialized


def test_inspect_workspace_config_rejection_does_not_leak_raw_error(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    raw_value = "raw-sk-review-token"

    def rejected_config(
        workspace_root: str | Path,
        overrides: dict[str, Any] | None = None,
    ) -> AgentQualityConfig:
        raise ConfigurationError(f"invalid config contains {raw_value}")

    monkeypatch.setattr(service_module, "load_config", rejected_config)

    response = inspect_workspace_service(str(tmp_path))
    serialized = json.dumps(response.model_dump(mode="json"), allow_nan=False)

    assert raw_value not in serialized
    assert "invalid config contains" not in serialized
    assert "Configuration rejected; safe defaults used" in response.security_decisions


def test_inspect_workspace_sanitizes_accepted_config_string_lists(
    tmp_path: Path,
) -> None:
    _write_python_file(tmp_path)
    raw_value = "raw-sk-review-token"
    (tmp_path / raw_value).mkdir()
    (tmp_path / raw_value / "hidden.py").write_text("hidden = True\n", encoding="utf-8")

    response = inspect_workspace_service(
        str(tmp_path),
        config_overrides={
            "workspace_exclusions": [raw_value],
            "secret_file_patterns": [raw_value],
            "secret_redaction_patterns": [raw_value],
        },
    )
    serialized = json.dumps(response.model_dump(mode="json"), allow_nan=False)

    assert response.python_file_count == 1
    assert response.config.workspace_exclusions != [raw_value]
    assert response.config.secret_file_patterns != [raw_value]
    assert response.config.secret_redaction_patterns == []
    assert response.excluded_directories != [raw_value]
    assert raw_value not in serialized
