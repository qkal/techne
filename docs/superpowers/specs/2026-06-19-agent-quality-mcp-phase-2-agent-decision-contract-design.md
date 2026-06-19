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

### Confidence

`confidence` contains:

- `score`: integer from 0 to 100.
- `level`: `low`, `medium`, or `high`.
- `rationale`: concise machine-readable sentence or short list.
- `factors`: structured inputs that affected confidence.

Confidence increases when required checks ran and produced consistent results.
Confidence decreases when tools are missing, checks are skipped, diagnostics are
truncated, commands time out, or risk factors require human interpretation.

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
- `related_blocker_ids`
- `rerun_hint`

The fix plan is guidance, not an automatic patch. It must not fabricate source
contents that were not observed through diagnostics or request metadata.

### Evidence

`evidence` contains compact facts:

- grouped diagnostic summaries
- command outcomes
- tool availability
- skipped checks and reasons
- risk score and factors
- truncation flags
- shadow-workspace status
- real-workspace mutation status

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
- Patch application failure returns `decision: revise_patch`, with patch-specific
  blockers for malformed hunks, path mismatches, or unsupported diff features.
- Ruff or Pyright failures return `decision: revise_patch` unless every finding
  is explicitly non-blocking.
- Missing required tools, command resolution failures, stale lockfiles, or broken
  project metadata return `decision: fix_tooling` when the patch itself cannot
  be judged reliably.
- Timeout or incomplete validation returns `decision: request_human_review`
  unless the incomplete result is clearly safe and localizable.
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

Service and integration tests should update the demo fixture expectations to
assert the new top-level response contract, including decision, blockers, next
actions, evidence, and real-workspace mutation status.

## Documentation

Update the README with:

- the new `validate_patch` response contract
- decision value meanings
- example successful response
- example revise-patch response
- example fix-tooling response
- guidance that Phase 2 is a breaking response cleanup
- the continued guarantee that real repository files are not modified by default

## Acceptance Criteria

- `validate_patch` returns the new agent decision contract.
- Common validation outcomes map to deterministic decisions.
- Diagnostic grouping reduces repeated findings while preserving underlying
  evidence references.
- `next_actions` are ranked, typed, and safe for autonomous agents to inspect.
- `fix_plan` appears for localizable patch revision cases.
- Missing tools and incomplete validation are distinguishable from bad patches.
- Security-sensitive failures remain fail-closed and redacted.
- Existing shadow-workspace safety guarantees remain intact.
- Unit tests cover decision, grouping, action, confidence, and service contract
  behavior.
- Integration tests pass or skip deterministically when external CLIs are
  unavailable.
- README examples match the new response shape.
