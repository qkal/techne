from __future__ import annotations

import json
from pathlib import Path

from agent_quality_mcp.cli.pyright import PyrightAdapter
from agent_quality_mcp.cli.ruff import RuffAdapter
from agent_quality_mcp.cli.runner import CommandRunResult
from agent_quality_mcp.cli.uv import UvAdapter
from agent_quality_mcp.exceptions import ToolUnavailableError
from agent_quality_mcp.models import AgentQualityConfig, CommandExecutionRecord, DiagnosticSeverity


class StubRunner:
    def __init__(
        self,
        records: list[CommandExecutionRecord | CommandRunResult] | None = None,
        *,
        config: AgentQualityConfig | None = None,
        unavailable: str | None = None,
    ) -> None:
        self.config = config or AgentQualityConfig()
        self.records = [_as_result(record) for record in records or []]
        self.calls: list[tuple[str, list[str], Path]] = []
        self.unavailable = unavailable

    def run(self, command: str, args: list[str], cwd: Path) -> CommandExecutionRecord:
        return self.run_with_output(command, args, cwd).record

    def run_with_output(self, command: str, args: list[str], cwd: Path) -> CommandRunResult:
        self.calls.append((command, args, cwd))
        if self.unavailable is not None:
            raise ToolUnavailableError(self.unavailable)
        return self.records.pop(0)


def _record(
    command: str,
    args: list[str],
    cwd: Path,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = 0,
    timed_out: bool = False,
) -> CommandExecutionRecord:
    return CommandExecutionRecord(
        command=command,
        args=[command, *args],
        cwd=str(cwd),
        duration_ms=7,
        exit_code=exit_code,
        timed_out=timed_out,
        stdout_preview=stdout,
        stderr_preview=stderr,
    )


def _as_result(record: CommandExecutionRecord | CommandRunResult) -> CommandRunResult:
    if isinstance(record, CommandRunResult):
        return record
    return CommandRunResult(
        record=record,
        stdout=record.stdout_preview,
        stderr=record.stderr_preview,
    )


def _result(
    command: str,
    args: list[str],
    cwd: Path,
    *,
    raw_stdout: str,
    stdout_preview: str,
    exit_code: int = 0,
) -> CommandRunResult:
    return CommandRunResult(
        record=_record(command, args, cwd, stdout=stdout_preview, exit_code=exit_code),
        stdout=raw_stdout,
        stderr="",
    )


def test_ruff_adapter_parses_json_and_returns_diagnostics_records_and_safe_fixes(
    tmp_path: Path,
) -> None:
    ruff_json = json.dumps(
        [
            {
                "code": "F401",
                "message": "`os` imported but unused",
                "filename": "pkg/app.py",
                "location": {"row": 1, "column": 1},
                "end_location": {"row": 1, "column": 10},
                "fix": {"message": "Remove unused import"},
            }
        ]
    )
    diff = "--- pkg/app.py\n+++ pkg/app.py\n@@\n-import os\n"
    runner = StubRunner(
        [
            _record(
                "ruff",
                ["check", "--no-cache", "--output-format", "json", "--", "pkg/app.py"],
                tmp_path,
                stdout=ruff_json,
                exit_code=1,
            ),
            _record(
                "ruff",
                ["check", "--no-cache", "--fix", "--diff", "--", "pkg/app.py"],
                tmp_path,
                stdout=diff,
            ),
        ]
    )

    diagnostics, records, safe_fixes = RuffAdapter(runner).check(
        tmp_path,
        [Path("pkg/app.py")],
        "standard",
        preview_safe_fixes=True,
    )

    assert runner.calls == [
        ("ruff", ["check", "--no-cache", "--output-format", "json", "--", "pkg/app.py"], tmp_path),
        ("ruff", ["check", "--no-cache", "--fix", "--diff", "--", "pkg/app.py"], tmp_path),
    ]
    assert len(records) == 2
    assert len(diagnostics) == 1
    assert diagnostics[0].source == "ruff"
    assert diagnostics[0].code == "F401"
    assert diagnostics[0].file == "pkg/app.py"
    assert diagnostics[0].is_fixable is True
    assert len(safe_fixes) == 1
    assert safe_fixes[0].tool == "ruff"
    assert safe_fixes[0].files == ["pkg/app.py"]
    assert safe_fixes[0].diff_preview == diff
    assert safe_fixes[0].requires_human_review is True


def test_ruff_adapter_reports_invalid_json_as_warning_with_records(tmp_path: Path) -> None:
    runner = StubRunner(
        [
            _record(
                "ruff",
                ["check", "--output-format", "json"],
                tmp_path,
                stdout="not json",
                exit_code=1,
            )
        ]
    )

    diagnostics, records, safe_fixes = RuffAdapter(runner).check(tmp_path, [], "standard")

    assert len(records) == 1
    assert safe_fixes == []
    assert diagnostics[0].source == "ruff"
    assert diagnostics[0].severity == DiagnosticSeverity.WARNING
    assert diagnostics[0].code == "invalid_json"


def test_ruff_adapter_converts_unavailable_tool_to_warning_diagnostic(tmp_path: Path) -> None:
    runner = StubRunner(unavailable="ruff was not found")

    diagnostics, records, safe_fixes = RuffAdapter(runner).check(tmp_path, [], "standard")

    assert records == []
    assert safe_fixes == []
    assert diagnostics[0].source == "system"
    assert diagnostics[0].severity == DiagnosticSeverity.WARNING
    assert diagnostics[0].code == "tool_unavailable"
    assert diagnostics[0].metadata == {"tool": "ruff"}


def test_ruff_adapter_parses_full_stdout_when_record_preview_is_truncated(
    tmp_path: Path,
) -> None:
    ruff_json = json.dumps(
        [
            {
                "code": "F401",
                "message": "x" * 200,
                "filename": "pkg/app.py",
            }
        ]
    )
    runner = StubRunner(
        [
            _result(
                "ruff",
                ["check", "--no-cache", "--output-format", "json", "--", "pkg/app.py"],
                tmp_path,
                raw_stdout=ruff_json,
                stdout_preview=ruff_json[:32] + "\n[TRUNCATED]",
                exit_code=1,
            )
        ]
    )

    diagnostics, records, safe_fixes = RuffAdapter(runner).check(
        tmp_path,
        [Path("pkg/app.py")],
        "standard",
    )

    assert len(records) == 1
    assert safe_fixes == []
    assert diagnostics[0].source == "ruff"
    assert diagnostics[0].code == "F401"
    assert diagnostics[0].file == "pkg/app.py"


def test_ruff_adapter_skips_safe_fix_preview_after_primary_timeout(tmp_path: Path) -> None:
    runner = StubRunner(
        [
            _record(
                "ruff",
                ["check", "--no-cache", "--output-format", "json", "--", "pkg/app.py"],
                tmp_path,
                exit_code=None,
                timed_out=True,
            ),
            _record(
                "ruff",
                ["check", "--no-cache", "--fix", "--diff", "--", "pkg/app.py"],
                tmp_path,
                stdout="--- partial diff\n",
            ),
        ]
    )

    diagnostics, records, safe_fixes = RuffAdapter(runner).check(
        tmp_path,
        [Path("pkg/app.py")],
        "standard",
        preview_safe_fixes=True,
    )

    assert runner.calls == [
        ("ruff", ["check", "--no-cache", "--output-format", "json", "--", "pkg/app.py"], tmp_path)
    ]
    assert len(records) == 1
    assert safe_fixes == []
    assert diagnostics[0].source == "ruff"
    assert diagnostics[0].severity == DiagnosticSeverity.WARNING
    assert diagnostics[0].code == "timeout"


def test_ruff_adapter_ignores_timed_out_safe_fix_preview_partial_stdout(
    tmp_path: Path,
) -> None:
    runner = StubRunner(
        [
            _record(
                "ruff",
                ["check", "--no-cache", "--output-format", "json", "--", "pkg/app.py"],
                tmp_path,
                stdout="[]",
            ),
            _record(
                "ruff",
                ["check", "--no-cache", "--fix", "--diff", "--", "pkg/app.py"],
                tmp_path,
                stdout="--- partial diff\n",
                exit_code=None,
                timed_out=True,
            ),
        ]
    )

    diagnostics, records, safe_fixes = RuffAdapter(runner).check(
        tmp_path,
        [Path("pkg/app.py")],
        "standard",
        preview_safe_fixes=True,
    )

    assert runner.calls == [
        ("ruff", ["check", "--no-cache", "--output-format", "json", "--", "pkg/app.py"], tmp_path),
        ("ruff", ["check", "--no-cache", "--fix", "--diff", "--", "pkg/app.py"], tmp_path),
    ]
    assert len(records) == 2
    assert safe_fixes == []
    assert diagnostics[0].source == "ruff"
    assert diagnostics[0].severity == DiagnosticSeverity.WARNING
    assert diagnostics[0].code == "timeout"


def test_ruff_adapter_skips_unsafe_changed_file_paths(tmp_path: Path) -> None:
    runner = StubRunner(
        [
            _record(
                "ruff",
                ["check", "--no-cache", "--output-format", "json", "--", "pkg/app.py"],
                tmp_path,
                stdout="[]",
            )
        ]
    )

    diagnostics, records, safe_fixes = RuffAdapter(runner).check(
        tmp_path,
        [
            Path("../outside.py"),
            tmp_path / "absolute.py",
            Path("--fix"),
            Path("."),
            Path("pkg/app.py"),
        ],
        "standard",
    )

    assert safe_fixes == []
    assert len(records) == 1
    assert runner.calls == [
        ("ruff", ["check", "--no-cache", "--output-format", "json", "--", "pkg/app.py"], tmp_path)
    ]
    unsafe_diagnostics = [
        diagnostic for diagnostic in diagnostics if diagnostic.code == "unsafe_path"
    ]
    assert [diagnostic.file for diagnostic in unsafe_diagnostics] == [
        "../outside.py",
        str(tmp_path / "absolute.py"),
        "--fix",
        ".",
    ]


def test_ruff_adapter_skips_tool_when_all_changed_files_are_unsafe(tmp_path: Path) -> None:
    runner = StubRunner(
        [_record("ruff", ["check", "--output-format", "json"], tmp_path, stdout="[]")]
    )

    diagnostics, records, safe_fixes = RuffAdapter(runner).check(
        tmp_path,
        [Path("../outside.py"), Path("--fix")],
        "standard",
    )

    assert records == []
    assert safe_fixes == []
    assert runner.calls == []
    assert [diagnostic.code for diagnostic in diagnostics] == ["unsafe_path", "unsafe_path"]


def test_ruff_adapter_rejects_directory_changed_file_targets(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    runner = StubRunner(
        [
            _record(
                "ruff",
                ["check", "--no-cache", "--output-format", "json", "--", "pkg"],
                tmp_path,
                stdout="[]",
            )
        ]
    )

    diagnostics, records, safe_fixes = RuffAdapter(runner).check(
        tmp_path,
        [Path("pkg")],
        "standard",
    )

    assert records == []
    assert safe_fixes == []
    assert runner.calls == []
    assert diagnostics[0].source == "ruff"
    assert diagnostics[0].code == "unsafe_path"
    assert diagnostics[0].file == "pkg"


def test_ruff_adapter_skips_symlink_escape_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "app.py").write_text("print('escape')\n", encoding="utf-8")
    (workspace / "pkg").symlink_to(outside, target_is_directory=True)
    runner = StubRunner(
        [
            _record(
                "ruff",
                ["check", "--no-cache", "--output-format", "json", "--", "pkg/app.py"],
                workspace,
                stdout="[]",
            )
        ]
    )

    diagnostics, records, safe_fixes = RuffAdapter(runner).check(
        workspace,
        [Path("pkg/app.py")],
        "standard",
    )

    assert records == []
    assert safe_fixes == []
    assert runner.calls == []
    assert diagnostics[0].code == "unsafe_path"
    assert diagnostics[0].file == "pkg/app.py"


def test_pyright_adapter_quick_mode_includes_changed_files_and_parses_json(
    tmp_path: Path,
) -> None:
    pyright_json = json.dumps(
        {
            "generalDiagnostics": [
                {
                    "file": "pkg/app.py",
                    "severity": "error",
                    "message": "Type mismatch",
                    "rule": "reportAssignmentType",
                    "range": {
                        "start": {"line": 2, "character": 4},
                        "end": {"line": 2, "character": 12},
                    },
                }
            ]
        }
    )
    runner = StubRunner(
        [
            _record(
                "pyright",
                ["--outputjson", "pkg/app.py"],
                tmp_path,
                stdout=pyright_json,
                exit_code=1,
            )
        ]
    )

    diagnostics, records = PyrightAdapter(runner).check(
        tmp_path,
        [Path("pkg/app.py")],
        "quick",
    )

    assert runner.calls == [("pyright", ["--outputjson", "pkg/app.py"], tmp_path)]
    assert len(records) == 1
    assert len(diagnostics) == 1
    assert diagnostics[0].source == "pyright"
    assert diagnostics[0].code == "reportAssignmentType"
    assert diagnostics[0].severity == DiagnosticSeverity.ERROR
    assert diagnostics[0].is_blocking is True


def test_pyright_adapter_parses_full_stdout_when_record_preview_is_truncated(
    tmp_path: Path,
) -> None:
    pyright_json = json.dumps(
        {
            "generalDiagnostics": [
                {
                    "file": "pkg/app.py",
                    "severity": "error",
                    "message": "x" * 200,
                    "rule": "reportGeneralTypeIssues",
                }
            ]
        }
    )
    runner = StubRunner(
        [
            _result(
                "pyright",
                ["--outputjson"],
                tmp_path,
                raw_stdout=pyright_json,
                stdout_preview=pyright_json[:32] + "\n[TRUNCATED]",
                exit_code=1,
            )
        ]
    )

    diagnostics, records = PyrightAdapter(runner).check(tmp_path, [], "standard")

    assert len(records) == 1
    assert diagnostics[0].source == "pyright"
    assert diagnostics[0].code == "reportGeneralTypeIssues"
    assert diagnostics[0].is_blocking is True


def test_pyright_adapter_standard_mode_omits_changed_files(tmp_path: Path) -> None:
    runner = StubRunner([_record("pyright", ["--outputjson"], tmp_path, stdout="{}")])

    diagnostics, records = PyrightAdapter(runner).check(
        tmp_path,
        [Path("pkg/app.py")],
        "standard",
    )

    assert diagnostics == []
    assert len(records) == 1
    assert runner.calls == [("pyright", ["--outputjson"], tmp_path)]


def test_pyright_adapter_strict_mode_omits_changed_files(tmp_path: Path) -> None:
    runner = StubRunner([_record("pyright", ["--outputjson"], tmp_path, stdout="{}")])

    diagnostics, records = PyrightAdapter(runner).check(
        tmp_path,
        [Path("pkg/app.py")],
        "strict",
    )

    assert diagnostics == []
    assert len(records) == 1
    assert runner.calls == [("pyright", ["--outputjson"], tmp_path)]


def test_pyright_adapter_quick_mode_skips_option_like_paths_instead_of_running_them(
    tmp_path: Path,
) -> None:
    runner = StubRunner([_record("pyright", ["--outputjson"], tmp_path, stdout="{}")])

    diagnostics, records = PyrightAdapter(runner).check(tmp_path, [Path("--stats")], "quick")

    assert runner.calls == []
    assert records == []
    assert diagnostics[0].source == "pyright"
    assert diagnostics[0].code == "unsafe_path"
    assert diagnostics[0].file == "--stats"


def test_pyright_adapter_quick_mode_skips_unsafe_changed_file_paths(
    tmp_path: Path,
) -> None:
    runner = StubRunner([_record("pyright", ["--outputjson", "pkg/app.py"], tmp_path, stdout="{}")])

    diagnostics, records = PyrightAdapter(runner).check(
        tmp_path,
        [
            Path("../outside.py"),
            tmp_path / "absolute.py",
            Path("pkg/\napp.py"),
            Path("."),
            Path("pkg/app.py"),
        ],
        "quick",
    )

    assert len(records) == 1
    assert runner.calls == [("pyright", ["--outputjson", "pkg/app.py"], tmp_path)]
    unsafe_diagnostics = [
        diagnostic for diagnostic in diagnostics if diagnostic.code == "unsafe_path"
    ]
    assert [diagnostic.file for diagnostic in unsafe_diagnostics] == [
        "../outside.py",
        str(tmp_path / "absolute.py"),
        "pkg/\napp.py",
        ".",
    ]


def test_pyright_adapter_quick_mode_skips_tool_when_all_changed_files_are_unsafe(
    tmp_path: Path,
) -> None:
    runner = StubRunner([_record("pyright", ["--outputjson"], tmp_path, stdout="{}")])

    diagnostics, records = PyrightAdapter(runner).check(
        tmp_path,
        [Path("../outside.py"), Path("--stats")],
        "quick",
    )

    assert records == []
    assert runner.calls == []
    assert [diagnostic.code for diagnostic in diagnostics] == ["unsafe_path", "unsafe_path"]


def test_pyright_adapter_quick_mode_rejects_directory_changed_file_targets(
    tmp_path: Path,
) -> None:
    (tmp_path / "pkg").mkdir()
    runner = StubRunner([_record("pyright", ["--outputjson", "pkg"], tmp_path, stdout="{}")])

    diagnostics, records = PyrightAdapter(runner).check(
        tmp_path,
        [Path("pkg")],
        "quick",
    )

    assert records == []
    assert runner.calls == []
    assert diagnostics[0].source == "pyright"
    assert diagnostics[0].code == "unsafe_path"
    assert diagnostics[0].file == "pkg"


def test_pyright_adapter_quick_mode_skips_symlink_escape_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "app.py").write_text("print('escape')\n", encoding="utf-8")
    (workspace / "pkg").symlink_to(outside, target_is_directory=True)
    runner = StubRunner(
        [_record("pyright", ["--outputjson", "pkg/app.py"], workspace, stdout="{}")]
    )

    diagnostics, records = PyrightAdapter(runner).check(
        workspace,
        [Path("pkg/app.py")],
        "quick",
    )

    assert records == []
    assert runner.calls == []
    assert diagnostics[0].code == "unsafe_path"
    assert diagnostics[0].file == "pkg/app.py"


def test_pyright_adapter_reports_invalid_json_as_warning_with_records(tmp_path: Path) -> None:
    runner = StubRunner(
        [_record("pyright", ["--outputjson"], tmp_path, stdout="{broken", exit_code=1)]
    )

    diagnostics, records = PyrightAdapter(runner).check(tmp_path, [], "standard")

    assert len(records) == 1
    assert diagnostics[0].source == "pyright"
    assert diagnostics[0].severity == DiagnosticSeverity.WARNING
    assert diagnostics[0].code == "invalid_json"


def test_uv_adapter_runs_version_and_lock_check_for_standard_mode_with_pyproject(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    runner = StubRunner(
        [
            _record("uv", ["--version"], tmp_path, stdout="uv 0.8.0\n"),
            _record("uv", ["lock", "--check"], tmp_path),
        ]
    )

    diagnostics, records = UvAdapter(runner).check(tmp_path, "standard")

    assert diagnostics == []
    assert len(records) == 2
    assert runner.calls == [
        ("uv", ["--version"], tmp_path),
        ("uv", ["lock", "--check"], tmp_path),
    ]


def test_uv_adapter_runs_sync_locked_dry_run_when_configured(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    runner = StubRunner(
        [
            _record("uv", ["--version"], tmp_path, stdout="uv 0.8.0\n"),
            _record("uv", ["sync", "--locked", "--dry-run"], tmp_path),
        ],
        config=AgentQualityConfig(uv_sync_dry_run=True),
    )

    diagnostics, records = UvAdapter(runner).check(tmp_path, "standard")

    assert diagnostics == []
    assert len(records) == 2
    assert runner.calls == [
        ("uv", ["--version"], tmp_path),
        ("uv", ["sync", "--locked", "--dry-run"], tmp_path),
    ]
