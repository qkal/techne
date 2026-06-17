from agent_quality_mcp import __version__
from agent_quality_mcp.server import main


def test_package_exports_version() -> None:
    assert __version__ == "0.1.0"


def test_console_entrypoint_is_importable() -> None:
    assert callable(main)
