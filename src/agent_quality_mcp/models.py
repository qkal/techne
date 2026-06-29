"""Pydantic models for Agent Quality MCP requests and responses."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SECRET_REDACTION_PATTERNS = 32
MAX_SECRET_REDACTION_PATTERN_LENGTH = 500
SECRET_REDACTION_LITERAL_METACHARS = frozenset(r"()[]{}|*+?.^$\\")
DEFAULT_WORKSPACE_EXCLUSIONS = (
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".pyright",
    "dist",
    "build",
    ".tox",
    ".nox",
)
DEFAULT_SECRET_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
)


class ValidationMode(StrEnum):
    """Validation depth for validate_patch."""

    QUICK = "quick"
    STANDARD = "standard"
    STRICT = "strict"


class SafetyMode(StrEnum):
    """Permission mode for validation."""

    READ_ONLY = "read_only"
    PREVIEW_SAFE_FIXES = "preview_safe_fixes"
    APPLY_SAFE_FIXES = "apply_safe_fixes"


class DiagnosticSeverity(StrEnum):
    """Normalized diagnostic severity."""

    BLOCKER = "blocker"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ResponseStatus(StrEnum):
    """validate_patch response status."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class RiskLevel(StrEnum):
    """Risk level derived from a numeric score."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AgentQualityBaseModel(BaseModel):
    """Base model with strict assignment behavior."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CommandConfig(AgentQualityBaseModel):
    """Configured command paths for supported quality tools."""

    uv: str | None = None
    ruff: str | None = None
    pyright: str | None = None
    pyright_langserver: str | None = None


class AgentQualityConfig(AgentQualityBaseModel):
    """Runtime configuration for the quality gate."""

    command_paths: CommandConfig = Field(default_factory=CommandConfig)
    default_mode: ValidationMode = ValidationMode.STANDARD
    default_safety_mode: SafetyMode = SafetyMode.READ_ONLY
    request_timeout_seconds: int = Field(default=120, gt=0)
    subprocess_timeout_seconds: int = Field(default=30, gt=0)
    max_patch_bytes: int = Field(default=200_000, gt=0)
    max_changed_files: int = Field(default=50, gt=0)
    max_changed_file_bytes: int = Field(default=500_000, gt=0)
    max_workspace_copy_bytes: int = Field(default=50_000_000, gt=0)
    max_output_bytes: int = Field(default=20_000, gt=0)
    max_diagnostics: int = Field(default=200, gt=0)
    uv_offline: bool = True
    uv_sync_dry_run: bool = False
    preserve_shadow_workspace: bool = False
    workspace_exclusions: list[str] = Field(
        default_factory=lambda: list(DEFAULT_WORKSPACE_EXCLUSIONS)
    )
    secret_file_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SECRET_FILE_PATTERNS)
    )
    secret_redaction_patterns: list[str] = Field(default_factory=list)

    @field_validator("secret_redaction_patterns")
    @classmethod
    def validate_secret_redaction_patterns(cls, value: list[str]) -> list[str]:
        """Keep configured redaction patterns as deterministic literal tokens."""

        if len(value) > MAX_SECRET_REDACTION_PATTERNS:
            raise ValueError(
                "secret_redaction_patterns exceeds the maximum count "
                f"of {MAX_SECRET_REDACTION_PATTERNS}"
            )
        for pattern in value:
            if pattern == "":
                raise ValueError("secret_redaction_patterns entries must not be empty")
            if len(pattern) > MAX_SECRET_REDACTION_PATTERN_LENGTH:
                raise ValueError(
                    "secret_redaction_patterns entry exceeds the maximum length "
                    f"of {MAX_SECRET_REDACTION_PATTERN_LENGTH}"
                )
            metacharacters = sorted(set(pattern) & SECRET_REDACTION_LITERAL_METACHARS)
            if metacharacters:
                raise ValueError(
                    f"Invalid secret_redaction_patterns entry {pattern!r}: "
                    f"regex metacharacters are not allowed: {''.join(metacharacters)}"
                )
        return value


class ValidatePatchRequest(AgentQualityBaseModel):
    """Input accepted by the validate_patch MCP tool."""

    workspace_root: str
    changed_files: list[str]
    patch_unified_diff: str | None = None
    mode: ValidationMode | None = None
    safety_mode: SafetyMode | None = None
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    config_overrides: dict[str, Any] | None = None

    @field_validator("changed_files")
    @classmethod
    def require_changed_files(cls, value: list[str]) -> list[str]:
        """Ensure callers provide at least one changed file."""
        if not value:
            raise ValueError("changed_files must contain at least one path")
        return value


class InspectWorkspaceRequest(AgentQualityBaseModel):
    """Input accepted by the inspect_workspace MCP tool."""

    workspace_root: str
    config_overrides: dict[str, Any] | None = None


class DiagnosticRange(AgentQualityBaseModel):
    """Optional one-based source range for diagnostics."""

    start_line: int = Field(gt=0)
    start_column: int = Field(gt=0)
    end_line: int = Field(gt=0)
    end_column: int = Field(gt=0)


class Diagnostic(AgentQualityBaseModel):
    """Normalized diagnostic emitted by any validator."""

    id: str
    source: Literal["system", "security", "workspace", "patch", "uv", "ruff", "pyright"]
    severity: DiagnosticSeverity
    code: str
    message: str
    file: str | None = None
    range: DiagnosticRange | None = None
    is_blocking: bool
    is_fixable: bool = False
    raw_source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommandExecutionRecord(AgentQualityBaseModel):
    """Result of a subprocess invocation."""

    command: str
    args: list[str]
    cwd: str
    duration_ms: int
    exit_code: int | None
    timed_out: bool = False
    stdout_preview: str = ""
    stderr_preview: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class SafeFixPreview(AgentQualityBaseModel):
    """Preview of safe fixes produced without mutating the real workspace."""

    tool: str
    description: str
    files: list[str]
    diff_preview: str
    is_safe: bool
    requires_human_review: bool


class RiskScore(AgentQualityBaseModel):
    """Deterministic risk score."""

    score: int = Field(ge=0, le=100)
    level: RiskLevel
    factors: list[str] = Field(default_factory=list)


class ContextSummary(AgentQualityBaseModel):
    """Summary of diagnostic compression and truncation."""

    total_diagnostics: int = 0
    returned_diagnostics: int = 0
    compressed_groups: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False


class ExecutionMetadata(AgentQualityBaseModel):
    """Execution summary for a validation request."""

    duration_ms: int = 0
    shadow_workspace_path: str | None = None
    shadow_workspace_preserved: bool = False
    commands: list[CommandExecutionRecord] = Field(default_factory=list)
    tool_availability: dict[str, bool] = Field(default_factory=dict)
    timed_out: bool = False
    output_truncated: bool = False


class AuditSummary(AgentQualityBaseModel):
    """Audit summary safe to return to callers."""

    event_count: int = 0
    permission_decisions: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=list)
    resource_limit_decisions: list[str] = Field(default_factory=list)
    redactions_applied: int = 0


class InspectWorkspaceResponse(AgentQualityBaseModel):
    """Safe metadata returned by inspect_workspace."""

    workspace_root: str
    config: AgentQualityConfig
    command_availability: dict[str, bool]
    resolved_command_paths: dict[str, str | None]
    default_limits: dict[str, int]
    python_file_count: int
    config_files: list[str]
    excluded_directories: list[str]
    security_decisions: list[str]
