# Security Policy

## Supported Versions

Agent Quality MCP is currently pre-1.0 (`0.x`). Only the latest published
release receives security fixes. There is no long-term support branch yet;
this will be revisited once the project reaches `1.0.0`.

| Version | Supported |
| ------- | --------- |
| Latest `0.x` release | Yes |
| Anything older       | No |

## Reporting A Vulnerability

Please report suspected vulnerabilities privately, not through a public
GitHub issue:

- Preferred: open a [GitHub private security advisory](https://github.com/qkal/techne/security/advisories/new)
  for this repository. This notifies maintainers without disclosing details
  publicly.
- If private advisories are unavailable to you, open a regular issue asking
  a maintainer to contact you privately, without including exploit details
  in the public issue body.

Please include: the affected version, a minimal reproduction (a
`validate_patch`/`inspect_workspace` request is ideal), and the expected
vs. actual behavior. Do not include real secrets, credentials, or
proprietary source code in your report — redact anything sensitive the
same way this project redacts it internally.

We aim to acknowledge new reports within a few business days and to agree
on a disclosure timeline with the reporter before any public write-up.

## What's Already Mitigated

Before reporting, please check whether the behavior you found is already an
intentional part of the existing security model (see `README.md`'s
"Security Model" section for the full list), including:

- The real workspace is read-only for validation; proposed patches are only
  ever applied inside a temporary shadow workspace, and `apply_safe_fixes`
  (real-workspace mutation) is always rejected.
- Workspace paths and patch targets must be relative, normalized, and stay
  inside the workspace; path traversal, absolute paths, drive prefixes,
  null bytes, symlinks, hard links, and target collisions are rejected.
- The patch parser only accepts a conservative text unified-diff subset;
  binary patches, malformed hunks, renames, copies, and Git mode changes
  are rejected.
- Subprocesses are restricted to an allowlist (`uv`, `ruff`, `pyright`,
  `pyright-langserver`), invoked with argument lists and `shell=False`, with
  workspace-owned executables excluded from command resolution.
- Subprocess output is redacted for common secret patterns and configured
  literal redaction tokens, then truncated to configured byte limits.

If your finding shows one of these guarantees can actually be bypassed,
that is exactly the kind of report this policy wants — please report it
privately using the process above rather than opening a public issue or PR.
