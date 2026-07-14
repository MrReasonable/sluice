# Design specs

Validated designs live here, one file per piece of work, named
`YYYY-MM-DD-<topic>-design.md`.

A spec is written **before** the implementation plan and **before** any code. It is the
artefact `/review-plan` reads: that skill dispatches the specialist reviewers
(`sluice-invariant-reviewer`, `sluice-neutrality-reviewer`, `sluice-reviewer`,
`sluice-test-engineer`, and `sluice-architect` on structural change) against the most recent
spec in this directory and reports severity-grouped findings.

A spec is negotiated with its author, not patched mechanically — which is why `/review-plan`
has no auto-fix loop, unlike `/review-pr`.

Specs are historical once implemented. They are not maintained docs: `docs/ARCHITECTURE.md`
and `.rulesync/rules/CLAUDE.md` are the living descriptions of how sluice actually works. If a
spec and the code disagree, the code is right and the spec is a record of what was intended.
