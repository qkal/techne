from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, cast

from agent_quality_mcp.diagnostics import diagnostic_from_message
from agent_quality_mcp.lsp.protocol import LspFramer, build_lsp_message
from agent_quality_mcp.lsp.pyright import (
    PyrightLspProcessSession,
    PyrightLspProvider,
    lsp_uri_from_path,
    normalize_lsp_diagnostics,
    path_from_lsp_uri,
)
from agent_quality_mcp.models import (
    AgentQualityConfig,
    CommandExecutionRecord,
    Diagnostic,
    DiagnosticSeverity,
    SafetyMode,
    ValidationMode,
)
from agent_quality_mcp.validators import (
    ValidatorCapability,
    ValidatorRequest,
    ValidatorScope,
)

RawLspDiagnostics = dict[str, list[dict[str, object]]]


class FakeByteStdin:
    def __init__(self) -> None:
        self.written = bytearray()
        self.flushes = 0

    def write(self, data: bytes) -> int:
        assert isinstance(data, bytes)
        self.written.extend(data)
        return len(data)

    def flush(self) -> None:
        self.flushes += 1


class FakeByteProcess:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.stdin = FakeByteStdin()
        self.stdout = BytesIO(b"".join(build_lsp_message(message) for message in messages))


class FakeChunkedStdout:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.chunks = [build_lsp_message(message) for message in messages]

    def read1(self, size: int) -> bytes:
        del size
        if not self.chunks:
            return b""
        return self.chunks.pop(0)


class FakeChunkedProcess:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.stdin = FakeByteStdin()
        self.stdout = FakeChunkedStdout(messages)


class FakePyrightLspSession:
    def __init__(
        self,
        raw_diagnostics: RawLspDiagnostics | None,
        fallback_reason: str | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.raw_diagnostics = raw_diagnostics
        self.fallback_reason = fallback_reason
        self.exception = exception
        self.calls: list[tuple[Path, list[Path], ValidatorScope, float]] = []
        self.documents_opened: list[Path] = []

    def collect_diagnostics(
        self,
        *,
        shadow_root: Path,
        changed_files: list[Path],
        scope: ValidatorScope,
        timeout_seconds: float,
    ) -> tuple[RawLspDiagnostics | None, str | None]:
        if self.exception is not None:
            raise self.exception
        self.calls.append((shadow_root, list(changed_files), scope, timeout_seconds))
        self.documents_opened = [
            shadow_root / changed_file
            for changed_file in changed_files
            if changed_file.suffix == ".py"
        ]
        return self.raw_diagnostics, self.fallback_reason


class FakePyrightLspManager:
    def __init__(
        self,
        session: FakePyrightLspSession,
        cleanup_exception: Exception | None = None,
    ) -> None:
        self.session = session
        self.cleanup_exception = cleanup_exception
        self.session_roots: list[Path] = []
        self.closed_shadow_roots: list[Path] = []

    def session_for(self, real_workspace_root: Path) -> FakePyrightLspSession:
        self.session_roots.append(real_workspace_root)
        return self.session

    def close_shadow_root(self, shadow_root: Path) -> None:
        if self.cleanup_exception is not None:
            raise self.cleanup_exception
        self.closed_shadow_roots.append(shadow_root)


class FakePyrightCliAdapter:
    def __init__(
        self,
        diagnostics: list[Diagnostic] | None = None,
        records: list[CommandExecutionRecord] | None = None,
    ) -> None:
        self.diagnostics = diagnostics or []
        self.records = records or []
        self.calls: list[tuple[Path, list[Path], str]] = []

    def check(
        self,
        cwd: Path,
        changed_files: list[Path],
        mode: str,
    ) -> tuple[list[Diagnostic], list[CommandExecutionRecord]]:
        self.calls.append((cwd, list(changed_files), mode))
        return list(self.diagnostics), list(self.records)


def _provider_request(
    tmp_path: Path,
    *,
    mode: ValidationMode = ValidationMode.STANDARD,
    requested_scope: ValidatorScope = ValidatorScope.CHANGED_FILES,
    changed_files: list[Path] | None = None,
) -> ValidatorRequest:
    real_root = tmp_path / "real"
    shadow_root = tmp_path / "shadow"
    real_root.mkdir()
    shadow_root.mkdir()
    return ValidatorRequest(
        real_workspace_root=real_root,
        shadow_workspace_root=shadow_root,
        changed_files=changed_files or [Path("pkg/app.py")],
        mode=mode,
        safety_mode=SafetyMode.READ_ONLY,
        requested_scope=requested_scope,
        timeout_budget_seconds=30.0,
        request_id="req-1",
        config=AgentQualityConfig(),
    )


def _command_record(
    cwd: Path,
    *,
    timed_out: bool = False,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
) -> CommandExecutionRecord:
    return CommandExecutionRecord(
        command="pyright",
        args=["pyright", "--outputjson"],
        cwd=str(cwd),
        duration_ms=11,
        exit_code=1,
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def _sent_lsp_messages(process: FakeByteProcess) -> list[dict[str, Any]]:
    framer = LspFramer(max_message_bytes=65536)
    return framer.feed(bytes(process.stdin.written))


def test_pyright_lsp_process_session_initializes_without_workspace_root(
    tmp_path: Path,
) -> None:
    real_workspace_root = tmp_path / "real-workspace"
    real_workspace_root.mkdir()
    shadow_root = tmp_path / "shadow"
    changed_file = shadow_root / "pkg" / "app.py"
    changed_file.parent.mkdir(parents=True)
    changed_file.write_text("print('ok')\n", encoding="utf-8")
    uri = lsp_uri_from_path(changed_file)
    process = FakeByteProcess(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": uri, "diagnostics": []},
            },
        ]
    )
    session = PyrightLspProcessSession(process=process, max_message_bytes=65536)

    raw_by_uri, fallback_reason = session.collect_diagnostics(
        shadow_root=shadow_root,
        changed_files=[Path("pkg/app.py")],
        scope=ValidatorScope.CHANGED_FILES,
        timeout_seconds=1.0,
    )

    assert raw_by_uri == {uri: []}
    assert fallback_reason is None
    sent_bytes = bytes(process.stdin.written)
    assert str(real_workspace_root).encode() not in sent_bytes
    sent_messages = _sent_lsp_messages(process)
    initialize = sent_messages[0]
    assert initialize["method"] == "initialize"
    assert initialize["params"]["rootPath"] is None
    assert initialize["params"]["rootUri"] is None
    assert initialize["params"]["workspaceFolders"] == []
    assert [message.get("method") for message in sent_messages[:4]] == [
        "initialize",
        "initialized",
        "workspace/didChangeWorkspaceFolders",
        "textDocument/didOpen",
    ]
    workspace_change = sent_messages[2]
    assert workspace_change["params"]["event"]["added"] == [
        {"uri": lsp_uri_from_path(shadow_root), "name": shadow_root.name}
    ]
    assert workspace_change["params"]["event"]["removed"] == []


def test_pyright_lsp_process_session_opens_changed_python_files(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    first_file = shadow_root / "pkg" / "app.py"
    second_file = shadow_root / "pkg" / "other.py"
    ignored_file = shadow_root / "README.md"
    first_file.parent.mkdir(parents=True)
    first_file.write_text("first = missing\n", encoding="utf-8")
    second_file.write_text("second = 2\n", encoding="utf-8")
    ignored_file.write_text("# ignored\n", encoding="utf-8")
    first_uri = lsp_uri_from_path(first_file)
    second_uri = lsp_uri_from_path(second_file)
    process = FakeByteProcess(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": first_uri, "diagnostics": []},
            },
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": second_uri, "diagnostics": []},
            },
        ]
    )
    session = PyrightLspProcessSession(process=process, max_message_bytes=65536)

    raw_by_uri, fallback_reason = session.collect_diagnostics(
        shadow_root=shadow_root,
        changed_files=[Path("pkg/app.py"), Path("README.md"), Path("pkg/other.py")],
        scope=ValidatorScope.CHANGED_FILES,
        timeout_seconds=1.0,
    )

    assert raw_by_uri == {first_uri: [], second_uri: []}
    assert fallback_reason is None
    did_open_messages = [
        message
        for message in _sent_lsp_messages(process)
        if message.get("method") == "textDocument/didOpen"
    ]
    assert [message["params"]["textDocument"]["uri"] for message in did_open_messages] == [
        first_uri,
        second_uri,
    ]
    assert did_open_messages[0]["params"]["textDocument"]["languageId"] == "python"
    assert did_open_messages[0]["params"]["textDocument"]["version"] == 1
    assert did_open_messages[0]["params"]["textDocument"]["text"] == "first = missing\n"


def test_pyright_lsp_process_session_returns_publish_diagnostics_by_uri(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    changed_file = shadow_root / "pkg" / "app.py"
    changed_file.parent.mkdir(parents=True)
    changed_file.write_text("print(missing)\n", encoding="utf-8")
    uri = lsp_uri_from_path(changed_file)
    diagnostic = {
        "range": {
            "start": {"line": 0, "character": 6},
            "end": {"line": 0, "character": 13},
        },
        "severity": 1,
        "code": "reportUndefinedVariable",
        "message": "Name is not defined",
    }
    process = FakeByteProcess(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": uri, "diagnostics": [diagnostic]},
            },
        ]
    )
    session = PyrightLspProcessSession(process=process, max_message_bytes=65536)

    raw_by_uri, fallback_reason = session.collect_diagnostics(
        shadow_root=shadow_root,
        changed_files=[Path("pkg/app.py")],
        scope=ValidatorScope.CHANGED_FILES,
        timeout_seconds=1.0,
    )

    assert raw_by_uri == {uri: [diagnostic]}
    assert fallback_reason is None


def test_pyright_lsp_process_session_workspace_scope_is_incomplete(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    shadow_root.mkdir()
    process = FakeByteProcess(
        [{"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}]
    )
    session = PyrightLspProcessSession(process=process, max_message_bytes=65536)

    raw_by_uri, fallback_reason = session.collect_diagnostics(
        shadow_root=shadow_root,
        changed_files=[Path("pkg/app.py")],
        scope=ValidatorScope.WORKSPACE,
        timeout_seconds=1.0,
    )

    assert raw_by_uri is None
    assert fallback_reason == "workspace diagnostics incomplete"


def test_pyright_lsp_process_session_returns_incomplete_when_changed_file_missing_diagnostics(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    changed_file = shadow_root / "pkg" / "app.py"
    outside_file = tmp_path / "outside.py"
    changed_file.parent.mkdir(parents=True)
    changed_file.write_text("print('ok')\n", encoding="utf-8")
    outside_file.write_text("print('outside')\n", encoding="utf-8")
    process = FakeByteProcess(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": lsp_uri_from_path(outside_file), "diagnostics": []},
            },
        ]
    )
    session = PyrightLspProcessSession(process=process, max_message_bytes=65536)

    raw_by_uri, fallback_reason = session.collect_diagnostics(
        shadow_root=shadow_root,
        changed_files=[Path("pkg/app.py")],
        scope=ValidatorScope.CHANGED_FILES,
        timeout_seconds=1.0,
    )

    assert raw_by_uri is None
    assert fallback_reason == "changed-file diagnostics incomplete"


def test_pyright_lsp_process_session_rejects_unexpected_response_id(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    changed_file = shadow_root / "pkg" / "app.py"
    changed_file.parent.mkdir(parents=True)
    changed_file.write_text("print('ok')\n", encoding="utf-8")
    process = FakeByteProcess(
        [{"jsonrpc": "2.0", "id": 99, "result": {"capabilities": {}}}]
    )
    session = PyrightLspProcessSession(process=process, max_message_bytes=65536)

    raw_by_uri, fallback_reason = session.collect_diagnostics(
        shadow_root=shadow_root,
        changed_files=[Path("pkg/app.py")],
        scope=ValidatorScope.CHANGED_FILES,
        timeout_seconds=1.0,
    )

    assert raw_by_uri is None
    assert fallback_reason == "Unexpected Pyright LSP response id during initialize"


def test_pyright_lsp_process_session_sanitizes_initialize_error_payload(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    changed_file = shadow_root / "pkg" / "app.py"
    changed_file.parent.mkdir(parents=True)
    changed_file.write_text("print('ok')\n", encoding="utf-8")
    process = FakeByteProcess(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": "secret raw payload"},
            }
        ]
    )
    session = PyrightLspProcessSession(process=process, max_message_bytes=65536)

    raw_by_uri, fallback_reason = session.collect_diagnostics(
        shadow_root=shadow_root,
        changed_files=[Path("pkg/app.py")],
        scope=ValidatorScope.CHANGED_FILES,
        timeout_seconds=1.0,
    )

    assert raw_by_uri is None
    assert fallback_reason == "Pyright LSP initialize returned an error"
    assert "secret raw payload" not in fallback_reason


def test_pyright_lsp_process_session_rejects_duplicate_response_id_during_diagnostics(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    changed_file = shadow_root / "pkg" / "app.py"
    changed_file.parent.mkdir(parents=True)
    changed_file.write_text("print('ok')\n", encoding="utf-8")
    uri = lsp_uri_from_path(changed_file)
    process = FakeChunkedProcess(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            {"jsonrpc": "2.0", "id": 1, "result": {"duplicate": True}},
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": uri, "diagnostics": []},
            },
        ]
    )
    session = PyrightLspProcessSession(process=process, max_message_bytes=65536)

    raw_by_uri, fallback_reason = session.collect_diagnostics(
        shadow_root=shadow_root,
        changed_files=[Path("pkg/app.py")],
        scope=ValidatorScope.CHANGED_FILES,
        timeout_seconds=1.0,
    )

    assert raw_by_uri is None
    assert fallback_reason == "Unexpected Pyright LSP response id during diagnostics"


def test_pyright_lsp_process_session_rejects_buffered_duplicate_initialize_response(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    shadow_root.mkdir()
    process = FakeByteProcess(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            {"jsonrpc": "2.0", "id": 1, "result": {"duplicate": True}},
        ]
    )
    session = PyrightLspProcessSession(process=process, max_message_bytes=65536)

    raw_by_uri, fallback_reason = session.collect_diagnostics(
        shadow_root=shadow_root,
        changed_files=[],
        scope=ValidatorScope.CHANGED_FILES,
        timeout_seconds=1.0,
    )

    assert raw_by_uri is None
    assert fallback_reason == "Unexpected Pyright LSP response id during initialize"


def test_pyright_lsp_provider_uses_lsp_for_changed_file_diagnostics(
    tmp_path: Path,
) -> None:
    request = _provider_request(
        tmp_path,
        changed_files=[Path("pkg/app.py"), Path("README.md")],
    )
    changed_file = request.shadow_workspace_root / "pkg" / "app.py"
    changed_file.parent.mkdir()
    changed_file.write_text("print(missing)\n", encoding="utf-8")
    session = FakePyrightLspSession(
        {
            lsp_uri_from_path(changed_file): [
                {
                    "range": {
                        "start": {"line": 0, "character": 6},
                        "end": {"line": 0, "character": 13},
                    },
                    "severity": 1,
                    "code": "reportUndefinedVariable",
                    "message": "Name is not defined",
                }
            ]
        }
    )
    manager = FakePyrightLspManager(session)
    cli_adapter = FakePyrightCliAdapter()

    result = PyrightLspProvider(manager, cli_adapter).validate(request)

    assert result.provider == "pyright"
    assert result.commands == []
    assert cli_adapter.calls == []
    assert manager.session_roots == [request.real_workspace_root]
    assert manager.closed_shadow_roots == [request.shadow_workspace_root]
    assert session.calls == [
        (
            request.shadow_workspace_root,
            [Path("pkg/app.py"), Path("README.md")],
            ValidatorScope.CHANGED_FILES,
            30.0,
        )
    ]
    assert session.documents_opened == [request.shadow_workspace_root / "pkg/app.py"]
    assert result.capabilities == [
        ValidatorCapability.TYPE_DIAGNOSTICS,
        ValidatorCapability.LSP_REUSE,
        ValidatorCapability.CHANGED_FILE_SCOPE,
    ]
    assert result.metadata["lsp_reused"] is True
    assert result.metadata["fallback_to_cli"] is False
    assert result.metadata["diagnostic_scope"] == "changed_files"
    assert result.metadata["documents_opened"] == ["pkg/app.py"]
    assert result.metadata["diagnostics_completed"] is True
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.message == "Name is not defined"
    assert diagnostic.file == "pkg/app.py"
    assert diagnostic.metadata == {"transport": "lsp"}


def test_pyright_lsp_provider_falls_back_to_cli_on_lsp_failure(
    tmp_path: Path,
) -> None:
    request = _provider_request(tmp_path, mode=ValidationMode.QUICK)
    fallback_reason = "pyright language server exited"
    session = FakePyrightLspSession(None, fallback_reason)
    manager = FakePyrightLspManager(session)
    cli_diagnostic = diagnostic_from_message(
        source="pyright",
        code="reportGeneralTypeIssues",
        message="CLI diagnostic",
        severity=DiagnosticSeverity.ERROR,
        is_blocking=True,
        file="pkg/app.py",
    )
    cli_record = _command_record(request.shadow_workspace_root, stdout_truncated=True)
    cli_adapter = FakePyrightCliAdapter([cli_diagnostic], [cli_record])

    result = PyrightLspProvider(manager, cli_adapter).validate(request)

    assert cli_adapter.calls == [
        (request.shadow_workspace_root, [Path("pkg/app.py")], "quick")
    ]
    assert result.commands == [cli_record]
    assert result.fallback_reason == fallback_reason
    assert result.metadata["lsp_reused"] is False
    assert result.metadata["fallback_to_cli"] is True
    assert result.metadata["fallback_reason"] == fallback_reason
    assert result.metadata["diagnostic_scope"] == "changed_files"
    assert result.metadata["diagnostics_completed"] is True
    assert result.timed_out is False
    assert result.output_truncated is True
    assert result.diagnostics[0].source == "pyright"
    assert result.diagnostics[0].code == "lsp_fallback"
    assert result.diagnostics[0].is_blocking is False
    assert result.diagnostics[1] == cli_diagnostic
    assert ValidatorCapability.TYPE_DIAGNOSTICS in result.capabilities
    assert ValidatorCapability.CLI_FALLBACK in result.capabilities
    assert ValidatorCapability.LSP_REUSE not in result.capabilities


def test_pyright_lsp_provider_falls_back_when_workspace_scope_incomplete(
    tmp_path: Path,
) -> None:
    request = _provider_request(
        tmp_path,
        mode=ValidationMode.STANDARD,
        requested_scope=ValidatorScope.WORKSPACE,
    )
    session = FakePyrightLspSession(
        raw_diagnostics={},
        fallback_reason="workspace diagnostics incomplete",
    )
    manager = FakePyrightLspManager(session)
    cli_record = _command_record(request.shadow_workspace_root)
    cli_adapter = FakePyrightCliAdapter(records=[cli_record])

    result = PyrightLspProvider(manager, cli_adapter).validate(request)

    assert session.calls == [
        (
            request.shadow_workspace_root,
            [Path("pkg/app.py")],
            ValidatorScope.WORKSPACE,
            30.0,
        )
    ]
    assert cli_adapter.calls == [
        (request.shadow_workspace_root, [Path("pkg/app.py")], "standard")
    ]
    assert result.commands == [cli_record]
    assert result.fallback_reason == "workspace diagnostics incomplete"
    assert result.metadata["fallback_to_cli"] is True
    assert result.metadata["diagnostic_scope"] == "workspace"


def test_pyright_lsp_provider_falls_back_when_lsp_raises(tmp_path: Path) -> None:
    request = _provider_request(tmp_path, mode=ValidationMode.QUICK)
    session = FakePyrightLspSession(None, exception=RuntimeError("initialize failed"))
    manager = FakePyrightLspManager(session)
    cli_record = _command_record(request.shadow_workspace_root)
    cli_adapter = FakePyrightCliAdapter(records=[cli_record])

    result = PyrightLspProvider(manager, cli_adapter).validate(request)

    assert cli_adapter.calls == [
        (request.shadow_workspace_root, [Path("pkg/app.py")], "quick")
    ]
    assert result.commands == [cli_record]
    assert result.fallback_reason == "initialize failed"
    assert result.metadata["fallback_to_cli"] is True
    assert result.diagnostics[0].code == "lsp_fallback"


def test_pyright_lsp_provider_cleanup_failure_does_not_mask_fallback(
    tmp_path: Path,
) -> None:
    request = _provider_request(tmp_path, mode=ValidationMode.QUICK)
    session = FakePyrightLspSession(None, exception=RuntimeError("initialize failed"))
    manager = FakePyrightLspManager(
        session,
        cleanup_exception=RuntimeError("cleanup failed"),
    )
    cli_record = _command_record(request.shadow_workspace_root)
    cli_adapter = FakePyrightCliAdapter(records=[cli_record])

    result = PyrightLspProvider(manager, cli_adapter).validate(request)

    assert result.commands == [cli_record]
    assert result.fallback_reason == "initialize failed"
    assert result.metadata["fallback_to_cli"] is True


def test_lsp_uri_round_trips_path(tmp_path: Path) -> None:
    path = tmp_path / "pkg" / "space file.py"
    path.parent.mkdir()
    path.write_text("value = 1\n", encoding="utf-8")

    uri = lsp_uri_from_path(path)

    assert uri == path.resolve().as_uri()
    assert path_from_lsp_uri(uri) == path.resolve()


def test_normalize_lsp_diagnostics_maps_file_range_and_severity(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow"
    file_path = shadow_root / "pkg" / "module.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("print(missing)\n", encoding="utf-8")

    diagnostics = normalize_lsp_diagnostics(
        lsp_uri_from_path(file_path),
        [
            {
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 2, "character": 8},
                },
                "severity": 1,
                "code": "reportUndefinedVariable",
                "source": "pyright",
                "message": "Name is not defined",
            }
        ],
        shadow_root,
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "pyright"
    assert diagnostic.raw_source == "pyright_lsp"
    assert diagnostic.metadata == {"transport": "lsp"}
    assert diagnostic.code == "reportUndefinedVariable"
    assert diagnostic.message == "Name is not defined"
    assert diagnostic.file == "pkg/module.py"
    assert diagnostic.severity == DiagnosticSeverity.ERROR
    assert diagnostic.is_blocking is True
    assert diagnostic.range is not None
    assert diagnostic.range.start_line == 1
    assert diagnostic.range.start_column == 5
    assert diagnostic.range.end_line == 3
    assert diagnostic.range.end_column == 9


def test_normalize_lsp_diagnostics_rejects_uri_outside_shadow_root(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow"
    outside_file = tmp_path / "outside" / "module.py"
    outside_file.parent.mkdir(parents=True)
    outside_file.write_text("print(missing)\n", encoding="utf-8")

    diagnostics = normalize_lsp_diagnostics(
        lsp_uri_from_path(outside_file),
        [{"message": "Name is not defined", "severity": 1}],
        shadow_root,
    )

    assert diagnostics == []


def test_normalize_lsp_diagnostics_rejects_relative_file_uri(
    tmp_path: Path,
    monkeypatch,
) -> None:
    shadow_root = tmp_path / "shadow"
    shadow_root.mkdir()
    (shadow_root / "module.py").write_text("print(missing)\n", encoding="utf-8")
    monkeypatch.chdir(shadow_root)

    diagnostics = normalize_lsp_diagnostics(
        "file:module.py",
        [{"message": "Name is not defined", "severity": 1}],
        shadow_root,
    )

    assert diagnostics == []


def test_normalize_lsp_diagnostics_rejects_malformed_top_level(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow"
    file_path = shadow_root / "module.py"
    shadow_root.mkdir()
    file_path.write_text("print(missing)\n", encoding="utf-8")

    assert normalize_lsp_diagnostics(lsp_uri_from_path(file_path), None, shadow_root) == []


def test_normalize_lsp_diagnostics_omits_reversed_ranges(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow"
    file_path = shadow_root / "module.py"
    shadow_root.mkdir()
    file_path.write_text("print(missing)\n", encoding="utf-8")

    diagnostics = normalize_lsp_diagnostics(
        lsp_uri_from_path(file_path),
        [
            {
                "range": {
                    "start": {"line": 10, "character": 0},
                    "end": {"line": 0, "character": 0},
                },
                "message": "Name is not defined",
                "severity": 1,
            }
        ],
        shadow_root,
    )

    assert diagnostics[0].range is None


def test_normalize_lsp_diagnostics_rejects_non_string_uri(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow"
    shadow_root.mkdir()

    assert (
        normalize_lsp_diagnostics(cast(Any, 123), [{"message": "ignored"}], shadow_root)
        == []
    )


def test_normalize_lsp_diagnostics_sanitizes_unserializable_text(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    file_path = shadow_root / "module.py"
    shadow_root.mkdir()
    file_path.write_text("print(missing)\n", encoding="utf-8")

    diagnostics = normalize_lsp_diagnostics(
        lsp_uri_from_path(file_path),
        [
            {
                "code": "\ud800",
                "message": "\ud800",
                "severity": 1,
            }
        ],
        shadow_root,
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "pyright_lsp"
    assert diagnostic.message == "Pyright diagnostic"
    assert diagnostic.id.startswith("pyright-lsp-")
    diagnostic.model_dump_json()
