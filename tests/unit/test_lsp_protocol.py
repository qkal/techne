from __future__ import annotations

import json

import pytest

from agent_quality_mcp.lsp.protocol import LspFramer, LspProtocolError, build_lsp_message


def test_build_lsp_message_adds_content_length_header_and_compact_json_body() -> None:
    message = {
        "jsonrpc": "2.0",
        "method": "$/test",
        "params": {"label": "caf\u00e9", "items": [1, 2]},
    }

    encoded = build_lsp_message(message)

    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert encoded == f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def test_lsp_framer_waits_for_complete_messages_across_chunks() -> None:
    framer = LspFramer(max_message_bytes=1024)
    message = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    encoded = build_lsp_message(message)
    split_at = encoded.index(b"\r\n\r\n") + 5

    assert framer.feed(encoded[:split_at]) == []
    assert framer.feed(encoded[split_at:]) == [message]


def test_lsp_framer_parses_multiple_messages_from_one_byte_stream() -> None:
    framer = LspFramer(max_message_bytes=1024)
    first = {"jsonrpc": "2.0", "id": 1, "result": None}
    second = {"jsonrpc": "2.0", "method": "window/logMessage", "params": {"type": 3}}

    messages = framer.feed(build_lsp_message(first) + build_lsp_message(second))

    assert messages == [first, second]


def test_lsp_framer_rejects_oversized_messages_before_waiting_for_body() -> None:
    framer = LspFramer(max_message_bytes=5)

    with pytest.raises(LspProtocolError, match="exceeds maximum"):
        framer.feed(b"Content-Length: 6\r\n\r\n")


def test_lsp_framer_rejects_malformed_json() -> None:
    framer = LspFramer(max_message_bytes=1024)

    with pytest.raises(LspProtocolError, match="invalid JSON"):
        framer.feed(b"Content-Length: 1\r\n\r\n{")
