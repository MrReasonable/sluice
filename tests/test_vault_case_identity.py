"""#205: a lead's identity must not be case-variant. Boards render one employer several
ways ("Example Co", "EXAMPLE CO", "example co"), the note name is built from the company
string VERBATIM, and `_locate` probes a CONSTRUCTED path -- so on a case-sensitive
filesystem each spelling seats its own note, with its own status. In the reported store
one spelling held a live shortlist while its twin held a dismissal, and the
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

from sluice.core.app import Sluice
from sluice.core.config import Config
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

    v.upsert(_lead("Example Co", title="Widget Analyst & XY", search="Widget Analyst & XY"))
    second = v.upsert(_lead("example co", title="widget analyst & xy",
                            search="widget analyst & xy", url="https://ex.invalid/2"))

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
def _seat_at(directory, name, *, company, status, role="Engineering Manager", score=1):
    """Hand-seat a note at an exact filename in an exact directory -- the way a store that
    predates this fix holds one, and now the only way to build a case-variant PAIR at all,
    since the write path refuses to mint one."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{name}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"---\ncompany: {company}\nrole: {role}\nstatus: {status}\n"
                f"score: {score}\nurl: https://ex.invalid/{score}\n"
                f"location: {LOCATIONS[0]}\n---\n\nbody\n")
    return path


def _seat(v, name, *, company, status, score):
    """`_seat_at` rooted at the vault's own leads dir."""
    return _seat_at(v.leads_dir, name, company=company, status=status, score=score)


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
          status="shortlist", score=1)
    _seat(v, "EXAMPLE CO - Engineering Manager", company="EXAMPLE CO",
          status="dismiss", score=2)

    with caplog.at_level("WARNING"):
        notes = v.read_leads()

    assert len(notes) == 2, "both twins are still returned; this is a report, not a filter"
    msgs = [r.getMessage() for r in caplog.records]
    hits = [m for m in msgs if "differ only by capitalisation" in m]
    assert len(hits) == 1, msgs
    assert "leads dedupe" in hits[0], "the report must name the remedy that already ships"
    assert "conflict" in hits[0], (
        "the report must not promise that --merge RESOLVES the pair: on the pair #205 "
        "reports it returns conflict and merges nothing (pinned below)")
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
          status="shortlist", score=1)
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
          status="shortlist", score=1)
    _seat(v, "EXAMPLE CO - Engineering Manager", company="EXAMPLE CO",
          status="dismiss", score=2)

    with caplog.at_level("WARNING"):
        v.read_leads()
        v.read_leads()

    hits = [r for r in caplog.records if "differ only by capitalisation" in r.getMessage()]
    assert len(hits) == 1


# ── the fast path, and the one fold ───────────────────────────────────────────
def test_an_exact_name_wins_over_a_case_variant_on_disk(tmp_path):
    """`_locate` probes the exact name FIRST and folds only on a miss, which is what keeps the
    steady-state lookup at its previous cost (the folded listing is orders of magnitude dearer;
    see `_locate`). Pinned as BEHAVIOUR rather than as a timing, which nothing could pin: with both
    spellings on disk,
    a lookup for one of them returns THAT one alone -- never the pair, which would refuse, and
    never the other, which would write to the wrong twin."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))
    exact = _seat(v, "Example Co - Engineering Manager", company="Example Co",
                  status="shortlist", score=1)
    _seat(v, "EXAMPLE CO - Engineering Manager", company="EXAMPLE CO",
          status="dismiss", score=2)

    assert v._locate("Example Co - Engineering Manager") == [exact]


def test_every_name_resolving_path_shares_one_fold(tmp_path):
    """Every path that resolves a lead by NAME must fold identically. A second copy of the
    rule kept in step by a comment is this repo's #30 failure mode, and here the consumers
    disagree SILENTLY -- a `_locate` that folds against an `_archived_match` that does not is
    measurably a resurrection, and a `reconcile_names` that does not measurably MINTS a pair.
    Both were live on this branch before review, which is also why this roster is not the
    three it shipped as: `reconcile_names` was missing from it while that function was busy
    creating the exact state the fold exists to prevent.

    Asserted on the SOURCE, because no fixture can witness a drift that has not happened
    yet -- the behaviour rows elsewhere in this file cover the drifts that HAVE."""
    import inspect
    import io
    import tokenize

    import sluice.core.vault as vault_module

    def _code_only(fn):
        """The function's source with COMMENTS removed, so this guard reads what runs.

        Load-bearing rather than tidy: `_archived_match`'s comment explains at length why
        `re.IGNORECASE` is NOT used here, and a raw `inspect.getsource` sweep matches that
        prose and fails on the very code that is correct. A guard that cannot tell an
        explanation from a use would force the explanation to be deleted, which is the one
        thing that must not happen -- the comment is what stops the flag being reinstated.
        Tokenizing rather than splitting on `#`, because a `#` inside a string literal would
        truncate a real line and quietly shrink what this sweep looks at."""
        src = inspect.getsource(fn)
        out = []
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                out.append(tok.string)
        return "\n".join(out)

    for fn in (vault_module.Vault._locate,
               vault_module.Vault._archived_match,
               vault_module.Vault.read_leads,
               vault_module.Vault.reconcile_names):
        code = _code_only(fn)
        assert "_fold_note_name" in code, (
            f"{fn.__qualname__} must fold through _fold_note_name, not its own casefold()")
        assert ".casefold()" not in code, (
            f"{fn.__qualname__} folds inline; that is the second copy the helper exists to "
            "prevent")
        assert "IGNORECASE" not in code, (
            f"{fn.__qualname__} uses re.IGNORECASE, which is a NARROWER equivalence than "
            "_fold_note_name wherever a fold changes length -- and it shipped past the two "
            "checks above, because neither of them can see a regex flag")


@pytest.mark.parametrize("scraped,expected", [
    ("Example Co", "updated"),    # matches one twin exactly -> fast path, one hit
    ("EXAMPLE CO", "updated"),    # matches the other exactly -> same
    ("example co", "refused"),    # a THIRD casing -> folded probe sees both -> ambiguous
])
def test_which_casings_of_an_existing_pair_reach_the_ambiguous_refusal(tmp_path, scraped,
                                                                       expected):
    """Pins the SCOPE of the ambiguous-identity refusal over a pre-existing collided pair,
    because `_resolve_candidates` now states it in prose and a prose claim about behaviour
    is the drift this repo keeps finding in its own comments.

    The exact probe runs first, so a scrape whose casing matches either note on disk returns
    one path and updates -- silently, leaving the twin untouched and unmentioned. Only a
    casing matching neither falls through to the folded probe, sees both, and refuses. A
    board that keeps sending the spelling that created the note therefore never reaches that
    line, which is why the standing report on such pairs lives in `read_leads` instead."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))
    _seat(v, "Example Co - Engineering Manager", company="Example Co",
          status="shortlist", score=1)
    _seat(v, "EXAMPLE CO - Engineering Manager", company="EXAMPLE CO",
          status="dismiss", score=2)

    assert v.upsert(_lead(scraped)).outcome == expected


def test_the_archive_pre_filter_folds_as_widely_as_the_decision_it_gates(tmp_path):
    """The pre-filter over `_merged/` must be at least as wide as the seated-name comparison
    it gates, or that comparison never runs for the population it exists to serve -- the
    entry is dropped before its recorded name is ever read, and the lead is re-created.

    `re.IGNORECASE` looks like it delivers that and does not. It is a simple per-character
    case mapping while `_fold_note_name` is a full `casefold`, and the two disagree wherever
    a fold changes LENGTH: measured, candidate `... Widget SS Analyst` against entry
    `... Widget ss-ligature Analyst.md` does not match under IGNORECASE and does match under
    casefold. So the flag left the pre-filter NARROWER than the decision on that population,
    which is a resurrection produced by the half-measure meant to prevent one. Found by
    CodeRabbit CLI on this branch; this row is what stops it coming back.

    FS-independent: `_merged/` entries are compared as Python strings, and the two spellings
    do not collide as filenames on any filesystem -- they differ by more than case."""
    v = Vault(str(tmp_path / "vault"))
    _merge_away(v,
                loser=_lead("Example Co", title="Widget ß Analyst",
                            search="Widget ß Analyst", url="https://ex.invalid/2"),
                survivor=_lead("Example Co", title="Widget Analyst II",
                               search="Widget Analyst II", url="https://ex.invalid/1"))

    # The same posting, re-scraped with the sharp s written out. `casefold` folds both to
    # "...widget ss analyst"; a per-character case map does not.
    again = v.upsert(_lead("Example Co", title="Widget SS Analyst",
                           search="Widget SS Analyst", url="https://ex.invalid/2"))

    assert again.outcome == "merged_away", (
        f"the archive pre-filter is narrower than the comparison it gates: {_notes(v)}")
    assert not any("SS Analyst" in n for n in _notes(v))


# ── what the report's named remedy actually does ──────────────────────────────
@pytest.mark.parametrize("status_a,status_b,expected", [
    ("shortlist", "dismiss", "conflict"),   # the pair #205 reports
    ("new", "new", "merged"),
    ("shortlist", "shortlist", "merged"),
])
def test_dedupe_clusters_a_case_variant_pair_but_merges_only_when_status_agrees(
        tmp_path, monkeypatch, status_a, status_b, expected):
    """`read_leads`' report names `job-sluice leads dedupe`, so what that command DOES on a
    case-variant pair is part of the claim the message makes and has to be pinned, not
    assumed. The first wording said "`--merge` resolves it" and was FALSE on exactly the
    pair the same sentence calls the harm: `resolve_merge_status` returns `conflict` for two
    distinct non-`new` triage states, `dedupe_merge` refuses the cluster, and both notes stay
    on disk. The row that asserted the message only checked that the string `leads dedupe`
    appeared, so the false half was certified green -- which is why this runs the real pass
    instead.

    The refusal is CORRECT and is asserted as the desired behaviour, not as a defect:
    choosing which of a live shortlist and a dismissal survives is the human judgement a
    conflict exists to demand, and a tool that picked would be deciding it silently. What
    had to change was the message, not the pass.

    Clustering holds in every row -- that is the half `_norm_tokens`' casefold delivers --
    so the parametrization separates "does dedupe SEE the pair" from "does --merge finish
    it"."""
    _require_case_sensitive_fs(tmp_path)
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    v = Vault(str(tmp_path / "vault"))
    _seat(v, "Example Co - Widget Analyst", company="Example Co", status=status_a, score=1)
    _seat(v, "EXAMPLE CO - Widget Analyst", company="EXAMPLE CO", status=status_b, score=2)

    app = Sluice(Config())
    report = app.dedupe_report()
    assert len(report) == 1, f"dedupe did not cluster the case-variant pair: {report}"
    cid = report[0].id

    assert app.dedupe_merge([cid]) == [(cid, expected)]
    assert len(_notes(v)) == (2 if expected == "conflict" else 1)


# ── the fold must bind every path that resolves a lead, not just the read one ──
def test_the_folded_probe_does_not_read_an_unlistable_directory_as_absent(tmp_path):
    """`[]` is not a neutral answer on this path: it is the `if not found:` branch, which
    CREATES and which lets `_archived_match` record a `merged_away` in `seen.db` -- a store
    with no removal path, so the lead is suppressed for ever with its `last_seen` frozen.

    A bare `except OSError` here shipped on this branch and was wrong, on a justification
    that sounded right and was not: "the exact probe already reported on this directory". It
    reports the STATABILITY OF ONE PATH, not the LISTABILITY of the directory, and the two
    come apart on a directory left executable but not readable -- `os.stat` of a known path
    inside it succeeds while `os.scandir` raises. A transient EIO on a network mount is the
    same shape without the permissions.

    The warm `_scan_dirs` cache is what makes it reachable and is set up explicitly here:
    `_walk`'s `onerror=_reraise` would otherwise have raised during the walk itself."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))
    sub = os.path.join(v.leads_dir, "Active")
    os.makedirs(sub, exist_ok=True)
    _seat_at(sub, "EXAMPLE CO - Widget Analyst", company="EXAMPLE CO",
             status="applied", role="Widget Analyst")

    assert v._locate("Example Co - Widget Analyst"), "control: the fold finds it when listable"

    os.chmod(sub, 0o111)   # executable, not readable: stat inside works, scandir raises
    try:
        v2 = Vault(str(tmp_path / "vault"))
        v2._scan_dirs_cache = [v.leads_dir, sub]   # as a prior successful walk left it
        with pytest.raises(OSError):
            v2._locate("Example Co - Widget Analyst")
    finally:
        os.chmod(sub, 0o755)


def test_a_vanished_scan_directory_is_still_skipped_not_raised(tmp_path):
    """The other half of the same catch, and the reason it is not simply `raise`: a subfolder
    DELETED since the walk that filled `_scan_dirs` genuinely means 'no directory there', and
    must not turn an ordinary lookup into a failure. Exactly the pair `_is_note_file` answers
    False for."""
    v = Vault(str(tmp_path / "vault"))
    sub = os.path.join(v.leads_dir, "Active")
    os.makedirs(sub, exist_ok=True)
    _seat(v, "Example Co - Widget Analyst", company="Example Co", status="new", score=1)
    v._locate("warm")                       # a real walk, so the cache holds `sub`
    os.rmdir(sub)

    assert v._locate("EXAMPLE CO - WIDGET ANALYST")   # folds onto the note that remains


def test_reconcile_names_refuses_to_mint_a_pair_differing_only_by_case(tmp_path):
    """`reconcile_names` is the sibling WRITE path, and the contract obligation
    `core/protocols.py` states -- a store's identity equivalence binds every path that
    resolves a lead -- has to reach it too.

    Layer 2 grouped by the exact target, so two placeholder-seated notes whose companies
    differ only in capitalisation resolved to targets differing only in capitalisation,
    landed in separate groups, and BOTH renamed: measured, two `renames` and `collisions:
    []`, with the case pair newly on disk. Layer 1 does not catch it either -- it runs
    against the pre-sweep vault, where neither target is occupied yet. So this pass MINTED
    the pair `_locate`'s fold exists to stop, which also falsified the claim that such pairs
    only predate the fix."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))
    _seat_at(v.leads_dir, "Unknown - Widget Analyst", company="Example Co",
             status="new", role="Widget Analyst")
    _seat_at(v.leads_dir, " - Widget Analyst", company="EXAMPLE CO",
             status="new", role="Widget Analyst")

    rep = v.reconcile_names(apply=True)

    assert rep["renames"] == [], f"a case pair was minted by the rename pass: {rep}"
    assert len(rep["collisions"]) == 2, rep
    assert all("capitalisation" in r for _s, _t, r in rep["collisions"]), rep
    assert len(_notes(v)) == 2


def test_a_target_differing_from_its_own_name_only_by_case_is_not_its_own_blocker(tmp_path):
    """The self-skip stayed exact while layer 1 reaches the vault through `_locate`, which
    now folds. Left that way, a note whose re-derived target differs from its own name only
    in capitalisation slips the skip, reaches layer 1, and `_locate` hands back the note
    ITSELF -- reported as its own blocker on every run, for ever. Skipping is also right on
    the merits: under the equivalence the target and the current name are one identity, so
    there is nothing to rename to."""
    v = Vault(str(tmp_path / "vault"))
    # The fixture has to REACH the self-skip, which is narrower than it looks and is what an
    # earlier version of this row got wrong -- it seated `company: n/a`, which
    # `is_placeholder_company` answers True for, so `_frontmatter_name` returned "nothing
    # better to rename to" and the note went to `unresolved` without ever reaching the
    # comparison. The mutant survived, i.e. the row was inert.
    #
    # Three conditions at once: the seated HEAD must be a placeholder head (`N-A` is --
    # `_sanitize` renders "n/a" that way), the frontmatter company must NOT be a placeholder
    # (`N-a` is not: only "n/a"/"na" are members, never "n-a"), and the two must fold equal.
    _seat_at(v.leads_dir, "N-A - Widget Analyst", company="N-a", status="new",
             role="Widget Analyst")

    rep = v.reconcile_names(apply=False)

    assert rep["collisions"] == [], f"the note was reported as its own blocker: {rep}"
    assert rep["renames"] == [], f"a pure case re-seat is not a rename worth making: {rep}"
    assert rep["unresolved"] == [], f"the fixture never reached the self-skip: {rep}"


def test_the_capitalisation_report_survives_a_status_filtered_read(tmp_path, caplog):
    """The report must sweep every lead note WALKED, not the list `read_leads` RETURNS.

    #205's shape is one twin `shortlist` and the other `dismiss`, so a status-filtered read
    surfaces exactly ONE of them -- and `read_leads({"shortlist"})` is among the commonest
    calls in this codebase. Swept over the returned list, the report said nothing about the
    very pair it exists for. Every other row here reads unfiltered, where the two sweeps are
    indistinguishable, so without this one the distinction is untested: measured, reverting
    to the returned list leaves all of them green."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))
    _seat_at(v.leads_dir, "Example Co - Engineering Manager", company="Example Co",
             status="shortlist")
    _seat_at(v.leads_dir, "EXAMPLE CO - Engineering Manager", company="EXAMPLE CO",
             status="dismiss", score=2)

    with caplog.at_level("WARNING"):
        notes = v.read_leads({"shortlist"})

    assert len(notes) == 1, "the filter itself still applies; only the report is unfiltered"
    hits = [r.getMessage() for r in caplog.records
            if "differ only by capitalisation" in r.getMessage()]
    assert len(hits) == 1, [r.getMessage() for r in caplog.records]
    assert "EXAMPLE CO - Engineering Manager" in hits[0], (
        "the filtered-out twin must still be named in the report")
