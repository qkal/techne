"""Shared MCP tool input validation helpers."""

from __future__ import annotations

from pydantic import ValidationError


def format_validation_error_summary(error: ValidationError, *, max_fields: int = 8) -> str:
    """Return a concise, agent-readable summary of Pydantic validation errors."""

    parts: list[str] = []
    for item in error.errors()[:max_fields]:
        location = ".".join(str(part) for part in item["loc"]) or "request"
        message = str(item.get("msg", "invalid value"))
        parts.append(f"{location}: {message}")
    if len(error.errors()) > max_fields:
        parts.append(f"... and {len(error.errors()) - max_fields} more field errors")
    return "; ".join(parts)


def sanitize_config_issue_message(message: str, *, max_length: int = 240) -> str:
    """Return a bounded configuration issue safe to expose to MCP clients."""

    safe_prefixes = (
        "Denied untrusted ",
        "Unsupported untrusted ",
        "Unable to read pyproject.toml",
        "[tool.agent_quality_mcp] must be a table",
    )
    if not any(message.startswith(prefix) for prefix in safe_prefixes):
        return "Configuration rejected"
    normalized = " ".join(message.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."
