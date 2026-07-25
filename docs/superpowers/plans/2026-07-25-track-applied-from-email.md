# Advance a lead to `applied` from its confirmation email (#10) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an application-confirmation receipt arrives, advance the matching `shortlist` lead to `applied` — matched deterministically by domain, auto-advancing only on an unambiguous proof-grade match, proposing otherwise.

**Architecture:** The LLM classifies a message as a `receipt`; a new pure module `sluice/track/receipt.py` decides *which* shortlist lead it belongs to by full-host domain match (never a fuzzy name match), refusing on ambiguity. The match runs in `engine.run()` (where the raw message is in scope) and writes its result onto the `Event`; `reconcile` advances `shortlist → applied` via `can_apply` on a proof-grade match, or proposes (dead-letter) on a corroborated/ambiguous match.

**Tech Stack:** Python 3.12+ stdlib only (`urllib.parse`, `re`, `dataclasses`); pytest + faker for tests. No new dependency.

Design spec: `docs/superpowers/specs/2026-07-25-track-applied-from-email-design.md` (converged through two `/review-plan` rounds).

## Global Constraints

- **Standard library only in `sluice/`.** `urllib.parse`, `re`, `dataclasses` are fine; no new runtime dependency.
- **Never-regress.** `shortlist → applied` is the `can_apply` transition. A receipt must never advance a lead out of `shortlist` (never pull `interview`/`offer`/terminal backward). `can_apply` returns True only for `shortlist`.
- **Never-clobber.** Status via surgical `Vault.update_fields` (named keys only); evidence via `Vault.append_body_section` (append, idempotent by tag). Never reuse `_advance` for a receipt (it stamps `interview_*`).
- **No personal data in `sluice/` or `tests/`.** Fixture company *hosts and names* use the RFC-reserved `example.com` / `example.invalid` family. Real ATS relay hosts (`greenhouse.io`, `lever.co`, …) may be named — they already ship in `ats_relay_domains`.
- **Config-driven.** New tunable `auto_apply_min` goes in `TrackConfig` *and* `sluice.yaml.example`.
- **Conventional Commits.** `feat(track): …`, `test(track): …`, `docs(track): …`.
- **Tests assert behaviour, offline.** No network/Camofox/backend; fake the backend with the existing `FakeBackend`. The false-positive guards assert *absence of a write*, not merely a return value.
- **`.rulesync/` is canonical** — `CLAUDE.md` is generated from it. The doc update (Task 7) edits `.rulesync/rules/CLAUDE.md` and regenerates; this is **user-approved** for this PR.

Branch: `feat/track-applied-from-email` (already created; the design commits are on it).

---

### Task 1: `can_transition` in `core/status.py` + docstrings

**Files:**
- Modify: `sluice/core/status.py` (add `can_transition`; update module docstring and the `can_apply` function docstring)
- Test: `tests/test_core_status_apply.py`

**Interfaces:**
- Produces: `can_transition(current: str, target: str) -> bool` — dispatches `applied`→`can_apply`, else→`can_advance`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_core_status_apply.py`:

```python
from sluice.core import status as S


def test_can_transition_routes_applied_through_can_apply():
    # shortlist -> applied is legal (can_apply), which can_advance would reject.
    assert S.can_transition("shortlist", "applied") is True
    assert S.can_advance("shortlist", "applied") is False  # the reason can_transition exists


def test_can_transition_refuses_applied_from_non_shortlist():
    for src in ("interview", "offer", "applied", "rejected", "new"):
        assert S.can_transition(src, "applied") is False


def test_can_transition_delegates_non_applied_to_can_advance():
    # A non-applied target routes to can_advance unchanged.
    assert S.can_transition("applied", "interview") is True
    assert S.can_transition("offer", "phone_screen") is False  # backward on the ladder
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_core_status_apply.py -k can_transition -v`
Expected: FAIL — `AttributeError: module 'sluice.core.status' has no attribute 'can_transition'`

- [ ] **Step 3: Add `can_transition` to `sluice/core/status.py`** (after `can_apply`, around line 49):

```python
def can_transition(current: str, target: str) -> bool:
    """Route a requested status change to the correct never-regress predicate.
    `applied` is reachable only via `can_apply` (shortlist -> applied); every other
    target is an on-ladder move governed by `can_advance`. This is the shared entry
    point `track confirm` uses, since it accepts an arbitrary `--to` target; the
    reconcile receipt branch calls `can_apply` directly because it already knows the
    target is `applied`. Routing lives here because status.py owns the ladder."""
    if normalize(target) == "applied":
        return can_apply(current)
    return can_advance(current, target)
```

- [ ] **Step 4: Update the two docstrings** for the receipt actor.

Replace the `can_apply` docstring body (`sluice/core/status.py:45-48`) so it is no longer apply-exclusive:

```python
def can_apply(status: str) -> bool:
    """True iff the lead is in the only state `shortlist -> applied` may start from.
    Both apply (on send) and track (on a confirmation receipt, via `can_transition`)
    advance shortlist -> applied through this predicate; every other state (including
    every APPLICATION_OWNED state) is refused."""
    return normalize(status) == "shortlist"
```

And extend the module docstring (`sluice/core/status.py:1-10`) — add one sentence to the paragraph describing the two lifecycles:

```
... triage must never touch a lead once it has entered that lifecycle. The single
exception into the application lifecycle is `shortlist -> applied`, made by apply (on
send) and by track (on a confirmation receipt); both go through `can_apply`.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_core_status_apply.py -v && ruff check sluice tests`
Expected: PASS (all), ruff clean.

- [ ] **Step 6: Commit**

```bash
git add sluice/core/status.py tests/test_core_status_apply.py
git commit -m "feat(status): add can_transition routing shortlist->applied through can_apply

track advances shortlist->applied on a confirmation receipt (#10), the same
transition apply makes on send; can_transition is the shared entry point for
an arbitrary --to target (track confirm), routing applied->can_apply else
can_advance. Docstrings updated to record track as a second actor.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Q7xXM4KpehkJpfkw4zpFnG"
```

---

### Task 2: `auto_apply_min` config + `sluice.yaml.example`

**Files:**
- Modify: `sluice/track/config.py` (add `auto_apply_min`)
- Modify: `sluice.yaml.example` (document `auto_apply_min` and `ats_relay_domains` semantics)
- Test: `tests/test_track_config.py`

**Interfaces:**
- Produces: `TrackConfig.auto_apply_min: float = 0.75` (read by the reconcile receipt branch in Task 5).

- [ ] **Step 1: Write the failing test** — append to `tests/test_track_config.py`:

```python
def test_auto_apply_min_default_and_override(tmp_path):
    from sluice.track.config import TrackConfig, load_track_config
    assert TrackConfig().auto_apply_min == 0.75
    cfg_file = tmp_path / "s.yaml"
    cfg_file.write_text("track:\n  auto_apply_min: 0.9\n")
    assert load_track_config(str(cfg_file)).auto_apply_min == 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_track_config.py -k auto_apply_min -v`
Expected: FAIL — `AttributeError: 'TrackConfig' object has no attribute 'auto_apply_min'`

- [ ] **Step 3: Add the field** in `sluice/track/config.py`, in the `TrackConfig` dataclass right after `auto_reject_min` (line 39):

```python
    auto_apply_min: float = 0.75              # min receipt-classification confidence to auto-advance shortlist->applied on a domain-PROOF match
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_track_config.py -v`
Expected: PASS.

- [ ] **Step 5: Document in `sluice.yaml.example`** — find the `track:` block and add, alongside the other track knobs:

```yaml
  # Minimum confidence (0..1) for the LLM's "this is an application receipt"
  # classification to AUTO-advance a shortlist lead to `applied` on a domain-PROOF
  # match. A corroborated (ATS-relay + company-in-body) or ambiguous match always
  # proposes for `sluice track confirm`, regardless of this value.
  auto_apply_min: 0.75
  # ats_relay_domains maps an applicant-tracking-system relay domain to a short
  # label. It is a SAFETY DENYLIST, not a preference gate: a receipt sent from one
  # of these shared multi-tenant hosts is never treated as domain-PROOF of a
  # specific company (it hosts many), only as corroboration when the company is also
  # named in the body. The default is non-empty by design; emptying it makes the
  # proof tier MORE permissive, not less. Add your own ATSs here.
  # ats_relay_domains:
  #   greenhouse.io: greenhouse
  #   lever.co: lever
```

- [ ] **Step 6: Commit**

```bash
git add sluice/track/config.py sluice.yaml.example tests/test_track_config.py
git commit -m "feat(track): add auto_apply_min receipt-confidence floor (#10)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Q7xXM4KpehkJpfkw4zpFnG"
```

---

### Task 3: `match_receipt` pure matcher (`track/receipt.py`)

**Files:**
- Create: `sluice/track/receipt.py`
- Test: `tests/test_track_receipt.py`

**Interfaces:**
- Produces:
  - `ReceiptMatch` dataclass: `lead_slug: str | None`, `tier: str` (`"proof"|"corroborated"|"none"`), `candidates: list[str]`.
  - `match_receipt(msg: dict, shortlist_leads, ats_relay_domains: dict) -> ReceiptMatch`. `shortlist_leads` is an iterable of note objects with `.slug` and `.fm` (a dict with `url`, `company`). `msg` is the track message dict: `{"headers": {"from", "subject"}, "body_text": str}`.
- Consumes: `sluice.core.leads._norm_tokens` (the shared tokenizer — reviewers endorsed reuse over reinvention).

- [ ] **Step 1: Write the failing test** — create `tests/test_track_receipt.py`:

```python
from types import SimpleNamespace
from sluice.track.receipt import match_receipt, ReceiptMatch

ATS = {"greenhouse.io": "greenhouse", "lever.co": "lever"}


def _lead(slug, url, company="Example"):
    return SimpleNamespace(slug=slug, fm={"url": url, "company": company})


def _msg(frm="", subject="", body=""):
    return {"headers": {"from": frm, "subject": subject}, "body_text": body}


def test_proof_exact_host_single_lead():
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm="jobs@example.com", subject="Thanks for applying"), leads, ATS)
    assert m == ReceiptMatch("Example - Analyst", "proof", [])


def test_proof_subdomain_of_lead_host():
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm="no-reply@careers.example.com"), leads, ATS)
    assert m.tier == "proof" and m.lead_slug == "Example - Analyst"


def test_proof_via_apply_link_in_body():
    leads = [_lead("Example - Analyst", "https://example.com/careers/1")]
    m = match_receipt(_msg(frm="mailer@sendgrid.invalid",
                           body="View your application at https://example.com/status"), leads, ATS)
    assert m.tier == "proof"


def test_ambiguous_two_leads_same_host_proposes_neither():
    leads = [_lead("Example - Analyst", "https://example.com/a"),
             _lead("Example - Manager", "https://example.com/b")]
    m = match_receipt(_msg(frm="jobs@example.com"), leads, ATS)
    assert m.lead_slug is None and m.tier == "corroborated"
    assert sorted(m.candidates) == ["Example - Analyst", "Example - Manager"]


def test_corroborated_ats_plus_company_in_body():
    leads = [_lead("Example - Analyst", "https://boards.greenhouse.io/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm="no-reply@greenhouse.io",
                           body="Example has received your application."), leads, ATS)
    assert m.tier == "corroborated" and m.lead_slug == "Example - Analyst"


def test_ats_without_company_in_body_no_match():
    leads = [_lead("Example - Analyst", "https://boards.greenhouse.io/example/jobs/1", company="Example")]
    m = match_receipt(_msg(frm="no-reply@greenhouse.io", body="Your application was received."), leads, ATS)
    assert m == ReceiptMatch(None, "none", [])


def test_none_traps():
    lead = [_lead("Example - Analyst", "https://example.com/careers/1")]
    # name-only mention from an unrelated service
    assert match_receipt(_msg(frm="digest@indeed.invalid", body="jobs at Example"), lead, ATS).tier == "none"
    for host in ("evilexample.com", "example.com.attacker.invalid", "notexample.com"):
        assert match_receipt(_msg(frm=f"x@{host}"), lead, ATS).tier == "none", host
    # sibling subdomain of a DIFFERENT registrable domain
    assert match_receipt(_msg(frm="x@careers.other.invalid"), lead, ATS).tier == "none"


def test_multipart_tld_does_not_collapse():
    # bigco.co.uk and random.co.uk must NOT match on a shared co.uk suffix.
    leads = [_lead("Bigco - Analyst", "https://bigco.co.uk/careers/1")]
    assert match_receipt(_msg(frm="noreply@random.co.uk"), leads, ATS).tier == "none"


def test_url_less_lead_never_matches():
    leads = [_lead("Example - Analyst", "")]
    assert match_receipt(_msg(frm="jobs@example.com"), leads, ATS).tier == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_track_receipt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sluice.track.receipt'`

- [ ] **Step 3: Create `sluice/track/receipt.py`:**

```python
"""Deterministic receipt -> lead matching. Pure: no I/O, so it is tested offline.
The LLM decides a message IS an application receipt; this module decides WHICH
shortlist lead it belongs to, by domain -- never by a fuzzy name match. A wrong or
arbitrary advance silently suppresses a real application, so the two failure modes
this guards are (a) matching a name-only mention and (b) advancing an AMBIGUOUS
match; both resolve to `none`/propose, never a proof advance (#10)."""
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from sluice.core.leads import _norm_tokens

# A permissive URL scrape of the body; the host is what matters, not the full URL.
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
_EMAIL_DOMAIN_RE = re.compile(r"[\w.+-]+@([\w.-]+)")


@dataclass
class ReceiptMatch:
    lead_slug: "str | None" = None
    tier: str = "none"                 # proof | corroborated | none
    candidates: list = field(default_factory=list)


def _host(value: str) -> str:
    """Host of a URL or a bare domain: lowercased, leading www. stripped. Empty when
    nothing parseable -- a url-less lead thus never matches (abstain, not match-all)."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "://" not in v:
        v = "//" + v                   # let urlparse read a bare host/domain
    host = urlparse(v).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _sender_host(msg) -> str:
    m = _EMAIL_DOMAIN_RE.search(msg.get("headers", {}).get("from", "") or "")
    return _host(m.group(1)) if m else ""


def _link_hosts(msg) -> set:
    return {h for h in (_host(u) for u in _URL_RE.findall(msg.get("body_text", "") or "")) if h}


def _hosts_match(a: str, b: str) -> bool:
    """Full-host equality or a subdomain relationship EITHER direction. Bidirectional so
    a bare corporate domain matches a jobs subdomain and vice versa; on FULL hosts, so
    bigco.co.uk and random.co.uk never collapse to a shared co.uk suffix."""
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _is_ats(host: str, ats) -> bool:
    return any(host == k or host.endswith("." + k) for k in (ats or {}))


def match_receipt(msg, shortlist_leads, ats_relay_domains) -> ReceiptMatch:
    ats = ats_relay_domains or {}
    receipt_hosts = {h for h in ({_sender_host(msg)} | _link_hosts(msg)) if h}
    if not receipt_hosts:
        return ReceiptMatch(None, "none", [])
    tokens = _norm_tokens(
        msg.get("headers", {}).get("subject", "") + " " + (msg.get("body_text", "") or ""))
    from_ats = any(_is_ats(r, ats) for r in receipt_hosts)
    proof, corrob = [], []
    for lead in shortlist_leads:
        lead_host = _host(lead.fm.get("url", ""))
        # proof: a full-host match to the lead's OWN (non-ATS) domain.
        if lead_host and not _is_ats(lead_host, ats) \
                and any(_hosts_match(r, lead_host) and not _is_ats(r, ats) for r in receipt_hosts):
            proof.append(lead.slug)
            continue
        # corroborated: from an ATS relay host, with the company named in the body.
        if from_ats:
            company = _norm_tokens(lead.fm.get("company", ""))
            if company and company <= tokens:
                corrob.append(lead.slug)
    if len(proof) == 1:
        return ReceiptMatch(proof[0], "proof", [])
    if len(proof) > 1:                                 # ambiguous proof -> propose, never advance
        return ReceiptMatch(None, "corroborated", sorted(proof))
    if len(corrob) == 1:
        return ReceiptMatch(corrob[0], "corroborated", [])
    if len(corrob) > 1:
        return ReceiptMatch(None, "corroborated", sorted(corrob))
    return ReceiptMatch(None, "none", [])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_track_receipt.py -v && ruff check sluice tests`
Expected: PASS (all 9), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add sluice/track/receipt.py tests/test_track_receipt.py
git commit -m "feat(track): pure domain-anchored receipt->lead matcher (#10)

Full-host matching (equality/subdomain, bidirectional) so multi-part TLDs never
collapse; refuse-on-ambiguity (>1 proof lead -> propose) so a receipt never
advances an arbitrary lead; ATS relay hosts are corroboration-only, never proof.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Q7xXM4KpehkJpfkw4zpFnG"
```

---

### Task 4: classify — `receipt` type + `Event` fields

**Files:**
- Modify: `sluice/track/classify.py` (add `receipt` to `_TYPES`; add `receipt_tier`, `sender`, `subject` to `Event`; populate `sender`/`subject`; skip name-resolution for receipts)
- Test: `tests/test_track_classify.py`

**Interfaces:**
- Produces: `Event` gains `receipt_tier: str | None = None`, `sender: str = ""`, `subject: str = ""`. `classify` types a receipt as `receipt` and leaves `lead_slug=None`/`candidates=[]` (the engine fills them); it populates `sender`/`subject` from the message headers.

- [ ] **Step 1: Write the failing test** — append to `tests/test_track_classify.py`:

```python
def test_receipt_typed_and_llm_lead_ignored():
    # A receipt: the LLM may still name a lead, but classify must NOT resolve it --
    # the deterministic matcher (engine) owns lead resolution for receipts.
    leads = [_lead("Example", "Analyst")]
    be = FakeBackend(json.dumps({"lead": "Example", "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    ev = C.classify(_msg(frm="jobs@example.com", subject="Thanks for applying"),
                    leads, be, TrackConfig(), ics=None)
    assert ev.type == "receipt"
    assert ev.lead_slug is None and ev.candidates == []      # NOT resolved by name
    assert ev.sender == "jobs@example.com" and ev.subject == "Thanks for applying"
    assert ev.receipt_tier is None                            # engine sets this later
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_track_classify.py -k receipt -v`
Expected: FAIL — `ev.type` is `not_job` (`receipt` not in `_TYPES`), and/or `AttributeError` on `sender`.

- [ ] **Step 3: Edit `sluice/track/classify.py`.**

Add `receipt` to `_TYPES` (line 14):

```python
_TYPES = {"phone_screen", "interview", "rejection", "offer", "update", "receipt", "not_job"}
```

Add the three fields to the `Event` dataclass (after `summary`, line 29):

```python
    receipt_tier: "str | None" = None   # set by engine.run for a receipt: proof|corroborated|none
    sender: str = ""                    # raw From header, for receipt evidence
    subject: str = ""                   # raw Subject header, for receipt evidence
```

In `classify()`, after the successful parse and before `ev.lead_slug, ev.candidates = _resolve_lead(...)` (line 87), populate sender/subject and skip name-resolution for receipts:

```python
        h = msg.get("headers", {})
        ev.sender = h.get("from", "")
        ev.subject = h.get("subject", "")
        if ev.type != "receipt":
            ev.lead_slug, ev.candidates = _resolve_lead(data.get("lead"), leads)
        # else: leave lead_slug=None/candidates=[]; engine.run resolves receipts by domain.
```

(Replace the existing single `ev.lead_slug, ev.candidates = _resolve_lead(data.get("lead"), leads)` line with the block above.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_track_classify.py -v`
Expected: PASS (existing tests + the new one).

- [ ] **Step 5: Commit**

```bash
git add sluice/track/classify.py tests/test_track_classify.py
git commit -m "feat(track): classify the receipt type; carry sender/subject/receipt_tier on Event (#10)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Q7xXM4KpehkJpfkw4zpFnG"
```

---

### Task 5: reconcile receipt branch + evidence

**Files:**
- Modify: `sluice/track/reconcile.py` (add `shortlist_by_slug` kw param; receipt branch before the None-guard; `_stamp_receipt`)
- Test: `tests/test_track_reconcile.py`

**Interfaces:**
- Consumes: `Event.receipt_tier`/`.lead_slug`/`.candidates`/`.sender`/`.subject`/`.confidence` (Task 4); `_status.can_apply` (Task 1); `TrackConfig.auto_apply_min` (Task 2).
- Produces: `reconcile(event, note_by_slug, vault, cfg, client, dry_run=False, *, shortlist_by_slug=None)` — new keyword-only param, so existing positional callers are unaffected.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_track_reconcile.py`. Add a url-carrying vault helper at the top of the file (after `_vault_with`):

```python
def _shortlist_with(slug, url, company="Example", status="shortlist"):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / f"{slug}.md").write_text(
        f'---\ncompany: "{company}"\nrole: "Analyst"\nurl: "{url}"\nstatus: {status}\n---\n\nBODY\n')
    v = Vault(root)
    note = [n for n in v.read_leads() if n.slug == slug][0]
    return v, {slug: note}, str(leads / f"{slug}.md")


def _receipt_ev(tier, slug, sender="jobs@example.com", subject="Thanks for applying", conf=0.9):
    return Event(type="receipt", receipt_tier=tier, lead_slug=slug, confidence=conf,
                 sender=sender, subject=subject, summary="application received")


def test_receipt_proof_advances_shortlist_to_applied():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.action == "applied" and res.status_to == "applied"
    text = pathlib.Path(path).read_text()
    assert "status: applied" in text and "## Application receipt" in text


def test_receipt_below_confidence_floor_proposes():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst", conf=0.5)  # below auto_apply_min
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.action == "proposed" and "status: shortlist" in pathlib.Path(path).read_text()


def test_receipt_corroborated_proposes_not_advances():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("corroborated", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.action == "proposed"
    text = pathlib.Path(path).read_text()
    assert "status: shortlist" in text and "## Application receipt" not in text  # absence-of-write


def test_receipt_ambiguous_proposes_neither():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = Event(type="receipt", receipt_tier="corroborated", lead_slug=None,
               candidates=["Example - Analyst", "Example - Manager"], confidence=0.9)
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.action == "proposed" and "status: shortlist" in pathlib.Path(path).read_text()


def test_receipt_cannot_regress_non_shortlist():
    # A receipt whose matched note is already at interview must NOT advance/regress it.
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1", status="interview")
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    assert res.status_to is None and "status: interview" in pathlib.Path(path).read_text()


def test_receipt_idempotent_no_double_evidence():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst"); ev.message_id = "m1"
    R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    # Re-read the now-applied note; a second identical receipt must not double-write.
    note2 = [n for n in v.read_leads() if n.slug == "Example - Analyst"][0]
    ev2 = _receipt_ev("proof", "Example - Analyst"); ev2.message_id = "m1"
    R.reconcile(ev2, {}, v, TrackConfig(), FakeGoogleClient(),
                shortlist_by_slug={"Example - Analyst": note2})
    assert pathlib.Path(path).read_text().count("## Application receipt") == 1


def test_receipt_advance_writes_no_interview_fields():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst"); ev.links = ["https://example.com/portal"]
    R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), shortlist_by_slug=sl)
    text = pathlib.Path(path).read_text()
    assert "interview_date" not in text and "interview_link" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_track_reconcile.py -k receipt -v`
Expected: FAIL — `reconcile()` has no `shortlist_by_slug` kw / no receipt branch (`TypeError` or wrong action).

- [ ] **Step 3: Edit `sluice/track/reconcile.py`.**

Add the signature param (line 49) — appended keyword-only:

```python
def reconcile(event, note_by_slug, vault, cfg, client, dry_run=False, *, shortlist_by_slug=None) -> ReconcileResult:
    shortlist_by_slug = shortlist_by_slug or {}
```

Add `_stamp_receipt` next to `_stamp_materials` (after line 34):

```python
def _stamp_receipt(vault, note, ev, dry_run=False):
    # Evidence for a receipt-driven advance. append_body_section is idempotent by tag,
    # so a re-processed receipt (same message_id) never double-writes; body untouched.
    tag = f"track-receipt-{ev.message_id or ev.type}"
    section = (f"## Application receipt <!--{tag}-->\n"
               f"- Received: {date.today().isoformat()}\n"
               f"- From: {ev.sender}\n"
               f"- Subject: {ev.subject}\n"
               f"- Match: {ev.receipt_tier}")
    if dry_run:
        return True
    return vault.append_body_section(note.ref, tag, section)
```

Insert the receipt branch in `reconcile`, immediately AFTER the `unknown` guard (after line 59, before the generic `if event.lead_slug is None ...` guard at line 61):

```python
    # Application receipt (#10): advance shortlist->applied on a domain-PROOF match.
    # Placed BEFORE the generic no-match guard: a receipt's lead is resolved by the
    # deterministic matcher (engine) against the SHORTLIST set, not note_by_slug, and
    # carries its own tier. never-regress: can_apply is True only for shortlist, so a
    # receipt can never pull a lead out of the application ladder.
    if event.type == "receipt":
        note = shortlist_by_slug.get(event.lead_slug) if event.lead_slug else None
        if event.receipt_tier == "proof" and note is not None \
                and _status.can_apply(note.status) and event.confidence >= cfg.auto_apply_min:
            r.status_from = note.status
            if not dry_run:
                # Receipt-specific field set: status + last_signal ONLY. Do NOT reuse
                # _advance, which stamps interview_date/interview_link from ev.when/links
                # -- wrong for an `applied` lead (a receipt is not an interview signal).
                vault.update_fields(note.ref, {"status": "applied",
                                               "last_signal": date.today().isoformat()})
                _stamp_receipt(vault, note, event, dry_run=dry_run)
            r.action = "applied"
            r.status_to = "applied"
            return r
        # corroborated, ambiguous, or proof below the confidence floor -> propose.
        if note is not None or event.candidates:
            r.status_from = note.status if note is not None else None
            r.action = "proposed"
            r.proposal = "receipt (confirm applied)"
            r.note = event.summary
            return r
        r.action = "skipped"
        r.note = event.summary
        return r
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_track_reconcile.py -v && ruff check sluice tests`
Expected: PASS (all existing + 7 new).

- [ ] **Step 5: Commit — BEFORE the mutation witness**

Commit first, deliberately: the witness mutates production code, and a restore that
reaches for `git checkout -- <file>` would wipe every uncommitted change in that file.
That has bitten this repo twice (#59), and the empty post-run diff hides the loss.
Committing first makes the witness non-destructive whichever restore is used.

```bash
git add sluice/track/reconcile.py tests/test_track_reconcile.py
git commit -m "feat(track): reconcile receipt branch -> advance shortlist to applied (#10)

Proof-grade + can_apply + confidence>=auto_apply_min auto-advances with a
receipt-specific field set (status,last_signal -- never _advance's interview_*)
and idempotent evidence; corroborated/ambiguous/low-confidence propose. shortlist
set injected via a keyword-only param, so existing callers are unaffected.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Q7xXM4KpehkJpfkw4zpFnG"
```

- [ ] **Step 6: Mutation witness (THE LESSON)** — confirm test 1 is load-bearing:

Run once: `python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`
Then in `reconcile.py`'s receipt branch, MOVE `_status.can_apply(note.status)` → `_status.can_advance(note.status, "applied")` (edit, don't add), and run:
`python -m pytest tests/test_track_reconcile.py::test_receipt_proof_advances_shortlist_to_applied -v`
Expected: **FAIL** (can_advance refuses shortlist→applied, so no advance). Confirm test 3 (`test_receipt_cannot_regress_non_shortlist`) stays green (inert for this mutant — both predicates refuse interview→applied). Then restore the line (`git checkout -- sluice/track/reconcile.py` is now safe, since Step 5 committed) and re-run the file: PASS. Report the witness result in your report file; leave the tree clean (`git status` empty).

---

### Task 6: engine wiring + intra-run reflection + `confirm`

**Files:**
- Modify: `sluice/track/engine.py` (load shortlist; run `match_receipt`; write Event fields; pass `shortlist_by_slug` to reconcile; reflect + `clear_lead` on the matched slug; `_PROPOSE_TARGET["receipt"]`; `confirm` uses `can_transition`)
- Test: `tests/test_track_engine.py`

**Interfaces:**
- Consumes: `match_receipt` (Task 3), reconcile's new signature (Task 5), `can_transition` (Task 1).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_track_engine.py`. It already defines `_dl()` (a real `DeadLetterDb`), `_vault(status)`, `FakeBackend`, and `FakeGoogleClient` subclasses with a `messages={id: {...}}` dict (e.g. `OneMsgClient`). Add a shortlist-with-url vault helper and a two-receipt client mirroring those, then the tests:

```python
def _vault_shortlist(url, status="shortlist"):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / "Example - Analyst.md").write_text(
        f'---\ncompany: "Example"\nrole: "Analyst"\nurl: "{url}"\nstatus: {status}\n---\n\nBODY\n')
    return Vault(root), str(leads / "Example - Analyst.md")


class TwoReceiptClient(FakeGoogleClient):
    def __init__(self):
        super().__init__(messages={
            "r1": {"headers": {"from": "jobs@example.com", "subject": "Thanks for applying"},
                   "body_text": "received", "thread_id": "t", "attachments": []},
            "r2": {"headers": {"from": "jobs@example.com", "subject": "Application received"},
                   "body_text": "received", "thread_id": "t", "attachments": []},
        }, events=[])


def test_confirm_to_applied_from_shortlist_and_refused_otherwise():
    v, path = _vault_shortlist("https://example.com/careers/1")
    res = E.confirm(v, TrackConfig(), "Example - Analyst", "applied", deadletter=_dl())
    assert res["ok"] and res["to"] == "applied"
    assert "status: applied" in pathlib.Path(path).read_text()
    # a non-shortlist lead is refused with its status as the reason
    v2, _ = _vault_shortlist("https://example.com/m", status="interview")
    res2 = E.confirm(v2, TrackConfig(), "Example - Analyst", "applied", deadletter=_dl())
    assert res2["ok"] is False and res2["reason"] == "interview"


def test_two_receipts_same_lead_one_run_advance_once():
    # The second receipt in one run sees the REFLECTED `applied` snapshot -> no-op.
    v, path = _vault_shortlist("https://example.com/careers/1")
    be = FakeBackend(json.dumps({"lead": None, "type": "receipt", "confidence": 0.9,
                                 "when": None, "links": [], "materials": [], "summary": "received"}))
    rep = E.run(v, TrackConfig(), TwoReceiptClient(), be, seen=set(), deadletter=_dl(),
                now_iso="2026-07-25T09:00:00+00:00")
    text = pathlib.Path(path).read_text()
    assert "status: applied" in text
    assert text.count("## Application receipt") == 1
```

Note: `E.run`/`E.confirm` and `_dl`/`_vault`/`FakeGoogleClient` are all already imported/defined at the top of `tests/test_track_engine.py` — reuse them; do not invent new fakes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_track_engine.py -k "receipt or confirm_to_applied" -v`
Expected: FAIL — `confirm` refuses shortlist→applied (still uses `can_advance`); receipts don't advance (no engine wiring).

- [ ] **Step 3: Edit `sluice/track/engine.py`.**

Add the import at module top (with the other `from sluice.track...` imports):

```python
from sluice.track.receipt import match_receipt
```

In `run()`, after `note_by_slug = {n.slug: n for n in leads}` (line 67), load the shortlist set:

```python
    shortlist_by_slug = {n.slug: n for n in vault.read_leads({"shortlist"})}
```

After `ev = classify(...)` and `rep.classified += 1` (lines 92-93), resolve a receipt's lead deterministically:

```python
            if ev.type == "receipt":
                m = match_receipt(msg, shortlist_by_slug.values(), cfg.ats_relay_domains)
                ev.lead_slug, ev.candidates, ev.receipt_tier = m.lead_slug, m.candidates, m.tier
```

Pass the shortlist set into reconcile (line 94):

```python
            res = reconcile(ev, note_by_slug, vault, cfg, client, dry_run=dry_run,
                            shortlist_by_slug=shortlist_by_slug)
```

Extend the intra-run reflection (lines 100-101) to cover the shortlist snapshot:

```python
            if not dry_run and res.status_to and ev.lead_slug:
                if ev.lead_slug in note_by_slug:
                    note_by_slug[ev.lead_slug].status = res.status_to
                elif ev.lead_slug in shortlist_by_slug:
                    shortlist_by_slug[ev.lead_slug].status = res.status_to
```

Add the propose target (with the other `_PROPOSE_TARGET` entries, line 19-20):

```python
_PROPOSE_TARGET = {"phone_screen": "phone_screen", "interview": "interview",
                   "rejection": "rejected", "offer": "offer", "receipt": "applied"}
```

In `confirm()` (line 158), route through `can_transition`:

```python
    if not _status.can_transition(note.status, to):
        return {"ok": False, "reason": note.status}
```

(`deadletter.clear_lead(ev.lead_slug)` on auto-advance at lines 108-109 already keys on `ev.lead_slug`, which is now set for receipts — no change needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_track_engine.py -v && python -m pytest && ruff check sluice tests`
Expected: PASS — the new engine tests, and the FULL suite green (confirms no existing track test broke).

- [ ] **Step 5: Commit**

```bash
git add sluice/track/engine.py tests/test_track_engine.py
git commit -m "feat(track): wire receipt matching into run/confirm; reflect shortlist advances (#10)

engine.run resolves a receipt's lead by domain where the raw msg is in scope,
writes the slug/candidates/tier onto the Event so the proposal hint, dead-letter
attribution, clear_lead and intra-run reflection all key correctly; the shortlist
snapshot reflects an advance so a second same-run receipt is a no-op. confirm
routes --to applied through can_transition.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Q7xXM4KpehkJpfkw4zpFnG"
```

---

### Task 7: docs — `.rulesync/`, ARCHITECTURE, regenerate

**Files:**
- Modify: `.rulesync/rules/CLAUDE.md` (never-regress paragraph — **user-approved** canonical edit)
- Modify: `docs/ARCHITECTURE.md` (track + status-lifecycle sections)
- Regenerate: `CLAUDE.md`, `AGENTS.md`, `.claude/*` via rulesync

**Interfaces:** none (documentation).

- [ ] **Step 1: Update `.rulesync/rules/CLAUDE.md`.** Find the never-regress invariant paragraph (the sentence "`shortlist -> applied` is the *only* transition apply may make"). Revise to record the receipt actor without weakening the apply rule:

```
`shortlist -> applied` is the only transition apply may make on send; track makes
the same `shortlist -> applied` transition when a confirmation receipt arrives
(via `can_apply`/`can_transition`, #10). Both are the sole crossing into the
application lifecycle; every later move is an on-ladder `can_advance` step.
```

- [ ] **Step 2: Update `docs/ARCHITECTURE.md`.** In the `track` section, note that track advances `shortlist -> applied` on a domain-matched application receipt (`track/receipt.py`, proof auto / corroborated propose). In the status-lifecycle description, add track as a second actor for the `shortlist -> applied` crossing. (Read the file first; match its existing wording and depth — one or two sentences each.)

- [ ] **Step 3: Regenerate the AI-tool outputs**

Run: `npx rulesync@9.6.3 generate -t '*' -f '*'`
Expected: regenerates `CLAUDE.md`, `AGENTS.md`, `.claude/…` (all gitignored) from `.rulesync/`. No error.

- [ ] **Step 4: Verify nothing else drifted + full suite**

Run: `python -m pytest && ruff check sluice tests`
Expected: full suite PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add .rulesync/rules/CLAUDE.md docs/ARCHITECTURE.md
git commit -m "docs(track): record track as a second shortlist->applied actor (#10)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_01Q7xXM4KpehkJpfkw4zpFnG"
```

---

## Definition of done

- `python -m pytest` green (full suite, offline); `ruff check sluice tests` clean.
- A proof-grade, unambiguous receipt advances a `shortlist` lead to `applied` with evidence; a corroborated/ambiguous/low-confidence receipt proposes (dead-letter → `track confirm --to applied`); a name-only or domain-mismatched receipt does nothing.
- Never-regress holds: a receipt cannot advance a lead out of `shortlist`; idempotent on re-receipt.
- The `match_receipt` mutation witness (`can_apply`→`can_advance`) reddens `test_receipt_proof_advances_shortlist_to_applied`.
- Docs regenerated; `.rulesync/` and `docs/ARCHITECTURE.md` record the receipt actor.
- Then: `/review-pr` (pre-push, per the standing cadence) → push → CodeRabbit → merge via the gate.

## Post-plan self-review (author notes)

- **Spec coverage:** every spec section maps to a task — match rule → T3; classify/Event → T4; reconcile branch + evidence + field-set → T5; engine wiring + reflection + confirm → T6; status/can_transition → T1; config → T2; docs → T7. Tests 1-12 in the spec map to T3 (unit table 11), T5 (reconcile 1-9), T6 (engine 12 + confirm 8).
- **Ambiguity guard (spec inv-001):** enforced in `match_receipt` (T3: `len(proof) > 1 → propose`) and re-asserted at reconcile (T5: `test_receipt_ambiguous_proposes_neither`).
- **`test_track_engine.py` fakes:** Step 1 of T6 says to read the file and reuse its existing Gmail/dead-letter fakes rather than assume names — the harness there is the source of truth for `run(...)`'s exact call shape.
