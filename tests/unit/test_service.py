from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import agent_quality_mcp.lsp.pyright as pyright_lsp_module
import agent_quality_mcp.service as service_module
from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.exceptions import ConfigurationError, ToolUnavailableError
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


def _record(command: str) -> CommandExecutionRecord:
    return CommandExecutionRecord(
        command=command,
        args=[command, "--version"],
        cwd="/tmp/shadow",  # noqa: S108 - fixed test shadow path sample.
        duration_ms=1,
        exit_code=0,
    )


class CleanUvAdapter:
    def __init__(self, runner: Any) -> None:
        self.runner = runner

    def check(self, cwd: Path, mode: str) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
        return [], [_record("uv")]


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
        return [], [_record("ruff")], []


class CleanPyrightAdapter:
    def __init__(self, runner: Any) -> None:
        self.runner = runner

    def check(
        self,
        cwd: Path,
        changed_files: list[Path],
        mode: str,
    ) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
        return [], [_record("pyright")]


class CleanPyrightProvider:
    def validate(self, request: ValidatorRequest) -> ValidatorResult:
        del request
        return ValidatorResult(
            provider="pyright",
            capabilities=[ValidatorCapability.TYPE_DIAGNOSTICS],
            commands=[_record("pyright")],
            metadata={"fallback_to_cli": False},
        )


class CleanPyrightLspSession:
    def is_healthy(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def collect_diagnostics(
        self,
        *,
        shadow_root: Path,
        changed_files: list[Path],
        scope: Any,
        timeout_seconds: float,
    ) -> tuple[dict[str, list[dict[str, object]]], None]:
        del shadow_root, changed_files, scope, timeout_seconds
        return {}, None

    def close_shadow_root(self, shadow_root: Path) -> None:
        del shadow_root


def _write_python_file(root: Path, relative_path: str = "pkg/app.py") -> Path:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("value = 1\n", encoding="utf-8")
    return target


def _install_clean_adapters(monkeypatch: Any) -> None:
    _install_clean_cli_adapters(monkeypatch)
    monkeypatch.setattr(
        service_module,
        "_build_pyright_provider",
        lambda runner: CleanPyrightProvider(),
    )


def _install_clean_cli_adapters(monkeypatch: Any) -> None:
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
    monkeypatch.setattr(
        service_module,
        "_build_pyright_provider",
        lambda runner: FailingAdapter(runner),
    )


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

    assert response.decision == "reject_request"
    assert response.evidence.real_workspace_modified is False
    assert response.evidence.shadow_workspace_used is False
    assert response.blockers[0].kind == "request"


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
    assert response.decision == "apply_patch"
    assert response.evidence.real_workspace_modified is False
    assert response.evidence.shadow_workspace_used is True
    assert response.evidence.command_outcomes == [
        {
            "command": "uv",
            "exit_code": 0,
            "timed_out": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
        },
        {
            "command": "ruff",
            "exit_code": 0,
            "timed_out": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
        },
        {
            "command": "pyright",
            "exit_code": 0,
            "timed_out": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
        },
    ]
    required_checks = {
        check["tool"]: check for check in response.model_dump(mode="json")["evidence"][
            "required_checks"
        ]
    }
    assert required_checks["uv"]["required"] is False
    assert required_checks["ruff"]["required"] is True
    assert required_checks["ruff"]["completed"] is True
    assert required_checks["pyright"]["required"] is True
    assert required_checks["pyright"]["completed"] is True


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
            return [], [_record("uv")]

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
            return [], [_record("ruff")], []

    class CapturePyrightProvider:
        def validate(self, request: ValidatorRequest) -> ValidatorResult:
            captured["pyright_mode"] = request.mode.value
            return ValidatorResult(
                provider="pyright",
                capabilities=[ValidatorCapability.TYPE_DIAGNOSTICS],
                commands=[_record("pyright")],
            )

    monkeypatch.setattr(service_module, "UvAdapter", CaptureUvAdapter)
    monkeypatch.setattr(service_module, "RuffAdapter", CaptureRuffAdapter)
    monkeypatch.setattr(
        service_module,
        "_build_pyright_provider",
        lambda runner: CapturePyrightProvider(),
    )
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

    assert response.decision == "request_human_review"
    assert response.evidence.real_workspace_modified is False
    assert response.blockers[0].kind == "timeout"


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

    assert response.decision == "request_human_review"
    assert response.blockers[0].kind == "human_review"
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

    assert response.decision == "request_human_review"
    assert response.blockers[0].kind == "human_review"
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
    assert response.decision == "revise_patch"
    assert response.evidence.real_workspace_modified is False
    assert response.evidence.shadow_workspace_used is True
    assert response.blockers[0].kind == "patch"


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

    assert response.decision == "reject_request"
    assert response.evidence.real_workspace_modified is False
    assert response.evidence.shadow_workspace_used is False
    assert response.blockers[0].kind == "security"


def test_validate_patch_changed_file_count_limit_is_request_blocker(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path, "pkg/one.py")
    _write_python_file(tmp_path, "pkg/two.py")
    _fail_if_tools_run(monkeypatch)
    monkeypatch.setattr(
        service_module,
        "load_config",
        lambda workspace_root, overrides=None: AgentQualityConfig(max_changed_files=1),
    )
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/one.py", "pkg/two.py"],
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    assert response.decision == "reject_request"
    assert response.blockers[0].kind == "request"
    assert response.evidence.real_workspace_modified is False
    assert response.evidence.shadow_workspace_used is False


def test_validate_patch_patch_size_limit_is_request_blocker(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    _fail_if_tools_run(monkeypatch)
    monkeypatch.setattr(
        service_module,
        "load_config",
        lambda workspace_root, overrides=None: AgentQualityConfig(max_patch_bytes=10),
    )
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        patch_unified_diff="x" * 11,
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    assert response.decision == "reject_request"
    assert response.blockers[0].kind == "request"
    assert response.evidence.real_workspace_modified is False
    assert response.evidence.shadow_workspace_used is False


def test_validate_patch_preserves_truncated_diagnostic_context(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)

    class NoopUvAdapter(CleanUvAdapter):
        pass

    class DiagnosticRuffAdapter(CleanRuffAdapter):
        def check(
            self,
            cwd: Path,
            changed_files: list[Path],
            mode: str,
            preview_safe_fixes: bool = False,
        ) -> tuple[list[Diagnostic], list[CommandExecutionRecord], list[SafeFixPreview]]:
            return [
                diagnostic_from_message(
                    source="ruff",
                    code="F401",
                    message="Unused import",
                    severity=DiagnosticSeverity.WARNING,
                    is_blocking=False,
                    file="pkg/app.py",
                ),
                diagnostic_from_message(
                    source="ruff",
                    code="E501",
                    message="Line too long",
                    severity=DiagnosticSeverity.WARNING,
                    is_blocking=False,
                    file="pkg/app.py",
                ),
            ], [_record("ruff")], []

    monkeypatch.setattr(service_module, "UvAdapter", NoopUvAdapter)
    monkeypatch.setattr(service_module, "RuffAdapter", DiagnosticRuffAdapter)
    monkeypatch.setattr(
        service_module,
        "_build_pyright_provider",
        lambda runner: CleanPyrightProvider(),
    )
    monkeypatch.setattr(
        service_module,
        "load_config",
        lambda workspace_root, overrides=None: AgentQualityConfig(max_diagnostics=1),
    )
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    assert response.evidence.diagnostic_count == 2
    assert response.evidence.total_diagnostic_count == 2
    assert response.evidence.returned_diagnostic_count == 1
    assert response.evidence.diagnostics_truncated is True


def test_validate_patch_preserves_truncated_missing_tool_evidence(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)

    class NoopUvAdapter(CleanUvAdapter):
        pass

    class DiagnosticRuffAdapter(CleanRuffAdapter):
        def check(
            self,
            cwd: Path,
            changed_files: list[Path],
            mode: str,
            preview_safe_fixes: bool = False,
        ) -> tuple[list[Diagnostic], list[CommandExecutionRecord], list[SafeFixPreview]]:
            return [
                diagnostic_from_message(
                    source="ruff",
                    code="F401",
                    message="Unused import",
                    severity=DiagnosticSeverity.WARNING,
                    is_blocking=False,
                    file="pkg/app.py",
                )
            ], [_record("ruff")], []

    class UnavailablePyrightProvider:
        def validate(self, request: ValidatorRequest) -> ValidatorResult:
            del request
            return ValidatorResult(
                provider="pyright",
                capabilities=[ValidatorCapability.TYPE_DIAGNOSTICS],
                diagnostics=[_tool_unavailable("pyright")],
            )

    monkeypatch.setattr(service_module, "UvAdapter", NoopUvAdapter)
    monkeypatch.setattr(service_module, "RuffAdapter", DiagnosticRuffAdapter)
    monkeypatch.setattr(
        service_module,
        "_build_pyright_provider",
        lambda runner: UnavailablePyrightProvider(),
    )
    monkeypatch.setattr(
        service_module,
        "load_config",
        lambda workspace_root, overrides=None: AgentQualityConfig(max_diagnostics=1),
    )
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    assert response.decision == "fix_tooling"
    assert response.evidence.diagnostics_truncated is True
    assert response.evidence.tool_availability["pyright"] is False
    required_checks = {
        check["tool"]: check for check in response.model_dump(mode="json")["evidence"][
            "required_checks"
        ]
    }
    assert required_checks["pyright"]["reason"] == "pyright is unavailable"
    assert any(blocker.kind == "tooling" for blocker in response.blockers)


def test_validate_patch_ignores_optional_quick_uv_unavailable(
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

    monkeypatch.setattr(service_module, "UvAdapter", UnavailableUvAdapter)
    monkeypatch.setattr(service_module, "RuffAdapter", CleanRuffAdapter)
    monkeypatch.setattr(
        service_module,
        "_build_pyright_provider",
        lambda runner: CleanPyrightProvider(),
    )
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    assert response.decision == "apply_patch"
    assert response.evidence.tool_availability["uv"] is False
    required_checks = {
        check["tool"]: check for check in response.model_dump(mode="json")["evidence"][
            "required_checks"
        ]
    }
    assert required_checks["uv"]["required"] is False
    assert required_checks["uv"]["completed"] is False
    assert all(blocker.kind != "tooling" for blocker in response.blockers)


def test_validate_patch_workspace_copy_limit_is_request_blocker(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    _fail_if_tools_run(monkeypatch)
    monkeypatch.setattr(
        service_module,
        "load_config",
        lambda workspace_root, overrides=None: AgentQualityConfig(max_workspace_copy_bytes=1),
    )
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        mode=ValidationMode.QUICK,
    )

    response = validate_patch_service(request)

    assert response.decision == "reject_request"
    assert response.blockers[0].kind == "request"
    assert response.evidence.real_workspace_modified is False
    assert response.evidence.shadow_workspace_used is False


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

    assert response.decision == "fix_tooling"
    assert response.evidence.tool_availability == {
        "uv": False,
        "ruff": False,
        "pyright": False,
        "pyright-langserver": True,
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

    assert response.decision == "apply_patch"
    assert response.evidence.real_workspace_modified is False
    assert response.evidence.command_outcomes[-1]["command"] == "pyright"
    required_checks = {
        check["tool"]: check for check in response.model_dump(mode="json")["evidence"][
            "required_checks"
        ]
    }
    assert required_checks["pyright"]["completed"] is True
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
                commands=[_record("pyright")],
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

    assert response.decision == "apply_patch"
    assert response.summary.warning_count == 1
    assert response.blockers == []
    assert response.evidence.command_outcomes[-1]["command"] == "pyright"


def test_validate_patch_reuses_pyright_lsp_session_across_config_loads(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    _install_clean_cli_adapters(monkeypatch)
    started: list[Path] = []

    def fake_start(
        real_workspace_root: Path,
        config: AgentQualityConfig,
    ) -> CleanPyrightLspSession:
        del config
        started.append(real_workspace_root)
        return CleanPyrightLspSession()

    monkeypatch.setattr(pyright_lsp_module, "_start_process_session", fake_start)
    request = ValidatePatchRequest(
        workspace_root=str(tmp_path),
        changed_files=["pkg/app.py"],
        mode=ValidationMode.QUICK,
    )

    first_response = validate_patch_service(request)
    second_response = validate_patch_service(request)

    assert first_response.decision == "apply_patch"
    assert second_response.decision == "apply_patch"
    assert started == [tmp_path.resolve()]


def test_validate_patch_marks_pyright_langserver_unavailable_when_lsp_start_fails(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    _install_clean_cli_adapters(monkeypatch)

    def fake_start(
        real_workspace_root: Path,
        config: AgentQualityConfig,
    ) -> CleanPyrightLspSession:
        del real_workspace_root, config
        raise ToolUnavailableError("Unable to resolve required tool: pyright-langserver")

    monkeypatch.setattr(pyright_lsp_module, "_start_process_session", fake_start)
    response = validate_patch_service(
        ValidatePatchRequest(
            workspace_root=str(tmp_path),
            changed_files=["pkg/app.py"],
            mode=ValidationMode.QUICK,
        )
    )

    assert response.decision == "apply_patch"
    assert response.evidence.tool_availability["pyright"] is True
    assert response.evidence.tool_availability["pyright-langserver"] is False
    assert response.summary.warning_count == 2


def test_validate_patch_marks_both_pyright_tools_unavailable_when_lsp_and_cli_fail(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    _write_python_file(tmp_path)
    monkeypatch.setattr(service_module, "UvAdapter", CleanUvAdapter)
    monkeypatch.setattr(service_module, "RuffAdapter", CleanRuffAdapter)

    class UnavailablePyrightAdapter:
        def __init__(self, runner: Any) -> None:
            del runner

        def check(
            self,
            cwd: Path,
            changed_files: list[Path],
            mode: str,
        ) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
            del cwd, changed_files, mode
            raise ToolUnavailableError("Unable to resolve required tool: pyright")

    def fake_start(
        real_workspace_root: Path,
        config: AgentQualityConfig,
    ) -> CleanPyrightLspSession:
        del real_workspace_root, config
        raise ToolUnavailableError("Unable to resolve required tool: pyright-langserver")

    monkeypatch.setattr(service_module, "PyrightAdapter", UnavailablePyrightAdapter)
    monkeypatch.setattr(pyright_lsp_module, "_start_process_session", fake_start)
    response = validate_patch_service(
        ValidatePatchRequest(
            workspace_root=str(tmp_path),
            changed_files=["pkg/app.py"],
            mode=ValidationMode.QUICK,
        )
    )

    assert response.decision == "fix_tooling"
    assert response.evidence.tool_availability["pyright"] is False
    assert response.evidence.tool_availability["pyright-langserver"] is False
    required_checks = {
        check["tool"]: check for check in response.model_dump(mode="json")["evidence"][
            "required_checks"
        ]
    }
    assert required_checks["pyright"]["reason"] == "pyright is unavailable"


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

    assert response.command_availability == {
        "uv": False,
        "ruff": False,
        "pyright": False,
        "pyright-langserver": False,
    }
    assert response.resolved_command_paths == {
        "uv": None,
        "ruff": None,
        "pyright": None,
        "pyright-langserver": None,
    }
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
