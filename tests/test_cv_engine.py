# tests/test_cv_engine.py
import os

import pytest

from sluice.cv.bundle import build_bundle, render_bundle
from sluice.cv.engine import run_one, run_batch
from sluice.cv.validate import validate
from sluice.core.backends import BackendError, FallbackBackend, OpenAiCompatibleBackend
from sluice.core.leads import StalenessPolicy
from sluice.core.protocols import CandidateProfile

# #107: the identity every test in this file gets unless it asks for something
# else. full_name() -> "Jane Roe" -- the literal name CLEAN_CV's header line
# already used before this task moved identity out of CvConfig, so no fixture
# text below needed to change to keep matching it. ONE contact field (mobile)
# is enough to clear the new "blank contact refuses" gate (see
# test_a_declared_name_with_blank_contact_also_refuses_before_spend) without
# picking a shape that collides with the two isolation fixtures
# (REVERSED_HEADER_CV, PREAMBLE_REPLACING_CONTACT_CV) that configure their own,
# different one-line contact below. CLEAN_CV's header carries this exact line
# first -- see its own comment -- which is what keeps the #99/#100 STRUCTURAL
# guard's header-vs-derived-identity comparison clean for every test that
# does not override it.
DEFAULT_CANDIDATE = CandidateProfile(forenames="Jane", surname="Roe", mobile="+1 555 0100")


class Note:
    def __init__(self, fm, path="Job Applications/Job Leads/Acme - Analyst.md"):
        # A store hands back an opaque `ref` and the slug it issued; it never hands
        # back a path for the caller to parse.
        self.fm = fm; self.ref = path; self.slug = path.split("/")[-1][:-3]

class FakeVault:
    def __init__(self, entries, notes=None, candidate=DEFAULT_CANDIDATE):
        self._entries = entries; self._notes = notes or []; self.written = {}; self.fields = {}
        self._candidate = candidate
    def read_experience_entries(self, verified_only=True): return self._entries
    # #107: cv/engine.py's identity gate is MUST-support (Store.read_candidate_profile),
    # not reached through getattr -- so every test that expects run_one to proceed past
    # it needs this to answer, not raise. `candidate` is a constructor param (not a
    # hardcoded DEFAULT_CANDIDATE return) so the two #99/#100 isolation tests
    # (REVERSED_HEADER_CV, PREAMBLE_REPLACING_CONTACT_CV) can seed a DIFFERENT declared
    # identity without a second fake class.
    def read_candidate_profile(self): return self._candidate
    # Tracks the SUBSET of protocols.Store that cv actually exercises, and each
    # method it does carry must match that method's real signature exactly -- this
    # fake carrying the old read_baseline(rel=...) is what let a real TypeError ship
    # green. Deliberately NOT the whole contract: update_fields below omits
    # require_status and require_blank, which cv never passes; the conformance suite
    # in tests/conformance/ is what holds real stores to the full signature.
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
    """The Renderer seam, injected. Records what it was asked to render so a test can
    assert a CV was NEVER rendered -- which is the fabrication gate's whole point.

    Implements `render` ONLY. That is the shape of the shipped `script` renderer, which
    shells out to arbitrary user code and imposes no grammar of its own, and it is the
    shape every test in this file wants by default: an engine that gated CVs on some
    renderer's private grammar would be gating THIS one too. `precheck` is the optional
    half of the seam (core/protocols.py) -- see PrecheckingRenderer below.
    """
    def __init__(self): self.rendered = []
    def render(self, cv_text, out_dir, *, neutral_name="CV.pdf"):
        self.rendered.append(cv_text)
        return f"/tmp/x/{neutral_name}"


class PrecheckingRenderer(FakeRenderer):
    """A renderer that DOES implement the seam's optional `precheck`, in exactly the
    shape `sluice/renderers/template.py` does: parse, and report a SHAPE failure as a
    `FORMAT:` string for the engine to fold in with the gate's own violations.

    Kept as a distinct class rather than added to FakeRenderer, because the distinction
    between the two is the thing under test -- see
    test_a_renderer_without_precheck_is_not_gated_by_another_renderers_grammar.
    """
    def precheck(self, cv_text):
        from sluice.cv.parse import CvParseError, parse_cv
        try:
            parse_cv(cv_text)
        except CvParseError as e:
            return [f"FORMAT: {e}"]
        return []

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
    # #107: cv/engine.py no longer reads cvcfg.name/cvcfg.contact -- identity comes
    # from the vault's Candidate Profile note (FakeVault's `candidate`, DEFAULT_CANDIDATE
    # unless a test overrides it). No override of c.name/c.contact belongs here any
    # more; CvConfig no longer HAS either field (#133/#107, Task 9), so there is
    # nothing left on this dataclass for such an override to even set.
    return c

# Synthetic throughout; only the descending start years are load-bearing.
# "+1 555 0100" is the ONE header line DEFAULT_CANDIDATE's mobile field produces
# (#107): the #99/#100 STRUCTURAL guard now compares this header block against
# the vault-derived identity, not cvcfg.name/cvcfg.contact, so this fixture's
# contact line must match DEFAULT_CANDIDATE exactly or every "rendered" test
# below would fail that guard instead of testing what it claims to.
CLEAN_CV = "\n".join([
    "+1 555 0100", "JANE ROE", "", "PROFILE", "I build reliable systems.", "", "WORK EXPERIENCE", "",
    "Example Systems", "02/2023–present | Example Location A | Staff Engineer", "- Shipped [EF1]", "",
    "Example Analytics", "06/2020–01/2023 | Example Location B | Senior Engineer",
    "- Grew team from 3 to 8 [EF1]", "",
    "Example Robotics", "09/2017–05/2020 | Example Location C | Engineer", "- Coached [EF1]", "",
    "Example Cartography", "07/2015–08/2017 | Example Location A | Junior Engineer", "- CI [EF1]", "",
    "CERTIFICATES", "- Example Scrum Master", "EDUCATION", "- Uni",
])


# An UNPARSEABLE meta line that still PASSES the fabrication gate -- the whole point of
# this wiring. validate() reads only `\d{2}/(\d{4})\s*[–-]` after WORK EXPERIENCE, so
# dropping the pipes leaves the years (and every citation) intact and the gate clean.
UNPARSEABLE_CV = CLEAN_CV.replace("02/2023–present | Example Location A | Staff Engineer",
                                  "02/2023–present Example Location A Staff Engineer")


def test_the_unparseable_fixture_still_passes_the_gate():
    """A PREMISE of both tests below: they claim the engine catches a formatting failure
    the GATE does not. If this fixture ever stops clearing the gate they would pass for
    the wrong reason -- the same trap test_clean_cv_is_actually_clean exists to close."""
    assert "Example Location A Staff Engineer" in UNPARSEABLE_CV, "the replace no-opped"
    bundle_text = render_bundle(build_bundle(
        entries=ENTRIES, baseline="BASELINE", negatives=[],
        jd_keywords=[], prefix_map={"Example Foundry": "EF"}))
    assert validate(UNPARSEABLE_CV, bundle_text) == []


def test_a_parse_failure_feeds_the_retry_not_the_bin(monkeypatch):
    """A CV whose role line wobbles must be RE-COMPOSED, not thrown away.

    The engine already composes up to twice, appending violations to the second prompt.
    Making a parse failure fatal would kill the lead AFTER the LLM spend with no
    recovery -- worse than the status quo, and it re-opens the exact problem this design
    exists to close. The model is being asked to fix its own formatting, which is the
    thing an LLM is reliably good at.

    _served(monkeypatch) is unrelated to the parse-retry wiring under test: without it
    the real `_render.serve` opens the FakeRenderer's made-up pdf path and raises
    FileNotFoundError, exactly as it would for any OTHER test in this file that reaches
    "rendered" with a non-empty served_dir -- see test_happy_path_renders_and_records and
    every #60 test below, all of which mock the same seam for the same reason.
    """
    _served(monkeypatch)

    class TwoShotBackend:
        """First compose returns the unparseable CV; the second returns a clean one."""
        def __init__(self):
            self.last_backend = "primary"; self.prompts = []
        def complete(self, prompt):
            # Mirrors FakeBackend's routing: compose prompts carry "SOURCE BUNDLE"
            # and not "auditing"; audit prompts carry both.
            if not ("SOURCE BUNDLE" in prompt and "auditing" not in prompt):
                return "supported\tx\tSF1"
            self.prompts.append(prompt)
            return UNPARSEABLE_CV if len(self.prompts) == 1 else CLEAN_CV

    be = TwoShotBackend()
    v = FakeVault(ENTRIES)
    rend = PrecheckingRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "rendered", "a parse failure binned the lead instead of retrying it"
    assert len(be.prompts) == 2, "the parse failure did not reach the existing retry"
    assert "FORMAT" in be.prompts[1], "the parse error never reached the retry prompt"
    assert rend.rendered == [CLEAN_CV], "the renderer got the unparseable CV"


def test_a_parse_failure_that_survives_the_retry_skips_the_lead():
    """Same outcome as a lead that cannot clear the gate, and the renderer is never
    reached -- a half-parsed CV must never become a PDF sent under the user's name."""
    v = FakeVault(ENTRIES)
    rend = PrecheckingRenderer()
    be = FakeBackend(UNPARSEABLE_CV)
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("FORMAT" in x for x in r.violations)
    assert rend.rendered == [], "an unparseable CV reached the renderer"
    assert v.written == {}


# The review's own measured case: a section `template`'s grammar does not model, in a CV
# that is otherwise entirely gate-clean. The bullet carries a citation and NO number --
# both are load-bearing, and both were got wrong while writing this: an uncited bullet
# reads as UNCITED (in_work is still true before CERTIFICATES), and a bare year reads as
# an INVENTED METRIC. Either makes the fixture gate-DIRTY, and the test then passes for
# the wrong reason -- which is what test_the_publications_fixture_passes_the_gate exists
# to stop.
PUBLICATIONS_CV = CLEAN_CV.replace(
    "CERTIFICATES", "PUBLICATIONS\n- A paper on delivery [EF1]\n\nCERTIFICATES")


# ── #99: a composer preamble desyncs cv/parse.py's header-line assignment ──────
# `parse_cv` (cv/parse.py) takes whatever non-blank lines precede PROFILE, calls
# the LAST one the name (its `header_lines[-1]` assignment) and everything before
# it the contact block -- zero shape check on either. All three fixtures
# below insert extra text ahead of "JANE ROE" (the one line CLEAN_CV already has
# there), reproducing the two variants captured on the real production path (#99):
# a composer routinely opens with a one-sentence acknowledgement before the CV
# proper.
#
# PREAMBLE_BEFORE_NAME_CV keeps the name line intact and correct -- only an EXTRA
# line appears before PROFILE. Isolates the count guard: the anchor guard would
# NOT fire here on its own (the last line before PROFILE still IS "JANE ROE"),
# which is what makes this fixture prove the count guard is independently
# load-bearing rather than redundant with the anchor check.
PREAMBLE_BEFORE_NAME_CV = CLEAN_CV.replace(
    "JANE ROE",
    "I'll compose a tailored CV for Jane Roe applying for Analyst at Example "
    "Foundry, drawing only from the verified source bundle.\n\nJANE ROE",
    1)

# PREAMBLE_WITH_CONTACT_BLOCK_CV is the fuller, realistic Variant B captured live:
# preamble, THEN the name, THEN a multi-line contact block the model volunteered
# from the bundle even though the derived contact block it was given is the bare
# single mobile line DEFAULT_CANDIDATE produces ("+1 555 0100", no label, no
# email/LinkedIn -- see contact_block()'s docstring). The real name ends up
# buried mid-block and the LAST line before PROFILE -- what parse.py takes as the
# name -- is a contact line. Both the count guard (5 lines where 1 was expected)
# and the anchor guard (the last line isn't the name) fire on this fixture; it is
# not meant to isolate either in isolation, only to prove the fix catches the
# realistic end-to-end shape, "two defects stacked" as it was actually observed.
PREAMBLE_WITH_CONTACT_BLOCK_CV = CLEAN_CV.replace(
    "JANE ROE",
    "I'll tailor Jane Roe's CV for the Analyst role at Example Foundry, "
    "emphasizing relevant delivery experience.\n\nJANE ROE\n\n"
    "Phone number: +1 555 0100\n"
    "Email address: jane.roe@example.invalid\n"
    "Web: https://www.example.invalid/in/example",
    1)

# REVERSED_HEADER_CV isolates the anchor guard from the count guard: the LINE COUNT
# is exactly what a configured one-line contact would produce, but the model emitted
# name-then-contact instead of the contact-then-name order compose.py's _RULES specify
# (`{contact}\n\n{name_heading}`) -- the accepted trade-off the comment beside
# `parse_cv`'s `header_lines[-1]` assignment (cv/parse.py) names, now closed once
# the derived identity carries ground truth to compare against. The count guard
# must NOT fire on this fixture (that is what proves the anchor guard is
# independently load-bearing, not merely a second copy of the count check).
#
# Strips CLEAN_CV's own default "+1 555 0100" line first (#107): this fixture and
# PREAMBLE_REPLACING_CONTACT_CV below each need a candidate profile whose contact is
# EXACTLY the one line they isolate ("Phone: +1 555 0100", the value the two tests
# below now seed onto FakeVault instead of the old cfg.contact), not
# DEFAULT_CANDIDATE's -- carrying both would make the header three lines against an
# expected two, tripping the COUNT guard these two fixtures exist to isolate FROM.
REVERSED_HEADER_CV = CLEAN_CV.replace("+1 555 0100\n", "", 1).replace(
    "JANE ROE", "JANE ROE\n\nPhone: +1 555 0100", 1)

# PREAMBLE_REPLACING_CONTACT_CV isolates the CONTENT guard (added on CodeRabbit review
# of #100's fix) from both the count guard and the anchor guard: with a one-line
# contact configured, a preamble sentence occupies the contact slot exactly -- the
# header is the expected two lines and the last one still IS the configured name, so
# neither the count check nor the anchor check fires. Only comparing header[:-1]
# against the derived contact's own lines catches that the "contact" line is prose,
# not the real contact information, which is gone. Unlike the two fixtures above,
# this one does NOT corrupt the parsed NAME -- parse_cv's last-line rule still lands
# on "JANE ROE" -- which is exactly why the anchor check alone cannot see anything
# wrong with it. Strips the default contact line first, for the same reason as
# REVERSED_HEADER_CV above.
PREAMBLE_REPLACING_CONTACT_CV = CLEAN_CV.replace("+1 555 0100\n", "", 1).replace(
    "JANE ROE",
    "Here is the tailored CV for the Analyst role, prepared from the verified "
    "source bundle only.\n\nJANE ROE",
    1)


# The REWORDED-CONTACT fixtures isolate the CONTENT guard's rendering arm (CodeRabbit,
# PR #161, twice -- case first, internal spacing on the following round). Each header is
# the expected two lines and the last one still IS the configured name, so neither the
# count check nor the anchor check fires -- exactly like PREAMBLE_REPLACING_CONTACT_CV
# above -- but the contact line is the DECLARED one, re-rendered.
#
# All three must still REFUSE (the contact block is emitted verbatim into the rendered
# CV, so accepting a re-rendered one prints text the candidate did not write), and all
# three must refuse with a message naming the rendering rather than a preamble: the
# retry gets one attempt, and "drop the preamble" is not something it can act on when
# there is no preamble.
#
# The declared contact these are compared against is "Phone: +1 555  0100" -- note the
# DOUBLE space, which the spacing fixture collapses. `contact_block` does not collapse
# whitespace runs (`full_name` does), so a declared double space is a real shape a user
# can have and a composer can normalise away.
_DECLARED_CONTACT = "Phone: +1 555  0100"
RECASED_CONTACT_CV = CLEAN_CV.replace("+1 555 0100\n", "", 1).replace(
    "JANE ROE", "phone: +1 555  0100\n\nJANE ROE", 1)
RESPACED_CONTACT_CV = CLEAN_CV.replace("+1 555 0100\n", "", 1).replace(
    "JANE ROE", "Phone: +1 555 0100\n\nJANE ROE", 1)
REWORDED_CONTACT_CV = CLEAN_CV.replace("+1 555 0100\n", "", 1).replace(
    "JANE ROE", "phone: +1 555 0100\n\nJANE ROE", 1)


def test_the_preamble_fixtures_are_gate_clean_and_misparse():
    """A PREMISE of every #99 test below: they claim the ENGINE catches something
    validate() and parse_cv() both silently accept. If any fixture ever stops being
    gate-clean, or parse_cv ever starts raising on it, those tests would pass for a
    reason that has nothing to do with the new guards -- the same trap
    test_the_unparseable_fixture_still_passes_the_gate closes for its own fixture.

    Also pins the actual misassignment computationally (not merely "does not raise"),
    since that misassignment -- not a parse failure -- is the entire defect #99 is
    about. Mirrors the redacted evidence posted to the real issue: the LinkedIn line
    becomes the parsed name; the preamble becomes part of the parsed contact.
    """
    from sluice.cv.parse import parse_cv
    from sluice.cv.slop import check_text

    bundle_text = render_bundle(build_bundle(
        entries=ENTRIES, baseline="BASELINE", negatives=[],
        jd_keywords=[], prefix_map={"Example Foundry": "EF"}))

    # The marker is a substring UNIQUE to what each `.replace()` actually inserted --
    # "JANE ROE" alone would not do (CLEAN_CV already contains it before any replace
    # runs, so that check would stay green even if a fixture silently reverted to
    # CLEAN_CV verbatim, which is exactly the no-op trap this assertion exists to
    # catch: PREAMBLE_BEFORE_NAME_CV == CLEAN_CV passed every OTHER assertion here).
    for fixture, marker, why in [
        (PREAMBLE_BEFORE_NAME_CV, "I'll compose a tailored CV",
         "an extra line before an otherwise-correct name"),
        (PREAMBLE_WITH_CONTACT_BLOCK_CV, "example.invalid/in/example",
         "a preamble ahead of a full contact block"),
        (REVERSED_HEADER_CV, "Phone: +1 555 0100",
         "name-then-contact instead of contact-then-name"),
        (PREAMBLE_REPLACING_CONTACT_CV, "Here is the tailored CV",
         "a preamble occupying the contact slot with the name still anchored"),
        # The three rewording fixtures need this premise as much as the four above:
        # their tests assert `skipped-gate` plus a message, both of which a fixture that
        # had stopped being gate-clean would still produce -- so they would stay green
        # while no longer isolating the STRUCTURAL guard from the fabrication gate,
        # which is the whole thing this sweep exists to catch (CodeRabbit, PR #161).
        (RECASED_CONTACT_CV, "phone: +1 555  0100",
         "a contact line differing from the declared one only in case"),
        (RESPACED_CONTACT_CV, "Phone: +1 555 0100",
         "a contact line differing from the declared one only in internal spacing"),
        (REWORDED_CONTACT_CV, "phone: +1 555 0100",
         "a contact line differing from the declared one in both case and spacing"),
    ]:
        assert marker in fixture, f"the replace no-opped ({why})"
        assert validate(fixture, bundle_text) == [], (
            f"fixture no longer gate-clean ({why}) -- the #99 tests below would "
            f"pass for the wrong reason")
        assert check_text(fixture)[0] == [], f"fixture no longer slop-clean ({why})"

    assert parse_cv(PREAMBLE_BEFORE_NAME_CV).name == "JANE ROE", (
        "premise changed: the anchor line is no longer intact in this fixture")
    assert parse_cv(PREAMBLE_WITH_CONTACT_BLOCK_CV).name == (
        "Web: https://www.example.invalid/in/example"), (
        "premise changed: the real corruption this fixture reproduces no longer "
        "misparses the same way")
    assert parse_cv(REVERSED_HEADER_CV).name == "Phone: +1 555 0100", (
        "premise changed: the reversed order no longer misparses the same way")
    # The opposite failure mode from the two fixtures above: name comes out RIGHT
    # (parse_cv's last-line rule still lands on "JANE ROE"), and it is the CONTACT
    # that silently becomes the preamble sentence -- which is exactly why the anchor
    # check alone cannot see anything wrong with this fixture.
    parsed = parse_cv(PREAMBLE_REPLACING_CONTACT_CV)
    assert parsed.name == "JANE ROE", (
        "premise changed: the name anchor is no longer intact in this fixture")
    assert parsed.contact == (
        "Here is the tailored CV for the Analyst role, prepared from the verified "
        "source bundle only."), (
        "premise changed: this fixture no longer misassigns contact the same way")


def test_the_publications_fixture_passes_the_gate():
    """A PREMISE of the test below, and the same trap
    test_the_unparseable_fixture_still_passes_the_gate closes for its own fixture: if
    this stops being gate-clean, the test below reports skipped-gate for a reason that
    has nothing to do with the seam and passes for the wrong reason."""
    assert "PUBLICATIONS" in PUBLICATIONS_CV, "the replace no-opped"
    bundle_text = render_bundle(build_bundle(
        entries=ENTRIES, baseline="BASELINE", negatives=[],
        jd_keywords=[], prefix_map={"Example Foundry": "EF"}))
    assert validate(PUBLICATIONS_CV, bundle_text) == []


@pytest.mark.parametrize("cv_text,why", [
    ("UNPARSEABLE_CV", "a meta line the template grammar cannot split"),
    ("PUBLICATIONS_CV", "a section the template grammar does not model -- the exact CV "
                        "the review measured as skipped-gate under cv.renderer: script"),
])
def test_a_renderer_without_precheck_is_not_gated_by_another_renderers_grammar(
        monkeypatch, cv_text, why):
    """The seam inversion, and the reason `precheck` is a per-renderer hook.

    The engine used to call `parse_cv` unconditionally, which is the `template`
    renderer's grammar -- so an operator on `cv.renderer: script`, whose own script
    imposes whatever grammar it likes, was gated by a requirement their renderer does not
    have. Measured 2026-08-06 on a genuinely gate-clean CV carrying a PUBLICATIONS
    section: `cv.renderer=script` reported skipped-gate with rendered=0, although the
    script would have laid that section out fine. The branch's own spec calls `script`
    the full-control escape hatch whose behaviour is out of scope, and an escape hatch
    that enforces the thing it exists to escape is not one.

    Both fixtures are pinned gate-CLEAN by their own premise tests above, so the only
    thing that could stop either rendering here is a grammar this renderer never
    declared. Asserts on `rend.rendered`, not merely on the status -- "rendered" with an
    empty renderer would mean the engine reported success having rendered nothing. The
    second half asserts the template-shaped renderer STILL refuses the same CV: a fix
    that simply stopped prechecking anything would satisfy the first half alone.
    """
    _served(monkeypatch)
    cv = globals()[cv_text]
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()          # render() only -- the `script` renderer's shape
    assert not hasattr(rend, "precheck"), "this fixture must NOT declare the optional hook"
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(cv), FakeCache(), renderer=rend)
    assert r.status == "rendered", (
        f"a renderer that declares no grammar was gated by another one's ({why}): "
        f"{r.violations}")
    assert rend.rendered == [cv]

    gated = PrecheckingRenderer()
    r2 = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                 FakeVault(ENTRIES), _cfg(), FakeBackend(cv), FakeCache(), renderer=gated)
    assert r2.status == "skipped-gate" and gated.rendered == [], (
        "the renderer that DOES declare this grammar stopped enforcing it, so the test "
        "above proves nothing about where the requirement lives")


def test_the_engine_folds_a_precheck_complaint_in_with_the_gates_own():
    """`precheck` returns STRINGS the engine treats exactly like a gate violation --
    that is the whole contract, and it is what puts a renderer's complaint in front of
    the model's one retry instead of after the LLM spend.

    Uses a renderer whose precheck is unrelated to parsing, so this pins the SEAM rather
    than re-testing parse_cv: any renderer's complaint must reach `violations` and stop
    the render, whatever its grammar happens to be.
    """
    class FussyRenderer(FakeRenderer):
        def precheck(self, cv_text):
            return ["FORMAT: this renderer wants something else entirely"]

    v = FakeVault(ENTRIES)
    rend = FussyRenderer()
    be = FakeBackend(CLEAN_CV)     # gate-clean: only the precheck can stop this
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("something else entirely" in x for x in r.violations), r.violations
    assert rend.rendered == [], "a renderer that refused the CV was still asked to render it"


def test_a_precheck_returning_a_bare_string_is_refused_by_name():
    """`precheck` is deliberately NOT a Protocol member (core/protocols.py explains why),
    so NOTHING types its return value -- and the failure that leaves open is silent.

    `list("FORMAT: bad meta line")` is 21 single-character strings, and the engine used
    to splice exactly that into `violations`: the model's one retry would then be handed
    twenty-one one-letter "gate violations" with the real complaint spelled out down the
    left margin of them, and the lead binned after a second LLM call. The refusal has to
    name the RENDERER, because the defect is in a plugin the engine did not write.

    Asserts the message, not merely the type: a bare `pytest.raises(TypeError)` is also
    satisfied by any unrelated TypeError raised anywhere in the compose path.
    """
    class SloppyRenderer(FakeRenderer):
        def precheck(self, cv_text):
            return "FORMAT: a string, not a list of them"

    rend = SloppyRenderer()
    with pytest.raises(TypeError, match=r"SloppyRenderer\.precheck returned str"):
        run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                FakeVault(ENTRIES), _cfg(), FakeBackend(CLEAN_CV), FakeCache(), renderer=rend)
    assert rend.rendered == [], "a renderer that broke the contract was still asked to render"


def test_a_precheck_returning_a_non_string_element_is_refused_by_name():
    """The CONTAINER check alone (`isinstance(reported, (list, tuple))`) let a non-str
    ELEMENT through: `[None]` passes it and then extends straight into `violations`,
    so a broken renderer that returns `[None]` fed `None` into the retry prompt build
    instead of being refused here, where the cause is still traceable to the renderer.
    Same shape as the bare-string case above, one level down.
    """
    class SloppyRenderer(FakeRenderer):
        def precheck(self, cv_text):
            return [None]

    rend = SloppyRenderer()
    with pytest.raises(TypeError, match=r"SloppyRenderer\.precheck returned list"):
        run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                FakeVault(ENTRIES), _cfg(), FakeBackend(CLEAN_CV), FakeCache(), renderer=rend)
    assert rend.rendered == [], "a renderer that broke the contract was still asked to render"


@pytest.mark.parametrize("reported,expected", [
    ([], "rendered"),
    ((), "rendered"),
    (["FORMAT: nope"], "skipped-gate"),
    (("FORMAT: nope",), "skipped-gate"),
])
def test_the_precheck_contract_still_accepts_both_intended_shapes(
        monkeypatch, reported, expected):
    """The counter-controls for the refusal above: the check must reject `str`, not
    everything. A guard that rejected the legitimate shapes too would be caught by
    nothing else here -- every other precheck test in this file returns a list."""
    _served(monkeypatch)

    class Renderer(FakeRenderer):
        def precheck(self, cv_text):
            return reported

    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                FakeVault(ENTRIES), _cfg(), FakeBackend(CLEAN_CV), FakeCache(),
                renderer=Renderer())
    assert r.status == expected


def test_the_real_template_renderer_prechecks_through_run_one():
    """The REAL `TemplateRenderer`, not a stand-in, driven through the engine.

    Every other precheck test in this file uses a fake that re-implements the hook, so
    renaming `TemplateRenderer.precheck` -- or letting the engine stop calling it --
    reddens only tests in the renderer's own file. That is the seam INVERSION bug this
    branch was written to fix, one level down: the wiring between the shipped renderer
    and the engine had no test of its own.

    `html_module` is a fake, so no WeasyPrint and no native libraries are needed; the
    render is never reached anyway, which is the point of the assertion. `None` for the
    template path takes the PACKAGED default, so this also proves the shipped template
    loads.

    NO `pytest.importorskip("jinja2")`, and its absence is deliberate. jinja2 is in the
    `test` extra precisely so this runs for real in CI, and this is the ONLY test proving
    `precheck` reaches the engine through the real renderer -- so a skip guard here would
    make the single test that matters most evaporate silently on the one machine where
    the dependency is missing, reading green. That trap is recorded in
    tests/test_renderers.py (weasyprint) and is now swept for across all of tests/ by
    test_renderer_template.py::test_no_test_module_uses_importorskip.
    """
    from sluice.renderers.template import TemplateRenderer

    class _Html:
        def __init__(self, **kw): pass
        def write_pdf(self, path): raise AssertionError("render must not be reached")

    rend = TemplateRenderer(None, html_module=_Html)
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                FakeVault(ENTRIES), _cfg(), FakeBackend(UNPARSEABLE_CV), FakeCache(),
                renderer=rend)
    assert r.status == "skipped-gate"
    assert any("FORMAT" in v for v in r.violations), r.violations
    # ...and the fixture is still the parser/gate disagreement it claims to be, not an
    # ordinary gate failure that would report skipped-gate whatever the renderer did.
    assert not any("FORMAT" not in v for v in r.violations), (
        f"the fixture stopped being gate-clean, so this says nothing about the "
        f"renderer's precheck: {r.violations}")


def test_the_engine_no_longer_imports_the_template_grammar():
    """The coupling this inversion removes, asserted STRUCTURALLY.

    Re-adding `from sluice.cv.parse import ...` to cv/engine.py would restore the
    inversion while every behavioural test above still passed -- the unconditional call
    is what they catch, not the import that enables it. `cv/parse.py` is the `template`
    renderer's grammar; the orchestrator has no business knowing it exists.
    """
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "sluice" / "cv" / "engine.py"
    tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
    offenders = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("cv.parse")]
    offenders += [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Import)
                  and any(a.name.endswith("cv.parse") for a in n.names)]
    assert not offenders, (
        f"cv/engine.py imports the `template` renderer's grammar at line(s) {offenders}; "
        "reach it through the renderer's optional precheck hook instead")


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
    # Exact message, not merely "STRUCTURAL" -- #99 added three more STRUCTURAL
    # producers to this same engine, so a loose substring match would stay green if
    # this guard broke and one of the newer ones happened to also fire.
    assert ("STRUCTURAL: composed CV lacks the exact 'WORK EXPERIENCE' header, so "
            "the citation gate did not run") in r.violations
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

def test_a_preamble_before_the_name_fails_closed():
    # Uses a plain FakeRenderer (no precheck) deliberately: this is the whole reason
    # the #99 guard lives in the ENGINE and not in cv/parse.py -- it must bind a
    # renderer that declares no grammar of its own (the `script` renderer's shape),
    # not only the `template` renderer whose precheck already reaches parse_cv.
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    assert not hasattr(rend, "precheck"), "this fixture must NOT declare the optional hook"
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(PREAMBLE_BEFORE_NAME_CV), FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("STRUCTURAL" in x and "before PROFILE" in x for x in r.violations), r.violations
    assert v.written == {}
    assert rend.rendered == [], "a CV was RENDERED despite an open fabrication gate"


def test_a_preamble_with_a_real_contact_block_fails_closed():
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(PREAMBLE_WITH_CONTACT_BLOCK_CV), FakeCache(),
                renderer=rend)
    assert r.status == "skipped-gate"
    assert any("STRUCTURAL" in x and "before PROFILE" in x for x in r.violations), r.violations
    assert v.written == {}
    assert rend.rendered == [], "a CV was RENDERED despite an open fabrication gate"


def test_a_reversed_header_block_fails_closed():
    # Isolates the ANCHOR guard from the count guard: the candidate profile's contact
    # (mobile="Phone: +1 555 0100", #107 -- seeded on FakeVault, not cfg.contact, since
    # the engine no longer reads that field) is exactly the one line REVERSED_HEADER_CV
    # supplies, so the line COUNT is correct and only the ORDER is wrong. If the count
    # guard alone were doing the work, this fixture -- which the count guard cannot see
    # anything wrong with -- would sail through.
    v = FakeVault(ENTRIES, candidate=CandidateProfile(
        forenames="Jane", surname="Roe", mobile="Phone: +1 555 0100"))
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(REVERSED_HEADER_CV), FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("STRUCTURAL" in x and "not the name heading" in x for x in r.violations), (
        r.violations)
    assert rend.rendered == [], "a CV was RENDERED despite an open fabrication gate"


def test_a_preamble_replacing_the_contact_line_fails_closed():
    # Isolates the CONTENT guard from both the count guard and the anchor guard: with
    # the candidate profile's contact (#107) declared as one line, PREAMBLE_REPLACING_
    # CONTACT_CV's header is exactly the expected two lines and the last one still IS
    # the configured name -- neither the count check nor the anchor check sees anything
    # wrong with it. Only comparing the actual line against the derived contact catches
    # that a preamble sentence, not the real contact information, occupies that slot.
    # (CodeRabbit, PR #100 review: the original #99 guards checked the header's line
    # COUNT and its final line but never the CONTENT of the lines in between.)
    v = FakeVault(ENTRIES, candidate=CandidateProfile(
        forenames="Jane", surname="Roe", mobile="Phone: +1 555 0100"))
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(PREAMBLE_REPLACING_CONTACT_CV), FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("STRUCTURAL" in x and "do not match" in x for x in r.violations), r.violations
    assert v.written == {}
    assert rend.rendered == [], "a CV was RENDERED despite an open fabrication gate"


@pytest.mark.parametrize("fixture,axis", [
    (RECASED_CONTACT_CV, "CASE"),
    (RESPACED_CONTACT_CV, "SPACING"),
    (REWORDED_CONTACT_CV, "CASE and SPACING"),
], ids=["case-only", "spacing-only", "both"])
def test_a_reworded_contact_block_refuses_but_says_so_accurately(fixture, axis):
    """The CONTENT guard's rendering arm, asserted in both directions per axis.

    Parametrised over all three because the axes were found ONE REVIEW ROUND APART
    (case, then internal spacing) -- which is the signal that enumerating shapes was the
    wrong shape of fix. The engine keys on a normalisation instead, and this table is
    what stops a later axis being added to `_CONTACT_REWORDINGS` without a row proving
    the message names it. The `both` row is the one a hand-written branch would most
    likely get wrong.

    Internal spacing needs its own row rather than being assumed equivalent to case:
    `header` and `expected_contact` are already per-line stripped, so only an INTERNAL
    run survives to reach the comparison, and `contact_block` deliberately does not
    collapse runs the way `full_name` does.

    Each fixture must still REFUSE. `cv/parse.py` takes the contact from the composed
    TEXT (`header_lines[:-1]`) and the engine never substitutes `cv_contact` back in, so
    whatever clears this check is what renders -- normalising the comparison, the fix
    originally suggested, would print a re-rendered LinkedIn URL or postcode on a PDF
    sent under the candidate's identity. That is why this guard does not normalise while
    the name anchor above it case-folds: a CV name heading is conventionally uppercase,
    so case drift there is what `compose.py`'s prompt asked for; the contact block is
    required verbatim.

    And each must say WHY accurately. The retry gets exactly one attempt off this
    message, and "a preamble or other text has replaced a real contact line" is a wrong
    diagnosis here -- acting on it, by dropping a preamble that is not there, cannot fix
    anything, so a gate-clean CV gets binned on a misdescription.
    """
    v = FakeVault(ENTRIES, candidate=CandidateProfile(
        forenames="Jane", surname="Roe", mobile=_DECLARED_CONTACT))
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(fixture), FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert rend.rendered == [], "a re-rendered contact block must not reach the renderer"
    assert any("STRUCTURAL" in x and f"{axis} was changed" in x for x in r.violations), (
        f"expected the message to name {axis!r}: {r.violations}")
    assert not any("preamble or other text" in x for x in r.violations), (
        "a rendering-only difference must not be diagnosed as a preamble -- the retry "
        f"cannot act on that: {r.violations}")


def test_a_preamble_reaches_the_retry_not_the_bin(monkeypatch):
    # Same posture as test_a_parse_failure_feeds_the_retry_not_the_bin: a composer
    # mistake must be fed back to the model for ONE retry, never binned outright.
    _served(monkeypatch)

    class TwoShotBackend:
        def __init__(self):
            self.last_backend = "primary"; self.prompts = []
        def complete(self, prompt):
            if not ("SOURCE BUNDLE" in prompt and "auditing" not in prompt):
                return "supported\tx\tSF1"
            self.prompts.append(prompt)
            return PREAMBLE_BEFORE_NAME_CV if len(self.prompts) == 1 else CLEAN_CV

    be = TwoShotBackend()
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "rendered", "a preamble binned the lead instead of retrying it"
    assert len(be.prompts) == 2, "the STRUCTURAL violation did not reach the existing retry"
    assert "STRUCTURAL" in be.prompts[1], "the violation never reached the retry prompt"
    assert rend.rendered == [CLEAN_CV], "the renderer got the preamble-corrupted CV"


class ComposeCountingBackend:
    """Like FakeBackend, but counts COMPOSE calls specifically rather than every
    complete() call -- both compose attempts route through the compose branch,
    while a successful run's one audit call does not, so compose_calls == 1 is
    the assertion that actually discriminates "recovered on the first attempt"
    from "exhausted both attempts": both paths total 2 complete() calls overall
    (2 compose + 0 audit on failure, 1 compose + 1 audit on success), which is
    what made an earlier be.calls == 2 assertion here pass for the wrong reason
    (sluice-test-engineer, local /review-pr on this branch)."""
    def __init__(self, cv_out, audit_out="supported\tx\tSF1"):
        self.cv_out = cv_out; self.audit_out = audit_out
        self.last_backend = "primary"; self.compose_calls = 0
    def complete(self, prompt):
        if "SOURCE BUNDLE" in prompt and "auditing" not in prompt:
            self.compose_calls += 1
            return self.cv_out
        return self.audit_out


# ── #28 (sixth branch): a conversational envelope around an otherwise-clean CV ──
def test_a_trailing_conversational_envelope_is_recovered_on_the_first_attempt(monkeypatch):
    # Captured on the real production path (#28): the composer wraps an
    # otherwise gate-clean CV in a closing remark behind a single markdown-style
    # '---' fence, with NO leading preamble -- the model complying with "no
    # preamble" but still appending a closing summary. This is the shape the
    # original #28 fix candidate's two-fence-only unwrap did not cover, and
    # which the #99/#100 header guards above cannot see at all (they only
    # inspect lines BEFORE PROFILE). Recovery happens in compose.py itself, so
    # this must render on the FIRST attempt -- compose_calls == 1 proves no
    # retry was needed, not merely that one eventually worked.
    _served(monkeypatch)
    enveloped = (CLEAN_CV + "\n\n---\n\n"
                 "CV tailored for Example Foundry's Analyst role. "
                 "All bullets cited from source bundle.")
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    be = ComposeCountingBackend(enveloped)
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), be, FakeCache(), renderer=rend)
    assert r.status == "rendered", r.violations
    assert be.compose_calls == 1, "the envelope reached a second compose attempt, not compose.py's own recovery"
    assert rend.rendered == [CLEAN_CV]


def test_a_header_stripped_between_name_and_profile_still_fails_closed():
    # The end-to-end proof for the accepted gap documented on
    # test_unwrap_envelope_may_strip_a_real_header_when_a_fence_splits_it_from_profile
    # (test_cv_compose.py): compose.py's envelope recovery can misread a fence
    # sitting between the name and PROFILE as a leading aside and strip the real
    # name with it -- an unobserved shape, but worth pinning that it degrades
    # safely. The resulting CV has no header line before PROFILE, which is
    # exactly what the pre-existing #99 STRUCTURAL count guard rejects, so this
    # must still report skipped-gate and render nothing, not a CV missing its
    # own name.
    lines = CLEAN_CV.splitlines()
    name_idx = lines.index("JANE ROE")
    corrupted = "\n".join(lines[:name_idx + 1] + ["---"] + lines[name_idx + 1:])
    v = FakeVault(ENTRIES)
    rend = FakeRenderer()
    r = run_one(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}),
                v, _cfg(), FakeBackend(corrupted), FakeCache(), renderer=rend)
    assert r.status == "skipped-gate"
    assert any("STRUCTURAL" in x and "before PROFILE" in x for x in r.violations), r.violations
    assert rend.rendered == [], "a CV missing its own name was RENDERED"


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
    be = FakeBackend(CLEAN_CV)
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
    alone is satisfied even by a composer that ran first and only failed the gate
    afterward (e.g. on the STRUCTURAL header guard, since a blank identity's header
    line is blank too) -- only a zero-call count proves nothing was spent.
    """
    def __init__(self, cv_out=CLEAN_CV):
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

    Also seeds a baseline CV: unlike read_candidate_profile/read_criteria,
    Vault.read_baseline has no missing-file abstain -- it raises FileNotFoundError --
    so without this, run_one's `vault.read_baseline()` call would raise before ever
    reaching compose, which would break test_a_fully_declared_identity_reaches_the_
    backend (the one case here that DOES need to reach it). No Experience Library
    entry is written: read_experience_entries abstains to [] on a missing library
    (the ordinary "no entries yet" case, tests/harness/config.py's own comment on
    _seed_vault makes the same choice), and this helper only needs the backend to be
    CALLED, never a CV that clears the fabrication gate.
    """
    from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH
    from sluice.core.vault import Vault

    if overrides:
        dest = os.path.join(str(tmp_path), CANDIDATE_PROFILE_RELPATH)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        fm = "\n".join(f"{k}: {v}" for k, v in overrides.items())
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(f"---\n{fm}\n---\n")
    baseline_dir = os.path.join(str(tmp_path), "My CV")
    os.makedirs(baseline_dir, exist_ok=True)
    with open(os.path.join(baseline_dir, "CV.md"), "w", encoding="utf-8") as fh:
        fh.write("Synthetic baseline CV for the test.\n")
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
    # composes with a blank headline, burns the spend, and fails the STRUCTURAL
    # guard on every attempt. Without this test, deleting the `not cv_name.strip()`
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


def test_a_fully_declared_identity_reaches_the_backend(tmp_path):
    vault = _vault_with_candidate(tmp_path, {"forenames": "Ada", "surname": "Example",
                                             "email": "ada@example.invalid"})
    backend = _CountingBackend()
    run_one(_note(), vault, _cfg(), backend, FakeCache(), renderer=FakeRenderer())
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
    from sluice.core.doctor import DEAD, classify_store

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
        engine_refused = run_one(_note(), vault, _cfg(), _CountingBackend(), FakeCache(),
                                 renderer=FakeRenderer()).status == "skipped-config"
        rows = [c for c in classify_store(vault.preflight()) if c.subject == "Candidate Profile"]
        assert len(rows) == 1, f"{label}: expected exactly one Candidate Profile row"
        doctor_dead = rows[0].state == DEAD
        assert engine_refused == doctor_dead, (
            f"{label}: run_one refused={engine_refused} but doctor DEAD={doctor_dead}")
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
    """#107: compose() must be called with the VAULT-derived cv_name/cv_contact, read
    fresh from `vault.read_candidate_profile()`, not any identity value diverted
    from that read. Every FakeBackend in this file returns a fixed canned CV
    regardless of what the prompt asked for, which is exactly why every OTHER
    "rendered" test here would stay green even if compose() were fed the wrong
    identity: the STRUCTURAL guard only re-derives cv_name/cv_contact and compares
    them against the (unconditionally correct) FIXED response, never against what
    was actually SENT. Only inspecting the recorded prompt itself proves the
    argument at the call site, not merely the guard reading it back.

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
            return CLEAN_CV if "SOURCE BUNDLE" in prompt and "auditing" not in prompt \
                else "supported\tx\tSF1"

    vault = _vault_with_candidate(
        tmp_path, {"forenames": "Distinctive", "surname": "Candidate",
                   "email": "distinctive@example.invalid"})
    be = RecordingBackend()
    run_one(_note(), vault, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    compose_prompts = [p for p in be.prompts if "SOURCE BUNDLE" in p and "auditing" not in p]
    assert compose_prompts, "compose was never reached"
    assert "Distinctive Candidate" in compose_prompts[0], (
        "the compose prompt did not carry the vault-derived name -- compose() may "
        "be reading a diverted identity instead of the derived cv_name")
    assert "distinctive@example.invalid" in compose_prompts[0], (
        "the compose prompt did not carry the vault-derived contact -- compose() "
        "may be reading a diverted identity instead of the derived cv_contact")


def test_slop_allow_reaches_the_shipped_compose_prompt():
    """#167 (Task 17, item 1): cv.slop_allow must reach the ACTUAL compose() call
    engine.py makes, not merely be plumbed through compose.py's own build_prompt in
    isolation. A unit test of build_prompt alone (see
    tests/test_cv_compose.py::test_an_allowed_phrase_is_not_instructed_against_either)
    would stay green even if run_one's `_compose.compose(...)` call site never forwarded
    `cvcfg.slop_allow` -- the parameter would exist and be dead. Mirrors
    test_the_compose_prompt_carries_the_derived_identity_not_cvcfg immediately above:
    only inspecting the recorded prompt itself proves the argument at the real call
    site, not merely a guard reading it back.

    "leverage" is chosen because it appears NOWHERE in this file's fixtures (ENTRIES,
    CLEAN_CV, the identity block) outside the ban-list sentence itself, so its absence
    from the shipped prompt can only mean slop_allow suppressed it there.

    `dry_run=True`: this test's only interest is the PROMPT compose() was sent, not the
    render/serve tail end of run_one -- CLEAN_CV matches DEFAULT_CANDIDATE (FakeVault's
    default), so the hard gate clears on attempt 1 and the real (unmocked)
    `sluice.cv.render.serve` would otherwise run against a path FakeRenderer never
    actually writes.
    """
    class RecordingBackend:
        def __init__(self):
            self.last_backend = "primary"; self.prompts = []
        def complete(self, prompt):
            self.prompts.append(prompt)
            return CLEAN_CV if "SOURCE BUNDLE" in prompt and "auditing" not in prompt \
                else "supported\tx\tSF1"

    v = FakeVault(ENTRIES)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    cfg = _cfg()
    cfg.slop_allow = ["leverage"]
    be = RecordingBackend()
    run_one(note, v, cfg, be, FakeCache(), renderer=FakeRenderer(), dry_run=True)
    compose_prompts = [p for p in be.prompts if "SOURCE BUNDLE" in p and "auditing" not in p]
    assert compose_prompts, "compose was never reached"
    assert "leverage" not in compose_prompts[0], (
        "cvcfg.slop_allow did not reach the shipped compose prompt -- engine.py's "
        "_compose.compose(...) call site may not be forwarding slop_allow")


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
    """A single shortlist lead composed against CLEAN_CV, with the fetched JD content
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
    return run_one(note, v, cfg, FakeBackend(CLEAN_CV), _VariableJdCache(jd_markdown),
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
    for name in ("read_experience_entries", "read_baseline", "read_leads", "set_tailored_cv",
                 "read_candidate_profile"):
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
    be, cache = FakeBackend(CLEAN_CV), RecordingCache()
    out = run_batch(v, _cfg(), be, cache, renderer=FakeRenderer())
    assert [r.status for r in out] == ["skipped-ambiguous", "skipped-ambiguous"]
    assert be.calls == 0 and cache.calls == 0      # no LLM call, no dossier fetch
    assert v.written == {} and v.fields == {}      # neither twin got a pointer or a hold


def test_run_batch_names_the_colliding_refs(caplog):
    """The refs, never the slug alone: these notes collide BY slug, so repeating it names
    nothing a human can act on while the paths name the two files to rename or merge."""
    with caplog.at_level("WARNING"):
        run_batch(FakeVault(ENTRIES, notes=_twins()), _cfg(), FakeBackend(CLEAN_CV),
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
    out = run_batch(v, _cfg(), FakeBackend(CLEAN_CV), FakeCache(), renderer=FakeRenderer())
    by_ref = {r.lead: r.status for r in out}
    assert by_ref[ordinary.ref] == "rendered"
    assert set(v.written) == {ordinary.ref}
    assert [s for s in by_ref.values() if s == "skipped-ambiguous"] == ["skipped-ambiguous"] * 2


# ── #167: the loop retains the last HARD-clean draft, and rebinds before the audit ──
#
# "Attempt 1 clears the HARD gate but carries a STYLE finding" is a sequence NO fixture
# in this file could produce before #167: the loop broke the moment the HARD gate was
# clean, so attempt 2 never ran and nothing here ever exercised a retained draft
# outliving a dirtier retry. Every fixture below exists to build one of those sequences.

# HARD-clean, STYLE-dirty. "leverage" is a slop._PHRASES stem, placed in PROFILE prose --
# one of the two regions cv/engine.py scopes the style tier to via section_spans. The
# replacement introduces no digit, so validate()'s profile-metric sweep stays clean and
# this draft clears the HARD gate exactly as CLEAN_CV does.
STYLE_DIRTY_CV = CLEAN_CV.replace(
    "I build reliable systems.",
    "I leverage the same delivery patterns across teams.")

# HARD-dirty and nothing else: an em dash, slop.HARD's blocking tier. The bullet keeps
# its citation and gains no number, so validate() still reports nothing -- the ONLY thing
# wrong with this draft is the HARD slop rule, which is what makes it a clean
# discriminator between the two tiers rather than a draft failing for several reasons at
# once.
HARD_DIRTY_CV = CLEAN_CV.replace("- Coached [EF1]", "- Coached — and mentored [EF1]")

# A phrase stem in an EMPLOYER line AND one in PROFILE prose. Both halves are
# load-bearing: the profile phrase is what forces a retry to exist at all, and without a
# retry prompt "no retry message names the employer" would be vacuously true. The
# employer keeps this file's synthetic "Example <Word>" convention -- nothing local can
# tell a real firm from an invented one, so the fixture must not put the question.
EMPLOYER_PHRASE_CV = CLEAN_CV.replace(
    "Example Systems", "Example Leverage", 1).replace(
    "I build reliable systems.", "I streamline delivery for platform teams.", 1)

# A CV that repeats the PROFILE header AFTER `WORK EXPERIENCE`. `section_spans` sets
# in_profile on that second header WITHOUT clearing in_work (its own docstring says so),
# so a BULLET underneath lands in BOTH of the lists it returns -- the only shape that
# can. Gate-clean and HARD-clean: the bullet carries a real bundle citation and no
# number, so the ONE thing this fixture exercises is the overlap.
DOUBLED_PROFILE_CV = CLEAN_CV.replace(
    "CERTIFICATES", "PROFILE\n- I foster delivery [EF1]\n\nCERTIFICATES", 1)

_DRAFTS = {
    "clean": CLEAN_CV,
    "hard-clean-style-dirty": STYLE_DIRTY_CV,
    "hard-dirty": HARD_DIRTY_CV,
    "employer-phrase": EMPLOYER_PHRASE_CV,
    "doubled-profile": DOUBLED_PROFILE_CV,
}


def test_the_sequence_fixtures_are_the_tiers_they_claim():
    """PREMISE of every test below, per fixture and per TIER.

    Each test below asserts a SEQUENCE outcome, and every one of them stays green if a
    fixture silently drifts into a different tier: a "hard-clean-style-dirty" draft that
    had become HARD-dirty would still produce skipped-gate, for a reason that has nothing
    to do with the retention this task adds. The same trap
    test_clean_cv_is_actually_clean closes for CLEAN_CV.

    This also subsumes the usual no-op check on the `.replace()` calls above: a
    replacement that silently matched nothing leaves the fixture equal to CLEAN_CV, which
    is (hard=False, style=False), so it fails its own row here.
    """
    from sluice.cv.slop import check_hard, check_phrases
    from sluice.cv.validate import section_spans

    bundle_text = render_bundle(build_bundle(
        entries=ENTRIES, baseline="BASELINE", negatives=[],
        jd_keywords=[], prefix_map={"Example Foundry": "EF"}))

    def _style(text):
        # The engine's own scoping, reproduced: PROFILE prose + WORK bullets, nothing
        # else. A fixture that is "style-dirty" only OUTSIDE that scope is not
        # style-dirty as far as this loop is concerned.
        profile, work = section_spans(text)
        return check_phrases(profile + work)

    for name, text, hard, style in [
        ("clean", CLEAN_CV, False, False),
        ("hard-clean-style-dirty", STYLE_DIRTY_CV, False, True),
        ("hard-dirty", HARD_DIRTY_CV, True, False),
        ("employer-phrase", EMPLOYER_PHRASE_CV, False, True),
        ("doubled-profile", DOUBLED_PROFILE_CV, False, True),
    ]:
        assert validate(text, bundle_text) == [], f"{name} is no longer gate-clean"
        assert bool(check_hard(text)) is hard, f"{name}'s HARD tier drifted"
        assert bool(_style(text)) is style, f"{name}'s STYLE tier drifted"

    # The employer line's OWN phrase must exist, or the scoping test below asserts the
    # absence of something that was never there in the first place.
    assert "Example Leverage" in EMPLOYER_PHRASE_CV, "the employer replace no-opped"
    assert check_phrases([(1, "Example Leverage")]), (
        "'Example Leverage' no longer matches a slop._PHRASES stem, so the scoping "
        "test below would pass without the engine scoping anything")


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
        # Same routing rule as FakeBackend: a compose prompt carries "SOURCE BUNDLE"
        # and not "auditing"; an audit prompt carries both.
        if "SOURCE BUNDLE" in prompt and "auditing" not in prompt:
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
            return _DRAFTS[name]
        body = prompt.partition(self._CV_MARKER)[2]
        assert body, "cv/audit.py no longer carries the CV under '=== CV ==='"
        self.audited.append(body[:-1])   # build_audit_prompt appends exactly one "\n"
        return self.audit_out


def _run_sequence(monkeypatch, drafts):
    """run_one over a scripted sequence of composed drafts.

    Returns (result, backend, renderer): the renderer records what SHIPPED and the
    backend records what was AUDITED, which is the pair these tests compare. _served
    stands in for the real render/serve seam for the same reason every other "rendered"
    test in this file does it -- FakeRenderer hands back a path that does not exist.
    """
    _served(monkeypatch)
    be, rend = _SequenceBackend(drafts), FakeRenderer()
    v = FakeVault(ENTRIES)
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
    assert rend.rendered == [STYLE_DIRTY_CV], (
        "the retained HARD-clean draft is what must ship")


def test_the_audit_runs_over_the_RENDERED_draft_not_the_discarded_one(monkeypatch):
    """`cv_text` is read post-loop TWICE -- by run_audit and by renderer.render -- and the
    audit's flags drive unsupported_claims -> hold_for_signoff -> the withheld
    tailored_cv. Auditing one draft while rendering another means a fabricated claim in
    the SERVED CV goes un-held, is written send-ready, and the run reports
    "rendered / audit flags: 0".

    Agreement alone is NOT the property, which is why the last assertion is here and is
    not a restatement of the first: dropping the rebind entirely leaves BOTH readers on
    the discarded attempt-2 draft, so they still agree -- on a CV that never cleared the
    HARD gate. They must agree ON THE RETAINED DRAFT.
    """
    from sluice.cv.slop import check_hard

    res, be, rend = _run_sequence(monkeypatch, ["hard-clean-style-dirty", "hard-dirty"])
    assert res.status == "rendered"
    assert be.audited, "the audit never ran, so the comparisons below would be vacuous"
    assert be.audited[-1] == rend.rendered[-1], (
        "the audit ran over a draft the user never sees")
    assert not check_hard(be.audited[-1]), (
        "both readers moved together onto the DISCARDED, HARD-dirty draft")


def test_a_phrase_in_an_EMPLOYER_line_never_reaches_the_retry(monkeypatch):
    """The scoping guarantee, pinned where the scoping actually HAPPENS.

    cv/slop.py's check_phrases has no opinion about which lines it is handed (it is
    deliberately dependency-free, so the PROFILE/WORK split cannot live there); the
    ENGINE is what must hand it only PROFILE prose and WORK bullets, via section_spans.
    A retry message naming an employer line is answerable only by RENAMING THE EMPLOYER
    -- a style rule turned into fabrication pressure, the shape CLAUDE.md records as the
    worst case this codebase has shipped.
    """
    _res, be, _rend = _run_sequence(monkeypatch, ["employer-phrase", "employer-phrase"])
    assert len(be.compose_prompts) == 2, "no retry happened, so this asserts nothing"
    retry = be.compose_prompts[1]
    assert "SLOP streamline" in retry, (
        "the PROFILE phrase never reached the retry either, so the absence below would "
        "say nothing about SCOPING")
    assert "Example Leverage" not in retry, retry


def test_a_line_in_BOTH_scoped_regions_is_complained_about_once(monkeypatch):
    """The style findings are handed to the composer VERBATIM, so the SHAPE of the list is
    what the model reads -- which is why `validate` merges its own two regions into one
    line-ordered pass rather than concatenating them (see its comment), and why the
    engine's style tier has to do the same. Concatenating instead yields the identical
    complaint once per region the line belongs to, and puts a late PROFILE line ahead of
    an earlier WORK bullet.
    """
    from sluice.cv.validate import section_spans

    profile, work = section_spans(DOUBLED_PROFILE_CV)
    assert set(dict(profile)) & set(dict(work)), (
        "the fixture no longer puts a line in BOTH regions, so this asserts nothing")

    _res, be, _rend = _run_sequence(monkeypatch, ["doubled-profile", "doubled-profile"])
    assert len(be.compose_prompts) == 2, "no retry happened, so there is no list to check"
    assert be.compose_prompts[1].count("SLOP foster") == 1, be.compose_prompts[1]


def test_a_retry_that_RAISES_still_ships_the_draft_attempt_1_earned(monkeypatch, caplog):
    """Retention has to cover a retry that never RETURNS, not just one that comes back
    worse.

    `_compose.compose` catches nothing (cv/compose.py), so a BackendError -- a timeout,
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
    assert rend.rendered == [STYLE_DIRTY_CV]
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
# the compose prompt ("SOURCE BUNDLE") or the audit one ("auditing"), and folding a
# third prompt kind into _SequenceBackend's two-way dispatch risks a voice call being
# silently misrouted into `audited` -- corrupting every OTHER test in this file that
# reads `be.audited` -- rather than a clean failure local to these tests.
class _VoiceBackend:
    """Scripts the compose drafts (from _DRAFTS, one per call) and the model's own
    reply to the VOICE check, and records which KIND every `complete()` call carried
    -- "compose", "voice", or "audit" -- so a wiring test can assert not just the
    outcome but which calls were made, and how many."""

    def __init__(self, drafts, *, voice_out="", voice_raises=False,
                audit_out="supported\tx\tSF1"):
        self.drafts = list(drafts)
        self.voice_out = voice_out
        self.voice_raises = voice_raises
        self.audit_out = audit_out
        self.last_backend = "primary"
        self.calls = []                # "compose" | "voice" | "audit", in call order
        self.compose_prompts = []

    def complete(self, prompt):
        first = prompt.splitlines()[0] if prompt else ""
        if first.startswith("You are judging the VOICE"):
            self.calls.append("voice")
            if self.voice_raises:
                raise RuntimeError("voice backend down")
            return self.voice_out
        if "SOURCE BUNDLE" in prompt and "auditing" not in prompt:
            self.calls.append("compose")
            self.compose_prompts.append(prompt)
            assert len(self.compose_prompts) <= len(self.drafts), (
                f"the engine composed {len(self.compose_prompts)} times; this "
                f"sequence scripts {len(self.drafts)} draft(s)")
            return _DRAFTS[self.drafts[len(self.compose_prompts) - 1]]
        self.calls.append("audit")
        return self.audit_out


def _run_voice_sequence(monkeypatch, drafts, *, voice_check, **kw):
    """run_one over a scripted draft sequence with `cv.voice_check` set. Returns
    (result, backend) -- mirrors _run_sequence, minus the renderer no test below
    needs to inspect."""
    _served(monkeypatch)
    be = _VoiceBackend(drafts, **kw)
    v = FakeVault(ENTRIES)
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
    # STYLE_DIRTY_CV's own phrase (see its fixture comment) -- pre-formatted "SLOP
    # <phrase>: <snippet>", the same shape `hard_msgs` already used for the retry.
    assert any(s.startswith("SLOP leverage:") for s in res.slop), res.slop
    # The scripted VOICE finding, verbatim -- run_voice keeps the whole "flag\t..."
    # line, not just the phrase (cv/voice.py's own parsing).
    assert res.voice_flags == ["flag\tThis reads like a press release."]
    # attempt 2's own defect (an em dash, HARD_DIRTY_CV's fixture) must not appear:
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
    both_dirty = STYLE_DIRTY_CV.replace(
        "- Coached [EF1]", "- Coached — and mentored [EF1]")
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
                            require_signoff=True, audit_out="supported\tx\tSF1"):
    _served(monkeypatch)
    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"})
    be = _SequenceBackend(drafts, audit_out=audit_out)
    v = FakeVault(ENTRIES, notes=[note])
    cfg = _cfg()
    cfg.style_hold = style_hold
    cfg.require_signoff = require_signoff
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
