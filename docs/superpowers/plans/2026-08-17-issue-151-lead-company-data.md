# Fix issue #151 — three defects leaving 32% of lead notes with no usable company

Spec: GitHub issue #151 (`gh issue view 151`), and the fully-researched, user-approved plan
produced in the preceding planning session (a local working document, outside this repo). That
plan is the binding authority for every design decision below; this document restates it as
SDD tasks.

## Context

On the production vault, 625 of 2514 lead notes (measured this session; the issue's own counts —
787 of 2454 — are slightly stale) carry a company field that is empty or a placeholder like
`"Unknown"`. This is not cosmetic: `receipt.match_receipt`'s corroborated tier starts with
`if company and company <= tokens:` — an empty company fails immediately and `"Unknown"`
tokenises to `{"unknown"}` — so these notes are structurally incapable of corroborating a
receipt (compounding #136). It also produces duplicate pairs (one correct, one garbled ingest of
the same role) that defeat status dedup.

Three unrelated defects share the symptom, confirmed against the live vault (see Global
Constraints for the exact measured numbers — every implementer should treat these as ground
truth over the issue's own prose):

1. **Company never extracted at ingest — naukrigulf mashes company onto role with no separator**
   (72 of 76 blank-company notes recoverable from the listing URL's own seam); **wellfound**
   yields company-*profile* card rows that aren't job postings at all.
2. **Filename stale, frontmatter fine — 226 notes** named `" - <role>.md"` while their
   frontmatter `company` is now populated by triage's backfill pass. Needs a dedicated rename
   pass, run after whatever backfills company.
3. **Resolver structurally cannot repair a placeholder company** (150 `"Unknown"`, 3
   `"Confidential"`, 1 `"NA"` = 154 notes). `classify.py` recognises the sentinel; the engine's
   resolution gate and its `require_blank` write guard do not — so the repair pass never runs
   on these notes even though the machinery to fix them already exists.

## Global Constraints

Bind every task below. Copy the relevant lines into no dispatch — the controller pastes this
whole block into every implementer/reviewer dispatch's constraints section.

**Repo-wide (from `CLAUDE.md`):**
- `pip install -e ".[test]"` is already done in this worktree's `.venv/`. Use
  `.venv/bin/python -m pytest` and `.venv/bin/ruff check sluice tests scripts` (ruff pinned at
  `0.15.21`, already installed). Both must be clean before any commit in this plan.
- TDD: write the failing test first for new behaviour; every existing test must stay green
  unless a task explicitly says otherwise (and names which test and why).
- Comments explain **why** — the invariant upheld, the bug prevented, the trade-off taken.
  Match the existing density; do not add comments describing what a line does or narrating the
  change ("added X" / "per the plan").
- Conventional commits (`fix(triage): ...`, `feat(vault): ...`, `refactor(triage): ...`,
  `docs: ...`). The exact subject line is given in each task.
- Every commit message ends with the trailer line:
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`
- `sluice/` is standard-library only (the guarded exceptions are `yaml`, the Google client
  libs, `jinja2`/`weasyprint`, and `argcomplete` — none of this plan's tasks need a new
  dependency). Do not add one.
- No real employer names, locations, or personal data anywhere in `sluice/` or `tests/`. Use
  `Example <Word>` or names already present in the fixtures being edited.
- Never `except BaseException`; a comment stating a mechanism needs a test that would fail if
  the mechanism were removed — don't add prose-only justifications.
- Before any mutation-testing witness (deleting/moving code to prove a test catches it — never
  by adding a duplicate check beside the original), run:
  `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
  first, or a same-second size-preserving edit can run stale bytecode and lie green.

**This plan's own invariants (do not re-litigate — these are settled design decisions):**
- **Never widen `_candidate_names`'s inputs to read frontmatter.** It must keep deriving a
  note's candidate filenames from the *scraped* `Lead` object only. Every legacy
  `archived_from_note` stamp in `_merged/` (the #81 non-resurrection probe) depends on that.
- **A guard that must see fresh data belongs inside a CAS transform, never at the caller.** Any
  new refusal/blank-check tied to `update_fields` must be evaluated against freshly-read bytes
  inside `core/vault.py`'s existing `_cas_write` transform closure — a caller-side check reading
  a stale snapshot is the textbook equivalent mutant this repo's `require_blank` design exists
  to forbid.
- **Never add a new frontmatter-write function.** Extend the *existing* `update_fields`
  signature with a new keyword argument. CodeQL treats a new write function as a new sink, and
  this repo has one deliberate sink (`_set_fm`/`update_fields`) by design.
- **A refusal/skip outcome must never silently mask a lead from `seen.db`.** Anything that
  causes ingest to skip writing a lead must keep it eligible for retry on the next run (i.e.
  never call `sink.write` in a way that marks it seen).
- **Measured production numbers, treat as ground truth over the issue text:** naukrigulf has 76
  blank-company notes, all 76 with a `-jobs-in-` URL seam, 72 recoverable by longest-prefix
  matching; 4 abstain (unusual separators — leave them as-is). wellfound has 25 notes, 12 with
  bare `/company/<slug>` URLs (company-profile cards, not job postings), 5 of those with
  `role == company`. 154 notes carry a placeholder company (150 `Unknown`, 3 `Confidential`, 1
  `NA`); only 4 have `" at "` anywhere in the role text. 226 notes have a stale `" - "` filename
  with a populated frontmatter company; 0 of the 226 rename targets collide with an existing
  note; 6 of the 226 are status `applied`. Only 1 of 18 currently-archived (`_merged/`) losers
  carries a blank-company archive stamp, and its source (`cwjobs`) is untouched by this plan —
  so defect 1's fix does not newly expose the #81 resurrection risk.
- **Explicitly rejected approaches — do not re-propose these:**
  - A generic ingest-time refusal for "role looks mashed" (e.g. a bare `[a-z][A-Z]` seam
    check) — false-positives on ordinary vocabulary (`JavaScript`, `DevOps`, `McKinsey`), and a
    `refused` lead stays out of `seen.db` and re-reports every run forever, which is worse than
    the defect.
  - A `company_from_url` rule for `cwjobs`/`totaljobs` — their URL grammar looks regular and
    matches 72% of the time, but validated against notes with a known company, the extracted
    segment is wrong 31% of the time. Rejected; those sources go in a follow-up issue instead
    (out of scope for this plan).
  - Rewriting `role` text when recovering a company from it (tier 0, Task 3) — one field, one
    guarded write; role stays as scraped.

## Task 1: One placeholder-company vocabulary

Both the sentinel-repair fix (Task 2) and the rename pass (Task 8) need "is this company field
an honest non-answer (blank, or one of `Unknown`/`Confidential`/`N/A`/...)". Building it twice
is exactly the drift failure `triage/resolve.py:101-110`'s own comment already describes for
`_NON_ANSWERS`. Build it once, here.

**Files:**
- `sluice/core/leads.py` — add near the existing cross-sub-app vocabulary (e.g. beside
  `SAME`/`DIFFERENT`/`UNKNOWN`/`UNTRUSTED_SCRAPED_CONTENT_WARNING`). This module is already
  imported by `core/vault.py`, `triage/engine.py`, and `triage/resolve.py`, and imports only
  `core.status` itself — no import cycle.
- `sluice/triage/resolve.py` — `_NON_ANSWERS` (currently around line 111-116) becomes an alias
  of the new constant; `_is_non_answer` (currently around line 332) delegates to the new
  predicate.

**Exact API to add to `core/leads.py`:**

```python
NON_ANSWER_COMPANIES = frozenset({
    # move this set VERBATIM from triage/resolve.py's current _NON_ANSWERS — do not
    # retype it by hand, copy the exact 16 values and their explanatory comment.
})

def fold_company_answer(value: str) -> str:
    """Normalise a company-field candidate the same way for every reader that needs to
    recognise a placeholder, so the write guard, the resolution gate, and the rename pass
    cannot drift on what counts as blank."""
    return (value or "").rstrip(".!").strip().casefold()


def is_placeholder_company(value: str) -> bool:
    """True when `value` names no real employer: blank, or a folded match against
    NON_ANSWER_COMPANIES (the board's own honest non-answers: "Unknown", "Confidential",
    "N/A", ...)."""
    stripped = (value or "").strip()
    if not stripped:
        return True
    return fold_company_answer(value) in NON_ANSWER_COMPANIES
```

In `sluice/triage/resolve.py`: replace the `_NON_ANSWERS` frozenset literal with
`from sluice.core.leads import NON_ANSWER_COMPANIES as _NON_ANSWERS` (or equivalent import at
the top of the file, matching the file's existing import style), and change `_is_non_answer`'s
body to delegate to `is_placeholder_company`/`fold_company_answer` from `core.leads`, keeping
the function's own name and signature exactly as-is.

**Constraint on this task specifically:** `tests/test_triage_resolve.py` (the existing suite)
must pass completely **unedited** after this change — that is the proof the move changed no
behaviour, not merely that it compiles. Do not touch that test file in this task.

**New tests:** `tests/test_leads_company.py` — parametrized: every one of the 16
`NON_ANSWER_COMPANIES` values is a placeholder in multiple casings/punctuation forms (e.g.
`"UNKNOWN"`, `"unknown."`, `" Unknown "`); blank and whitespace-only are placeholders; a real
company name (e.g. `"Example Meridian"`) is not; the set itself contains `"unknown"` and
`"confidential"`.

**Commit:** `refactor(triage): one placeholder-company vocabulary, because two copies drifted`

**Verification:** `.venv/bin/python -m pytest` (full suite green, including
`tests/test_triage_resolve.py` unedited), `.venv/bin/ruff check sluice tests scripts`.

---

## Task 2: The placeholder-company resolution gate and write guard

The load-bearing half of defect 3. Nothing in sluice ever writes `"Unknown"` — those notes are
legacy/foreign data — but sluice's own resolution pass is structurally unable to repair them:
`classify.py` recognises the sentinel, the engine's gate does not, and even a widened gate could
not write because `require_blank` refuses on *presence*, by documented contract.

Depends on Task 1 (`is_placeholder_company`, `NON_ANSWER_COMPANIES` in `core/leads.py`).

**Files and exact changes:**

1. `sluice/triage/classify.py` (currently around line 181-182): change
   `if not company or company.lower() == "unknown":` to
   `if is_placeholder_company(company):` — add the import from `core.leads`.

2. `sluice/triage/engine.py` (the resolution gate, currently around line 125-126): change
   `decision == "needs_review" and not company and note.status in _status.TRIAGE_OWNED` to
   `decision == "needs_review" and is_placeholder_company(company) and note.status in
   _status.TRIAGE_OWNED` (the new predicate subsumes the old `not company`, so there is no `or`
   to add — just replace the middle conjunct).

3. `sluice/core/protocols.py` — extend `update_fields`'s signature (currently around lines
   241-276) with a new keyword-only parameter `blank_values: frozenset | None = None`. Append a
   new paragraph to the docstring's `require_blank` section along these lines (write it in the
   file's existing prose style, not verbatim if a better phrasing fits the surrounding text):
   > `blank_values`, when given alongside `require_blank`, names the stored values that count
   > as BLANK for that guard in addition to empty/whitespace-only. Both sides of the comparison
   > are normalised through `core.leads.fold_company_answer` (strip, drop a trailing `.`/`!`,
   > casefold) — the same delegation `require_status` already makes to `core.status.normalize`.
   > It widens exactly one thing: a value in the given set now counts as blank for the presence
   > check. Every other non-blank value is still refused, including one that merely *differs*
   > from the value being written — never-clobber holds for anything not named here.
   > `blank_values` given without `require_blank` is inert and must never become a guard of its
   > own.

4. `sluice/core/vault.py` — implement the same keyword on `Vault.update_fields` (currently
   around line 1024-1028), and inside the CAS transform (currently around lines 1073-1081)
   change the `require_blank` check from a plain truthiness test to route through a new small
   helper:
   ```python
   def _counts_as_blank(value: str, blank_values: frozenset | None) -> bool:
       if not value.strip():
           return True
       return blank_values is not None and fold_company_answer(value) in blank_values
   ```
   and the guard becomes:
   ```python
   if require_blank is not None and any(
           not _counts_as_blank(_fm_value(inner, key), blank_values)
           for key in require_blank):
       return text
   ```
   This MUST run inside the CAS transform closure against freshly-read bytes (`inner`) — not
   against any snapshot captured before the transform runs. Place `_counts_as_blank` as a
   module-level helper beside the existing `_fm_value`.

5. `sluice/triage/engine.py`, the write call (currently around lines 159-162): add
   `blank_values=NON_ANSWER_COMPANIES` to the existing `vault.update_fields(...)` call.
   Update the adjoining failure-message string (currently around lines 166-170) from "company
   was already set" to "company was already set to a real name" (or similar wording reflecting
   that a placeholder no longer counts as "already set").

**Do not implement Task 3's tier-0 rule in this task** — that is a separate commit. This task
is the gate + write guard only.

**New tests:**
- `tests/conformance/test_store_contract.py` (extend the existing `require_blank` section,
  currently around lines 782-821, for every store the conformance suite runs against): (a)
  `require_blank` + `blank_values` lets a write **replace** a listed placeholder value; (b) the
  guard still **refuses** a value not in `blank_values` (a real company mid-run); (c) the
  comparison is folded (e.g. `blank_values={"unknown"}` and the stored value is `"Unknown."`
  still counts as blank); (d) `blank_values` given without `require_blank` gates nothing.
- `tests/test_classify.py` (new tests): a placeholder company (parametrized over several of
  the 16 values plus casing variants) still classifies `needs_review`; a real company still
  classifies `keep`; a placeholder company that also matches a reject-title still classifies
  `reject` (the placeholder branch must not short-circuit ahead of title/pay rejects).
- `tests/test_triage_engine.py` (new tests, extending the existing `_blank_fields`-style
  fixture helper — do not rename the existing helper, add a sibling that seeds
  `company: "Unknown"` instead of `company: ""`): (a) a sentinel-company lead with a source
  whose `company_from_url` or dossier fetch would resolve it now gets resolved and rewritten;
  (b) a human typing a **real** company mid-run over a sentinel still causes the write to
  refuse (`report.failures` names it) — resume the existing test
  `test_company_write_never_overwrites_a_company_a_human_typed_mid_run` unmodified as the
  control case and add the new sentinel-specific race test beside it; (c) a human typing
  `"Unknown"` mid-run does **not** block the resolution's write (this is the deliberate,
  reasoned exception — write a one-sentence comment in the test explaining why: the guard's
  promise is "never replace a human's answer", and a placeholder is not one).

**Commit:** `fix(triage): a placeholder company is resolved like a blank one`

**Verification:** full suite green, ruff clean, and specifically confirm
`test_company_write_never_overwrites_a_company_a_human_typed_mid_run` (the pre-existing test)
still passes unedited.

---

## Task 3: Tier-0 recovery — the trailing "at &lt;Company&gt;" role clause

Depends on Task 1 and Task 2 (needs `is_placeholder_company` and the gate/write-guard changes
already in place so a tier-0 hit can actually be written).

Measured: this rule recovers 4 of 154 sentinel notes on the real vault. Ship it anyway — it's
free once the resolution machinery exists, the issue explicitly asks for it, and it also
matters for notes seen for the first time with a mashed role from defect 1 (it must correctly
abstain there, not misfire — see the abstain table below).

**File:** `sluice/triage/resolve.py`. Add ahead of tier 1 in `resolve_company` (currently around
line 382-468) — i.e. tier 0 is the very first thing that function tries, unconditionally (no
config gate — same reasoning as tier 1 being ungated: it needs no network, no LLM, and should
run on a zero-config install under `--no-llm`).

**Exact regex and helper**, placed beside the existing `_TITLE_PATTERNS` (currently around
lines 28-34) for precedent:

```python
_MAX_ROLE_COMPANY_WORDS = 6
_MAX_COMPANY_CHARS = 80   # reuse whatever constant tier 3 already uses for its own cap; if
                          # tier 3's cap has a different name, reuse THAT name/value instead
                          # of introducing a second one — check resolve.py for it first.

_ROLE_AT_COMPANY = re.compile(
    r"^(?P<role>.*\S)\s+(?i:at)\s+(?P<company>[^,|/@()\[\]]+?)\s*$")


def _looks_like_a_name(candidate: str) -> bool:
    """Abstain when the tail opens lowercase -- this is what tells "...at Example Meridian"
    from "...at scale"/"...at pace"/"...at a fast-growing startup". Deliberately checks
    islower() rather than isupper(), so a non-Latin-script name is not thrown away by this
    check; the cost is a genuinely lowercase brand, which abstains -- the safe direction."""
    return bool(candidate) and not candidate[0].islower()


def _company_from_role(role) -> str | None:
    """Tier 0: recover a trailing "<role> at <Company>" clause from the role text itself, no
    fetch, no LLM. Anchored full-string and abstain-on-near-miss, matching _TITLE_PATTERNS'
    own doctrine: a role that merely CONTAINS " at " without ending in a name-shaped clause
    must abstain, not guess.
    """
    if not isinstance(role, str) or not role.strip():
        return None
    m = _ROLE_AT_COMPANY.match(role.strip())
    if not m:
        return None
    candidate = m.group("company").strip()
    if not candidate or not m.group("role").strip():
        return None
    if not _looks_like_a_name(candidate):
        return None
    if len(candidate) > _MAX_COMPANY_CHARS or len(candidate.split()) > _MAX_ROLE_COMPANY_WORDS:
        return None
    if _is_non_answer(candidate):
        return None
    return candidate
```

Notes an implementer must get right:
- The role group `(?P<role>.*\S)` is **greedy**, which is deliberate: it makes the match use
  the **last** occurrence of `" at "` in the string (a role like "Engineer at Scale at Example
  Meridian" must resolve to `"Example Meridian"`, not `"Scale at Example Meridian"`). Do not
  change this to a lazy/non-greedy group — that is a bug, not a simplification.
- `[^,|/@()\[\]]+?` excludes separator characters from the company segment, so "Engineer at
  Example Meridian, London" abstains rather than writing a location into `company`.
- `_is_non_answer` (from Task 1's delegation) must be checked **inside** `_company_from_role`,
  not only at the call site — so "Engineer at Confidential" can never write a deny-listed
  value regardless of where this helper is called from in the future.

**The call site**, at the top of `resolve_company`, before tier 1's block, mirroring tier 3's
composition pattern (check `resolve.py` for how tier 3 applies `_is_board_name` and
`frontmatter_safe` around its own candidate — reuse the exact same two calls here):

```python
role_hit = _company_from_role(fm.get("role"))
if role_hit and not _is_board_name(role_hit, fm):
    hit = frontmatter_safe(role_hit)
    if hit:
        return Resolution(hit, "tier0")
```

(`Resolution` — use whatever the existing return type/shape of `resolve_company`'s other tiers
is; match it exactly, do not introduce a new shape.)

**Also required:**
- `sluice/triage/engine.py`: the `report.resolved` default dict (currently seeded with keys
  like `"tier1": 0, "tier2": 0, "tier3": 0` — check the exact current shape) gains a `"tier0"`
  key, pre-seeded to 0 (not created lazily on first hit — the honest-counting discipline the
  surrounding code already follows).
- **Never renumber tiers 1/2/3.** The audit log (`_resolve_audit`, called from `engine.py`)
  persists the literal tier strings; only add `"tier0"`, never shift the others.
- Update `resolve.py`'s own module docstring, which currently describes "tier 1 ... tier 2 ...
  tier 3" — add tier 0 at the top of that description.

**New tests**, in `tests/test_triage_resolve.py` (new section, do not touch the existing
`_is_non_answer` tests from Task 1's untouched-suite constraint):
- Positive: `"Head of Platform Engineering at Example Meridian"` → `"Example Meridian"`.
- Greedy-group regression: `"Engineer at Scale at Example Meridian"` → `"Example Meridian"`
  (not `"Scale at Example Meridian"`).
- Abstain table (each a separate parametrized case): lowercase-opening tail (`"...at scale"`);
  comma in the tail; a `|` in the tail; no `" at "` at all; nothing before `" at "`; the
  defect-1 concatenation shape (a mashed role with no `" at "` anywhere) — confirm it falls
  through to tier 1 untouched, not a false hit.
- Boundary: a tail past `_MAX_ROLE_COMPANY_WORDS` or `_MAX_COMPANY_CHARS` abstains.
- `"Engineer at Confidential"` abstains (deny-list check fires inside the helper).
- A role from a board source (e.g. `source: linkedin`) whose tail happens to read the board's
  own name abstains via `_is_board_name`.
- A role containing an embedded quote character abstains via `frontmatter_safe`.
- Non-string/missing `role` abstains without raising.
- Tier 0 wins ahead of a tier-1 hit that would answer differently (order test).
- Tier 0 fires with `no_llm=True`, `company_resolve_fetch=False`, and no source adapter
  available — i.e. on a fully zero-config install.

Also in `tests/test_triage_engine.py`: an end-to-end test with both `company_resolve_fetch` and
`company_resolve_llm` off and `--no-llm` set, resolving a sentinel-company note via tier 0 alone
(`report.resolved["tier0"] == 1`, no backend calls made), and the 5 (or however many currently
exist) `report.resolved == {...}` equality assertions elsewhere in that file updated to include
the new `"tier0"` key.

**Commit:** `feat(triage): recover the employer from a trailing "at &lt;Company&gt;" role clause`

**Verification:** full suite green, ruff clean.

---

## Task 4: Placeholder company can't corroborate a receipt (small, optional-but-recommended)

Depends on Task 1. `sluice/track/receipt.py`'s corroborated tier (currently around lines
233-240) tokenises `lead.fm.get("company")` and checks `if company and company <= tokens:`.
`"Unknown"` tokenises to `{"unknown"}`, so an ATS-relay email whose body happens to contain the
word "unknown" could produce a false corroboration **proposal** (this is the `corrob` bucket,
which becomes a dead-letter row a human confirms — it is NOT an auto-advance path; only the
`proof` tier auto-advances, and it never reads `company`). Real but modest severity; cheap to
close, and this repo's own standing rule is to address items found along the way rather than
deferring them.

**File:** `sluice/track/receipt.py`. Guard the corroborated-tier company check with
`is_placeholder_company` from `core.leads`:
```python
company = _norm_tokens(lead.fm.get("company") or "")
if company and not is_placeholder_company(lead.fm.get("company") or "") and company <= tokens:
    corrob.append(lead.slug)
```
(Match the exact surrounding variable names and control flow already in the file — this is
describing the semantic change, not a literal diff; adapt to the file's real current shape.)

**New test:** in `tests/test_track_receipt.py`, a lead with `company: "Unknown"` and an ATS
email whose body contains the literal word "unknown" does **not** land in the corroborated
bucket.

**Commit:** `fix(track): a placeholder company cannot corroborate a receipt`

**Verification:** full suite green, ruff clean.

---

## Task 5: naukrigulf recovers the mashed company from the listing-URL seam

Independent of Tasks 1-4 (different files). The golden fixture's URL **paths** are currently
flattened to `https://example.com/jobs/N` by neutrality sanitization, destroying the
`-jobs-in-` seam this fix keys on — restoring path *shape* (not identity) under the synthetic
host is part of this task, not a separate cleanup.

**Files:** `sluice/ingest/sources/naukrigulf.py`, `tests/fixtures/naukrigulf/raw.json`, new
`tests/test_naukrigulf_split.py`.

**Design (from the approved plan, implement exactly):**

New `_NaukrigulfSource(BrowserListSource)` overriding **only** `parse` — rewrite rows before
delegating to `super().parse`, so `_row_to_lead`/`_demash_company`/the existing title-non-empty
filter in the base class all still run over the recovered fields:

```python
def _slug(text: str) -> str:
    """Mirrors Lead.slug's character class (core/leads.py) closely enough for URL comparison."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _split_mashed_title(title: str, url: str) -> tuple[str, str] | None:
    """(role, company) recovered from a title where the board mashed them together with no
    separator, proven by the listing URL's own "...-jobs-in-<city>-in-<company>-..." seam.
    None means abstain -- the title is left exactly as scraped."""
    path = urlparse(url or "").path
    candidates = [path[:m.start()].lstrip("/") for m in re.finditer(r"-jobs-in-", path)]
    if not candidates:
        return None
    best = None
    for i in range(1, len(title)):
        if _slug(title[:i]) not in candidates:
            continue
        if title[i - 1].isspace():        # the mashing signature: no separating space
            continue
        if not title[i].isupper():        # company opens a fresh capitalised token
            continue
        best = i
    if best is None:
        return None
    role, company = title[:best].strip(), title[best:].strip()
    if not role or not company:
        return None
    return role, company


def _recover(row: dict) -> dict:
    if (row.get("company") or "").strip():
        return row   # never touch a populated field
    split = _split_mashed_title(row.get("title") or "", row.get("link") or row.get("url") or "")
    if split is None:
        if re.search(r"[a-z][A-Z]", row.get("title") or ""):
            _log.warning(
                "naukrigulf: title %r looks mashed but the URL seam did not prove a split; "
                "lead kept as-is", row.get("title"))
        return row
    role, company = split
    return {**row, "title": role, "company": company}


class _NaukrigulfSource(BrowserListSource):
    def parse(self, raw, search):
        raw = raw if isinstance(raw, dict) else {}
        rows = [_recover(r) if isinstance(r, dict) else r for r in (raw.get("result") or [])]
        return super().parse({**raw, "result": rows}, search)
```

(`_log` = `get_logger(__name__)` or whatever this module's existing logging convention is —
check the file for the pattern other sources use, if any; if none exists yet, use the same
`core.logging`-style helper `triage/resolve.py` or similar already uses elsewhere in the repo.)

Change the module's `register(BrowserListSource(...))` call to
`register(_NaukrigulfSource(...))`, keeping every other constructor argument identical.

**Abstain conditions, each must be independently testable:** company already populated (never
touched, checked first); no `-jobs-in-` seam anywhere in the URL path; no title prefix's slug
matches any seam-derived candidate; the matched prefix is the *entire* title (a clean row, not
a mash — nothing to abstain from since there's no split to make, but the function must not
treat this as an error); the mashing-signature boundary check fails (whitespace immediately
before the cut, or the character immediately after is not uppercase).

**Fixture edit** (`tests/fixtures/naukrigulf/raw.json`): give every row (mashed and already-clean
alike) a link URL of the shape
`https://example.com/<slug(role)>-jobs-in-<slug(city)>-in-<slug(company)>-<n>` derived from each
row's own correct split (for already-clean rows, this also pins "abstain-on-populated-company"
at the fixture level, since the seam is present but the row shouldn't be touched).

**Correction, checked against the real fixture before dispatch:** all 17 currently-mashed rows in
this fixture (rows 8, 9, 11-13, 15, 17-25 as of this writing — re-verify against the file, don't
trust this list if the fixture has since changed) split cleanly under the algorithm above; none
of them models the "unusual separator" abstain case the production-vault measurement found (4 of
76 real notes). Do not designate any existing row as a deliberate abstain case — there isn't one
to designate. Instead, **add one new row** to the fixture (append at the end, don't renumber or
touch the existing rows) whose title mashes company onto role using one of the unusual boundary
shapes actually observed in production (e.g. an underscore, a slash, or an en dash immediately
before the company name instead of a bare uppercase letter — something that fails the "boundary
opens with an uppercase letter directly abutting the role" check), on a URL that DOES carry a
`-jobs-in-` seam matching the role half only. This is the deliberate honest-residual case: it
must parse through with the title left mashed and `company` still empty, and the golden test
must assert that outcome explicitly (not merely fail to break). Pick title/company text that is
clearly synthetic (an `Example <Word>`-style name), consistent with every other fixture entry.

**Tests** (`tests/test_naukrigulf_split.py`, in `tests/test_demash.py`'s style — positive cases,
mirror-harm/no-op cases, abstain table, never-empties-either-field): unit tests over
`_split_mashed_title` covering a single-word role, a multi-word role, a punctuation-heavy role,
a parenthesised role, and a role containing the literal words "jobs in" (proving the
longest-prefix-across-all-seam-occurrences logic, not just the first occurrence); mirror-harm
cases (clean title/empty company → `None`; a role ending in a plural like "Bankers" against a
seam role slug "banker" → `None`, the mid-word case; whitespace before the seam → `None`;
lowercase-opening remainder → `None`); parse-level tests on `_NaukrigulfSource.parse` (rewrites
a recoverable row, passes an unrecoverable row through unchanged, tolerates
`{"result": []}` and non-dict raw input, never mutates the input row dict); a fixture-level test
parsing the whole golden fixture and asserting exact `(title, company)` on a handful of
previously-mashed rows plus the deliberately-mashed row 25, and that 25 leads still come out
total.

**No `tests/test_fixture_name_neutrality.py` roster changes** — that sweep reads `tests/**/*.py`
source, not `raw.json` fixture data, and this task only edits `raw.json`'s `link` field (no new
identity strings). Confirm by running the full suite, don't assume.

**Constraint:** `tests/test_parsers.py::test_source_is_well_formed` and
`::test_parser_yields_valid_leads`, and every test in `tests/test_demash.py`, must pass
unmodified.

**Commit:** `fix(ingest): naukrigulf recovers the mashed company from the listing-URL seam`

**Verification:** full suite green, ruff clean.

---

## Task 6: wellfound drops company-profile cards from parse

Independent of Tasks 1-5 (different files, though same fixture-neutrality caveat as Task 5).

**Files:** `sluice/ingest/sources/wellfound.py`, `tests/fixtures/wellfound/raw.json`,
`tests/test_parsers.py` (extend the existing wellfound section).

**Design:** override `parse` to drop rows whose `link` is a bare company-profile URL — these
have no role at all (the title fell back to the company's own name), so they are not leads.
`company_from_url` (the existing triage-time hook) is untouched by this task.

```python
_COMPANY_CARD_PATH_RE = re.compile(r"^/company/[a-z0-9-]+$")


def _is_company_card(url: str) -> bool:
    """Bare /company/<slug> with no trailing path -- the MEASURED company-card shape this
    module's own docstring already records (vs. real job cards' /jobs/<id>-<title-slug>).
    Path-only and deliberately host-blind: parse only ever sees rows THIS source's own
    extractor collected, so re-checking the host adds nothing, and host-anchoring would make
    the sanitized golden fixture (which lives on example.com) unable to exercise the filter."""
    try:
        return bool(_COMPANY_CARD_PATH_RE.match(urlparse(url or "").path))
    except ValueError:
        return False   # an unparseable URL is not the measured card shape; keep the row


class WellfoundSource(BrowserListSource):
    def parse(self, raw, search):
        raw = raw if isinstance(raw, dict) else {}
        rows = [r for r in (raw.get("result") or [])
                if not (isinstance(r, dict) and _is_company_card(r.get("link") or ""))]
        return super().parse({**raw, "result": rows}, search)
    # company_from_url stays exactly as it is
```

(`WellfoundSource` already exists as a class in this file — add the `parse` override to it,
don't create a second class. Keep every other existing method/attribute untouched.)

The asymmetry to state in a comment: a wrong *keep* costs one junk lead a human can dismiss; a
wrong *drop* silently bins a real job — so the regex only matches the exact measured shape
(end-anchored slug, no trailing path), and anything not byte-shaped like that capture is kept.

**Fixture edit** (`tests/fixtures/wellfound/raw.json`): the rows whose titles are company names
rather than roles (the plan identifies 8 of the current 15 rows this way) get links rewritten to
`https://example.com/company/<slug-of-title>`. This leaves 7 genuine job rows —
`test_parsers.py`'s non-empty-leads assertion must still hold; confirm by running the test, not
by counting manually.

**New tests**, beside the existing `company_from_url` suite in `tests/test_parsers.py`:
`_is_company_card` positive on a bare `/company/<slug>` with and without a query string; mirror
cases that must stay `False` (`/jobs/<id>-<slug>`, `/company/<slug>/jobs/...`, a trailing-path
variant, an empty URL, a garbage/unparseable URL); a parse-level test over a two-row raw payload
confirming the filter removes only the company-card row; a fixture-level test asserting exactly
7 leads come out and none carries a bare company-profile link.

**Constraint:** the existing `company_from_url` monkeypatch test suite in `test_parsers.py`
must pass unmodified.

**Commit:** `fix(ingest): wellfound drops company-profile cards from parse`

**Verification:** full suite green, ruff clean.

---

## Task 7: Extractor JS belt-and-braces (naukrigulf + wellfound)

Depends on Tasks 5 and 6 (touches the same two source files). This commit is explicitly
**unverifiable offline** — it improves the DOM extraction itself, which can only be validated
against the live sites. The parse-side fixes from Tasks 5-6 are the durable defence regardless
of whether this task's JS change is ever validated live.

**Files:** `sluice/ingest/sources/naukrigulf.py`, `sluice/ingest/sources/wellfound.py` — the
`extractor_js`/`_JS` string constants only. No Python logic changes, no test changes (there is
nothing to test offline).

**naukrigulf:** tighten the extractor so the title node's descendant org text (when present) is
excluded before reading `.textContent`, and the org is read from inside the anchor when the
current `a.info-org` selector finds nothing at card level. Something in the shape of:
```js
const cl=a.cloneNode(true);
cl.querySelectorAll('.info-org,[class*="org"]').forEach(e=>e.remove());
const t=cl.textContent.trim();
const co=(c.querySelector('a.info-org')||a.querySelector('.info-org,[class*="org"]'))
    ?.textContent?.trim()||'';
```
Adapt to fit the surrounding extractor's existing variable names and structure exactly — do not
introduce new variable names that don't match the file's current style.

**wellfound:** drop `a[href*="/company/"]` from the row-selection selector (real job cards link
`/jobs/<id>-<slug>`, per the module's own docstring). Keep Task 6's parse-side filter regardless
— this is defence in depth against DOM drift, not a replacement.

**Commit body** must state explicitly: this change is unverified offline; validate via
`job-sluice ingest test-source naukrigulf --raw` and `job-sluice ingest test-source wellfound
--raw` against the live sites before relying on it; wellfound's raw row count is expected to
drop (roughly 15→7 in the fixture's proportions), which may register as a one-time `drop` in
`detect_drift` the first time it runs live.

**Commit:** `fix(ingest): extractor JS reads company from the DOM shapes that mashed it`

**Verification:** full suite green (no test changes expected, so this is confirming nothing
broke), ruff clean. No live validation is possible or expected from this implementer — say so
in the report rather than attempting it.

---

## Task 8: `Vault.reconcile_names` — the filename-to-frontmatter rename pass

Depends on Task 1 (`is_placeholder_company`). Conceptually follows Tasks 2-3 (the backfill this
pass reconciles against), but has no hard code dependency on them beyond Task 1.

**File:** `sluice/core/vault.py`.

**1. `Vault._frontmatter_name(note)`** — place immediately after `_candidate_names` (the two
belong together as the only readers of the name-minting machinery):

```python
def _frontmatter_name(self, note) -> tuple[str | None, str | None]:
    """(the name this note's FRONTMATTER would mint, the placeholder the CURRENT name was
    minted from). (None, None) means the current name is not one THIS STORE minted from a
    placeholder company -- leave the note alone entirely. (None, head) means it is, but the
    frontmatter still offers nothing better than the same placeholder.

    The qualification is an exact RE-DERIVATION, never a " - " prefix heuristic: the current
    stem must be byte-identical to one of _candidate_names' own outputs when called with the
    PLACEHOLDER head. That is what makes a human-renamed note invisible to this pass by
    construction, and makes a company that merely CONTAINS " - " impossible to mis-split.

    _candidate_names itself is never touched here and never learns about frontmatter -- it
    keeps deriving names from the scraped Lead, which is what protects every legacy
    archived_from_note stamp in _merged/ (see _archived_match). Re-deriving names from
    frontmatter was tried and abandoned there for exactly this reason (see _archived_match's
    docstring) -- and the FAILURE DIRECTION differs on purpose: there, a failed re-derivation
    resurrects a merged-away lead (fail-open, on the one arm that must never fail open); here,
    a failed re-derivation just leaves the note unrenamed (fail-closed, to the status quo).
    """
    stem = note.slug
    head, sep, _ = stem.partition(_SEP)
    if not sep or not is_placeholder_company(head):
        return None, None
    role = note.fm.get("role", "")
    location = note.fm.get("location", "")
    if not role:
        return None, None
    minted, _capped = self._candidate_names(head, role, location)
    if stem not in minted:
        return None, None
    company = note.fm.get("company", "")
    if is_placeholder_company(company):
        return None, head
    fresh, _capped = self._candidate_names(company, role, location)
    return fresh[0], head
```

(`_SEP` is the existing module-level note-name separator constant — reuse it, don't redefine
it. Import `is_placeholder_company` from `core.leads`.)

**2. `Vault.reconcile_names(self, *, apply: bool = False) -> dict`** — place after
`reconcile_layout` (mirrors its shape end to end: report by default, `apply=True` performs the
moves, same report-then-summary structure). Key design points, all must be implemented exactly:

- **No `lead_layout` gate.** Unlike `reconcile_layout`, this pass is independent of whether
  layout is configured — folder and basename are orthogonal axes. Do not add a
  `if not self.lead_layout: return ...` guard.
- **No `_managed_dirs()` gate.** A user-filed note (one sitting outside sluice's managed
  folders) keeps its folder; only its basename changes. Do not restrict the scan to managed
  directories.
- **Enumeration:** `notes = self.read_leads()` (this already prunes `_merged/` — confirm by
  reading `read_leads`'s existing behaviour, do not re-implement the prune).
- **For each note:** call `_frontmatter_name(note)`. `(None, None)` → skip entirely (not
  counted in any bucket except the overall `examined` count). `(None, head)` → append to
  `unresolved`. A real target → proceed to move planning.
- **Target is always candidate 1** (`fresh[0]` from `_frontmatter_name`'s own computation),
  even when the note's *current* stale name was seated at a location-suffixed or digest-suffixed
  candidate. This matters: if the rename seated the note at anything other than candidate 1,
  the next scrape would probe candidate 1 first, find nothing, and mint a duplicate.
- **Collision handling, three layers, in this order:**
  1. **Vault-wide precheck:** `self._locate(target)` (the store's own existing lookup
     primitive) returns non-empty → this note's rename goes into `collisions`, nothing moves.
     This is the layer `_reserve_and_move`'s directory-scoped `O_EXCL` cannot provide by
     itself, and it's what catches the cross-folder duplicate-pair case the issue describes.
  2. **Within-run precheck:** if two different stale notes in this same sweep would both mint
     the same target, refuse **both** (do not pick one arbitrarily) — put both in `collisions`.
  3. **`_reserve_and_move(src, dest_dir, f"{target}.md", suffix_on_collision=False)`** as the
     last word — reuse this existing primitive exactly as `reconcile_layout` does, with the
     same `suffix_on_collision=False` policy (a collision here almost always means a duplicate
     pair the operator should merge with `leads dedupe`, not something to auto-suffix around).
  4. Per-note `OSError`/`FileExistsError` isolation exactly matching `reconcile_layout`'s
     existing pattern (one note's failure never aborts the sweep).
- **No special case for `role == company` notes** (the wellfound company-profile-card
  population from Task 6). Rename them the same as any other qualifying note — a name this pass
  wouldn't mint is a name the candidate walk can never find again, which is the exact harm this
  pass exists to fix.
- **Symlink guard, repositioned from `reconcile_layout`'s (which guards the *destination
  directory* — unreachable here since source dir == dest dir).** The reachable hazard here is a
  symlinked **note itself** (a rename via `os.replace` would move the link, not the target
  file). Check `os.path.islink(note.ref)` and route into `skipped` with that reason, before
  attempting any move.
- **Post-sweep race probe — different from `reconcile_layout`'s, and this is the part most
  likely to get copied wrong.** A raced *reconcile* move re-creates the source at the *same*
  basename (same slug → caught by `index_by_slug`'s existing `ambiguous` bucket). A raced
  *rename* re-creates the source at the **old** (different) basename — a different slug,
  invisible to `index_by_slug`. So: after applying all renames, for each note that WAS renamed,
  re-check whether its old path still exists (`os.path.exists`); if so, append it to a new
  bucket `resurrected` (not `ambiguous`). Do **not** call `_rescan_dirs()` here — a rename
  creates no new directories, so the scan-set cache cannot go stale the way `reconcile_layout`'s
  move-into-a-new-folder does; explain that in a one-line comment rather than cargo-culting the
  call from `reconcile_layout`.
- **Report vocabulary** — an `EMPTY_RENAME_REPORT` dict, structurally similar to whatever
  `EMPTY_RECONCILE_REPORT` looks like in this codebase (check `core/leads.py` for the exact
  existing shape and match its conventions), with these keys: `examined` (int, total lead notes
  read — the denominator), `renames` (list of `(old_slug, new_slug, folder)` tuples — actual or
  proposed depending on `apply`), `unresolved` (list of `(slug, company)` — stale name,
  frontmatter offers nothing better yet), `collisions` (list — target already claimed, by any
  of the three layers above), `ambiguous` (dict `slug -> [refs]`, reusing `index_by_slug`'s
  existing shape for the case where two *different* notes claim one slug going in), `resurrected`
  (list of `(old_slug, new_slug)` — the raced-move case above), `skipped` (list of `(slug,
  reason)` — symlinked note, or an isolated per-note `OSError`).
- **Never write a status, never write any frontmatter.** This is a pure filename operation —
  the moved file's bytes are byte-identical before and after.

**Commit:** `feat(vault): reconcile a lead note's filename to its frontmatter company`

**New tests**, `tests/test_leads_rename.py`, mirroring `tests/test_leads_reconcile.py`'s
existing 17-test shape and pin list (read that file first for the exact fixture helpers
`_seed`/`_v` or equivalent, and reuse them): report-first (nothing moves without `apply=True`);
a blank-company note renames to its frontmatter name; a sentinel-named note (`"Unknown - "`)
renames once frontmatter carries a real company; frontmatter still blank/still-sentinel →
`unresolved`, untouched; a name this store never minted (seed a note whose current stem's
placeholder-head check passes but whose role text differs from what would have minted it) is
invisible — assert the precondition that the head IS recognised as a placeholder, so the test
actually reaches the re-derivation arm rather than passing at a shallower check; a
location-suffixed stale note renames to the *bare* candidate 1 (not a location-suffixed target),
and a follow-up `upsert` with the fixed company resolves in place afterward (not a duplicate);
idempotence — running the pass twice renames nothing the second time; a target already claimed
by a note in a *different* folder is refused via the vault-wide precheck (the case
`_reserve_and_move` alone cannot catch — write this test so it would FAIL if the vault-wide
precheck were removed and only `_reserve_and_move` ran); same-folder collision refuses and is
never suffixed; two stale notes racing to mint one target both refuse; an archived (`_merged/`)
loser is never a rename source; a symlinked note is refused into `skipped`, not renamed; a raced
rename (monkeypatch `_reserve_and_move` to re-create the source path after the move, matching
`tests/test_leads_reconcile.py`'s existing race-test fixture shape) produces `resurrected`, and
explicitly assert it is **not** also in `ambiguous` (the two passes' residuals differ on
purpose — a "simplification" that merges them back to one probe would silently break this); the
moved note's bytes are identical before and after (never writes a status); a user-filed note
(outside any managed folder) still renames in place and stays in its own folder; the pass still
renames notes even with no `lead_layout` configured (the deliberate divergence from
`reconcile_layout`); a plain non-lead `.md` file is never touched; a renamed note is found in
place by the next `upsert` call carrying the now-correct company (assert the outcome is
`updated`/`merged`, not `created`); a note renamed while active and later merged via
`merge_cluster` gets its `archived_from_note` stamp written against the **new** (post-rename)
name, and a fresh `Vault` instance's `upsert` against that same lead afterward returns
`merged_away`, not a fresh `created`; a lead whose *source* still scrapes a blank company (i.e.
defect 1 unfixed for that source) resolves to the *old* stale name on the next scrape, proving
`_candidate_names` was never taught to read frontmatter.

**Verification:** full suite green, ruff clean.

---

## Task 9: `DeadLetterDb.rename_lead` — migrate dead-letter rows on rename

Depends on Task 8 conceptually (this is what Task 10 wires together), but is independently
implementable and testable against `track/deadletter.py` alone.

**File:** `sluice/track/deadletter.py` — this module is the sole owner of the dead-letter
table's SQL; no SQL for this table lives anywhere else, and this task must not change that.

Add, after the existing `clear_lead` method:

```python
def rename_lead(self, old_slug: str, new_slug: str) -> int:
    """Re-file every open row from `old_slug` to `new_slug`. Returns the number of rows moved.

    Unlike clear_lead's status-only split, this migrates EVERY row kind -- a rename changes
    the lead's IDENTITY, it does not resolve anything a dead-letter row is proposing. A
    `calendar` row saying "remove this from your calendar by hand" is still just as much this
    lead's row after the rename as before it, and dropping it here would be the #49 silent-loss
    class arriving through a new door.

    A MISSING store is a no-op returning 0 and creates no file (mirrors clear_lead's rule for
    a missing store). A corrupt/unreadable/unwritable store RAISES (fail-loud, matching every
    other writer of this table)."""
```

Match the file's existing conventions exactly for: how it opens the connection (the same
open-vs-missing-vs-corrupt handling `clear_lead` already uses), how it commits, what exception
type a corrupt store raises (reuse the existing one, don't invent a new exception class), and
its docstring style/length relative to neighboring methods.

**New tests**, extending `tests/test_track_deadletter.py`: `rename_lead` moves every row kind
(not just the status-proposal subset `clear_lead` targets) — seed rows of at least two different
kinds under one slug, rename, assert all moved; on a missing store, `rename_lead` is a no-op
returning 0 and creates no database file; on a corrupt/unreadable store, it raises (assert the
same exception type `clear_lead` raises in the equivalent case, for consistency, not a new one).

**Commit:** `feat(track): re-file dead-letter rows when a lead's slug changes`

**Verification:** full suite green, ruff clean.

---

## Task 10: `job-sluice leads rename` — CLI, facade, and wiring

Depends on Task 8 (`Vault.reconcile_names`) and Task 9 (`DeadLetterDb.rename_lead`).

**Files:** `sluice/core/app.py`, `sluice/cli.py`, `docs/USAGE.md`.

**1. `core/app.py`:**
- A new exception `StoreCannotRename(RuntimeError)`, mirroring the existing
  `StoreHasNoLayout` pattern exactly (same module, same style).
- `Sluice._naming_store(self)` — mirrors `_layout_store()`: `getattr(store,
  "reconcile_names", None)`, raising `StoreCannotRename` naming the store if absent.
- `Sluice.rename_report(self) -> dict` — the read-only facade path (mirrors
  `reconcile_report()`'s existing shape: call `_naming_store()`, call `reconcile_names(apply=False)`
  on it, and additionally compute a **best-effort** dead-letter preview: for stores that expose
  the dead-letter mechanism, count open rows whose `lead` matches any of the would-rename old
  slugs into `report["deadletter"]["pending"]`. This computation must **never** fail the whole
  report — wrap it so any exception becomes `report["deadletter"]["error"] = str(...)` instead
  of propagating, since a read command must not fail over a store it isn't writing.
- `Sluice.rename(self, apply: bool = False) -> dict` — the write path. When `apply=True`:
  1. Load track config the same way the two existing dead-letter *writers* in this codebase do,
     with the relocated-store refusal flag on (`refuse_relocated_seen_db=True` or whichever
     exact keyword those call sites use — grep for them and match exactly).
  2. Call `check_reachable()` on the dead-letter store **before** calling
     `reconcile_names(apply=True)`. If it raises, refuse the whole operation with a clear
     message and rename **nothing** — do not let some notes rename while dead-letter migration
     is known to be unreachable.
  3. Call `reconcile_names(apply=True)` on the naming store.
  4. For each entry in the resulting report's `renames` list, call
     `deadletter_db.rename_lead(old_slug, new_slug)`. Isolate any per-pair failure into
     `report["deadletter"]["failed"]` (list) rather than aborting the loop — vault state (the
     rename that already happened) is the more important of the two to preserve; do **not**
     roll back a rename because its dead-letter migration failed.
  5. Track total rows migrated in `report["deadletter"]["refiled"]`.

**2. `cli.py`:**
- `cmd_leads_rename(args, config) -> int` mirroring `cmd_leads_reconcile`'s exact structure
  (report to stdout, summary line to stderr, `--json` support, exit-code rule).
- A new `leads rename` subparser, in the same file/location as the other `leads` subcommands
  (`dedupe`/`expire`/`reconcile`), with:
  - `--apply` (store_true) — no `--dry-run` flag (the default IS the dry run; confirm by
    checking `tests/test_leads_reconcile_cli.py`'s existing "no dry-run" test and writing the
    exact sibling test for `rename`).
  - `--json` (store_true).
  - Help/description text that carries **both** of these, near-verbatim:
    1. The same "do NOT run --apply concurrently with a pipeline command" warning
       `leads reconcile`'s existing help text carries (copy its wording, adapted to say
       "resurrected" instead of "ambiguous" as the reported bucket for a race).
    2. A new caveat specific to this command: a renamed note is only found by the next scrape
       once that scrape *also* carries the correct company — for a source not yet fixed at
       ingest, that guarantee lives entirely in `seen.db` (which keys on the listing URL, not
       the filename), and it disappears the moment `seen.db` is rebuilt or relocated.
  - Exit code: non-zero (only under `--apply`) when `collisions`, `ambiguous`, `resurrected`,
    `skipped`, or `deadletter["failed"]` is non-empty — matching `leads reconcile`'s existing
    exit-code rule exactly, with the wider bucket list. `unresolved` does **not** count toward
    exit code (it's a state this pass is designed to leave alone, same treatment as
    `reconcile`'s `unknown`/`user_filed`).
  - Human report (stdout): prints `renames`, `collisions`, `ambiguous`, `resurrected`,
    `skipped`. `unresolved` appears **only as a count** on the summary line (stderr), not
    listed item-by-item — it's typically hundreds of notes an operator can't act on yet, and
    listing them would bury the ones they can. Note this deliberately in a comment so it isn't
    "fixed" into printing the full list later.

**3. `docs/USAGE.md`:** add a `### job-sluice leads rename [--apply] [--json]` section in the
same style/location as the existing `leads reconcile` section — this is **required**, not
optional: `tests/test_docs_claims.py` walks the real CLI parser and fails the build if a real
command exists undocumented.

**New tests**, `tests/test_leads_rename_cli.py`, mirroring `tests/test_leads_reconcile_cli.py`'s
suite: facade report changes nothing; facade apply performs renames; a store lacking
`reconcile_names` raises `StoreCannotRename` → CLI exit 2, not a traceback; the subparser
registers `--apply` and has **no** `--dry-run` (assert `SystemExit` on passing `--dry-run`); the
`--json` report carries every key from the report shape; human report to stdout, summary to
stderr (assert via captured streams); `unresolved` is counted, not listed, in the human report
(a test that would fail if someone "fixed" this into full listing); exit-code parametrized over
each bucket that should cause non-zero, and a clean sweep exits zero; the help text (fetched
through the real argparse parser, not hand-typed) contains both the concurrency warning and the
seen.db caveat; dead-letter rows are actually refiled end-to-end (seed a dead-letter row under
an old slug, run `rename --apply`, confirm `track confirm --lead "<new slug>"` now clears it);
an unreachable dead-letter store causes the whole `--apply` to refuse with **zero** renames
landed (assert no vault files moved); a report (non-apply) never fails even when the dead-letter
store is corrupt — assert `deadletter["error"]` is set and the rest of the report is still
returned.

**Commit:** `feat(cli): job-sluice leads rename`

**Verification:** full suite green, ruff clean, and specifically run
`.venv/bin/python -m pytest tests/test_docs_claims.py` to confirm the new command's docs
coverage.

---

## Task 11: Docs — the basename axis beside the folder axis

Depends on all prior tasks (describes shipped behaviour).

**Files:** `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`.

- `docs/ARCHITECTURE.md`: add a short paragraph beside the existing `leads reconcile`
  description explaining the basename axis (`leads rename`) is independent of the folder axis
  and the two passes can run in either order; and update the triage company-resolution ladder
  description to mention tier 0 (the trailing "at &lt;Company&gt;" rule) ahead of tier 1.
- `docs/CONFIGURATION.md`: a brief note that tier-0 company resolution runs unconditionally
  (no config knob), matching how tier 1 is already documented.
- Do **not** hand-edit `CHANGELOG.md` — release-please owns it.

**Commit:** `docs: the basename axis beside the folder axis, and the resolution ladder starts at tier 0`

**Verification:** full suite green (docs changes shouldn't affect tests except
`test_docs_claims.py`, already covered in Task 10), ruff clean.

---

## Follow-up issue (file this once all tasks are complete, not a task in this plan)

File a new GitHub issue (title along the lines of: "ingest: ~300 blank-company notes across
cwjobs/weworkremotely/totaljobs/linkedin/eighty_k need live DOM re-capture") documenting the
measured per-source breakdown (cwjobs 125, weworkremotely 104, totaljobs 44, linkedin 35,
eighty_k 40 — eighty_k's link extraction is broken outright, no path at all in the URL), the
explicit finding that a URL-derived company rule for cwjobs/totaljobs was tried and rejected
(31% wrong when validated against known-company notes), and that each source needs
`job-sluice ingest test-source <id> --raw` plus live DOM inspection, one adapter at a time.
Cross-link from #151. This is out of scope for this plan's implementation tasks.

**Widen this issue's scope to also cover the 76 existing naukrigulf notes** already in
production with a blank company, seated before this plan's Task 5 fix shipped. Task 5's parse-
side recovery can never reach them: their URLs are already recorded in `seen.db`, so a re-scrape
of the same posting is deduped at ingest and the new parse code never runs against it. Tier 0
(the trailing "at &lt;Company&gt;" role-text regex) abstains too — a mashed title has no `" at "`
to match — and tier 1 abstains because naukrigulf ships no `company_from_url` hook (recovery there
needs the mashed TITLE, which cannot be expressed as a pure `company_from_url(url)` function of
the URL alone). So these 76 notes stay `needs_review` forever under a default, opt-in-tiers-off
install; only a targeted backfill (or opting into tiers 2/3) resolves them. File this alongside
the other five sources' breakdown, not as a separate issue.
