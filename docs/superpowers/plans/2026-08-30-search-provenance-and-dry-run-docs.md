# Search provenance (#212 direction 1) + dry-run docs (#216) — Implementation Plan

> **Historical.** This plan records the design as written before implementation; per `CLAUDE.md`,
> a plan is not maintained once its work ships, and the shipped code is authoritative wherever the
> two disagree.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a `Search` say whether it came from the user's config or from the source's shipped example, surface that at run time and in `ingest list-sources --health`, and state in `docs/USAGE.md` what `--dry-run` does not bound.

**Architecture:** One new field on the `Search` dataclass, set in the one function that already
chooses between the two sources of searches (`searches_for`). Two read-only consumers surface it.
Nothing else changes behaviour: the field's default is `False`, which is what a shipped example is,
so every existing construction stays correct without being touched.

**Tech Stack:** Python 3.12+, stdlib only, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-30-role-type-and-company-casing-design.md` (§1.4, §6 PR 1)

**Why this PR exists first:** PR 2 (#223) needs to tell a user's `job_type` from the tool's guess.
Spec §1.4 measures that `_row_to_lead`'s `{**extra, **params}` merge is lossy and that
`searches_for` erases the only distinction available. This PR restores it. Without it, §2.1's
`declared` provenance cannot be computed and #223's fix demotes the user's own config to a guess.

## Global Constraints

- **Standard library only in `sluice/`.** No new runtime dependency in this PR.
- **Empty/absent config abstains.** `Search.configured` defaults to `False`; an unconfigured
  install must behave exactly as it does today.
- **No personal data in `sluice/` or `tests/`** — no employer names, locations, hostnames or
  absolute paths. Test fixtures use `example.invalid` URLs and the existing `demo` source helper.
- **Lazy imports in `cli.py`** for Camofox, the vault/store and the backends -- and ONLY those
  three; `sluice.ingest.base` is none of them (stdlib plus `sluice.core.leads` only), so it
  belongs at MODULE scope, as `from sluice.ingest import base as base_mod` (this is what
  shipped, in `sluice/cli.py`, and it is correct). Two reasons, not one: the codebase's own
  lazy-import rule is scoped to the three heavy seams, not to every import inside a command
  function, and this plan's own tests monkeypatch `cli.base_mod` (Task 3, Step 1) -- which
  requires the name to exist at module scope to be patchable at all. An earlier draft of this
  constraint had that backwards.
- **Conventional Commits** on every commit.
- **`.rulesync/` is canonical**; `CLAUDE.md`/`AGENTS.md`/`.claude/` are generated and gitignored.
  This PR does not need a `.rulesync/` change.
- Run `.venv/bin/ruff check sluice tests scripts` and `.venv/bin/python -m pytest` before every commit. `ruff` is not in
  the `test` extra: `pip install ruff==0.15.21` (the CI pin).

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `sluice/ingest/base.py` | `Search` dataclass; `_mk_search`; `searches_for` | Modify — add the field, set it in the override branch |
| `sluice/ingest/engine.py` | `SourceResult`; `_run_source` | Modify — count example searches per source per run |
| `sluice/cli.py` | `_print_report`; `cmd_list_sources` | Modify — surface the count |
| `docs/USAGE.md` | CLI reference | Modify — #216's sentence |
| `docs/ARCHITECTURE.md` | living technical description | Modify — one paragraph on the marker |
| `tests/test_base_sources.py` | `Search`/`searches_for` unit tests | Modify — add 4 tests |
| `tests/test_engine.py` | ingest engine tests | Modify — add 1 test |
| `tests/test_cli_report.py` | run-report rendering | Modify — add 1 test |
| `tests/test_health_completeness_visibility.py` | `list-sources --health` rendering | Modify — add 2 tests |

---

### Task 1: `Search` carries its provenance

**Files:**
- Modify: `sluice/ingest/base.py:29-33` (the `Search` dataclass), `:209-215` (`_mk_search`), `:218-231` (`searches_for`)
- Test: `tests/test_base_sources.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Search.configured: bool` (default `False`). `searches_for(source, config=None) -> list[Search]`
  returns searches whose `configured` is `True` **only** when they came from
  `sources.<id>.searches`. `_mk_search(spec, *, configured: bool = False) -> Search`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_base_sources.py`:

```python
def test_a_builtin_example_search_is_not_marked_configured():
    src = _demo_browser()
    assert [s.configured for s in searches_for(src, None)] == [False]


def test_a_config_override_search_is_marked_configured():
    src = _demo_browser()
    cfg = _FakeConfig({"demo": [["Mine", "https://example.invalid/q", {"job_type": "perm"}]]})
    assert [s.configured for s in searches_for(src, cfg)] == [True]


def test_an_empty_override_falls_back_and_is_not_marked_configured():
    # An override that is present but empty falls back to the built-in, so the
    # fallback must not inherit the override branch's provenance.
    src = _demo_browser()
    cfg = _FakeConfig({"demo": []})
    assert [s.configured for s in searches_for(src, cfg)] == [False]


def test_every_registered_source_ships_at_least_one_unconfigured_example():
    """SCOPE assertion, not a behaviour one: this sweep is what stops the three tests
    above certifying an empty set. A source with no built-in searches would make
    `configured is False` vacuously true for it, and #212 is precisely about the
    built-in set being non-empty and invisible."""
    from sluice.ingest import registry
    sources = list(registry.all_sources())
    assert len(sources) >= 10, f"registry enumerated only {len(sources)} sources"
    for src in sources:
        builtin = searches_for(src, None)
        assert builtin, f"{src.id} ships no example search"
        assert all(s.configured is False for s in builtin), f"{src.id} marks a built-in configured"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_base_sources.py -k configured -v`
Expected: FAIL — `AttributeError: 'Search' object has no attribute 'configured'`

- [ ] **Step 3: Implement**

In `sluice/ingest/base.py`, extend the dataclass:

```python
@dataclass
class Search:
    label: str
    url: str | None = None
    params: dict | None = None
    # Did this search come from the user's `sources.<id>.searches`, or is it the
    # source's shipped example? (#212) The default is False because that is what a
    # shipped example IS -- so every existing construction, including
    # `BrowserListSource.searches()`, stays correct without being touched, and a
    # future search-producing path that forgets to think about provenance is
    # treated as the tool's guess rather than the user's assertion. The direction
    # matters: #223 lets a `configured` search's `params` drive a pay floor, so a
    # wrong True is a shipped preference wearing the user's authority.
    configured: bool = False
```

Thread it through `_mk_search`:

```python
def _mk_search(spec, *, configured: bool = False) -> Search:
    """A searches_spec entry is (label, url) or (label, url, params) - the optional
    params carry per-search metadata (e.g. {"job_type": "perm"}) so the one engine
    covers perm + contract just by varying search terms/params, not code.

    `configured` says which SIDE of `searches_for`'s choice this entry came from; it is
    keyword-only so a positional third argument can never be mistaken for it."""
    label, url = spec[0], spec[1]
    params = spec[2] if len(spec) > 2 else None
    return Search(label=label, url=url, params=params, configured=configured)
```

And set it in the one place that knows:

```python
        if override:
            return [_mk_search(spec, configured=True) for spec in override]
    return list(source.searches())
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_base_sources.py -v`
Expected: PASS, including the four pre-existing `searches_for` tests.

- [ ] **Step 5: Run the whole suite and the linter**

Run: `.venv/bin/python -m pytest && .venv/bin/ruff check sluice tests scripts`
Expected: all pass. The suite is fast and hermetic; run all of it.

- [ ] **Step 6: Commit**

```bash
git add sluice/ingest/base.py tests/test_base_sources.py
git commit -m "feat(ingest): a Search records whether the user configured it

searches_for() returned a config override and a shipped example as the same
plain list, so nothing downstream could tell a user's search criteria from the
source's illustrative one. #223 needs that distinction to tell a job_type the
user asserted from one the tool guessed.

The default is False -- what a shipped example is -- so every existing
construction stays correct and a future producer that forgets provenance is
treated as the tool's guess rather than the user's assertion.

Refs #212"
```

---

### Task 2: the run report counts example searches

**Files:**
- Modify: `sluice/ingest/engine.py:89-118` (`SourceResult`), `:182-195` (`_run_source`)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `Search.configured` from Task 1.
- Produces: `SourceResult.example_searches: int` — how many of the searches this source ran this
  run were shipped examples. `0` when every search was user-configured.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engine.py`:

```python
def test_source_result_counts_the_example_searches_it_ran():
    """#212: an unconfigured source runs someone else's criteria at full volume and
    nothing said so. The count is per RUN, so a partially-configured source reports
    the remainder rather than being binary."""
    from sluice.ingest.base import Search
    from sluice.ingest.engine import SourceResult, _count_example_searches

    assert _count_example_searches([Search("a"), Search("b")]) == 2
    assert _count_example_searches([Search("a", configured=True), Search("b")]) == 1
    assert _count_example_searches([Search("a", configured=True)]) == 0
    assert _count_example_searches([]) == 0
    assert SourceResult(source_id="demo").example_searches == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engine.py -k example_searches -v`
Expected: FAIL — `ImportError: cannot import name '_count_example_searches'`

- [ ] **Step 3: Implement**

Add the field to `SourceResult` in `sluice/ingest/engine.py`, beside `withheld`:

```python
    # How many of THIS RUN's searches were the source's shipped example rather than the
    # user's own (#212). A count, not a flag: a source with three configured searches and
    # one left at the example is the case a boolean would round away, and it is the case
    # most likely to surprise -- the user believes they configured that board.
    example_searches: int = 0
```

Add the helper above `_run_source`:

```python
def _count_example_searches(searches) -> int:
    """How many of `searches` are the source's shipped example. Keyed on the flag
    `searches_for` set, never re-derived from the config here: re-deriving would put a
    second copy of that decision one layer away from the one that made it."""
    return sum(1 for s in searches if not getattr(s, "configured", False))
```

And record it in `_run_source`, immediately after `searches` is built:

```python
    searches = list(searches_for(source, getattr(ctx, "config", None)))
    result.example_searches = _count_example_searches(searches)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite and the linter**

Run: `.venv/bin/python -m pytest && .venv/bin/ruff check sluice tests scripts`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add sluice/ingest/engine.py tests/test_engine.py
git commit -m "feat(ingest): count the shipped example searches a run used

A count rather than a flag: a source with three configured searches and one
left at its example is the case a boolean rounds away, and it is the one most
likely to surprise.

Refs #212"
```

---

### Task 3: surface it in `_print_report` and `ingest list-sources`

**Files:**
- Modify: `sluice/cli.py:506-515` (`_print_report`), `sluice/cli.py:151-207` (`cmd_list_sources`)
- Test: `tests/test_cli_report.py`, `tests/test_health_completeness_visibility.py`

**Interfaces:**
- Consumes: `SourceResult.example_searches` (Task 2), `Search.configured` (Task 1).
- Produces: no new API. Two rendered strings: ` example_searches=N` on a run-report line, and
  ` EXAMPLE-SEARCH(n/m)` on an enabled source's `list-sources` line (with or without `--health`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_report.py`:

```python
def test_report_line_names_example_searches_only_when_there_are_some():
    from sluice.cli import _print_report
    from sluice.ingest.engine import RunReport, SourceResult
    import io, contextlib

    rep = RunReport()
    rep.sources = [SourceResult(source_id="alpha", fetched=3, fresh=1, example_searches=2),
                   SourceResult(source_id="beta", fetched=3, fresh=1, example_searches=0)]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_report(rep)
    alpha, beta = [l for l in buf.getvalue().splitlines() if l.strip()][:2]
    assert "example_searches=2" in alpha
    assert "example_searches" not in beta
```

Append to `tests/test_health_completeness_visibility.py`:

```python
def test_list_sources_health_flags_a_source_running_only_examples(monkeypatch, capsys):
    """#212: `list-sources --health` is what a human runs to ask whether a board is
    working. An unconfigured board IS working and returning someone else's criteria,
    which is the state this line makes visible."""
    from types import SimpleNamespace
    import sluice.cli as cli

    src = SimpleNamespace(id="demo", kind="list", searches=lambda: [], unpublished_fields=())
    monkeypatch.setattr(cli.registry, "all_sources", lambda: [src])
    monkeypatch.setattr(cli, "_is_enabled", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_disabled_or_warn", lambda: set())
    monkeypatch.setattr(cli.base_mod, "searches_for", lambda s, c: [
        cli.base_mod.Search("Example", "https://example.invalid/a"),
        cli.base_mod.Search("Example", "https://example.invalid/b"),
    ])
    cli.cmd_list_sources(SimpleNamespace(health=False), None)
    assert "EXAMPLE-SEARCH(2/2)" in capsys.readouterr().out


def test_list_sources_health_says_nothing_when_every_search_is_configured(monkeypatch, capsys):
    from types import SimpleNamespace
    import sluice.cli as cli

    src = SimpleNamespace(id="demo", kind="list", searches=lambda: [], unpublished_fields=())
    monkeypatch.setattr(cli.registry, "all_sources", lambda: [src])
    monkeypatch.setattr(cli, "_is_enabled", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_disabled_or_warn", lambda: set())
    monkeypatch.setattr(cli.base_mod, "searches_for", lambda s, c: [
        cli.base_mod.Search("Mine", "https://example.invalid/a", configured=True),
    ])
    cli.cmd_list_sources(SimpleNamespace(health=False), None)
    assert "EXAMPLE-SEARCH" not in capsys.readouterr().out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_report.py -k example tests/test_health_completeness_visibility.py -k EXAMPLE -v`
Expected: FAIL — the strings are absent, and `cli.base_mod` does not exist.

- [ ] **Step 3: Implement**

In `sluice/cli.py`, add the module alias the tests monkeypatch, beside the existing `registry`
import at module scope:

```python
from sluice.ingest import base as base_mod   # Search/searches_for; stdlib-only, no browser
```

Extend `_print_report`'s f-string, after the `withheld` clause and matching its sparse style:

```python
              # Sparse like `withheld`: on a fully configured install this is always 0, and
              # a source running its shipped example is exactly what #212 says is invisible.
              f"{f' example_searches={r.example_searches}' if r.example_searches else ''}"
```

In `cmd_list_sources`, at the OUTER level of the `for src in ...` loop — immediately before
`print(line)`, **not** inside the `if health is not None:` block that wraps the UNMEASURED/UNGUARDED
arms. Two reasons, and the first is load-bearing: that block ends at `print(line)`, so anything
placed inside it appears only under `--health`, and the tests above call `cmd_list_sources` with
`health=False`. The second is semantic — which searches a source runs is CONFIG state, not health
state, so it belongs on the plain listing too.

```python
        # Whether this source runs the USER's criteria or its shipped example (#212).
        # Enabled sources only, for the same reason UNGUARDED is: a disabled source runs
        # nothing, so it is neither configured nor falling back. Printed as n/m rather than
        # a bare flag so a PARTIALLY configured source -- three searches of its own and one
        # left at the example -- is visible rather than rounded to one answer or the other.
        if state == "enabled":
            searches = base_mod.searches_for(src, config)
            examples = sum(1 for s in searches if not s.configured)
            if examples:
                line += f" EXAMPLE-SEARCH({examples}/{len(searches)})"
        print(line)
```

(The existing `print(line)` is shown for placement only — do not add a second one.)

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_cli_report.py tests/test_health_completeness_visibility.py -v`
Also run `job-sluice ingest list-sources` (no `--health`) and confirm the marker appears there
too — that is the placement this task depends on, and a test calling the function directly would
not catch it being nested one level too deep.
Expected: PASS.

- [ ] **Step 5: Run the whole suite and the linter**

Run: `.venv/bin/python -m pytest && .venv/bin/ruff check sluice tests scripts`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add sluice/cli.py tests/test_cli_report.py tests/test_health_completeness_visibility.py
git commit -m "feat(cli): surface which sources are running shipped example searches

A successful run never named the search it ran -- search.label reached the user
only inside a failure warning, the inverse of when it helps. Now the run report
names the count and list-sources --health names it per source, both sparse so a
fully configured install sees nothing.

Refs #212"
```

---

### Task 4: docs — #216's sentence, and the marker

**Files:**
- Modify: `docs/USAGE.md:67` region (the `ingest run` flag table), `docs/ARCHITECTURE.md`
- Test: `tests/test_docs_claims.py` (existing; must keep passing)

**Interfaces:**
- Consumes: nothing. Documentation only.
- Produces: nothing.

- [ ] **Step 1: Confirm the docs guard passes before the edit**

Run: `.venv/bin/python -m pytest tests/test_docs_claims.py tests/test_doc_links_from_code.py -v`
Expected: PASS. This is the baseline — these guards walk the real `cli.py` parser and check every
anchored link between shipped docs, so a later failure is attributable to this task.

- [ ] **Step 2: Add #216's sentence to `docs/USAGE.md`**

Immediately after the `ingest run` flag table (the line
`| `--dry-run` | off | still records source health, writes nothing to the vault or `seen.db` |`),
insert this paragraph:

```markdown
`--dry-run` bounds what sluice **writes**, not what a run **does**. A dry run still invokes every
selected source's `fetch` exactly as a real run does — the flag only changes the SINK (JSON instead
of vault) and softens the relocated-`seen.db` guard from a refusal to a warning (`fatal=not
(dry_run or json_sink)` in `Sluice.ingest`). The flag itself never reaches `fetch`, and no source can ask whether it
is set — so a source has no way to suppress a remote side effect just because this is a dry run; any
side effect a fetch has on the far side happens on a dry run exactly as it does on a real one.
`ingest test-source` calls `fetch` with no sink at all and inherits the same property. No shipped
source mutates remote state today; this states the boundary rather than a current hazard, the way
the `triage run` row above names its billed backend call.
```

- [ ] **Step 3: Add the marker paragraph to `docs/ARCHITECTURE.md`**

In the ingest section, after the paragraph describing `searches_for`, insert:

```markdown
A `Search` records whether it came from `sources.<id>.searches` or from the source's shipped
example (`Search.configured`, #212). `searches_for` is the only writer — it is the one function that
chooses between the two — and the default is `False`, which is what a shipped example is, so a
producer that never thinks about provenance is treated as the tool's guess rather than the user's
assertion. Two read-only consumers surface it: the run report's `example_searches=N` and
`ingest list-sources --health`'s `EXAMPLE-SEARCH(n/m)`, both sparse, so a fully configured install
sees neither. The flag exists because a plain `list[Search]` could not distinguish the two, which
is what stopped #223 telling a `job_type` the user asserted from one a source's `extra` guessed.
```

- [ ] **Step 4: Re-run the docs guards and the whole suite**

Run: `.venv/bin/python -m pytest && .venv/bin/ruff check sluice tests scripts`
Expected: all pass, `tests/test_docs_claims.py` and `tests/test_doc_links_from_code.py` included.

- [ ] **Step 5: Commit**

```bash
git add docs/USAGE.md docs/ARCHITECTURE.md
git commit -m "docs: state what --dry-run does not bound, and the search marker

USAGE's ingest --dry-run row named only the vault and seen.db while the triage
row named its billed backend call. dry_run is consumed in Sluice.ingest purely
to pick the sink; it never reaches a fetcher and no fetcher can ask. Stating
the boundary now rather than after a source mutates remote state -- none does,
since CarouselSource was retired at #217, which is why this is a sentence and
not a fetch_mutates_remote declaration nothing would implement.

Closes #216. Refs #212"
```

---

## Definition of Done

- [ ] `.venv/bin/python -m pytest` passes (whole suite; it is fast, hermetic and offline).
- [ ] `.venv/bin/ruff check sluice tests scripts` passes.
- [ ] `.venv/bin/python -m pytest --cov` reports without error (coverage is reported, never gated).
- [ ] `job-sluice ingest list-sources` prints `EXAMPLE-SEARCH(n/m)` for an enabled source
      with no `sources.<id>.searches` override, and nothing for a fully configured one. Run it.
- [ ] `grep -rn "configured" sluice/ingest/base.py` shows ONE function that ever sets the flag
      TRUE (`searches_for`'s override branch) and ONE writer of `Search(...)` itself in
      `sluice/` (`_mk_search`, the only site in `sluice/` that constructs a `Search` --
      `tests/` constructs one directly at 34 sites, `Search(...)` not being private to
      this module).
- [ ] No new entry in `sluice.yaml.example` — this PR adds no config key.
- [ ] Before pushing: run `/review-pr`, then CodeRabbit. That order — the specialist team is free
      and parallel; CodeRabbit is the scarce resource and is dismissed on every push.

## Out of scope for this PR

- #212 direction 2 (a `doctor` line for search-configuration coverage).
- #212 direction 3 (what an unpicked board should do at `init`). This is the design-laden half and
  is where the empty-config-abstains inversion gets decided; the reasoning is on the issue.
- Anything in `sluice/core/roletype.py`, `Lead.job_type_source`, or `classify.py` — that is PR 2.
