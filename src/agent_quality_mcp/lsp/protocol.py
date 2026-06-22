from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any, cast

_HEADER_DELIMITER = b"\r\n\r\n"
_MAX_HEADER_BYTES = 8192


class LspProtocolError(RuntimeError):
    """Raised when an LSP byte stream cannot be framed or decoded."""


def build_lsp_message(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


class LspFramer:
    def __init__(self, max_message_bytes: int) -> None:
        self._max_message_bytes = max_message_bytes
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer.extend(data)
        messages: list[dict[str, Any]] = []

        while True:
            header_end = self._buffer.find(_HEADER_DELIMITER)
            if header_end == -1:
                if len(self._buffer) > _MAX_HEADER_BYTES:
                    raise LspProtocolError(
                        f"LSP header exceeds maximum {_MAX_HEADER_BYTES} bytes"
                    )
                return messages
            if header_end > _MAX_HEADER_BYTES:
                raise LspProtocolError(f"LSP header exceeds maximum {_MAX_HEADER_BYTES} bytes")

            content_length = _parse_content_length(bytes(self._buffer[:header_end]))
            if content_length > self._max_message_bytes:
                raise LspProtocolError(
                    f"LSP message length {content_length} exceeds maximum "
                    f"{self._max_message_bytes}"
                )

            body_start = header_end + len(_HEADER_DELIMITER)
            body_end = body_start + content_length
            if len(self._buffer) < body_end:
                return messages

            body = bytes(self._buffer[body_start:body_end])
            del self._buffer[:body_end]
            messages.append(_decode_message(body))


def _parse_content_length(header_bytes: bytes) -> int:
    try:
        header_text = header_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise LspProtocolError("invalid LSP header encoding") from exc

    content_length: int | None = None
    for line in header_text.split("\r\n"):
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            raise LspProtocolError("invalid LSP header")
        if name.lower() != "content-length":
            continue
        if content_length is not None:
            raise LspProtocolError("duplicate Content-Length header")
        value = value.strip()
        if value.startswith("-"):
            raise LspProtocolError("negative Content-Length")
        if not value.isdecimal():
            raise LspProtocolError("non-integer Content-Length")
        try:
            content_length = int(value)
        except ValueError as exc:
            raise LspProtocolError("Content-Length is too large") from exc

    if content_length is None:
        raise LspProtocolError("missing Content-Length header")
    return content_length


def _decode_message(body: bytes) -> dict[str, Any]:
    try:
        message = json.loads(body.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise LspProtocolError("invalid UTF-8 JSON body") from exc
    except JSONDecodeError as exc:
        raise LspProtocolError("invalid JSON message body") from exc

    if not isinstance(message, dict):
        raise LspProtocolError("LSP message must decode to a JSON object")
    return cast(dict[str, Any], message)
