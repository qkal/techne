"""Typed exceptions for Agent Quality MCP."""


class AgentQualityMcpError(Exception):
    """Base class for all Agent Quality MCP errors."""


class ConfigurationError(AgentQualityMcpError):
    """Raised when configuration cannot be loaded or validated."""


class SecurityError(AgentQualityMcpError):
    """Raised when input violates the security model."""


class WorkspaceError(AgentQualityMcpError):
    """Raised when workspace resolution or inspection fails."""


class PatchApplyError(AgentQualityMcpError):
    """Raised when a unified diff cannot be safely applied."""


class ResourceLimitError(WorkspaceError):
    """Raised when request data exceeds configured resource limits."""


class CommandExecutionError(AgentQualityMcpError):
    """Raised when a command cannot be safely resolved or executed."""


class ToolUnavailableError(CommandExecutionError):
    """Raised when an allowed quality tool cannot be resolved."""
