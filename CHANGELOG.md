# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

> **Versioning note:** versions below `1.0.0` may contain breaking changes
> to the `validate_patch`/`inspect_workspace` response contract without a
> major-version bump (see `CONTRIBUTING.md`). `1.0.0` is the point at which
> that contract is declared stable.

## [Unreleased]

### Added

- `LICENSE` (MIT), `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  and GitHub issue/PR templates.
- `ruff` and `pyright` are now bundled as core runtime dependencies, so a
  default `pip install agent-quality-mcp` produces a fully working server
  instead of one missing two of its three quality tools.
- `validate_patch` and `inspect_workspace` MCP tool schemas now include
  per-field descriptions so an agent can use them correctly without first
  reading the README.
- `agent-quality-mcp --version` and `--help` now return immediately instead
  of hanging; unrecognized arguments fail fast with a usage message.
- A tag-driven release pipeline (`.github/workflows/release.yml`) that
  publishes to PyPI via Trusted Publishing and to the official MCP
  Registry.
- `server.json` for MCP Registry discovery, with a matching `mcp-name`
  ownership marker in `README.md`.
- README badges and an MCP-client quickstart section (Claude Desktop,
  Cursor).

### Fixed

- `.gitignore` no longer has a blanket `docs/` rule that could silently
  drop newly added design docs from `git add .`.

## [0.1.0] - 2026-06-18

Initial development version. Shadow-workspace `validate_patch` and
`inspect_workspace` MCP tools; `uv`, Ruff, and Pyright (CLI and LSP)
validation adapters; the Phase 2 agent decision contract
(`decision`/`confidence`/`blockers`/`next_actions`/`fix_plan`/`evidence`).
Not yet published to PyPI or the MCP Registry.
