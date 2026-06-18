"""Safe unified-diff application for shadow workspaces."""

from __future__ import annotations

import os
import re
import stat
import tempfile
import unicodedata
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
    "diff --git ",
    "GIT binary patch",
    "index ",
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


@dataclass
class NoNewlineState:
    """Tracks sides closed by no-newline markers within one file patch."""

    old_closed: bool = False
    new_closed: bool = False
    old_marker_seen: bool = False
    new_marker_seen: bool = False


@dataclass(frozen=True)
class WriteOperation:
    """A staged write or deletion for a patch target."""

    target: Path
    relative_target: Path
    content: str | None


@dataclass(frozen=True)
class PreparedWrite:
    """A write operation with any replacement content materialized."""

    operation: WriteOperation
    temp_path: Path | None


@dataclass
class CommitRecord:
    """A committed filesystem step that can be rolled back."""

    target: Path
    relative_target: Path
    backup_path: Path | None
    target_written: bool = False


def apply_unified_diff(shadow_root: Path, changed_files: list[Path], patch_text: str) -> None:
    """Apply a standard text unified diff inside a shadow workspace."""

    root = shadow_root.resolve()
    file_patches = _parse_patch(patch_text)
    changed = [_normalize_relative_path(path.as_posix()) for path in changed_files]
    if len(set(changed)) != len(changed):
        raise PatchApplyError("changed_files contains duplicate file targets")
    targets = [_patch_target(file_patch) for file_patch in file_patches]
    if len(set(targets)) != len(targets):
        raise PatchApplyError("patch contains duplicate file targets")
    if set(targets) != set(changed):
        raise SecurityError("patch targets must exactly match changed_files")
    target_paths = {target: _validate_literal_target(root, target) for target in targets}
    _validate_target_collisions(root, targets, target_paths)

    writes: list[WriteOperation] = []
    for file_patch in file_patches:
        relative_target = _patch_target(file_patch)
        target = target_paths[relative_target]
        if file_patch.old_path is None:
            if target.exists():
                raise PatchApplyError(f"target already exists: {relative_target.as_posix()}")
            _validate_target_parent(target, relative_target)
            original = ""
        else:
            if not target.exists():
                raise PatchApplyError(f"target does not exist: {relative_target.as_posix()}")
            _validate_existing_patch_target(target, relative_target)
            original = _read_utf8(target)

        patched = _apply_file_patch(original, file_patch)
        if file_patch.new_path is None:
            if patched != "":
                message = f"deletion patch leaves content: {relative_target.as_posix()}"
                raise PatchApplyError(message)
            writes.append(WriteOperation(target, relative_target, None))
        else:
            writes.append(WriteOperation(target, relative_target, patched))

    _apply_write_operations(writes)


def _parse_patch(patch_text: str) -> list[FilePatch]:
    if "\0" in patch_text:
        raise PatchApplyError("binary patch text is not supported")

    lines = _split_patch_lines(patch_text)
    patches: list[FilePatch] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        _reject_unsupported_line(line)
        if not line:
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
    no_newline_state = NoNewlineState()
    while index < len(lines):
        line = lines[index]
        _reject_unsupported_line(line)
        if line.startswith(("diff --git ", "--- ")):
            break
        if not line:
            index += 1
            continue
        if line.startswith("@@"):
            hunk, index = _parse_hunk(lines, index, no_newline_state)
            hunks.append(hunk)
            continue
        raise PatchApplyError(f"unsupported file patch line: {line}")

    if not hunks:
        raise PatchApplyError("file patch does not contain hunks")
    file_patch = FilePatch(old_path=old_path, new_path=new_path, hunks=hunks)
    _validate_file_hunk_ranges(file_patch)
    return file_patch, index


def _parse_hunk(
    lines: list[str],
    index: int,
    no_newline_state: NoNewlineState,
) -> tuple[Hunk, int]:
    header = lines[index]
    match = HUNK_RE.match(header)
    if match is None:
        raise PatchApplyError(f"malformed hunk header: {header}")

    old_start = int(match.group("old_start"))
    old_count = _hunk_count(match.group("old_count"))
    new_start = int(match.group("new_start"))
    new_count = _hunk_count(match.group("new_count"))
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
            marker_kind = _remove_trailing_newline(hunk_lines)
            _record_no_newline_marker(marker_kind, no_newline_state)
            index += 1
            continue
        if not line or line[0] not in {" ", "-", "+"}:
            raise PatchApplyError(f"malformed hunk line: {line}")
        kind = line[0]
        _validate_no_newline_side_open(kind, no_newline_state)
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


def _split_patch_lines(patch_text: str) -> list[str]:
    lines = patch_text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _validate_file_hunk_ranges(file_patch: FilePatch) -> None:
    for hunk in file_patch.hunks:
        zero_line_hunk = not hunk.lines
        if file_patch.old_path is None:
            _validate_dev_null_hunk_range("old", hunk.old_start, hunk.old_count)
        else:
            _validate_hunk_range(
                "old",
                hunk.old_start,
                hunk.old_count,
                zero_start_allowed=file_patch.new_path is not None,
                zero_count_allowed=zero_line_hunk,
            )
        if file_patch.new_path is None:
            _validate_dev_null_hunk_range("new", hunk.new_start, hunk.new_count)
        else:
            _validate_hunk_range(
                "new",
                hunk.new_start,
                hunk.new_count,
                zero_start_allowed=file_patch.old_path is not None,
                zero_count_allowed=zero_line_hunk,
            )


def _validate_dev_null_hunk_range(side: str, start: int, count: int) -> None:
    if start == 0 and count == 0:
        return
    raise PatchApplyError(f"{side} /dev/null hunk range must be 0,0")


def _validate_hunk_range(
    side: str,
    start: int,
    count: int,
    *,
    zero_start_allowed: bool,
    zero_count_allowed: bool,
) -> None:
    if start == 0 and count == 0 and (zero_start_allowed or zero_count_allowed):
        return
    if start == 0:
        raise PatchApplyError(f"{side} hunk range start 0 is invalid for this file patch")


def _validate_no_newline_side_open(kind: str, state: NoNewlineState) -> None:
    if kind in {" ", "-"} and state.old_closed:
        raise PatchApplyError("old-side hunk line appears after no-newline marker")
    if kind in {" ", "+"} and state.new_closed:
        raise PatchApplyError("new-side hunk line appears after no-newline marker")


def _record_no_newline_marker(kind: str, state: NoNewlineState) -> None:
    closes_old = kind in {" ", "-"}
    closes_new = kind in {" ", "+"}
    if closes_old:
        if state.old_marker_seen:
            raise PatchApplyError("duplicate old-side no-newline marker")
        state.old_marker_seen = True
        state.old_closed = True
    if closes_new:
        if state.new_marker_seen:
            raise PatchApplyError("duplicate new-side no-newline marker")
        state.new_marker_seen = True
        state.new_closed = True


def _remove_trailing_newline(hunk_lines: list[HunkLine]) -> str:
    if not hunk_lines:
        raise PatchApplyError("no-newline marker has no preceding hunk line")
    previous = hunk_lines[-1]
    if previous.text.endswith("\n"):
        hunk_lines[-1] = HunkLine(kind=previous.kind, text=previous.text[:-1])
    return previous.kind


def _parse_patch_path(line: str, prefix: str) -> Path | None:
    raw_path = line.removeprefix(prefix)
    if "\t" in raw_path:
        raw_path = raw_path.split("\t", 1)[0]
    if raw_path != raw_path.strip():
        raise PatchApplyError("patch path contains unexpected leading or trailing whitespace")
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
    parts = tuple(path_text.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise SecurityError("patch paths must not contain empty, dot, or dot-dot segments")
    pure = PurePosixPath(path_text)
    if pure.is_absolute():
        raise SecurityError("patch paths must be relative")
    if any(len(part) >= 2 and part[1] == ":" and part[0].isalpha() for part in parts):
        raise SecurityError("patch paths must not contain drive prefixes")
    return Path(*parts)


def _patch_target(file_patch: FilePatch) -> Path:
    target = file_patch.new_path if file_patch.new_path is not None else file_patch.old_path
    if target is None:
        raise PatchApplyError("file patch cannot target /dev/null twice")
    return target


def _validate_literal_target(root: Path, relative_target: Path) -> Path:
    candidate = root / relative_target
    current = root
    for part in relative_target.parts:
        current /= part
        if current.is_symlink():
            message = f"patch target must not include symlinks: {relative_target.as_posix()}"
            raise SecurityError(message)
        if not current.exists():
            break
    if candidate.exists():
        ensure_within_directory(root, candidate)
    else:
        ensure_within_directory(root, candidate.parent)
    return candidate


def _validate_target_collisions(
    root: Path,
    targets: list[Path],
    target_paths: dict[Path, Path],
) -> None:
    _validate_target_hierarchy(targets)
    existing_targets: dict[tuple[int, int], Path] = {}
    planned_creates: dict[tuple[tuple[int, int], tuple[str, ...], str], Path] = {}
    for relative_target in targets:
        target = target_paths[relative_target]
        if target.exists():
            target_stat = _stat_target(target, relative_target)
            key = (target_stat.st_dev, target_stat.st_ino)
            previous = existing_targets.get(key)
            if previous is not None:
                raise SecurityError("patch targets must not resolve to the same file")
            existing_targets[key] = relative_target
            continue

        key = _planned_create_collision_key(root, target)
        previous = planned_creates.get(key)
        if previous is not None:
            raise SecurityError("planned creates must not collide by normalized name")
        planned_creates[key] = relative_target


def _validate_target_hierarchy(targets: list[Path]) -> None:
    normalized_targets = [
        (tuple(_normalized_name(part) for part in target.parts), target)
        for target in targets
    ]
    for index, (left_parts, left_target) in enumerate(normalized_targets):
        for right_parts, right_target in normalized_targets[index + 1 :]:
            if _is_ancestor_path(left_parts, right_parts) or _is_ancestor_path(
                right_parts,
                left_parts,
            ):
                message = (
                    "patch targets must not contain ancestor/descendant pairs: "
                    f"{left_target.as_posix()}, {right_target.as_posix()}"
                )
                raise SecurityError(message)


def _is_ancestor_path(
    candidate_parts: tuple[str, ...],
    descendant_parts: tuple[str, ...],
) -> bool:
    return len(candidate_parts) < len(descendant_parts) and (
        descendant_parts[: len(candidate_parts)] == candidate_parts
    )


def _planned_create_collision_key(
    root: Path,
    target: Path,
) -> tuple[tuple[int, int], tuple[str, ...], str]:
    existing_parent = target.parent
    missing_parts: list[str] = []
    while not existing_parent.exists():
        missing_parts.append(_normalized_name(existing_parent.name))
        existing_parent = existing_parent.parent
    if not existing_parent.is_relative_to(root):
        raise SecurityError("patch target parent must remain inside shadow root")
    parent_stat = _stat_target(existing_parent, Path("."))
    return (
        (parent_stat.st_dev, parent_stat.st_ino),
        tuple(reversed(missing_parts)),
        _normalized_name(target.name),
    )


def _normalized_name(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _validate_existing_patch_target(target: Path, relative_target: Path) -> None:
    target_stat = _stat_target(target, relative_target)
    if not stat.S_ISREG(target_stat.st_mode):
        raise PatchApplyError(f"target is not a regular file: {relative_target.as_posix()}")
    if target_stat.st_nlink > 1:
        raise SecurityError(f"patch target must not be a hard link: {relative_target.as_posix()}")


def _stat_target(target: Path, relative_target: Path) -> os.stat_result:
    try:
        return target.stat(follow_symlinks=False)
    except OSError as exc:
        raise PatchApplyError(f"failed to inspect target: {relative_target.as_posix()}") from exc


def _validate_target_parent(target: Path, relative_target: Path) -> None:
    existing = target.parent
    while not existing.exists():
        existing = existing.parent
    if not existing.is_dir():
        raise PatchApplyError(f"target parent is not a directory: {relative_target.as_posix()}")


def _create_target_parent(target: Path, relative_target: Path) -> list[Path]:
    missing_parents: list[Path] = []
    current = target.parent
    while not current.exists():
        missing_parents.append(current)
        current = current.parent
    if not current.is_dir():
        raise PatchApplyError(f"target parent is not a directory: {relative_target.as_posix()}")

    created: list[Path] = []
    try:
        for parent in reversed(missing_parents):
            parent.mkdir()
            created.append(parent)
    except OSError as exc:
        _remove_created_dirs(created)
        message = f"failed to create target parent: {relative_target.as_posix()}"
        raise PatchApplyError(message) from exc
    return created


def _apply_write_operations(writes: list[WriteOperation]) -> None:
    _preflight_write_operations(writes)
    prepared: list[PreparedWrite] = []
    created_dirs: list[Path] = []
    committed = False
    try:
        for operation in writes:
            temp_path = None
            if operation.content is not None:
                created_dirs.extend(
                    _create_target_parent(operation.target, operation.relative_target),
                )
                temp_path = _write_temp_utf8(operation)
            prepared.append(PreparedWrite(operation, temp_path))
        _commit_prepared_writes(prepared)
        committed = True
    finally:
        for prepared_write in prepared:
            if prepared_write.temp_path is not None:
                _safe_unlink(prepared_write.temp_path)
        if not committed:
            _remove_created_dirs(created_dirs)


def _preflight_write_operations(writes: list[WriteOperation]) -> None:
    for operation in writes:
        _validate_target_parent(operation.target, operation.relative_target)
        if operation.target.exists():
            _validate_existing_patch_target(operation.target, operation.relative_target)


def _write_temp_utf8(operation: WriteOperation) -> Path:
    file_descriptor: int | None = None
    temp_path: Path | None = None
    content = operation.content or ""
    try:
        content.encode("utf-8")
    except UnicodeEncodeError as exc:
        message = f"patch content is not valid UTF-8: {operation.relative_target.as_posix()}"
        raise PatchApplyError(message) from exc
    try:
        file_descriptor, raw_temp_path = tempfile.mkstemp(
            prefix=f".{operation.target.name}.",
            suffix=".tmp",
            dir=operation.target.parent,
        )
        temp_path = Path(raw_temp_path)
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as handle:
            file_descriptor = None
            handle.write(content)
        if operation.target.exists():
            target_stat = _stat_target(operation.target, operation.relative_target)
            temp_path.chmod(stat.S_IMODE(target_stat.st_mode))
        return temp_path
    except (OSError, UnicodeEncodeError) as exc:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if temp_path is not None:
            _safe_unlink(temp_path)
        message = f"failed to write temporary target: {operation.relative_target.as_posix()}"
        raise PatchApplyError(message) from exc


def _commit_prepared_writes(prepared_writes: list[PreparedWrite]) -> None:
    committed: list[CommitRecord] = []
    try:
        for prepared_write in prepared_writes:
            operation = prepared_write.operation
            backup_path = None
            if operation.target.exists():
                backup_path = _move_target_to_backup(operation)
            record = CommitRecord(operation.target, operation.relative_target, backup_path)
            committed.append(record)
            if prepared_write.temp_path is not None:
                _replace_path(
                    prepared_write.temp_path,
                    operation.target,
                    operation.relative_target,
                )
                record.target_written = True
    except PatchApplyError as exc:
        rollback_error = _rollback_committed_writes(committed)
        if rollback_error is not None:
            raise rollback_error from exc
        raise
    except OSError as exc:
        rollback_error = _rollback_committed_writes(committed)
        if rollback_error is not None:
            raise rollback_error from exc
        raise PatchApplyError("failed to commit patch writes") from exc
    else:
        for record in committed:
            if record.backup_path is not None:
                _safe_unlink(record.backup_path)


def _move_target_to_backup(operation: WriteOperation) -> Path:
    backup_path = _backup_path(operation.target)
    try:
        _replace_path(operation.target, backup_path, operation.relative_target)
    except PatchApplyError:
        _safe_unlink(backup_path)
        raise
    return backup_path


def _backup_path(target: Path) -> Path:
    file_descriptor: int | None = None
    try:
        file_descriptor, raw_backup_path = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".bak",
            dir=target.parent,
        )
        return Path(raw_backup_path)
    except OSError as exc:
        raise PatchApplyError(f"failed to create backup for target: {target}") from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)


def _replace_path(source: Path, destination: Path, relative_target: Path) -> None:
    try:
        os.replace(source, destination)
    except OSError as exc:
        message = f"failed to replace target: {relative_target.as_posix()}"
        raise PatchApplyError(message) from exc


def _rollback_committed_writes(committed: list[CommitRecord]) -> PatchApplyError | None:
    failures: list[tuple[Path, PatchApplyError]] = []
    for record in reversed(committed):
        try:
            if record.target_written:
                _unlink_rollback_target(record.target, record.relative_target)
            if record.backup_path is not None and record.backup_path.exists():
                _replace_path(record.backup_path, record.target, record.relative_target)
        except PatchApplyError as exc:
            failures.append((record.relative_target, exc))
    if not failures:
        return None
    failed_targets = ", ".join(target.as_posix() for target, _ in failures)
    return PatchApplyError(f"failed to roll back patch writes: {failed_targets}")


def _unlink_rollback_target(path: Path, relative_target: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        message = f"failed to remove rollback target: {relative_target.as_posix()}"
        raise PatchApplyError(message) from exc


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def _remove_created_dirs(created_dirs: list[Path]) -> None:
    for directory in sorted(set(created_dirs), key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            continue


def _apply_file_patch(original: str, file_patch: FilePatch) -> str:
    original_lines = original.splitlines(keepends=True)
    patched_lines: list[str] = []
    original_index = 0

    for hunk in file_patch.hunks:
        hunk_index = _hunk_original_index(
            hunk,
            original_index=original_index,
            original_line_count=len(original_lines),
            patched_line_count=len(patched_lines),
        )
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


def _hunk_original_index(
    hunk: Hunk,
    *,
    original_index: int,
    original_line_count: int,
    patched_line_count: int,
) -> int:
    new_index = _hunk_new_index(hunk)
    has_in_bounds_candidate = False
    for candidate in _hunk_original_index_candidates(hunk):
        if candidate < original_index or candidate > original_line_count:
            continue
        has_in_bounds_candidate = True
        patched_position = patched_line_count + candidate - original_index
        if patched_position == new_index:
            return candidate
    if not has_in_bounds_candidate:
        raise PatchApplyError("hunk starts outside target content")
    raise PatchApplyError("hunk new range does not match patched output position")


def _hunk_original_index_candidates(hunk: Hunk) -> tuple[int, ...]:
    if hunk.old_start == 0:
        if hunk.old_count != 0:
            raise PatchApplyError("zero hunk start requires zero old line count")
        return (0,)
    if hunk.old_count == 0:
        if hunk.new_count > 0:
            return (hunk.old_start - 1, hunk.old_start)
        return (hunk.old_start,)
    return (hunk.old_start - 1,)


def _hunk_new_index(hunk: Hunk) -> int:
    if hunk.new_start == 0:
        if hunk.new_count != 0:
            raise PatchApplyError("zero new hunk start requires zero new line count")
        return 0
    if hunk.new_count == 0:
        return hunk.new_start
    return hunk.new_start - 1


def _reject_unsupported_line(line: str) -> None:
    if line.startswith(_UNSUPPORTED_PREFIXES):
        raise PatchApplyError(f"unsupported patch feature: {line}")


def _read_utf8(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except UnicodeDecodeError as exc:
        raise PatchApplyError(f"target is not valid UTF-8: {path}") from exc
    except OSError as exc:
        raise PatchApplyError(f"failed to read target: {path}") from exc
