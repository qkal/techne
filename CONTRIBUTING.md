# Contributing

Thanks for considering a contribution to Agent Quality MCP. This project
validates proposed code changes for coding agents inside isolated shadow
workspaces, so changes here should be held to the same bar the tool itself
enforces: tested, type-checked, and reviewed for security implications.

## Development Setup

```bash
uv sync --extra dev
```

This creates the local virtual environment with the test/lint/type-check
toolchain. The package requires Python 3.12 or newer.

## Running Checks Locally

Run the full verification suite before opening a PR:

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m pytest tests/integration/test_validate_patch_demo.py -v
.venv/bin/ruff check .
.venv/bin/pyright --pythonpath .venv/bin/python
git diff --check
```

For a narrower unit-only pass during iteration:

```bash
.venv/bin/python -m pytest tests/unit -v
```

CI runs the same checks (lint, type check, test with coverage, whitespace)
on every push and pull request; all of them must pass before merge.

## Pull Request Conventions

- Keep changes small and focused on one concern, matching this repository's
  existing history (each PR generally addresses one theme: a bugfix, one
  refactor, one feature).
- Write or update tests for any behavior change. New validators, decision
  rules, or response fields need unit test coverage; user-facing flows
  should have integration coverage.
- Do not weaken the security model (shadow-workspace-only execution, no
  real-workspace mutation, command allowlisting, redaction) without an
  explicit design discussion first — open an issue describing the proposed
  change before sending a PR that touches `paths.py`, `shadow.py`,
  `exclusions.py`, or `cli/runner.py`'s command resolution.
- Update `README.md` and `CHANGELOG.md` for any user-visible change.

## Versioning Policy

This project follows [Semantic Versioning](https://semver.org/). Until
`1.0.0`:

- The project is `0.y.z`. Per SemVer, anything may change at any time,
  including the `validate_patch`/`inspect_workspace` response contract —
  this has already happened once (Phase 2's decision-contract redesign).
  Breaking changes are recorded in `CHANGELOG.md` but do not require a
  major-version bump while still below `1.0.0`.
- `1.0.0` is the point at which the response contract is declared stable;
  after that, MAJOR bumps are required for breaking response-contract
  changes, MINOR for new optional fields/tools/capabilities, and PATCH for
  bugfixes and docs-only changes.

## Releasing (Maintainers Only)

Releases are tag-driven via `.github/workflows/release.yml`. Before the
**first** tag push, a maintainer must:

1. Publish an initial version to PyPI manually once (trusted publishing
   requires the project to already exist, or a "pending" trusted publisher
   can be registered before the first publish — see PyPI's trusted
   publishing documentation for the current option), and register a PyPI
   Trusted Publisher binding for project `agent-quality-mcp`, repository
   `qkal/techne`, workflow `release.yml`, environment `pypi`.
2. Enable "Private vulnerability reporting" in the repository's Settings →
   Security page, so `SECURITY.md`'s reporting flow works end to end.

After that one-time setup, cutting a release is:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

This triggers CI verification, then builds and publishes the package to
PyPI, then publishes/updates the listing in the official MCP Registry.
