# Triage company-name resolution (#109) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `classify()`'s blank-company `needs_review` branch a real, deterministic resolution
attempt (URL-pattern extraction, then an optional no-LLM page visit) before a lead is left in the
one-way `needs_review` trap, per the approved design spec
`docs/superpowers/specs/2026-08-10-triage-company-resolution-design.md`.

**Architecture:** `classify()` stays pure and untouched. `triage/engine.py`'s classify-pass loop
gains a resolution step that fires only when `classify()` already returned
`needs_review`-because-blank-company (never ahead of its existing free rejects), writes a
confirmed company back through the same CAS `update_fields` path every other triage write uses,
then re-classifies. Tier 1 is a per-`Source` optional `company_from_url(url) -> str | None`
member (free, no network); tier 2 is a real, no-LLM page visit reusing the existing dossier fetch
closure and cache, gated behind a new `company_resolve_fetch` config knob that defaults **off**.
A hardening fix closes a pre-existing, unrelated race this design's own tier-2 fetch widens: the
very next write on the same code path (`apply_classification`/`apply_verdict`) gains the same
`require_status` guard, and a new `"skipped-race"` outcome stops that guard's abort from still
producing a false persisted audit-log entry.

**Tech Stack:** Python 3.12+ stdlib only (`re`, `json`, `hashlib`); no new dependency.

## Global Constraints

- **No LLM-based company guessing.** Both tiers are deterministic extraction, never inference.
  (Superseded by #120, 2026-08-12: a third, LLM-backed tier was added on top of
  these two, gated behind its own `company_resolve_llm` knob. The constraint above
  accurately describes #109 as shipped; see
  `docs/superpowers/specs/2026-08-12-triage-company-resolution-llm-tier-design.md`
  for what changed and why.)
- **Abstain over guess, everywhere.** A candidate that fails validation, or either tier missing,
  returns `None` — never a fabricated or low-confidence company name.
- **`classify()`'s signature and purity contract are untouched** — no `dossier_cache`, `sources`,
  or `fetcher` parameter, ever. Pinned by a dedicated regression test (Task 7).
- **`company_resolve_fetch` (the tier-2 gate) defaults to `False`.** Tier 1 is unconditional (free,
  no network) and unaffected by this knob.
- **Every new/modified vault write goes through `update_fields` with `require_status`** where the
  write follows any resolution-path I/O — never a caller-side status check.
- **No new runtime dependency.** `sluice/` stays stdlib-only except the already-approved
  exceptions in `CLAUDE.md`.
- **Mutation-witness discipline applies to every production change below**: run
  `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` once before
  mutating, and mutate by moving/deleting, never adding. The spec's mutation-witness table (copied
  into each task below) is the acceptance bar, not merely a suggestion.
- Definition of done for the whole plan: `ruff check sluice tests` and `python -m pytest` both
  clean, run from the repo root.

---

## File Structure

| File | Responsibility |
|---|---|
| `sluice/core/dossier.py` | Modify: `DossierCache.cache_key` prefers a url-hash; `get_or_build` captures `page_title`/`structured_data`; `slim()` excludes both from what reaches the judge. |
| `sluice/core/app.py` | Modify: the dossier fetch closure (`Sluice.dossier_cache`) also captures `document.title` and JSON-LD script content; `Sluice.triage()` threads `sources.get` into `triage.engine.run`. |
| `sluice/ingest/base.py` | Modify: `Source(Protocol)` gains a docstring documenting the optional `company_from_url` member. |
| `sluice/ingest/sources/wellfound.py` | Modify: adds `WellfoundSource.company_from_url`, a tier-1 URL-pattern extractor. |
| `sluice/triage/config.py` | Modify: `TriageConfig.company_resolve_fetch: bool = False`. |
| `sluice/triage/resolve.py` | **Create.** `resolve_company` (the orchestrator), `_from_dossier`, `_hiring_org_from_jsonld`, `_safe`/`_UNSAFE_CHARS`. |
| `sluice/core/vault.py` | Modify: `_set_fm`'s docstring gains a clause pointing raw-content writers at `resolve.py`'s `_safe` precedent. |
| `sluice/triage/apply.py` | Modify: `apply_classification`/`apply_verdict` gain `require_status=` and a new `"skipped-race"` return value. |
| `sluice/triage/engine.py` | Modify: `run()` gains `get_source=None`; the classify-pass loop gains the resolution call site; both loops gain `"skipped-race"` counting/audit-gating. |
| `sluice.yaml.example` | Modify: new commented `triage.company_resolve_fetch` entry. |
| `docs/CONFIGURATION.md` | Modify: new row in the `triage:` table. |
| `docs/ARCHITECTURE.md` | Modify: dossier fetch closure description, five-sub-apps caveat, `dossier.py` one-line description, `sources` seam note. |
| `tests/test_dossier.py` | Modify: `cache_key`/`slim()`/legacy-schema coverage. |
| `tests/test_dossier_guard.py` | Modify: `_Tab` fake gains `title`/`ld_json`; the exact-probe-sequence test updates; new capture tests. |
| `tests/test_parsers.py` | Modify: Wellfound `company_from_url` golden-fixture tests. |
| `tests/test_sluice_neutral_defaults.py` | Modify: `company_resolve_fetch` abstain-default pinning tests. |
| `tests/test_triage_resolve.py` | **Create.** Unit tests for `resolve_company`/`_from_dossier`. |
| `tests/test_apply.py` | Modify: `apply_classification`/`apply_verdict` race tests. |
| `tests/test_triage_engine.py` | Modify: the restructured classify-pass integration tests. |
| `tests/test_classify.py` | Modify: the `classify()` signature regression test. |
| `tests/test_app_operations.py` | Modify: `Sluice.triage()` threads `get_source` into `engine.run`. |

---

## Task 1: `DossierCache` schema — `page_title`/`structured_data`, url-hash `cache_key`, `slim()` exclusion

**Files:**
- Modify: `sluice/core/dossier.py`
- Test: `tests/test_dossier.py`

**Interfaces:**
- Produces: `DossierCache.cache_key(lead: dict) -> str` — now prefers `lead_id`, then a stable
  `"url-" + sha256(url)[:16]` hash, then the existing `_slug` fallback. `get_or_build`'s returned
  dict now always carries `"page_title"` and `"structured_data"` string keys (default `""`).
  `slim(dossier, *, jd_limit=4000)` no longer includes either key in its output.
- Consumed by: Task 2 (the fetch closure populates `page_title`/`structured_data` in the `enrich`
  dict `get_or_build` reads from), Task 5 (`resolve.py`'s `_from_dossier` reads both keys off a
  dossier dict).

This is dependency-order step 1 in the spec: nothing in tier 2 is testable without it, and no
other task depends on Wellfound's capture (step 2), so it can land first or in parallel.

- [ ] **Step 1: Write the failing `cache_key` tests**

Add to `tests/test_dossier.py` (add `import json` at the top alongside the existing
`from datetime import datetime, timedelta` / `from sluice.core.dossier import DossierCache, slim`):

```python
def test_cache_key_prefers_a_stable_url_hash_over_the_company_role_slug():
    dc = DossierCache("/unused", ttl_days=7, fetcher=lambda lead: {})
    before = dc.cache_key({"company": "", "role": "Staff Engineer",
                           "url": "https://x.invalid/y"})
    after = dc.cache_key({"company": "Example Co", "role": "Staff Engineer",
                          "url": "https://x.invalid/y"})
    assert before == after
    assert before.startswith("url-")


def test_cache_key_still_prefers_lead_id_over_url():
    dc = DossierCache("/unused", ttl_days=7, fetcher=lambda lead: {})
    assert dc.cache_key({"lead_id": "abc123", "url": "https://x.invalid/y"}) == "abc123"


def test_cache_key_falls_back_to_slug_with_no_url_or_lead_id():
    dc = DossierCache("/unused", ttl_days=7, fetcher=lambda lead: {})
    assert dc.cache_key({"company": "Example Co", "role": "Staff Engineer"}) == \
        "example-co-staff-engineer"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dossier.py -v -k cache_key`
Expected: FAIL — `test_cache_key_prefers_a_stable_url_hash_over_the_company_role_slug` fails
because `before != after` (today's `cache_key` derives from `_slug`, which changes when `company`
changes); the other two pass already (documenting today's behaviour) and will keep passing.

- [ ] **Step 3: Implement `cache_key`'s url-hash preference**

In `sluice/core/dossier.py`, add `import hashlib` to the top-of-file imports, then replace:

```python
    def cache_key(self, lead: dict) -> str:
        return (lead.get("lead_id") or _slug(lead))
```

with:

```python
    def cache_key(self, lead: dict) -> str:
        """`lead_id` first, then a stable hash of `url` (#109) -- url does not change
        across the classify-pass company mutation this feature performs, so the
        classify-pass resolution fetch and the later enrich-pass judge fetch land on
        the SAME cache entry instead of double-fetching. Falls back to the
        company/role slug only when neither is available (e.g. a #23 Google lead
        with no url)."""
        lead_id = lead.get("lead_id")
        if lead_id:
            return lead_id
        url = lead.get("url")
        if url:
            return "url-" + hashlib.sha256(url.encode()).hexdigest()[:16]
        return _slug(lead)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dossier.py -v -k cache_key`
Expected: PASS, all three.

- [ ] **Step 5: Write the failing `slim()` exclusion test**

Add to `tests/test_dossier.py`:

```python
def test_slim_excludes_page_title_and_structured_data():
    d = {"lead_snapshot": {"a": 1}, "jd": {"markdown": "y"}, "company": "Z",
        "page_title": "Staff Engineer at Example Co | Example Board",
        "structured_data": '{"@type": "JobPosting"}'}
    s = slim(d)
    assert "page_title" not in s
    assert "structured_data" not in s
```

- [ ] **Step 6: Run it to verify it fails**

Run: `python -m pytest tests/test_dossier.py -v -k excludes_page_title`
Expected: FAIL — both keys are currently passed through by `slim()`'s `{k: v for k, v in
dossier.items() if k != "lead_snapshot"}` comprehension.

- [ ] **Step 7: Implement `slim()`'s exclusion**

Replace:

```python
def slim(dossier: dict, *, jd_limit: int = 4000) -> dict:
    out = {k: v for k, v in dossier.items() if k != "lead_snapshot"}
    jd = dict(out.get("jd") or {})
    if "markdown" in jd:
        jd["markdown"] = jd["markdown"][:jd_limit]
    out["jd"] = jd
    return out
```

with:

```python
def slim(dossier: dict, *, jd_limit: int = 4000) -> dict:
    # page_title/structured_data (#109) are resolution-only fields, never judge-relevant --
    # structured_data especially can run several KB on some boards, so excluding it here
    # (not at storage time in get_or_build, which resolve.py's _from_dossier still needs
    # to read directly off the cached dict) is what keeps it out of every judge prompt.
    out = {k: v for k, v in dossier.items()
          if k not in ("lead_snapshot", "page_title", "structured_data")}
    jd = dict(out.get("jd") or {})
    if "markdown" in jd:
        jd["markdown"] = jd["markdown"][:jd_limit]
    out["jd"] = jd
    return out
```

- [ ] **Step 8: Run it to verify it passes**

Run: `python -m pytest tests/test_dossier.py -v -k excludes_page_title`
Expected: PASS.

- [ ] **Step 9: Write the failing schema-addition + legacy-dossier tests**

Add to `tests/test_dossier.py` (needs `import json` at the top of the file):

```python
def test_get_or_build_captures_page_title_and_structured_data(tmp_path):
    def fetcher(lead):
        return {"jd": {"markdown": "x"}, "glassdoor": {},
                "page_title": "Staff Engineer at Example Co | Example Board",
                "structured_data": '{"@type": "JobPosting"}'}
    dc = DossierCache(str(tmp_path), ttl_days=7, fetcher=fetcher,
                      clock=_clock(datetime(2026, 7, 7)))
    d = dc.get_or_build({"company": "", "role": "Staff Engineer", "url": "https://x.invalid/y"})
    assert d["page_title"] == "Staff Engineer at Example Co | Example Board"
    assert d["structured_data"] == '{"@type": "JobPosting"}'


def test_get_or_build_loads_a_legacy_cached_dossier_missing_the_new_fields(tmp_path):
    # A pre-#109 cache entry never wrote page_title/structured_data at all.
    legacy = {"schema_version": 2, "lead_id": "legacy-co-role", "company": "Legacy",
             "position": "Role", "location": "", "role_type": "",
             "lead_snapshot": {}, "jd": {"markdown": ""}, "glassdoor": {},
             "built_at": datetime(2026, 7, 7).isoformat()}
    (tmp_path / "legacy-co-role.json").write_text(json.dumps(legacy))
    dc = DossierCache(str(tmp_path), ttl_days=7,
                      fetcher=lambda lead: {"jd": {}, "glassdoor": {}},
                      clock=_clock(datetime(2026, 7, 8)))
    d = dc.get_or_build({"lead_id": "legacy-co-role"})
    assert d["company"] == "Legacy"
    assert d.get("page_title") is None    # never written; get_or_build must not raise
```

- [ ] **Step 10: Run them to verify the capture test fails**

Run: `python -m pytest tests/test_dossier.py -v -k "captures_page_title or legacy"`
Expected: `test_get_or_build_captures_page_title_and_structured_data` FAILs with a `KeyError` (the
dossier dict has no `"page_title"` key yet); `test_get_or_build_loads_a_legacy_cached_dossier...`
PASSes already (a fresh hit just returns the cached JSON verbatim via `json.loads`, no coercion
happens today).

- [ ] **Step 11: Implement the schema addition in `get_or_build`**

Replace the `dossier = {...}` construction:

```python
        dossier = {
            "schema_version": 2,
            "lead_id": self.cache_key(lead),
            "company": lead.get("company", ""),
            "position": lead.get("role", ""),
            "location": lead.get("location", ""),
            "role_type": lead.get("role_type", ""),
            "lead_snapshot": dict(lead),
            "jd": enrich.get("jd", {}),
            "glassdoor": enrich.get("glassdoor", {}),
            "built_at": self.clock().isoformat(),
        }
```

with:

```python
        dossier = {
            "schema_version": 2,
            "lead_id": self.cache_key(lead),
            "company": lead.get("company", ""),
            "position": lead.get("role", ""),
            "location": lead.get("location", ""),
            "role_type": lead.get("role_type", ""),
            "lead_snapshot": dict(lead),
            "jd": enrich.get("jd", {}),
            "glassdoor": enrich.get("glassdoor", {}),
            # #109 tier-2 company resolution reads these two off a fresh dossier
            # directly; defaulting to "" here (not None) is what lets an OLD cached
            # dossier missing them entirely still parse via a plain .get(...) or "".
            "page_title": enrich.get("page_title", ""),
            "structured_data": enrich.get("structured_data", ""),
            "built_at": self.clock().isoformat(),
        }
```

- [ ] **Step 12: Run the full file to verify everything passes**

Run: `python -m pytest tests/test_dossier.py -v`
Expected: PASS, all tests including the two pre-existing ones (`test_miss_then_hit_and_ttl`,
`test_slim_strips_and_truncates`).

- [ ] **Step 13: Mutation-witness this task's changes**

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` once, then
for each row, apply the mutation with `Edit`, run the named test, confirm it reddens, then `Edit`
again with the arguments reversed to restore:

| Mutant | Must redden |
|---|---|
| Delete `cache_key`'s url-hash preference (fall back to `_slug` unconditionally) | `test_cache_key_prefers_a_stable_url_hash_over_the_company_role_slug` |
| Delete `slim()`'s `page_title`/`structured_data` exclusion | `test_slim_excludes_page_title_and_structured_data` |

- [ ] **Step 14: Commit**

```bash
git add sluice/core/dossier.py tests/test_dossier.py
git commit -m "$(cat <<'EOF'
feat(triage): dossier cache_key prefers a stable url hash, adds page_title/structured_data (#109)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 2: Dossier fetch closure captures `page_title` and JSON-LD `structured_data`

**Files:**
- Modify: `sluice/core/app.py`
- Test: `tests/test_dossier_guard.py`

**Interfaces:**
- Consumes: Task 1's `DossierCache.get_or_build`, which now reads `enrich.get("page_title", "")`
  / `enrich.get("structured_data", "")` off the dict the fetch closure returns.
- Produces: the fetch closure's returned dict now always carries `"page_title"` and
  `"structured_data"` string keys, captured in the SAME already-open tab as the existing JD body
  read, and best-effort (never refuses the whole fetch if either probe comes back unreadable —
  only the JD body read stays a hard refusal).

- [ ] **Step 1: Read the current fetch closure once more for the exact insertion point**

`sluice/core/app.py`'s `dossier_cache` method, inside the `fetch(lead)` closure, after the
existing body-read block:

```python
                    body = c.evaluate(tid, "document.body.innerText")
                    md = body.get("result") if isinstance(body, dict) else None
                    if not isinstance(md, str):
                        # Same reasoning as no-tab: a non-string body used to become
                        # cached empty JD indistinguishable from a real empty one.
                        _refuse(urlguard.BODY_UNREADABLE, pre.host)
                finally:
                    c.close_tab(tid)
            return {"jd": {"markdown": md or ""}, "glassdoor": {}}
```

- [ ] **Step 2: Write the failing capture tests in `tests/test_dossier_guard.py`**

First, extend the `_Tab` fake so it can serve `document.title` and the new JSON-LD probe
distinctly from the JD body it already serves. Replace:

```python
class _Tab:
    """A fake Fetcher recording its exact probe sequence."""

    def __init__(self, landed="https://jobs.invalid/x", body="JD BODY",
                 landed_result=_UNSET):
        self.landed, self.body, self.landed_result = landed, body, landed_result
        self.calls = []

    def create_tab(self, url):
        self.calls.append(("create_tab", url))
        return "tab-1"

    def evaluate(self, tid, js):
        self.calls.append(("evaluate", js))
        if js == "location.href":
            if self.landed_result is not _UNSET:
                return self.landed_result
            return {"result": self.landed}
        return {"result": self.body}
```

with:

```python
class _Tab:
    """A fake Fetcher recording its exact probe sequence."""

    def __init__(self, landed="https://jobs.invalid/x", body="JD BODY",
                 landed_result=_UNSET, title="", ld_json=""):
        self.landed, self.body, self.landed_result = landed, body, landed_result
        self.title, self.ld_json = title, ld_json
        self.calls = []

    def create_tab(self, url):
        self.calls.append(("create_tab", url))
        return "tab-1"

    def evaluate(self, tid, js):
        self.calls.append(("evaluate", js))
        if js == "location.href":
            if self.landed_result is not _UNSET:
                return self.landed_result
            return {"result": self.landed}
        if js == "document.title":
            return {"result": self.title}
        if js == _LD_JSON_JS:
            return {"result": self.ld_json}
        return {"result": self.body}
```

Add the import `from sluice.core.app import _LD_JSON_JS` to the top of `tests/test_dossier_guard.py`,
alongside the existing `from sluice.core.app import Sluice`.

Then update the positive-control exact-sequence assertion. Replace:

```python
def test_an_allowed_url_fetches_and_probes_in_order(tmp_path, role):
    """The positive control every absence assertion below is paired with."""
    tab = _Tab()
    d = _cache(tmp_path, tab).get_or_build({"url": "https://jobs.invalid/x",
                                            "company": "Aye", "role": role})
    assert d["jd"]["markdown"] == "JD BODY"
    assert tab.calls == [
        ("create_tab", "https://jobs.invalid/x"),
        ("evaluate", "location.href"),
        ("evaluate", "document.body.innerText"),
        ("close_tab", "tab-1"),
    ]
```

with:

```python
def test_an_allowed_url_fetches_and_probes_in_order(tmp_path, role):
    """The positive control every absence assertion below is paired with."""
    tab = _Tab()
    d = _cache(tmp_path, tab).get_or_build({"url": "https://jobs.invalid/x",
                                            "company": "Aye", "role": role})
    assert d["jd"]["markdown"] == "JD BODY"
    assert tab.calls == [
        ("create_tab", "https://jobs.invalid/x"),
        ("evaluate", "location.href"),
        ("evaluate", "document.body.innerText"),
        ("evaluate", "document.title"),
        ("evaluate", _LD_JSON_JS),
        ("close_tab", "tab-1"),
    ]
```

Finally, add two new tests, near the positive control:

```python
def test_page_title_and_structured_data_are_captured_into_the_dossier(tmp_path, role):
    tab = _Tab(title="Staff Engineer at Example Co | Example Board",
              ld_json='{"@type": "JobPosting"}')
    d = _cache(tmp_path, tab).get_or_build({"url": "https://jobs.invalid/x",
                                            "company": "Aye", "role": role})
    assert d["page_title"] == "Staff Engineer at Example Co | Example Board"
    assert d["structured_data"] == '{"@type": "JobPosting"}'


def test_a_lead_with_no_url_gets_blank_page_title_and_structured_data(tmp_path, role):
    tab = _Tab()
    d = _cache(tmp_path, tab).get_or_build({"company": "Aye", "role": role})
    assert d["page_title"] == "" and d["structured_data"] == ""
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dossier_guard.py -v`
Expected: `test_an_allowed_url_fetches_and_probes_in_order` FAILs (the closure does not yet make
the two new `evaluate` calls, and `_LD_JSON_JS` does not exist yet to import — this is an
`ImportError` collection failure until Step 4 lands, which is the correct "red" state: the whole
file fails to collect).

- [ ] **Step 4: Implement the capture in `core/app.py`**

Add the module-level JS constant near the seam constants (after `_SEAMS = (...)` around line 105):

```python
# Read once per successful dossier fetch, alongside document.body.innerText: JobPosting
# structured data, when a board embeds it (#109 tier-2 company resolution).
_LD_JSON_JS = (
    "(() => { const el = document.querySelector("
    "'script[type=\"application/ld+json\"]'); return el ? el.textContent : ''; })()"
)
```

Then in `dossier_cache`'s `fetch` closure, change the initial unpacking line:

```python
        def fetch(lead: dict) -> dict:
            md, url = "", lead.get("url")
```

to:

```python
        def fetch(lead: dict) -> dict:
            md, url = "", lead.get("url")
            page_title, structured_data = "", ""
```

and replace the body-read block:

```python
                    body = c.evaluate(tid, "document.body.innerText")
                    md = body.get("result") if isinstance(body, dict) else None
                    if not isinstance(md, str):
                        # Same reasoning as no-tab: a non-string body used to become
                        # cached empty JD indistinguishable from a real empty one.
                        _refuse(urlguard.BODY_UNREADABLE, pre.host)
                finally:
                    c.close_tab(tid)
            return {"jd": {"markdown": md or ""}, "glassdoor": {}}
```

with:

```python
                    body = c.evaluate(tid, "document.body.innerText")
                    md = body.get("result") if isinstance(body, dict) else None
                    if not isinstance(md, str):
                        # Same reasoning as no-tab: a non-string body used to become
                        # cached empty JD indistinguishable from a real empty one.
                        _refuse(urlguard.BODY_UNREADABLE, pre.host)
                    # #109 tier-2 resolution: best-effort, unlike the JD body above --
                    # a source that omits a page title or JSON-LD is common and not a
                    # transport failure, so a non-string probe result degrades to ""
                    # rather than refusing the whole (otherwise-good) dossier fetch.
                    title_res = c.evaluate(tid, "document.title")
                    got_title = title_res.get("result") if isinstance(title_res, dict) else None
                    page_title = got_title if isinstance(got_title, str) else ""
                    ld_res = c.evaluate(tid, _LD_JSON_JS)
                    got_ld = ld_res.get("result") if isinstance(ld_res, dict) else None
                    structured_data = got_ld if isinstance(got_ld, str) else ""
                finally:
                    c.close_tab(tid)
            return {"jd": {"markdown": md or ""}, "glassdoor": {},
                    "page_title": page_title, "structured_data": structured_data}
```

Also update the method's docstring to mention the new capture — append a sentence to
`dossier_cache`'s docstring:

```python
    def dossier_cache(self, dossier_dir, ttl_days):
        """A DossierCache whose fetcher is resolved lazily on the first cache miss, so a
        --no-llm or fully-cached run never opens a browser. JD text read via
        evaluate(document.body.innerText) -- the same {"result": ...} shape ingest uses.

        The lead url comes off a scraped listing, so it is guarded (#18): checked before
        a tab is opened, and the LANDED url re-checked before the body is read. A refusal
        RAISES rather than returning an empty dossier -- see the comment on the raise.

        Also captures document.title and any JSON-LD script tag's text content in the
        same already-open tab (#109), for triage's tier-2 company resolution. Both are
        best-effort: an unreadable probe degrades to "" rather than refusing the fetch.
        """
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dossier_guard.py -v`
Expected: PASS, all tests in the file (including every pre-existing one — none of the other
assertions in this file check an exact `tab.calls` sequence past the point where the new probes
are inserted, since every refusal branch exits before reaching them).

- [ ] **Step 6: Run the whole suite once to catch any other exact-sequence assertion**

Run: `python -m pytest -v -k dossier`
Expected: PASS. (This is the sweep CLAUDE.md's "enumerate, don't hand-list" lesson calls for —
confirm no OTHER test file also pins an exact `evaluate` call sequence against this closure.)

- [ ] **Step 7: Mutation-witness**

| Mutant | Must redden |
|---|---|
| Delete the `document.title` probe (or its `isinstance` guard) | `test_page_title_and_structured_data_are_captured_into_the_dossier` |
| Delete the JSON-LD probe (or its `isinstance` guard) | `test_page_title_and_structured_data_are_captured_into_the_dossier` |

- [ ] **Step 8: Commit**

```bash
git add sluice/core/app.py tests/test_dossier_guard.py
git commit -m "$(cat <<'EOF'
feat(triage): dossier fetch closure captures page_title and JSON-LD for company resolution (#109)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 3: Wellfound `company_from_url` + `Source` protocol docstring

**Files:**
- Modify: `sluice/ingest/base.py`
- Modify: `sluice/ingest/sources/wellfound.py`
- Test: `tests/test_parsers.py`

**Interfaces:**
- Produces: `Source.company_from_url(self, url: str) -> str | None` — an OPTIONAL protocol member
  (undeclared on `Source(Protocol)` itself, same shape as `Store.preflight`/`Renderer.precheck`).
  `WellfoundSource` (a thin `BrowserListSource` subclass) implements it.
- Consumed by: Task 5's `resolve.py`, via `getattr(source, "company_from_url", None)`.

Independent of Tasks 1–2 (per the spec's dependency order, `rev3-003`): this is a pure
tier-1 URL-string extractor that touches neither `DossierCache` nor `cache_key`.

**This task has a live-verification gate the other tasks do not.** The illustrative pattern below
(`rev2-002`) is a starting point only — before finalizing the fixtures in Step 4, run a real
capture and confirm the actual anchor `href` shape matches. Do not commit fixtures crafted to fit
an unverified regex.

- [ ] **Step 1: Capture the real URL shape**

Run: `job-sluice ingest test-source wellfound --raw`

Inspect the raw output for the `link`/`href` values Wellfound's card anchors actually carry.
Confirm they match `/company/<slug>/...` with a clear `/` (or end-of-string) boundary on both
sides of `<slug>` — the shape the illustrative pattern below assumes. If the real shape differs
(e.g. a trailing job-id segment with no slash, or the slug living in a different path position),
adjust the regex in Step 3 to match what was actually captured, and note the adjustment in the
commit message.

- [ ] **Step 2: Add the `Source` protocol docstring**

In `sluice/ingest/base.py`, `Source(Protocol)` currently carries no docstring at all. Replace:

```python
class Source(Protocol):
    id: str
    enabled: bool
    kind: str

    def searches(self) -> list: ...
    def fetch(self, ctx: Ctx, search: Search) -> dict: ...
    def parse(self, raw: dict, search: Search) -> list: ...
    def health_hint(self, raw: dict) -> dict: ...
```

with:

```python
class Source(Protocol):
    """A job board plugin: what to search, how to fetch results, and how to parse
    them into Leads. `fetch` is the only impure member -- it drives a `Ctx`'s
    browser client; `parse` is pure, tested offline against golden fixtures under
    tests/fixtures/<id>/raw.json.

    OPTIONAL MEMBER -- `company_from_url(self, url: str) -> str | None`. Not
    declared as a required member below, for the identical reason `Store.preflight`
    and `Renderer.precheck` are not: a Protocol member is a REQUIRED member, and the
    whole point of this hook is that a source may omit it.
    `sluice.triage.resolve.resolve_company` (#109) reaches it via
    `getattr(source, "company_from_url", None)` and treats its absence as tier-1
    abstaining for that source -- the same shape those two other optional seam
    members already use.

    Implement it only where the board's real URL shape unambiguously encodes the
    hiring company with a clear delimiter on both ends of the captured slug --
    never a guessed split point. Must never raise: it runs against live,
    hand-maintained scraped URLs on every triage run, so `resolve_company` isolates
    any exception from it and treats that as an abstain rather than letting one
    source's bug on one unanticipated URL shape crash the whole batch.
    """

    id: str
    enabled: bool
    kind: str

    def searches(self) -> list: ...
    def fetch(self, ctx: Ctx, search: Search) -> dict: ...
    def parse(self, raw: dict, search: Search) -> list: ...
    def health_hint(self, raw: dict) -> dict: ...
```

- [ ] **Step 3: Write the failing `company_from_url` tests**

Add to `tests/test_parsers.py`:

```python
def test_wellfound_company_from_url_confident_match():
    src = sources.get("wellfound")
    assert src.company_from_url(
        "https://wellfound.com/company/example-co/jobs/2837465-staff-engineer"
    ) == "Example Co"


def test_wellfound_company_from_url_abstains_without_a_company_segment():
    src = sources.get("wellfound")
    assert src.company_from_url("https://wellfound.com/role/r/software-engineer") is None


def test_wellfound_company_from_url_abstains_on_an_empty_url():
    src = sources.get("wellfound")
    assert src.company_from_url("") is None
```

Use the URLs Step 1's real capture actually confirmed; the two above are illustrative and must be
replaced with the confirmed shape before this step is considered done.

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python -m pytest tests/test_parsers.py -v -k company_from_url`
Expected: FAIL with `AttributeError: 'WellfoundSource' object has no attribute 'company_from_url'`
(today `sources.get("wellfound")` returns a plain `BrowserListSource` instance).

- [ ] **Step 5: Implement `WellfoundSource.company_from_url`**

Replace the full contents of `sluice/ingest/sources/wellfound.py`:

```python
"""Wellfound (wellfound.com, ex-AngelList) - startup EM roles (permanent).
Declarative extractor JS + an example search (override via config).

`company_from_url` (#109) is a tier-1, free URL-pattern extractor: a Wellfound
job/company URL carries the hiring company as a `/company/<slug>/...` path
segment, delimited by the literal `/company/` segment on one side and the next
`/` (or end of string) on the other -- unambiguous, so this abstains (returns
None) for any URL shape that does not carry that segment, rather than guess a
split point. Verified against a real `job-sluice ingest test-source wellfound
--raw` capture, not committed from the illustrative pattern alone.
"""
import re

from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register

_JS = """(()=>{const r=[];document.querySelectorAll('a[href*="/company/"], a[href*="/jobs/"]').forEach(a=>{const t=a.querySelector('h2,h3,div[class*="title"]')?.textContent?.trim()||a.textContent.trim();const p=a.closest('div,li');const co=p?.querySelector('div[class*="company"], span[class*="company"]')?.textContent?.trim()||'';if(t&&t.length>3&&!r.find(x=>x.title===t))r.push({title:t,company:co,location:'',link:a.href,salary:''})});return r.slice(0,15)})()"""

_COMPANY_URL_RE = re.compile(r"^https?://(?:www\.)?wellfound\.com/company/([a-z0-9-]+)")


class WellfoundSource(BrowserListSource):
    def company_from_url(self, url: str) -> str | None:
        m = _COMPANY_URL_RE.match(url or "")
        if not m:
            return None
        return m.group(1).replace("-", " ").title() or None


register(WellfoundSource(
    id="wellfound",
    extractor_js=_JS,
    wait=4, scrolls=2, scroll_amount=600,
    extra={"job_type": "perm"},
    searches_spec=[
        ('Wellfound example', 'https://wellfound.com/role/r/software-engineer'),
    ],
))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_parsers.py -v`
Expected: PASS, all tests in the file — including `test_all_expected_sources_register` and
`test_source_is_well_formed[wellfound]` (a `WellfoundSource` still satisfies every existing
`Source` duck-type check, since it only ADDS a method) and
`test_parser_yields_valid_leads[wellfound]` (the existing `raw.json` fixture and `.parse()` are
untouched).

- [ ] **Step 7: Mutation-witness**

| Mutant | Must redden |
|---|---|
| Delete the `if not m: return None` guard (or loosen the regex to match without a `/company/` segment) | `test_wellfound_company_from_url_abstains_without_a_company_segment` |
| Delete the `.replace("-", " ").title()` transform (return the raw slug) | `test_wellfound_company_from_url_confident_match` |

- [ ] **Step 8: Commit**

```bash
git add sluice/ingest/base.py sluice/ingest/sources/wellfound.py tests/test_parsers.py
git commit -m "$(cat <<'EOF'
feat(triage): Wellfound tier-1 company_from_url URL-pattern extractor (#109)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 4: `TriageConfig.company_resolve_fetch` knob (default off) + config docs

**Files:**
- Modify: `sluice/triage/config.py`
- Modify: `sluice.yaml.example`
- Modify: `docs/CONFIGURATION.md`
- Test: `tests/test_sluice_neutral_defaults.py`

**Interfaces:**
- Produces: `TriageConfig.company_resolve_fetch: bool = False`, loaded by the existing
  `hasattr`-filtered `setattr` loop in `load_triage_config` (no special-casing needed — this is a
  plain bool field, not the `lead_ttl_days` int/bool-subclass hazard).
- Consumed by: Task 7's `engine.py` (`cfg.company_resolve_fetch`, passed to `resolve_company`).

Independent of every other task; can land any time before Task 7.

- [ ] **Step 1: Write the failing abstain-default pinning tests**

Add to `tests/test_sluice_neutral_defaults.py`, near the `lead_ttl_days`/`lead_layout` guards
(after line ~394's `test_example_config_ships_lead_ttl_days_off`, matching that section's naming
convention exactly since `neu3-001` requires it):

```python
# ── #109: tier-2 company resolution's opt-in gate ────────────────────────────
# company_resolve_fetch needs its OWN guard, same reasoning as lead_ttl_days above:
# turning it on lets a blank-company lead trigger a REAL page visit, so an
# unconfigured install must never start doing that unprompted the moment it
# upgrades. Unlike lead_ttl_days this is a genuine bool field (no int/bool
# YAML-resolution hazard), so no extra validation is needed -- only the default.

def test_company_resolve_fetch_dataclass_default_is_off():
    assert TriageConfig().company_resolve_fetch is False


def test_company_resolve_fetch_loader_default_is_off(monkeypatch):
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert load_triage_config(None).company_resolve_fetch is False


def test_the_example_config_ships_company_resolve_fetch_commented():
    import yaml
    text = _EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "company_resolve_fetch:" in text, "company_resolve_fetch must be documented at all"
    doc = yaml.safe_load(text) or {}
    assert "company_resolve_fetch" not in (doc.get("triage") or {}), \
        "company_resolve_fetch must ship COMMENTED, not active"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_sluice_neutral_defaults.py -v -k company_resolve_fetch`
Expected: FAIL — `TriageConfig()` has no `company_resolve_fetch` attribute yet (`AttributeError`
on the first two), and `"company_resolve_fetch:"` is absent from `sluice.yaml.example` (the third).

- [ ] **Step 3: Add the field to `TriageConfig`**

In `sluice/triage/config.py`, add the new field to the dataclass, after `route_borderline`:

```python
    route_borderline: bool = False
```

becomes:

```python
    route_borderline: bool = False
    # Off by default (#109): gates the tier-2 (real, no-LLM page-visit) half of
    # blank-company resolution independently of --no-llm. An unconfigured install
    # must not start opening real browser tabs against arbitrary third-party sites
    # for its whole needs_review backlog the moment it upgrades -- the same
    # abstain-by-default posture as lead_ttl_days/lead_layout. Tier 1 (free,
    # URL-pattern-only) is unaffected by this knob and always runs.
    company_resolve_fetch: bool = False
```

- [ ] **Step 4: Add the commented entry to `sluice.yaml.example`**

In the `triage:` block, replace:

```yaml
  # Where the rejected-leads audit log is written. Unset, it resolves per-system under
  # $XDG_STATE_HOME (or ~/.local/state); $TRIAGE_AUDIT still wins over this key. This key
  # was declared but read by nothing before #80 -- setting it did nothing, silently.
  # A leading `~` is expanded, in the key and in the variable alike.
  # audit_jsonl:        # <- uncomment and set YOUR OWN
  contract_floor_gbp_day: 450     # illustrative; set your own (0 = no floor)
```

with:

```yaml
  # Where the rejected-leads audit log is written. Unset, it resolves per-system under
  # $XDG_STATE_HOME (or ~/.local/state); $TRIAGE_AUDIT still wins over this key. This key
  # was declared but read by nothing before #80 -- setting it did nothing, silently.
  # A leading `~` is expanded, in the key and in the variable alike.
  # audit_jsonl:        # <- uncomment and set YOUR OWN
  # Off by default (#109): turning this on lets a blank-company needs_review lead
  # trigger a REAL page visit (no LLM call) to try to identify the employer from
  # the page itself. An unconfigured install must not start opening browser tabs
  # against arbitrary third-party sites the moment it upgrades -- the same
  # opt-in-only posture as lead_ttl_days/lead_layout above.
  # company_resolve_fetch: true   # <- uncomment to opt in
  contract_floor_gbp_day: 450     # illustrative; set your own (0 = no floor)
```

- [ ] **Step 5: Add the row to `docs/CONFIGURATION.md`**

In the `## \`triage:\`` table, after the `route_borderline` row, add:

```markdown
| `company_resolve_fetch` | `false` | opt-in: lets a blank-company `needs_review` lead trigger a real (no-LLM) page visit to try to identify the employer from the page itself; off by default so an unconfigured install never opens a browser tab it wasn't asked to |
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_sluice_neutral_defaults.py -v -k company_resolve_fetch`
Expected: PASS, all three.

- [ ] **Step 7: Run the full neutral-defaults file once**

Run: `python -m pytest tests/test_sluice_neutral_defaults.py -v`
Expected: PASS — confirms the new field did not accidentally get swept into the LIST-keyed
`#26/#63` sweep (it shouldn't, since that sweep is `isinstance(..., list)`-keyed and this is a
bool) and that no other guard in the file broke.

- [ ] **Step 8: Mutation-witness**

| Mutant | Must redden |
|---|---|
| Delete `TriageConfig.company_resolve_fetch`'s `False` default (change to `True`) | `test_company_resolve_fetch_dataclass_default_is_off` |

- [ ] **Step 9: Commit**

```bash
git add sluice/triage/config.py sluice.yaml.example docs/CONFIGURATION.md tests/test_sluice_neutral_defaults.py
git commit -m "$(cat <<'EOF'
feat(triage): add company_resolve_fetch config knob, off by default (#109)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 5: `sluice/triage/resolve.py` — `resolve_company` + `_from_dossier`

**Files:**
- Create: `sluice/triage/resolve.py`
- Modify: `sluice/core/vault.py` (docstring only)
- Test: `tests/test_triage_resolve.py`

**Interfaces:**
- Consumes: `get_source` (a callable like `sluice.ingest.sources.get`, or `None`), a
  `dossier_cache` with `.get_or_build(fm) -> dict` (Task 1's schema), `fm: dict` (a lead's
  frontmatter dict, reading `url`/`source`).
- Produces: `resolve_company(fm, get_source, dossier_cache, *, no_llm, company_resolve_fetch=False)
  -> str | None` — the sole public entry point Task 7's `engine.py` calls. Also
  `_from_dossier(dossier: dict) -> str | None` (tier 2's pure extraction step, unit-tested
  directly) and the module-level `_UNSAFE_CHARS` tuple.

Depends on Task 1 (dossier schema), Task 3 (a real `company_from_url` to exercise tier 1 against
in the engine-level tests later — this task's own unit tests use fakes, so it does not block on
Task 3 landing first), and Task 4 (the `company_resolve_fetch` parameter name/semantics).

- [ ] **Step 1: Write the failing `resolve_company` unit tests**

Create `tests/test_triage_resolve.py`:

```python
import pytest

from sluice.triage import resolve


class _RecordingCache:
    def __init__(self, dossier=None, raises=None):
        self.calls = 0
        self._dossier = dossier or {}
        self._raises = raises

    def get_or_build(self, fm):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._dossier


def _source(company_from_url=None, raises=None):
    class _Source:
        pass
    src = _Source()
    if raises is not None:
        def _boom(url):
            raise raises
        src.company_from_url = _boom
    elif company_from_url is not None:
        src.company_from_url = company_from_url
    return src


def _get_source(mapping):
    def _get(sid):
        if sid not in mapping:
            raise KeyError(sid)
        return mapping[sid]
    return _get


FM = {"url": "https://example.invalid/jobs/1", "source": "example-board"}


def test_tier1_hit_never_calls_the_dossier_cache():
    src = _source(company_from_url=lambda url: "Example Co")
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got == "Example Co"
    assert cache.calls == 0


def test_tier1_miss_falls_through_to_tier2():
    src = _source(company_from_url=lambda url: None)
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got is None
    assert cache.calls == 1


def test_both_tiers_miss_returns_none():
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got is None


def test_get_source_none_skips_tier1_unconditionally():
    cache = _RecordingCache(dossier={
        "structured_data": '{"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}}',
        "page_title": ""})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got == "Example Co"       # tier 2 still runs; only tier 1 is unconditionally skipped
    assert cache.calls == 1


def test_no_llm_never_calls_the_dossier_cache_even_on_a_tier1_miss():
    src = _source(company_from_url=lambda url: None)
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=True, company_resolve_fetch=True)
    assert got is None
    assert cache.calls == 0


def test_company_resolve_fetch_false_never_calls_the_dossier_cache():
    src = _source(company_from_url=lambda url: None)
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=False)
    assert got is None
    assert cache.calls == 0


def test_unknown_source_id_abstains_rather_than_raising():
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, _get_source({}), cache, no_llm=False,
                                  company_resolve_fetch=True)
    assert got is None


def test_dossier_fetch_exception_abstains_rather_than_propagating():
    cache = _RecordingCache(raises=RuntimeError("boom"))
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got is None


def test_extractor_exception_abstains_rather_than_propagating():
    src = _source(raises=RuntimeError("boom"))
    cache = _RecordingCache(dossier={"page_title": "", "structured_data": ""})
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got is None
    assert cache.calls == 1    # tier 1's crash must not stop tier 2 from being attempted


@pytest.mark.parametrize("unsafe", ['Example "Co"', "Example\nCo", "Example\rCo"])
def test_tier1_candidate_with_a_structural_character_is_rejected(unsafe):
    src = _source(company_from_url=lambda url: unsafe)
    cache = _RecordingCache()
    got = resolve.resolve_company(FM, _get_source({"example-board": src}), cache,
                                  no_llm=False, company_resolve_fetch=True)
    assert got is None


@pytest.mark.parametrize("unsafe", ['Example "Co"', "Example\nCo", "Example\rCo"])
def test_tier2_candidate_with_a_structural_character_is_rejected(unsafe):
    cache = _RecordingCache(dossier={"page_title": f"Staff Engineer at {unsafe} | Board",
                                     "structured_data": ""})
    got = resolve.resolve_company(FM, None, cache, no_llm=False, company_resolve_fetch=True)
    assert got is None


def test_from_dossier_reads_jobposting_jsonld():
    d = {"structured_data": '{"@type": "JobPosting", "hiringOrganization": {"name": "Example Co"}}',
        "page_title": ""}
    assert resolve._from_dossier(d) == "Example Co"


def test_from_dossier_reads_a_title_pattern_when_structured_data_is_absent():
    d = {"structured_data": "", "page_title": "Staff Engineer at Example Co | Example Board"}
    assert resolve._from_dossier(d) == "Example Co"


def test_from_dossier_prefers_jsonld_when_both_present_and_disagree():
    d = {"structured_data": '{"@type": "JobPosting", "hiringOrganization": {"name": "JSON-LD Co"}}',
        "page_title": "Staff Engineer at Title Co | Example Board"}
    assert resolve._from_dossier(d) == "JSON-LD Co"


@pytest.mark.parametrize("title", [
    "We are hiring at Example Co",       # "at" present, not the "role at Company | Board" shape
    "Example Co hiring engineers now",   # "hiring" present, not "is hiring a/an ..." shape
])
def test_from_dossier_title_pattern_near_miss_abstains(title):
    d = {"structured_data": "", "page_title": title}
    assert resolve._from_dossier(d) is None


def test_from_dossier_both_absent_returns_none():
    assert resolve._from_dossier({"structured_data": "", "page_title": ""}) is None


def test_from_dossier_malformed_jsonld_returns_none_not_raises():
    d = {"structured_data": "{not valid json", "page_title": ""}
    assert resolve._from_dossier(d) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_triage_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.triage.resolve'` (collection
failure across the whole file).

- [ ] **Step 3: Create `sluice/triage/resolve.py`**

```python
"""Tier 1 (free, URL-pattern) then tier 2 (a real, no-LLM page visit) company
resolution for a blank-company `needs_review` lead (#109). Both tiers abstain
rather than guess: classify.py's blank-company branch already treats a blank
company as the honest "unknown" state, and a wrong company would silently carry
through keep -> judge -> apply -> a CV addressed to the wrong employer, which is
worse than staying blank."""
import json
import re

_UNSAFE_CHARS = ('"', "\n", "\r")

# Anchored full-string, deliberately narrow: a page_title that merely CONTAINS
# "at"/"hiring" without this exact shape must abstain, not guess a company from a
# coincidental substring match (see the near-miss test in test_triage_resolve.py).
_TITLE_PATTERNS = (
    re.compile(r"^(?P<role>.+?)\s+at\s+(?P<company>.+?)\s+\|\s+.+$"),
    re.compile(r"^(?P<company>.+?)\s+is\s+hiring\s+(?:a|an)\s+.+$"),
)


def _hiring_org_from_jsonld(raw: str) -> str | None:
    """schema.org/JobPosting -> hiringOrganization.name, tolerating a bare object, a
    list of nodes, or a `@graph` array -- and any malformed/missing shape, which
    abstains (None) rather than raising."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if isinstance(data, list):
        nodes = data
    elif isinstance(data, dict):
        graph = data.get("@graph")
        nodes = graph if isinstance(graph, list) else [data]
    else:
        return None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if "JobPosting" not in types:
            continue
        org = node.get("hiringOrganization")
        if isinstance(org, dict):
            name = (org.get("name") or "").strip()
            if name:
                return name
    return None


def _from_dossier(dossier: dict) -> str | None:
    """Tier 2's pure extraction step: JSON-LD first (structured, board-authored,
    highest confidence), then a small set of real-capture-validated title shapes.
    JSON-LD wins when both are present and disagree."""
    hit = _hiring_org_from_jsonld(dossier.get("structured_data") or "")
    if hit:
        return hit
    title = dossier.get("page_title") or ""
    for pattern in _TITLE_PATTERNS:
        m = pattern.match(title)
        if m:
            company = m.group("company").strip()
            if company:
                return company
    return None


def resolve_company(fm: dict, get_source, dossier_cache, *,
                    no_llm: bool, company_resolve_fetch: bool = False) -> str | None:
    """Tier 1 then tier 2, first confident match wins. Returns None -- never a guess --
    when both abstain, INCLUDING when a candidate contains a frontmatter-structural
    character. `get_source` is `sluice.ingest.sources.get` (or None, meaning tier 1
    always abstains), injected so this stays testable without importing the real
    registry."""
    def _safe(candidate):
        return candidate if candidate and not any(c in candidate for c in _UNSAFE_CHARS) else None

    url = fm.get("url") or ""
    src_id = fm.get("source") or ""
    if get_source is not None and url and src_id:
        try:
            source = get_source(src_id)
        except KeyError:
            source = None
        extractor = getattr(source, "company_from_url", None)
        if extractor:
            try:
                hit = _safe(extractor(url))
            except Exception:
                hit = None  # a per-source extractor is newly-authored, hand-maintained regex
                            # code running against live scraped URLs -- exactly the untrusted
                            # input class the _safe guard exists for. One source's bug on one
                            # unanticipated URL shape must not crash the whole triage run.
            if hit:
                return hit
    if no_llm or not company_resolve_fetch or not url:
        return None
    try:
        dossier = dossier_cache.get_or_build(fm)
    except Exception:
        return None  # a failed fetch just means "couldn't resolve" -- fall through to
                     # classify()'s existing needs_review branch, not a fatal per-lead error
    return _safe(_from_dossier(dossier))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_triage_resolve.py -v`
Expected: PASS, every test.

- [ ] **Step 5: Add the `_set_fm` docstring clause (`arch3-003`)**

`resolve.py`'s `_safe` guard is the FIRST time raw, unmediated open-web content reaches a
frontmatter write in this codebase — `_set_fm`'s own docstring should say so, for the next
raw-content writer. In `sluice/core/vault.py`, find:

```python
def _set_fm(inner: str, key: str, literal: str) -> str:
    """Replace `key:`'s line in a frontmatter block, or append it if absent.
    `literal` is written verbatim, so the caller controls quoting."""
```

Replace with:

```python
def _set_fm(inner: str, key: str, literal: str) -> str:
    """Replace `key:`'s line in a frontmatter block, or append it if absent.
    `literal` is written verbatim, so the caller controls quoting -- a caller
    writing unmediated external content (e.g. #109's resolved company, pulled
    from a scraped page's title or JSON-LD) is responsible for its OWN
    structural-character guard before the value reaches here; see
    `triage/resolve.py`'s `_safe`. This is not a design change, just a warning
    for the next raw-content writer: a blanket character check at this layer
    would reject the wrapping quotes every existing quoted caller
    (`glassdoor_rating`, `culture_flags`) already relies on -- the check is only
    meaningful pre-quote, which only the caller holds."""
```

This is a docstring-only change with no behaviour to test; verify it with a read-back rather than
a new test.

Run: `grep -n "_safe" sluice/core/vault.py` and confirm the new sentence is present.

- [ ] **Step 6: Mutation-witness**

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first.

| Mutant | Must redden |
|---|---|
| Delete the `_safe(...)` check on either tier's candidate | both structural-character tests |
| Delete the `except KeyError` around `get_source(src_id)` | `test_unknown_source_id_abstains_rather_than_raising` |
| Delete the `except Exception` around `dossier_cache.get_or_build` | `test_dossier_fetch_exception_abstains_rather_than_propagating` |
| Delete the `except Exception` around `extractor(url)` | `test_extractor_exception_abstains_rather_than_propagating` |
| Swap tier order (try tier 2 before tier 1) | `test_tier1_hit_never_calls_the_dossier_cache` |
| Delete the `if hit:` guard so an empty-string extractor result is treated as resolved | `test_tier1_miss_falls_through_to_tier2` |
| Delete the JSON-LD parse's `try/except` | `test_from_dossier_malformed_jsonld_returns_none_not_raises` |
| Loosen a title-pattern regex boundary (drop the `\|` or the `(?:a\|an)` requirement) | `test_from_dossier_title_pattern_near_miss_abstains` |
| Swap the two extraction attempts inside `_from_dossier` (title-pattern before JSON-LD) | `test_from_dossier_prefers_jsonld_when_both_present_and_disagree` |
| Delete the `no_llm`/`company_resolve_fetch`/`url` guard before the tier-2 fetch | `test_no_llm_never_calls_the_dossier_cache_even_on_a_tier1_miss` / `test_company_resolve_fetch_false_never_calls_the_dossier_cache` |

- [ ] **Step 7: Commit**

```bash
git add sluice/triage/resolve.py sluice/core/vault.py tests/test_triage_resolve.py
git commit -m "$(cat <<'EOF'
feat(triage): add resolve_company -- tier-1/tier-2 blank-company resolution (#109)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 6: `apply_classification`/`apply_verdict` — `require_status` hardening + `"skipped-race"`

**Files:**
- Modify: `sluice/triage/apply.py`
- Test: `tests/test_apply.py`

**Interfaces:**
- Produces: `apply_classification(vault, note, decision, reason) -> str` and `apply_verdict(vault,
  note, verdict, dossier) -> str` now return `"skipped-race"` (a NEW distinct outcome, alongside
  the existing `"applied"`/`"skipped"`) when their `update_fields` write abstains because the
  fresh status has left `TRIAGE_OWNED` or the value already matched.
- Consumed by: Task 7's `engine.py`, which must bucket `"skipped-race"` under
  `report.counts["skipped"]` but gate `_audit(...)` and add a distinct `report.failures` entry for
  it, never treating it the same as the pre-existing `"skipped"` (the `_guarded()` read-time skip,
  which is untouched).

This is the Critical fix (`inv2-001`) from round 2 of `/review-plan`. Independent of Tasks 1–5;
ordered here (matching the spec's own step-5 placement) only because Task 7's engine-level race
tests reuse this task's scaffolding pattern.

- [ ] **Step 1: Write the failing race tests**

Add to `tests/test_apply.py`:

```python
def test_apply_classification_returns_skipped_race_on_a_status_change_between_read_and_write(tmp_path):
    v = Vault(str(tmp_path))
    _note(v, "D.md", ['company: "Delta"', "status: new", "score: 0",
                      'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    # Simulate a receipt/manual `apply record` landing between read_leads() and
    # this write -- the lead has already left TRIAGE_OWNED by the time the write
    # is attempted, but `note.status` (frozen at read time) still reads "new".
    v.update_fields(note.ref, {"status": "applied"})
    assert apply_classification(v, note, "reject", "IC role") == "skipped-race"
    assert v.read_leads()[0].status == "applied"     # the real status survives untouched


def test_apply_verdict_returns_skipped_race_on_a_status_change_between_read_and_write(tmp_path):
    v = Vault(str(tmp_path))
    _note(v, "E.md", ['company: "Epsilon"', "status: new", "score: 0",
                      'glassdoor_rating: ""', 'culture_flags: ""', 'relevance_notes: ""'])
    note = v.read_leads({"new"})[0]
    v.update_fields(note.ref, {"status": "applied"})
    verdict = {"verdict": "shortlist", "relevance_score": 82, "fit_reasoning": "fit"}
    assert apply_verdict(v, note, verdict, {}) == "skipped-race"
    assert v.read_leads()[0].status == "applied"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_apply.py -v -k skipped_race`
Expected: FAIL — both currently return `"applied"` and actually clobber the vault's `applied`
status with `dismiss`/`shortlist`. Confirm the second assertion (`v.read_leads()[0].status ==
"applied"`) is what fails: today's `apply_classification`/`apply_verdict` have no `require_status`
guard at all, so the write lands unconditionally.

- [ ] **Step 3: Implement the hardening**

Replace `apply_classification`:

```python
def apply_classification(vault, note, decision, reason) -> str:
    if _guarded(note):
        return "skipped"
    new_status = _DECISION_STATUS.get(decision, "needs_review")
    tag = f"[triage {date.today().isoformat()}]"
    vault.update_fields(
        note.ref, {"status": new_status},
        append_note=f"{tag} {decision}: {reason}".strip(), note_tag=tag,
    )
    return "applied"
```

with:

```python
def apply_classification(vault, note, decision, reason) -> str:
    if _guarded(note):
        return "skipped"
    new_status = _DECISION_STATUS.get(decision, "needs_review")
    tag = f"[triage {date.today().isoformat()}]"
    # require_status (#109 inv2-001): the pre-existing _guarded() check above reads
    # note.status, a plain dataclass field frozen at read_leads() time -- byte-identical
    # to no guard at all against a real vault. This re-reads the FRESH status inside the
    # CAS transform, closing the window a #109 tier-2 fetch (real page load, seconds) now
    # opens ahead of this write.
    wrote = vault.update_fields(
        note.ref, {"status": new_status},
        append_note=f"{tag} {decision}: {reason}".strip(), note_tag=tag,
        require_status=frozenset(_status.TRIAGE_OWNED))
    return "applied" if wrote else "skipped-race"
```

Replace `apply_verdict`:

```python
def apply_verdict(vault, note, verdict, dossier) -> str:
    if _guarded(note):
        return "skipped"
    status = _status.normalize(verdict.get("verdict", "needs_review"))
    score = int(verdict.get("relevance_score", 0) or 0)
    rating = (dossier.get("glassdoor") or {}).get("rating", "")
    flags = ", ".join(verdict.get("culture_flags") or [])
    fields = {
        "status": status,
        "score": str(score),
        "glassdoor_rating": f'"{rating}"',
        "culture_flags": f'"{flags}"',
    }
    tag = f"[triage {date.today().isoformat()}]"
    parts = [verdict.get("fit_reasoning", "")]
    if verdict.get("concerns"):
        parts.append("Concerns: " + "; ".join(verdict["concerns"]))
    if verdict.get("recommended_next_action"):
        parts.append("Next: " + verdict["recommended_next_action"])
    note_text = f"{tag} " + " ".join(p for p in parts if p)
    vault.update_fields(note.ref, fields, append_note=note_text.strip(), note_tag=tag)
    return "applied"
```

with:

```python
def apply_verdict(vault, note, verdict, dossier) -> str:
    if _guarded(note):
        return "skipped"
    status = _status.normalize(verdict.get("verdict", "needs_review"))
    score = int(verdict.get("relevance_score", 0) or 0)
    rating = (dossier.get("glassdoor") or {}).get("rating", "")
    flags = ", ".join(verdict.get("culture_flags") or [])
    fields = {
        "status": status,
        "score": str(score),
        "glassdoor_rating": f'"{rating}"',
        "culture_flags": f'"{flags}"',
    }
    tag = f"[triage {date.today().isoformat()}]"
    parts = [verdict.get("fit_reasoning", "")]
    if verdict.get("concerns"):
        parts.append("Concerns: " + "; ".join(verdict["concerns"]))
    if verdict.get("recommended_next_action"):
        parts.append("Next: " + verdict["recommended_next_action"])
    note_text = f"{tag} " + " ".join(p for p in parts if p)
    # require_status: same hardening as apply_classification above, closing the
    # identical pre-existing gap behind the (even longer) dossier-fetch-plus-judge
    # round trip.
    wrote = vault.update_fields(note.ref, fields, append_note=note_text.strip(), note_tag=tag,
                                require_status=frozenset(_status.TRIAGE_OWNED))
    return "applied" if wrote else "skipped-race"
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_apply.py -v`
Expected: PASS, all tests in the file — including the three pre-existing ones
(`test_apply_classification_rejects_to_dismiss`, `test_apply_verdict_writes_all_fields`,
`test_never_clobbers_application_status`), none of which pass `require_status` explicitly and are
unaffected since their status is always in `TRIAGE_OWNED` (or, for the third, the PRE-EXISTING
`_guarded()` read-time check already catches it before `update_fields` is even called).

- [ ] **Step 5: Run the full triage test suite to catch any caller assuming only `"applied"`/`"skipped"`**

Run: `python -m pytest tests/test_triage_engine.py tests/test_apply.py -v`
Expected: PASS. (Task 7 will separately wire `engine.py` to handle `"skipped-race"` explicitly —
this step just confirms nothing in the CURRENT `engine.py` breaks by receiving an outcome string
it doesn't recognize yet; today's `key = "skipped" if outcome == "skipped" else (...)` line falls
through the `else` branch for `"skipped-race"`, miscounting it as a dismiss/needs_review bucket
and still auditing it — a real bug, closed in Task 7, not this one.)

- [ ] **Step 6: Mutation-witness**

| Mutant | Must redden |
|---|---|
| Delete `require_status=` from `apply_classification`'s write | `test_apply_classification_returns_skipped_race_on_a_status_change_between_read_and_write` |
| Delete `require_status=` from `apply_verdict`'s write | `test_apply_verdict_returns_skipped_race_on_a_status_change_between_read_and_write` |
| Change either `"applied" if wrote else "skipped-race"` back to plain `return "applied"` | the same two tests (the return-value assertion, not just the vault-status one) |

- [ ] **Step 7: Commit**

```bash
git add sluice/triage/apply.py tests/test_apply.py
git commit -m "$(cat <<'EOF'
fix(triage): require_status-guard apply_classification/apply_verdict against the widened race (#109)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 7: `engine.py` restructuring — resolution call site, `get_source` threading, `"skipped-race"` wiring

**Files:**
- Modify: `sluice/triage/engine.py`
- Modify: `sluice/core/app.py` (`Sluice.triage()`)
- Test: `tests/test_triage_engine.py`
- Test: `tests/test_classify.py` (signature regression test)
- Test: `tests/test_app_operations.py` (`Sluice.triage()` wiring test)

**Interfaces:**
- Consumes: Task 5's `resolve.resolve_company`, Task 6's `"skipped-race"` outcome, Task 4's
  `cfg.company_resolve_fetch`.
- Produces: `run(vault, cfg, backend, dossier_cache, audit, *, statuses=("new", "research"),
  limit=None, dry_run=False, no_llm=False, get_source=None)` — the new `get_source` parameter is
  keyword-only, defaulting to `None`, so every existing direct call site in
  `tests/test_triage_engine.py` (six of them: four via the top-level `run` import, two via
  `eng.run`) is unaffected by construction.

This is the largest task: the core restructuring the whole design exists to deliver, plus its
integration test suite (the tests that actually prove the cost-neutrality, dry-run, and
cache-reuse claims the spec makes). Depends on Tasks 1, 3, 4, 5, 6 all landing first.

- [ ] **Step 1: Write the failing engine-restructuring tests**

Add to `tests/test_triage_engine.py`. First, two small helpers near the top (after the existing
`_cache` helper):

```python
def _blank_fields(role, *, source="ex-board", url="https://x/y", status="new"):
    return ['company: ""', f'role: "{role}"', 'location: "remote"',
           'salary: ""', 'role_type: "permanent"', f'url: "{url}"',
           f'source: "{source}"',
           f"status: {status}", "score: 0", 'glassdoor_rating: ""',
           'culture_flags: ""', 'relevance_notes: ""']


class _RecordingCache:
    """A DossierCache stand-in recording get_or_build calls without touching disk,
    for proving how many fetches a run actually performs."""
    def __init__(self, dossier=None):
        self.calls = []
        self._dossier = dossier or {"page_title": "", "structured_data": ""}

    def get_or_build(self, fm):
        self.calls.append(dict(fm))
        return dict(self._dossier)


def _tier1_source(company):
    class _Source:
        def company_from_url(self, url):
            return company
    return _Source()


def _get_source(mapping):
    def _get(sid):
        if sid not in mapping:
            raise KeyError(sid)
        return mapping[sid]
    return _get
```

Then the integration tests:

```python
def test_resolution_never_attempted_for_a_lead_classify_would_reject_anyway(tmp_path, titles):
    # Proves the arch-001/rev-002 cost-neutrality fix: gating resolution on
    # decision == "needs_review" means classify()'s existing free title reject
    # (which runs BEFORE the blank-company branch) short-circuits resolution
    # entirely for a lead that was never going to reach it.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(reject[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_titles = list(reject)
    cfg.company_resolve_fetch = True
    cache = _RecordingCache()

    eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",), get_source=_get_source({}))

    assert cache.calls == []
    assert v.read_leads()[0].status == "dismiss"


def test_tier1_resolution_reclassifies_to_dismiss_on_reject_companies(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_companies = ["resolved co"]
    cfg.company_resolve_fetch = True
    cache = _RecordingCache()

    eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",),
           get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"
    assert after.status == "dismiss"
    assert cache.calls == []          # tier 1 hit, tier 2 never reached


def test_tier2_resolution_reclassifies_to_dismiss_on_reject_companies(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_companies = ["resolved co"]
    cfg.company_resolve_fetch = True
    dossier = {"page_title": "", "structured_data":
              '{"@type": "JobPosting", "hiringOrganization": {"name": "Resolved Co"}}'}
    cache = _RecordingCache(dossier=dossier)

    eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",),
           get_source=_get_source({}))     # unknown source -> tier 1 abstains

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"
    assert after.status == "dismiss"
    assert len(cache.calls) == 1


def test_no_llm_leaves_a_tier1_miss_lead_unresolved(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cache = _RecordingCache()

    eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",),
           no_llm=True, get_source=_get_source({}))

    after = v.read_leads()[0]
    assert after.fm["company"] == ""
    assert after.status == "needs_review"
    assert cache.calls == []


def test_company_resolve_fetch_off_by_default_leaves_a_lead_unresolved(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()          # company_resolve_fetch left at its False default
    cache = _RecordingCache()

    eng.run(v, cfg, _Backend(), cache, audit, statuses=("new",), get_source=_get_source({}))

    after = v.read_leads()[0]
    assert after.fm["company"] == ""
    assert after.status == "needs_review"
    assert cache.calls == []


def test_company_write_vault_conflict_leaves_the_original_needs_review_decision(tmp_path, titles, monkeypatch):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    real = v.update_fields
    calls = {"n": 0}
    def flaky(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise VaultConflict(ref)
        return real(ref, fields, **kw)
    monkeypatch.setattr(v, "update_fields", flaky)

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    assert after.fm["company"] == ""               # the company write never landed
    assert after.status == "needs_review"           # classify()'s original decision stands
    assert any("company-resolve" in f for f in report.failures)


def test_company_write_require_status_abstain_leaves_the_original_needs_review_decision(tmp_path, titles, monkeypatch):
    # Mirrors #9's own require_status race test construction: the lead advances
    # into the application lifecycle between read_leads() and the company write.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    real = v.update_fields
    calls = {"n": 0}
    def racer(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            real(ref, {"status": "applied"})    # a receipt lands first
        return real(ref, fields, **kw)
    monkeypatch.setattr(v, "update_fields", racer)

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    assert after.status == "applied"
    assert after.fm["company"] == ""
    assert any("company-resolve" in f for f in report.failures)


def test_company_write_already_current_self_heals_without_a_misleading_claim(tmp_path, titles, monkeypatch):
    # rev2-001: a concurrent resolution computes the identical company first, so
    # THIS run's write is a genuine no-op (require_status still passes; the value
    # already matched). Same behaviour as the require_status-abstain case: decision
    # stays needs_review this run, self-healing on the next once company reads
    # back non-blank.
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    real = v.update_fields
    calls = {"n": 0}
    def racer(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            real(ref, {"company": '"Resolved Co"'})   # a concurrent resolution wrote first
        return real(ref, fields, **kw)
    monkeypatch.setattr(v, "update_fields", racer)

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     get_source=_get_source({"ex-board": _tier1_source("Resolved Co")}))

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"       # the concurrent write survives
    assert any("company-resolve" in f for f in report.failures)
    assert after.status == "needs_review"     # this run's write was a no-op; the eventual
                                               # apply_classification write below still lands


def test_apply_classification_race_produces_no_persisted_audit_entry(tmp_path, titles, monkeypatch):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "race.md", _fields("Race Co", reject[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_titles = list(reject)

    real = eng.apply_classification
    def racer(vault, note, decision, reason):
        vault.update_fields(note.ref, {"status": "applied"})    # a receipt lands first
        return real(vault, note, decision, reason)
    monkeypatch.setattr(eng, "apply_classification", racer)

    report = eng.run(v, cfg, _Backend(), _cache(tmp_path), audit, statuses=("new",))

    after = v.read_leads()[0]
    assert after.status == "applied"                       # the vault clobber is stopped
    assert report.counts["skipped"] >= 1
    assert any("apply-race" in f for f in report.failures)
    assert audit.read_recent(30) == []                      # no false persisted entry


def test_apply_verdict_race_produces_no_persisted_audit_entry(tmp_path, titles, monkeypatch):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "race.md", _fields("Race Co", accept[0].title()))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)

    real = eng.apply_verdict
    def racer(vault, note, verdict, dossier):
        vault.update_fields(note.ref, {"status": "applied"})
        return real(vault, note, verdict, dossier)
    monkeypatch.setattr(eng, "apply_verdict", racer)

    report = eng.run(v, cfg, _Backend(), _cache(tmp_path), audit, statuses=("new",))

    after = v.read_leads()[0]
    assert after.status == "applied"
    assert report.counts["skipped"] >= 1
    assert any("apply-race" in f for f in report.failures)
    assert audit.read_recent(30) == []


def test_dry_run_resolution_keep_count_is_accurate_but_reject_bucket_is_not(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "keep.md", _blank_fields(accept[0].title(), source="ex-board", url="https://x/1"))
    _note(v, "rej.md", _blank_fields(accept[0].title(), source="ex-board", url="https://x/2"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.reject_companies = ["blocked co"]
    cfg.company_resolve_fetch = True

    class _UrlKeyedSource:
        def company_from_url(self, url):
            return {"https://x/1": "Resolved Co", "https://x/2": "Blocked Co"}.get(url)

    report = eng.run(v, cfg, _Backend(), _RecordingCache(), audit, statuses=("new",),
                     dry_run=True, get_source=_get_source({"ex-board": _UrlKeyedSource()}))

    assert report.counts["keep"] == 1
    assert report.counts["skipped"] >= 1
    for note in v.read_leads():
        assert note.fm["company"] == ""      # nothing actually written under dry_run


def test_tier2_resolution_and_the_later_judge_share_one_fetch(tmp_path, titles):
    accept, reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "blank.md", _blank_fields(accept[0].title(), source="ex-board", url="https://x/1"))
    audit = AuditLog(str(tmp_path / "audit.jsonl"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True

    calls = []
    def fetcher(lead):
        calls.append(lead.get("url"))
        return {"jd": {"markdown": "j"}, "glassdoor": {}, "page_title": "",
                "structured_data": '{"@type": "JobPosting", '
                                   '"hiringOrganization": {"name": "Resolved Co"}}'}
    real_cache = DossierCache(str(tmp_path / "dos"), ttl_days=7, fetcher=fetcher,
                              clock=lambda: datetime(2026, 7, 7))

    eng.run(v, cfg, _Backend(), real_cache, audit, statuses=("new",),
           get_source=_get_source({}))     # unknown source -> tier 1 abstains, tier 2 runs

    after = v.read_leads()[0]
    assert after.fm["company"] == "Resolved Co"
    assert after.status == "shortlist"           # resolved, then judged and shortlisted
    assert len(calls) == 1                       # classify-pass fetch reused by enrich/judge
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_triage_engine.py -v`
Expected: FAIL — `TypeError: run() got an unexpected keyword argument 'get_source'` on every new
test (today's `run()` has no such parameter). The pre-existing tests in the file still pass.

- [ ] **Step 3: Restructure `sluice/triage/engine.py`**

Add the module-scope import, alongside the existing `from sluice.triage.prompt import
build_system_prompt_from`:

```python
from sluice.triage.apply import apply_classification, apply_verdict
from sluice.triage.audit import render_rejected_note
from sluice.triage.classify import classify
from sluice.triage.judge import judge
from sluice.triage.prompt import build_system_prompt_from
```

becomes:

```python
from sluice.triage import resolve
from sluice.triage.apply import apply_classification, apply_verdict
from sluice.triage.audit import render_rejected_note
from sluice.triage.classify import classify
from sluice.triage.judge import judge
from sluice.triage.prompt import build_system_prompt_from
```

Change the `run()` signature:

```python
def run(vault, cfg, backend, dossier_cache, audit, *,
        statuses=("new", "research"), limit=None, dry_run=False, no_llm=False):
```

to:

```python
def run(vault, cfg, backend, dossier_cache, audit, *,
        statuses=("new", "research"), limit=None, dry_run=False, no_llm=False,
        get_source=None):
```

Replace the whole classify-pass loop:

```python
    # ── classify pass (free) ──
    for note in notes:
        decision, reason = classify(note.fm, cfg)
        if decision == "keep":
            report.counts["keep"] += 1
            keeps.append(note)
            continue
        if dry_run:
            outcome = "skipped"
        else:
            try:
                outcome = apply_classification(vault, note, decision, reason)
            except VaultConflict as e:
                # #16: a concurrent edit won the write race; leave the lead as-is,
                # retried next run. except VaultConflict (not broad Exception) so a
                # real apply-layer logic bug is not silently counted as a transient
                # conflict. continue skips the counting/audit below for this lead.
                report.failures.append(f"apply {note.ref}: {e}")
                continue
        key = "skipped" if outcome == "skipped" else (
            "dismiss" if decision == "reject" else "needs_review")
        report.counts[key] = report.counts.get(key, 0) + 1
        _audit({"ts": today, "slug": note.slug,
                "company": note.fm.get("company", ""), "role": note.fm.get("role", ""),
                "url": note.fm.get("url", ""), "stage": "classify",
                "decision": decision, "reason": reason, "score": 0})
```

with:

```python
    # ── classify pass (free unless resolution's tier 2 visits a page) ──
    for note in notes:
        company = (note.fm.get("company") or "").strip()
        decision, reason = classify(note.fm, cfg)
        # #109: resolution attempted only for classify()'s OWN blank-company
        # needs_review branch, never ahead of its existing title/location/pay
        # rejects (which don't depend on company at all) -- so a lead classify
        # would reject regardless never triggers a tier-2 page visit.
        if decision == "needs_review" and not company:
            resolved = resolve.resolve_company(
                note.fm, get_source, dossier_cache,
                no_llm=no_llm, company_resolve_fetch=cfg.company_resolve_fetch)
            if resolved:
                wrote = False
                if not dry_run:
                    try:
                        wrote = vault.update_fields(
                            note.ref, {"company": f'"{resolved}"'},
                            require_status=frozenset(_status.TRIAGE_OWNED))
                    except VaultConflict as e:
                        report.failures.append(f"company-resolve {note.ref}: {e}")
                    else:
                        if not wrote:
                            report.failures.append(
                                f"company-resolve {note.ref}: company write did not land "
                                "(status changed, or the value was already current)")
                if wrote or dry_run:
                    note.fm["company"] = resolved
                    decision, reason = classify(note.fm, cfg)
        if decision == "keep":
            report.counts["keep"] += 1
            keeps.append(note)
            continue
        if dry_run:
            outcome = "skipped"
        else:
            try:
                outcome = apply_classification(vault, note, decision, reason)
            except VaultConflict as e:
                # #16: a concurrent edit won the write race; leave the lead as-is,
                # retried next run. except VaultConflict (not broad Exception) so a
                # real apply-layer logic bug is not silently counted as a transient
                # conflict. continue skips the counting/audit below for this lead.
                report.failures.append(f"apply {note.ref}: {e}")
                continue
        if outcome == "skipped-race":
            # #109 round 3 (arch3-001/inv3-001): apply_classification's own
            # require_status guard already stopped the vault write -- this closes
            # the remaining gap, a PERSISTED audit-log entry claiming a decision
            # that never actually applied, which render_rejected_note would
            # otherwise render into a human-facing summary as if it had.
            report.failures.append(
                f"apply-race {note.ref}: status changed before the {decision} write "
                "landed (or the value was already current)")
        key = "skipped" if outcome in ("skipped", "skipped-race") else (
            "dismiss" if decision == "reject" else "needs_review")
        report.counts[key] = report.counts.get(key, 0) + 1
        if outcome != "skipped-race":
            _audit({"ts": today, "slug": note.slug,
                    "company": note.fm.get("company", ""), "role": note.fm.get("role", ""),
                    "url": note.fm.get("url", ""), "stage": "classify",
                    "decision": decision, "reason": reason, "score": 0})
```

Replace the judge-pass write/count/audit block:

```python
            if dry_run:
                outcome = "skipped"
            else:
                try:
                    outcome = apply_verdict(vault, note, verdict, dossier)
                except VaultConflict as e:
                    # Symmetric with the classify-pass site above.
                    report.failures.append(f"apply {note.ref}: {e}")
                    continue
            key = "skipped" if outcome == "skipped" else _status.normalize(
                verdict.get("verdict", ""))
            report.counts[key] = report.counts.get(key, 0) + 1
            _audit({"ts": today, "slug": verdict["lead_id"],
                    "company": note.fm.get("company", ""),
                    "role": note.fm.get("role", ""), "url": note.fm.get("url", ""),
                    "stage": "judge", "verdict": verdict.get("verdict"),
                    "reason": verdict.get("fit_reasoning", ""),
                    "score": verdict.get("relevance_score", 0)})
```

with:

```python
            if dry_run:
                outcome = "skipped"
            else:
                try:
                    outcome = apply_verdict(vault, note, verdict, dossier)
                except VaultConflict as e:
                    # Symmetric with the classify-pass site above.
                    report.failures.append(f"apply {note.ref}: {e}")
                    continue
            if outcome == "skipped-race":
                # Symmetric with the classify-pass site above.
                report.failures.append(
                    f"apply-race {note.ref}: status changed before the verdict write "
                    "landed (or the value was already current)")
            key = "skipped" if outcome in ("skipped", "skipped-race") else _status.normalize(
                verdict.get("verdict", ""))
            report.counts[key] = report.counts.get(key, 0) + 1
            if outcome != "skipped-race":
                _audit({"ts": today, "slug": verdict["lead_id"],
                        "company": note.fm.get("company", ""),
                        "role": note.fm.get("role", ""), "url": note.fm.get("url", ""),
                        "stage": "judge", "verdict": verdict.get("verdict"),
                        "reason": verdict.get("fit_reasoning", ""),
                        "score": verdict.get("relevance_score", 0)})
```

Also update the module docstring's first line to mention the resolution step:

```python
"""Triage orchestrator: load -> classify -> enrich -> judge -> apply -> audit.

Deterministic classify resolves the obvious cases for free (no dossier, no LLM);
only the kept, ambiguous leads are enriched and judged. dry_run computes and
reports but writes nothing (no vault edits, no audit lines). no_llm runs classify
+ apply + audit only. Every lead already in the application lifecycle is skipped
by the apply layer, so triage never clobbers human state.
"""
```

becomes:

```python
"""Triage orchestrator: load -> classify -> resolve -> enrich -> judge -> apply -> audit.

Deterministic classify resolves the obvious cases for free (no dossier, no LLM).
A lead classify() leaves at blank-company needs_review gets ONE resolution
attempt (#109): a free URL-pattern tier 1, then -- opt-in via
cfg.company_resolve_fetch, independent of no_llm -- a real, no-LLM page-visit
tier 2, reusing the same fetch/cache the enrich pass needs anyway. Only the
kept, ambiguous leads are enriched and judged. dry_run computes and reports but
writes nothing (no vault edits, no audit lines) -- resolution's COMPUTATION
still runs under dry_run, only its write is skipped. no_llm runs classify +
(tier-1-only) resolve + apply + audit only. Every lead already in the
application lifecycle is skipped by the apply layer, so triage never clobbers
human state.
"""
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_triage_engine.py -v`
Expected: PASS, every test in the file — the twelve new ones and all seven pre-existing ones
(`test_pipeline_classifies_and_judges`, `test_no_llm_skips_judge`,
`test_judge_prompt_is_composed_from_vault_criteria`, `test_dry_run_writes_nothing`,
`test_triage_classify_conflict_is_counted_and_batch_continues`,
`test_triage_judge_conflict_is_counted_and_batch_continues`).

- [ ] **Step 5: Wire `Sluice.triage()`**

In `sluice/core/app.py`, the `triage` method's body:

```python
        from sluice.triage.audit import AuditLog
        from sluice.triage.config import load_triage_config
        from sluice.triage.engine import run as _triage_run
        tcfg = load_triage_config()
        # `tcfg.audit_jsonl`, not a second $TRIAGE_AUDIT read: this key was DEAD --
        # declared on TriageConfig and read by nothing, because this line carried its
        # own env read and its own literal default, so setting it in YAML changed
        # nothing and said nothing. The loader resolves it (env -> config key -> the
        # per-system state root), and that one value is what everything uses.
        audit = AuditLog(tcfg.audit_jsonl)
        backend = None if no_llm else self.backend(
            backend_role, primary_name=tcfg.primary_backend,
            primary_model=tcfg.claude_max_model, effort=tcfg.claude_max_effort,
            host=tcfg.claude_max_host, claude_path=tcfg.claude_max_path,
            fallback_name=tcfg.fallback_backend, fallback_model=tcfg.cheap_model)
        cache = self.dossier_cache(self._dossier_dir(), tcfg.ttl_days)
        return _triage_run(self.store(), tcfg, backend, cache, audit,
                           statuses=tuple(statuses), limit=limit,
                           dry_run=dry_run, no_llm=no_llm)
```

becomes:

```python
        from sluice.ingest import sources
        from sluice.triage.audit import AuditLog
        from sluice.triage.config import load_triage_config
        from sluice.triage.engine import run as _triage_run
        tcfg = load_triage_config()
        # `tcfg.audit_jsonl`, not a second $TRIAGE_AUDIT read: this key was DEAD --
        # declared on TriageConfig and read by nothing, because this line carried its
        # own env read and its own literal default, so setting it in YAML changed
        # nothing and said nothing. The loader resolves it (env -> config key -> the
        # per-system state root), and that one value is what everything uses.
        audit = AuditLog(tcfg.audit_jsonl)
        backend = None if no_llm else self.backend(
            backend_role, primary_name=tcfg.primary_backend,
            primary_model=tcfg.claude_max_model, effort=tcfg.claude_max_effort,
            host=tcfg.claude_max_host, claude_path=tcfg.claude_max_path,
            fallback_name=tcfg.fallback_backend, fallback_model=tcfg.cheap_model)
        cache = self.dossier_cache(self._dossier_dir(), tcfg.ttl_days)
        return _triage_run(self.store(), tcfg, backend, cache, audit,
                           statuses=tuple(statuses), limit=limit,
                           dry_run=dry_run, no_llm=no_llm, get_source=sources.get)
```

Also add a sentence to the method's docstring:

```python
    def triage(self, *, statuses=("new", "research"), limit=None, dry_run=False,
               no_llm=False, backend_role="auto"):
        """Run the triage sub-app end to end: classify, dossier-enrich the kept leads,
        judge them, and write the audit trail. `no_llm` skips backend construction
        entirely (`triage()`'s deterministic classify-only path), preserving the
        offline guarantee `--no-llm` has always given `sluice triage run`.

        The primary/fallback field mapping here (`claude_max_*` for primary,
        `cheap_model` for fallback) is triage's own config shape -- other sub-apps
        (cv, apply) have their own `*Config` with their own field names, so this
        mapping is NOT shared and belongs in this method, not in `Sluice.backend`."""
```

becomes:

```python
    def triage(self, *, statuses=("new", "research"), limit=None, dry_run=False,
               no_llm=False, backend_role="auto"):
        """Run the triage sub-app end to end: classify, dossier-enrich the kept leads,
        judge them, and write the audit trail. `no_llm` skips backend construction
        entirely (`triage()`'s deterministic classify-only path), preserving the
        offline guarantee `--no-llm` has always given `sluice triage run`.

        The primary/fallback field mapping here (`claude_max_*` for primary,
        `cheap_model` for fallback) is triage's own config shape -- other sub-apps
        (cv, apply) have their own `*Config` with their own field names, so this
        mapping is NOT shared and belongs in this method, not in `Sluice.backend`.

        Also threads `sources.get` (#109) into `triage.engine.run` as `get_source`,
        the same lazy, inside-the-method import `ingest()` already uses for
        `ingest.base`/`ingest.engine` -- `triage/` itself never imports
        `sluice.ingest` directly."""
```

- [ ] **Step 6: Write the failing `Sluice.triage()` wiring test**

Add to `tests/test_app_operations.py`, near `test_triage_threads_the_triage_config_into_the_backend`:

```python
def test_triage_threads_get_source_into_engine_run(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "d"))
    app = Sluice(Config())
    seen = {}
    def fake_run(vault, cfg, backend, cache, audit, **kw):
        seen.update(kw)
        from sluice.triage.engine import TriageReport
        return TriageReport()
    monkeypatch.setattr("sluice.triage.engine.run", fake_run)
    app.triage(no_llm=True)
    from sluice.ingest import sources
    assert seen["get_source"] is sources.get
```

- [ ] **Step 7: Run it to verify it fails**

Run: `python -m pytest tests/test_app_operations.py -v -k threads_get_source`
Expected: FAIL — `KeyError: 'get_source'` (today's `Sluice.triage()` never passes it).

- [ ] **Step 8: Run it to verify it passes**

Run: `python -m pytest tests/test_app_operations.py -v -k threads_get_source`
Expected: PASS.

- [ ] **Step 9: Write and pass the `classify()` signature regression test**

Add to `tests/test_classify.py`:

```python
def test_classify_signature_never_gains_a_side_effecting_dependency():
    import inspect
    params = set(inspect.signature(classify).parameters)
    assert params == {"lead", "cfg"}, (
        "classify() must stay pure -- no dossier_cache, sources, or fetcher "
        "parameter, per its own docstring's no-dossier/no-LLM contract")
```

Run: `python -m pytest tests/test_classify.py -v -k signature`
Expected: PASS immediately (this task never touches `classify.py`'s signature — the test pins
that invariant against any FUTURE change, matching the way
`test_a_renderer_without_precheck_is_not_gated_by_another_renderers_grammar` protects the
renderer seam's optionality).

- [ ] **Step 10: Run the full existing suite to catch any other `run()`/`triage()` caller**

Run: `python -m pytest -v -k "triage or app_operations or dossier_guard or apply"`
Expected: PASS across `tests/test_triage_engine.py`, `tests/test_triage_config.py`,
`tests/test_app_operations.py`, `tests/test_config_paths.py`, `tests/test_dossier_guard.py`,
`tests/test_doctor.py`, `tests/test_apply.py`.

- [ ] **Step 11: Run the ENTIRE suite once**

Run: `python -m pytest`
Expected: PASS, no regressions anywhere.

- [ ] **Step 12: Mutation-witness**

Run `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` first.

| Mutant | Must redden |
|---|---|
| Move the resolve attempt to run before `classify()` (or drop the `decision == "needs_review" and not company` guard) | `test_resolution_never_attempted_for_a_lead_classify_would_reject_anyway` |
| Delete the `if not dry_run` guard around the company write | `test_dry_run_resolution_keep_count_is_accurate_but_reject_bucket_is_not` (nothing gets written under dry_run) |
| Delete `require_status=` from the company write | `test_company_write_require_status_abstain_leaves_the_original_needs_review_decision` |
| Change `key = "skipped" if outcome in ("skipped", "skipped-race")` back to `outcome == "skipped"` | `test_apply_classification_race_produces_no_persisted_audit_entry` (miscounts into dismiss/needs_review) |
| Delete the `if outcome != "skipped-race": _audit(...)` guard (audit unconditionally) | `test_apply_classification_race_produces_no_persisted_audit_entry` / `test_apply_verdict_race_produces_no_persisted_audit_entry` |
| Delete `cache_key`'s url-hash preference (regresses Task 1, but only OBSERVABLE at this integration layer) | `test_tier2_resolution_and_the_later_judge_share_one_fetch` |
| Swap `get_source=sources.get` for `get_source=None` in `Sluice.triage()` | `test_triage_threads_get_source_into_engine_run` |

- [ ] **Step 13: Commit**

```bash
git add sluice/triage/engine.py sluice/core/app.py tests/test_triage_engine.py \
       tests/test_classify.py tests/test_app_operations.py
git commit -m "$(cat <<'EOF'
feat(triage): wire blank-company resolution into the classify pass, thread get_source (#109)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Task 8: Docs — `docs/ARCHITECTURE.md`

**Files:**
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the `dossier.py` one-line module description**

Find:

```markdown
- `resilience.py`: retry-with-backoff, hard timeout, and rate-limit
  precheck helpers that wrap each source's I/O.
- `health.py`, `dossier.py`, `leads.py`, `log.py`, `relevance.py`: health
  reporting, per-lead dossier assembly, the source-agnostic `Lead` model,
  logging, and the relevance gate.
```

Replace with:

```markdown
- `resilience.py`: retry-with-backoff, hard timeout, and rate-limit
  precheck helpers that wrap each source's I/O.
- `health.py`, `dossier.py`, `leads.py`, `log.py`, `relevance.py`: health
  reporting, per-lead dossier assembly (`DossierCache`, keyed on a stable url
  hash rather than the company/role slug so a #109 mid-run company mutation
  does not double-fetch; also captures `page_title`/`structured_data` for
  triage's tier-2 company resolution, excluded from what `slim()` sends the
  judge), the source-agnostic `Lead` model, logging, and the relevance gate.
```

- [ ] **Step 2: Add a caveat to the "five sub-apps" narrative's triage paragraph**

Find:

```markdown
2. **triage** (`sluice/triage/`): `classify.py` resolves obvious cases
   deterministically, for free; only kept, ambiguous leads are enriched
   and sent to an LLM judge (`judge.py`, `prompt.py`, over `core.backends`).
   `apply.py` writes verdicts back, skipping any lead already in the
   application lifecycle; `audit.py` logs every decision.
```

Replace with:

```markdown
2. **triage** (`sluice/triage/`): `classify.py` resolves obvious cases
   deterministically, for free; only kept, ambiguous leads are enriched
   and sent to an LLM judge (`judge.py`, `prompt.py`, over `core.backends`).
   A lead classify() leaves at blank-company `needs_review` gets one
   resolution attempt (`resolve.py`, #109) before that -- a free
   URL-pattern tier 1, then an opt-in, no-LLM page-visit tier 2 -- so
   "for free" no longer describes the WHOLE classify pass unconditionally:
   a blank-company lead can trigger a real (still non-LLM) page visit when
   `triage.company_resolve_fetch` is on. `apply.py` writes verdicts back,
   skipping any lead already in the application lifecycle (its own writes,
   and the new resolution write, are all `require_status`-guarded against
   a lead entering that lifecycle mid-run); `audit.py` logs every decision.
```

- [ ] **Step 3: Document the dossier fetch closure and the `sources.get` threading**

In the "Adapter-selector seams" section, find the `sources` bullet:

```markdown
- **fetcher**: `sluice/fetchers/`, selected by `fetcher:` (default `camofox`).
  Implementations: `camofox` (the headless-browser HTTP server).
- **sources**: `ingest/sources/`, the registry all of the above are modelled on.
```

Replace with:

```markdown
- **fetcher**: `sluice/fetchers/`, selected by `fetcher:` (default `camofox`).
  Implementations: `camofox` (the headless-browser HTTP server). The dossier
  fetch closure built from it (`Sluice.dossier_cache`) reads
  `document.body.innerText` for the JD, and -- for triage's tier-2 company
  resolution (#109) -- also `document.title` and any
  `script[type="application/ld+json"]` tag's text content, in the same
  already-open tab. The JD read is a hard refusal on an unreadable body; the
  two resolution-only captures are best-effort and degrade to `""` instead.
- **sources**: `ingest/sources/`, the registry all of the above are modelled on.
  A source may optionally implement `company_from_url(url) -> str | None`
  (#109), the same optional-member shape as `Store.preflight`/
  `Renderer.precheck` above -- `Sluice.triage()` threads `sources.get` into
  `triage.engine.run` as `get_source`, the same lazy inside-the-method import
  `ingest()` already uses; `triage/` itself never imports `sluice.ingest`
  directly.
```

- [ ] **Step 4: Verify the edits render sensibly**

Run: `grep -n "109" docs/ARCHITECTURE.md` and read each hit in context to confirm no duplicated or
contradictory sentence was introduced.

- [ ] **Step 5: Regenerate the AI-tool outputs and confirm no drift**

Run: `npm ci --ignore-scripts && npm run rulesync` (only if `.rulesync/rules/CLAUDE.md` itself was
touched — it was not by this task, so this step is a no-op verification):

Run: `git status --short` and confirm `CLAUDE.md`/`AGENTS.md`/`.claude/` show no unexpected diff
(they are gitignored and untouched by a docs-only change to `docs/ARCHITECTURE.md`).

- [ ] **Step 6: Commit**

```bash
git add docs/ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
docs: document #109 company resolution in ARCHITECTURE.md

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
EOF
)"
```

---

## Final verification (whole plan)

- [ ] Run the full Definition of Done from the spec:

```bash
ruff check sluice tests
python -m pytest
```

Expected: both clean.

- [ ] Run coverage the way CI reports it (report-only, no gate):

```bash
python -m pytest --cov
```

- [ ] Confirm the neutrality convention held: no real employer/location/contact data was
  introduced. Every fixture URL in this plan uses the `example.invalid`/`example-co`/`Example Co`
  family.

- [ ] Follow the project's standing cadence before pushing: run `/review-pr` BEFORE pushing the
  branch (not after opening the PR), and fold every finding in rather than deferring — per project
  memory's standing rules on this repo.

---

## Changelog

**2026-08-10:** Initial plan, produced via `superpowers:writing-plans` from the approved,
three-times-`/review-plan`'d design spec `docs/superpowers/specs/2026-08-10-triage-company-resolution-design.md`.
