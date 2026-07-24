"""The Store contract, asserted against EVERY registered store.

This file is the reason the store seam is safe to open.

Never-clobber used to be a property of `core/vault.py` -- of ONE implementation, guarded
by tests that named that implementation. The moment a second store is selectable from
config, it has to be a property of the CONTRACT instead, or the second store ships
without it and silently destroys data the first one protected. Nobody notices until a
re-scrape has eaten a week of triage decisions.

An adversarial review of the FIRST version of this file wrote a store that preserved
`status`, `score` and the body -- the only things that version checked -- while wiping
`tailored_cv`, `applied_date` and `applied_url` on every re-scrape. It passed all nine
tests. That store would have erased the record of when the user applied, and made an
already-applied lead re-appliable. So: assert the WHOLE frontmatter dict, not a sample.
The invariant is "only last_seen may change", and that is what is written below. (The
same evil store now fails, which is how I know this version has teeth.)

Everything here asserts BEHAVIOUR. Nothing may know that a lead is a file, that a `ref`
is a path, or that a slug came from a filename.
"""
import inspect

import pytest

from sluice.core import plugins
from sluice.core.app import Sluice
from sluice.core.leads import Lead
from tests.conformance.seeds import seed
from tests.conftest import LOCATIONS

_STORES = Sluice.available("store")

# A parametrize over an EMPTY list skips every test and exits 0. The suite that is "the
# reason the store seam is safe to open" would then report success having tested nothing,
# and a new store that mis-registers (typo'd seam name) would never be tested at all.
# `plugins.autoload` deliberately swallows a broken plugin's ImportError, which makes an
# empty registry a realistic accident rather than a hypothetical one. Fail loudly.
assert _STORES, "no store is registered: the contract suite would pass vacuously"

pytestmark = pytest.mark.parametrize("store_name", _STORES)


def _make_store(store_name, tmp_path, monkeypatch):
    """Build the named store rooted somewhere disposable."""
    from sluice.core.config import Config
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    return plugins.get("store", store_name)(Config())


def _lead(**kw):
    base = dict(source="testboard", search="s", title="Analyst", company="Example Foundry",
                location=LOCATIONS[0], salary="", url="https://example.invalid/jobs/1")
    base.update(kw)
    return Lead(**base)


def _enrich(store, ref):
    """Drive a lead into the state a real one reaches: judged, CV'd, applied. These are
    exactly the fields a too-narrow never-clobber test forgets about."""
    store.update_fields(ref, {
        "status": "applied",
        "score": "9",
        "relevance_notes": '"strong culture match"',
        "tailored_cv": "CV_ab12.pdf (2026-07-10)",
        "applied_date": "2026-07-10",
        "applied_url": "https://example.invalid/apply/1",
        "ats": "greenhouse",
    })


# ── never-clobber ────────────────────────────────────────────────────────────
def test_rescrape_touches_last_seen_AND_NOTHING_ELSE(store_name, tmp_path, monkeypatch):
    """THE invariant, stated exactly: on a re-scrape, `last_seen` is the ONLY field that
    may change. Not "status survives". Not "score survives". Only last_seen changes.

    The failure this pins: the user applies to a role; the board re-lists it; the next
    `ingest run` erases `applied_date` and `applied_url`. `track` still sees
    `status: applied`, so nothing errors and nothing is logged. The application record is
    simply gone, and they discover it when they cannot remember whether they ever heard
    back.
    """
    store = _make_store(store_name, tmp_path, monkeypatch)
    # Create with an EXPLICIT early date, not the _today() default, so the forward
    # re-scrape below is deterministic and does not depend on when the suite runs (the
    # re-scrape date must be LATER than the stored one for last_seen to move at all --
    # last_seen is monotonic, see test_rescrape_never_regresses_last_seen).
    assert store.upsert(_lead(first_seen="2026-07-10", last_seen="2026-07-10")) == "created"
    _enrich(store, store.read_leads()[0].ref)
    store.append_body_section(store.read_leads()[0].ref, "d-1",
                              "## Dossier <!--d-1-->\n\nbody")

    before = store.read_leads()[0]
    before_fm = dict(before.fm)

    # The lead comes back on a LATER scrape, as it will every day it stays posted.
    assert store.upsert(_lead(last_seen="2026-07-14")) == "updated"

    after = store.read_leads()[0]
    after_fm = dict(after.fm)

    assert after_fm.pop("last_seen", None) == "2026-07-14"
    before_fm.pop("last_seen", None)
    assert after_fm == before_fm, "a re-scrape changed a field other than last_seen"
    assert after.body == before.body, "a re-scrape rewrote the note body"
    assert after.status == "applied", "a re-scrape clobbered an application-owned status"


def test_rescrape_never_regresses_last_seen(store_name, tmp_path, monkeypatch):
    """last_seen is MONOTONIC: a re-scrape carrying a date OLDER than the stored one must
    leave the newer value in place. "Last seen" moving into the past is incoherent -- the
    lead WAS seen on the newer date -- and a board that re-lists a role with a stale date
    must not drag the marker backwards. Stated on the CONTRACT, next to the never-clobber
    invariant it belongs with, so a second store inherits it rather than silently regressing.
    """
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(last_seen="2026-07-14")) == "created"
    # The board re-lists the role with a STALE date. Update or merge -- either way the
    # stamp must not move into the past.
    assert store.upsert(_lead(last_seen="2026-07-09")) in ("updated", "merged")
    assert store.read_leads()[0].fm.get("last_seen") == "2026-07-14", \
        "an older re-scrape regressed last_seen into the past"


def test_update_fields_sets_only_the_named_keys_and_preserves_body(store_name, tmp_path,
                                                                   monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    _enrich(store, store.read_leads()[0].ref)
    store.append_body_section(store.read_leads()[0].ref, "s-1",
                              "## Notes <!--s-1-->\n\ntext")

    before = store.read_leads()[0]
    before_fm = dict(before.fm)
    store.update_fields(before.ref, {"status": "research"})
    after = store.read_leads()[0]

    assert after.status == "research"
    assert after.body == before.body, "update_fields must leave the body byte-for-byte intact"
    changed = {k for k in set(before_fm) | set(after.fm)
               if before_fm.get(k) != after.fm.get(k)}
    assert changed == {"status"}, f"update_fields touched unnamed keys: {changed - {'status'}}"


def test_append_body_section_is_idempotent(store_name, tmp_path, monkeypatch):
    """The contract puts the onus on the CALLER to embed `tag` in the section (real
    callers use an HTML comment -- see track/reconcile.py); the store then refuses a
    second append. Writing this test is what taught me that: I first asserted a store
    which tracked tags itself, a contract the vault never offered."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    section = "## Dossier <!--dossier-1-->\n\ntext"
    assert store.append_body_section(ref, "dossier-1", section) is True
    assert store.append_body_section(ref, "dossier-1", section) is False
    assert store.read_leads()[0].body.count("## Dossier") == 1


def test_merge_cluster_preserves_survivor_and_removes_losers(store_name, tmp_path, monkeypatch):
    """merge_cluster unions the audit trail onto a SEEDED survivor without touching its
    state, and reversibly removes the losers from the active set. Store-agnostic (#23)."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    # location must be a SECOND, token-disjoint LOCATIONS entry, not "" -- an empty location
    # is UNKNOWN evidence (same_opportunity), so upsert would MERGE this lead into the first
    # note instead of creating a second one, leaving nothing at url .../2 to test against.
    assert store.upsert(_lead(url="https://example.invalid/1", location=LOCATIONS[0],
                              first_seen="2026-07-10", last_seen="2026-07-10")) == "created"
    assert store.upsert(_lead(url="https://example.invalid/2", location=LOCATIONS[1],
                              first_seen="2026-07-05", last_seen="2026-07-20")) == "created"
    survivor = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/1")
    _enrich(store, survivor.ref)
    survivor = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/1")
    before = dict(survivor.fm)
    loser = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/2")
    store.merge_cluster(survivor.ref, [loser.ref], alt_urls=["https://example.invalid/2"],
                        first_seen="2026-07-05", last_seen="2026-07-20")
    after = next(iter(store.read_leads()))
    assert len(store.read_leads()) == 1                                  # loser removed
    changed = {k for k in before if before.get(k) != after.fm.get(k)} | (set(after.fm) - set(before))
    assert changed <= {"alt_urls", "first_seen", "last_seen"}, changed   # never-clobber


# ── status vocabulary ────────────────────────────────────────────────────────
def test_read_leads_filters_on_the_normalised_status(store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    store.update_fields(store.read_leads()[0].ref, {"status": "Dismissed"})  # historic drift

    assert len(store.read_leads({"dismiss"})) == 1
    assert store.read_leads({"new"}) == []
    assert store.read_leads()[0].status == "dismiss"


def test_unrecognised_status_is_passed_through_untouched(store_name, tmp_path, monkeypatch):
    """A genuinely new state must never be silently rewritten into a canonical one.
    Guessing is how a lead in a state the code does not know about gets quietly dragged
    back into a lifecycle it had already left."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    store.update_fields(store.read_leads()[0].ref, {"status": "escalated_to_human"})
    assert store.read_leads()[0].status == "escalated_to_human"


def test_normalize_all_statuses_reports_rather_than_guesses(store_name, tmp_path,
                                                            monkeypatch):
    """A store may canonicalise drift, but a note whose duplicate status lines DISAGREE
    must be REPORTED, never auto-resolved. A store that guesses can silently drag a
    shortlisted lead into `dismiss`, where it vanishes from triage with no error."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    # SEEDED with a genuine conflict. The previous version upserted one CLEAN lead and
    # asserted only that `conflicts` was a list -- with a `.get(..., [])` default, so a
    # store that auto-resolved conflicts and never reported one passed.
    seed(store_name, store, conflicted_status=("shortlist", "dismiss"))

    summary = store.normalize_all_statuses(dry_run=False)
    assert summary["conflicts"], \
        "a store must REPORT a status conflict for a human, never auto-resolve it: " \
        "guessing silently drags a shortlisted lead into dismiss, where it vanishes"

    # ...and the conflicted note is left alone, not rewritten to a guess.
    conflicted = [n for n in store.read_leads() if n.fm.get("company") == "Conflicted"]
    assert conflicted, "the conflicted note was destroyed rather than reported"


# ── identity: ref and slug ───────────────────────────────────────────────────
def test_ref_round_trips_through_every_write_method(store_name, tmp_path, monkeypatch):
    """`ref` is opaque: a caller only ever hands back what the store gave it."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref

    store.update_fields(ref, {"status": "shortlist"})
    store.append_body_section(ref, "x-1", "## X <!--x-1-->\n\ny")
    store.set_tailored_cv(ref, "CV_deadbeef.pdf (2026-07-14)")

    note = store.read_leads()[0]
    assert note.status == "shortlist"
    assert note.fm.get("tailored_cv", "").startswith("CV_deadbeef.pdf")


def test_slug_is_issued_stable_and_unique(store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead(company="Example Foundry", title="Analyst", url="https://example.invalid/1"))
    store.upsert(_lead(company="Example Analytics", title="Engineer", url="https://example.invalid/2"))

    slugs = [n.slug for n in store.read_leads()]
    assert len(set(slugs)) == 2 and all(slugs), "slugs must exist and be unique per lead"
    assert sorted(slugs) == sorted(n.slug for n in store.read_leads()), "slug must be stable"


# ── documents: the judge's and the gate's ground truth ───────────────────────
def test_read_baseline_takes_no_path_argument_and_reads_the_baseline(store_name, tmp_path,
                                                                     monkeypatch):
    """Where the baseline lives is the STORE's business.

    Pinned by signature AND by behaviour, because this is exactly where the first version
    of this refactor broke: it dropped the argument from `Vault.read_baseline`, left
    `cv/engine.py` still passing one, and shipped GREEN -- the test fake still carried the
    old signature and conformance did not cover the method. `sluice cv run` was dead on
    every lead, reporting `error` per lead through run_batch's per-lead swallow.
    """
    store = _make_store(store_name, tmp_path, monkeypatch)
    params = list(inspect.signature(store.read_baseline).parameters)
    assert params == [], f"read_baseline must take no arguments, got {params}"

    store.write_document("My CV/CV.md", "BASELINE TEXT")
    assert store.read_baseline() == "BASELINE TEXT"


def test_read_experience_entries_honours_verified_only(store_name, tmp_path, monkeypatch):
    """This is the FABRICATION GATE'S GROUND TRUTH.

    `validate()` checks every CV bullet against the bundle built from these entries. A
    store that ignores `verified_only` feeds unverified, agent-authored material into that
    bundle -- and then validate() finds every number properly cited in a "real" entry and
    returns NO violations. The gate does not fail; it SUCCEEDS, on fabricated evidence,
    and renders. It is the one path where opening the store seam can put an invented
    metric in front of an employer with the gate reporting green.
    """
    store = _make_store(store_name, tmp_path, monkeypatch)
    # SEEDED. The previous version of this test ran against an EMPTY library, so
    # `verified == []`, `every == []`, and `all([])` is True: it passed for a store that
    # ignored verified_only completely. Vacuous tests are how this hole stayed open
    # through two review passes.
    seed(store_name, store, experience=[
        {"id": "SF1", "verified": True, "body": "Cut costs 12%."},
        {"id": "SF2", "verified": False, "body": "Unverified draft. Cut costs 40%."},
    ])

    verified = store.read_experience_entries(verified_only=True)
    every = store.read_experience_entries(verified_only=False)

    assert len(every) == 2, "the seeder did not land; this test would pass vacuously"
    assert len(verified) == 1, \
        "verified_only=True returned the UNVERIFIED entry: the fabrication gate would " \
        "then validate an invented metric against agent-authored 'evidence' and RENDER it"
    assert all(e.get("verified") for e in verified)
    assert {e["title"] for e in verified} == {"SF1"}
    # The employer must survive the round trip. It did not: the seeder wrote an
    # `Employer:` key while `read_experience_entries` reads `Company:`, so every
    # entry came back with company="" and the seeder's employer argument was dead
    # on arrival. Nothing asserted it, so nothing noticed -- the same vacuity this
    # test's own docstring was written about. The bundle cites entries by company,
    # so a store that drops it feeds the fabrication gate anonymous evidence.
    assert {e["company"] for e in verified} == {"Example Foundry"}


def test_read_criteria_abstains_when_unset(store_name, tmp_path, monkeypatch):
    """The judge's criteria, on the critical path: a store that gets this wrong changes
    which jobs the user is shown. Unset must yield "" so the caller falls back to the
    shipped default, which states only that nothing is configured and declines to invent
    an opinion. A store that invents criteria here is the whole no-preferences property,
    broken."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.read_criteria() == "", "an unset criteria source must abstain, not guess"

    # ...and when it IS set, the store must actually return it. Asserting only the abstain
    # direction passes an amnesiac store that never reads the user's profile at all -- the
    # judge would then score every lead against the shipped neutral default, silently
    # ignoring everything the user wrote about what they want.
    seed(store_name, store, criteria="I want roles that do X. I refuse roles that do Y.")
    assert "I refuse roles that do Y" in store.read_criteria()


def test_write_document_round_trips(store_name, tmp_path, monkeypatch):
    # Asserting only a truthy handle passes a store that returns a plausible handle and
    # writes NOTHING. Read it back through the one reader the contract offers.
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.write_document("Job Applications/Rejected Leads Audit.md", "# Digest\n")
    store.write_document("My CV/CV.md", "ROUND TRIP")
    assert store.read_baseline() == "ROUND TRIP", "write_document returned a handle but wrote nothing"


def test_write_document_cannot_escape_the_store(store_name, tmp_path, monkeypatch):
    """The ONE wholesale-write primitive on a never-clobber contract must not be able to
    scribble outside the store -- including over the baseline CV, which is the fabrication
    gate's ground truth."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    for escape in ("/etc/passwd", "../escaped.md", "a/../../escaped.md"):
        with pytest.raises(ValueError):
            store.write_document(escape, "should never be written")


# ── empty store ──────────────────────────────────────────────────────────────
def test_reading_an_empty_store_is_not_an_error(store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.read_leads() == []


# ── #5: a note must never silently absorb a different job ─────────────────────
def test_two_jobs_differing_in_location_produce_two_notes(store_name, tmp_path, monkeypatch):
    """Two provably-different jobs (a proven location difference) must not collapse into one
    note. Stated in Store terms, on the slug SET, so a second store inherits the property."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(location=LOCATIONS[0], url="https://example.invalid/1")) == "created"
    assert store.upsert(_lead(location=LOCATIONS[1], url="https://example.invalid/2")) == "created"
    assert len({n.slug for n in store.read_leads()}) == 2, \
        "two provably-different jobs collapsed into one note"


def test_identical_strings_two_urls_produce_one_note(store_name, tmp_path, monkeypatch):
    """Same company+title+location, two urls -> one note (the accepted cross-board merge)."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(url="https://example.invalid/1")) == "created"
    assert store.upsert(_lead(url="https://example.invalid/2")) in ("updated", "merged")
    assert len({n.slug for n in store.read_leads()}) == 1


def test_two_url_less_leads_differing_in_location_produce_two_notes(store_name, tmp_path, monkeypatch):
    """An empty URL is never proof of sameness: two url-less leads sharing company+title but
    differing in location must split into two notes at the STORE, on LOCATION alone (not a
    url difference). End-to-end the engine's dedup_key collapses url-less leads first -- that
    read-key half is #23; this pins the store contract, which is what a 2nd store inherits."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(location=LOCATIONS[0], url="")) == "created"
    assert store.upsert(_lead(location=LOCATIONS[1], url="")) == "created"
    assert len({n.slug for n in store.read_leads()}) == 2, \
        "two url-less jobs differing in location collapsed into one note"


def test_upsert_return_is_always_within_the_vocabulary(store_name, tmp_path, monkeypatch):
    """EVERY upsert returns a MEMBER of the four-outcome vocabulary -- the assertion that stops
    an out-of-vocab outcome slipping past the sink's allowlist. Membership, not a fixed string;
    exercised across create AND the same-lead re-scrape (update/merge), not just the create path."""
    vocab = ("created", "updated", "merged", "refused")
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead()) in vocab                       # create
    assert store.upsert(_lead(last_seen="2026-07-14")) in vocab  # re-scrape -> update/merge


# ── #60: profile-audit sign-off (outcome verdict + never-clobber) ─────────────
def test_hold_for_signoff_stamps_only_when_no_tailored_cv(store_name, tmp_path, monkeypatch):
    """hold_for_signoff is a CONTRACT property: it stamps pending_cv/needs_signoff ONLY when
    no tailored_cv exists in FRESH content (returns True), and does NOTHING when a real
    pointer already exists (returns False) -- so a flagged re-tailor never latches a lead that
    already has a send-ready CV, and never clobbers that pointer. Whole-fm-dict assertions."""
    import json
    store = _make_store(store_name, tmp_path, monkeypatch)
    claims = json.dumps(["unsupported\tMotivated by placeholder\tNONE"])

    # No tailored_cv -> stamps, True.
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    assert store.hold_for_signoff(ref, pending="CV_ab12.pdf (2026-07-24)", claims=claims) is True
    fm = dict(store.read_leads()[0].fm)
    assert fm.get("pending_cv") == "CV_ab12.pdf (2026-07-24)" and "needs_signoff" in fm

    # A real tailored_cv present -> does nothing, False, pointer untouched.
    store.sign_off(ref)  # promote -> tailored_cv set, markers cleared
    base = dict(store.read_leads()[0].fm)
    assert store.hold_for_signoff(ref, pending="CV_STALE.pdf (2026-07-24)", claims=claims) is False
    assert dict(store.read_leads()[0].fm) == base   # nothing stamped, tailored_cv intact


def test_sign_off_reports_each_outcome_and_never_clobbers(store_name, tmp_path, monkeypatch):
    """sign_off's four outcomes are a CONTRACT property, like upsert's vocabulary: the
    store reports what it did on FRESH content (promoted|discarded|collision|nothing), so a
    caller never reconstructs the verdict from a stale snapshot. And every branch is
    surgical -- only pending_cv/needs_signoff (and, on promote, tailored_cv) change; an
    existing tailored_cv is NEVER clobbered. Asserted on the WHOLE frontmatter dict per
    branch, per this file's opening lesson."""
    import json
    store = _make_store(store_name, tmp_path, monkeypatch)

    def _pending(ref, pending):
        store.update_fields(ref, {"pending_cv": pending,
                                  "needs_signoff": json.dumps(["unsupported\tMotivated by placeholder\tNONE"])})

    # nothing: no pending_cv -> no write, no field change.
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    before = dict(store.read_leads()[0].fm)
    assert store.sign_off(ref) == "nothing"
    assert dict(store.read_leads()[0].fm) == before

    # promoted: pending_cv -> tailored_cv, markers cleared, EVERY other key identical.
    _pending(ref, "CV_ab12.pdf (2026-07-24)")
    base = dict(store.read_leads()[0].fm)
    assert store.sign_off(ref) == "promoted"
    fm = dict(store.read_leads()[0].fm)
    expected = {k: v for k, v in base.items() if k not in ("pending_cv", "needs_signoff")}
    expected["tailored_cv"] = "CV_ab12.pdf (2026-07-24)"
    assert fm == expected, "promote touched a key other than pending_cv/needs_signoff/tailored_cv"

    # discarded: markers cleared, tailored_cv NOT promoted (the earlier pointer stands).
    _pending(ref, "CV_disc.pdf (2026-07-24)")
    assert store.sign_off(ref, accept=False) == "discarded"
    fm = dict(store.read_leads()[0].fm)
    assert "pending_cv" not in fm and "needs_signoff" not in fm
    assert fm.get("tailored_cv") == "CV_ab12.pdf (2026-07-24)", "discard promoted a pending CV"

    # collision: a stale pending over an existing tailored_cv -> pointer kept UNCHANGED.
    _pending(ref, "CV_STALE.pdf (2026-07-24)")
    assert store.sign_off(ref) == "collision"
    fm = dict(store.read_leads()[0].fm)
    assert fm.get("tailored_cv") == "CV_ab12.pdf (2026-07-24)", "collision clobbered the real pointer"
    assert "pending_cv" not in fm and "needs_signoff" not in fm


# ── modify-write conflict (#16) ───────────────────────────────────────────────
def test_a_sustained_write_conflict_refuses_rather_than_clobbers(store_name, tmp_path, monkeypatch):
    """The conflict OUTCOME is a contract property (§2a of the #16 design): a modify-write
    that keeps losing the race must refuse loudly (raise VaultConflict for the field-writers,
    or return `refused` from upsert) and write nothing -- never a partial clobber. Skipped
    for stores whose write is not read-modify-write (they cannot exhibit the race).

    Assert the WHOLE note, not a sample -- this file's own opening lesson, re-learned here:
    the first version of this test asserted only `status`, which a store that refused the
    status write but still clobbered some OTHER field (company, url, or the body) would pass
    outright. The identifying keys and the attempted value get their own assertions too, so a
    fix that merely makes `status` survive is not enough to satisfy this test."""
    from sluice.core.protocols import VaultConflict
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    before = store.read_leads()[0].fm
    # Interpose the vault read so every capture sees a moved file (filesystem store only).
    import sluice.core.vault as vaultmod
    if getattr(store, "__class__", None).__module__ != vaultmod.__name__:
        pytest.skip("conflict simulation is filesystem-store specific")
    real = vaultmod._read
    n = {"i": 0}
    last = {"raw": None}
    def churn(path):
        text = real(path)
        if str(path) == str(ref):
            n["i"] += 1
            new = text + f"\nrace: {n['i']}"
            vaultmod._write(path, new)
            last["raw"] = new
        return text
    monkeypatch.setattr(vaultmod, "_read", churn)
    with pytest.raises(VaultConflict):
        store.update_fields(ref, {"status": "shortlist"})
    monkeypatch.setattr(vaultmod, "_read", real)
    # RAW bytes first: `churn` appends `\nrace: {n}` on every interposed read, so the parsed
    # frontmatter comparisons below (`after == before`) hold even though the raw file keeps
    # growing -- a body clobber by the refused write would be invisible to them. Prove
    # instead that the file on disk is EXACTLY the racer's last write: nothing from the
    # refused `update_fields` landed on top of it.
    assert real(ref) == last["raw"], "a refused write clobbered the racer's last content"
    after = store.read_leads()[0].fm
    assert after == before, \
        f"a refused write touched a field other than none: {sorted(k for k in set(after) | set(before) if after.get(k) != before.get(k))}"
    assert after.get("company") == before.get("company"), \
        "a refused write lost the identifying company key"
    assert after.get("url") == before.get("url"), "a refused write lost the identifying url key"
    assert "shortlist" not in after.values(), \
        "the attempted write left a trace even though it raised VaultConflict"
