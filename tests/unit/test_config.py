from pathlib import Path

from agent_quality_mcp.config import load_config
from agent_quality_mcp.exceptions import ConfigurationError
from agent_quality_mcp.models import SafetyMode, ValidationMode


def test_load_config_uses_defaults_without_pyproject(tmp_path: Path) -> None:
    config = load_config(tmp_path)

    assert config.default_mode == ValidationMode.STANDARD
    assert config.default_safety_mode == SafetyMode.READ_ONLY
    assert config.uv_offline is True


def test_load_config_accepts_repo_sample_config() -> None:
    config = load_config(Path.cwd())

    assert config.default_mode == ValidationMode.STANDARD
    assert config.default_safety_mode == SafetyMode.READ_ONLY


def test_load_config_merges_safe_pyproject_and_overrides(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.agent_quality_mcp]
default_mode = "quick"
secret_redaction_patterns = ["internal-secret-marker"]
""".strip(),
        encoding="utf-8",
    )

    config = load_config(tmp_path, {"default_safety_mode": "read_only"})

    assert config.default_mode == ValidationMode.QUICK
    assert config.default_safety_mode == SafetyMode.READ_ONLY
    assert config.secret_redaction_patterns == ["internal-secret-marker"]


def test_load_config_rejects_invalid_overrides(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"max_changed_files": 0})
    except ConfigurationError:
        pass
    else:
        raise AssertionError("invalid max_changed_files should fail validation")


def test_load_config_rejects_untrusted_command_paths_override(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"command_paths": {"ruff": "/tmp/ruff"}})  # noqa: S108
    except ConfigurationError as exc:
        assert "command_paths" in str(exc)
    else:
        raise AssertionError("untrusted command_paths should fail validation")


def test_load_config_rejects_untrusted_resource_limit_override(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"max_patch_bytes": 999999999})
    except ConfigurationError as exc:
        assert "max_patch_bytes" in str(exc)
    else:
        raise AssertionError("untrusted max_patch_bytes should fail validation")


def test_load_config_rejects_untrusted_pyproject_command_paths(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.agent_quality_mcp]
command_paths = { ruff = "/tmp/ruff" }
""".strip(),
        encoding="utf-8",
    )

    try:
        load_config(tmp_path)
    except ConfigurationError as exc:
        assert "command_paths" in str(exc)
    else:
        raise AssertionError("untrusted pyproject command_paths should fail validation")


def test_load_config_rejects_untrusted_uv_offline_false(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.agent_quality_mcp]
uv_offline = false
""".strip(),
        encoding="utf-8",
    )

    try:
        load_config(tmp_path)
    except ConfigurationError as exc:
        assert "uv_offline" in str(exc)
    else:
        raise AssertionError("untrusted uv_offline=false should fail validation")


def test_load_config_rejects_untrusted_apply_safe_fixes_override(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"default_safety_mode": "apply_safe_fixes"})
    except ConfigurationError as exc:
        assert "default_safety_mode" in str(exc)
    else:
        raise AssertionError("untrusted apply_safe_fixes should fail validation")


def test_load_config_rejects_untrusted_apply_safe_fixes_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.agent_quality_mcp]
default_safety_mode = "apply_safe_fixes"
""".strip(),
        encoding="utf-8",
    )

    try:
        load_config(tmp_path)
    except ConfigurationError as exc:
        assert "default_safety_mode" in str(exc)
    else:
        raise AssertionError("untrusted pyproject apply_safe_fixes should fail validation")


def test_load_config_accepts_untrusted_preview_safe_fixes(tmp_path: Path) -> None:
    config = load_config(tmp_path, {"default_safety_mode": "preview_safe_fixes"})

    assert config.default_safety_mode == SafetyMode.PREVIEW_SAFE_FIXES


def test_load_config_rejects_invalid_safety_mode_string(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"default_safety_mode": "invalid"})
    except ConfigurationError as exc:
        assert "default_safety_mode" in str(exc)
    else:
        raise AssertionError("invalid default_safety_mode should fail validation")


def test_load_config_rejects_invalid_secret_redaction_pattern(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"secret_redaction_patterns": ["["]})
    except ConfigurationError:
        pass
    else:
        raise AssertionError("invalid secret_redaction_patterns should fail validation")


def test_load_config_rejects_regex_like_secret_redaction_pattern(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"secret_redaction_patterns": ["sk-[A-Za-z0-9_-]+"]})
    except ConfigurationError:
        pass
    else:
        raise AssertionError("regex-like secret_redaction_patterns should fail validation")


def test_load_config_rejects_empty_secret_redaction_pattern(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"secret_redaction_patterns": [""]})
    except ConfigurationError:
        pass
    else:
        raise AssertionError("empty secret_redaction_patterns should fail validation")


def test_load_config_rejects_nested_quantified_secret_pattern(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"secret_redaction_patterns": ["(a+)+$"]})
    except ConfigurationError:
        pass
    else:
        raise AssertionError("risky secret_redaction_patterns should fail validation")


def test_load_config_rejects_alternating_quantified_secret_pattern(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"secret_redaction_patterns": ["(a|aa)+$"]})
    except ConfigurationError:
        pass
    else:
        raise AssertionError("alternating secret_redaction_patterns should fail validation")


def test_load_config_rejects_optional_alternating_secret_pattern(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"secret_redaction_patterns": ["^(a|a?)+$"]})
    except ConfigurationError:
        pass
    else:
        raise AssertionError("optional alternating secret_redaction_patterns should fail")


def test_load_config_rejects_quantified_wildcard_secret_pattern(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"secret_redaction_patterns": [r"prefix.*secret"]})
    except ConfigurationError:
        pass
    else:
        raise AssertionError("quantified wildcard secret_redaction_patterns should fail")


def test_load_config_rejects_optional_dot_wildcard_secret_pattern(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"secret_redaction_patterns": [r"prefix.?secret"]})
    except ConfigurationError:
        pass
    else:
        raise AssertionError("optional dot wildcard secret_redaction_patterns should fail")


def test_load_config_accepts_literal_secret_patterns(tmp_path: Path) -> None:
    config = load_config(
        tmp_path,
        {"secret_redaction_patterns": ["internal-secret-marker"]},
    )

    assert config.secret_redaction_patterns == ["internal-secret-marker"]


def test_load_config_accepts_safe_pyproject_fields(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.agent_quality_mcp]
default_mode = "quick"
secret_redaction_patterns = ["internal-secret-marker"]
workspace_exclusions = [".git", ".venv"]
secret_file_patterns = [".env"]
uv_offline = true
""".strip(),
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.default_mode == ValidationMode.QUICK
    assert config.secret_redaction_patterns == ["internal-secret-marker"]
    assert config.workspace_exclusions == [".git", ".venv"]
    assert config.secret_file_patterns == [".env"]


def test_load_config_rejects_excessive_secret_pattern_length(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"secret_redaction_patterns": ["a" * 501]})
    except ConfigurationError:
        pass
    else:
        raise AssertionError("long secret_redaction_patterns should fail validation")


def test_load_config_rejects_excessive_secret_pattern_count(tmp_path: Path) -> None:
    try:
        load_config(tmp_path, {"secret_redaction_patterns": [r"token=\S+"] * 33})
    except ConfigurationError:
        pass
    else:
        raise AssertionError("too many secret_redaction_patterns should fail validation")
