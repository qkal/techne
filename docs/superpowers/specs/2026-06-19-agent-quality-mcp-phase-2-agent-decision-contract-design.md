# Agent Quality MCP Phase 2 Agent Decision Contract Design

## Purpose

Phase 2 makes `validate_patch` more useful to autonomous coding agents by turning
raw validation results into a direct, machine-actionable decision contract.

Phase 1 proved safe shadow-workspace validation, patch application, minimal uv,
Ruff, and Pyright adapters, structured diagnostics, risk scoring, suggestions,
and audit metadata. Phase 2 deliberately performs a breaking response cleanup:
the top-level response should first answer what the agent should do next, then
provide compact evidence for that decision.

This phase prioritizes autonomous coding agents over human readability. Human
developers should still be able to inspect the response, but the shape is
optimized for branching, retrying, editing, and escalating without interpreting
long prose.

## Scope

Phase 2 includes:

- A breaking `validate_patch` response contract centered on agent decisions.
- A new decision layer that converts diagnostics, tool outcomes, risk, and
  validation completeness into a top-level decision.
- Compact diagnostic grouping so repeated or related findings become issue
  clusters.
- Ranked next actions with explicit safety and human-review metadata.
- Optional fix-plan guidance for patch revision scenarios.
- Evidence fields that summarize diagnostics, command outcomes, tool
  availability, skipped checks, risk factors, and confidence inputs.
- Service-level and unit tests that assert exact decisions for common scenarios.
- README and example updates for the new response shape.

Phase 2 may improve validation-tool behavior only where it supports the agent
decision contract, such as clearer skipped-check metadata or better tool outcome
classification.

The `inspect_workspace` tool remains source-compatible in Phase 2. It may add
metadata that helps explain `validate_patch` decisions, but it should not receive
a breaking redesign in this phase.

Phase 2 defers:

- Broad Python project-shape detection beyond what is needed for clearer
  evidence.
- LSP orchestration.
- Real-repository mutation.
- Production-grade auto-fix application.
- Maintaining strict backward compatibility with the Phase 1 response shape.
- A human-first report format.

## Response Contract

`validate_patch` returns a response shaped around these top-level concepts:

- `request_id`: caller-provided or generated request identifier.
- `workspace_root`: normalized workspace root when resolution succeeds, otherwise
  the redacted caller-provided root.
- `mode`: effective validation mode after config defaults are applied.
- `safety_mode`: effective safety mode after config defaults are applied.
- `decision`: machine-actionable outcome.
- `confidence`: score and rationale for the decision reliability.
- `summary`: compact status summary for agent logs.
- `blockers`: ranked issues that prevent applying the patch.
- `next_actions`: ordered actions the agent can take.
- `fix_plan`: optional grouped edit guidance when the patch should be revised.
- `evidence`: compact supporting facts from tools, diagnostics, and risk.
- `execution`: simplified metadata about checks that ran, skipped, failed, or
  timed out.
- `audit`: redacted security and policy metadata retained from Phase 1 where
  still relevant.

The response should avoid duplicating the same information in multiple places.
The top level tells the agent what to do. `blockers`, `next_actions`, `fix_plan`,
and `evidence` explain why.

Phase 2 removes the Phase 1 top-level `status`, `blocking_errors`, `warnings`,
`info`, `suggested_actions`, `safe_fixes`, and `context_summary` fields from
`validate_patch`. Their information moves into `decision`, `blockers`,
`next_actions`, `fix_plan`, and `evidence`. Request models remain compatible
unless an input was already rejected by Phase 1 validation.

### Decision Values

`decision` is one of:

- `apply_patch`: validation completed sufficiently and no blocking issue remains.
- `revise_patch`: the patch should be edited and validated again.
- `fix_tooling`: the validator could not provide reliable quality feedback
  because required tooling or project metadata is unavailable or broken.
- `request_human_review`: validation is incomplete, ambiguous, risky, or timed
  out in a way an autonomous agent should not resolve alone.
- `reject_request`: the request is invalid, unsafe, or outside supported policy.

The decision layer must distinguish "the patch is bad" from "the validator
could not complete." Agents need that distinction to choose whether to edit code,
fix tools, retry, or escalate.

Decision precedence is deterministic:

1. Unsupported or unsafe requests return `reject_request`.
2. Security and path-validation failures return `reject_request`.
3. Patch parsing or patch application failures return `revise_patch`.
4. Validation timeouts or unexpected internal failures return
   `request_human_review`.
5. Required-tool or project-metadata failures return `fix_tooling` when the patch
   cannot be judged reliably.
6. Ruff, Pyright, uv, or dependency findings that are attributable to the patch
   return `revise_patch`.
7. Clean validation returns `apply_patch`.

When more than one condition is present, the highest-precedence condition wins.
Lower-precedence conditions still appear in blockers or evidence when they were
observed before validation stopped.

### Confidence

`confidence` contains:

- `score`: integer from 0 to 100.
- `level`: `low`, `medium`, or `high`.
- `rationale`: concise machine-readable sentence or short list.
- `factors`: structured inputs that affected confidence.

Confidence increases when required checks ran and produced consistent results.
Confidence decreases when tools are missing, checks are skipped, diagnostics are
truncated, commands time out, or risk factors require human interpretation.

Confidence level thresholds are:

- `low`: 0 through 39
- `medium`: 40 through 74
- `high`: 75 through 100

`apply_patch` should normally require high confidence in `standard` and `strict`
mode. In `quick` mode, `apply_patch` may be medium confidence when skipped checks
are expected for that mode and all completed checks are clean.

### Blockers

`blockers` are ranked issue clusters, not a flat dump of every diagnostic.

Each blocker contains:

- `id`
- `kind`: `request`, `security`, `patch`, `quality`, `type`, `tooling`,
  `timeout`, `dependency`, or `human_review`
- `severity`: `error`, `warning`, or `info`
- `title`
- `details`
- `files`
- `related_diagnostic_ids`
- `first_evidence`
- `count`
- `fixability`: `agent_fixable`, `tooling_fixable`, `human_review`, or
  `not_fixable`

Security and path-validation blockers always rank before quality issues.

### Next Actions

`next_actions` are ordered and explicit.

Each action contains:

- `id`
- `kind`: `edit`, `rerun`, `inspect`, `fix_tooling`, `ask_human`, or `stop`
- `priority`
- `title`
- `details`
- `safe_to_run`
- `requires_human`
- `command`: optional argument list, never a shell string
- `related_blocker_ids`
- `expected_result`

Suggested commands must not include shell operators, pipes, redirects, command
chaining, or untrusted executable names.

### Fix Plan

`fix_plan` is present when `decision` is `revise_patch` and the issue is
localizable enough for an agent to edit.

It contains:

- `strategy`
- `steps`
- `target_files`
- `safe_fix_previews`: optional previews produced by tools such as Ruff when
  `preview_safe_fixes` is requested
- `related_blocker_ids`
- `rerun_hint`

The fix plan is guidance, not an automatic patch. It must not fabricate source
contents that were not observed through diagnostics or request metadata.
Safe-fix previews must remain redacted and truncated, and they never imply that
the real workspace may be modified automatically.

### Evidence

`evidence` contains compact facts:

- grouped diagnostic summaries
- command outcomes
- tool availability
- skipped checks and reasons
- required checks and whether each required check completed
- risk score and factors
- truncation flags
- shadow-workspace status
- real-workspace mutation status
- diagnostic truncation and grouping metadata

Detailed raw stdout, stderr, patch content, and file contents remain redacted or
truncated according to Phase 1 security rules.

## Architecture

The validation pipeline remains service-led and shadow-workspace based. Tool
adapters continue to report facts. A new response assembly layer interprets
those facts into decisions and actions.

Primary additions:

- `decision.py`: computes the final decision, confidence, and rationale.
- `grouping.py`: groups diagnostics by file, cause, tool, and likely fix path.
- `actions.py`: creates ranked next actions and fix-plan guidance.
- `response.py`: defines the Phase 2 response models while reusing shared
  request, diagnostic, command, audit, and risk models from `models.py`.
- Targeted updates to `service.py` so orchestration delegates decision assembly
  instead of constructing the full response inline.
- A replacement for the current `build_error_response` helper that emits the
  Phase 2 response contract for MCP-tool-layer validation errors.

The key boundary is that uv, Ruff, Pyright, and patch adapters do not decide
what the agent should do. They report normalized facts. The decision layer owns
interpretation.

## Data Flow

The Phase 2 `validate_patch` flow is:

1. Parse and validate the request.
2. Resolve config and security policy.
3. Build the shadow workspace.
4. Apply the patch in the shadow workspace when provided.
5. Run checks selected by mode.
6. Normalize tool output into diagnostics and command outcomes.
7. Group diagnostics into issue clusters.
8. Classify blockers by kind and fixability.
9. Compute risk and confidence from blockers, tool availability, skipped checks,
   timeouts, truncation, and validation completeness.
10. Produce the top-level decision.
11. Produce ordered next actions and an optional fix plan.
12. Return compact evidence and execution metadata.

## Error Handling

Phase 2 routes failures through the decision contract:

- Invalid request or unsafe input returns `decision: reject_request`.
- `apply_safe_fixes` returns `decision: reject_request` because real-workspace
  mutation remains unsupported.
- Patch application failure returns `decision: revise_patch`, with patch-specific
  blockers for malformed hunks, path mismatches, or unsupported diff features.
- Ruff or Pyright failures return `decision: revise_patch` unless every finding
  is explicitly non-blocking.
- Missing required tools, command resolution failures, stale lockfiles, or broken
  project metadata return `decision: fix_tooling` when the patch itself cannot
  be judged reliably.
- Timeout or incomplete validation returns `decision: request_human_review`
  unless the incomplete result is clearly safe and localizable.
- Pydantic request-validation failures in the MCP tool wrapper return
  `decision: reject_request` without calling the service layer.
- Unexpected internal exceptions return `decision: request_human_review` with
  redacted evidence and no permission to apply the patch.
- Security-sensitive failures always block application and expose only redacted
  evidence.

Expected validation failures should become structured blockers. Unexpected
internal failures should become fail-closed responses with a clear decision and
redacted evidence.

## Mode Behavior

Mode names remain `quick`, `standard`, and `strict`, but the response explains
what each mode actually proved.

- `quick`: optimized for fast local feedback. Missing heavyweight checks reduce
  confidence rather than being hidden.
- `standard`: default balanced validation. Decisions should usually be high
  confidence when required tools are available.
- `strict`: broader validation and stricter routing to `request_human_review`
  when checks are incomplete or risk is elevated.

Mode behavior must be represented in `evidence` and `confidence.factors` so
agents can decide whether a stricter rerun is worthwhile.

The implementation plan must define the required checks for each mode before
coding the decision layer. Missing optional checks reduce confidence. Missing
required checks produce `fix_tooling` or `request_human_review`, depending on
whether the cause is actionable tooling setup or incomplete validation.

## Testing

Unit tests should cover:

- clean validation returns `decision: apply_patch`
- Ruff failures return `decision: revise_patch`
- Pyright failures return `decision: revise_patch`
- malformed diffs return `decision: revise_patch`
- unsafe paths return `decision: reject_request`
- missing required tooling returns `decision: fix_tooling`
- timeout or incomplete validation returns `decision: request_human_review`
- repeated diagnostics are grouped into compact issue clusters
- security blockers rank above quality blockers
- confidence drops when checks are skipped, truncated, unavailable, or timed out
- next actions are ordered and use command argument lists
- unsafe or human-required actions are explicitly marked
- fix plans are present only when the patch is agent-fixable
- `preview_safe_fixes` places redacted tool fix previews under `fix_plan`
- MCP tool-wrapper validation errors return the new response contract
- `inspect_workspace` remains source-compatible

Service and integration tests should update the demo fixture expectations to
assert the new top-level response contract, including decision, blockers, next
actions, evidence, safe-fix previews when requested, and real-workspace mutation
status.

## Documentation

Update the README with:

- the new `validate_patch` response contract
- decision value meanings
- example successful response
- example revise-patch response
- example fix-tooling response
- guidance that Phase 2 is a breaking response cleanup
- mapping from removed Phase 1 fields to Phase 2 fields
- the continued guarantee that real repository files are not modified by default

## Acceptance Criteria

- `validate_patch` returns the new agent decision contract.
- Common validation outcomes map to deterministic decisions.
- Diagnostic grouping reduces repeated findings while preserving underlying
  evidence references.
- `next_actions` are ranked, typed, and safe for autonomous agents to inspect.
- `fix_plan` appears for localizable patch revision cases.
- Missing tools and incomplete validation are distinguishable from bad patches.
- `preview_safe_fixes` remains available through the new fix-plan structure.
- Tool-layer invalid requests return the same response contract as service-layer
  failures.
- `inspect_workspace` remains source-compatible with Phase 1.
- Security-sensitive failures remain fail-closed and redacted.
- Existing shadow-workspace safety guarantees remain intact.
- Unit tests cover decision, grouping, action, confidence, and service contract
  behavior.
- Integration tests pass or skip deterministically when external CLIs are
  unavailable.
- README examples match the new response shape.
