# Contributing

## Setup

```bash
git clone https://github.com/MrReasonable/sluice.git
cd sluice
npm ci --ignore-scripts && npm run rulesync   # populates CLAUDE.md, AGENTS.md, .claude/ — all gitignored
pip install -e ".[test]"                      # pytest, pytest-cov, faker, jinja2, setuptools + build
```

`.rulesync/rules/CLAUDE.md` is the **canonical** source for the detailed engineering rules
this project runs on — module architecture, the config-layering discipline, the four
invariants below, and a lot of load-bearing rationale earned the hard way. `CLAUDE.md`,
`AGENTS.md` and `.claude/` are *generated* from it and gitignored; editing one of those instead
of the `.rulesync/` source is drift, and CI's `rulesync` job fails the build on any. Run
`npm run rulesync` again after pulling a change to `.rulesync/`.

`--ignore-scripts` is not optional housekeeping: one package in the pinned tree declares a
`postinstall`, and CI runs the exact same install command — a doc that drops the flag sends a
contributor down an install path CI doesn't take.

## Running the checks

```bash
python -m pytest                        # full suite; hermetic, no network, sub-second
python -m pytest tests/test_x.py -k y   # one file / one test
ruff check sluice tests scripts         # pip install ruff==0.15.21 -- the exact CI pin; ruff is NOT in [test]
python -m pytest --cov                  # the coverage report CI publishes; REPORTS, does not gate (see below)
```

Everything above is fully offline — no Camofox, no live backend, no vault required. The
suite runs in well under a second, so there's no reason to run a subset while iterating; run
all of it. `run_tests.sh` is the same thing via `.venv/bin/python`, if you keep a local venv.

**Why coverage has no threshold.** `[tool.coverage.report]` in `pyproject.toml` sets
`show_missing = true` and deliberately no `fail_under`. A percentage floor invites tests
written to move the number rather than to catch a bug — three of this project's worst defects
(a flag parsed and never forwarded, a fallback backend that looked wired but wasn't, a default
that silently binned every lead) were all invisible to a fully green, high-coverage suite. What
counts here is whether a test *asserts on behaviour*, not whether a line executed.

## What a test needs to prove

Tests assert on behaviour, not merely that code runs, and fixtures stay synthetic — see
"Neutrality" below. If you're adding a guard (a check that something *doesn't* happen — a
leaked value, a forbidden pattern, a gate staying closed), the standard here is higher than
"the assertion passed":

- **Prove it catches the bug it claims to catch, by breaking the code and watching the test go
  red** — not by reading the code and reasoning that it should. Mutate by *moving or deleting*
  a line, never by adding a check beside the original; an added check is an equivalent mutant
  that leaves the original one still firing, so the suite stays green either way and proves
  nothing about the new assertion.
- **A guard over a *negative* property (nothing found = success) must still assert it looked at
  something.** `all([])` is `True` in Python, so a sweep whose matcher is silently broken
  passes vacuously. Pin the scope it enumerated (the files walked, the classes discovered),
  not only the absence of violations.
- Before mutating, run `python -m compileall -q -f --invalidation-mode checked-hash sluice
  tests scripts` once. CPython invalidates a `.pyc` on `(mtime, size)`, so a same-second,
  size-preserving edit can silently run *stale* bytecode against the *new* source and pass for
  the wrong reason — this has cost a real debugging session here before. That command makes
  `sluice/`'s and `scripts/`'s caches content-addressed instead.

## Commit style

Conventional Commits (`fix(cv): ...`, `feat(triage): ...`, `docs: ...`, `ci: ...`). This is
not a style preference: [release-please](https://github.com/googleapis/release-please) reads
commit subjects to decide the next version and draft the changelog, so a mistyped `type`
silently changes what a release claims to contain.

Releases are cut by **merging release-please's PR**, never by tagging by hand — the tag and
the declared version are written by the same tool in the same commit, so they can't drift
apart. Version bumps and the changelog itself get edited by hand *inside that PR* before
merging: a generated commit subject can't tell you a config's *meaning* changed, and per
`CHANGELOG.md`'s own policy a breaking config change matters more here than a breaking API
change, since nothing imports `sluice` as a library — what you've invested in is your
`sluice.yaml` and your vault.

## The four invariants

These are enforced by tests and by the specialist review agents this project runs on every
PR (`.rulesync/skills/review-pr/SKILL.md`); the full mechanics live in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and `.rulesync/rules/CLAUDE.md`.

- **Never-clobber.** A re-scrape of an existing lead touches only its `last_seen` marker —
  never status, never enrichment, never the note body. Every field-level write is a
  compare-and-set against a freshly re-read note, never a blind overwrite.
- **Never-regress.** Status only moves forward. Triage owns the early states and must never
  touch a lead that has already entered the application lifecycle.
- **The CV fabrication gate is hard.** Every `WORK EXPERIENCE` bullet must cite a real,
  verified source entry; a CV that fails is never rendered. This gate is pure and
  deterministic — it does not get relaxed for convenience.
- **Empty config means abstain, not match-nothing.** Every preference gate defaults to empty,
  and an unconfigured gate passes every lead through. Getting this backwards has silently
  binned someone's entire job hunt before; see `docs/CONFIGURATION.md`.

## Neutrality

No employer names, role preferences, locations, contact details, hostnames or absolute paths
belong in `sluice/` or `tests/`. Generate synthetic fixtures (this project uses seeded `faker`
— see `tests/conftest.py`) rather than hardcoding anyone's real job search. Your own
preferences belong in your config and your vault, never in a commit.

## Dependencies

`sluice/` is standard-library only, apart from `pyyaml`. The exceptions —
`jinja2`/`weasyprint` (the `render` extra), the Google client libraries (`google`), and
`argcomplete` (`completion`) — are all lazily and defensively imported, so a bare install never
needs them. Don't add a new runtime dependency without a deliberate decision recorded in
`.rulesync/rules/CLAUDE.md`'s dependency section, matching how the existing ones are justified
there.

## Pull requests

Small, focused, and landing with tests that would fail without the change. If you're touching
one of the four invariants above, a store/renderer/fetcher/backend seam, or anything in
`core/paths.py`, expect that to get closer review — those are exactly the places a quiet wrong
default has bitten this project before.
