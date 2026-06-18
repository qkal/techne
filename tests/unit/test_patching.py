from __future__ import annotations

import inspect
import os
from pathlib import Path
from textwrap import dedent

import pytest  # pyright: ignore[reportMissingImports]

from agent_quality_mcp import patching
from agent_quality_mcp.exceptions import PatchApplyError, SecurityError
from agent_quality_mcp.patching import apply_unified_diff


def test_apply_unified_diff_modifies_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("alpha\nbeta\nomega\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1,3 +1,3 @@
         alpha
        -beta
        +gamma
         omega
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)

    assert target.read_text(encoding="utf-8") == "alpha\ngamma\nomega\n"


def test_apply_unified_diff_handles_zero_length_insertion_hunk(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("one\nthree\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1,0 +2 @@
        +two
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)

    assert target.read_text(encoding="utf-8") == "one\ntwo\nthree\n"


def test_apply_unified_diff_handles_removed_lines_that_look_like_file_headers(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("-- flag\nkeep\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1,2 +1 @@
        --- flag
         keep
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)

    assert target.read_text(encoding="utf-8") == "keep\n"


def test_apply_unified_diff_creates_file_from_dev_null(tmp_path: Path) -> None:
    patch_text = dedent(
        """\
        --- /dev/null
        +++ b/pkg/new.py
        @@ -0,0 +1,2 @@
        +created = True
        +value = 1
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/new.py")], patch_text)

    assert (tmp_path / "pkg" / "new.py").read_text(encoding="utf-8") == (
        "created = True\nvalue = 1\n"
    )


def test_apply_unified_diff_creates_file_with_single_line_hunk(tmp_path: Path) -> None:
    patch_text = dedent(
        """\
        --- /dev/null
        +++ b/pkg/new.py
        @@ -0,0 +1 @@
        +created = True
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/new.py")], patch_text)

    assert (tmp_path / "pkg" / "new.py").read_text(encoding="utf-8") == "created = True\n"


def test_apply_unified_diff_deletes_file_to_dev_null(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "gone.py"
    target.parent.mkdir()
    target.write_text("delete_me = True\nvalue = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/gone.py
        +++ /dev/null
        @@ -1,2 +0,0 @@
        -delete_me = True
        -value = 1
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/gone.py")], patch_text)

    assert not target.exists()


def test_apply_unified_diff_deletes_file_with_single_line_hunk(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "gone.py"
    target.parent.mkdir()
    target.write_text("delete_me = True\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/gone.py
        +++ /dev/null
        @@ -1 +0,0 @@
        -delete_me = True
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/gone.py")], patch_text)

    assert not target.exists()


def test_apply_unified_diff_rejects_patch_target_outside_changed_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("value = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1 +1 @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(SecurityError):
        apply_unified_diff(tmp_path, [Path("pkg/other.py")], patch_text)


def test_apply_unified_diff_rejects_binary_patch_text(tmp_path: Path) -> None:
    patch_text = dedent(
        """\
        diff --git a/pkg/app.py b/pkg/app.py
        GIT binary patch
        literal 3
        abc
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)


def test_apply_unified_diff_rejects_malformed_hunk_header(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ malformed @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)


def test_apply_unified_diff_rejects_hunk_header_with_attached_garbage(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1 +1 @@garbage
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_unified_diff_rejects_path_header_with_extra_space(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++  b/pkg/app.py
        @@ -1 +1 @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_unified_diff_rejects_invalid_new_side_zero_start(
    tmp_path: Path,
) -> None:
    patch_text = dedent(
        """\
        --- /dev/null
        +++ b/pkg/new.py
        @@ -0,0 +0 @@
        +created = True
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg/new.py")], patch_text)
    assert not (tmp_path / "pkg" / "new.py").exists()


def test_apply_unified_diff_modifies_existing_empty_file_with_zero_old_range(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg" / "empty.py"
    target.parent.mkdir()
    target.write_text("", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/empty.py
        +++ b/pkg/empty.py
        @@ -0,0 +1 @@
        +value = 1
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/empty.py")], patch_text)

    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_unified_diff_inserts_before_first_line_with_zero_old_start(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -0,0 +1 @@
        +value = 2
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)

    assert target.read_text(encoding="utf-8") == "value = 2\nvalue = 1\n"


def test_apply_unified_diff_inserts_before_first_line_with_bsd_zero_count_range(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1,0 +1 @@
        +value = 2
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)

    assert target.read_text(encoding="utf-8") == "value = 2\nvalue = 1\n"


def test_apply_unified_diff_empties_existing_file_with_zero_new_range(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1 +0,0 @@
        -value = 1
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)

    assert target.read_text(encoding="utf-8") == ""


def test_apply_unified_diff_deletes_first_line_with_zero_new_range(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("first\nsecond\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1 +0,0 @@
        -first
        """,
    )

    apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)

    assert target.read_text(encoding="utf-8") == "second\n"


def test_apply_unified_diff_rejects_new_start_mismatch(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1 +999 @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)
    assert target.read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.parametrize("patch_path", ["../x.py", "b/../x.py"])
def test_apply_unified_diff_rejects_traversal_paths(
    tmp_path: Path,
    patch_path: str,
) -> None:
    patch_text = "\n".join(
        [
            "--- /dev/null",
            f"+++ {patch_path}",
            "@@ -0,0 +1 @@",
            "+escape = True",
            "",
        ],
    )

    with pytest.raises(SecurityError):
        apply_unified_diff(tmp_path, [Path("x.py")], patch_text)


@pytest.mark.parametrize("patch_path", ["b/pkg/./app.py", "b/pkg//app.py"])
def test_apply_unified_diff_rejects_dot_and_empty_patch_path_segments(
    tmp_path: Path,
    patch_path: str,
) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    patch_text = "\n".join(
        [
            "--- a/pkg/app.py",
            f"+++ {patch_path}",
            "@@ -1 +1 @@",
            "-value = 1",
            "+value = 2",
            "",
        ],
    )

    with pytest.raises(SecurityError):
        apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_unified_diff_rejects_rename_only_patch(tmp_path: Path) -> None:
    patch_text = dedent(
        """\
        diff --git a/pkg/old.py b/pkg/new.py
        similarity index 100%
        rename from pkg/old.py
        rename to pkg/new.py
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg/old.py"), Path("pkg/new.py")], patch_text)


def test_apply_unified_diff_rejects_file_mode_changes(tmp_path: Path) -> None:
    patch_text = dedent(
        """\
        diff --git a/pkg/app.py b/pkg/app.py
        old mode 100644
        new mode 100755
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1 +1 @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)


def test_apply_unified_diff_rejects_utf8_decode_failures(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_bytes(b"value = \xff\n")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1 +1 @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)


def test_apply_unified_diff_rejects_directory_target_for_modification(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg"
    target.mkdir()
    patch_text = dedent(
        """\
        --- a/pkg
        +++ b/pkg
        @@ -1 +1 @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg")], patch_text)
    assert target.is_dir()


def test_apply_unified_diff_rejects_directory_target_for_deletion(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg"
    target.mkdir()
    patch_text = dedent(
        """\
        --- a/pkg
        +++ /dev/null
        @@ -1 +0,0 @@
        -value = 1
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg")], patch_text)
    assert target.is_dir()


def test_apply_unified_diff_rejects_create_under_file_parent(tmp_path: Path) -> None:
    parent = tmp_path / "pkg"
    parent.write_text("not a directory\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- /dev/null
        +++ b/pkg/new.py
        @@ -0,0 +1 @@
        +created = True
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg/new.py")], patch_text)
    assert parent.read_text(encoding="utf-8") == "not a directory\n"


def test_apply_unified_diff_rejects_targets_that_escape_shadow_root(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside_target.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "link.py").symlink_to(outside)
    patch_text = dedent(
        """\
        --- a/link.py
        +++ b/link.py
        @@ -1 +1 @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(SecurityError):
        apply_unified_diff(tmp_path, [Path("link.py")], patch_text)
    assert outside.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_unified_diff_rejects_hard_link_target_outside_shadow_root(
    tmp_path: Path,
) -> None:
    shadow_root = tmp_path / "shadow"
    target = shadow_root / "pkg" / "app.py"
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    try:
        os.link(outside, target)
    except OSError as exc:
        pytest.skip(f"hard links are not available: {exc}")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1 +1 @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises((PatchApplyError, SecurityError)):
        apply_unified_diff(shadow_root, [Path("pkg/app.py")], patch_text)
    assert outside.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_unified_diff_rejects_changed_files_mismatches(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("value = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1 +1 @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(SecurityError):
        apply_unified_diff(tmp_path, [Path("pkg/app.py"), Path("pkg/extra.py")], patch_text)


def test_apply_unified_diff_rejects_duplicate_changed_files_entries(
    tmp_path: Path,
) -> None:
    (tmp_path / "pkg").mkdir()
    target = tmp_path / "pkg" / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1 +1 @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(PatchApplyError):
        apply_unified_diff(
            tmp_path,
            [Path("pkg/app.py"), Path("pkg/app.py")],
            patch_text,
        )
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_unified_diff_rejects_existing_case_alias_targets(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg" / "Case.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    alias = tmp_path / "pkg" / "case.py"
    if not alias.exists() or not target.samefile(alias):
        pytest.skip("filesystem is case-sensitive")
    patch_text = dedent(
        """\
        --- a/pkg/Case.py
        +++ b/pkg/Case.py
        @@ -1 +1 @@
        -value = 1
        +value = 2
        --- a/pkg/case.py
        +++ b/pkg/case.py
        @@ -1 +1 @@
        -value = 1
        +value = 3
        """,
    )

    with pytest.raises((PatchApplyError, SecurityError)):
        apply_unified_diff(tmp_path, [Path("pkg/Case.py"), Path("pkg/case.py")], patch_text)
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_unified_diff_rejects_casefold_colliding_creates(
    tmp_path: Path,
) -> None:
    patch_text = dedent(
        """\
        --- /dev/null
        +++ b/pkg/Case.py
        @@ -0,0 +1 @@
        +value = 1
        --- /dev/null
        +++ b/pkg/case.py
        @@ -0,0 +1 @@
        +value = 2
        """,
    )

    with pytest.raises((PatchApplyError, SecurityError)):
        apply_unified_diff(tmp_path, [Path("pkg/Case.py"), Path("pkg/case.py")], patch_text)
    assert not (tmp_path / "pkg" / "Case.py").exists()
    assert not (tmp_path / "pkg" / "case.py").exists()


def test_apply_unified_diff_rejects_duplicate_patch_targets(tmp_path: Path) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("one\ntwo\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1,2 +1,2 @@
        -one
        +uno
         two
        --- a/pkg/app.py
        +++ b/pkg/app.py
        @@ -1,2 +1,2 @@
         one
        -two
        +dos
        """,
    )

    with pytest.raises((PatchApplyError, SecurityError)):
        apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)
    assert target.read_text(encoding="utf-8") == "one\ntwo\n"


def test_apply_unified_diff_rejects_in_root_symlink_alias(
    tmp_path: Path,
) -> None:
    real_target = tmp_path / "real.py"
    real_target.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "link.py").symlink_to(real_target)
    patch_text = dedent(
        """\
        --- a/link.py
        +++ b/link.py
        @@ -1 +1 @@
        -value = 1
        +value = 2
        """,
    )

    with pytest.raises(SecurityError):
        apply_unified_diff(tmp_path, [Path("link.py")], patch_text)
    assert real_target.read_text(encoding="utf-8") == "value = 1\n"


def test_apply_unified_diff_rolls_back_committed_writes_on_later_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "pkg" / "one.py"
    second = tmp_path / "pkg" / "two.py"
    first.parent.mkdir()
    first.write_text("one = 1\n", encoding="utf-8")
    second.write_text("two = 1\n", encoding="utf-8")
    patch_text = dedent(
        """\
        --- a/pkg/one.py
        +++ b/pkg/one.py
        @@ -1 +1 @@
        -one = 1
        +one = 2
        --- a/pkg/two.py
        +++ b/pkg/two.py
        @@ -1 +1 @@
        -two = 1
        +two = 2
        """,
    )
    real_replace = os.replace
    failed = False

    def fail_second_commit(source: Path, destination: Path) -> None:
        nonlocal failed
        if not failed and destination == second:
            failed = True
            raise OSError("forced replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second_commit)

    with pytest.raises(PatchApplyError):
        apply_unified_diff(tmp_path, [Path("pkg/one.py"), Path("pkg/two.py")], patch_text)
    assert first.read_text(encoding="utf-8") == "one = 1\n"
    assert second.read_text(encoding="utf-8") == "two = 1\n"


def test_apply_unified_diff_does_not_use_external_patch_commands() -> None:
    source = inspect.getsource(patching)

    assert "subprocess" not in source
    assert "os.system" not in source
    assert "shell=True" not in source
    assert "git apply" not in source


def test_apply_unified_diff_preserves_deterministic_newline_behavior(
    tmp_path: Path,
) -> None:
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("one\nold", encoding="utf-8")
    patch_text = "\n".join(
        [
            "--- a/pkg/app.py",
            "+++ b/pkg/app.py",
            "@@ -1,2 +1,2 @@",
            " one",
            "-old",
            "\\ No newline at end of file",
            "+new",
            "\\ No newline at end of file",
            "",
        ],
    )

    apply_unified_diff(tmp_path, [Path("pkg/app.py")], patch_text)

    assert target.read_text(encoding="utf-8") == "one\nnew"
