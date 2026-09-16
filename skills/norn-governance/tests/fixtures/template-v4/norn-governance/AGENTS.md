<!-- norn:managed:start governance-directory -->
# Norn governance directory

This directory separates durable semantics, active development state, and optional evidence so each is loaded only when it can answer the current question.

## Directory responsibilities

- `spec/`: the main specification and, only when scale requires it, subordinate specifications indexed by `main-spec.md`. This is the project's semantic authority.
- `plans/`: one file per active unfinished development task. Plans make branch work resumable across sessions or devices; they are not specifications or history.
- `appendix/`: non-normative evidence and explanatory aids. It is optional reading and cannot authorize implementation.

## Active development plans

- Create a plan when work is expected to outlive the current session or device, or when several dependent checkpoints make reconstruction costly or risky.
- Name it with a stable date and task slug: `YYYY-MM-DD-<task>.md`.
- Inspect the relevant specification, code, tests, and Git state before writing. Record verified facts separately from assumptions and unresolved decisions; a plan must not substitute for discovery.
- Define an observable outcome, explicit numbered requirements with their authority or confirmation source and acceptance evidence, non-goals, and the verified baseline.
- Use ordered `pending`, `in_progress`, or `completed` steps. Every implementation step names the requirements it satisfies, repository-relative files or stable symbols, the intended behavior change, dependencies or material risks, and its verification. If a target is unknown, use a bounded discovery step with a concrete search and expected decision instead of inventing a path.
- Allow at most one `in_progress` step and keep one exact next action. Phrases such as “implement the feature”, “add tests”, or “update docs” are not actionable without targets, behavior, and verification.
- Use repository-relative paths and stable identifiers. Never store secrets, file bodies, temporary machine paths, command transcripts, or reusable approvals.
- On resume, verify the repository and specification before trusting the plan. Changed facts make the plan stale navigation, not authority.
- Update the plan only at meaningful checkpoints. Commit or push it with the development branch only when the user authorizes those Git actions.
- Delete the plan when work completes or is cancelled after durable results are captured in the specification, code, tests, and Git history. Keep a blocked plan only when its unblock condition and first resume action are explicit.
- Do not create plan indexes, completed-plan archives, a global `current-plan.md`, README files, or placeholders. The directory may be absent when no task is active.

## Appendix discipline

- Keep only evidence or explanation that is useful when tracing rationale and too costly or inappropriate to reconstruct from Git.
- Every appendix file must make its non-authoritative status clear and identify which current specification boundary it helps explain.
- When an appendix contains a current normative rule, promote that rule into the main specification and leave only historical evidence in the appendix.
- Prefer deletion over preserving duplicated, stale, or ownerless documentation.
<!-- norn:managed:end governance-directory -->
