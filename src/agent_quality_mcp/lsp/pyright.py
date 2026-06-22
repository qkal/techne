"""Normalize Pyright Language Server Protocol diagnostics."""

from __future__ import annotations

import hashlib
import json
import os
import select
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import unquote, urlparse

from agent_quality_mcp.cli.runner import start_long_running_command
from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.exceptions import CommandExecutionError
from agent_quality_mcp.lsp.protocol import LspFramer, LspProtocolError, build_lsp_message
from agent_quality_mcp.models import (
    AgentQualityConfig,
    CommandExecutionRecord,
    Diagnostic,
    DiagnosticRange,
    DiagnosticSeverity,
)
from agent_quality_mcp.validators import (
    ValidatorCapability,
    ValidatorRequest,
    ValidatorResult,
    ValidatorScope,
)

RawLspDiagnostics = dict[str, list[dict[str, object]]]
_SHADOW_CLEANUP_TIMEOUT_SECONDS = 1.0


def lsp_uri_from_path(path: Path) -> str:
    """Return a local file URI for a filesystem path."""

    return path.resolve().as_uri()


def path_from_lsp_uri(uri: str) -> Path:
    """Return a resolved local path from a ``file://`` LSP document URI."""

    if not isinstance(uri, str):
        raise ValueError("LSP URI must be a string")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"} or not parsed.path:
        raise ValueError("LSP URI must be a local file URI")
    if parsed.query or parsed.fragment:
        raise ValueError("LSP URI must not include query or fragment components")
    path = Path(unquote(parsed.path))
    if not path.is_absolute():
        raise ValueError("LSP file URI path must be absolute")
    return path.resolve()


def normalize_lsp_diagnostics(
    uri: str,
    raw_diagnostics: Any,
    shadow_root: Path,
) -> list[Diagnostic]:
    """Normalize Pyright LSP diagnostics for one shadow-workspace document."""

    try:
        file_path = path_from_lsp_uri(uri)
        shadow_root_resolved = shadow_root.resolve()
        relative_file = file_path.relative_to(shadow_root_resolved).as_posix()
    except (OSError, ValueError):
        return []

    diagnostics: list[Diagnostic] = []
    if not isinstance(raw_diagnostics, list):
        return diagnostics

    for item in raw_diagnostics:
        if not isinstance(item, dict):
            continue

        severity, is_blocking = _lsp_severity(item.get("severity"))
        code = _string_or_default(item.get("code"), "pyright_lsp")
        message = _string_or_default(item.get("message"), "Pyright diagnostic")
        diagnostic_range = _lsp_range(item.get("range"))
        metadata = {"transport": "lsp"}

        diagnostics.append(
            Diagnostic(
                id=_lsp_diagnostic_id(
                    code=code,
                    message=message,
                    severity=severity,
                    is_blocking=is_blocking,
                    file=relative_file,
                    diagnostic_range=diagnostic_range,
                    metadata=metadata,
                ),
                source="pyright",
                severity=severity,
                code=code,
                message=message,
                file=relative_file,
                range=diagnostic_range,
                is_blocking=is_blocking,
                raw_source="pyright_lsp",
                metadata=metadata,
            )
        )

    return diagnostics


def _lsp_diagnostic_id(
    *,
    code: str,
    message: str,
    severity: DiagnosticSeverity,
    is_blocking: bool,
    file: str,
    diagnostic_range: DiagnosticRange | None,
    metadata: dict[str, str],
) -> str:
    payload = {
        "source": "pyright",
        "code": code,
        "message": message,
        "severity": severity.value,
        "is_blocking": is_blocking,
        "file": file,
        "range": diagnostic_range.model_dump() if diagnostic_range is not None else None,
        "metadata": metadata,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"pyright-lsp-{digest}"


def _lsp_severity(raw_severity: Any) -> tuple[DiagnosticSeverity, bool]:
    if isinstance(raw_severity, bool) or not isinstance(raw_severity, int):
        return DiagnosticSeverity.WARNING, False
    if raw_severity == 1:
        return DiagnosticSeverity.ERROR, True
    if raw_severity == 2:
        return DiagnosticSeverity.WARNING, False
    if raw_severity in {3, 4}:
        return DiagnosticSeverity.INFO, False
    return DiagnosticSeverity.WARNING, False


def _lsp_range(raw_range: Any) -> DiagnosticRange | None:
    if not isinstance(raw_range, dict):
        return None

    start = raw_range.get("start")
    end = raw_range.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None

    start_line = _zero_based_lsp_int(start.get("line"))
    start_column = _zero_based_lsp_int(start.get("character"))
    end_line = _zero_based_lsp_int(end.get("line"))
    end_column = _zero_based_lsp_int(end.get("character"))
    if (
        start_line is None
        or start_column is None
        or end_line is None
        or end_column is None
    ):
        return None
    if (end_line, end_column) < (start_line, start_column):
        return None

    return DiagnosticRange(
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
    )


def _zero_based_lsp_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value + 1


def _string_or_default(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value)
    if not text:
        return default
    try:
        text.encode("utf-8")
        json.dumps(text, ensure_ascii=False).encode("utf-8")
    except (TypeError, UnicodeEncodeError):
        return default
    return text


class PyrightLspSession(Protocol):
    def collect_diagnostics(
        self,
        *,
        shadow_root: Path,
        changed_files: list[Path],
        scope: ValidatorScope,
        timeout_seconds: float,
    ) -> tuple[RawLspDiagnostics | None, str | None]:
        """Collect Pyright LSP diagnostics for a shadow workspace."""
        ...


class PyrightLspManager(Protocol):
    def session_for(
        self,
        real_workspace_root: Path,
        config: AgentQualityConfig,
    ) -> PyrightLspSession:
        """Return the reusable Pyright LSP session for a real workspace root."""
        ...

    def discard_session(self, real_workspace_root: Path) -> None:
        """Close and remove a failed reusable session."""
        ...


class PyrightCliAdapter(Protocol):
    def check(
        self,
        cwd: Path,
        changed_files: list[Path],
        mode: str,
    ) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
        """Run the Pyright CLI fallback."""
        ...


class PyrightLspProcessSession:
    """Collect diagnostics from a running Pyright language server process."""

    def __init__(self, *, process: object, max_message_bytes: int) -> None:
        self.process = process
        self._framer = LspFramer(max_message_bytes=max_message_bytes)
        self._initialized = False
        self._next_id = 1
        self._pending_messages: deque[dict[str, Any]] = deque()
        self._open_workspace_uris: set[str] = set()
        self._open_document_uris_by_workspace_uri: dict[str, set[str]] = {}
        self._request_lock = threading.RLock()
        self._last_cleanup_error: str | None = None

    def collect_diagnostics(
        self,
        *,
        shadow_root: Path,
        changed_files: list[Path],
        scope: ValidatorScope,
        timeout_seconds: float,
    ) -> tuple[RawLspDiagnostics | None, str | None]:
        """Open changed Python files and wait for their Pyright diagnostics."""

        shadow_root_resolved = shadow_root.resolve()
        deadline = time.perf_counter() + max(0.0, timeout_seconds)
        with self._request_lock:
            try:
                return self._collect_diagnostics_unlocked(
                    shadow_root=shadow_root_resolved,
                    changed_files=changed_files,
                    scope=scope,
                    deadline=deadline,
                )
            finally:
                try:
                    self._close_shadow_root_unlocked(
                        shadow_root_resolved,
                        deadline=deadline,
                    )
                except Exception as exc:
                    self._last_cleanup_error = str(exc) or exc.__class__.__name__

    def _collect_diagnostics_unlocked(
        self,
        *,
        shadow_root: Path,
        changed_files: list[Path],
        scope: ValidatorScope,
        deadline: float,
    ) -> tuple[RawLspDiagnostics | None, str | None]:
        try:
            shadow_root_resolved = shadow_root
            if not self._initialized:
                self._initialize(deadline)
            self._open_shadow_workspace(shadow_root_resolved, deadline)

            if scope is ValidatorScope.WORKSPACE:
                return None, "workspace diagnostics incomplete"

            expected_documents = _changed_python_document_paths(
                shadow_root_resolved,
                changed_files,
            )
            expected_uris = {lsp_uri_from_path(path) for path in expected_documents}
            if not expected_uris:
                return {}, None

            for document_path in expected_documents:
                self._open_shadow_document(shadow_root_resolved, document_path, deadline)

            raw_by_uri: RawLspDiagnostics = {}
            while time.perf_counter() < deadline:
                message = self._read_one_message(deadline)
                if message is None:
                    break
                if _is_lsp_response_message(message):
                    raise LspProtocolError(
                        "Unexpected Pyright LSP response id during diagnostics"
                    )

                diagnostic_message = _publish_diagnostics_from_message(
                    message,
                    shadow_root_resolved,
                )
                if diagnostic_message is None:
                    continue

                uri, diagnostics = diagnostic_message
                if uri not in expected_uris:
                    continue
                raw_by_uri[uri] = diagnostics
                if expected_uris.issubset(raw_by_uri):
                    return raw_by_uri, None

            return None, "changed-file diagnostics incomplete"
        except Exception as exc:
            return None, str(exc) or exc.__class__.__name__

    def _initialize(self, deadline: float) -> None:
        request_id = self._next_request_id()
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "processId": None,
                    "rootPath": None,
                    "rootUri": None,
                    "workspaceFolders": [],
                    "capabilities": {
                        "workspace": {
                            "workspaceFolders": True,
                            "didChangeConfiguration": {
                                "dynamicRegistration": False,
                            },
                        },
                        "textDocument": {
                            "publishDiagnostics": {
                                "relatedInformation": True,
                            }
                        }
                    },
                },
            },
            deadline,
        )
        self._read_until_response(request_id, deadline, operation="initialize")
        self._reject_buffered_lsp_responses(operation="initialize")
        self._send(
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            deadline,
        )
        self._initialized = True

    def _open_shadow_workspace(self, shadow_root: Path, deadline: float) -> None:
        uri = lsp_uri_from_path(shadow_root)
        if uri in self._open_workspace_uris:
            return

        self._send(
            {
                "jsonrpc": "2.0",
                "method": "workspace/didChangeWorkspaceFolders",
                "params": {
                    "event": {
                        "added": [{"uri": uri, "name": shadow_root.name}],
                        "removed": [],
                    }
                },
            },
            deadline,
        )
        self._open_workspace_uris.add(uri)

    def _open_shadow_document(
        self,
        shadow_root: Path,
        document_path: Path,
        deadline: float,
    ) -> None:
        workspace_uri = lsp_uri_from_path(shadow_root)
        uri = lsp_uri_from_path(document_path)
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "python",
                        "version": 1,
                        "text": document_path.read_text(encoding="utf-8"),
                    }
                },
            },
            deadline,
        )
        self._open_document_uris_by_workspace_uri.setdefault(workspace_uri, set()).add(uri)

    def close_shadow_root(self, shadow_root: Path) -> None:
        """Remove a request-scoped shadow workspace from the Pyright session."""

        deadline = time.perf_counter() + _SHADOW_CLEANUP_TIMEOUT_SECONDS
        with self._request_lock:
            try:
                self._close_shadow_root_unlocked(shadow_root.resolve(), deadline=deadline)
            except Exception as exc:
                self._last_cleanup_error = str(exc) or exc.__class__.__name__

    def is_healthy(self) -> bool:
        """Return whether the cached process is alive and cleanup state is reusable."""

        return self._last_cleanup_error is None and _process_is_alive(self.process)

    def close(self) -> None:
        """Terminate the underlying language-server process."""

        with self._request_lock:
            _close_process(self.process)

    def _close_shadow_root_unlocked(
        self,
        shadow_root: Path,
        deadline: float | None = None,
    ) -> None:
        workspace_uri = lsp_uri_from_path(shadow_root)
        document_uris = sorted(
            self._open_document_uris_by_workspace_uri.get(workspace_uri, set())
        )
        cleanup_errors: list[Exception] = []
        for document_uri in document_uris:
            try:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didClose",
                        "params": {"textDocument": {"uri": document_uri}},
                    },
                    deadline,
                )
            except Exception as exc:
                cleanup_errors.append(exc)
            else:
                open_document_uris = self._open_document_uris_by_workspace_uri.get(
                    workspace_uri
                )
                if open_document_uris is not None:
                    open_document_uris.discard(document_uri)
                    if not open_document_uris:
                        self._open_document_uris_by_workspace_uri.pop(
                            workspace_uri,
                            None,
                        )

        if workspace_uri not in self._open_workspace_uris:
            if cleanup_errors:
                raise cleanup_errors[0]
            return

        if self._open_document_uris_by_workspace_uri.get(workspace_uri):
            if cleanup_errors:
                raise cleanup_errors[0]
            return

        try:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": "workspace/didChangeWorkspaceFolders",
                    "params": {
                        "event": {
                            "added": [],
                            "removed": [
                                {
                                    "uri": workspace_uri,
                                    "name": shadow_root.name,
                                }
                            ],
                        }
                    },
                },
                deadline,
            )
        except Exception as exc:
            cleanup_errors.append(exc)
        else:
            self._open_workspace_uris.remove(workspace_uri)

        if cleanup_errors:
            raise cleanup_errors[0]
        self._last_cleanup_error = None

    def _next_request_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id

    def _send(self, message: dict[str, Any], deadline: float | None = None) -> None:
        stdin = getattr(self.process, "stdin", None)
        if stdin is None:
            raise RuntimeError("pyright language server stdin unavailable")

        encoded = build_lsp_message(message)
        _write_stdin_message(stdin, encoded, deadline)

    def _read_until_response(
        self,
        request_id: int,
        deadline: float,
        *,
        operation: str,
    ) -> dict[str, Any]:
        while time.perf_counter() < deadline:
            message = self._read_one_message(deadline)
            if message is None:
                break
            if not _is_lsp_response_message(message):
                continue
            message_id = message.get("id")
            if message_id != request_id:
                raise LspProtocolError(
                    f"Unexpected Pyright LSP response id during {operation}"
                )
            if "error" in message:
                raise LspProtocolError(f"Pyright LSP {operation} returned an error")
            return message
        raise TimeoutError(f"Pyright LSP {operation} response incomplete")

    def _reject_buffered_lsp_responses(self, *, operation: str) -> None:
        if any(_is_lsp_response_message(message) for message in self._pending_messages):
            raise LspProtocolError(
                f"Unexpected Pyright LSP response id during {operation}"
            )

    def _read_one_message(self, deadline: float) -> dict[str, Any] | None:
        if self._pending_messages:
            return self._pending_messages.popleft()

        stdout = getattr(self.process, "stdout", None)
        if stdout is None:
            raise RuntimeError("pyright language server stdout unavailable")

        while not self._pending_messages:
            chunk = _read_stdout_chunk(stdout, deadline)
            if not chunk:
                return None

            if isinstance(chunk, str):
                data = chunk.encode("utf-8")
            elif isinstance(chunk, bytes | bytearray):
                data = bytes(chunk)
            else:
                raise TypeError("pyright language server stdout returned non-bytes data")

            self._pending_messages.extend(self._framer.feed(data))

        return self._pending_messages.popleft()


def _changed_python_document_paths(
    shadow_root: Path,
    changed_files: list[Path],
) -> list[Path]:
    document_paths: list[Path] = []
    for changed_file in changed_files:
        if changed_file.suffix != ".py":
            continue

        candidate = changed_file if changed_file.is_absolute() else shadow_root / changed_file
        try:
            document_path = candidate.resolve()
            document_path.relative_to(shadow_root)
        except (OSError, ValueError):
            continue
        document_paths.append(document_path)
    return document_paths


def _publish_diagnostics_from_message(
    message: dict[str, Any],
    shadow_root: Path,
) -> tuple[str, list[dict[str, object]]] | None:
    if message.get("method") != "textDocument/publishDiagnostics":
        return None

    params = message.get("params")
    if not isinstance(params, dict):
        return None

    uri = params.get("uri")
    if not isinstance(uri, str):
        return None

    try:
        path_from_lsp_uri(uri).relative_to(shadow_root)
    except (OSError, ValueError):
        return None

    raw_diagnostics = params.get("diagnostics")
    if not isinstance(raw_diagnostics, list):
        return None
    if not all(isinstance(item, dict) for item in raw_diagnostics):
        return None

    return uri, cast(list[dict[str, object]], raw_diagnostics)


def _is_lsp_response_message(message: dict[str, Any]) -> bool:
    return "result" in message or "error" in message


def _write_stdin_message(
    stdin: Any,
    encoded: bytes,
    deadline: float | None,
) -> None:
    fd = _stream_fileno(stdin)
    if fd is None:
        try:
            stdin.write(encoded)
        except TypeError:
            stdin.write(encoded.decode("utf-8"))

        flush = getattr(stdin, "flush", None)
        if callable(flush):
            flush()
        return

    if not _stdin_ready(stdin, deadline):
        raise TimeoutError("Pyright LSP write timed out")

    was_blocking = os.get_blocking(fd)
    if was_blocking:
        os.set_blocking(fd, False)
    offset = 0
    try:
        while offset < len(encoded):
            if not _stdin_ready(stdin, deadline):
                raise TimeoutError("Pyright LSP write timed out")
            try:
                written = os.write(fd, encoded[offset : offset + 4096])
            except BlockingIOError:
                continue
            if written <= 0:
                raise BrokenPipeError("pyright language server stdin closed")
            offset += written
    finally:
        if was_blocking:
            os.set_blocking(fd, True)


def _stream_fileno(stream: Any) -> int | None:
    try:
        return int(stream.fileno())
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _stdin_ready(stdin: Any, deadline: float | None) -> bool:
    if _stream_fileno(stdin) is None:
        return True
    if deadline is None:
        return True

    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        return False
    try:
        _, writable, _ = select.select([], [stdin], [], remaining)
    except (OSError, ValueError):
        return True
    return bool(writable)


def _read_stdout_chunk(stdout: Any, deadline: float) -> bytes | bytearray | str | None:
    if not _stdout_ready(stdout, deadline):
        return None

    read_one = getattr(stdout, "read1", None)
    if callable(read_one):
        return cast(bytes | bytearray | str, read_one(4096))
    return cast(bytes | bytearray | str, stdout.read(4096))


def _stdout_ready(stdout: Any, deadline: float) -> bool:
    try:
        stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return True

    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        return False
    try:
        readable, _, _ = select.select([stdout], [], [], remaining)
    except (OSError, ValueError):
        return True
    return bool(readable)


class PyrightLspProvider:
    """Validate Pyright diagnostics through a reusable LSP session with CLI fallback."""

    def __init__(self, manager: PyrightLspManager, cli_adapter: PyrightCliAdapter) -> None:
        self.manager = manager
        self.cli_adapter = cli_adapter

    def validate(self, request: ValidatorRequest) -> ValidatorResult:
        started_at = time.perf_counter()
        scope = request.requested_scope
        documents_opened = _changed_python_documents(request.changed_files)

        try:
            session = self.manager.session_for(request.real_workspace_root, request.config)
            raw_by_uri, fallback_reason = session.collect_diagnostics(
                shadow_root=request.shadow_workspace_root,
                changed_files=request.changed_files,
                scope=scope,
                timeout_seconds=request.timeout_budget_seconds,
            )
        except CommandExecutionError as exc:
            return self._fallback(
                request=request,
                reason=str(exc) or exc.__class__.__name__,
                started_at=started_at,
                documents_opened=documents_opened,
                lsp_tool_unavailable=True,
                discard_lsp_session=True,
            )
        except Exception as exc:
            return self._fallback(
                request=request,
                reason=str(exc) or exc.__class__.__name__,
                started_at=started_at,
                documents_opened=documents_opened,
                discard_lsp_session=True,
            )
        finally:
            _close_shadow_root(self.manager, request.shadow_workspace_root)

        if raw_by_uri is not None and fallback_reason is None:
            diagnostics = _normalize_lsp_diagnostics_by_uri(
                raw_by_uri,
                request.shadow_workspace_root,
            )
            return ValidatorResult(
                provider="pyright",
                capabilities=[
                    ValidatorCapability.TYPE_DIAGNOSTICS,
                    ValidatorCapability.LSP_REUSE,
                    _scope_capability(scope),
                ],
                diagnostics=diagnostics,
                metadata={
                    "lsp_reused": True,
                    "fallback_to_cli": False,
                    "diagnostic_scope": scope.value,
                    "documents_opened": documents_opened,
                    "diagnostics_completed": True,
                },
                duration_ms=_duration_ms(started_at),
            )

        return self._fallback(
            request=request,
            reason=fallback_reason or "pyright LSP diagnostics unavailable",
            started_at=started_at,
            documents_opened=documents_opened,
            discard_lsp_session=_should_discard_lsp_session(fallback_reason),
        )

    def _fallback(
        self,
        *,
        request: ValidatorRequest,
        reason: str,
        started_at: float,
        documents_opened: list[str],
        lsp_tool_unavailable: bool = False,
        discard_lsp_session: bool = False,
    ) -> ValidatorResult:
        if discard_lsp_session:
            self.manager.discard_session(request.real_workspace_root)
        fallback_diagnostic = diagnostic_from_message(
            source="pyright",
            code="lsp_fallback",
            message=f"Pyright LSP unavailable; falling back to CLI: {reason}",
            severity=DiagnosticSeverity.WARNING,
            is_blocking=False,
            metadata={"fallback_reason": reason},
        )
        diagnostics = [fallback_diagnostic]
        if lsp_tool_unavailable:
            diagnostics.insert(1, _pyright_langserver_unavailable(reason))
        records: list[CommandExecutionRecord] = []
        try:
            cli_diagnostics, records = self.cli_adapter.check(
                request.shadow_workspace_root,
                request.changed_files,
                request.mode.value,
            )
        except CommandExecutionError as exc:
            diagnostics.append(_pyright_cli_unavailable(str(exc) or exc.__class__.__name__))
        else:
            diagnostics.extend(cli_diagnostics)
        return ValidatorResult(
            provider="pyright",
            capabilities=[
                ValidatorCapability.TYPE_DIAGNOSTICS,
                ValidatorCapability.CLI_FALLBACK,
            ],
            diagnostics=diagnostics,
            commands=records,
            metadata={
                "lsp_reused": False,
                "fallback_to_cli": True,
                "fallback_reason": reason,
                "diagnostic_scope": request.requested_scope.value,
                "documents_opened": documents_opened,
                "diagnostics_completed": True,
            },
            fallback_reason=reason,
            duration_ms=_duration_ms(started_at),
            timed_out=any(record.timed_out for record in records),
            output_truncated=any(
                record.stdout_truncated or record.stderr_truncated for record in records
            ),
        )


def _normalize_lsp_diagnostics_by_uri(
    raw_by_uri: RawLspDiagnostics,
    shadow_root: Path,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for uri, raw_diagnostics in raw_by_uri.items():
        diagnostics.extend(normalize_lsp_diagnostics(uri, raw_diagnostics, shadow_root))
    return diagnostics


def _changed_python_documents(changed_files: list[Path]) -> list[str]:
    return [
        changed_file.as_posix()
        for changed_file in changed_files
        if changed_file.suffix == ".py"
    ]


def _scope_capability(scope: ValidatorScope) -> ValidatorCapability:
    if scope is ValidatorScope.WORKSPACE:
        return ValidatorCapability.WORKSPACE_SCOPE
    return ValidatorCapability.CHANGED_FILE_SCOPE


def _close_shadow_root(manager: PyrightLspManager, shadow_root: Path) -> None:
    close_shadow_root = getattr(manager, "close_shadow_root", None)
    if callable(close_shadow_root):
        try:
            close_shadow_root(shadow_root)
        except Exception:
            return


def _pyright_langserver_unavailable(reason: str) -> Diagnostic:
    return diagnostic_from_message(
        source="system",
        code="tool_unavailable",
        message=f"Pyright language server unavailable: {reason}",
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        metadata={"tool": "pyright-langserver"},
    )


def _pyright_cli_unavailable(reason: str) -> Diagnostic:
    return diagnostic_from_message(
        source="system",
        code="tool_unavailable",
        message=reason,
        severity=DiagnosticSeverity.WARNING,
        is_blocking=False,
        metadata={"tool": "pyright"},
    )


def _should_discard_lsp_session(fallback_reason: str | None) -> bool:
    return fallback_reason != "workspace diagnostics incomplete"


def _process_is_alive(process: object) -> bool:
    poll = getattr(process, "poll", None)
    if not callable(poll):
        return True
    try:
        return poll() is None
    except Exception:
        return False


def _close_process(process: object) -> None:
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(process, stream_name, None)
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                _ignore_process_cleanup_error(exc)

    if not _process_is_alive(process):
        return

    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        try:
            terminate()
        except Exception as exc:
            _ignore_process_cleanup_error(exc)
    _wait_for_process_exit(process, timeout=1.0)
    if not _process_is_alive(process):
        return

    kill = getattr(process, "kill", None)
    if callable(kill):
        try:
            kill()
        except Exception as exc:
            _ignore_process_cleanup_error(exc)
    _wait_for_process_exit(process, timeout=1.0)


def _wait_for_process_exit(process: object, *, timeout: float) -> None:
    wait = getattr(process, "wait", None)
    if not callable(wait):
        return
    try:
        wait(timeout=timeout)
    except Exception as exc:
        _ignore_process_cleanup_error(exc)


def _ignore_process_cleanup_error(exc: Exception) -> None:
    del exc


def _duration_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


class RealPyrightLspManager:
    """Reusable Pyright LSP session manager keyed by real workspace."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[Path, PyrightLspProcessSession] = {}

    def session_for(
        self,
        real_workspace_root: Path,
        config: AgentQualityConfig,
    ) -> PyrightLspProcessSession:
        key = real_workspace_root.resolve()
        with self._lock:
            session = self._sessions.get(key)
            if session is not None and not session.is_healthy():
                session.close()
                self._sessions.pop(key, None)
                session = None
            if session is None:
                session = _start_process_session(key, config)
                self._sessions[key] = session
            return session

    def close_shadow_root(self, shadow_root: Path) -> None:
        for session in list(self._sessions.values()):
            session.close_shadow_root(shadow_root)

    def discard_session(self, real_workspace_root: Path) -> None:
        key = real_workspace_root.resolve()
        with self._lock:
            session = self._sessions.pop(key, None)
        if session is not None:
            session.close()

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()


def _start_process_session(
    real_workspace_root: Path,
    config: AgentQualityConfig,
) -> PyrightLspProcessSession:
    command = start_long_running_command(
        "pyright-langserver",
        ["--stdio"],
        cwd=real_workspace_root,
        config=config,
        process_cwd=Path(os.sep),
    )
    return PyrightLspProcessSession(
        process=command.process,
        max_message_bytes=config.max_output_bytes,
    )
