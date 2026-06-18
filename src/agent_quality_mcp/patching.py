"""Safe unified-diff application for shadow workspaces."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agent_quality_mcp.exceptions import PatchApplyError, SecurityError
from agent_quality_mcp.paths import ensure_within_directory

HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?: .*)?$",
)

_NO_NEWLINE_MARKER = r"\ No newline at end of file"
_UNSUPPORTED_PREFIXES = (
    "Binary files ",
    "GIT binary patch",
    "old mode ",
    "new mode ",
    "deleted file mode ",
    "new file mode ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
)


@dataclass(frozen=True)
class HunkLine:
    """One context, removal, or addition line in a hunk."""

    kind: str
    text: str


@dataclass(frozen=True)
class Hunk:
    """A parsed unified-diff hunk."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[HunkLine]


@dataclass(frozen=True)
class FilePatch:
    """A parsed file-level unified-diff patch."""

    old_path: Path | None
    new_path: Path | None
    hunks: list[Hunk]


def apply_unified_diff(shadow_root: Path, changed_files: list[Path], patch_text: str) -> None:
    """Apply a standard text unified diff inside a shadow workspace."""

    root = shadow_root.resolve()
    file_patches = _parse_patch(patch_text)
    changed = {_normalize_relative_path(path.as_posix()) for path in changed_files}
    targets = {_patch_target(file_patch) for file_patch in file_patches}
    if targets != changed:
        raise SecurityError("patch targets must exactly match changed_files")

    writes: list[tuple[Path, str | None]] = []
    for file_patch in file_patches:
        relative_target = _patch_target(file_patch)
        target = ensure_within_directory(root, root / relative_target)
        if file_patch.old_path is None:
            if target.exists():
                raise PatchApplyError(f"target already exists: {relative_target.as_posix()}")
            original = ""
        else:
            if not target.exists():
                raise PatchApplyError(f"target does not exist: {relative_target.as_posix()}")
            original = _read_utf8(target)

        patched = _apply_file_patch(original, file_patch)
        if file_patch.new_path is None:
            if patched != "":
                message = f"deletion patch leaves content: {relative_target.as_posix()}"
                raise PatchApplyError(message)
            writes.append((target, None))
        else:
            writes.append((target, patched))

    for target, content in writes:
        if content is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_utf8(target, content)


def _parse_patch(patch_text: str) -> list[FilePatch]:
    if "\0" in patch_text:
        raise PatchApplyError("binary patch text is not supported")

    lines = patch_text.splitlines()
    patches: list[FilePatch] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        _reject_unsupported_line(line)
        if not line or line.startswith(("diff --git ", "index ")):
            index += 1
            continue
        if line.startswith("--- "):
            file_patch, index = _parse_file_patch(lines, index)
            patches.append(file_patch)
            continue
        raise PatchApplyError(f"unsupported unified diff line: {line}")

    if not patches:
        raise PatchApplyError("patch does not contain a unified diff")
    return patches


def _parse_file_patch(lines: list[str], index: int) -> tuple[FilePatch, int]:
    old_path = _parse_patch_path(lines[index], "--- ")
    index += 1
    if index >= len(lines) or not lines[index].startswith("+++ "):
        raise PatchApplyError("file patch is missing new path header")
    new_path = _parse_patch_path(lines[index], "+++ ")
    index += 1
    if old_path is not None and new_path is not None and old_path != new_path:
        raise PatchApplyError("renaming files is not supported")

    hunks: list[Hunk] = []
    while index < len(lines):
        line = lines[index]
        _reject_unsupported_line(line)
        if line.startswith(("diff --git ", "--- ")):
            break
        if not line:
            index += 1
            continue
        if line.startswith("@@"):
            hunk, index = _parse_hunk(lines, index)
            hunks.append(hunk)
            continue
        raise PatchApplyError(f"unsupported file patch line: {line}")

    if not hunks:
        raise PatchApplyError("file patch does not contain hunks")
    return FilePatch(old_path=old_path, new_path=new_path, hunks=hunks), index


def _parse_hunk(lines: list[str], index: int) -> tuple[Hunk, int]:
    header = lines[index]
    match = HUNK_RE.match(header)
    if match is None:
        raise PatchApplyError(f"malformed hunk header: {header}")

    old_start = int(match.group("old_start"))
    old_count = _hunk_count(match.group("old_count"))
    new_start = int(match.group("new_start"))
    new_count = _hunk_count(match.group("new_count"))
    _validate_hunk_range("old", old_start, old_count)
    _validate_hunk_range("new", new_start, new_count)
    index += 1
    hunk_lines: list[HunkLine] = []
    old_seen = 0
    new_seen = 0

    while index < len(lines):
        line = lines[index]
        _reject_unsupported_line(line)
        hunk_complete = old_seen == old_count and new_seen == new_count
        if line.startswith(("@@", "diff --git ")) or (hunk_complete and line.startswith("--- ")):
            break
        if line == _NO_NEWLINE_MARKER:
            _remove_trailing_newline(hunk_lines)
            index += 1
            continue
        if not line or line[0] not in {" ", "-", "+"}:
            raise PatchApplyError(f"malformed hunk line: {line}")
        kind = line[0]
        hunk_lines.append(HunkLine(kind=kind, text=f"{line[1:]}\n"))
        if kind in {" ", "-"}:
            old_seen += 1
        if kind in {" ", "+"}:
            new_seen += 1
        index += 1

    if old_seen != old_count or new_seen != new_count:
        raise PatchApplyError("hunk line counts do not match header")
    return (
        Hunk(
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            lines=hunk_lines,
        ),
        index,
    )


def _hunk_count(value: str | None) -> int:
    if value is None:
        return 1
    return int(value)


def _validate_hunk_range(side: str, start: int, count: int) -> None:
    if start == 0 and count == 0:
        return
    if start == 0:
        raise PatchApplyError(f"{side} hunk range start 0 requires count 0")


def _remove_trailing_newline(hunk_lines: list[HunkLine]) -> None:
    if not hunk_lines:
        raise PatchApplyError("no-newline marker has no preceding hunk line")
    previous = hunk_lines[-1]
    if previous.text.endswith("\n"):
        hunk_lines[-1] = HunkLine(kind=previous.kind, text=previous.text[:-1])


def _parse_patch_path(line: str, prefix: str) -> Path | None:
    raw_path = line.removeprefix(prefix).strip()
    if "\t" in raw_path:
        raw_path = raw_path.split("\t", 1)[0]
    if raw_path == "/dev/null":
        return None
    if raw_path.startswith(("a/", "b/")):
        raw_path = raw_path[2:]
    return _normalize_relative_path(raw_path)


def _normalize_relative_path(path_text: str) -> Path:
    if path_text in {"", "."}:
        raise SecurityError("patch paths must identify a relative file")
    if "\0" in path_text:
        raise SecurityError("patch paths must not contain null bytes")
    if "\\" in path_text:
        raise SecurityError("patch paths must use forward slash separators")
    pure = PurePosixPath(path_text)
    if pure.is_absolute():
        raise SecurityError("patch paths must be relative")
    parts = pure.parts
    if any(part in {"", ".", ".."} for part in parts):
        raise SecurityError("patch paths must not contain empty, dot, or dot-dot segments")
    if any(len(part) >= 2 and part[1] == ":" and part[0].isalpha() for part in parts):
        raise SecurityError("patch paths must not contain drive prefixes")
    return Path(*parts)


def _patch_target(file_patch: FilePatch) -> Path:
    target = file_patch.new_path if file_patch.new_path is not None else file_patch.old_path
    if target is None:
        raise PatchApplyError("file patch cannot target /dev/null twice")
    return target


def _apply_file_patch(original: str, file_patch: FilePatch) -> str:
    original_lines = original.splitlines(keepends=True)
    patched_lines: list[str] = []
    original_index = 0

    for hunk in file_patch.hunks:
        hunk_index = _hunk_original_index(hunk)
        if hunk_index < original_index or hunk_index > len(original_lines):
            raise PatchApplyError("hunk starts outside target content")
        patched_lines.extend(original_lines[original_index:hunk_index])
        original_index = hunk_index

        for hunk_line in hunk.lines:
            if hunk_line.kind == "+":
                patched_lines.append(hunk_line.text)
                continue
            if original_index >= len(original_lines):
                raise PatchApplyError("hunk exceeds target content")
            if original_lines[original_index] != hunk_line.text:
                raise PatchApplyError("hunk context does not match target content")
            if hunk_line.kind == " ":
                patched_lines.append(hunk_line.text)
            original_index += 1

    patched_lines.extend(original_lines[original_index:])
    return "".join(patched_lines)


def _hunk_original_index(hunk: Hunk) -> int:
    if hunk.old_start == 0:
        if hunk.old_count != 0:
            raise PatchApplyError("zero hunk start requires zero old line count")
        return 0
    if hunk.old_count == 0:
        return hunk.old_start
    return hunk.old_start - 1


def _reject_unsupported_line(line: str) -> None:
    if line.startswith(_UNSUPPORTED_PREFIXES):
        raise PatchApplyError(f"unsupported patch feature: {line}")


def _read_utf8(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except UnicodeDecodeError as exc:
        raise PatchApplyError(f"target is not valid UTF-8: {path}") from exc


def _write_utf8(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)
