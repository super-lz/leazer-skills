<!-- norn:managed:start specification-governance -->
# Main specification governance

`main-spec.md` is the project's semantic authority—the product's durable purpose and rules independent of language, framework, file layout, or one implementation revision.

## What belongs in the main specification

- real users, problems, and observable outcomes;
- accepted scope, explicit non-goals, and deferred boundaries;
- core flows, states, rules, interfaces, side effects, and failure behavior;
- permission, safety, privacy, portability, and recovery invariants;
- acceptance criteria and evidence required to advance a milestone;
- stable decisions needed to rebuild a semantically equivalent system.

Do not put transcripts, daily progress, task checklists, speculative wish lists, temporary implementation choices, or code-level walkthroughs in the specification.

## Maintenance

- Read the complete main specification before architectural or behavioral work.
- A new requirement or intentional contract change must be summarized and confirmed before writeback and implementation.
- When reachable code and tests prove stable behavior that the specification omits, update the specification in the same change.
- When intent is ambiguous or implementations disagree, ask for a product decision instead of silently choosing one.
- Record the semantic result and trade-off, not the conversation that produced it.
- Mark `Accepted`, `Proposed`, `Experimental`, and `Deferred` boundaries explicitly. Only accepted current scope authorizes implementation.

## Splitting the specification

Keep one `main-spec.md` while it remains easy to navigate. Split only when independent system boundaries have become stable and a single file materially harms comprehension or ownership.

When splitting:

```text
norn-governance/spec/
  main-spec.md
  <system-boundary>/
    index.md
    <contract>.md
```

`main-spec.md` remains the authoritative product map and links every subordinate specification. Split by stable system boundary, not by team, ticket type, or development phase. A reader must still be able to start from `main-spec.md` and discover every normative rule.
<!-- norn:managed:end specification-governance -->
