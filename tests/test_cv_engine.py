# tests/test_cv_engine.py
from sluice.cv.bundle import build_bundle, render_bundle
from sluice.cv.engine import run_one, run_batch
from sluice.cv.validate import validate
from sluice.core.backends import BackendError, FallbackBackend, OpenAiCompatibleBackend
from sluice.core.leads import StalenessPolicy

class Note:
    def __init__(self, fm, path="Job Applications/Job Leads/Acme - Analyst.md"):
        # A store hands back an opaque `ref` and the slug it issued; it never hands
        # back a path for the caller to parse.
        self.fm = fm; self.ref = path; self.slug = path.split("/")[-1][:-3]

class FakeVault:
    def __init__(self, entries, notes=None):
        self._entries = entries; self._notes = notes or []; self.written = {}; self.fields = {}
    def read_experience_entries(self, verified_only=True): return self._entries
    # Signature must track protocols.Store EXACTLY. This fake carrying the old
    # read_baseline(rel=...) is what let a real TypeError ship green.
    def read_baseline(self): return "BASELINE"
    def read_leads(self, statuses=None): return self._notes
    def _fresh(self, ref): return next((n for n in self._notes if n.ref == ref), None)
    def set_tailored_cv(self, ref, value, *, only_if_absent=False):
        # Mirrors the real Vault.set_tailored_cv (#16 cv long-window): only_if_absent
        # checks the FRESH note in self._notes -- not the `note` object the caller
        # (run_one) is holding, which may be a stale snapshot from before a concurrent
        # writer's set_tailored_cv landed. Returns whether a write happened.
        fresh = self._fresh(ref)
        if only_if_absent and fresh is not None and fresh.fm.get("tailored_cv"):
            return False
        self.written[ref] = value
        if fresh is not None:
            fresh.fm["tailored_cv"] = value
        return True
    def update_fields(self, ref, fields, *, append_note=None, note_tag=None):
        # Surgical named-key set. Records to self.fields for assertion and applies to the
        # fresh note (mirrors the real store setting frontmatter without touching the body).
        self.fields.setdefault(ref, {}).update(fields)
        fresh = self._fresh(ref)
        if fresh is not None:
            fresh.fm.update(fields)
    def hold_for_signoff(self, ref, *, pending, claims):
        # Mirrors Vault.hold_for_signoff: stamp only if no tailored_cv on the FRESH note.
        fresh = self._fresh(ref)
        if fresh is not None and fresh.fm.get("tailored_cv"):
            return False
        self.fields.setdefault(ref, {}).update({"pending_cv": pending, "needs_signoff": claims})
        if fresh is not None:
            fresh.fm.update({"pending_cv": pending, "needs_signoff": claims})
        return True
    def sign_off(self, ref, *, accept=True):
        # Mirrors Vault.sign_off's outcome verdict on the fresh note (#60).
        fresh = self._fresh(ref)
        pending = fresh.fm.get("pending_cv") if fresh is not None else None
        if not pending:
            return "nothing"
        fresh.fm.pop("pending_cv", None); fresh.fm.pop("needs_signoff", None)
        if not accept:
            return "discarded"
        if fresh.fm.get("tailored_cv"):
            return "collision"
        fresh.fm["tailored_cv"] = pending
        return "promoted"

class FakeCache:
    def get_or_build(self, fm): return {"jd": {"markdown": "we value delivery"}}

class FakeRenderer:
    """The Renderer seam, injected. Records what it was asked to render so a test can
    assert a CV was NEVER rendered -- which is the fabrication gate's whole point."""
    def __init__(self): self.rendered = []
    def render(self, cv_text, out_dir, *, neutral_name="CV.pdf"):
        self.rendered.append(cv_text)
        return f"/tmp/x/{neutral_name}"

class FakeBackend:
    def __init__(self, cv_out, audit_out="supported\tx\tSF1"):
        self.cv_out = cv_out; self.audit_out = audit_out
        self.last_backend = "primary"; self.calls = 0
    def complete(self, prompt):
        self.calls += 1
        # first call = compose, later audit; return CV then audit
        return self.cv_out if "SOURCE BUNDLE" in prompt and "auditing" not in prompt else self.audit_out

ENTRIES = [{"title": "Grew team", "company": "Example Foundry", "best_for": "delivery",
            "category": "people", "metrics": "3 8", "body": "Grew 3 to 8."}]

def _cfg():
    from sluice.cv.config import CvConfig
    c = CvConfig(); c.output_dir = "/tmp/cvout"; c.served_dir = "/tmp/cvserved"
    # prefix_map now defaults to {}; CLEAN_CV's citations are hardcoded to [EF1],
    # so the single ENTRIES company must still code to "EF1" (the 2-letter
    # fallback for "Example Foundry" would yield "EX1").
    c.prefix_map = {"Example Foundry": "EF"}
    return c

# Synthetic throughout; only the descending start years are load-bearing.
CLEAN_CV = "\n".join([
    "JANE ROE", "", "PROFILE", "I build reliable systems.", "", "WORK EXPERIENCE", "",
    "Example Systems", "02/2023–present | Alfa | Staff Engineer", "- Shipped [EF1]", "",
    "Example Analytics", "06/2020–01/2023 | Bravo | Senior Engineer",
    "- Grew team from 3 to 8 [EF1]", "",
    "Example Robotics", "09/2017–05/2020 | Charlie | Engineer", "- Coached [EF1]", "",
    "Example Cartography", "07/2015–08/2017 | Alfa | Junior Engineer", "- CI [EF1]", "",
    "CERTIFICATES", "- CSM", "EDUCATION", "- Uni",
])


def test_clean_cv_is_actually_clean():
    # CLEAN_CV's validity is a PREMISE of every skipped-gate test below: those
    # assert the engine skips when the gate fails, and they keep passing if
    # CLEAN_CV silently stops being clean -- vacuously, for the wrong reason.
    # Measured: breaking its first start year fails 5 tests loudly but leaves 3
    # of these passing on a false premise. State the premise instead of implying
    # it, because a fixture regeneration is exactly when it would quietly break.
    bundle_text = render_bundle(build_bundle(
        entries=ENTRIES, baseline="BASELINE", negatives=[],
        jd_keywords=[], prefix_map={"Example Foundry": "EF"}))
    assert validate(CLEAN_CV, bundle_text) == []


def test_application_owned_lead_is_refused():
    v = FakeVault(ENTRIES)
    r = run_one(Note({"status": "applied", "company": "Acme"}), v, _cfg(),
                FakeBackend("x"), FakeCache(), renderer=FakeRenderer())
    assert r.status == "skipped-selection"
    assert v.written == {}

def test_gate_failure_skips_and_never_renders():
    # `rend` is BOUND so the assertion below can look at it. Every gate test used to pass
    # renderer=FakeRenderer() inline and throw the reference away, which made the
    # "never rendered" claim in the test name unassertable -- an unconditional
    # renderer.render() before the gate check passed the entire suite.
    # compose returns an uncited CV -> validate fails both attempts -> skip
    bad = CLEAN_CV.replace("- Grew team from 3 to 8 [EF1]", "- Grew team from 3 to 8")
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(bad), FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("UNCITED" in x for x in r.violations)
    assert v.written == {}   # nothing recorded
    # THE assertion. `v.written == {}` says nothing about rendering: the gate path returns
    # before set_tailored_cv either way. Only this proves no PDF with an invented metric
    # was written to the output dir under the neutral filename -- the exact file a user
    # picks up and attaches to an application.
    assert rend.rendered == [], "a CV was RENDERED despite an open fabrication gate"

def test_dry_run_reports_but_writes_nothing():
    v = FakeVault(ENTRIES)
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(CLEAN_CV), FakeCache(), renderer=FakeRenderer(), dry_run=True)
    assert r.status == "dry-run"
    assert v.written == {}

def test_batch_skips_leads_that_already_have_a_cv():
    notes = [Note({"status": "shortlist", "company": "A", "role": "Analyst", "tailored_cv": "x.pdf"})]
    v = FakeVault(ENTRIES, notes=notes)
    results = run_batch(v, _cfg(), FakeBackend(CLEAN_CV), FakeCache(), renderer=FakeRenderer(), dry_run=True)
    assert results[0].status == "skipped-has-cv"

def test_non_shortlist_lead_is_refused():
    v = FakeVault(ENTRIES)
    r = run_one(Note({"status": "new", "company": "Acme"}), v, _cfg(),
                FakeBackend("x"), FakeCache(), renderer=FakeRenderer())
    assert r.status == "skipped-selection"
    assert v.written == {}

def test_drifted_work_header_fails_closed():
    # Proves the fail-open hole is closed: validate()'s per-bullet citation checks
    # only run inside the section keyed on the exact "WORK EXPERIENCE" header, so a
    # fully-cited CV whose header drifted would sail through plain validate() with
    # zero violations (see engine.py's STRUCTURAL guard). The engine must catch this
    # itself and HARD-fail the gate rather than rendering an unchecked draft.
    drifted = CLEAN_CV.replace("WORK EXPERIENCE", "PROFESSIONAL EXPERIENCE")
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(drifted), FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("STRUCTURAL" in x for x in r.violations)
    assert v.written == {}
    assert rend.rendered == [], "a CV was RENDERED despite an open fabrication gate"

def test_missing_profile_header_is_structural():
    # A composed CV with no PROFILE header: the profile sweep never runs (fail-open),
    # so the engine must HARD-fail the gate and render nothing. Mirror of
    # test_drifted_work_header_fails_closed.
    no_profile = CLEAN_CV.replace("PROFILE\nI build reliable systems.\n\n", "")
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(no_profile), FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert ("STRUCTURAL: composed CV lacks the exact 'PROFILE' header, so the "
            "profile fabrication check did not run") in r.violations
    assert rend.rendered == [], "a CV with no PROFILE header was RENDERED"

def test_happy_path_renders_and_records(monkeypatch):
    import sluice.cv.render as _render_mod
    monkeypatch.setattr(_render_mod, "render",
                        lambda *a, **k: "/tmp/x/Jane Roe CV.pdf")
    monkeypatch.setattr(_render_mod, "serve",
                        lambda *a, **k: "Jane_Roe_CV_deadbeef.pdf")
    v = FakeVault(ENTRIES)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    r = run_one(note, v, _cfg(), FakeBackend(CLEAN_CV), FakeCache(), renderer=FakeRenderer())
    assert r.status == "rendered"
    assert r.served == "Jane_Roe_CV_deadbeef.pdf"
    assert "Jane_Roe_CV_deadbeef.pdf" in v.written[note.ref]

def test_no_serve_renders_but_does_not_mark_lead():
    # --no-serve is emulated via cvcfg.served_dir = "": the engine's serve() call is
    # short-circuited entirely (the `if cvcfg.served_dir else None` guard in run_one), so
    # nothing is published. Proves the fixed bug -- writing the literal string "None (...)"
    # into tailored_cv, which is truthy and so would permanently dedup-skip the lead in
    # run_batch even though no CV was ever published -- cannot recur: a render that is never
    # served must leave the vault untouched.
    #
    # Rendering itself must STILL happen on this path, so assert on the INJECTED renderer
    # (the active seam). The old monkeypatch of sluice.cv.render.render was inert here --
    # run_one renders through the injected renderer, not that module function -- so removing
    # the render call would have left this test green while publishing nothing.
    cfg = _cfg()
    cfg.served_dir = ""  # emulates --no-serve
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, cfg, FakeBackend(CLEAN_CV), FakeCache(), renderer=rend)
    assert r.status == "rendered"
    assert rend.rendered == [CLEAN_CV]   # the render still happened; only serving was skipped
    assert r.served is None
    assert v.written == {}   # no tailored_cv marker when nothing was published

def test_slop_only_failure_fails_gate_and_feeds_retry():
    # A CV that is correctly cited (validate() passes clean) but whose first WORK
    # bullet contains an em dash -- a slop HARD error. Proves (a) a slop-only
    # failure still fails the gate, and (b) the SLOP message reaches the retry
    # prompt via prior_violations.
    slop_cv = CLEAN_CV.replace("- Shipped [EF1]", "- Shipped, launched \u2014 and iterated [EF1]")

    class RecordingBackend:
        def __init__(self, cv):
            self.cv = cv; self.last_backend = "primary"; self.prompts = []
        def complete(self, prompt):
            self.prompts.append(prompt)
            # compose prompts contain "SOURCE BUNDLE" and not "auditing"; audit
            # prompts contain both, so this mirrors FakeBackend's routing.
            return self.cv if "SOURCE BUNDLE" in prompt and "auditing" not in prompt else "supported\tx\tSF1"

    be = RecordingBackend(slop_cv)
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert r.slop                      # slop error surfaced
    assert v.written == {}             # nothing rendered/recorded
    # the SECOND compose prompt must carry the slop feedback
    compose_prompts = [p for p in be.prompts if "SOURCE BUNDLE" in p and "auditing" not in p]
    assert len(compose_prompts) == 2
    assert "SLOP" in compose_prompts[1]
    assert rend.rendered == [], "a CV was RENDERED despite an open fabrication gate"

def test_retry_happens_exactly_once():
    # A persistently gate-failing CV must be composed exactly twice: the initial
    # attempt plus the single retry, then skip -- never a third attempt.
    bad = CLEAN_CV.replace("- Grew team from 3 to 8 [EF1]", "- Grew team from 3 to 8")
    v = FakeVault(ENTRIES)
    backend = FakeBackend(bad)
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), backend, FakeCache(), renderer=FakeRenderer())
    assert r.status == "skipped-gate"
    assert backend.calls == 2   # audit is never reached on skipped-gate, so this
                                # counts compose calls exactly

def test_advisory_audit_failure_does_not_block_render(monkeypatch):
    # The audit is explicitly advisory ("NEVER blocks", audit.py). A backend error
    # or timeout during the audit call must not prevent a CV that already passed
    # the HARD citation gate from rendering -- it must be swallowed and logged.
    import sluice.cv.render as _render_mod
    monkeypatch.setattr(_render_mod, "render",
                        lambda *a, **k: "/tmp/x/Jane Roe CV.pdf")
    monkeypatch.setattr(_render_mod, "serve",
                        lambda *a, **k: "Jane_Roe_CV_deadbeef.pdf")

    class AuditRaisingBackend:
        def __init__(self, cv):
            self.cv = cv; self.last_backend = "primary"; self.audited = False
        def complete(self, prompt):
            # compose call succeeds with a clean, fully-cited CV; the audit call
            # (same routing rule as FakeBackend: contains "SOURCE BUNDLE" AND
            # "auditing") raises, simulating a backend timeout/error.
            if "SOURCE BUNDLE" in prompt and "auditing" not in prompt:
                return self.cv
            self.audited = True
            raise RuntimeError("backend timeout during advisory audit")

    v = FakeVault(ENTRIES)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    be = AuditRaisingBackend(CLEAN_CV)
    # _cfg() carries require_signoff's default (True), so this ALSO pins the #60 fail-open:
    # when the audit backend errors, run_audit swallows it -> no blockers -> the pointer is
    # STILL set and the CV serves. The gate is best-effort, never harder than the audit ran.
    r = run_one(note, v, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    assert r.status == "rendered"
    assert r.audit_flags == []
    assert be.audited, "the audit was never invoked; the fail-open assertion would be vacuous"
    assert note.ref in v.written, "fail-open must still set the send-ready pointer (#60)"


# --- #60 sign-off gate: engine behaviour (withhold, sticky, require_signoff) ---

def _served(monkeypatch, served="Jane_Roe_CV_deadbeef.pdf"):
    import sluice.cv.render as _render_mod
    monkeypatch.setattr(_render_mod, "render", lambda *a, **k: "/tmp/x/Jane Roe CV.pdf")
    monkeypatch.setattr(_render_mod, "serve", lambda *a, **k: served)


def test_unsupported_flag_withholds_pointer_and_marks_needs_signoff(monkeypatch):
    # An `unsupported` audit flag WITHHOLDS the send-ready tailored_cv pointer (apply keys
    # on it) and records pending_cv + needs_signoff for a human to sign off. The CV still
    # rendered and served (it passed the HARD gate) -- only the pointer is withheld. Uses
    # _cfg()'s DEFAULT require_signoff.
    import json
    _served(monkeypatch)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    v = FakeVault(ENTRIES, notes=[note])
    be = FakeBackend(CLEAN_CV, audit_out="unsupported\tMotivated by placeholder\tNONE")
    r = run_one(note, v, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    assert r.status == "needs-signoff"
    assert r.served == "Jane_Roe_CV_deadbeef.pdf"            # rendered + served
    assert note.ref not in v.written                          # tailored_cv WITHHELD
    assert "tailored_cv" not in note.fm
    assert note.fm.get("pending_cv", "").startswith("Jane_Roe_CV_deadbeef.pdf")
    assert json.loads(note.fm["needs_signoff"]) == ["unsupported\tMotivated by placeholder\tNONE"]


def test_paraphrase_only_still_renders_and_sets_pointer(monkeypatch):
    # `paraphrase` is legitimate tailoring, not a fabrication -- it must NOT block. A CV
    # whose only audit flags are paraphrase/supported serves normally.
    _served(monkeypatch)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    v = FakeVault(ENTRIES, notes=[note])
    be = FakeBackend(CLEAN_CV, audit_out="paraphrase\tgrew it\tEF1\nsupported\tled\tEF1")
    r = run_one(note, v, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    assert r.status == "rendered"
    assert note.ref in v.written                              # pointer SET
    assert "pending_cv" not in note.fm and "needs_signoff" not in note.fm


def test_require_signoff_false_serves_despite_unsupported(monkeypatch):
    # The off-switch restores the old auto-serve: with require_signoff False, an
    # `unsupported` flag no longer withholds the pointer.
    _served(monkeypatch)
    cfg = _cfg(); cfg.require_signoff = False
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    v = FakeVault(ENTRIES, notes=[note])
    be = FakeBackend(CLEAN_CV, audit_out="unsupported\tMotivated by placeholder\tNONE")
    r = run_one(note, v, cfg, be, FakeCache(), renderer=FakeRenderer())
    assert r.status == "rendered"
    assert note.ref in v.written and "pending_cv" not in note.fm


def test_pending_lead_is_sticky_and_not_recomposed(monkeypatch):
    # THE LATCH (#60): a lead already carrying pending_cv is held out of BOTH cv paths
    # BEFORE compose, so a re-run cannot re-roll the non-deterministic audit into a
    # send-ready pointer. Assert the backend's compose was never called.
    _served(monkeypatch)
    fm = {"status": "shortlist", "company": "Example Foundry", "role": "Analyst",
          "pending_cv": "Jane_Roe_CV_old.pdf (2026-07-24)",
          "needs_signoff": '["unsupported\\tMotivated by placeholder\\tNONE"]'}
    note = Note(dict(fm))
    v = FakeVault(ENTRIES, notes=[note])
    be = FakeBackend(CLEAN_CV, audit_out="supported\tx\tEF1")
    r = run_one(note, v, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    assert r.status == "skipped-needs-signoff"
    assert be.calls == 0, "a held (pending) lead was recomposed -- the audit could re-roll clean"
    assert note.ref not in v.written and "tailored_cv" not in note.fm
    # ...and the batch path (which routes through run_one) skips it identically.
    note2 = Note(dict(fm))
    vb = FakeVault(ENTRIES, notes=[note2])
    beb = FakeBackend(CLEAN_CV, audit_out="supported\tx\tEF1")
    batch = run_batch(vb, _cfg(), beb, FakeCache(), renderer=FakeRenderer())
    assert [b.status for b in batch] == ["skipped-needs-signoff"]
    assert beb.calls == 0


def test_batch_limit_counts_needs_signoff(monkeypatch):
    # A held (needs-signoff) lead did the full compose+render+serve, so it counts toward
    # --limit just like a rendered one -- the batch must stop after one, not run on to
    # compose a second expensive CV.
    _served(monkeypatch)
    notes = [
        Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"},
             path="Job Applications/Job Leads/Example Foundry - Analyst.md"),
        Note({"status": "shortlist", "company": "Example Analytics", "role": "Engineer"},
             path="Job Applications/Job Leads/Example Analytics - Engineer.md"),
    ]
    v = FakeVault(ENTRIES, notes=notes)
    be = FakeBackend(CLEAN_CV, audit_out="unsupported\tMotivated by placeholder\tNONE")
    results = run_batch(v, _cfg(), be, FakeCache(), renderer=FakeRenderer(), limit=1)
    assert [r.status for r in results] == ["needs-signoff"]   # stopped after one held lead


def test_flagged_recompose_does_not_latch_a_lead_that_already_has_a_cv(monkeypatch):
    # A lead with a real tailored_cv, re-tailored (single-lead), whose NEW compose is flagged:
    # the hold must NOT be stamped over the existing pointer -- that would latch the lead behind
    # a redundant sign-off even though a send-ready CV already exists. Report skipped-has-cv and
    # leave the existing pointer untouched (mirrors set_tailored_cv's only_if_absent).
    _served(monkeypatch)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst",
                 "tailored_cv": "CV_real.pdf (2026-07-24)"})
    v = FakeVault(ENTRIES, notes=[note])
    be = FakeBackend(CLEAN_CV, audit_out="unsupported\tMotivated by placeholder\tNONE")
    r = run_one(note, v, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    assert r.status == "skipped-has-cv"
    assert "pending_cv" not in note.fm and "needs_signoff" not in note.fm   # no redundant hold
    assert note.fm["tailored_cv"] == "CV_real.pdf (2026-07-24)"             # existing pointer intact

def test_batch_survives_a_single_lead_exception(monkeypatch):
    # The triage engine records per-lead failures and continues; the CV engine
    # must do the same. One lead's render failure (e.g. WeasyPrint blowing up)
    # must not abort the rest of the --all-shortlist batch.
    #
    # The failure is injected through the RENDERER SEAM rather than by monkeypatching
    # sluice.cv.render: the engine no longer reaches into that module, so a monkeypatch
    # there would be inert and this test would pass for the wrong reason.
    import sluice.cv.render as _render_mod

    class FlakyRenderer:
        def render(self, cv_text, out_dir, *, neutral_name="CV.pdf"):
            if "acme" in out_dir:
                raise RuntimeError("weasyprint boom")
            return f"/tmp/x/{neutral_name}"

    monkeypatch.setattr(_render_mod, "serve",
                        lambda *a, **k: "Jane_Roe_CV_deadbeef.pdf")

    notes = [
        Note({"status": "shortlist", "company": "Acme", "role": "Analyst"},
             path="Job Applications/Job Leads/Acme - Analyst.md"),
        Note({"status": "shortlist", "company": "Example Analytics", "role": "Analyst"},
             path="Job Applications/Job Leads/Example Analytics - Analyst.md"),
    ]
    v = FakeVault(ENTRIES, notes=notes)
    results = run_batch(v, _cfg(), FakeBackend(CLEAN_CV), FakeCache(), renderer=FlakyRenderer())
    assert len(results) == 2   # the batch did not abort after the first failure
    assert results[0].status == "error"
    assert results[1].status == "rendered"
    assert notes[0].ref not in v.written   # the errored lead is never marked tailored
    assert notes[1].ref in v.written       # the surviving lead still gets recorded

def test_batch_reports_dossier_failed_when_the_blocked_lead_then_errors():
    # CodeRabbit finding on #18: dossier_failed is set inside run_one's local scope
    # (when the SSRF guard blocks the fetch), but run_batch's per-lead catch-all --
    # which MUST stay a catch-all, so one bad lead never aborts the batch, see the
    # test above -- used to build CvResult(ref, "error") with no dossier_failed
    # argument at all, silently defaulting it to False. That undercounts cli.py's
    # "N CV(s) composed blind" summary for exactly the lead an operator most needs
    # to see it for: one where the dossier was ALSO refused. run_one now stamps
    # dossier_failed onto the exception before re-raising (its own comment explains
    # why that is the only channel left once the stack unwinds past it); this drives
    # both failures through the real run_batch to prove the flag survives the
    # boundary, not just that run_one sets it locally (test_dossier_guard.py already
    # covers that half in isolation).
    from sluice.core import urlguard

    class _BlockedCache:
        """Stands in for Sluice.dossier_cache() after the SSRF guard has refused
        the lead's url -- exactly what get_or_build raises in production."""
        def get_or_build(self, fm):
            raise urlguard.DossierBlocked(urlguard.BLOCKED_ADDRESS)

    class _BoomRenderer:
        """A downstream failure UNRELATED to the dossier (e.g. WeasyPrint), so this
        test proves the flag survives a SECOND, independent exception -- not just
        the dossier's own."""
        def render(self, cv_text, out_dir, *, neutral_name="CV.pdf"):
            raise RuntimeError("weasyprint boom")

    notes = [Note({"status": "shortlist", "company": "Acme", "role": "Analyst"})]
    v = FakeVault(ENTRIES, notes=notes)
    results = run_batch(v, _cfg(), FakeBackend(CLEAN_CV), _BlockedCache(),
                        renderer=_BoomRenderer())
    assert len(results) == 1
    assert results[0].status == "error"
    assert results[0].dossier_failed is True, \
        "the dossier WAS blocked -- run_batch's catch-all must not silently lose that"

def test_batch_records_error_when_fallback_response_is_truncated():
    # A truncated fallback response (finish_reason==length) is a hard error, not
    # a silent partial (see OpenAiCompatibleBackend.complete). Drive this through
    # a real FallbackBackend + OpenAiCompatibleBackend -- primary down, fallback
    # truncated -- and prove the batch surfaces "error", never a rendered CV
    # built from the partial content.
    class FailingPrimary:
        last_backend = None
        def complete(self, prompt):
            raise BackendError("claude-max invocation failed: ssh down")

    def truncated_http(url, data, headers, timeout):
        return ('{"choices":[{"message":{"content":"JANE ROE\\n\\nWORK EXP"},'
                '"finish_reason":"length"}]}')

    fallback = OpenAiCompatibleBackend("m", base_url="http://x", api_key="k",
                                       http=truncated_http)
    backend = FallbackBackend(FailingPrimary(), fallback)

    notes = [Note({"status": "shortlist", "company": "Acme", "role": "Analyst"})]
    v = FakeVault(ENTRIES, notes=notes)
    results = run_batch(v, _cfg(), backend, FakeCache(), renderer=FakeRenderer())
    assert len(results) == 1
    assert results[0].status == "error"
    assert v.written == {}   # never marked tailored off a truncated partial


def test_run_one_batch_guard_skips_when_cv_appeared_during_render(monkeypatch):
    # Simulates the #16 cv long-window race: `note` is the snapshot run_one composed
    # against (no tailored_cv at read time), but by the time the served write happens a
    # concurrent writer has already set tailored_cv on the FRESH note. FakeVault tracks
    # that fresh state in self._notes, separately from the `note` object passed in --
    # exactly the gap between "what we read" and "what's there now" that only_if_absent
    # closes atomically in the real vault.
    import sluice.cv.render as _render_mod
    monkeypatch.setattr(_render_mod, "render", lambda *a, **k: "/tmp/x/Jane Roe CV.pdf")
    monkeypatch.setattr(_render_mod, "serve",
                        lambda *a, **k: "Jane_Roe_CV_deadbeef.pdf")

    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    fresh = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst",
                 "tailored_cv": "PREEXISTING.pdf (2026-07-10)"}, path=note.ref)
    v = FakeVault(ENTRIES, notes=[fresh])
    rend = FakeRenderer()
    r = run_one(note, v, _cfg(), FakeBackend(CLEAN_CV), FakeCache(), renderer=rend,
                guard_existing_cv=True)
    assert r.status == "skipped-has-cv"
    # The render itself still happened -- the CV passed the gate and was rendered/served
    # before the write race was discovered; only the note pointer write was withheld.
    assert rend.rendered == [CLEAN_CV]
    assert note.ref not in v.written
    assert v.read_leads()[0].fm.get("tailored_cv") == "PREEXISTING.pdf (2026-07-10)"


def test_run_one_direct_path_overwrites(monkeypatch):
    # Same fresh-note setup as the guard test above, but run_one is called WITHOUT
    # guard_existing_cv (default False) -- the direct single-lead cv path, which must
    # keep its current unconditional-overwrite behaviour.
    import sluice.cv.render as _render_mod
    monkeypatch.setattr(_render_mod, "render", lambda *a, **k: "/tmp/x/Jane Roe CV.pdf")
    monkeypatch.setattr(_render_mod, "serve",
                        lambda *a, **k: "Jane_Roe_CV_deadbeef.pdf")

    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    fresh = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst",
                 "tailored_cv": "PREEXISTING.pdf (2026-07-10)"}, path=note.ref)
    v = FakeVault(ENTRIES, notes=[fresh])
    r = run_one(note, v, _cfg(), FakeBackend(CLEAN_CV), FakeCache(), renderer=FakeRenderer())
    assert r.status == "rendered"
    assert v.read_leads()[0].fm.get("tailored_cv") != "PREEXISTING.pdf (2026-07-10)"
    assert "Jane_Roe_CV_deadbeef.pdf" in v.read_leads()[0].fm.get("tailored_cv")


def test_the_fake_vault_conforms_to_the_real_store_signature():
    """The join between "conformance tests real stores" and "engine tests use a fake" was
    MANUAL, and that is exactly how a total breakage of `cv run` shipped green: the fake's
    read_baseline still took the `rel` argument the real Vault had dropped, so the engine's
    stale call site was invisible.

    Any method this fake implements must match the real Vault's signature. It need not
    implement all of them -- it is a fake for the CV path -- but where it does, it may not
    drift.
    """
    import inspect
    from sluice.core.vault import Vault

    fake = FakeVault([])
    for name in ("read_experience_entries", "read_baseline", "read_leads", "set_tailored_cv"):
        real_sig = inspect.signature(getattr(Vault, name))
        fake_sig = inspect.signature(getattr(FakeVault, name))
        assert list(fake_sig.parameters) == list(real_sig.parameters), (
            f"FakeVault.{name}{fake_sig} has drifted from Vault.{name}{real_sig}. "
            f"A fake that outlives the contract it fakes hides real breakage.")
    assert fake is not None


# ── #9: the staleness gate ───────────────────────────────────────────────────

class RecordingCache:
    """A dossier cache that records whether it was asked for anything.

    This is the ONLY witness for the gate's PLACEMENT. Every `skipped-stale` assertion
    below stays green if the check is moved below `dossier_cache.get_or_build`; only a
    zero-call assertion catches that, and catching it is the whole point -- the gate
    exists to spend nothing on a lead whose posting has probably closed.
    """
    def __init__(self): self.calls = 0
    def get_or_build(self, fm):
        self.calls += 1
        return {"jd": {"markdown": "we value delivery"}}


_STALE_FM = {"status": "shortlist", "company": "Example Foundry", "role": "Analyst",
             "last_seen": "2026-01-01"}
_POLICY = StalenessPolicy(ttl_days=90, today="2026-07-27")


def test_stale_lead_is_skipped_before_any_dossier_fetch():
    v, cache, rend, be = FakeVault(ENTRIES), RecordingCache(), FakeRenderer(), FakeBackend(CLEAN_CV)
    r = run_one(Note(dict(_STALE_FM)), v, _cfg(), be, cache, renderer=rend,
                policy=_POLICY)
    assert r.status == "skipped-stale"
    assert cache.calls == 0, "a stale lead must cost no dossier fetch"
    assert be.calls == 0, "a stale lead must cost no compose"
    assert rend.rendered == []
    assert v.written == {}


def _ran(note_fm, policy=None):
    """Run to completion with serve disabled and report whether the gate let it past.

    Asserting on the RECORDING CACHE rather than on `status != "skipped-stale"` is the
    stronger claim: it says the lead actually reached the first line that spends, which
    is what "the gate did not fire" means.
    """
    cfg = _cfg()
    cfg.served_dir = ""          # the existing no-serve idiom; keeps this off the disk
    cache = RecordingCache()
    kw = {"policy": policy} if policy is not None else {}
    run_one(Note(note_fm), FakeVault(ENTRIES), cfg, FakeBackend(CLEAN_CV), cache,
            renderer=FakeRenderer(), **kw)
    return cache.calls


def test_fresh_lead_is_unaffected_by_the_gate():
    assert _ran(dict(_STALE_FM, last_seen="2026-07-20"), _POLICY) == 1


def test_include_stale_composes_a_stale_lead():
    p = StalenessPolicy(ttl_days=90, today="2026-07-27", include_stale=True)
    assert _ran(dict(_STALE_FM), p) == 1


def test_default_policy_leaves_the_gate_inert():
    # A call site that forgets to thread a policy must fail SAFE.
    assert _ran(dict(_STALE_FM)) == 1


def test_a_lead_both_HELD_and_stale_still_reports_needs_signoff():
    # The gate sits AFTER the #60 latch, so it is strictly additive: it can only fire on
    # leads that would otherwise have gone on to compose. #60's observable behaviour must
    # not move.
    held = dict(_STALE_FM, pending_cv="CV.pdf")
    r = run_one(Note(held), FakeVault(ENTRIES), _cfg(), FakeBackend(CLEAN_CV),
                FakeCache(), renderer=FakeRenderer(), policy=_POLICY)
    assert r.status == "skipped-needs-signoff"


def test_run_batch_skips_a_stale_lead():
    v = FakeVault(ENTRIES, notes=[Note(dict(_STALE_FM))])
    cache = RecordingCache()
    out = run_batch(v, _cfg(), FakeBackend(CLEAN_CV), cache, renderer=FakeRenderer(),
                    policy=_POLICY)
    assert [r.status for r in out] == ["skipped-stale"]
    assert cache.calls == 0
