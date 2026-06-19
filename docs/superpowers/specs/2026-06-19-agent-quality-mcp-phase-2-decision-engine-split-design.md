# Agent Quality MCP Phase 2 Decision Engine Split Design

## Purpose

This design revises the Phase 2 implementation scope for the existing agent
decision contract.

The approved Phase 2 contract still stands: `validate_patch` will move from the
Phase 1 validation-result shape to a breaking response centered on `decision`,
`confidence`, `blockers`, `next_actions`, `fix_plan`, `evidence`, `execution`,
and `audit`.

The change is sequencing. Phase 2 should be implemented decision-engine-first so
the hardest behavior is tested before the public MCP response changes. The goal
is to de-risk the breaking API change without adding a temporary Phase 1
compatibility shim.

## Scope

This split includes:

- A first implementation milestone that adds internal decision, grouping, and
  action-generation logic while keeping the public `validate_patch` response
  shape unchanged.
- A second milestone that adds the Phase 2 response models and response assembly
  layer using the tested internal decision result.
- A final milestone that switches `validate_patch` to the breaking Phase 2
  response contract and updates README, service tests, tool-wrapper tests, and
  integration tests.
- Focused unit tests for precedence, confidence, blockers, next actions, and
  fix-plan behavior before the service response changes.

This split does not include:

- Preserving the full Phase 1 `validate_patch` response shape behind a request
  option.
- A dual-response mode.
- Broad new validation features outside the Phase 2 decision contract.
- Breaking changes to `inspect_workspace`.

## Recommended Approach

The implementation should use a decision-engine-first split.

Schema-first would make the public contract concrete early, but it would not
prove the behavior that actually drives agent decisions. Vertical slices would
reduce PR size, but they risk scattering shared precedence and confidence logic
across several partial paths. Decision-engine-first keeps the central behavior in
one place and lets the project prove it independently before changing the public
API.

## Architecture

The validation pipeline remains service-led and shadow-workspace based. CLI
adapters continue to produce facts. The new internal decision engine interprets
those facts before the public response assembly layer is introduced.

The staged architecture is:

1. Build internal decision modules behind the existing response contract.
2. Build Phase 2 response models and mappers on top of those internal modules.
3. Switch the MCP tool and service to return the new response shape.

The service should orchestrate these layers but should not accumulate new
decision branches itself.

## Components

`grouping.py` groups normalized diagnostics and command outcomes into
deterministic blocker clusters. It owns grouping and ranking details, but it
does not decide the final response.

`decision.py` owns decision precedence, required-check evaluation by mode,
confidence scoring, and the final internal decision result. It consumes
blockers, execution completeness, risk, truncation, timeouts, skipped checks, and
tool availability.

`actions.py` converts the internal decision result plus blockers into ordered
next actions and optional fix-plan guidance. Suggested commands must remain
argument lists, and each action must carry safety and human-review metadata.

`response.py` comes after the decision engine is proven. It defines the public
Phase 2 response models and maps the internal decision result into the breaking
response contract.

`service.py` should call the decision and response layers. It should preserve
the existing shadow workspace, patching, command execution, redaction, and audit
responsibilities.

## Milestone 1: Internal Decision Engine

Milestone 1 adds the internal behavior without changing the public
`validate_patch` response.

It should create the grouping, decision, and action modules with focused unit
tests. Tests should cover:

- unsafe requests and security diagnostics returning an internal
  `reject_request` decision
- patch parse or patch application failures returning `revise_patch`
- missing required tools or broken project metadata returning `fix_tooling`
- timeouts, truncation, or incomplete validation returning
  `request_human_review` where appropriate
- Ruff and Pyright findings returning `revise_patch`
- clean required checks returning `apply_patch`
- security blockers ranking above patch, tooling, and quality blockers
- confidence dropping when checks are skipped, unavailable, truncated, or timed
  out
- next actions being ordered, typed, and explicit about safety
- fix plans appearing only for localizable agent-fixable revision cases

Existing service and integration tests remain Phase 1-compatible in this
milestone. The new modules may be exercised through pure unit tests or narrow
service-adjacent tests that do not switch the returned MCP payload.

## Milestone 2: Response Assembly

Milestone 2 adds the Phase 2 public response models and builders while still
keeping the service switch contained.

It should create `response.py` with the public enums and Pydantic models for:

- `decision`
- `confidence`
- `summary`
- `blockers`
- `next_actions`
- `fix_plan`
- `evidence`
- simplified `execution`
- redacted `audit`

The response builder should consume the internal decision result from Milestone
1. It should not recompute precedence, confidence, or blocker ranking.

Tests in this milestone should assert exact response shapes for representative
cases, including invalid requests, security failures, patch failures, missing
tooling, clean validation, and localizable revise-patch outcomes. These tests
should also assert that stale Phase 1 fields are absent from the new builder
payload.

## Milestone 3: Public Service Switch

Milestone 3 switches `validate_patch` to return only the Phase 2 contract.

It should update:

- `service.py` to assemble the Phase 2 payload through `response.py`
- `tools.py` so MCP tool-wrapper request validation failures return the same
  Phase 2 contract
- service tests and integration tests to assert the breaking response shape
- README examples and field mapping notes

The switch should happen in one contained slice. After this milestone,
`validate_patch` no longer returns the Phase 1 top-level `status`,
`blocking_errors`, `warnings`, `info`, `suggested_actions`, `safe_fixes`, or
`context_summary` fields.

## Data Flow

Milestone 1 data flow:

1. Existing validation code produces diagnostics, command records, execution
   metadata, risk, audit, and safe-fix previews.
2. New internal modules group diagnostics, classify blockers, compute the
   internal decision, compute confidence, and generate internal next-action and
   fix-plan data.
3. Tests assert the internal result directly.
4. The public Phase 1 response remains unchanged.

Milestone 2 data flow:

1. The internal decision result from Milestone 1 becomes the response builder
   input.
2. The builder maps it to the Phase 2 public response fields.
3. Tests assert the builder output without requiring the service to switch yet.

Milestone 3 data flow:

1. `validate_patch` follows the normal shadow-workspace validation path.
2. The service calls the decision engine and response builder.
3. The MCP tool returns the Phase 2 public response.
4. README and integration examples match the new payload.

## Error Handling

The split must preserve the approved Phase 2 precedence:

1. Unsupported or unsafe requests return `reject_request`.
2. Security and path-validation failures return `reject_request`.
3. Patch parsing or patch application failures return `revise_patch`.
4. Validation timeouts or unexpected internal failures return
   `request_human_review`.
5. Required-tool or project-metadata failures return `fix_tooling` when the patch
   cannot be judged reliably.
6. Ruff, Pyright, uv, or dependency findings attributable to the patch return
   `revise_patch`.
7. Clean validation returns `apply_patch`.

The implementation order changes, but the precedence does not. Milestone 1
should prove these rules before the response builder or service switch lands.

## Testing Strategy

Each milestone should be independently green.

Milestone 1 testing focuses on pure behavior:

- grouping tests
- decision precedence tests
- confidence tests
- action-generation tests
- fix-plan tests

Milestone 2 testing focuses on schema and mapping:

- response model serialization tests
- builder tests for representative decisions
- absence checks for removed Phase 1 fields
- redaction and truncation preservation checks where response assembly touches
  sensitive evidence

Milestone 3 testing focuses on integration:

- service-level tests for the new top-level response
- MCP tool-wrapper validation error tests
- demo integration test updates
- README example verification by inspection
- full repository checks after the switch

The usual verification commands remain:

```bash
.venv/bin/python -m pytest -v
.venv/bin/ruff check .
.venv/bin/pyright --pythonpath .venv/bin/python
git diff --check
```

## Documentation

The existing Phase 2 design remains the source for the final response contract.
This split design controls implementation sequencing.

The implementation plan should be rewritten around the three milestones above.
The README should not be updated in Milestone 1 unless a short developer-facing
note is needed. Public README examples should update in Milestone 3, when
`validate_patch` actually switches to the new response.

## Acceptance Criteria

- The Phase 2 implementation plan is reorganized into decision-engine,
  response-assembly, and service-switch milestones.
- Milestone 1 can land without changing the public `validate_patch` response.
- Decision precedence and confidence rules are tested before the public response
  switch.
- Milestone 2 maps the tested internal decision result into the Phase 2 response
  contract without recomputing decision behavior.
- Milestone 3 performs the breaking response change in one contained slice.
- No temporary Phase 1 compatibility mode is introduced.
- `inspect_workspace` remains source-compatible throughout.
- Existing shadow-workspace, redaction, command safety, and real-workspace
  non-mutation guarantees remain intact.
