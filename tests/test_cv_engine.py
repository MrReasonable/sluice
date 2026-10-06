# tests/test_cv_engine.py
import dataclasses
import json
import os

import pytest

from sluice.cv.bundle import build_bundle
from sluice.cv.engine import run_one, run_batch
from sluice.cv.validate import check_selection, entry_facts
from sluice.core.backends import (
    BackendError, Completion, OpenAiCompatibleBackend, RetryingBackend,
)
from sluice.core.leads import StalenessPolicy
from sluice.core import status as _status
from sluice.core.protocols import CandidateProfile, Store
from tests.conftest import SYNTHETIC_LAYOUT

# #107: the identity every test in this file gets unless it asks for something else.
# sluice assembles the CV's name and contact block from it (#364/#365/#368, spec §7.1), so
# no reply carries either. ONE contact field (mobile) is enough to clear the "blank contact
# refuses" gate (see test_a_declared_name_with_blank_contact_also_refuses_before_spend).
DEFAULT_CANDIDATE = CandidateProfile(forenames="Jane", surname="Roe", mobile="+1 555 0100")

# The first line of every compose prompt (cv/compose.py::build_structured_prompt). The
# fakes below route on it: the structured prompt carries no `SOURCE BUNDLE` header, and the
# audit prompt opens "You are auditing", so neither can be mistaken for the other.
_COMPOSE = "Compose a tailored CV for"


class Note:
    def __init__(self, fm, path="Job Applications/Job Leads/Acme - Analyst.md"):
        # A store hands back an opaque `ref` and the slug it issued; it never hands
        # back a path for the caller to parse.
        self.fm = fm; self.ref = path; self.slug = path.split("/")[-1][:-3]

class FakeVault:
    def __init__(self, entries, notes=None, candidate=DEFAULT_CANDIDATE,
                 layout=SYNTHETIC_LAYOUT):
        self._entries = entries; self._notes = notes or []; self.written = {}; self.fields = {}
        self._candidate = candidate
        self._layout = layout
    def read_cv_layout(self): return self._layout
    def read_evidence(self, kind, verified_only=True):
        return self._entries if kind == "experience" else []
    # #107: cv/engine.py's identity gate is MUST-support (Store.read_candidate_profile),
    # not reached through getattr -- so every test that expects run_one to proceed past
    # it needs this to answer, not raise. `candidate` is a constructor param (not a
    # hardcoded DEFAULT_CANDIDATE return) so a test can seed a different declared identity
    # without a second fake class.
    def read_candidate_profile(self): return self._candidate
    # Tracks the SUBSET of protocols.Store that cv actually exercises, and each
    # method it does carry must match that method's real signature exactly -- this
    # fake carrying a stale signature for the baseline-CV reader (a Store method since
    # retired by #364/#365/#368) is what let a real TypeError ship green. Deliberately NOT the whole contract, but every member it does carry has
    # the real parameter list: a guard the fake accepted and ignored is the shape that
    # made a real guard look tested, so it honours what it cheaply can and RAISES on the
    # rest. The conformance suite in tests/conformance/ holds real stores to the contract.
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
    def update_fields(self, ref, fields, *, append_note=None, note_tag=None,
                      require_status=None, require_blank=None, blank_values=None,
                      require_unchanged=None, preserve_block_values=None):
        # Surgical named-key set. Records to self.fields for assertion and applies to the
        # fresh note (mirrors the real store setting frontmatter without touching the body).
        for name, value in (("blank_values", blank_values),
                            ("require_unchanged", require_unchanged),
                            ("preserve_block_values", preserve_block_values)):
            if value is not None:
                raise NotImplementedError(
                    f"FakeVault.update_fields does not mirror `{name}`; a fake that "
                    "silently ignored a guard would make the real one look tested")
        fresh = self._fresh(ref)
        if fresh is None and (require_status is not None or require_blank is not None):
            # The real store re-reads the note inside the transform, so a guard on a note it
            # does not hold raises FileNotFoundError rather than passing or abstaining.
            raise FileNotFoundError(ref)
        if require_status is not None \
                and _status.normalize(str(fresh.fm.get("status") or "")) not in require_status:
            return False
        if require_blank is not None \
                and any(str(fresh.fm.get(k) or "").strip() for k in require_blank):
            return False
        self.fields.setdefault(ref, {}).update(fields)
        if fresh is not None:
            fresh.fm.update(fields)
        return True
    def hold_for_signoff(self, ref, *, pending, claims):
        # Mirrors Vault.hold_for_signoff: stamp only if no tailored_cv on the FRESH note.
        fresh = self._fresh(ref)
        if fresh is not None and fresh.fm.get("tailored_cv"):
            return False
        self.fields.setdefault(ref, {}).update({"pending_cv": pending, "needs_signoff": claims})
        if fresh is not None:
            fresh.fm.update({"pending_cv": pending, "needs_signoff": claims})
        return True
    def sign_off(self, ref, *, accept=True, require_pending=None):
        # Mirrors Vault.sign_off's outcome verdict on the fresh note (#60).
        fresh = self._fresh(ref)
        pending = fresh.fm.get("pending_cv") if fresh is not None else None
        if not pending:
            return "nothing"
        if require_pending is not None and pending != require_pending:
            return "stale"
        fresh.fm.pop("pending_cv", None); fresh.fm.pop("needs_signoff", None)
        if not accept:
            return "discarded"
        if fresh.fm.get("tailored_cv"):
            return "collision"
        fresh.fm["tailored_cv"] = pending
        return "promoted"

class FakeCache:
    def get_or_build(self, fm): return {"jd": {"markdown": "we value delivery"}}
    # #169: run_one now calls dossier_cache.jd_arrived(d) on every SUCCESSFUL fetch, so
    # this duck-typed double needs an answer or every test using it AttributeErrors on
    # that new line. FakeCache's fixed markdown above is a healthy, non-empty JD -- the
    # fixture every OTHER test in this file relies on meaning "the fetch worked" -- so
    # True is the answer that keeps this double's meaning consistent with what it
    # returns, not a blanket stub: test_the_cv_consumer_records_a_clean_fetch_as_not_blind
    # (tests/test_dossier_guard.py) pins that this exact combination must leave
    # dossier_failed False.
    def jd_arrived(self, dossier): return True

class FakeRenderer:
    """The Renderer seam, injected. Records the DOCUMENT it was asked to render so a test
    can assert a CV was NEVER rendered -- which is the fabrication gate's whole point --
    and which attempt's text it was handed."""
    def __init__(self): self.rendered = []
    def render(self, document, out_dir, *, neutral_name="CV.pdf"):
        self.rendered.append(document)
        return f"/tmp/x/{neutral_name}"


def _bullets(rend):
    """The one role's bullets of every document `rend` was handed."""
    return [d.work[0].bullets for d in rend.rendered]


def _profiles(rend):
    """The profile of every document `rend` was handed: which ATTEMPT rendered."""
    return [d.profile for d in rend.rendered]


def _profile(reply_text):
    """The profile a scripted reply carries, to compare against `_profiles`."""
    return json.loads(reply_text)["profile"]


class FakeBackend:
    def __init__(self, cv_out, audit_out="supported\tx\tSF1"):
        self.cv_out = cv_out; self.audit_out = audit_out
        self.last_backend = "primary"; self.calls = 0
    def complete(self, prompt):
        self.calls += 1
        # A compose prompt gets the scripted reply; everything else is the audit.
        return Completion(self.cv_out if prompt.startswith(_COMPOSE) else self.audit_out)

# `CI` in the body is load-bearing (#194): CLEAN_REPLY's `CI` bullet names it, and the
# unbundled-term check reports a capitalised term the bundle never carries. Without it every
# test composing CLEAN_REPLY would get a retry it does not credit, which in review MASKED the
# slop-driven retry other tests exist to witness. No digit added, so no allowlist moves.
ENTRIES = [{"title": "Grew team", "company": "Example Foundry", "best_for": "delivery",
            "category": "people", "metrics": "3 8", "body": "Grew 3 to 8 with CI."}]

def _cfg():
    from sluice.cv.config import CvConfig
    c = CvConfig(); c.served_dir = "/tmp/cvserved"
    # INSIDE the per-test sandbox, because every run that reaches composition writes its
    # diagnostic artefacts under output_dir/<slug>/ (cv/artefacts.py). It was a fixed
    # /tmp/cvout, harmless while nothing but FakeRenderer ever "wrote" there, and then one
    # directory shared by every test in the session. HOME is what conftest's autouse
    # `_pin_paths` points at a fresh tmp_path for each test, and this helper takes no
    # fixture of its own because several other test files import and call it.
    # test_cv_run_artefacts.py::test_the_shared_engine_config_writes_inside_the_test_sandbox
    # pins it.
    c.output_dir = os.path.join(os.environ["HOME"], "cvout")
    # prefix_map defaults to {}; the replies cite EF1, so the single ENTRIES company must
    # code to "EF1" (the 2-letter fallback for "Example Foundry" would yield "EX1").
    c.prefix_map = {"Example Foundry": "EF"}
    return c


# ── the replies (#364/#365/#368, spec §5.2) ─────────────────────────────────────────
CLEAN_BULLETS = ("Shipped", "Grew team from 3 to 8", "Coached", "CI")


def _reply(profile="I build reliable systems.", bullets=CLEAN_BULLETS, skills=()):
    """A reply for SYNTHETIC_LAYOUT's one role (R1, Example Foundry), every bullet citing
    EF1. `CI` is in ENTRIES' body, so naming it draws no unbundled-term finding."""
    return json.dumps({"profile": profile,
                       "roles": {"R1": [{"text": b, "cites": ["EF1"]} for b in bullets]},
                       "skills": list(skills)})


CLEAN_REPLY = _reply()
# HARD-dirty and nothing else: an em dash in a bullet the model wrote, slop.HARD's blocking
# tier. The bullet keeps its citation and gains no number, so check_selection reports
# nothing -- the ONLY thing wrong with this reply is the HARD slop rule, which is what makes
# it a clean discriminator between the two tiers.
HARD_DIRTY_REPLY = _reply(bullets=("Shipped", "Grew team from 3 to 8",
                                   "Coached \u2014 and mentored", "CI"))
# HARD-clean, STYLE-dirty: "leverage" is a slop._PHRASES stem, in the profile the model
# wrote. No digit, so the profile figure check stays clean.
STYLE_DIRTY_REPLY = _reply(profile="I leverage the same delivery patterns across teams.")
# Two STYLE findings where STYLE_DIRTY_REPLY has one (#194 retention): `leverage` and
# `seamless` are both slop._PHRASES stems.
STYLE_DIRTIER_REPLY = _reply(profile="I leverage seamless delivery patterns across teams.")
# ONE finding, like STYLE_DIRTY_REPLY, but different text -- so a tie between the two is
# observable in what renders.
STYLE_DIRTY_B_REPLY = _reply(profile="I foster the same delivery patterns across teams.")
# A hard-clean reply whose profile names a term no source carries (#194).
UNBUNDLED_TERM_REPLY = _reply(profile="I build reliable systems on Examplequery.")
# STYLE_DIRTY_REPLY's ONE slop finding plus ONE unbundled term (#194): a reply whose
# findings are split across BOTH deterministic members of the retained tuple, so the
# retention comparison can only rank it correctly by counting the term member too.
STYLE_DIRTY_WITH_TERM_REPLY = _reply(
    profile="I leverage the same delivery patterns across teams on Examplequery.")
# A slop stem in an EMPLOYER heading is vault text and draws nothing; the same stem family in
# the profile is the model's and is found. Run against EMPLOYER_PHRASE_LAYOUT. The heading
# keeps this file's synthetic "Example <Word>" convention.
EMPLOYER_PHRASE_REPLY = _reply(profile="I streamline delivery for platform teams.")
EMPLOYER_PHRASE_LAYOUT = dataclasses.replace(SYNTHETIC_LAYOUT, roles=(dataclasses.replace(
    SYNTHETIC_LAYOUT.roles[0], heading="Example Leverage",
    employers=("Example Foundry",)),))
# A bullet with no citation: the fabrication gate's UNCITED BULLET row.
UNCITED_REPLY = json.dumps({
    "profile": "I build reliable systems.",
    "roles": {"R1": [{"text": "Shipped", "cites": ["EF1"]},
                     {"text": "Grew team from 3 to 8", "cites": []}]},
    "skills": []})

_DRAFTS = {
    "clean": CLEAN_REPLY,
    "unbundled-term": UNBUNDLED_TERM_REPLY,
    "style-dirty-with-term": STYLE_DIRTY_WITH_TERM_REPLY,
    "hard-clean-style-dirty": STYLE_DIRTY_REPLY,
    "hard-clean-style-dirtier": STYLE_DIRTIER_REPLY,
    "hard-clean-style-dirty-b": STYLE_DIRTY_B_REPLY,
    "hard-dirty": HARD_DIRTY_REPLY,
    "employer-phrase": EMPLOYER_PHRASE_REPLY,
}


def _selected(reply_text, *, entries=ENTRIES, layout=SYNTHETIC_LAYOUT):
    """`reply_text` run through the same pure functions run_one runs, in the same order:
    the bundle, the slots, the reply read, the selection. So a fixture's premise is
    computed the way the engine computes it rather than restated by hand."""
    from sluice.core.layout import build_slots
    from sluice.cv.reply import Reply, extract_json, parse_reply
    from sluice.cv.selection import build_pool, select

    bundle = build_bundle(entries, [], [], {"Example Foundry": "EF"})
    slots = build_slots(layout, bundle["entries"])
    reply = parse_reply(extract_json(reply_text), [s.id for s in slots])
    assert isinstance(reply, Reply), reply
    selection = select(reply, slots, build_pool([], entries), layout.skills_max)
    return bundle, slots, selection


def _tiers(reply_text, *, layout=SYNTHETIC_LAYOUT):
    """(gate violations, HARD slop findings, STYLE phrase findings, unbundled terms) for
    one reply -- each tier the engine reads, over the model's own text only."""
    from sluice.cv.bundle import term_vocabulary
    from sluice.cv.document import model_lines
    from sluice.cv.selection import zero_bullet_findings
    from sluice.cv.slop import check_hard, check_phrases
    from sluice.cv.terms import unbundled_terms
    from sluice.cv.validate import check_selection, entry_facts

    bundle, slots, selection = _selected(reply_text, layout=layout)
    violations = (zero_bullet_findings(selection, slots)
                  + check_selection(selection, slots, entry_facts(bundle, layout)))
    lines = model_lines(selection, slots)
    hard = [label for _n, text in lines for _ln, label, _snip in check_hard(text)]
    style = check_phrases(lines)
    terms = [t for _ln, t, _s in unbundled_terms(lines, term_vocabulary(bundle, layout))]
    return violations, hard, style, terms


def _audited(reply_text):
    """What the advisory audit is shown for `reply_text` (cv/document.py::audit_text)."""
    from sluice.cv.document import audit_text
    _bundle, slots, selection = _selected(reply_text)
    return audit_text(selection, slots)


def _excerpt(reply_text, *, layout=SYNTHETIC_LAYOUT):
    """The model's own lines for `reply_text`, joined: what the voice judge is shown."""
    from sluice.cv.document import model_lines
    _bundle, slots, selection = _selected(reply_text, layout=layout)
    return "\n".join(text for _ln, text in model_lines(selection, slots))


def test_clean_cv_is_actually_clean(monkeypatch):
    # CLEAN_REPLY's cleanliness is a PREMISE of every skipped-gate test below: those
    # assert the engine skips when the gate fails, and they keep passing if CLEAN_REPLY
    # silently stops being clean -- vacuously, for the wrong reason. State the premise
    # instead of implying it, through the same pure functions the engine runs...
    assert _tiers(CLEAN_REPLY) == ([], [], [], [])
    # ...and through the engine itself: rendered on the FIRST attempt with no finding of
    # any kind, the bullets exactly as replied.
    _served(monkeypatch)
    be, rend = FakeBackend(CLEAN_REPLY), FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                FakeVault(ENTRIES), _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "rendered"
    assert (r.violations, r.slop, r.terms, r.voice_flags, r.skills_dropped,
            r.bullets_trimmed) == ([], [], [], [], [], [])
    assert be.calls == 2, "one compose and one audit: a finding would have cost a retry"
    assert _bullets(rend) == [list(CLEAN_BULLETS)]


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
    # compose returns an uncited bullet -> the gate fails both attempts -> skip
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(UNCITED_REPLY), FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("UNCITED" in x for x in r.violations)
    assert v.written == {}   # nothing recorded
    # THE assertion. `v.written == {}` says nothing about rendering: the gate path returns
    # before set_tailored_cv either way. Only this proves no PDF with an uncited claim
    # was written to the output dir under the neutral filename -- the exact file a user
    # picks up and attaches to an application.
    assert rend.rendered == [], "a CV was RENDERED despite an open fabrication gate"

def test_dry_run_reports_but_writes_nothing():
    v = FakeVault(ENTRIES)
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(CLEAN_REPLY), FakeCache(), renderer=FakeRenderer(), dry_run=True)
    assert r.status == "dry-run"
    assert v.written == {}

def test_batch_skips_leads_that_already_have_a_cv():
    notes = [Note({"status": "shortlist", "company": "A", "role": "Analyst", "tailored_cv": "x.pdf"})]
    v = FakeVault(ENTRIES, notes=notes)
    results = run_batch(v, _cfg(), FakeBackend(CLEAN_REPLY), FakeCache(), renderer=FakeRenderer(), dry_run=True)
    assert results[0].status == "skipped-has-cv"

def test_non_shortlist_lead_is_refused():
    v = FakeVault(ENTRIES)
    r = run_one(Note({"status": "new", "company": "Acme"}), v, _cfg(),
                FakeBackend("x"), FakeCache(), renderer=FakeRenderer())
    assert r.status == "skipped-selection"
    assert v.written == {}


def test_a_blank_candidate_profile_is_refused_before_any_spend():
    # #107 superseded #99 3b's old mechanism (cvcfg.name still "Your Name"): identity
    # now comes from the vault, so an all-blank Candidate Profile note -- the state an
    # install that never ran `sluice init`'s interview leaves behind -- is what a
    # blank-default install actually looks like, not a specific placeholder string
    # comparison. Refuse before any spend, mirroring the #9 staleness guard immediately
    # above it in cv/engine.py. The zero-calls assertion is the load-bearing one:
    # "refuses" alone would also be satisfied by a refusal AFTER an LLM call. See also
    # test_a_blank_derived_name_refuses_before_any_backend_spend below, which pins the
    # identical claim through a REAL Vault reading a REAL (missing) note rather than
    # this file's fake -- the two are deliberately redundant across the Store boundary.
    v = FakeVault(ENTRIES, candidate=CandidateProfile())
    rend = FakeRenderer()
    be = FakeBackend(CLEAN_REPLY)
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "skipped-config"
    assert be.calls == 0, "the blank profile was refused AFTER an LLM call, not before"
    assert v.written == {}
    assert rend.rendered == []


# ── #107: the identity gate proven through the REAL Store, not FakeVault ──────────
# Every test above seeds identity through FakeVault.candidate, a hand-maintained fake.
# These three drive run_one through a REAL Vault(tmp_path) reading a REAL Candidate
# Profile note (Task 2's read_candidate_profile, the same code path `sluice cv run`
# hits in production) -- proof the wiring holds across the Store boundary, not merely
# that this file's fake was told to answer a certain way.
def _note():
    return Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})


class _CountingBackend:
    """Wraps FakeBackend, counting every complete() call. The load-bearing witness for
    #107: the refusal must fire BEFORE any backend spend, and asserting the RESULT
    alone is satisfied even by a composer that ran first and only failed afterward --
    only a zero-call count proves nothing was spent.
    """
    def __init__(self, cv_out=CLEAN_REPLY):
        self._inner = FakeBackend(cv_out)
        self.last_backend = self._inner.last_backend
        self.calls = 0
    def complete(self, prompt):
        self.calls += 1
        return self._inner.complete(prompt)


def _vault_with_candidate(tmp_path, overrides):
    """A REAL Vault, not FakeVault: the #107 refusal must be proven through
    Store.read_candidate_profile itself (Task 2's real reader over a real note),
    not a fake that could silently diverge from it. `overrides` is written as the
    note's frontmatter verbatim (bare `key: value` lines); an empty dict writes no
    note at all, exercising read_candidate_profile's OWN missing-note abstain path
    (see tests/test_vault_candidate_profile.py) rather than an empty-but-present one.

    Also seeds the CV Layout note (#364/#365/#368): `run_one` refuses a vault with no
    layout (`skipped-config`) before any spend -- the refusal that replaced the retired
    baseline CV's missing-file raise -- so the rows here that must REACH the backend (test_a_fully_
    declared_identity_reaches_the_backend, the both-declared case of test_run_ones_
    skipped_config_status_and_doctors_candidate_profile_row_agree, and
    tests/test_onboard_questions.py's candidate-note probe, which borrows this helper)
    need one. No Experience Library entry is written: read_evidence abstains to [] on a missing library
    (the ordinary "no entries yet" case, tests/harness/config.py's own comment on
    _seed_vault makes the same choice), and this helper only needs the backend to be
    CALLED, never a CV that clears the fabrication gate.
    """
    from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH, CV_LAYOUT_RELPATH
    from sluice.core.vault import Vault
    from tests.conftest import layout_yaml

    if overrides:
        dest = os.path.join(str(tmp_path), CANDIDATE_PROFILE_RELPATH)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        fm = "\n".join(f"{k}: {v}" for k, v in overrides.items())
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(f"---\n{fm}\n---\n")
    layout = os.path.join(str(tmp_path), CV_LAYOUT_RELPATH)
    os.makedirs(os.path.dirname(layout), exist_ok=True)
    with open(layout, "w", encoding="utf-8") as fh:
        fh.write(layout_yaml())
    return Vault(str(tmp_path))


def test_a_blank_derived_name_refuses_before_any_backend_spend(tmp_path):
    """#107: the refusal must happen BEFORE the backend call AND before the dossier
    fetch, not after either. Asserting the result alone would pass even if the
    engine composed first and refused after -- the whole point is no spend.

    RecordingCache, not FakeCache (round-1 review finding): FakeCache.get_or_build
    records nothing it was asked, so a mutant that moved this refusal to sit AFTER
    `dossier_cache.get_or_build(fm)` -- reachable, since #9's staleness guard right
    above this one already fetches nothing, so nothing else in this function's
    control flow forces the ordering -- left `backend.calls == 0` green while a real
    browser fetch had already happened. RecordingCache's own docstring calls itself
    "the ONLY witness for the gate's PLACEMENT" for the identical reason on the #9
    guard immediately above; the same argument applies here.
    """
    vault = _vault_with_candidate(tmp_path, {})       # all-blank profile
    backend = _CountingBackend()
    cache = RecordingCache()
    res = run_one(_note(), vault, _cfg(), backend, cache, renderer=FakeRenderer())
    assert res.status == "skipped-config"
    assert backend.calls == 0, "a blank identity must cost no backend call"
    assert cache.calls == 0, "a blank identity must cost no dossier fetch"


def test_a_declared_name_with_blank_contact_also_refuses_before_spend(tmp_path):
    # #107's actual reported shape: the name was fine, the CONTACT was blank. Same
    # RecordingCache reasoning as the test above -- see its comment.
    vault = _vault_with_candidate(tmp_path, {"forenames": "Ada", "surname": "Example"})
    backend = _CountingBackend()
    cache = RecordingCache()
    res = run_one(_note(), vault, _cfg(), backend, cache, renderer=FakeRenderer())
    assert res.status == "skipped-config"
    assert backend.calls == 0
    assert cache.calls == 0, "a blank identity must cost no dossier fetch"


def test_a_name_with_blank_contact_declared_the_other_way_also_refuses(tmp_path):
    # I2 (round-1 review): the refusal condition is `not cv_name.strip() or not
    # cv_contact.strip()` -- an OR of two independently-blank operands. The two
    # tests above cover "both blank" and "name declared, contact blank"; neither
    # covers the mirror shape, a user who fills `mobile` and leaves forenames/
    # surname empty. Reachable in practice (a real vault note filled in top to
    # bottom, contact fields first) and exactly the #107 harm if missed: the CV
    # would compose with a blank headline and burn the spend (it once also failed
    # the since-removed header guard on every attempt). Without this test, deleting the `not cv_name.strip()`
    # term outright is a pure delete-mutation that survives the whole suite, since
    # the two tests above still refuse through the surviving `not cv_contact.strip()`
    # operand alone.
    vault = _vault_with_candidate(tmp_path, {"mobile": "+1 555 0100"})
    backend = _CountingBackend()
    cache = RecordingCache()
    res = run_one(_note(), vault, _cfg(), backend, cache, renderer=FakeRenderer())
    assert res.status == "skipped-config"
    assert backend.calls == 0
    assert cache.calls == 0


def _cfg_unserved():
    """_cfg() with serving off (the --no-serve idiom). The real-Vault rows here seed no
    experience entry, so the CV Layout's one slot can cite nothing: every bullet is
    trimmed, the selection is hard-clean, and the run reaches render -- where FakeRenderer's
    made-up path would otherwise reach the real `serve`."""
    cfg = _cfg()
    cfg.served_dir = ""
    return cfg


def test_a_fully_declared_identity_reaches_the_backend(tmp_path):
    vault = _vault_with_candidate(tmp_path, {"forenames": "Ada", "surname": "Example",
                                             "email": "ada@example.invalid"})
    backend = _CountingBackend()
    run_one(_note(), vault, _cfg_unserved(), backend, FakeCache(), renderer=FakeRenderer())
    assert backend.calls >= 1


def test_run_ones_skipped_config_status_and_doctors_candidate_profile_row_agree(tmp_path):
    """M3 (doctor task-8 fix round 1): run_one's `skipped-config` refusal
    (`not cv_name.strip() or not cv_contact.strip()`, sluice/cv/engine.py) and
    classify_store's Candidate Profile row (`not (name_present and
    contact_present)`, sluice/core/doctor.py) are De Morgan-identical over the
    same two pure derivations (full_name/contact_block) applied to the same
    store read -- but they are two SEPARATE lines of code, not one shared
    implementation, so nothing before this test could catch a third
    requirement added to one side and not the other: doctor would keep
    reporting OK for an identity a real compose still refuses on (or the
    reverse -- doctor DEAD-blocking a compose that would actually proceed).

    Round-trips all four (name, contact) shapes the file's other tests above
    already cover individually -- both-blank, name-only, contact-only,
    both-declared -- through BOTH `run_one` and `classify_store` off the SAME
    seeded vault, and asserts the two never disagree on any of them."""
    from sluice.core.doctor import DEAD, DEGRADED, SETUP, classify_store

    # Each shape gets its OWN vault directory. `_vault_with_candidate` writes NO note at
    # all for `{}` (that is how the missing-note abstain path is exercised), so on a
    # SHARED tmp_path the both-blank row only tests what it claims to because it happens
    # to run first: move it after a populated row and it reads the previous row's note
    # instead, asserting on a foreign identity while staying green. `str(tmp_path /
    # label)` is the isolation `tests/test_onboard_questions.py`'s `status()` helper
    # already uses for the same reason.
    shapes = [
        ("both-blank", {}),
        ("name-only", {"forenames": "Ada", "surname": "Example"}),
        ("contact-only", {"mobile": "+1 555 0100"}),
        ("both-declared", {"forenames": "Ada", "surname": "Example",
                           "email": "ada@example.invalid"}),
    ]
    for label, overrides in shapes:
        vault = _vault_with_candidate(str(tmp_path / label), overrides)
        engine_refused = run_one(_note(), vault, _cfg_unserved(), _CountingBackend(),
                                 FakeCache(),
                                 renderer=FakeRenderer()).status == "skipped-config"
        rows = [c for c in classify_store(vault.preflight()) if c.subject == "Candidate Profile"]
        assert len(rows) == 1, f"{label}: expected exactly one Candidate Profile row"
        # Keyed on `blocks`, not on a state NAME. #243 renamed this row's blocking state
        # DEAD -> SETUP (an unfilled Candidate Profile is unsupplied, not broken) without
        # touching what it blocks, and a state-keyed assertion would have read that rename
        # as "doctor stopped reporting the problem" -- when the row, its detail and its
        # `blocks=("cv",)` were all unchanged. `blocks` is also the thing the claim is
        # actually about: doctor must say cv is stopped exactly when `run_one` stops it.
        # The state conjunct names the three states `verdict()` and `exit_code` actually
        # ACT on, not merely "not OK". Measured: with `state != OK`, flipping this row to
        # NOTICE (leaving `blocks` alone) left this test green while `verdict()` dropped
        # the row entirely and printed `Ready now: tailored CVs` for a vault `run_one`
        # refuses -- the exact disagreement the test is named for.
        doctor_blocks_cv = "cv" in rows[0].blocks and rows[0].state in (SETUP, DEGRADED, DEAD)
        assert engine_refused == doctor_blocks_cv, (
            f"{label}: run_one refused={engine_refused} but doctor blocks cv="
            f"{doctor_blocks_cv} (state={rows[0].state!r}, blocks={rows[0].blocks!r})")
        # ...and the EXPECTED verdict for this shape, not merely that the two agree.
        # Agreement alone is order-blind: both sides read the same vault, so a note left
        # behind by a previous iteration moves them together and the assertion above
        # stays green while the row silently stops testing the shape it names. Only
        # `both-declared` clears the gate; the other three are each missing at least one
        # half of the identity. This is what gives the per-label vault directory above a
        # hostile witness -- without the isolation, running `both-declared` before
        # `both-blank` leaves a full identity in place and reddens this line.
        assert engine_refused == (label != "both-declared"), (
            f"{label}: expected refused={label != 'both-declared'}, got {engine_refused} "
            "-- this row is not exercising the identity shape it names")


def test_the_compose_prompt_carries_the_derived_identity_not_cvcfg(tmp_path):
    """#107: the composer must be named the VAULT-derived candidate, read fresh from
    `vault.read_candidate_profile()`, not any identity value diverted from that read.
    Every FakeBackend in this file returns a fixed canned reply regardless of what the
    prompt asked for, which is exactly why every OTHER "rendered" test here would stay
    green even if the composer were told the wrong name. Only inspecting the recorded
    prompt itself proves the argument at the call site.

    The CONTACT block no longer reaches the composer at all (#364/#365/#368, spec §7.1):
    sluice assembles it from the Candidate Profile itself, so the model never writes it
    and has no reason to see it. Its absence is asserted, since a prompt carrying it would
    be handing personal data to a backend for nothing.

    Originally witnessed a mutation of `name=cv_name, contact=cv_contact` to
    `name=cvcfg.name, contact=cvcfg.contact` at that call site surviving the
    entire rest of this suite (verified while writing this test). #133/#107
    (Task 9) has since removed `name`/`contact` from `CvConfig` entirely, so that
    EXACT mutation can no longer even be expressed -- it would raise
    AttributeError immediately rather than silently substituting the wrong
    value. The property this test proves is broader than that one retired
    mutation shape, though, and stays load-bearing against any future diversion
    of the identity argument (a stale cache, a hardcoded placeholder, a
    different vault read), so the test is kept rather than retired with it.
    """
    class RecordingBackend:
        def __init__(self):
            self.last_backend = "primary"; self.prompts = []
        def complete(self, prompt):
            self.prompts.append(prompt)
            return Completion(CLEAN_REPLY if prompt.startswith(_COMPOSE) \
                else "supported\tx\tSF1")

    vault = _vault_with_candidate(
        tmp_path, {"forenames": "Distinctive", "surname": "Candidate",
                   "email": "distinctive@example.invalid"})
    be = RecordingBackend()
    run_one(_note(), vault, _cfg_unserved(), be, FakeCache(), renderer=FakeRenderer())
    compose_prompts = [p for p in be.prompts if p.startswith(_COMPOSE)]
    assert compose_prompts, "compose was never reached"
    assert "Distinctive Candidate" in compose_prompts[0], (
        "the compose prompt did not carry the vault-derived name -- compose() may "
        "be reading a diverted identity instead of the derived cv_name")
    assert "distinctive@example.invalid" not in compose_prompts[0], (
        "the compose prompt carried the candidate's contact block, which sluice now "
        "assembles itself -- the model has no use for it")


def test_slop_allow_reaches_the_shipped_compose_prompt():
    """#167 (Task 17, item 1): cv.slop_allow must reach the ACTUAL compose() call
    engine.py makes, not merely be plumbed through compose.py's own build_structured_prompt in
    isolation. A unit test of that function alone (see
    tests/test_cv_structured_prompt.py::test_an_allowed_phrase_is_not_instructed_against_either)
    would stay green even if run_one's `_compose.compose_structured(...)` call site never forwarded
    `cvcfg.slop_allow` -- the parameter would exist and be dead. Mirrors
    test_the_compose_prompt_carries_the_derived_identity_not_cvcfg immediately above:
    only inspecting the recorded prompt itself proves the argument at the real call
    site, not merely a guard reading it back.

    "leverage" is chosen because it appears NOWHERE in this file's fixtures (ENTRIES,
    CLEAN_REPLY, the identity block) outside the ban-list sentence itself, so its absence
    from the shipped prompt can only mean slop_allow suppressed it there.

    `dry_run=True`: this test's only interest is the PROMPT compose() was sent, not the
    render/serve tail end of run_one -- CLEAN_REPLY clears the hard gate on attempt 1, and
    the real (unmocked) `sluice.cv.render.serve` would otherwise run against a path
    FakeRenderer never actually writes.
    """
    class RecordingBackend:
        def __init__(self):
            self.last_backend = "primary"; self.prompts = []
        def complete(self, prompt):
            self.prompts.append(prompt)
            return Completion(CLEAN_REPLY if prompt.startswith(_COMPOSE) \
                else "supported\tx\tSF1")

    v = FakeVault(ENTRIES)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    cfg = _cfg()
    cfg.slop_allow = ["leverage"]
    be = RecordingBackend()
    run_one(note, v, cfg, be, FakeCache(), renderer=FakeRenderer(), dry_run=True)
    compose_prompts = [p for p in be.prompts if p.startswith(_COMPOSE)]
    assert compose_prompts, "compose was never reached"
    assert "leverage" not in compose_prompts[0], (
        "cvcfg.slop_allow did not reach the shipped compose prompt -- engine.py's "
        "_compose.compose_structured(...) call site may not be forwarding slop_allow")


def test_happy_path_renders_and_records(monkeypatch):
    import sluice.cv.render as _render_mod
    monkeypatch.setattr(_render_mod, "render",
                        lambda *a, **k: "/tmp/x/Jane Roe CV.pdf")
    monkeypatch.setattr(_render_mod, "serve",
                        lambda *a, **k: "Jane_Roe_CV_deadbeef.pdf")
    v = FakeVault(ENTRIES)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    r = run_one(note, v, _cfg(), FakeBackend(CLEAN_REPLY), FakeCache(), renderer=FakeRenderer())
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
                v, cfg, FakeBackend(CLEAN_REPLY), FakeCache(), renderer=rend)
    assert r.status == "rendered"
    # The render still happened; only serving was skipped.
    assert _bullets(rend) == [list(CLEAN_BULLETS)]
    assert r.served is None
    assert v.written == {}   # no tailored_cv marker when nothing was published

def test_slop_only_failure_fails_gate_and_feeds_retry():
    # A reply that is correctly cited (the selection check passes clean) but whose first
    # bullet contains an em dash -- a slop HARD error in the model's own text. Proves (a) a
    # slop-only failure still fails the gate, and (b) the SLOP message reaches the retry
    # prompt via prior_findings.
    slop_cv = _reply(bullets=("Shipped, launched \u2014 and iterated", "Grew team from 3 to 8"))

    class RecordingBackend:
        def __init__(self, cv):
            self.cv = cv; self.last_backend = "primary"; self.prompts = []
        def complete(self, prompt):
            self.prompts.append(prompt)
            # Mirrors FakeBackend's routing: a compose prompt opens with _COMPOSE.
            return Completion(self.cv if prompt.startswith(_COMPOSE) else "supported\tx\tSF1")

    be = RecordingBackend(slop_cv)
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert r.slop                      # slop error surfaced
    assert v.written == {}             # nothing rendered/recorded
    # the SECOND compose prompt must carry the slop feedback
    compose_prompts = [p for p in be.prompts if p.startswith(_COMPOSE)]
    assert len(compose_prompts) == 2
    assert "SLOP" in compose_prompts[1]
    assert rend.rendered == [], "a CV was RENDERED despite an open fabrication gate"

def test_retry_happens_exactly_once():
    # A persistently gate-failing reply must be composed exactly twice: the initial
    # attempt plus the single retry, then skip -- never a third attempt.
    v = FakeVault(ENTRIES)
    backend = FakeBackend(UNCITED_REPLY)
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), backend, FakeCache(), renderer=FakeRenderer())
    assert r.status == "skipped-gate"
    assert backend.calls == 2   # audit is never reached on skipped-gate, so this
                                # counts compose calls exactly

def test_advisory_audit_failure_does_not_block_render_but_holds_the_cv(monkeypatch):
    # The audit is explicitly advisory ("NEVER blocks", audit.py). A backend error
    # or timeout during the audit call must not prevent a CV that already passed
    # the HARD citation gate from rendering -- it is caught and logged.
    import sluice.cv.render as _render_mod
    monkeypatch.setattr(_render_mod, "render",
                        lambda *a, **k: "/tmp/x/Jane Roe CV.pdf")
    monkeypatch.setattr(_render_mod, "serve",
                        lambda *a, **k: "Jane_Roe_CV_deadbeef.pdf")

    class AuditRaisingBackend:
        def __init__(self, cv):
            self.cv = cv; self.last_backend = "primary"; self.audited = False
        def complete(self, prompt):
            # compose call succeeds with a clean, fully-cited reply; the audit call
            # (same routing rule as FakeBackend: anything not opening with _COMPOSE)
            # raises, simulating a backend timeout/error.
            if prompt.startswith(_COMPOSE):
                return Completion(self.cv)
            self.audited = True
            raise RuntimeError("backend timeout during advisory audit")

    v = FakeVault(ENTRIES)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    be = AuditRaisingBackend(CLEAN_REPLY)
    # _cfg() carries require_signoff's default (True). Before #333 this pinned the #60
    # FAIL-OPEN -- no flags, so the pointer was set and the CV served unreviewed. An audit
    # that could not run has checked nothing, so it now HOLDS the CV like an `unsupported`
    # flag: rendered and served, but withheld from send-ready until a human signs off.
    rend = FakeRenderer()
    r = run_one(note, v, _cfg(), be, FakeCache(), renderer=rend)
    assert _bullets(rend) == [list(CLEAN_BULLETS)], "an audit failure must not stop the render"
    assert r.status == "needs-signoff"
    assert r.audit_flags == []
    assert be.audited, "the audit was never invoked; the hold assertion would be vacuous"
    assert note.ref not in v.written, "an unaudited CV must not get the send-ready pointer"


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
    be = FakeBackend(CLEAN_REPLY, audit_out="unsupported\tMotivated by placeholder\tNONE")
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
    be = FakeBackend(CLEAN_REPLY, audit_out="paraphrase\tgrew it\tEF1\nsupported\tled\tEF1")
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
    be = FakeBackend(CLEAN_REPLY, audit_out="unsupported\tMotivated by placeholder\tNONE")
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
    be = FakeBackend(CLEAN_REPLY, audit_out="supported\tx\tEF1")
    r = run_one(note, v, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    assert r.status == "skipped-needs-signoff"
    assert be.calls == 0, "a held (pending) lead was recomposed -- the audit could re-roll clean"
    assert note.ref not in v.written and "tailored_cv" not in note.fm
    # ...and the batch path (which routes through run_one) skips it identically.
    note2 = Note(dict(fm))
    vb = FakeVault(ENTRIES, notes=[note2])
    beb = FakeBackend(CLEAN_REPLY, audit_out="supported\tx\tEF1")
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
    be = FakeBackend(CLEAN_REPLY, audit_out="unsupported\tMotivated by placeholder\tNONE")
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
    be = FakeBackend(CLEAN_REPLY, audit_out="unsupported\tMotivated by placeholder\tNONE")
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
        def render(self, document, out_dir, *, neutral_name="CV.pdf"):
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
    results = run_batch(v, _cfg(), FakeBackend(CLEAN_REPLY), FakeCache(), renderer=FlakyRenderer())
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
        the lead's url -- exactly what get_or_build raises in production.

        No jd_arrived here (#169): run_one's new call to it sits INSIDE the same
        try block, after get_or_build's own line, so a raise from get_or_build never
        reaches it -- the `except` arm sets dossier_failed and moves on. A double
        whose get_or_build always raises has no jd_arrived branch to exercise.
        """
        def get_or_build(self, fm):
            raise urlguard.DossierBlocked(urlguard.BLOCKED_ADDRESS)

    class _BoomRenderer:
        """A downstream failure UNRELATED to the dossier (e.g. WeasyPrint), so this
        test proves the flag survives a SECOND, independent exception -- not just
        the dossier's own."""
        def render(self, document, out_dir, *, neutral_name="CV.pdf"):
            raise RuntimeError("weasyprint boom")

    notes = [Note({"status": "shortlist", "company": "Acme", "role": "Analyst"})]
    v = FakeVault(ENTRIES, notes=notes)
    results = run_batch(v, _cfg(), FakeBackend(CLEAN_REPLY), _BlockedCache(),
                        renderer=_BoomRenderer())
    assert len(results) == 1
    assert results[0].status == "error"
    assert results[0].dossier_failed is True, \
        "the dossier WAS blocked -- run_batch's catch-all must not silently lose that"


class _VariableJdCache:
    """A dossier cache whose JD content the CALLER chooses, unlike FakeCache's and
    RecordingCache's fixed non-empty one. Neither of those can exercise jd_arrived's
    negative branch (see the #169 comment on each), so the test below -- which needs
    to prove a SUCCESSFUL fetch that returns no JD is flagged, not just a raising one
    -- needs a double whose answer can actually vary.

    jd_arrived mirrors DossierCache.jd_arrived's core rule (core/dossier.py): an
    empty/blank markdown never arrived. It does not model the real class's min_jd_chars
    floor -- this test is about the fact of an empty JD, not the floor -- so it is a
    narrower, purpose-built stand-in rather than a full fake of the real class.
    """
    def __init__(self, jd_markdown):
        self._jd_markdown = jd_markdown

    def get_or_build(self, fm):
        return {"jd": {"markdown": self._jd_markdown}}

    def jd_arrived(self, dossier):
        markdown = (dossier.get("jd") or {}).get("markdown")
        return bool(isinstance(markdown, str) and markdown.strip())


def _run_one(tmp_path, *, jd_markdown):
    """A single shortlist lead composed against CLEAN_REPLY, with the fetched JD content
    controlled by the caller via _VariableJdCache. served_dir="" is the file's existing
    no-serve idiom (see test_no_serve_renders_but_does_not_mark_lead) -- this helper is
    about dossier_failed, not the served pointer, so skipping serve keeps the test off
    disk without needing a monkeypatch fixture. tmp_path isolates output_dir from every
    other test's hardcoded _cfg() value; nothing under it is actually read, since
    FakeRenderer never touches disk.
    """
    cfg = _cfg()
    cfg.output_dir = str(tmp_path / "cvout")
    cfg.served_dir = ""
    v = FakeVault(ENTRIES)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    return run_one(note, v, cfg, FakeBackend(CLEAN_REPLY), _VariableJdCache(jd_markdown),
                   renderer=FakeRenderer())


def test_a_cv_composed_without_a_JD_is_flagged_rather_than_silently_tailored(tmp_path):
    # #18 added dossier_failed for a fetch that RAISED. A fetch that succeeds and
    # returns page chrome is the same fact wearing different clothes: without the
    # flag, "status: rendered" is indistinguishable from a CV genuinely tailored to a
    # real job description. Control flow is deliberately unchanged -- composing from
    # the bundle alone is degraded, not wrong, and skipping the lead here would be a
    # bigger behaviour change than this issue should carry.
    res = _run_one(tmp_path, jd_markdown="")
    assert res.status == "rendered"
    assert res.dossier_failed is True


def test_batch_records_error_when_the_response_is_truncated():
    # A truncated response (finish_reason==length) is a hard error, not a silent
    # partial (see OpenAiCompatibleBackend.complete). Drive this through a real
    # RetryingBackend + OpenAiCompatibleBackend and prove the batch surfaces "error",
    # never a rendered CV built from the partial content -- and that the truncation,
    # being non-transient, is not retried.
    calls = []

    def truncated_http(url, data, headers, timeout):
        calls.append(url)
        return ('{"choices":[{"message":{"content":"JANE ROE\\n\\nWORK EXP"},'
                '"finish_reason":"length"}]}')

    backend = RetryingBackend(
        OpenAiCompatibleBackend("m", base_url="http://x", api_key="k", http=truncated_http),
        retries=2, label="openai m", sleep=lambda s: None)

    notes = [Note({"status": "shortlist", "company": "Acme", "role": "Analyst"})]
    v = FakeVault(ENTRIES, notes=notes)
    results = run_batch(v, _cfg(), backend, FakeCache(), renderer=FakeRenderer())
    assert len(results) == 1
    assert results[0].status == "error"
    assert v.written == {}   # never marked tailored off a truncated partial
    assert len(calls) == 1


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
    r = run_one(note, v, _cfg(), FakeBackend(CLEAN_REPLY), FakeCache(), renderer=rend,
                guard_existing_cv=True)
    assert r.status == "skipped-has-cv"
    # The render itself still happened -- the CV passed the gate and was rendered/served
    # before the write race was discovered; only the note pointer write was withheld.
    assert _bullets(rend) == [list(CLEAN_BULLETS)]
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
    r = run_one(note, v, _cfg(), FakeBackend(CLEAN_REPLY), FakeCache(), renderer=FakeRenderer())
    assert r.status == "rendered"
    assert v.read_leads()[0].fm.get("tailored_cv") != "PREEXISTING.pdf (2026-07-10)"
    assert "Jane_Roe_CV_deadbeef.pdf" in v.read_leads()[0].fm.get("tailored_cv")


def test_the_fake_vault_conforms_to_the_real_store_signature():
    """The join between "conformance tests real stores" and "engine tests use a fake" was
    MANUAL, and that is exactly how a total breakage of `cv run` shipped green: the fake's
    baseline-CV reader (since retired, #364/#365/#368) still took an argument the real Vault
    had dropped, so the engine's stale call site was invisible.

    Any method this fake implements must match the real Vault's signature. It need not
    implement all of them -- it is a fake for the CV path -- but where it does, it may not
    drift.
    """
    import inspect
    from sluice.core.vault import Vault

    fake = FakeVault([])
    shared = sorted(n for n in dir(Store)
                    if not n.startswith("_") and callable(getattr(Store, n))
                    and hasattr(FakeVault, n))
    # Scope floor: the members the CV path drives must be among those checked.
    assert {"read_evidence", "read_candidate_profile", "read_cv_layout",
            "set_tailored_cv", "read_leads"} <= set(shared), shared
    for name in shared:
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
    # #169: same reasoning as FakeCache.jd_arrived beside it (this class returns the
    # identical fixed, non-empty markdown) -- every test below that reaches PAST the
    # staleness gate (cache.calls == 1) now calls this, not just get_or_build.
    def jd_arrived(self, dossier): return True


_STALE_FM = {"status": "shortlist", "company": "Example Foundry", "role": "Analyst",
             "last_seen": "2026-01-01"}
_POLICY = StalenessPolicy(ttl_days=90, today="2026-07-27")


def test_stale_lead_is_skipped_before_any_dossier_fetch():
    v, cache, rend, be = FakeVault(ENTRIES), RecordingCache(), FakeRenderer(), FakeBackend(CLEAN_REPLY)
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
    run_one(Note(note_fm), FakeVault(ENTRIES), cfg, FakeBackend(CLEAN_REPLY), cache,
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
    r = run_one(Note(held), FakeVault(ENTRIES), _cfg(), FakeBackend(CLEAN_REPLY),
                FakeCache(), renderer=FakeRenderer(), policy=_POLICY)
    assert r.status == "skipped-needs-signoff"


def test_run_batch_skips_a_stale_lead():
    v = FakeVault(ENTRIES, notes=[Note(dict(_STALE_FM))])
    cache = RecordingCache()
    out = run_batch(v, _cfg(), FakeBackend(CLEAN_REPLY), cache, renderer=FakeRenderer(),
                    policy=_POLICY)
    assert [r.status for r in out] == ["skipped-stale"]
    assert cache.calls == 0


# ── #1: two shortlist notes claiming one slug ─────────────────────────────────
_TWIN_FM = {"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}
_TWIN_DIR = "Job Applications/Job Leads"


def _twins():
    """Two notes at one basename in two subfolders -- the state a recursive scan (#1)
    admits and a flat store could not. `Note.slug` is the basename, so both issue the
    same slug while their refs differ, which is exactly what the store hands back."""
    return [Note(dict(_TWIN_FM), path=f"{_TWIN_DIR}/Active/Example Foundry - Analyst.md"),
            Note(dict(_TWIN_FM), path=f"{_TWIN_DIR}/Archive/Example Foundry - Analyst.md")]


def test_run_batch_composes_for_neither_of_two_notes_claiming_one_slug():
    """`run_batch` walked `read_leads` directly -- the same shape that let
    `apply/select.py:select_all` keep both twins -- so a single job was composed TWICE.

    Asserted on the SPEND, not only on the status strings: the statuses alone stay green if
    the guard is moved below the compose, which is the placement that would make it useless.
    """
    v = FakeVault(ENTRIES, notes=_twins())
    be, cache = FakeBackend(CLEAN_REPLY), RecordingCache()
    out = run_batch(v, _cfg(), be, cache, renderer=FakeRenderer())
    assert [r.status for r in out] == ["skipped-ambiguous", "skipped-ambiguous"]
    assert be.calls == 0 and cache.calls == 0      # no LLM call, no dossier fetch
    assert v.written == {} and v.fields == {}      # neither twin got a pointer or a hold


def test_run_batch_names_the_colliding_refs(caplog):
    """The refs, never the slug alone: these notes collide BY slug, so repeating it names
    nothing a human can act on while the paths name the two files to rename or merge."""
    with caplog.at_level("WARNING"):
        run_batch(FakeVault(ENTRIES, notes=_twins()), _cfg(), FakeBackend(CLEAN_REPLY),
                  RecordingCache(), renderer=FakeRenderer())
    said = " ".join(r.getMessage() for r in caplog.records)
    assert f"{_TWIN_DIR}/Active/Example Foundry - Analyst.md" in said
    assert f"{_TWIN_DIR}/Archive/Example Foundry - Analyst.md" in said


def test_run_batch_still_composes_an_unambiguous_lead_beside_a_twin_pair(monkeypatch):
    """MIRROR HARM. The guard drops the two notes that collide and nothing else -- a blanket
    refusal would pass the test above and silently stop every CV in a vault holding one
    hand-made duplicate."""
    import sluice.cv.render as _render_mod
    monkeypatch.setattr(_render_mod, "serve", lambda *a, **k: "Jane_Roe_CV_deadbeef.pdf")
    ordinary = Note({"status": "shortlist", "company": "Example Systems", "role": "Clerk"},
                    path=f"{_TWIN_DIR}/Example Systems - Clerk.md")
    v = FakeVault(ENTRIES, notes=[*_twins(), ordinary])
    out = run_batch(v, _cfg(), FakeBackend(CLEAN_REPLY), FakeCache(), renderer=FakeRenderer())
    by_ref = {r.lead: r.status for r in out}
    assert by_ref[ordinary.ref] == "rendered"
    assert set(v.written) == {ordinary.ref}
    assert [s for s in by_ref.values() if s == "skipped-ambiguous"] == ["skipped-ambiguous"] * 2


# ── #167: the loop retains the last HARD-clean draft, and rebinds before the audit ──
#
# "Attempt 1 clears the HARD gate but carries a STYLE finding" is a sequence NO fixture
# in this file could produce before #167: the loop broke the moment the HARD gate was
# clean, so attempt 2 never ran and nothing here ever exercised a retained draft
# outliving a dirtier retry. The replies in `_DRAFTS` (defined with CLEAN_REPLY above)
# exist to build those sequences.


def test_the_sequence_fixtures_are_the_tiers_they_claim():
    """PREMISE of every test below, per fixture and per TIER.

    Each test below asserts a SEQUENCE outcome, and every one of them stays green if a
    fixture silently drifts into a different tier: a "hard-clean-style-dirty" reply that
    had become HARD-dirty would still produce skipped-gate, for a reason that has nothing
    to do with retention. The same trap test_clean_cv_is_actually_clean closes for
    CLEAN_REPLY. Computed through `_tiers`, the engine's own pure functions in its order.
    """
    from sluice.cv.slop import check_phrases

    for name, layout, hard, style in [
        ("clean", SYNTHETIC_LAYOUT, False, False),
        ("hard-clean-style-dirty", SYNTHETIC_LAYOUT, False, True),
        ("hard-clean-style-dirtier", SYNTHETIC_LAYOUT, False, True),
        ("hard-clean-style-dirty-b", SYNTHETIC_LAYOUT, False, True),
        ("hard-dirty", SYNTHETIC_LAYOUT, True, False),
        ("employer-phrase", EMPLOYER_PHRASE_LAYOUT, False, True),
        ("unbundled-term", SYNTHETIC_LAYOUT, False, False),
        ("style-dirty-with-term", SYNTHETIC_LAYOUT, False, True),
    ]:
        violations, hard_slop, phrases, _terms = _tiers(_DRAFTS[name], layout=layout)
        assert violations == [], f"{name} is no longer gate-clean: {violations}"
        assert bool(hard_slop) is hard, f"{name}'s HARD tier drifted"
        assert bool(phrases) is style, f"{name}'s STYLE tier drifted"

    # The employer heading's OWN phrase must exist, or the scoping test below asserts the
    # absence of something that was never there in the first place.
    assert check_phrases([(1, EMPLOYER_PHRASE_LAYOUT.roles[0].heading)]), (
        "the employer heading no longer matches a slop._PHRASES stem, so the scoping "
        "test below would pass without the engine scoping anything")


def test_the_retention_fixtures_carry_the_finding_counts_they_claim():
    """PREMISE of the two retention tests below: a 'fewer findings' comparison means
    nothing unless the fixtures really differ in count, and a stem leaving
    slop._PHRASES would silently collapse them to a tie."""
    assert len(_tiers(STYLE_DIRTY_REPLY)[2]) == 1
    assert len(_tiers(STYLE_DIRTY_B_REPLY)[2]) == 1
    assert len(_tiers(STYLE_DIRTIER_REPLY)[2]) == 2


def test_the_unbundled_term_fixture_carries_exactly_one_term():
    assert _tiers(UNBUNDLED_TERM_REPLY)[3] == ["Examplequery"]


def test_the_style_dirty_with_term_fixture_carries_one_slop_and_one_term():
    """PREMISE of the term-counting retention row below: one finding in EACH member."""
    _violations, _hard, phrases, terms = _tiers(STYLE_DIRTY_WITH_TERM_REPLY)
    assert len(phrases) == 1
    assert terms == ["Examplequery"]


class _SequenceBackend:
    """Hands back a scripted SEQUENCE of composed drafts, one per compose call, and
    records what every AUDIT call was asked to audit.

    The audited text is recovered from the audit prompt itself -- cv/audit.py's
    build_audit_prompt appends `"=== CV ===\\n" + cv_text + "\\n"` -- rather than by
    monkeypatching run_audit, so these tests exercise the real seam run_one calls. The
    marker assertion below is what turns a prompt-shape change into a loud failure
    instead of a silently empty `audited` list.
    """
    _CV_MARKER = "=== CV ===\n"

    def __init__(self, drafts, audit_out="supported\tx\tSF1"):
        # Draft NAMES, resolved per call rather than up front: "backend-error" is not a
        # draft at all but an instruction to fail the way core/backends fails, and no
        # text could express that. A compose that never RETURNS is a case the retry has
        # to survive, not a shape of CV.
        self.drafts = list(drafts)
        self.audit_out = audit_out
        self.last_backend = "primary"
        self.compose_prompts = []
        self.audited = []

    def complete(self, prompt):
        # Same routing rule as FakeBackend: a compose prompt opens with _COMPOSE, and
        # anything else is the audit.
        if prompt.startswith(_COMPOSE):
            self.compose_prompts.append(prompt)
            # Running past the end of the sequence means the engine composed more times
            # than the retry budget allows -- a regression to report, not a shortfall to
            # improvise a draft for.
            assert len(self.compose_prompts) <= len(self.drafts), (
                f"the engine composed {len(self.compose_prompts)} times; this sequence "
                f"scripts {len(self.drafts)} draft(s) and the retry budget is one")
            name = self.drafts[len(self.compose_prompts) - 1]
            if name == "backend-error":
                # The shape core/backends raises when every leg is down, the request
                # times out, or a reply is truncated at max_tokens. compose() catches
                # nothing, so this lands in the engine's loop.
                raise BackendError("compose timeout: every backend leg is down")
            return Completion(_DRAFTS[name])
        body = prompt.partition(self._CV_MARKER)[2]
        assert body, "cv/audit.py no longer carries the CV under '=== CV ==='"
        self.audited.append(body[:-1])   # build_audit_prompt appends exactly one "\n"
        return Completion(self.audit_out)


def _run_sequence(monkeypatch, drafts, *, layout=SYNTHETIC_LAYOUT):
    """run_one over a scripted sequence of composed drafts.

    Returns (result, backend, renderer): the renderer records what SHIPPED and the
    backend records what was AUDITED, which is the pair these tests compare. _served
    stands in for the real render/serve seam for the same reason every other "rendered"
    test in this file does it -- FakeRenderer hands back a path that does not exist.
    """
    _served(monkeypatch)
    be, rend = _SequenceBackend(drafts), FakeRenderer()
    v = FakeVault(ENTRIES, layout=layout)
    res = run_one(Note({"status": "shortlist", "company": "Example Foundry",
                        "role": "Analyst"}),
                  v, _cfg(), be, FakeCache(), renderer=rend)
    return res, be, rend


def test_skipped_gate_IFF_no_attempt_was_ever_hard_clean(monkeypatch):
    """The safety property: `skipped-gate` iff no attempt was ever HARD-clean.

    The loop is two attempts, so the space is SEQUENCES, not per-attempt outcomes -- a
    table of (hard, style) per-attempt combinations samples something else entirely. But
    these rows are a SAMPLE too, and saying otherwise would be the claim this file most
    wants to avoid: six of the nine two-attempt sequences over the three tiers. What
    makes the sample worth having is the second assertion, which DERIVES the expected
    verdict from the sequence instead of reading it off a hand-written column, so a row
    added later cannot be given a wrong expectation.

    A STYLE finding must NEVER bin a lead -- attempt 2 is an unconstrained,
    non-deterministic compose, so a loop that discarded a hard-clean draft to chase a
    phrase would lose the lead whenever the retry came back worse, which is exactly what
    the decision to HOLD rather than block exists to avoid.
    """
    for seq, expected in [
        (["hard-dirty", "hard-dirty"], "skipped-gate"),
        (["hard-dirty", "clean"], "rendered"),
        (["clean", "clean"], "rendered"),
        # `best` first set on attempt 2 AND carrying live style findings -- the one
        # sequence that produces that `(cv_text, style_msgs)` state, which is what a
        # surviving-style consequence would read.
        (["hard-dirty", "hard-clean-style-dirty"], "rendered"),
        (["hard-clean-style-dirty", "hard-dirty"], "rendered"),        # the regression
        (["hard-clean-style-dirty", "hard-clean-style-dirty"], "rendered"),
    ]:
        res, _be, _rend = _run_sequence(monkeypatch, seq)
        # The FULL status, not `!= "skipped-gate"`. `error`, `skipped-has-cv` and
        # `dry-run` all satisfy that weaker form, so a lead lost to an exception or held
        # back by a clobber guard would have read as a pass -- and one of them really
        # was live: a retry that RAISED returned `error` here (see
        # test_a_retry_that_RAISES_still_ships_the_draft_attempt_1_earned).
        assert res.status == expected, (seq, res.status)
        # ...and the property those statuses encode, DERIVED from the sequence rather
        # than restated: every draft name here except "hard-dirty" clears the HARD gate,
        # and a scripted hard-clean draft is always reached (attempt 1 always runs, and
        # the loop only breaks early on a draft that was itself hard-clean).
        assert (res.status != "skipped-gate") == any(d != "hard-dirty" for d in seq), (
            seq, res.status)


def test_a_hard_clean_draft_is_rendered_even_when_the_retry_comes_back_dirty(monkeypatch):
    """The sequence nothing in this file could produce before #167.

    The pre-#167 loop broke the moment the HARD gate was clean, so a hard-clean attempt 1
    WAS what rendered. Adding a STYLE tier that feeds the retry must not change that: the
    retained draft, not the dirtier retry, is what ships -- and a HARD-dirty attempt 2
    must never reach the renderer, which validates nothing itself.
    """
    res, be, rend = _run_sequence(monkeypatch, ["hard-clean-style-dirty", "hard-dirty"])
    assert res.status == "rendered"
    assert len(be.compose_prompts) == 2, "the STYLE finding never reached the retry"
    assert _profiles(rend) == [_profile(STYLE_DIRTY_REPLY)], (
        "the retained HARD-clean draft is what must ship")


def test_a_retry_with_MORE_style_findings_does_not_replace_a_cleaner_draft(monkeypatch):
    """#194, spec §2.3. Both drafts are hard-clean; attempt 2 is style-WORSE. The loop used
    to keep whichever hard-clean draft came LAST, so the dirtier retry shipped. With
    `style_hold` off (the default) nothing then flagged it, and an unbundled-term finding is
    a probable invention -- so keeping the worse draft is the failure this rule removes."""
    res, be, rend = _run_sequence(
        monkeypatch, ["hard-clean-style-dirty", "hard-clean-style-dirtier"])
    assert res.status == "rendered"
    assert len(be.compose_prompts) == 2, "the style finding never reached the retry"
    assert _profiles(rend) == [_profile(STYLE_DIRTY_REPLY)], "the cleaner attempt-1 draft must ship"
    # The audit reads the same retained draft the renderer got (the engine's rebind).
    assert be.audited == [_audited(STYLE_DIRTY_REPLY)]


def test_a_tie_in_style_findings_keeps_the_later_draft(monkeypatch):
    """A tie keeps attempt 2: it was composed with attempt 1's findings in front of it,
    and keeping it is the pre-#194 behaviour, so a tie changes nothing."""
    res, _be, rend = _run_sequence(
        monkeypatch, ["hard-clean-style-dirty", "hard-clean-style-dirty-b"])
    assert res.status == "rendered"
    assert _profiles(rend) == [_profile(STYLE_DIRTY_B_REPLY)]


def test_the_audit_runs_over_the_RENDERED_draft_not_the_discarded_one(monkeypatch):
    """The retained selection is read post-loop TWICE -- by run_audit and by
    renderer.render -- and the audit's flags drive unsupported_claims -> hold_for_signoff
    -> the withheld tailored_cv. Auditing one attempt while rendering another means a
    fabricated claim in the SERVED CV goes un-held, is written send-ready, and the run
    reports "rendered / audit flags: 0".

    Agreement alone is NOT the property, which is why the last assertion is here and is
    not a restatement of the first: dropping the rebind entirely leaves BOTH readers on
    the discarded attempt-2 reply, so they still agree -- on a CV that never cleared the
    HARD gate. They must agree ON THE RETAINED ATTEMPT.
    """
    from sluice.cv.slop import check_hard

    res, be, rend = _run_sequence(monkeypatch, ["hard-clean-style-dirty", "hard-dirty"])
    assert res.status == "rendered"
    assert be.audited, "the audit never ran, so the comparisons below would be vacuous"
    # Both readers on the RETAINED attempt-1 reply: its profile and its (em-dash-free)
    # bullets rendered, and its text is exactly what the audit was shown.
    assert (_profiles(rend), _bullets(rend)) == ([_profile(STYLE_DIRTY_REPLY)],
                                                 [list(CLEAN_BULLETS)])
    assert be.audited[-1] == _audited(STYLE_DIRTY_REPLY), (
        "the audit ran over a draft the user never sees")
    assert not check_hard(be.audited[-1]), (
        "both readers moved together onto the DISCARDED, HARD-dirty draft")


def test_a_phrase_in_an_EMPLOYER_line_never_reaches_the_retry(monkeypatch):
    """The scoping guarantee, pinned where the scoping actually HAPPENS.

    cv/slop.py's check_phrases has no opinion about which lines it is handed (it is
    deliberately dependency-free); the ENGINE is what must hand it only the text the MODEL
    wrote (cv/document.py::model_lines). A retry message naming an employer heading is
    answerable only by RENAMING THE EMPLOYER -- a style rule turned into fabrication
    pressure, the shape CLAUDE.md records as the worst case this codebase has shipped.

    The heading itself legitimately appears in every compose prompt -- it names the slot
    the bullets go under -- so the absence asserted is of a FINDING about it, in the
    retry's own findings block.
    """
    _res, be, _rend = _run_sequence(monkeypatch, ["employer-phrase", "employer-phrase"],
                                    layout=EMPLOYER_PHRASE_LAYOUT)
    assert len(be.compose_prompts) == 2, "no retry happened, so this asserts nothing"
    findings = [ln for ln in be.compose_prompts[1].splitlines() if ln.startswith("- SLOP")]
    assert any("SLOP streamline" in ln for ln in findings), (
        "the PROFILE phrase never reached the retry either, so the absence below would "
        "say nothing about SCOPING")
    assert not any("Leverage" in ln or "leverag" in ln for ln in findings), findings


def test_a_retry_that_RAISES_still_ships_the_draft_attempt_1_earned(monkeypatch, caplog):
    """Retention has to cover a retry that never RETURNS, not just one that comes back
    worse.

    `_compose.compose_structured` catches nothing (cv/compose.py), so a BackendError -- a timeout,
    every fallback leg down, a reply truncated at max_tokens -- propagates out of this
    loop, past the retained draft, to run_one's outer `except: raise` and then to
    run_batch, which records `error`. The CONTROL is the whole argument: that identical
    backend failure is HARMLESS when attempt 1 is style-CLEAN, because no second compose
    is attempted at all (see the sibling below). So the only thing that turns it into a
    lost lead is a phrase match -- and a phrase may never cost a lead (#167).
    """
    with caplog.at_level("WARNING"):
        res, be, rend = _run_sequence(monkeypatch,
                                      ["hard-clean-style-dirty", "backend-error"])
    assert res.status == "rendered"
    assert len(be.compose_prompts) == 2, "the retry never happened, so nothing raised"
    assert _profiles(rend) == [_profile(STYLE_DIRTY_REPLY)]
    assert any("compose timeout" in r.getMessage() for r in caplog.records), (
        "a swallowed backend failure that logs nothing is invisible in production")


def test_the_same_backend_failure_is_harmless_when_attempt_1_is_style_clean(monkeypatch):
    """The CONTROL for the test above, and the reason this is a REGRESSION rather than a
    pre-existing weakness: with a style-clean attempt 1 the loop breaks and the scripted
    failure is never reached. That was the path EVERY hard-clean attempt 1 took before
    the style tier existed."""
    res, be, _rend = _run_sequence(monkeypatch, ["clean", "backend-error"])
    assert res.status == "rendered"
    assert len(be.compose_prompts) == 1, "a style-clean draft must not buy a second call"


def test_a_FIRST_compose_that_raises_still_bins_the_lead(monkeypatch):
    """MIRROR HARM. The guard above must not swallow a failure with nothing retained
    behind it: with `best` unset there is no draft to ship, and turning a backend outage
    into a silent non-result would be strictly worse than today's `error`. A bare `raise`
    keeps both the behaviour and the original traceback."""
    with pytest.raises(BackendError):
        _run_sequence(monkeypatch, ["backend-error"])


# ── #167 Task 14: the opt-in model-judged VOICE check (cv/voice.py) ──────────────
#
# A separate scripted backend rather than an extension of _SequenceBackend: the VOICE
# prompt (cv/voice.py's "You are judging the VOICE...") shares no marker with either
# the compose prompt (`_COMPOSE`) or the audit one ("auditing"), and folding a
# third prompt kind into _SequenceBackend's two-way dispatch risks a voice call being
# silently misrouted into `audited` -- corrupting every OTHER test in this file that
# reads `be.audited` -- rather than a clean failure local to these tests.

# cv/voice.py's own content delimiter. Deliberately NOT _SequenceBackend._CV_MARKER
# ("=== CV ==="), which belongs to the AUDIT prompt: the two prompts carry different
# documents and now say so, and a test that split on the wrong one would silently read
# the whole CV back out of a voice prompt and pass.
_VOICE_MARKER = "=== EXCERPT ==="


def _voice_judge(excerpt, marks):
    """The scripted model, as a REACTIVE rule rather than a fixed reply: flag every
    line of the text it was SHOWN that contains one of `marks`.

    A fixed `voice_out` cannot witness a retention row decided by WHICH attempt carried a
    finding: the judge must react to the text it was shown. Made a module-level function,
    not a method, so a premise row can run the SAME rule over a fixture's excerpt and
    prove which lines it fires on.
    """
    return "".join(f"flag\t{line.strip()}\treads as machine-generated\n"
                   for line in excerpt.splitlines()
                   if any(m in line for m in marks))


class _VoiceBackend:
    """Scripts the compose drafts (from _DRAFTS, one per call) and the model's own
    reply to the VOICE check, and records which KIND every `complete()` call carried
    -- "compose", "voice", or "audit" -- so a wiring test can assert not just the
    outcome but which calls were made, and how many.

    `voice_marks` swaps the fixed `voice_out` for `_voice_judge` over the text this
    backend was actually shown; `voice_prompts` keeps those texts so a test can assert
    on the INPUT directly and not only on what came back.
    """

    def __init__(self, drafts, *, voice_out="", voice_raises=False, voice_marks=(),
                audit_out="supported\tx\tSF1"):
        self.drafts = list(drafts)
        self.voice_out = voice_out
        self.voice_raises = voice_raises
        self.voice_marks = tuple(voice_marks)
        self.audit_out = audit_out
        self.last_backend = "primary"
        self.calls = []                # "compose" | "voice" | "audit", in call order
        self.compose_prompts = []
        self.voice_prompts = []

    def _usage(self, prompt):
        """A synthetic usage report, because a real backend always files one (#308). Sized
        from the prompt so the three stages come out in realistic proportions rather than
        uniform -- mirrors tests/harness/backend.py::ScriptedBackend._usage."""
        from sluice.core.backends import Usage
        return Usage(provider="scripted", model="scripted-model",
                     input_tokens=max(1, len(prompt) // 4), output_tokens=1,
                     cache_read_tokens=0)

    def complete(self, prompt):
        first = prompt.splitlines()[0] if prompt else ""
        if first.startswith("You are judging the VOICE"):
            self.calls.append("voice")
            self.voice_prompts.append(prompt)
            if self.voice_raises:
                raise RuntimeError("voice backend down")
            if self.voice_marks:
                # Judge only what follows the prompt's own content marker, so the rule
                # can never fire on the instruction preamble. cv/voice.py's marker is
                # "=== EXCERPT ===" and cv/audit.py's is "=== CV ===" (the audit really
                # is handed the document) -- distinct, so neither backend can split on
                # the other's prompt by accident. Indexed `[1]`, never `[-1]`: a marker
                # that stopped matching would make `[-1]` hand back the WHOLE prompt and
                # scan the preamble with it, passing silently.
                return Completion(_voice_judge(prompt.split(_VOICE_MARKER, 1)[1], self.voice_marks), usage=self._usage(prompt))
            return Completion(self.voice_out, usage=self._usage(prompt))
        if prompt.startswith(_COMPOSE):
            self.calls.append("compose")
            self.compose_prompts.append(prompt)
            assert len(self.compose_prompts) <= len(self.drafts), (
                f"the engine composed {len(self.compose_prompts)} times; this "
                f"sequence scripts {len(self.drafts)} draft(s)")
            return Completion(_DRAFTS[self.drafts[len(self.compose_prompts) - 1]], usage=self._usage(prompt))
        self.calls.append("audit")
        return Completion(self.audit_out, usage=self._usage(prompt))


def _run_voice_sequence(monkeypatch, drafts, *, voice_check, entries=ENTRIES, **kw):
    """run_one over a scripted draft sequence with `cv.voice_check` set. Returns
    (result, backend) -- mirrors _run_sequence, minus the renderer no test below
    needs to inspect.

    `entries` defaults to the shared ENTRIES fixture every existing caller in this
    file uses unmodified. The one caller that needs a SOURCED skill declared
    (#168's row 2 containment check now reaches the SKILLS region too, so an
    un-sourced skill line fails the HARD gate before the STYLE tier this test
    isolates ever runs) passes its own single-entry list rather than widening
    ENTRIES -- and, with it, every other test in this file that builds on ENTRIES
    unmodified.
    """
    _served(monkeypatch)
    be = _VoiceBackend(drafts, **kw)
    v = FakeVault(entries)
    cfg = _cfg()
    cfg.voice_check = voice_check
    res = run_one(Note({"status": "shortlist", "company": "Example Foundry",
                        "role": "Analyst"}),
                  v, cfg, be, FakeCache(), renderer=FakeRenderer())
    return res, be


def test_voice_check_off_by_default_makes_no_extra_backend_call(monkeypatch):
    # An unconfigured install (voice_check defaults False -- #167 Task 11's
    # CvConfig field) must make ZERO additional backend calls -- not "a call that is
    # skipped quickly", literally no `complete()` invocation shaped like the voice
    # prompt.
    res, be = _run_voice_sequence(monkeypatch, ["clean"], voice_check=False)
    assert res.status == "rendered"
    assert "voice" not in be.calls


def test_a_voice_backend_error_degrades_to_no_findings_rather_than_blocking(
        monkeypatch, caplog):
    # Fails OPEN, exactly as the fabrication audit does (cv/audit.py): a gate must
    # never be harder than the check that actually ran.
    with caplog.at_level("WARNING"):
        res, be = _run_voice_sequence(monkeypatch, ["clean"], voice_check=True,
                                      voice_raises=True)
    assert res.status == "rendered"
    assert be.calls.count("voice") == 1
    assert any("voice check" in r.getMessage() for r in caplog.records), (
        "a swallowed voice-backend failure that logs nothing is the counting-only "
        "`except` this repo has a real incident for")


def test_the_voice_check_does_not_run_while_the_hard_gate_is_dirty(monkeypatch):
    # No point spending a call judging the voice of a draft about to be recomposed
    # for citation reasons anyway.
    res, be = _run_voice_sequence(monkeypatch, ["hard-dirty", "clean"],
                                  voice_check=True)
    assert res.status == "rendered"
    assert be.calls.count("voice") == 1


def test_a_voice_finding_reaches_the_retry(monkeypatch):
    res, be = _run_voice_sequence(
        monkeypatch, ["clean", "clean"], voice_check=True,
        voice_out="flag\tThis reads like a press release.\n")
    assert res.status == "rendered"
    assert be.calls.count("compose") == 2, "the VOICE finding never reached the retry"
    assert "VOICE: flag\tThis reads like a press release." in be.compose_prompts[1]


# ── #194: retention counts VOICE findings too ────────────────────────────────────────
#
# The fewest-findings comparison (spec §2.3) sums slop AND voice findings, on BOTH sides
# of the comparison. The style-only retention rows above drive no voice check, so a
# comparison that dropped either voice term would leave them green. Each row below is
# decided by a voice count alone, and asserts the retained draft through the result's
# own findings, which describe that draft and no other.

# Hard-clean, slop-clean, and differing from CLEAN_REPLY on TWO lines the model wrote -- the
# profile and a bullet -- so a reactive voice judge can flag two lines of THIS reply and none
# of STYLE_DIRTY_REPLY's. No digit and no new citation: the HARD gate is untouched.
_VOICE_TWO_LINE_REPLY = _reply(profile="I build reliable systems for every team.",
                               bullets=("Shipped the reporting layer", "Grew team from 3 to 8",
                                        "Coached", "CI"))
_VOICE_TWO_MARKS = ("for every team", "the reporting layer")


def _run_voice_rendered(monkeypatch, backend):
    """run_one with `cv.voice_check` on over a prepared backend, returning (result,
    renderer) so a row can assert what SHIPPED as well as the findings it carries."""
    _served(monkeypatch)
    rend = FakeRenderer()
    cfg = _cfg()
    cfg.voice_check = True
    res = run_one(Note({"status": "shortlist", "company": "Example Foundry",
                        "role": "Analyst"}),
                  FakeVault(ENTRIES), cfg, backend, FakeCache(), renderer=rend)
    return res, rend


def test_the_voice_retention_fixture_is_hard_and_slop_clean_and_marks_only_itself():
    """PREMISE of the voice-retention rows: the two-line reply is hard- and slop-clean,
    each mark hits exactly one of its lines, and neither mark hits STYLE_DIRTY_REPLY --
    otherwise the voice counts below are not the ones the rows claim."""
    violations, hard, phrases, _terms = _tiers(_VOICE_TWO_LINE_REPLY)
    assert (violations, hard, phrases) == ([], [], [])
    assert _voice_judge(_excerpt(_VOICE_TWO_LINE_REPLY), _VOICE_TWO_MARKS).count("flag\t") == 2
    assert _voice_judge(_excerpt(STYLE_DIRTY_REPLY), _VOICE_TWO_MARKS) == ""


def test_a_voice_finding_ties_with_a_slop_finding_and_the_later_draft_is_kept(monkeypatch):
    """Attempt 1: zero slop, ONE voice finding. Attempt 2: ONE slop finding, zero voice.
    A tie, so attempt 2 is kept. This row is what sees the RETAINED draft's voice count:
    dropped from the comparison, attempt 1 reads as cleaner and wrongly survives. The
    row below sees the NEW attempt's voice count."""
    be = _VoiceBackend(["clean", "hard-clean-style-dirty"],
                       voice_marks=("I build reliable systems.",))
    res, rend = _run_voice_rendered(monkeypatch, be)
    assert res.status == "rendered"
    assert be.calls.count("voice") == 2, "both attempts must have been voice-judged"
    assert _profiles(rend) == [_profile(STYLE_DIRTY_REPLY)]
    assert res.voice_flags == []


def test_a_retry_with_MORE_voice_findings_does_not_replace_a_cleaner_draft(monkeypatch):
    """Attempt 1: ONE slop finding, zero voice. Attempt 2: zero slop, TWO voice findings.
    Attempt 2 is strictly worse only once its OWN voice findings are counted, so
    attempt 1 ships."""
    be = _VoiceBackend(["hard-clean-style-dirty", "voice-two-line"],
                       voice_marks=_VOICE_TWO_MARKS)
    monkeypatch.setitem(_DRAFTS, "voice-two-line", _VOICE_TWO_LINE_REPLY)
    res, rend = _run_voice_rendered(monkeypatch, be)
    assert res.status == "rendered"
    assert be.calls.count("voice") == 2, "both attempts must have been voice-judged"
    assert _profiles(rend) == [_profile(STYLE_DIRTY_REPLY)]
    assert res.voice_flags == []


class _SecondVoiceCallRaises(_VoiceBackend):
    """A voice check that is measured on attempt 1 and RAISES on attempt 2 -- the outage
    shape `_VoiceBackend`'s all-or-nothing `voice_raises` cannot script."""

    def complete(self, prompt):
        if (prompt.splitlines()[0].startswith("You are judging the VOICE")
                and self.calls.count("voice") == 1):
            self.calls.append("voice")
            raise RuntimeError("voice backend down")
        return super().complete(prompt)


def test_a_retry_whose_voice_check_FAILED_does_not_replace_a_measured_draft(monkeypatch):
    """F5. A voice check that raised fails open to no flags, which counts as zero voice
    findings -- so without a guard, a retry nobody voice-judged out-ranks an attempt 1 whose
    one voice finding was really measured. Attempt 2 here is also slop-clean, so its
    unmeasured zero is the only thing that could make it win."""
    clean_b = _reply(profile="I build dependable systems.")
    monkeypatch.setitem(_DRAFTS, "clean-b", clean_b)
    be = _SecondVoiceCallRaises(["clean", "clean-b"],
                                voice_marks=("I build reliable systems.",))
    res, rend = _run_voice_rendered(monkeypatch, be)
    assert res.status == "rendered"
    assert be.calls.count("voice") == 2, "attempt 2's voice check must have been attempted"
    assert _profiles(rend) == [_profile(CLEAN_REPLY)], "the voice-MEASURED attempt 1 must ship"
    assert len(res.voice_flags) == 1, res.voice_flags


class _FirstVoiceCallRaises(_VoiceBackend):
    """The mirror of `_SecondVoiceCallRaises`: attempt 1's voice check RAISES, attempt 2's
    is measured."""

    def complete(self, prompt):
        if (prompt.splitlines()[0].startswith("You are judging the VOICE")
                and self.calls.count("voice") == 0):
            self.calls.append("voice")
            raise RuntimeError("voice backend down")
        return super().complete(prompt)


# The symmetric case of F5 (#194 cleanup C4). An attempt 1 whose voice check raised AND
# carried no style finding is never followed by a retry -- the loop stops on a draft with
# no findings -- so attempt 1 here carries ONE term finding, which is what makes a retry
# happen at all. A MEASURED retry is then judged on the count alone, both ways.

def test_a_measured_retry_with_no_more_findings_replaces_an_unmeasured_draft(monkeypatch):
    be = _FirstVoiceCallRaises(["unbundled-term", "clean"],
                               voice_marks=("I build reliable systems.",))
    res, rend = _run_voice_rendered(monkeypatch, be)
    assert be.calls.count("voice") == 2, "both attempts' voice checks must have run"
    assert res.status == "rendered"
    assert _profiles(rend) == [_profile(CLEAN_REPLY)], "a tie keeps the later, MEASURED draft"
    assert len(res.voice_flags) == 1 and res.terms == [], (res.voice_flags, res.terms)


def test_a_measured_retry_with_MORE_findings_does_not_replace_an_unmeasured_draft(
        monkeypatch):
    be = _FirstVoiceCallRaises(["unbundled-term", "hard-clean-style-dirty"],
                               voice_marks=("I leverage the same",))
    res, rend = _run_voice_rendered(monkeypatch, be)
    assert be.calls.count("voice") == 2, "both attempts' voice checks must have run"
    assert res.status == "rendered"
    assert _profiles(rend) == [_profile(UNBUNDLED_TERM_REPLY)], (
        "attempt 1 counts one finding, attempt 2 a slop plus a voice flag")
    assert res.voice_flags == [], res.voice_flags


def test_a_retry_whose_voice_check_failed_still_replaces_an_UNMEASURED_draft(monkeypatch):
    """Pins `best_voice_measured`'s `not voice_failed`: when BOTH checks raised, neither
    draft was voice-judged, so the count alone decides and the cleaner retry ships. Were a
    raised attempt 1 recorded as measured, the outage guard would refuse attempt 2."""
    res, rend = _run_voice_rendered(
        monkeypatch, _VoiceBackend(["hard-clean-style-dirty", "clean"], voice_raises=True))
    assert res.status == "rendered"
    assert _profiles(rend) == [_profile(CLEAN_REPLY)]


# ── #167: the STYLE tier's two halves judge one set of lines ─────────────────────────
#
# The phrase list and the voice judge are both handed the text the MODEL wrote
# (cv/document.py::model_lines) and nothing else: a complaint about an employer heading or
# a certificate -- vault text -- is answerable only by renaming the thing it names, a style
# rule turned into fabrication pressure. tests/test_cv_structured_engine.py::
# test_the_voice_judge_is_shown_only_the_models_text pins the voice half against vault text;
# this row pins that the two halves see the SAME lines.

def test_the_voice_check_is_shown_exactly_the_lines_the_phrase_tier_is(monkeypatch):
    """The two halves of the STYLE tier judge ONE set of lines, not two.

    Asserted against `model_lines` itself rather than against a transcribed list, so a
    later change to what counts as the model's text moves both halves together or fails
    here.
    """
    _res, be = _run_voice_sequence(monkeypatch, ["hard-clean-style-dirty", "clean"],
                                   voice_check=True)
    assert be.voice_prompts, "the voice check never ran"
    shown = be.voice_prompts[0].split(_VOICE_MARKER, 1)[1].strip()
    assert shown == _excerpt(STYLE_DIRTY_REPLY).strip()

    # Non-vacuity: the shown text drops everything sluice assembles from the vault. Named
    # individually because "shorter" alone would also be true of a truncation bug.
    role = SYNTHETIC_LAYOUT.roles[0]
    for dropped in ("Jane Roe", "+1 555 0100", role.heading, role.title, role.location,
                    *SYNTHETIC_LAYOUT.certificates, *SYNTHETIC_LAYOUT.education):
        assert dropped not in shown, dropped
    # ...while the text the check exists to judge is all still there.
    assert "I leverage the same delivery patterns across teams." in shown
    assert "Grew team from 3 to 8" in shown


# ── #167 Task 16: CvResult.slop and CvResult.voice_flags gain readers ────────────────
#
# `slop` has had NO reader since it was added -- a field computed and never read is
# the same defect #167 opened over the slop linter's own matches. `voice_flags` is a
# brand-new field. The trap this section exists to avoid: a test asserting only that a
# field EXISTS, or that it is empty on a clean run, cannot tell a working reader from a
# broken one -- an empty list is what BOTH produce. Every test below therefore drives a
# genuinely populated case.

def test_a_rendered_results_slop_and_voice_flags_describe_the_RETAINED_draft(
        monkeypatch):
    """The populated case for the success path: attempt 1 (hard-clean-style-dirty) is
    RETAINED and carries a real STYLE phrase match plus a scripted VOICE finding;
    attempt 2 (hard-dirty) is discarded. `res.slop`/`res.voice_flags` must describe
    attempt 1, never attempt 2 -- mirroring the same retained-vs-discarded property
    test_a_hard_clean_draft_is_rendered_even_when_the_retry_comes_back_dirty already
    pins for `rend.rendered` and the retry prompt."""
    res, be = _run_voice_sequence(
        monkeypatch, ["hard-clean-style-dirty", "hard-dirty"], voice_check=True,
        voice_out="flag\tThis reads like a press release.\n")
    assert res.status == "rendered"
    assert be.calls.count("compose") == 2, "attempt 2 never ran, so this proves nothing"
    # STYLE_DIRTY_REPLY's own phrase (see its fixture comment) -- pre-formatted "SLOP
    # <phrase>: <snippet>", the same shape `hard_msgs` already used for the retry.
    assert any(s.startswith("SLOP leverage:") for s in res.slop), res.slop
    # The scripted VOICE finding, verbatim -- run_voice keeps the whole "flag\t..."
    # line, not just the phrase (cv/voice.py's own parsing).
    assert res.voice_flags == ["flag\tThis reads like a press release."]
    # attempt 2's own defect (an em dash, HARD_DIRTY_REPLY's fixture) must not appear:
    # a reader seeing it would mean the fields drifted back onto the discarded draft.
    assert not any("EM-DASH" in s for s in res.slop), res.slop


def test_skipped_gate_slop_carries_both_tiers_SLOP_formatted():
    """`slop`'s WRITER on this branch predates #167 entirely (it stored bare HARD-tier
    snippets, with no "SLOP" label and no STYLE tier at all) and had no reader either
    way, so nothing here regresses a previously-observed shape -- see the field's own
    comment on CvResult. Reformatted to match `hard_msgs`'s own "SLOP <label>:
    <snippet>" shape and folded together with the STYLE tier, so a caller printing
    `r.slop` sees every deterministic finding on the failing draft, not half of them.
    """
    both_dirty = _reply(profile=_profile(STYLE_DIRTY_REPLY),
                        bullets=("Shipped", "Coached \u2014 and mentored"))
    v = FakeVault(ENTRIES)
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry",
                      "role": "Analyst"}),
               v, _cfg(), FakeBackend(both_dirty), FakeCache(), renderer=FakeRenderer())
    assert r.status == "skipped-gate"
    assert any(s.startswith("SLOP EM-DASH:") for s in r.slop), r.slop
    assert any(s.startswith("SLOP leverage:") for s in r.slop), r.slop
    assert r.voice_flags == []


# ── #167 Task 15: cv.style_hold withholds the send-ready pointer ─────────────────────
#
# Neither _run_sequence nor _run_voice_sequence's FakeVault carries `notes=`, so none of
# their siblings could ever read back what landed in frontmatter -- every existing
# assertion there stops at `res`/`be`/`rend`. These tests need `note.fm` itself
# (pending_cv, needs_signoff, tailored_cv), so the two helpers below seed a real Note the
# vault can mutate in place and hand it back -- otherwise identical to their namesakes,
# mirroring _run_voice_sequence's own cfg-copy-and-flip pattern for style_hold instead of
# voice_check.

def _run_sequence_with_note(monkeypatch, drafts, *, style_hold=False,
                            require_signoff=True, audit_out="supported\tx\tSF1",
                            slop_allow=()):
    _served(monkeypatch)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    be = _SequenceBackend(drafts, audit_out=audit_out)
    v = FakeVault(ENTRIES, notes=[note])
    cfg = _cfg()
    cfg.style_hold = style_hold
    cfg.require_signoff = require_signoff
    cfg.slop_allow = list(slop_allow)
    res = run_one(note, v, cfg, be, FakeCache(), renderer=FakeRenderer())
    return res, note, be


def _run_voice_sequence_with_note(monkeypatch, drafts, *, voice_check, style_hold=False,
                                  **kw):
    """Only the style_hold x voice interaction test below needs note.fm from a voice-
    scripted run; every other voice test reads `be.calls`/`be.compose_prompts` and is
    served fine by the shared _run_voice_sequence."""
    _served(monkeypatch)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    be = _VoiceBackend(drafts, **kw)
    v = FakeVault(ENTRIES, notes=[note])
    cfg = _cfg()
    cfg.voice_check = voice_check
    cfg.style_hold = style_hold
    res = run_one(note, v, cfg, be, FakeCache(), renderer=FakeRenderer())
    return res, note, be


def test_a_style_finding_does_not_withhold_the_pointer_by_default(monkeypatch):
    # style_hold defaults False (CvConfig, Task 11): a STYLE finding feeds the retry
    # (Task 13) but, on its own, must not cost the lead its send-ready pointer -- riding
    # require_signoff (True by default, chosen for FABRICATION) would withhold
    # tailored_cv on ~40 case-insensitive stems out of the box at shipped defaults
    # (CvConfig.style_hold's own comment), and a rendered CV with no pointer is inert to
    # apply/select.
    res, note, _be = _run_sequence_with_note(
        monkeypatch, ["hard-clean-style-dirty", "hard-dirty"])
    assert res.status == "rendered"
    assert note.fm.get("tailored_cv"), "style_hold is off by default"
    assert "pending_cv" not in note.fm and "needs_signoff" not in note.fm


def test_style_hold_withholds_the_pointer_when_enabled(monkeypatch):
    res, note, _be = _run_sequence_with_note(
        monkeypatch, ["hard-clean-style-dirty", "hard-dirty"], style_hold=True)
    assert res.status == "needs-signoff"
    assert "tailored_cv" not in note.fm
    assert note.fm.get("pending_cv")


def test_slop_allow_suppresses_the_style_finding_at_the_ENFORCEMENT_site(monkeypatch):
    # The ENFORCEMENT half of cv.slop_allow. Its sibling
    # test_slop_allow_reaches_the_shipped_compose_prompt covers only the PROMPT half, and
    # it drives a CLEAN draft under dry_run, so no phrase ever matches and no retry ever
    # runs there: dropping `allow=` from engine.py's `_slop_phrases(...)` call reddened
    # nothing at all before this test.
    #
    # STYLE_DIRTY_REPLY's only defect is "leverage" in its profile. Allowing that stem must
    # leave the first draft clean, so the retry never fires -- ONE compose call is the
    # discriminator, and it is what an un-suppressed finding would double. style_hold is
    # ON deliberately: it makes the consequence of getting this wrong visible on the note
    # (a withheld pointer) as well as in the call count.
    res, note, be = _run_sequence_with_note(
        monkeypatch, ["hard-clean-style-dirty", "hard-dirty"],
        style_hold=True, slop_allow=["leverage"])
    assert len(be.compose_prompts) == 1, (
        "an allowed phrase still drove the retry -- cvcfg.slop_allow is not reaching "
        "the enforcement call in cv/engine.py")
    assert res.status == "rendered"
    assert note.fm.get("tailored_cv"), "an allowed phrase withheld the send-ready pointer"


def test_style_hold_withholds_even_when_require_signoff_is_off(monkeypatch):
    # cv.require_signoff continues to gate the FABRICATION hold alone -- its default was
    # chosen for fabrication, and style_hold borrows nothing from it. Turning it off must
    # not disable style_hold's own, independent consequence.
    res, note, _be = _run_sequence_with_note(
        monkeypatch, ["hard-clean-style-dirty", "hard-dirty"],
        style_hold=True, require_signoff=False)
    assert res.status == "needs-signoff"
    assert "tailored_cv" not in note.fm


def test_style_hold_claims_are_style_tagged_not_the_fabrication_shape(monkeypatch):
    # hold_for_signoff(ref, *, pending, claims) keeps its Store-protocol signature
    # unwidened: `claims` stays a flat JSON ARRAY, and core/app.py reads it back as
    # `parsed if isinstance(parsed, list) else [str(parsed)]` -- a wrapped
    # {"kind": ..., "claims": [...]} object would collapse into ONE bogus claim string,
    # so the kind has to live on each ENTRY instead. The retained draft's own STYLE
    # finding ("SLOP leverage: ...", from _slop_phrases) must reach the array tagged
    # "style\t...", distinguishing it from a raw, unprefixed audit verdict line.
    import json
    res, note, _be = _run_sequence_with_note(
        monkeypatch, ["hard-clean-style-dirty", "hard-dirty"], style_hold=True)
    assert res.status == "needs-signoff"
    claims = json.loads(note.fm["needs_signoff"])
    assert claims, "the style finding never reached the hold"
    assert all(c.startswith("style\t") for c in claims), claims
    assert any("leverag" in c for c in claims), claims


def test_a_hold_combines_fabrication_and_style_claims_in_one_call(monkeypatch):
    # Never-clobber: ONE hold_for_signoff call carries BOTH kinds when both fire -- never
    # a second write function, and never two separate holds racing each other. Also pins
    # that a legacy fabrication claim stays UNPREFIXED even once style_hold is on.
    import json
    res, note, _be = _run_sequence_with_note(
        monkeypatch, ["hard-clean-style-dirty", "hard-dirty"], style_hold=True,
        audit_out="unsupported\tMotivated by placeholder\tNONE")
    assert res.status == "needs-signoff"
    claims = json.loads(note.fm["needs_signoff"])
    fabrication = [c for c in claims if not c.startswith("style\t")]
    style = [c for c in claims if c.startswith("style\t")]
    assert fabrication == ["unsupported\tMotivated by placeholder\tNONE"]
    assert style, "the style finding was dropped once a fabrication claim also held"


def test_a_voice_finding_alone_can_trigger_the_style_hold(monkeypatch):
    # The STYLE tier is slop._PHRASES matches PLUS LLM voice findings (both feed the
    # SAME retry, Task 14), so a voice-only finding -- no slop phrase survives at all --
    # must still withhold the pointer under style_hold; the consequence is not wired to
    # style_msgs alone.
    import json
    res, note, be = _run_voice_sequence_with_note(
        monkeypatch, ["clean", "clean"], voice_check=True, style_hold=True,
        voice_out="flag\tThis reads like a press release.\n")
    assert res.status == "needs-signoff"
    assert be.calls.count("compose") == 2, "the voice finding never reached the retry"
    assert "tailored_cv" not in note.fm
    claims = json.loads(note.fm["needs_signoff"])
    assert any(c.startswith("style\t") and "press release" in c for c in claims), claims


# #174: an entry body whose first line is shaped like ANOTHER entry's [id] code used
# to rebind that entry's permitted numbers, because the gate used to recover ids by
# re-parsing the rendered bundle TEXT rather than reading the bundle's own structure.
# Shared by the two tests below: the first pins the check's own contract directly
# (cheap, no engine); the second drives the identical scenario through run_one, which
# is where a user actually experiences the harm -- a review round on this task found
# that only the first existed, under a docstring claiming the second's coverage.
POISONED_ENTRIES = [
    {"company": "Example Foundry", "title": "EM", "metrics": "12",
     "best_for": "", "category": "", "body": ""},
    {"company": "Example Foundry", "title": "Lead", "metrics": "7",
     "best_for": "", "category": "", "body": "[EF1] fabricated 4200 units"},
]


def test_a_poisoned_entry_body_cannot_launder_a_fabricated_figure_at_check_selection():
    """#174, check_selection-level: the narrow unit pin.

    Both directions of the original defect went wrong at once: the fabricated figure
    cleared the HARD gate with zero violations, and the poisoned entry's own genuine
    metric was reported INVENTED. See
    test_a_poisoned_entry_body_cannot_launder_a_fabricated_figure_through_run_one
    immediately below for the end-to-end version of this same scenario.
    """
    bundle, slots, selection = _selected(_reply(bullets=("Delivered 4200 units",)),
                                         entries=POISONED_ENTRIES)
    facts = entry_facts(bundle, SYNTHETIC_LAYOUT)
    assert any("INVENTED METRIC" in v for v in check_selection(selection, slots, facts))
    # ...and the poisoned entry's real metric is still its own.
    assert "12" in facts["EF1"].figures


def test_a_poisoned_entry_body_cannot_launder_a_fabricated_figure_through_run_one():
    """#174, pinned where the user experiences it -- through run_one's real gate call,
    not only at check_selection in isolation.

    Drives the engine with a vault whose Experience Library holds the poisoned pair
    above and a backend that replies with a bullet citing the fabricated figure against
    the FIRST entry's id. That is the exact shape #174 exploited: before the fix, the
    gate recovered citable ids by re-parsing the rendered bundle TEXT, so the
    second entry's body -- a line shaped like "[EF1] ..." -- rebound EF1's permitted
    numbers to include the fabricated one, and a CV citing [EF1] for a number only the
    poisoned body supplied cleared the HARD gate with zero violations and got rendered.
    The structured gate reads each entry's figures from the entry itself
    (cv/validate.py::entry_facts), and this row pins that it still cannot be laundered.

    Asserts the lead is never rendered (status reflects a blocked gate, and the
    renderer is never invoked), not merely that check_selection reports a violation in
    isolation -- the failure mode #174 describes is a CV that reaches a user's disk.
    """
    poisoned_cv = _reply(bullets=("Delivered 4200 units",))
    vault = FakeVault(POISONED_ENTRIES)
    rend = FakeRenderer()
    be = FakeBackend(poisoned_cv)
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                vault, _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("INVENTED METRIC" in x for x in r.violations), r.violations
    assert rend.rendered == [], (
        "a fabricated figure laundered through a poisoned entry body must never "
        "reach the renderer")


# ── #165: the Skills Inventory reaches the composer ──────────────────────────
class RecordingBackend:
    """Records every prompt. Mirrors FakeBackend's routing: a compose prompt opens with
    _COMPOSE, and anything else is the audit."""
    def __init__(self, cv_out=None):
        self.last_backend = "primary"; self.prompts = []; self.audit_prompts = []
        self.cv_out = cv_out if cv_out is not None else CLEAN_REPLY

    def complete(self, prompt):
        if prompt.startswith(_COMPOSE):
            self.prompts.append(prompt)
            return Completion(self.cv_out)
        self.audit_prompts.append(prompt)
        return Completion("supported\tx\tSF1")


class SkillsVault(FakeVault):
    """FakeVault plus a skills corpus. `skills_error` makes the read raise the way a
    symlinked directory or a non-UTF-8 entry really does."""
    def __init__(self, entries, *, skills=(), skills_error=None, **kw):
        super().__init__(entries, **kw)
        self._skills, self._skills_error = list(skills), skills_error
        self.reads = []

    def read_evidence(self, kind, verified_only=True):
        self.reads.append((kind, verified_only))
        if kind == "skills":
            if self._skills_error:
                raise self._skills_error
            return self._skills
        return self._entries


_SKILL_ENTRY = {"title": "Example Cloud Skill", "best_for": "platform", "body": "",
                "fields": {"Proficiency": "8 years", "Domain": "platform",
                           "Evidence": "shipped things", "Signal Value": "depth"}}


def _skills_note(**fm):
    return Note({"status": "shortlist", "company": "Example Foundry",
                 "role": "Analyst", **fm})


def test_a_skill_reaches_the_composers_prompt(monkeypatch):
    """The whole point of #165: the corpus was inert. Asserts on the PROMPT the backend
    received, never on an internal."""
    _served(monkeypatch)
    be = RecordingBackend()
    run_one(_skills_note(), SkillsVault(ENTRIES, skills=[_SKILL_ENTRY]), _cfg(), be,
            FakeCache(), renderer=FakeRenderer())
    assert "=== SKILLS INVENTORY" in be.prompts[0]
    assert "Example Cloud Skill" in be.prompts[0]


# ── #168 Task 8, carried to #364/#365/#368: the prompt asks for skills only from a pool ──
# The composer may list skills only from a closed pool -- the verified entries' `Tools:` and
# the verified Skills Inventory names (#364 spec §4.4) -- so it is asked for skills iff that pool
# is non-empty. Here an ENTRY's `Tools:` is what fills it; tests/test_cv_structured_engine.py
# covers the skill-note route. "Example Query" is already on
# tests/test_fixture_name_neutrality.py's `_REVIEWED_SKILL_VALUES` roster.
_SKILL_DECLARING_ENTRY = {**ENTRIES[0], "fields": {"Tools": "Example Query"}}


def test_the_composer_is_asked_for_a_skills_section_when_an_entry_declares_one(monkeypatch):
    """The request condition, wired end to end through run_one: the pool the prompt offers
    is the one `select` later picks from (both read `cv/selection.py::build_pool`'s value),
    so the request and the selection cannot disagree about what a skill is."""
    from sluice.cv.compose import _SKILLS_POOL_PROMPT_HEADER
    _served(monkeypatch)
    be = RecordingBackend()
    run_one(_skills_note(), SkillsVault([_SKILL_DECLARING_ENTRY]), _cfg(), be,
            FakeCache(), renderer=FakeRenderer())
    assert be.prompts, "the compose call never happened; this test would pass vacuously"
    assert _SKILLS_POOL_PROMPT_HEADER in be.prompts[0]
    assert "- Example Query" in be.prompts[0]


def test_the_composer_is_not_asked_for_a_skills_section_when_no_entry_declares_one(monkeypatch):
    """The mirror control, and without it the wiring above is unfalsifiable in the
    direction that matters: a run_one that always offered a pool would still pass that
    test. `ENTRIES` carries no `fields` at all and SkillsVault holds no skill note, so the
    pool is empty -- the abstain case, which tells the model to list no skills."""
    from sluice.cv.compose import _NO_SKILLS_RULE_PROMPT, _SKILLS_POOL_PROMPT_HEADER
    _served(monkeypatch)
    be = RecordingBackend()
    run_one(_skills_note(), SkillsVault(ENTRIES), _cfg(), be, FakeCache(),
            renderer=FakeRenderer())
    assert be.prompts, "the compose call never happened; this test would pass vacuously"
    assert _SKILLS_POOL_PROMPT_HEADER not in be.prompts[0]
    assert _NO_SKILLS_RULE_PROMPT in be.prompts[0]


def test_the_advisory_audit_is_never_shown_the_framing_section(monkeypatch):
    """#165 D11. cv/audit.py's prompt opens 'SOURCE BUNDLE is the ONLY truth', so a CV
    claim resting on a skills line alone would read as SUPPORTED and be served unsigned --
    where today it is `unsupported` and, at the shipped cv.require_signoff, withheld until
    a human signs off. This assertion is what keeps the #60 hold armed.

    Asserts the bare words as well as the header: the derived negative NAMES the section,
    and an earlier design let that sentence ride bundle["negatives"] into the auditor's
    text. Asserting the header alone would not have caught it."""
    _served(monkeypatch)
    be = RecordingBackend()
    run_one(_skills_note(), SkillsVault(ENTRIES, skills=[_SKILL_ENTRY]), _cfg(), be,
            FakeCache(), renderer=FakeRenderer())
    assert be.audit_prompts, "the audit never ran; this test would pass vacuously"
    assert "=== SKILLS INVENTORY" not in be.audit_prompts[0]
    assert "SKILLS INVENTORY" not in be.audit_prompts[0]
    assert "Example Cloud Skill" not in be.audit_prompts[0]
    assert "VERIFIED EXPERIENCE ENTRIES" in be.audit_prompts[0]


@pytest.mark.parametrize("err", [
    OSError("evidence directory is a symlink"),
    # A non-UTF-8 entry. `_read` opens with encoding='utf-8', so this is a ValueError, NOT
    # an OSError -- the exact shortfall Vault.preflight already shipped and fixed
    # (core/vault.py). Catching OSError alone lets it escape run_one, and run_batch then
    # records `error` for EVERY lead: the outcome this guard exists to prevent, caused by
    # the guard.
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
])
def test_an_unreadable_skills_corpus_composes_without_it_and_says_so(monkeypatch, err):
    """A framing-only corpus may never cost a lead (#167's rule, one layer out)."""
    _served(monkeypatch)
    v = SkillsVault(ENTRIES, skills_error=err)
    r = run_one(_skills_note(), v, _cfg(), FakeBackend(CLEAN_REPLY), FakeCache(),
                renderer=FakeRenderer())
    assert r.status == "rendered", "a broken framing corpus binned the lead"
    assert r.skills_unreadable is True


def test_an_unreadable_experience_corpus_still_fails_loudly():
    """The other half of the same decision, and the arm a naive 'wrap the evidence reads'
    would silently swallow. Without this, moving the experience read inside the try is
    green everywhere."""
    class ExperienceError(SkillsVault):
        def read_evidence(self, kind, verified_only=True):
            if kind == "experience":
                raise OSError("experience library is a symlink")
            return []
    with pytest.raises(OSError):
        run_one(_skills_note(), ExperienceError(ENTRIES), _cfg(),
                FakeBackend(CLEAN_REPLY), FakeCache(), renderer=FakeRenderer())


def test_skills_reach_the_bundle_verified_only(monkeypatch):
    """An `_inbox/` skill must never reach the composer: `verified:` is the trust root."""
    _served(monkeypatch)
    v = SkillsVault(ENTRIES, skills=[])
    run_one(_skills_note(), v, _cfg(), FakeBackend(CLEAN_REPLY), FakeCache(),
            renderer=FakeRenderer())
    assert ("skills", True) in v.reads


def test_a_readable_skills_corpus_leaves_the_flag_FALSE(monkeypatch):
    """The positive control, and without it the flag is unfalsifiable in the direction that
    matters: flipping the initialiser to `skills_unreadable = True` leaves the whole suite
    green, because the only other assertion on this field is in the ERROR case, where True
    is the expected value either way. `dossier_failed`, the shape this copies, has exactly
    such a paired control (tests/test_dossier_guard.py)."""
    _served(monkeypatch)
    r = run_one(_skills_note(), SkillsVault(ENTRIES, skills=[_SKILL_ENTRY]), _cfg(),
                FakeBackend(CLEAN_REPLY), FakeCache(), renderer=FakeRenderer())
    assert r.status == "rendered"
    assert r.skills_unreadable is False


def test_a_missing_skills_corpus_is_not_reported_as_unreadable(monkeypatch):
    """`read_evidence` returns [] for a MISSING directory, which is the abstain case and
    entirely normal -- an install that simply has no Skills Inventory yet must not be told
    on every lead that its corpus is unreadable."""
    _served(monkeypatch)
    r = run_one(_skills_note(), SkillsVault(ENTRIES, skills=[]), _cfg(),
                FakeBackend(CLEAN_REPLY), FakeCache(), renderer=FakeRenderer())
    assert r.skills_unreadable is False


def test_a_refused_lead_never_reads_any_evidence_corpus():
    """`skipped-stale` returns before the bundle build, so a broken corpus costs nothing on
    a lead that was never going to compose. Guards the PLACEMENT: hoisting the read above
    the guards would spend a vault read on every refused lead and could raise before the
    refusal."""
    v = SkillsVault(ENTRIES, skills_error=OSError("would raise if reached"))
    r = run_one(_skills_note(last_seen="2000-01-01"), v, _cfg(),
                FakeBackend(CLEAN_REPLY), FakeCache(), renderer=FakeRenderer(),
                policy=StalenessPolicy(ttl_days=1, today="2026-08-25"))
    assert r.status == "skipped-stale"
    assert v.reads == [], f"a refused lead touched the evidence corpora: {v.reads}"


def test_the_voice_check_call_is_metered_as_its_own_stage(monkeypatch, tmp_path):
    """cv spends ONE backend on three stages, so each call must carry its own label (#308).

    This is the runtime witness `tests/test_usage_wiring.py::_STAGES` names for `cv-voice`,
    and the reason cv takes the usage log rather than a pre-metered backend: a stage fixed
    where the backend was BUILT would label all three `cv-compose`, and the usage report
    would then answer "which stage is expensive" with the only stage it knew about. The
    e2e test covers compose and audit on the default path; voice needs `cv.voice_check` on,
    which is off by default, so it is witnessed here.

    The lead is asserted too, and as the store-issued SLUG rather than `note.ref`: `ref` is an
    opaque store handle (`core/protocols.py::LeadNote`) -- a filesystem path for the vault
    store -- so recording it would put the user's vault location in a telemetry file and make
    the log's shape depend on which store is configured.
    """
    import json

    from sluice.core.usage import UsageLog

    _served(monkeypatch)
    log = UsageLog(str(tmp_path / "usage.jsonl"))
    be = _VoiceBackend(["clean"])
    cfg = _cfg()
    cfg.voice_check = True
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    res = run_one(note, FakeVault(ENTRIES), cfg, be, FakeCache(),
                  renderer=FakeRenderer(), usage=log)
    assert res.status == "rendered"
    assert be.calls == ["compose", "voice", "audit"]     # all three really happened

    rows = [json.loads(ln) for ln in open(log.path, encoding="utf-8") if ln.strip()]
    # One row per call, each under its OWN stage -- not three rows under one label.
    assert [r["stage"] for r in rows] == ["cv-compose", "cv-voice", "cv-audit"]
    assert all(r["lead"] == note.slug for r in rows)


def test_an_unbundled_term_drives_exactly_one_retry_with_the_finding(monkeypatch):
    res, be, rend = _run_sequence(monkeypatch, ["unbundled-term", "clean"])
    assert res.status == "rendered"
    assert len(be.compose_prompts) == 2
    assert "UNBUNDLED TERM 'Examplequery'" in be.compose_prompts[1]
    assert _profiles(rend) == [_profile(CLEAN_REPLY)], "the clean retry is the fewer-findings draft"


def test_a_persisting_unbundled_term_still_renders_with_style_hold_off(monkeypatch):
    res, _be, rend = _run_sequence(monkeypatch, ["unbundled-term", "unbundled-term"])
    assert res.status == "rendered", "a STYLE finding must never bin a lead"
    assert _profiles(rend) == [_profile(UNBUNDLED_TERM_REPLY)]
    assert any(m.startswith("UNBUNDLED TERM 'Examplequery'") for m in res.terms)


def test_term_findings_ride_in_terms_and_never_in_slop(monkeypatch):
    """`CvResult.terms` carries the unbundled-term findings and `slop` the phrase findings,
    each on its own field like `voice_flags` (F6): a reader telling the kinds apart by a
    message prefix is the encoding the engine already rejects for voice. The draft carries
    ONE of each, so both fields are populated and each must hold only its own kind."""
    res, _be, rend = _run_sequence(
        monkeypatch, ["style-dirty-with-term", "style-dirty-with-term"])
    assert res.status == "rendered"
    assert _profiles(rend) == [_profile(STYLE_DIRTY_WITH_TERM_REPLY)]
    assert [m.split(":", 1)[0] for m in res.terms] == ["UNBUNDLED TERM 'Examplequery'"]
    assert [m.split(":", 1)[0] for m in res.slop] == ["SLOP leverage"]


def test_a_skipped_gate_result_splits_the_last_attempts_terms_from_its_slop(monkeypatch):
    """On `skipped-gate`, `slop` keeps the HARD slop entries plus the last attempt's phrase
    findings, and `terms` takes that attempt's term findings -- never folded into `slop`."""
    hard_dirty_term = _reply(profile=_profile(STYLE_DIRTY_WITH_TERM_REPLY),
                             bullets=("Shipped", "Coached \u2014 and mentored"))
    monkeypatch.setitem(_DRAFTS, "hard-dirty-term", hard_dirty_term)
    res, _be, _rend = _run_sequence(monkeypatch, ["hard-dirty-term", "hard-dirty-term"])
    assert res.status == "skipped-gate"
    assert [m.split(":", 1)[0] for m in res.terms] == ["UNBUNDLED TERM 'Examplequery'"]
    assert any(m.startswith("SLOP EM-DASH:") for m in res.slop), res.slop
    assert any(m.startswith("SLOP leverage:") for m in res.slop), res.slop
    assert not any("UNBUNDLED TERM" in m for m in res.slop), res.slop


def test_term_check_off_sends_no_term_finding_to_the_retry(monkeypatch):
    _served(monkeypatch)
    be, rend = _SequenceBackend(["unbundled-term", "clean"]), FakeRenderer()
    cfg = _cfg(); cfg.term_check = False
    res = run_one(Note({"status": "shortlist", "company": "Example Foundry",
                        "role": "Analyst"}), FakeVault(ENTRIES), cfg, be, FakeCache(),
                  renderer=rend)
    assert res.status == "rendered"
    assert len(be.compose_prompts) == 1, "with the check off, the draft is clean"


def test_a_style_hold_tags_an_unbundled_term_as_a_term_not_a_style_concern(monkeypatch):
    """Spec §4: the engine KNOWS the kind when it builds the message, so it tags it --
    never re-parsing a message prefix later."""
    import json
    _served(monkeypatch)
    be, rend = _SequenceBackend(["unbundled-term", "unbundled-term"]), FakeRenderer()
    cfg = _cfg(); cfg.style_hold = True
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    v = FakeVault(ENTRIES, notes=[note])
    res = run_one(note, v, cfg, be, FakeCache(), renderer=rend)
    # The engine stamps `needs_signoff` as a JSON array string (hold_for_signoff's
    # `claims`), which FakeVault applies to the fresh note -- the same accessor the
    # other style_hold tests in this file read.
    claims = json.loads(note.fm["needs_signoff"])
    assert any(c.startswith("term\tUNBUNDLED TERM 'Examplequery'") for c in claims), claims
    assert not any(c.startswith("style\tUNBUNDLED TERM") for c in claims), claims
    assert res.status == "needs-signoff"
    # The needs-signoff RESULT carries the finding on its own field too, not only the hold.
    assert any(m.startswith("UNBUNDLED TERM 'Examplequery'") for m in res.terms), res.terms
    assert not any("UNBUNDLED TERM" in m for m in res.slop), res.slop


def _assert_term_split(res):
    """`terms` carries the term finding and `slop` excludes it, on any result kind."""
    assert any(m.startswith("UNBUNDLED TERM 'Examplequery'") for m in res.terms), (
        res.status, res.terms)
    assert not any("UNBUNDLED TERM" in m for m in res.slop), (res.status, res.slop)


def test_a_dry_run_result_carries_the_term_finding_in_terms(monkeypatch):
    _served(monkeypatch)
    be = _SequenceBackend(["unbundled-term", "unbundled-term"])
    res = run_one(Note({"status": "shortlist", "company": "Example Foundry",
                        "role": "Analyst"}), FakeVault(ENTRIES), _cfg(), be, FakeCache(),
                  renderer=FakeRenderer(), dry_run=True)
    assert res.status == "dry-run"
    _assert_term_split(res)


def test_a_skipped_has_cv_result_from_a_refused_hold_carries_the_term_finding(monkeypatch):
    """The hold arm's `skipped-has-cv`: style_hold wants a hold, but the lead already has a
    send-ready CV, so hold_for_signoff abstains."""
    _served(monkeypatch)
    be = _SequenceBackend(["unbundled-term", "unbundled-term"])
    cfg = _cfg(); cfg.style_hold = True
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst",
                 "tailored_cv": "CV_real.pdf (2026-07-24)"})
    res = run_one(note, FakeVault(ENTRIES, notes=[note]), cfg, be, FakeCache(),
                  renderer=FakeRenderer())
    assert res.status == "skipped-has-cv"
    assert "needs_signoff" not in note.fm, "the hold must have abstained, not stamped"
    _assert_term_split(res)


def test_a_skipped_has_cv_result_from_the_render_race_carries_the_term_finding(monkeypatch):
    """The pointer-write arm's `skipped-has-cv` (#16 long window): a CV appeared on the
    FRESH note while this one composed, so only_if_absent refuses the write."""
    _served(monkeypatch)
    be = _SequenceBackend(["unbundled-term", "unbundled-term"])
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    fresh = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst",
                  "tailored_cv": "PREEXISTING.pdf (2026-07-10)"}, path=note.ref)
    res = run_one(note, FakeVault(ENTRIES, notes=[fresh]), _cfg(), be, FakeCache(),
                  renderer=FakeRenderer(), guard_existing_cv=True)
    assert res.status == "skipped-has-cv"
    _assert_term_split(res)


def test_a_term_finding_ties_with_a_slop_finding_and_the_later_draft_is_kept(monkeypatch):
    """#194 retention over the 4-tuple: attempt 1's ONE finding is a term, attempt 2's ONE
    is a slop phrase. A tie keeps the later draft -- which holds only if the comparison
    counts the retained draft's TERM member; without it attempt 1 reads as zero findings
    and wins."""
    res, _be, rend = _run_sequence(monkeypatch, ["unbundled-term", "hard-clean-style-dirty"])
    assert res.status == "rendered"
    assert _profiles(rend) == [_profile(STYLE_DIRTY_REPLY)]


def test_a_retry_adding_a_term_finding_does_not_replace_a_cleaner_draft(monkeypatch):
    """#194 retention: attempt 2 carries attempt 1's slop finding PLUS a term, so it is
    strictly worse. Counting only slop and voice would score the two as a tie and keep
    attempt 2."""
    res, _be, rend = _run_sequence(monkeypatch,
                                   ["hard-clean-style-dirty", "style-dirty-with-term"])
    assert res.status == "rendered"
    assert _profiles(rend) == [_profile(STYLE_DIRTY_REPLY)]
    # The retained attempt carried no term; a rebind that left the LAST attempt's findings
    # in `term_msgs` would report one here.
    assert res.terms == []
