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

# One definition, enforced in TWO places. test_upsert_return_is_always_within_the_vocabulary
# checks membership on a scenario that produces neither #81 outcome, so it cannot police an
# under-widening; test_merged_away_lead_is_never_recreated actually produces one and does.
_VOCAB = ("created", "updated", "merged", "refused", "merged_away", "merged_away_unproven")

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
    assert store.upsert(_lead(first_seen="2026-07-10", last_seen="2026-07-10")).outcome == "created"
    _enrich(store, store.read_leads()[0].ref)
    store.append_body_section(store.read_leads()[0].ref, "d-1",
                              "## Dossier <!--d-1-->\n\nbody")

    before = store.read_leads()[0]
    before_fm = dict(before.fm)

    # The lead comes back on a LATER scrape, as it will every day it stays posted.
    assert store.upsert(_lead(last_seen="2026-07-14")).outcome == "updated"

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
    assert store.upsert(_lead(last_seen="2026-07-14")).outcome == "created"
    # The board re-lists the role with a STALE date. Update or merge -- either way the
    # stamp must not move into the past.
    assert store.upsert(_lead(last_seen="2026-07-09")).outcome in ("updated", "merged")
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
                              first_seen="2026-07-10", last_seen="2026-07-10")).outcome == "created"
    assert store.upsert(_lead(url="https://example.invalid/2", location=LOCATIONS[1],
                              first_seen="2026-07-05", last_seen="2026-07-20")).outcome == "created"
    survivor = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/1")
    _enrich(store, survivor.ref)
    store.append_body_section(survivor.ref, "d-merge", "## body\nkeep\n")
    survivor = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/1")
    before = dict(survivor.fm)
    before_body = survivor.body
    loser = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/2")
    store.merge_cluster(survivor.ref, [loser.ref], alt_urls=["https://example.invalid/2"],
                        first_seen="2026-07-05", last_seen="2026-07-20")
    after = next(iter(store.read_leads()))
    assert len(store.read_leads()) == 1                                  # loser removed
    changed = {k for k in before if before.get(k) != after.fm.get(k)} | (set(after.fm) - set(before))
    assert changed <= {"alt_urls", "first_seen", "last_seen"}, changed   # never-clobber
    assert after.body == before_body                                     # never-clobber covers the BODY too


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


def test_slug_is_issued_stable_and_unique_across_what_the_store_creates(store_name, tmp_path,
                                                                       monkeypatch):
    """Uniqueness is BOUNDED, in the shape `upsert`/`merge_cluster` already use for
    merged-away: a store must never itself CREATE two notes at one slug. It cannot promise
    that no two ever ARRIVE at one -- the vault's slug is the note filename, and a human with
    a filesystem can seat that name in two directories once the scan is recursive (#1). That
    residual is the read-path warning's business and `index_by_slug`'s; see LeadNote.

    The previous version asserted the ABSOLUTE property from a fixture of two unrelated
    leads, which cannot collide whatever the store does -- so it certified an invariant this
    branch broke, vacuously. The seeds below drive the reconciliation arms instead: a
    straight re-scrape and a re-scrape whose location moved must both land back on the
    existing note (`created/updated/updated/created/created`, measured), and two further
    identities create.

    What this can and cannot falsify, stated because the honest bound is the point of the
    rewrite. It reddens if the store stops reconciling and mints a second note at an
    identity it already holds -- which is the bounded promise. It CANNOT redden on two notes
    arriving at one slug from different DIRECTORIES: the Store API offers no way to seat
    one (the vault's create arm writes to a single directory), that state comes from a human
    with a filesystem, and the contract disclaims it on purpose. So this is not a claim
    about "every arm that could mint a second note" -- there is no such arm reachable here.
    The residual has its own coverage: `Vault._resolve_path` refuses an ambiguous candidate
    (test_vault_subfolder_resolution.py), `read_leads` warns on it, and
    `tests/test_slug_indexing_discipline.py` sweeps `sluice/` for the consumers that would
    silently keep one twin.

    The COUNT and the per-seed OUTCOMES are what carry that, and neither is decoration.
    Uniqueness alone cannot fail: a store that stopped reconciling seats the duplicate
    identity at the NEXT name candidate, so it gets a DIFFERENT slug and every slug stays
    unique. `assert upsert(...) in _VOCAB` cannot fail either -- `created` is in the
    vocabulary. Measured on exactly that mutant (the reconcile short-circuit deleted from
    `Vault._resolve_candidates`): the outcomes went created/updated/updated/created/created
    to created/created/created/created/created and the note count 3 -> 5, with this node id
    GREEN throughout. So the store's own count of notes is asserted, and each seed that
    re-presents an identity the store already holds is pinned to a NON-create outcome.
    """
    store = _make_store(store_name, tmp_path, monkeypatch)
    seeds = [
        _lead(company="Example Foundry", title="Analyst", url="https://example.invalid/1"),
        _lead(company="Example Foundry", title="Analyst", url="https://example.invalid/1"),
        _lead(company="Example Foundry", title="Analyst", url="https://example.invalid/1",
              location=LOCATIONS[1]),
        _lead(company="Example Foundry", title="Engineer", url="https://example.invalid/2"),
        _lead(company="Example Analytics", title="Engineer", url="https://example.invalid/3"),
    ]
    outcomes = [store.upsert(lead).outcome for lead in seeds]
    assert all(o in _VOCAB for o in outcomes), outcomes

    # Seeds 0, 3 and 4 are three DISTINCT identities, so each must create. Seeds 1 and 2
    # re-present seed 0's identity (url-identical; seed 2 has merely moved location), so a
    # store that creates for either has minted a second note at an identity it already
    # holds -- the bounded promise, stated as the outcome rather than inferred from the
    # slugs, which a next-candidate create leaves unique.
    assert outcomes[0] == "created", f"a new identity must create, got {outcomes[0]}"
    assert outcomes[3] == "created", f"a new identity must create, got {outcomes[3]}"
    assert outcomes[4] == "created", f"a new identity must create, got {outcomes[4]}"
    assert outcomes[1] != "created", \
        "an identical re-scrape created a SECOND note at an identity the store already holds"
    assert outcomes[2] != "created", \
        "a re-scrape whose location moved created a SECOND note at a url-identical identity"

    slugs = [n.slug for n in store.read_leads()]
    # SCOPE first: a store that returned nothing would satisfy every assertion below, and
    # "no duplicates" is a negative property, so an empty result is its success case.
    assert len(slugs) >= 2, "the fixture must really have seated several notes"
    assert all(slugs), "every returned note must carry a slug"
    assert len(slugs) == 3, \
        f"five seeds carrying three identities must leave three notes, got {sorted(slugs)}"
    assert len(set(slugs)) == len(slugs), \
        f"the store created two notes at one slug: {sorted(slugs)}"
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


def test_write_document_only_if_absent_creates_then_abstains(store_name, tmp_path, monkeypatch):
    """On the CONTRACT, not on Vault. protocols.py's own docstring says never-clobber lives here
    precisely because 'a second store would ship without them', and #1 (the store seam) is the
    next backlog item -- so the second store is not hypothetical. `require_status`, the precedent
    this parameter follows, got three conformance rows.

    Asserted through read_criteria(), never a path: a store need not have one."""
    from sluice.core.protocols import CRITERIA_RELPATH
    store = _make_store(store_name, tmp_path, monkeypatch)
    handle = store.write_document(CRITERIA_RELPATH, "first", only_if_absent=True)
    # The contract states BOTH halves: "" on abstain, and a NON-EMPTY handle on a real write,
    # because callers distinguish the two by truthiness. A bare truthiness check here would
    # accept a store returning a non-str sentinel.
    assert isinstance(handle, str) and handle
    assert store.write_document(CRITERIA_RELPATH, "second", only_if_absent=True) == ""
    assert store.read_criteria() == "first"


def test_only_if_absent_lets_exactly_ONE_concurrent_caller_claim_the_create(
        store_name, tmp_path, monkeypatch):
    """`protocols.py` requires never-clobber be a property of the CREATE ITSELF -- an exclusive
    open -- and not an exists()-then-write pair. Nothing could falsify that.

    Measured: a store implementing `only_if_absent` as `if os.path.exists(...): return ""` followed
    by a plain write passes every SEQUENTIAL assertion here, including the row above. It is
    distinguishable only under concurrency, and only on this property -- the file CONTENTS do not
    separate them, because either way one writer's text ends up on disk.

    So the assertion is on how many callers claim the create. With O_CREAT|O_EXCL exactly one open
    succeeds; with a check-then-write both callers see an absent file and both report success, and
    the second silently overwrote the first. The racer is a human editing in Obsidian, who takes no
    lock (#16), and #1 lands the second store next -- so this needs to bind before that arrives.
    """
    import threading

    from sluice.core.protocols import CRITERIA_RELPATH

    # ROUNDS, not one pass. Measured: a single race caught a deliberately racy exists()-then-write
    # store only 89 times in 400, so a second implementer would have seen a green suite on ~78% of
    # runs while shipping a writer that clobbers the user's Judging Profile. Fifty rounds takes the
    # miss probability to effectively zero and still runs in well under a second.
    for round_no in range(50):
        store = _make_store(store_name, tmp_path / f"r{round_no}", monkeypatch)
        claimed, barrier = [], threading.Barrier(2)

        def create(i, _store=store, _claimed=claimed, _barrier=barrier):
            _barrier.wait()     # maximise the overlap rather than hoping for it
            _claimed.append(bool(_store.write_document(CRITERIA_RELPATH, f"writer-{i}",
                                                       only_if_absent=True)))

        threads = [threading.Thread(target=create, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(claimed) == 2, "precondition: both callers ran"
        assert sum(claimed) == 1, (
            f"round {round_no}: two callers both claimed to create the same document, so "
            "only_if_absent is an exists()-then-write pair rather than a property of the open -- "
            "the second write clobbered the first")
        assert store.read_criteria().startswith("writer-")


def test_the_default_arm_REPLACES_rather_than_abstaining(store_name, tmp_path, monkeypatch):
    """The other arm. Twelve lines of contract specified `only_if_absent=True` and nothing
    specified the default, and no row wrote the same key twice with it -- every existing row writes
    each key exactly once.

    The requirement is live: `triage/audit.py` regenerates the rejected-leads digest through this
    arm on every run. A store implementing create-exclusive as its primitive would freeze that
    digest at its first version, silently."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.write_document("My CV/CV.md", "FIRST")
    assert store.write_document("My CV/CV.md", "SECOND"), "the default arm returned no handle"
    assert store.read_baseline() == "SECOND", \
        "the default arm abstained instead of replacing; the digest would freeze at version one"


def test_write_document_cannot_escape_the_store(store_name, tmp_path, monkeypatch):
    """The ONE wholesale-write primitive on a never-clobber contract must not be able to
    scribble outside the store -- including over the baseline CV, which is the fabrication
    gate's ground truth."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    for escape in ("/etc/passwd", "../escaped.md", "a/../../escaped.md"):
        # BOTH write paths. `only_if_absent` takes a different branch inside the writer, and one
        # guard covering one branch is how a second implementer ends up with an escape on the arm
        # nobody parametrised.
        for only_if_absent in (False, True):
            with pytest.raises(ValueError):
                store.write_document(escape, "should never be written",
                                     only_if_absent=only_if_absent)


def test_write_document_accepts_interior_traversal_that_stays_inside(store_name, tmp_path,
                                                                     monkeypatch):
    """The PERMITTED half, which a rejection-only suite passes while disagreeing about the rule.

    The contract refuses a key whose RESOLVED path leaves the store -- not one that merely contains
    `..`. `Vault` enforces that with realpath + commonpath, so `a/../My CV/CV.md` is accepted and
    lands on the baseline. A second store implementing the cruder rule (reject any `..` component)
    satisfies every assertion in the escape test above and still diverges from `Vault` on this key,
    which is exactly the split #1's second store makes real -- and the split CodeRabbit found in the
    contract PROSE last round, where the written rule was stricter than the code.

    Both write paths, for the same reason the escape test parametrises them: the two branches
    resolve the key separately, so a guard on one says nothing about the other.
    """
    store = _make_store(store_name, tmp_path, monkeypatch)
    # Exclusive arm FIRST, while the baseline is absent -- that is the branch `sluice init` drives,
    # and once the document exists `only_if_absent` abstains and proves nothing about the key.
    assert store.write_document("a/../My CV/CV.md", "INTERIOR", only_if_absent=True), \
        "an interior `..` resolving inside the store was refused by the exclusive arm"
    assert store.read_baseline() == "INTERIOR", \
        "the accepted key did not resolve to the baseline document"
    assert store.write_document("a/../My CV/CV.md", "INTERIOR AGAIN"), \
        "the default arm refused an interior `..` the exclusive arm accepted"
    assert store.read_baseline() == "INTERIOR AGAIN"


# ── empty store ──────────────────────────────────────────────────────────────
def test_reading_an_empty_store_is_not_an_error(store_name, tmp_path, monkeypatch):
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.read_leads() == []


# ── #5: a note must never silently absorb a different job ─────────────────────
def test_two_jobs_differing_in_location_produce_two_notes(store_name, tmp_path, monkeypatch):
    """Two provably-different jobs (a proven location difference) must not collapse into one
    note. Stated in Store terms, on the slug SET, so a second store inherits the property."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(location=LOCATIONS[0], url="https://example.invalid/1")).outcome == "created"
    assert store.upsert(_lead(location=LOCATIONS[1], url="https://example.invalid/2")).outcome == "created"
    assert len({n.slug for n in store.read_leads()}) == 2, \
        "two provably-different jobs collapsed into one note"


def test_identical_strings_two_urls_produce_one_note(store_name, tmp_path, monkeypatch):
    """Same company+title+location, two urls -> one note (the accepted cross-board merge)."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(url="https://example.invalid/1")).outcome == "created"
    assert store.upsert(_lead(url="https://example.invalid/2")).outcome in ("updated", "merged")
    assert len({n.slug for n in store.read_leads()}) == 1


def test_two_url_less_leads_differing_in_location_produce_two_notes(store_name, tmp_path, monkeypatch):
    """An empty URL is never proof of sameness: two url-less leads sharing company+title but
    differing in location must split into two notes at the STORE, on LOCATION alone (not a
    url difference). End-to-end the engine's dedup_key collapses url-less leads first -- that
    read-key half is #23; this pins the store contract, which is what a 2nd store inherits."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(location=LOCATIONS[0], url="")).outcome == "created"
    assert store.upsert(_lead(location=LOCATIONS[1], url="")).outcome == "created"
    assert len({n.slug for n in store.read_leads()}) == 2, \
        "two url-less jobs differing in location collapsed into one note"


def test_upsert_return_is_always_within_the_vocabulary(store_name, tmp_path, monkeypatch):
    """EVERY upsert returns a MEMBER of the six-outcome vocabulary -- the assertion that stops
    an out-of-vocab outcome slipping past the sink's allowlist. Membership, not a fixed string;
    exercised across create AND the same-lead re-scrape (update/merge), not just the create path."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead()).outcome in _VOCAB                       # create
    assert store.upsert(_lead(last_seen="2026-07-14")).outcome in _VOCAB  # re-scrape -> update/merge


# ── #81: a lead merged away must never be resurrected ─────────────────────────
def test_merged_away_lead_is_never_recreated(store_name, tmp_path, monkeypatch):
    """#81, a SAFETY property in the never-clobber family: a lead merged away via
    merge_cluster is never re-created by upsert. A store that archives losers and then
    creates freely resurrects them, so it must be stated -- a synthetic-id store does NOT
    get this for free.

    SCOPE assertions first: a test that merges nothing would satisfy the property
    trivially. Same shape as test_merge_cluster_preserves_survivor_and_removes_losers --
    two token-disjoint LOCATIONS, no filenames in the test's vocabulary."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    assert store.upsert(_lead(url="https://example.invalid/1", location=LOCATIONS[0])).outcome == "created"
    assert store.upsert(_lead(url="https://example.invalid/2", location=LOCATIONS[1])).outcome == "created"
    survivor = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/1")
    loser = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/2")
    store.merge_cluster(survivor.ref, [loser.ref], alt_urls=["https://example.invalid/2"],
                        first_seen="2026-07-05", last_seen="2026-07-20")
    # SCOPE: the merge actually happened and the loser actually left the active view.
    assert len(store.read_leads()) == 1, "nothing was merged: the property below is vacuous"
    assert all(n.fm.get("url") != "https://example.invalid/2" for n in store.read_leads())

    # THE PROPERTY: the merged-away lead, re-scraped with the dedup set empty.
    result = store.upsert(_lead(url="https://example.invalid/2", location=LOCATIONS[1]))
    assert result.outcome != "created", f"{store_name} re-created a lead a human merged away"
    assert result.outcome in _VOCAB
    assert len(store.read_leads()) == 1


# ── #131 post-final-review: upsert reports the note it ACTUALLY wrote to ──────
def test_upsert_result_slug_names_the_note_this_call_actually_wrote(
        store_name, tmp_path, monkeypatch):
    """The concrete regression this task exists to prevent (#131 final review):
    two notes legitimately share company+title (a proven-different location seats
    a second note at that identity) -- a THIRD call whose url proves it the SAME
    posting as the FIRST note, but whose location matches the SECOND note, must
    report the FIRST note's slug, not the second's. No post-hoc filter over the
    finished note set can get this right in general; only the store's own
    resolution, which this test proves by checking the note ACTUALLY ON DISK
    matches what `result.slug` claims -- not merely that `first.slug`/`second.slug`
    are the strings the FIRST two calls happened to return (a stub that always
    echoed the SECOND note's slug back for every write would pass a check that
    stopped at that), but that `last_seen` genuinely moved on the note
    `third.slug` names and genuinely did NOT move on the other one (#131
    round-2 review, Important #1: an earlier version of this test asserted
    only `fm.get("url")`, which `updated`/`merged` never write, so it could
    not tell a real write from a completely inert stub)."""
    store = _make_store(store_name, tmp_path, monkeypatch)

    # Explicit, DISTINCT last_seen stamps on the first two creates -- not the
    # `_lead()` default (today's date via Lead.__post_init__) -- so the third
    # call's own stamp can only land on ONE of them without both already
    # coinciding, which would make "did last_seen move" undecidable.
    first = store.upsert(_lead(company="Example Ltd", title="Example Role",
                               url="https://example.invalid/1", location=LOCATIONS[0],
                               first_seen="2026-01-01", last_seen="2026-01-01"))
    assert first.outcome == "created" and first.slug

    second = store.upsert(_lead(company="Example Ltd", title="Example Role",
                                url="https://example.invalid/2", location=LOCATIONS[1],
                                first_seen="2026-01-02", last_seen="2026-01-02"))
    assert second.outcome == "created" and second.slug
    assert second.slug != first.slug

    # The third call's url proves it the SAME posting as the FIRST note (url match
    # is definitive), even though its own incoming location (LOCATIONS[1]) coincides
    # with the SECOND, unrelated note's location -- the original reproduction that
    # broke TWO of the three prior "guess after the fact" strategies (location-only,
    # and a flat same_opportunity filter over the whole candidate set). The THIRD
    # strategy (a two-tier url-then-location priority, #131's immediately-preceding
    # commit) gets THIS scenario right too, because the third call's url matches
    # exactly ONE note's url here -- see
    # test_upsert_result_slug_is_not_fooled_by_an_unrelated_notes_matching_url below
    # for the scenario that distinguishes the real fix from that third strategy.
    third = store.upsert(_lead(company="Example Ltd", title="Example Role",
                               url="https://example.invalid/1", location=LOCATIONS[1],
                               last_seen="2026-01-15"))
    assert third.outcome in ("updated", "merged")
    assert third.slug == first.slug, (
        f"the url-proven write touched the FIRST note but reported "
        f"{third.slug!r} instead of {first.slug!r}")

    # Ground truth: `last_seen` is the ONLY field `updated`/`merged` may ever
    # change (never-clobber), so it is the one signal that proves a write really
    # landed where `result.slug` claims. The FIRST note's last_seen must have
    # advanced to the third call's own stamp; the SECOND note's must NOT have
    # moved off its own original stamp -- confirming the write landed on the note
    # `third.slug` names and nowhere else, not merely that the string looks right.
    notes = {n.slug: n for n in store.read_leads()}
    assert notes[first.slug].fm.get("last_seen") == "2026-01-15", (
        "the FIRST note's last_seen was not actually bumped by the third call "
        "-- third.slug named a note this write never touched")
    assert notes[second.slug].fm.get("last_seen") == "2026-01-02", (
        "the SECOND note's last_seen moved even though the third call's write "
        "was reported as landing on the FIRST note -- never-clobber violated")
    assert notes[first.slug].fm.get("url") == "https://example.invalid/1"
    assert notes[second.slug].fm.get("url") == "https://example.invalid/2"


def test_upsert_result_slug_is_not_fooled_by_an_unrelated_notes_matching_url(
        store_name, tmp_path, monkeypatch):
    """#131 round-2 review, Important #3: the scenario above does NOT distinguish
    the real fix from #131's immediately-preceding commit (a two-tier url-then-
    location priority applied to a post-hoc `read_leads()` filter) -- that strategy
    happens to get it right too, because the incoming url there matches exactly ONE
    note. This scenario is the one that actually tells them apart, reimplemented and
    verified against the real two-tier code before writing this test: swap WHICH
    field matches which note. The incoming lead's url matches the SECOND note, but
    its location coincidentally matches the FIRST note, which is seated at the BARE
    candidate name the real candidate walk checks FIRST -- so the real write lands on
    the FIRST note via a location-only verdict, WITHOUT the walk ever reaching the
    SECOND note (or its url) at all. A url-then-location priority computed
    afterward over the finished note set, in contrast, finds the SECOND note's url a
    match FIRST and never even considers location, since exactly one url match ends
    its search -- reporting the SECOND note's slug for a write that actually landed
    on the FIRST."""
    store = _make_store(store_name, tmp_path, monkeypatch)

    first = store.upsert(_lead(company="Example Ltd", title="Example Role",
                               url="https://example.invalid/1", location=LOCATIONS[0],
                               first_seen="2026-01-01", last_seen="2026-01-01"))
    assert first.outcome == "created" and first.slug

    second = store.upsert(_lead(company="Example Ltd", title="Example Role",
                                url="https://example.invalid/2", location=LOCATIONS[1],
                                first_seen="2026-01-02", last_seen="2026-01-02"))
    assert second.outcome == "created" and second.slug
    assert second.slug != first.slug

    # The SECOND note's own url, but the FIRST note's own location: the bare
    # candidate name (the FIRST note) is checked before any location-suffixed one,
    # and a url MISMATCH there does not block a location MATCH from resolving the
    # walk right there (same_opportunity's real, pre-existing, documented rule: a
    # non-matching url is not proof of DIFFERENT, so location is still consulted).
    third = store.upsert(_lead(company="Example Ltd", title="Example Role",
                               url="https://example.invalid/2", location=LOCATIONS[0],
                               last_seen="2026-01-15"))
    assert third.outcome in ("updated", "merged")
    assert third.slug == first.slug, (
        f"the write actually landed on the FIRST note (matched by location on the "
        f"bare candidate) but result.slug reported {third.slug!r} -- a url-then-"
        f"location strategy applied after the fact would report the SECOND note's "
        f"slug {second.slug!r} here, which is wrong")

    # Ground truth, same discipline as the sibling test above: last_seen is the
    # only observable effect of update/merge, so it is what proves the write
    # landed where result.slug claims.
    notes = {n.slug: n for n in store.read_leads()}
    assert notes[first.slug].fm.get("last_seen") == "2026-01-15", (
        "the FIRST note's last_seen was not actually bumped by the third call")
    assert notes[second.slug].fm.get("last_seen") == "2026-01-02", (
        "the SECOND note's last_seen moved even though the write was reported as "
        "landing on the FIRST note")


def test_upsert_result_slug_is_blank_for_every_no_write_outcome(store_name, tmp_path, monkeypatch):
    """refused/merged_away/merged_away_unproven never carry a slug -- confirms the
    negative half of the contract, not just the positive one above."""
    store = _make_store(store_name, tmp_path, monkeypatch)

    refused = store.upsert(_lead(company="", title="", url=""))
    assert refused.outcome == "refused"
    assert refused.slug == ""

    # merged_away / merged_away_unproven: same setup as
    # test_merged_away_lead_is_never_recreated -- merge a loser away, then re-scrape
    # the identity that was merged. Either outcome is a valid store response (they
    # differ only in evidence strength), and NEITHER may carry a slug: the archived
    # note is not one this call wrote into, only matched against.
    survivor = store.upsert(_lead(url="https://example.invalid/3", location=LOCATIONS[0]))
    loser = store.upsert(_lead(url="https://example.invalid/4", location=LOCATIONS[1]))
    assert survivor.outcome == "created" and loser.outcome == "created"
    survivor_note = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/3")
    loser_note = next(n for n in store.read_leads() if n.fm.get("url") == "https://example.invalid/4")
    store.merge_cluster(survivor_note.ref, [loser_note.ref],
                        alt_urls=["https://example.invalid/4"],
                        first_seen="2026-07-05", last_seen="2026-07-20")

    archived = store.upsert(_lead(url="https://example.invalid/4", location=LOCATIONS[1]))
    assert archived.outcome in ("merged_away", "merged_away_unproven")
    assert archived.slug == ""


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


def test_sign_off_require_pending_refuses_a_stale_confirmation_at_the_cas_layer(
        store_name, tmp_path, monkeypatch):
    """#131 decision 13: tested DIRECTLY at the Vault.sign_off layer, with NO
    confirm-token layer anywhere in this call path -- the outer confirm-token
    comparison (cv_signoff's own two-call flow) already catches a re-hold interleaved
    BETWEEN two MCP calls; only a direct call here exercises require_pending's OWN
    CAS-level guard, which would otherwise go completely unwitnessed by the described
    test suite."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    from sluice.core.leads import Lead
    lead = Lead(source="s", search="q", title="Example Role", company="Example Ltd",
               url="https://example.invalid/1")
    assert store.upsert(lead).outcome == "created"
    note = store.read_leads()[0]
    store.hold_for_signoff(note.ref, pending="CV_deadbeef.pdf (2026-08-14)",
                           claims='["unsupported claim"]')

    outcome = store.sign_off(note.ref, accept=True,
                             require_pending="CV_deadbeef.pdf (STALE-DOES-NOT-MATCH)")
    assert outcome == "stale"
    fresh = store.read_leads()[0]
    assert fresh.fm.get("pending_cv", "") == "CV_deadbeef.pdf (2026-08-14)"   # untouched
    assert "tailored_cv" not in fresh.fm


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


def test_update_fields_require_status_abstains_on_a_fresh_mismatch(store_name, tmp_path,
                                                                   monkeypatch):
    """#9 never-regress. The status check must happen against the FRESH stored note,
    inside the write -- not against whatever the caller enumerated.

    A caller-side check on the in-memory LeadNote is byte-identical to NO check. Probed
    against a real vault: deleting the guard and running it with
    `is_application_owned(note.status)` produce the same bytes, because the snapshot is
    stale by construction. `leads expire`'s read loop is a window in which a lead can
    enter the application lifecycle via `apply record` or a #10 receipt, and the caller
    cannot see that happen. So the store owns it, and it is on the contract because a
    second store that skipped it would silently overwrite `applied`.
    """
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    store.update_fields(ref, {"status": "applied"})

    wrote = store.update_fields(ref, {"status": "dismiss"},
                                require_status=frozenset({"new", "shortlist"}))

    assert wrote is False, "a fresh-status mismatch must write nothing"
    assert store.read_leads()[0].status == "applied", \
        "require_status must not let a triage write land on an application-owned lead"


def test_update_fields_require_status_writes_on_a_fresh_match(store_name, tmp_path,
                                                              monkeypatch):
    """The other half: a matching fresh status writes normally and reports True."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    before = store.read_leads()[0].status

    wrote = store.update_fields(ref, {"status": "dismiss"},
                                require_status=frozenset({before}))

    assert wrote is True
    assert store.read_leads()[0].status == "dismiss"


def test_update_fields_require_status_compares_the_NORMALIZED_status(store_name, tmp_path,
                                                                     monkeypatch):
    """Drifted vocabulary must still match. `core/status.py:22` exists because real vaults
    carry `dismissed`/`Researching`/`needs review`; a store comparing raw strings would
    abstain on every one of them -- forever reporting the lead stale and never writing it,
    with no error. Both other require_status cases use canonical values only, so nothing
    else here can catch that."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    store.update_fields(ref, {"status": "Shortlist"})     # drifted casing, on purpose

    wrote = store.update_fields(ref, {"status": "dismiss"},
                                require_status=frozenset({"shortlist"}))

    assert wrote is True, "require_status must compare the NORMALIZED status"
    assert store.read_leads()[0].status == "dismiss"


def test_update_fields_require_blank_abstains_when_the_field_is_already_set(store_name, tmp_path,
                                                                           monkeypatch):
    """#109 never-clobber. Same argument as require_status directly above, for a NON-status
    field: the caller decides "this field is blank, so filling it in is safe" from a
    snapshot, then spends seconds on a tier-2 page fetch before writing. A human editing
    the note in Obsidian inside that window is exactly who never-clobber protects, and a
    caller-side blankness check cannot see them. So the store owns it.

    The refusal is on PRESENCE, not on inequality: it must refuse a value that DIFFERS
    from the one being written, which is what separates this from the benign
    already-current no-op `update_fields` already reports as False.
    """
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    store.update_fields(ref, {"company": '"Human Typed Co"'})

    wrote = store.update_fields(ref, {"company": '"Scraped Co"'},
                                require_blank=frozenset({"company"}))

    assert wrote is False, "a field already carrying a value must not be overwritten"
    assert store.read_leads()[0].fm["company"] == "Human Typed Co", \
        "require_blank must not let a scraped value land on a human's own edit"


def test_update_fields_require_blank_writes_when_the_field_is_blank(store_name, tmp_path,
                                                                    monkeypatch):
    """The other half: a genuinely blank field is filled in normally and reports True.
    A guard that refused everything would be indistinguishable from the feature being
    dead, and the abstain test above cannot tell those apart."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    store.update_fields(ref, {"company": '""'})

    wrote = store.update_fields(ref, {"company": '"Scraped Co"'},
                                require_blank=frozenset({"company"}))

    assert wrote is True
    assert store.read_leads()[0].fm["company"] == "Scraped Co"


def test_update_fields_reports_False_when_the_record_does_not_change(store_name, tmp_path,
                                                                    monkeypatch):
    """The bool reports whether the stored record CHANGED, not whether the guard passed.
    A caller distinguishing 'written' from 'skipped' by this value needs that pinned."""
    store = _make_store(store_name, tmp_path, monkeypatch)
    store.upsert(_lead())
    ref = store.read_leads()[0].ref
    current = store.read_leads()[0].status

    assert store.update_fields(ref, {"status": current},
                               require_status=frozenset({current})) is False
