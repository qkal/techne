import logging

from agent_quality_mcp.audit import AuditRecorder, redact_text
from agent_quality_mcp.models import AgentQualityConfig


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_redact_text_removes_secret_like_values() -> None:
    config = AgentQualityConfig()
    text = "token=super-secret-value sk-testvalue ghp_testvalue"

    redacted = redact_text(text, config)

    assert "super-secret-value" not in redacted
    assert "sk-testvalue" not in redacted
    assert "ghp_testvalue" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_text_removes_builtin_secrets_with_empty_configured_patterns() -> None:
    config = AgentQualityConfig(secret_redaction_patterns=[])

    redacted = redact_text("sk-testvalue ghp_testvalue", config)

    assert "sk-testvalue" not in redacted
    assert "ghp_testvalue" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_redact_text_removes_literal_configured_patterns() -> None:
    config = AgentQualityConfig(secret_redaction_patterns=["internal-secret-marker"])

    redacted = redact_text("prefix internal-secret-marker suffix", config)

    assert "internal-secret-marker" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_text_removes_common_secret_log_formats() -> None:
    config = AgentQualityConfig()
    text = "\n".join(
        [
            "Authorization: Bearer abc123",
            "api_key: plainvalue",
            "password: hunter2",
            "token whitespacevalue",
        ]
    )

    redacted = redact_text(text, config)

    assert "abc123" not in redacted
    assert "plainvalue" not in redacted
    assert "hunter2" not in redacted
    assert "whitespacevalue" not in redacted
    assert redacted.count("[REDACTED]") == 4


def test_redact_text_removes_bare_bearer_token() -> None:
    redacted = redact_text("Bearer abc123", AgentQualityConfig())

    assert "abc123" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_text_removes_whitespace_labeled_secret_values() -> None:
    redacted = redact_text(
        "secret abc123 api_key abc456 password abc789",
        AgentQualityConfig(),
    )

    assert "abc123" not in redacted
    assert "abc456" not in redacted
    assert "abc789" not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_audit_recorder_tracks_decisions_without_patch_content() -> None:
    recorder = AuditRecorder(request_id="req-1")

    recorder.permission("allowed read-only validation")
    recorder.denied_path("../secret.py")
    recorder.resource_limit("max_patch_bytes checked")
    summary = recorder.summary(redactions_applied=2)

    assert summary.event_count == 3
    assert summary.denied_paths == ["../secret.py"]
    assert summary.redactions_applied == 2


def test_audit_recorder_redacts_secret_like_metadata_keys() -> None:
    logger = logging.getLogger("agent_quality_mcp.audit")
    handler = _ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        recorder = AuditRecorder(request_id="req-1")

        recorder.event("test", "message", api_key="plainvalue")
    finally:
        logger.removeHandler(handler)

    stored_event = recorder.events[0]
    stored_payload = str(recorder.events)
    logged_payload = "\n".join(handler.messages)

    assert "api_key" not in stored_payload
    assert "plainvalue" not in stored_payload
    assert "api_key" not in logged_payload
    assert "plainvalue" not in logged_payload
    assert stored_event["metadata"]["redacted_metadata_1"] == "[REDACTED]"
    assert "redacted_metadata_1" in logged_payload


def test_audit_recorder_metadata_cannot_override_reserved_fields() -> None:
    recorder = AuditRecorder(request_id="req-1")

    recorder.event("test", "message", request_id="evil", kind="evil", message="evil")

    stored_event = recorder.events[0]
    assert stored_event["request_id"] == "req-1"
    assert stored_event["kind"] == "test"
    assert stored_event["message"] == "message"
    assert stored_event["metadata"] == {
        "request_id": "evil",
        "kind": "evil",
        "message": "evil",
    }
