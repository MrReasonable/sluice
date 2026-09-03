"""#205: a lead's identity must not be case-variant. Boards render one employer several
ways ("Example Co", "EXAMPLE CO", "example co"), the note name is built from the company
string VERBATIM, and `_locate` probes a CONSTRUCTED path -- so on a case-sensitive
filesystem each spelling seats its own note, with its own status. In the reported store
one spelling held a live shortlist at score 86 while its twin held a dismissal, and the
pair also wedged Syncthing on the case-insensitive machine, which had never received a
version of either note.

WHY THESE TESTS GATE THEMSELVES ON THE FILESYSTEM. On a case-INSENSITIVE filesystem this
defect does not exist: `_locate`'s `os.path.isfile("EXAMPLE CO - X.md")` answers True for
a note seated at "Example Co - X.md", the walk finds it, and the second scrape UPDATES.
Measured on both, against the shipped code:

    case-insensitive (macOS APFS default)  ->  created, updated  -> 1 note
    case-sensitive   (Linux, and CI)       ->  created, created  -> 2 notes

So a test asserting "one note results" is RED on CI and vacuously GREEN on a developer's
Mac -- the repo's own "a guard that discovers nothing passes" shape, one rung out from the
code. It is gated on a MEASURED probe of the actual leads dir rather than on `sys.platform`
(a Mac can mount a case-sensitive volume, and a Linux CI runner could in principle not be
one), and it SKIPS with the reason named rather than passing quietly, so the local reader
is told the guard did not run instead of being shown a green tick that certifies nothing.
"""
import os

import pytest

from sluice.core.leads import Lead
from sluice.core.vault import Vault
from tests.conftest import LOCATIONS


def _lead(company, **kw):
    # `ex-board`, not a shipped adapter's name: `source` is persisted into the note's
    # frontmatter (`Vault._render`), and nothing here asserts adapter identity, so naming a
    # real board writes a claim the test never makes. `ex-board` is this suite's dominant
    # synthetic id already, which is why it is used in preference to inventing a new one.
    base = dict(source="ex-board", search="Engineering Manager", title="Engineering Manager",
                company=company, url="https://ex.invalid/1", location=LOCATIONS[0],
                salary="", job_type="permanent",
                first_seen="2026-07-07", last_seen="2026-07-07")
    base.update(kw)
    return Lead(**base)


def _notes(vault):
    """Every ACTIVE note name under leads_dir, as a sorted list of basenames without .md.

    `_merged/` is excluded, and not as a tidy-up: an archived loser keeps the name it was
    seated at, so a walk that includes it reports the archive itself as a case-variant of
    the re-scrape that was correctly suppressed -- which reads exactly like the resurrection
    these rows exist to catch. Excluding it is the same prune `read_leads` applies (by name,
    `_PRIVATE_SUBDIRS`), for the same reason."""
    out = []
    for dirpath, dirnames, filenames in os.walk(vault.leads_dir):
        dirnames[:] = [d for d in dirnames if d != "_merged"]
        out.extend(n[:-3] for n in filenames if n.endswith(".md"))
    return sorted(out)


def _require_case_sensitive_fs(tmp_path):
    """Skip unless the filesystem under `tmp_path` distinguishes case. PROBED, never
    inferred from the platform: this test's whole subject is what the filesystem does with
    two names differing only in case, so asking it directly is the only answer that cannot
    be wrong. The probe writes into a dedicated subdirectory so it cannot collide with a
    vault the caller has already built.

    TWO different reasons bring callers here, and neither is a shortcoming of the test.
    Some rows would pass without exercising anything -- `_locate`'s stat already resolves
    the variant, so the defect does not exist to be caught. The rest cannot even build
    their FIXTURE: a pre-existing collided pair is two files whose names differ only by
    case, which a case-insensitive filesystem cannot hold at all (the second write lands
    on the first). Both are honest skips rather than green ticks.

    The FS-independent rows -- the `_merged/` archive probe, and the fold-sharing check --
    deliberately do NOT call this: their comparisons are Python string equality, not
    filesystem lookups, so they run everywhere and are what a developer on a Mac still
    gets."""
    probe = tmp_path / "_case_probe"
    probe.mkdir(exist_ok=True)
    (probe / "CaseProbe").write_text("")
    collides = (probe / "caseprobe").exists()
    for p in probe.iterdir():
        p.unlink()
    probe.rmdir()
    if collides:
        pytest.skip(
            "needs a case-sensitive filesystem: on a case-insensitive one (macOS APFS by "
            "default) either _locate's stat already finds the case-variant note (so #205 "
            "cannot be reproduced) or the two-spelling fixture cannot be seated at all. "
            "CI (ubuntu-latest) is case-sensitive and does run these."
        )


def test_a_re_scrape_under_different_company_casing_updates_rather_than_duplicates(tmp_path):
    """The defect, through the real write path: one role, two boards, one employer spelled
    two ways. `upsert` must reconcile them onto ONE note -- a second note is a second
    identity, and the two then hold divergent status, so a dismissal recorded under one
    spelling does not stop the role returning as `new` under the other."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))

    first = v.upsert(_lead("Example Co"))
    second = v.upsert(_lead("EXAMPLE CO", url="https://ex.invalid/2"))

    assert first.outcome == "created"
    assert second.outcome != "created", (
        f"a case-variant company minted a second note: {_notes(v)}")
    assert len(_notes(v)) == 1, f"case-variant duplicate: {_notes(v)}"


def test_lowercase_and_mixed_case_company_are_one_identity(tmp_path):
    """The second reported pair -- an all-lowercase board spelling against a mixed-case one.
    Kept separate from the all-caps pair above because the two are NOT equivalent under
    every candidate fix: an acronym-safe title-caser converges this pair and leaves the
    all-caps pair apart (measured, 2026-09-03), so a fix that only passes this one has not
    closed #205."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))

    v.upsert(_lead("Example Co", title="Head of Data & AI", search="Head of Data & AI"))
    second = v.upsert(_lead("example co", title="head of data & ai",
                            search="head of data & ai", url="https://ex.invalid/2"))

    assert second.outcome != "created", (
        f"a case-variant company minted a second note: {_notes(v)}")
    assert len(_notes(v)) == 1, f"case-variant duplicate: {_notes(v)}"


def test_a_note_already_seated_at_a_variant_casing_is_found_not_duplicated(tmp_path):
    """The MIGRATION direction, and the one a name-canonicalising fix gets wrong on its own.
    Every store predating the fix holds notes at board-verbatim names. If a fix canonicalises
    the name it derives but leaves resolution case-sensitive, the very first re-scrape of an
    existing note derives a name the walk cannot find and CREATES the duplicate the fix was
    written to prevent -- so the store is worse, not better, and only after upgrading."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))

    # Seat the note the way a pre-fix store holds it: board-verbatim, shouty.
    seeded = v.upsert(_lead("EXAMPLE CO"))
    assert seeded.outcome == "created"
    before = _notes(v)
    assert len(before) == 1, before

    again = v.upsert(_lead("Example Co", url="https://ex.invalid/2"))

    assert again.outcome != "created", (
        f"re-scraping an existing note under a different casing duplicated it: {_notes(v)}")
    assert len(_notes(v)) == 1, f"case-variant duplicate on re-scrape: {_notes(v)}"


# ── the archive probe (#81) ───────────────────────────────────────────────────
def _merge_away(v, loser, survivor):
    """Archive `loser`'s note through the REAL merge_cluster, so the fixture cannot drift
    from what the production archive path writes."""
    assert v.upsert(survivor).outcome == "created"
    assert v.upsert(loser).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    s, lo = notes[survivor.url], notes[loser.url]
    v.merge_cluster(s.ref, [lo.ref], alt_urls=[loser.url],
                    first_seen="2026-07-01", last_seen="2026-07-07")
    return lo


@pytest.mark.parametrize("company,title", [
    ("EXAMPLE CO", "ENGINEERING MANAGER"),
    ("example co", "engineering manager"),
])
def test_a_case_variant_rescrape_does_not_resurrect_a_merged_away_lead(tmp_path, company, title):
    """The more serious half of #205, and one the issue does not report. Measured on shipped
    code, this returned `created`: the #81 guard was working -- the exact-casing control below
    suppresses -- and the re-scrape simply walked past it, because `_archived_match` compared
    the seated name byte-for-byte. A wrong create here undoes a human's merge decision, and
    where the surviving twin was already `applied` it means a second application under the
    user's name.

    Does NOT need a case-sensitive filesystem: the comparison this pins is `_archived_match`'s
    own string equality against the name `merge_cluster` recorded, which is Python, not the
    filesystem. The archived note is not at a colliding path -- it is under `_merged/` and the
    re-scrape's candidate is an ACTIVE name -- so nothing here depends on what the filesystem
    does with two spellings."""
    v = Vault(str(tmp_path / "vault"))
    loser = _merge_away(v,
                        loser=_lead("Example Co", url="https://ex.invalid/2"),
                        survivor=_lead("Example Co", title="Engineering Manager II",
                                       search="Engineering Manager II",
                                       url="https://ex.invalid/1"))
    assert "Example Co" in loser.slug

    again = v.upsert(_lead(company, title=title, search=title, url="https://ex.invalid/2"))

    assert again.outcome == "merged_away", (
        f"a case-variant re-scrape resurrected a merged-away lead: {_notes(v)}")
    assert not any(n.casefold() == f"{company} - {title}".casefold() for n in _notes(v))


def test_a_folded_archive_match_without_url_proof_is_not_recorded(tmp_path):
    """The fold widens WHICH archived entries a candidate matches, so it must not widen what
    enters `seen.db` -- that store has no removal path, and a permanently suppressed lead with
    no note anywhere is unrecoverable. It does not: the recorded arm is gated on `url_proven`
    (a matching non-empty url), which no amount of name folding can manufacture. A same
    company/title/location RE-POST carrying a brand-new url is a real job, so it lands on the
    UNPROVEN arm, writes nothing, records nothing, and re-reports every run until a human acts.

    This is the guard that makes widening the comparison safe, so it is asserted rather than
    argued: deleting `and url_proven` in `_archived_match` turns this row red."""
    v = Vault(str(tmp_path / "vault"))
    _merge_away(v,
                loser=_lead("Example Co", url="https://ex.invalid/2"),
                survivor=_lead("Example Co", title="Engineering Manager II",
                               search="Engineering Manager II",
                               url="https://ex.invalid/1"))

    # Same identity up to case, but a BRAND-NEW url: a re-post, not the archived posting.
    again = v.upsert(_lead("EXAMPLE CO", title="ENGINEERING MANAGER",
                           search="ENGINEERING MANAGER", url="https://ex.invalid/999"))

    assert again.outcome == "merged_away_unproven"


# ── the report on pairs a pre-fix store already holds ─────────────────────────
def _seat(v, name, *, company, status, score):
    """Hand-seat a note at an exact filename, the way a pre-fix store holds one."""
    os.makedirs(v.leads_dir, exist_ok=True)
    path = os.path.join(v.leads_dir, f"{name}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"---\ncompany: {company}\nrole: Engineering Manager\nstatus: {status}\n"
                f"score: {score}\nurl: https://ex.invalid/{score}\n"
                f"location: {LOCATIONS[0]}\n---\n\nbody\n")
    return path


def test_read_leads_reports_a_pair_that_differs_only_by_capitalisation(tmp_path, caplog):
    """A store that predates this fix already holds pairs -- that is what wedged replication.
    `_locate` probes the exact name first, so `upsert` keeps updating whichever twin the scrape
    names and says nothing; the read path walks anyway, so the report costs one grouping over a
    list that already exists. The message must name the REMEDY, because `leads dedupe` already
    clusters such a pair (`_norm_tokens` casefolds) and a user told only that something is wrong
    has to invent a repair that already ships."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))
    _seat(v, "Example Co - Engineering Manager", company="Example Co",
          status="shortlist", score=86)
    _seat(v, "EXAMPLE CO - Engineering Manager", company="EXAMPLE CO",
          status="dismiss", score=0)

    with caplog.at_level("WARNING"):
        notes = v.read_leads()

    assert len(notes) == 2, "both twins are still returned; this is a report, not a filter"
    msgs = [r.getMessage() for r in caplog.records]
    hits = [m for m in msgs if "differ only by capitalisation" in m]
    assert len(hits) == 1, msgs
    assert "leads dedupe" in hits[0], "the report must name the remedy that already ships"
    assert "Example Co - Engineering Manager" in hits[0]
    assert "EXAMPLE CO - Engineering Manager" in hits[0]


def test_the_capitalisation_report_is_not_raised_for_notes_at_one_name(tmp_path, caplog):
    """Two notes at ONE name (same basename, different subfolders) is the OTHER collision, and
    it already has its own message with a different consequence -- consumers keyed on slug see
    only one of them. Saying both things about one fact in two vocabularies teaches a reader to
    skip both, so the case sweep reports a fold group only when it holds more than one DISTINCT
    slug."""
    v = Vault(str(tmp_path / "vault"))
    os.makedirs(os.path.join(v.leads_dir, "Active"), exist_ok=True)
    _seat(v, "Example Co - Engineering Manager", company="Example Co",
          status="shortlist", score=86)
    dup = os.path.join(v.leads_dir, "Active", "Example Co - Engineering Manager.md")
    with open(dup, "w", encoding="utf-8") as f:
        f.write(f"---\ncompany: Example Co\nrole: Engineering Manager\nstatus: new\n"
                f"score: 0\nurl: https://ex.invalid/0\nlocation: {LOCATIONS[0]}\n---\n\nbody\n")

    with caplog.at_level("WARNING"):
        v.read_leads()

    msgs = [r.getMessage() for r in caplog.records]
    assert any("is claimed by" in m for m in msgs), msgs
    assert not [m for m in msgs if "differ only by capitalisation" in m], msgs


def test_the_capitalisation_report_is_raised_once_per_store(tmp_path, caplog):
    """Every command that reads leads walks this path, and several read twice. An unsuppressed
    report would say the same unchanged fact on each pass, which is the noise the sibling
    warning is already deduped against."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))
    _seat(v, "Example Co - Engineering Manager", company="Example Co",
          status="shortlist", score=86)
    _seat(v, "EXAMPLE CO - Engineering Manager", company="EXAMPLE CO",
          status="dismiss", score=0)

    with caplog.at_level("WARNING"):
        v.read_leads()
        v.read_leads()

    hits = [r for r in caplog.records if "differ only by capitalisation" in r.getMessage()]
    assert len(hits) == 1


# ── the fast path, and the one fold ───────────────────────────────────────────
def test_an_exact_name_wins_over_a_case_variant_on_disk(tmp_path):
    """`_locate` probes the exact name FIRST and folds only on a miss, which is what keeps the
    steady-state lookup at its previous cost (~7us against ~1.9ms for the folded listing over a
    3190-note store). Pinned as BEHAVIOUR rather than as a timing: with both spellings on disk,
    a lookup for one of them returns THAT one alone -- never the pair, which would refuse, and
    never the other, which would write to the wrong twin."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))
    exact = _seat(v, "Example Co - Engineering Manager", company="Example Co",
                  status="shortlist", score=86)
    _seat(v, "EXAMPLE CO - Engineering Manager", company="EXAMPLE CO",
          status="dismiss", score=0)

    assert v._locate("Example Co - Engineering Manager") == [exact]


def test_the_three_consumers_share_one_fold(tmp_path):
    """`_locate`, `_archived_match` and `read_leads`' report must fold identically. A second
    copy of the rule kept in step by a comment is this repo's #30 failure mode, and here the
    three disagree SILENTLY: a `_locate` that folds against an `_archived_match` that does not
    is measurably a resurrection (the case this file's archive rows pin). Asserted on the
    SOURCE, because no fixture can witness a drift that has not happened yet."""
    import inspect

    import sluice.core.vault as vault_module

    for fn in (vault_module.Vault._locate,
               vault_module.Vault._archived_match,
               vault_module.Vault.read_leads):
        src = inspect.getsource(fn)
        assert "_fold_note_name" in src, (
            f"{fn.__qualname__} must fold through _fold_note_name, not its own casefold()")
        assert ".casefold()" not in src, (
            f"{fn.__qualname__} folds inline; that is the second copy the helper exists to "
            "prevent")
