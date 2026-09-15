# AWS Connect Agent Guide

## Purpose

This file is the repository map, not the knowledge base. Keep detailed product,
architecture, security, UI, and execution knowledge under `docs/`. Repository
state—not chat history—must be sufficient for a new session to continue safely.

## Start Every Session

1. Run `rg --files`; run `git status --short` when Git exists.
2. Read `ARCHITECTURE.md` and list `docs/exec-plans/active/`.
3. Read the active plan and only the product/design documents it links.
4. Inspect related code/tests with `rg` before adding a rule, helper, or module.
5. Identify the active phase, acceptance criteria, blockers, and smallest verifiable step.

Never rely on unrecorded context from an earlier conversation.

## Knowledge Map

- `ARCHITECTURE.md` — short system, layer, and dependency map.
- `docs/product-specs/` — requirements and user journeys.
- `docs/design-docs/` — architecture decisions, beliefs, and UI references.
- `docs/exec-plans/active/` — executable plans currently in progress.
- `docs/exec-plans/completed/` — completed plans and verification evidence.
- `docs/exec-plans/tech-debt-tracker.md` — accepted debt and ownership.
- `docs/generated/` — reproducible generated docs; never hand-edit.
- `docs/references/` — curated external references.
- `docs/DESIGN.md` — GUI design authority.
- `docs/FRONTEND.md` — GUI implementation rules.
- `docs/PLANS.md` — execution-plan format and lifecycle.
- `docs/PRODUCT_SENSE.md` — user value and scope judgment.
- `docs/QUALITY_SCORE.md` — quality gaps and trends.
- `docs/RELIABILITY.md` — failure, retry, process, port, and SQLite policy.
- `docs/SECURITY.md` — credentials, DPAPI, masking, and least privilege.
- `ref/` — legacy BAT/INI reference only; never use its secrets as fixtures.

## Authority and Boundaries

Resolve conflicts in this order: product spec; security/reliability invariants;
architecture/design; active plan; implementation patterns. Record conflicts and
update the authority—never silently decide a product or security rule in code.

- Dependency direction: `presentation -> application -> domain`.
- Infrastructure implements ports; Domain never imports outer layers.
- `bootstrap.py` is the sole composition root for real adapters.
- CLI and GUI use the same typed Application Services and DTOs.
- GUI never executes or parses CLI output.
- Domain/Application never use `print`, `input`, `sys.exit`, or GUI widgets.
- Translate AWS/Windows failures to typed application errors at the boundary.
- Define error types once; give CLI and GUI one mapper each.
- Use shared operation contracts for MFA, progress, cancellation, and sessions.
- Avoid a catch-all `common.py`; share by domain responsibility and change reason.

Architecture rules require automated tests, not prose alone.

## Delivery Order

For each vertical slice: confirm acceptance criteria; define Domain/DTO/error
contracts; implement Application Services and ports; unit-test with fakes;
implement/test adapters; connect and verify CLI; connect GUI to the same service;
run consistency/security/package gates; update plans and authorities.

Feature GUI logic begins after its Application and CLI gates pass. A fake-backed
GUI shell may proceed independently.

## Execution and Verification

- Keep one non-trivial effort in `docs/exec-plans/active/NNN-title.md`.
- Record scope, non-goals, criteria, decisions, side effects, commands, and results.
- Mark progress with evidence; put accepted debt in the tracker.
- Move finished plans to `completed/`; update divergence before continuing.
- Iterate with the narrowest test, then run `./scripts/check.ps1` before completion.
- Use the specialized `scripts/test-*.ps1` and `scripts/verify-docs.ps1` as needed.
- Never claim a check passed without its command and result.
- Real AWS tests require explicitly approved test profiles and resources.
- If a required script is absent, implement it through the active plan; do not
  claim that it ran.

## Documentation and Security

- Keep this file navigational; link to details instead of duplicating them.
- Update the authority when schema, CLI, errors, security, or UI flows change.
- Validate links, generated-doc drift, CLI help, and error-mapper completeness.
- Turn repeated agent confusion into a clearer map, rule, fixture, or check.
- Never commit credentials, tokens, MFA codes, secret values, or real resource IDs.
- Never expose secrets in arguments, logs, fixtures, snapshots, or serialized errors.
- Use DPAPI storage, central masking, least privilege, and non-production AWS tests.
- Never delete/overwrite user data or AWS resources without explicit authorization.

## End Every Session

Update the active plan with completed work, exact commands and pass/fail evidence,
decisions/deviations, blockers, and a concrete next step. Record accepted follow-ups
in the debt tracker. No essential continuation context may live only in chat.

A task is done only when its acceptance criteria and relevant gates pass, secrets
checks and runtime side effects are accounted for, and plans plus authorities match
the implementation.
