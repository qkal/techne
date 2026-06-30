import tomllib
from pathlib import Path

from agent_quality_mcp import __version__
from agent_quality_mcp.server import main


def test_package_exports_version() -> None:
    assert __version__ == "0.1.0"


def test_console_entrypoint_is_importable() -> None:
    assert callable(main)


def _pyproject_data() -> dict:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject_path.read_text(encoding="utf-8"))


def test_runtime_dependencies_include_ruff_and_pyright() -> None:
    dependencies = _pyproject_data()["project"]["dependencies"]
    names = {dependency.split(">=")[0].split("==")[0].strip() for dependency in dependencies}
    assert {"ruff", "pyright"} <= names


def test_project_metadata_has_urls_and_classifiers() -> None:
    project = _pyproject_data()["project"]
    assert project.get("urls", {}).get("Repository")
    assert any(
        classifier.startswith("License ::") for classifier in project.get("classifiers", [])
    )
