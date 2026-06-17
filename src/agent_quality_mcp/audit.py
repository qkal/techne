"""Audit and redaction helpers for Agent Quality MCP."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from agent_quality_mcp.models import AgentQualityConfig, AuditSummary

LOGGER = logging.getLogger("agent_quality_mcp.audit")
SECRET_KEY_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|credential|auth|bearer)"
)
DEFAULT_SECRET_TEXT_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]+"),
    re.compile(r"\bghp_[A-Za-z0-9_]+"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"),
    re.compile(r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"(?i)\b(api[_-]?key|password|secret|token)\s+\S+"),
)


def redact_text(text: str, config: AgentQualityConfig) -> str:
    """Redact secret-like values from text using configured patterns."""

    redacted = text
    for pattern in DEFAULT_SECRET_TEXT_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    for literal in config.secret_redaction_patterns:
        redacted = redacted.replace(literal, "[REDACTED]")
    return redacted


def truncate_text(text: str, max_bytes: int) -> tuple[str, bool]:
    """Return text truncated by UTF-8 byte length."""

    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated + "\n[TRUNCATED]", True


def _redacted_metadata_key(redaction_count: int) -> str:
    """Return a generic metadata key for redacted secret-like keys."""

    return f"redacted_metadata_{redaction_count}"


def _redact_value(value: Any, config: AgentQualityConfig) -> Any:
    """Redact strings in audit payloads while preserving basic structure."""

    if isinstance(value, str):
        return redact_text(value, config)
    if isinstance(value, list):
        return [_redact_value(item, config) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, config) for item in value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        redaction_count = 0
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEY_PATTERN.search(key_text):
                redaction_count += 1
                redacted[_redacted_metadata_key(redaction_count)] = "[REDACTED]"
            else:
                redacted[key_text] = _redact_value(item, config)
        return redacted
    return value


@dataclass
class AuditRecorder:
    """Collect structured audit events for a request."""

    request_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    permission_decisions: list[str] = field(default_factory=list)
    denied_paths: list[str] = field(default_factory=list)
    resource_limit_decisions: list[str] = field(default_factory=list)
    redaction_config: AgentQualityConfig = field(default_factory=AgentQualityConfig)

    def event(self, event_kind: str, event_message: str, **metadata: Any) -> None:
        """Record and log a structured audit event."""

        redacted_metadata = _redact_value(metadata, self.redaction_config)
        payload = {
            "request_id": self.request_id,
            "kind": event_kind,
            "message": event_message,
            "metadata": redacted_metadata,
        }
        redacted_payload = _redact_value(payload, self.redaction_config)
        self.events.append(redacted_payload)
        LOGGER.info("%s", redacted_payload)

    def permission(self, message: str) -> None:
        """Record a permission decision."""

        redacted_message = redact_text(message, self.redaction_config)
        self.permission_decisions.append(redacted_message)
        self.event("permission", redacted_message)

    def denied_path(self, path: str) -> None:
        """Record a denied path without reading file contents."""

        redacted_path = redact_text(path, self.redaction_config)
        self.denied_paths.append(redacted_path)
        self.event("denied_path", "path denied", path=redacted_path)

    def resource_limit(self, message: str) -> None:
        """Record a resource-limit decision."""

        redacted_message = redact_text(message, self.redaction_config)
        self.resource_limit_decisions.append(redacted_message)
        self.event("resource_limit", redacted_message)

    def summary(self, redactions_applied: int = 0) -> AuditSummary:
        """Return a response-safe audit summary."""

        return AuditSummary(
            event_count=len(self.events),
            permission_decisions=list(self.permission_decisions),
            denied_paths=list(self.denied_paths),
            resource_limit_decisions=list(self.resource_limit_decisions),
            redactions_applied=redactions_applied,
        )
