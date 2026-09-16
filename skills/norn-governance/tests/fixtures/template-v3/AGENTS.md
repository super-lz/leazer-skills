<!-- norn:managed:start core-governance -->
# Norn collaboration contract

Norn keeps project truth small, explicit, and resumable. Use the repository rather than chat history as the durable source of truth.

## Authority model

Use these sources for different questions:

1. `norn-governance/spec/main-spec.md` is the main specification: the durable product purpose, user outcomes, rules, boundaries, contracts, failure behavior, and acceptance criteria.
2. Current code and tests are implementation reality: they show how the accepted specification currently works.
3. Git history records evolution: it explains when and why specification and implementation changed.

`norn-governance/plans/` is temporary development state, not authority. `norn-governance/appendix/` is optional evidence and explanatory material, not authority. Neither may silently override the main specification or current implementation facts.

## Required reading

- Read this file before non-trivial work.
- Read the complete main specification before proposing architecture, changing behavior, or implementing a new requirement.
- When resuming unfinished work, first verify the branch, Git status, main specification, and relevant code; then read only the matching active plan.
- Read appendix material only when the task needs original evidence, historical rationale, or an explanatory aid.

## Development lifecycle

1. Identify the real user outcome, the smallest complete boundary, explicit non-goals, and how the result will be verified.
2. Reconcile the request with the main specification and current code. A new or changed durable requirement must be summarized and confirmed before the specification is changed or implementation begins.
3. Create an active development plan only when the task is expected to remain unfinished across a session or device, or when dependent checkpoints make reliable resumption materially valuable. Before saving it, inspect the relevant specification, code, tests, and Git state; record an observable outcome, explicit requirements and non-goals, a verified baseline, and ordered steps tied to repository-relative targets and verification. Unknown locations become bounded discovery steps rather than invented paths. Simple work stays in the current task context.
4. Implement against the main specification, using code and tests to preserve implementation truth. Do not add infrastructure or abstractions without a current measured need.
5. Update an active plan at meaningful checkpoints. Keep step states current, allow at most one active step, preserve verification evidence and blockers, and name one exact next action. Do not turn it into a transcript.
6. Before completion, run proportionate verification and reconcile stable behavior with the main specification.
7. When the task completes or is cancelled, move durable conclusions into the specification, code, tests, or Git history and delete its active plan. A blocked task may retain its plan with a concrete resume condition.

## Specification synchronization

- If current, reachable code and tests clearly implement a stable behavior that the specification omits, update the specification in the same change and report the semantic writeback.
- If code and specification conflict and the intended behavior is not provable, stop and ask for the product decision; do not choose authority by convenience.
- Implementation mechanisms that do not affect semantic equivalence stay in code. Promote them only when they become a user promise, cross-boundary contract, safety rule, or acceptance condition.
- Deferred ideas and appendix statements do not authorize scaffolding or implementation. They must first become accepted current scope in the main specification.

## Documentation placement

- `spec/`: durable normative semantics required to rebuild an equivalent system.
- `plans/`: active, unfinished development state needed to resume work.
- `appendix/`: non-normative evidence or explanation used only when tracing why.
- Code, tests, and necessary comments: current implementation mechanisms and invariants.

Classify documents by content, never by filename or old directory alone. If one document mixes roles, split or consolidate it so each lasting statement has one authoritative home.

## Safety and verification

- Preserve unrelated user changes and inspect the working tree before editing.
- Never commit secrets, real personal data, credentials, production datasets, or private raw documents.
- Consider failure, cancellation, cross-platform, security, privacy, migration, and recovery effects in proportion to the change.
- Report only verification that was actually run. A structurally current Norn installation does not prove that project semantics are complete or correctly classified.
<!-- norn:managed:end core-governance -->
