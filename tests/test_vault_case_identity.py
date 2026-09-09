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

#299 adds a SECOND axis to the same function. `casefold()` performs no normalization, so
two composition forms of one accented employer name fold apart and seat two notes -- the
identical harm, reproduced on Linux against shipped code (`created, created`). The rows for
it live here rather than in a file of their own because the fold has ONE home and its
equivalence class should have one too.
"""
import os
import unicodedata

import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.vault import Vault, _fold_group_report, _fold_note_name
from tests.conftest import (LOCATIONS, UNREADABLE_DIR, require_case_sensitive_fs,
                            require_normalization_sensitive_fs)


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


def test_a_re_scrape_under_different_company_casing_updates_rather_than_duplicates(tmp_path):
    """The defect, through the real write path: one role, two boards, one employer spelled
    two ways. `upsert` must reconcile them onto ONE note -- a second note is a second
    identity, and the two then hold divergent status, so a dismissal recorded under one
    spelling does not stop the role returning as `new` under the other."""
    require_case_sensitive_fs(tmp_path)
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
    require_case_sensitive_fs(tmp_path)
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
    require_case_sensitive_fs(tmp_path)
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


def _seat_pair(v, base, *, status_a="shortlist", status_b="dismiss"):
    """Seat a case-variant PAIR in two different SUBFOLDERS, which is what makes these rows
    run on every filesystem instead of skipping on a developer's Mac.

    What a case-insensitive filesystem refuses is two entries differing only by case in ONE
    directory. Across two directories the pair exists happily -- and the recursive scan is
    exactly what makes that the same identity to sluice, because `_slug_for` is the BASENAME
    and both `read_leads` sweeps group on it. So the store state #205 describes is
    reproducible here without a case-sensitive mount, and the rows that only need that STATE
    (rather than needing the write path to mint it) must not be gated.

    Getting that wrong is not a cosmetic over-caution: gated, deleting the whole
    capitalisation sweep -- and separately regressing the folded probe to a bare
    `except OSError`, the arm that creates and records an irreversible `seen.db` row --
    reddened NOTHING on macOS. The guards were there and inert, which is the shape this
    file's own docstring warns about."""
    other = base.upper()
    _seat_at(os.path.join(v.leads_dir, "Active"), base,
             company="Example Co", status=status_a, score=1)
    _seat_at(os.path.join(v.leads_dir, "Archive"), other,
             company="EXAMPLE CO", status=status_b, score=2)
    return base, other


def test_read_leads_reports_a_pair_that_differs_only_by_capitalisation(tmp_path, caplog):
    """A store that predates this fix already holds pairs -- that is what wedged replication.
    `_locate` probes the exact name first, so `upsert` keeps updating whichever twin the scrape
    names and says nothing; the read path walks anyway, so the report costs one grouping over a
    list that already exists. The message must name the REMEDY, because `leads dedupe` already
    clusters such a pair (`_norm_tokens` casefolds) and a user told only that something is wrong
    has to invent a repair that already ships.

    Ungated: the pair is seated across two SUBFOLDERS, so it exists on every filesystem (see
    `_seat_pair`). Gated, deleting this whole sweep reddened nothing at all on macOS."""
    v = Vault(str(tmp_path / "vault"))
    a, b = _seat_pair(v, "Example Co - Engineering Manager")

    with caplog.at_level("WARNING"):
        notes = v.read_leads()

    assert len(notes) == 2, "both twins are still returned; this is a report, not a filter"
    msgs = [r.getMessage() for r in caplog.records]
    hits = [m for m in msgs if "one identity up to capitalisation" in m]
    assert len(hits) == 1, msgs
    assert "leads dedupe" in hits[0], "the report must name the remedy that already ships"
    assert "conflict" in hits[0], (
        "the report must not promise that --merge RESOLVES the pair: on the pair #205 "
        "reports it returns conflict and merges nothing (pinned below)")
    assert a in hits[0] and b in hits[0]


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
    assert not [m for m in msgs if "one identity up to capitalisation" in m], msgs


def test_the_capitalisation_report_is_raised_once_per_store(tmp_path, caplog):
    """Every command that reads leads walks this path, and several read twice. An unsuppressed
    report would say the same unchanged fact on each pass, which is the noise the sibling
    warning is already deduped against."""
    v = Vault(str(tmp_path / "vault"))
    _seat_pair(v, "Example Co - Engineering Manager")

    with caplog.at_level("WARNING"):
        v.read_leads()
        v.read_leads()

    hits = [r for r in caplog.records if "one identity up to capitalisation" in r.getMessage()]
    assert len(hits) == 1


# ── the fast path, and the one fold ───────────────────────────────────────────
def test_an_exact_name_wins_over_a_case_variant_on_disk(tmp_path):
    """`_locate` probes the exact name FIRST and folds only on a miss, which is what keeps the
    steady-state lookup at its previous cost (the folded listing is orders of magnitude dearer;
    see `_locate`). Pinned as BEHAVIOUR rather than as a timing, which nothing could pin: with both
    spellings on disk,
    a lookup for one of them returns THAT one alone -- never the pair, which would refuse, and
    never the other, which would write to the wrong twin."""
    require_case_sensitive_fs(tmp_path)
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
    import ast
    import inspect
    import io
    import textwrap
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

    # The roster is every site that FOLDS a note name, which is wider than the four that
    # resolve a lead by one: #298's pre-filter folds to decide which filename to ATTEMPT,
    # never which lead a name is, and it belongs here anyway because the checks below bind
    # any folding site alike -- an inline `.casefold()` there is the same second copy, and
    # `re.IGNORECASE` or `.lower()` there is the same narrower equivalence.
    #
    # BOTH halves of that pre-filter are listed, and that is the load-bearing part rather
    # than tidiness: it compares `_folded_archive_names`' fold of the directory against
    # `_archive_name_candidates`' fold of the candidate, so if the two ever fold DIFFERENTLY
    # the skip silently stops firing for exactly the sharp-s and normalization pairs #299
    # widened the fold for, and `_merged/` regains the pair a replica cannot hold. Measured:
    # with only one half listed, mutating the other to `n.lower()` left the FULL suite green.
    for fn in (vault_module.Vault._locate,
               vault_module.Vault._archived_match,
               vault_module.Vault.read_leads,
               vault_module.Vault.reconcile_names,
               vault_module._archive_name_candidates,
               vault_module._folded_archive_names):
        code = _code_only(fn)
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))

        # The POSITIVE half, matched on the AST as a CALL. A token-level `"_fold_note_name"
        # in code` was satisfied by a DOCSTRING mention, and `_code_only` strips comments but
        # keeps strings -- so for the two consumers #298 added, both of which name the helper
        # in their own prose, this assertion was inert. Measured: delegating
        # `_folded_archive_names`' fold to a new module-level `_repl_fold(n): return
        # n.casefold()` left the FULL suite green on both filesystems. The AST ban below is
        # per-function so it never saw the delegation, and this check saw the prose.
        #
        # Requiring a CALL closes it for a member that folds ONCE: it no longer calls
        # `_fold_note_name` itself, so this fires. It does NOT close it for a member that folds
        # more than once -- three of the six do -- because one remaining call satisfies this
        # check while a delegated helper does the other fold. Measured: delegating one of
        # `_locate`'s two folds reddened NOTHING. That gap is closed by the module-wide ban in
        # `test_no_inline_fold_anywhere_in_the_vault_module_outside_the_named_three`, not here.
        # `getattr`, `.translate` and `.upper()` remain unreachable by either and are not worth
        # chasing, because none is a plausible way to write this code. ACCEPTED RESIDUAL, and
        # the concession list is not exhaustive: a fold delegated to another module -- and
        # `core/leads.py` is already imported here and already holds inline folds -- survives
        # both guards even when it has DIVERGED. Closing that means an expected list spanning
        # all of `sluice/`, which nobody reads and which would fail on unrelated work.
        assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "_fold_note_name" for node in ast.walk(tree)), (
            f"{fn.__qualname__} does not CALL _fold_note_name -- naming it in a docstring is "
            "not folding through it, and a fold delegated to a helper is the second copy this "
            "roster exists to prevent")
        assert "IGNORECASE" not in code, (
            f"{fn.__qualname__} uses re.IGNORECASE, which is a NARROWER equivalence than "
            "_fold_note_name wherever a fold changes length -- and it shipped past the "
            "checks around it, because none of them can see a regex flag")

        # INLINE FOLDS are matched on the AST, never as a substring, and that is a
        # correction rather than a preference. `_code_only` joins TOKENS with newlines, so
        # `x.casefold()` comes back as ".\ncasefold\n(\n)" and the substring ".casefold()"
        # can never appear in it: the substring form of this assertion was UNSATISFIABLE for
        # its whole life, and so was a `.lower()` ban added beside it. Measured -- three live
        # mutants (`_locate` folding with `e.name.casefold()`, the same with `.lower()`, and
        # `reconcile_names` comparing `target.casefold() == n.slug.casefold()`) each kept a
        # `_fold_note_name` call elsewhere in the function, satisfied the check above, and
        # left this row AND the full suite green.
        #
        # The AST also covers spellings a substring never could: `str.lower(x)` and
        # `x.lower ()` are both `Attribute` nodes and both caught.
        inline = sorted({node.attr for node in ast.walk(tree)
                         if isinstance(node, ast.Attribute)
                         and node.attr in {"casefold", "lower"}})
        assert not inline, (
            f"{fn.__qualname__} folds inline via {inline}. `.casefold()` is the second copy "
            "the helper exists to prevent; `.lower()` is worse than a copy -- a per-character "
            "map, NARROWER wherever a fold changes length (the sharp s) and blind to "
            "normalization entirely, so both halves of #298's pre-filter stop agreeing")


def test_no_inline_fold_anywhere_in_the_vault_module_outside_the_named_three():
    """The per-function ban above cannot see a DELEGATED fold, and that is not hypothetical.

    Measured: replacing ONE of `_locate`'s two `_fold_note_name` calls with a module-level
    helper that casefolds reddens NOTHING. The positive half is satisfied because the other
    call remains, and the per-function inline ban never looks inside the helper. Round 3 tested
    that mutation on `_folded_archive_names`, which calls the fold exactly once, so requiring a
    CALL did fire there -- and the fix was recorded as closing the gap when it closes it only
    for single-call members. Three of the six roster members call the fold more than once.

    So the ban is module-WIDE, and stated as an EQUALITY against a hand-written set rather than
    a search for offenders: a sweep that derives its own roster cannot see an addition, and
    "no inline folds except in functions I listed" would let a new helper in simply by being
    new. Any inline `.casefold()`/`.lower()` anywhere in `core/vault.py` must be added here
    with a reason, and none of the three below folds a LEAD note name.
    """
    import ast
    import collections
    import inspect

    import sluice.core.vault as vault_module

    tree = ast.parse(inspect.getsource(vault_module))
    scope = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                scope.setdefault(id(child), node.name)
    # COUNTED, not a set. `"<module scope>"` is a LABEL rather than a symbol, so a set absorbs
    # every additional fold that shares a listed key: measured, a module-scope lambda taking one
    # of `_locate`'s two folds passed the FULL suite green -- the exact delegation shape this row
    # was added for. Counting also catches a second fold inside an already-listed function.
    found = collections.Counter((scope.get(id(n), "<module scope>"), n.attr)
                                for n in ast.walk(tree)
                                if isinstance(n, ast.Attribute) and n.attr in {"casefold", "lower"})

    expected = collections.Counter({
        ("_fold_note_name", "casefold"): 1,   # THE fold. Every LEAD-name comparison routes here.
        ("evidence_slug", "lower"): 1,        # slugifies an EVIDENCE name, not a lead note name.
        ("<module scope>", "casefold"): 1,    # _SANITIZED_NON_ANSWERS: placeholder COMPANY
                                              # answers, a different vocabulary entirely.
    })
    assert found == expected, (
        "the inline folds in core/vault.py no longer match the three sites that legitimately "
        f"have one -- this fires on an addition, a removal and a rename alike. Found {found}. "
        "If a new one folds a LEAD note name it must call _fold_note_name "
        "instead -- a delegated helper is the second copy the one-fold rule exists to prevent, "
        "and the per-function guard above cannot see it. If it folds something else, add it "
        "here with the reason.")


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
    require_case_sensitive_fs(tmp_path)
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
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    v = Vault(str(tmp_path / "vault"))
    _seat_pair(v, "Example Co - Engineering Manager",
               status_a=status_a, status_b=status_b)

    app = Sluice(Config())
    report = app.dedupe_report()
    assert len(report) == 1, f"dedupe did not cluster the case-variant pair: {report}"
    cid = report[0].id

    assert app.dedupe_merge([cid]) == [(cid, expected)]
    assert len(_notes(v)) == (2 if expected == "conflict" else 1)


# ── the fold must bind every path that resolves a lead, not just the read one ──
@UNREADABLE_DIR
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
    `_walk`'s `onerror=_reraise` would otherwise have raised during the walk itself.

    `@UNREADABLE_DIR` because the whole fixture is a mode bit, and a mode bit binds neither
    uid 0 nor Windows: as root `os.scandir` succeeds regardless of `0o111`, so `_locate`
    returns normally and the `pytest.raises` fails -- reporting a defect that is not there.
    The shared marker rather than a local `os.geteuid()` check, which is what the review
    suggested: `tests/conftest.py` already owns this predicate, it covers the Windows arm
    too, and a second copy of a platform rule kept in step by a comment is the drift this
    very file argues against elsewhere."""
    v = Vault(str(tmp_path / "vault"))
    sub = os.path.join(v.leads_dir, "Active")
    os.makedirs(sub, exist_ok=True)
    _seat_at(sub, "Example Co - Engineering Manager", company="Example Co", status="applied")

    # Any name the exact probe misses reaches the fold, so no case variant is needed and
    # this runs on every filesystem. An earlier version asked for a case variant and so
    # gated itself on a case-sensitive mount -- leaving the arm that CREATES and records an
    # irreversible `seen.db` row with no guard at all on a developer's machine.
    absent = "Example Foundry - Analyst"
    assert v._locate(absent) == [], "control: a genuinely absent name is [] and does not raise"

    os.chmod(sub, 0o111)   # executable, not readable: stat inside works, scandir raises
    try:
        v2 = Vault(str(tmp_path / "vault"))
        v2._scan_dirs_cache = [v.leads_dir, sub]   # as a prior successful walk left it
        with pytest.raises(OSError):
            v2._locate(absent)
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
    only predate the fix.

    Ungated: the two SOURCE notes have plainly different names, and the whole point is that
    both renames are REFUSED, so nothing case-colliding is written. The row therefore runs on
    every filesystem -- which matters, because the mutant it kills is a WRITE path minting the
    pair."""
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


def test_reconcile_names_names_the_right_axis_for_a_composition_pair(tmp_path):
    """The sibling of `read_leads`' identical-glyph report, and the reason that fix had to be
    made as a CLASS rather than an instance. `same` here is BYTE equality, so a canonically
    equivalent pair is not "same" and fell into the capitalisation arm -- which named the
    wrong axis AND printed two strings a reader cannot tell apart, so the operator was told
    that two visibly identical names differ by capitalisation.

    Ungated: both source notes have plainly different names and both renames are REFUSED, so
    nothing composition-colliding is ever written and no filesystem property is needed."""
    v = Vault(str(tmp_path / "vault"))
    nfc = unicodedata.normalize("NFC", "Example Caf\u00e9 Co")
    nfd = unicodedata.normalize("NFD", "Example Caf\u00e9 Co")
    assert nfc != nfd, "the pair collapsed; this row would certify nothing"
    _seat_at(v.leads_dir, "Unknown - Widget Analyst", company=nfc,
             status="new", role="Widget Analyst")
    _seat_at(v.leads_dir, " - Widget Analyst", company=nfd,
             status="new", role="Widget Analyst")

    rep = v.reconcile_names(apply=True)

    assert rep["renames"] == [], f"a composition pair was minted by the rename pass: {rep}"
    assert len(rep["collisions"]) == 2, rep
    reasons = {r for _s, _t, r in rep["collisions"]}
    assert len(reasons) == 1, reasons
    reason = reasons.pop()
    assert "capitalisation" not in reason, (
        f"a composition pair was blamed on capitalisation: {reason}")
    assert "Unicode composition" in reason, reason
    # The two names must be tellable apart IN THE MESSAGE. Comparing the rendered forms,
    # never the bytes: the raw pair differs in bytes by construction, so a byte comparison
    # here would pass with the disambiguation deleted -- the exact trap the sibling row in
    # this file was written after falling into.
    shown = reason.split(": ", 1)[1].split(", ")
    assert len(shown) == 2, reason
    assert (unicodedata.normalize("NFC", shown[0])
            != unicodedata.normalize("NFC", shown[1])), (
        f"the reason named one visible string twice: {reason}")


def test_an_exact_duplicate_is_not_reported_as_a_composition_variant():
    """`_fold_group_report`'s ambiguity test is "a member with DIFFERENT BYTES renders the same
    as me", not "some member renders the same as me". The weaker form counts a member against
    ITSELF whenever an exact duplicate is present, so a group holding two notes that resolve to
    the same target plus a third differing only by case came back `composition` -- an axis
    nothing in that group differs on -- and escaped the two duplicates for nothing.

    `reconcile_names` really can hand it that shape: its groups are keyed on the FOLD of the
    target, and two notes resolving to one identical target land in the same group as a
    case-variant third. Found in the round after this helper was introduced, independently by
    three reviewers and by CodeRabbit's cloud pass -- not missed by earlier rounds, which
    reviewed trees where the helper did not yet exist.

    Unit-level, because the defect is in the derivation rather than in any one caller, and
    every caller inherits it."""
    dup, cased = "Example Co - Role", "EXAMPLE CO - ROLE"
    shown, axis = _fold_group_report([dup, dup, cased])
    assert axis == "capitalisation", (
        f"an exact duplicate was read as a composition variant: {axis}")
    assert shown == sorted([dup, dup, cased]), (
        f"members were escaped though none renders like a DIFFERENT member: {shown}")

    # The genuine composition case must still be caught, and still says so.
    nfc = unicodedata.normalize("NFC", "Example Caf\u00e9 Co - Role")
    nfd = unicodedata.normalize("NFD", "Example Caf\u00e9 Co - Role")
    _shown, axis = _fold_group_report([nfc, nfd])
    assert axis == "composition", axis

    # ...including when an exact duplicate sits alongside it.
    _shown, axis = _fold_group_report([nfc, nfc, nfd])
    assert axis == "composition", (
        f"a real composition pair was masked by an exact duplicate beside it: {axis}")


def test_a_THREE_member_group_still_names_the_composition_axis(tmp_path):
    """The group shape that defeated the first version of that arm. It chose its wording on
    whether the WHOLE group rendered alike, so adding a third member that renders differently
    (a case variant) flipped it back to the capitalisation arm -- printing two visually
    identical targets under the wrong axis, which is the defect the arm exists to close.

    Per MEMBER, the NFC/NFD pair is still ambiguous and still escaped, and the case variant
    stays readable beside them."""
    v = Vault(str(tmp_path / "vault"))
    nfc = unicodedata.normalize("NFC", "Example Caf\u00e9 Co")
    nfd = unicodedata.normalize("NFD", "Example Caf\u00e9 Co")
    upper = unicodedata.normalize("NFC", "EXAMPLE CAF\u00e9 CO")
    assert len({nfc, nfd, upper}) == 3
    # Three DISTINCT placeholder heads, all in `NON_ANSWER_COMPANIES`. `reconcile_names` only
    # considers a note whose current name this store minted from a placeholder, so an invented
    # head like "Unknown 1" is skipped outright and the row reports nothing -- measured, and
    # how an earlier draft of this row passed vacuously.
    for head, company in (("Unknown", nfc), ("Confidential", nfd), ("Undisclosed", upper)):
        _seat_at(v.leads_dir, f"{head} - Widget Analyst", company=company,
                 status="new", role="Widget Analyst")

    rep = v.reconcile_names(apply=True)

    assert rep["renames"] == [], rep
    # THREE, asserted: a two-member group satisfies every assertion below, so without this the
    # row's entire subject -- the group shape that defeated the first version of that arm --
    # would go untested while the row stayed green.
    assert len(rep["collisions"]) == 3, (
        f"the group did not hold three members, so the three-member shape is untested: {rep}")
    reasons = {r for _s, _t, r in rep["collisions"]}
    assert len(reasons) == 1, reasons
    reason = reasons.pop()
    assert "capitalisation" not in reason, (
        f"a three-member group sent a composition pair to the capitalisation arm: {reason}")
    assert "Unicode composition" in reason, reason


def test_only_the_ambiguous_members_are_escaped_not_the_whole_group(tmp_path, caplog):
    """The per-member choice, witnessed. Deleting the disambiguation outright is caught
    elsewhere; swapping it for a per-GROUP predicate was not, and that is the distinction the
    helper's own comment calls "not cosmetic": `ascii()` renders every non-ASCII character as
    an escape, so escaping a whole group to disambiguate ONE pair inside it makes the other
    members unreadable to the person being asked to act on them.

    All three names fold to one identity, so they form ONE group -- that is what makes the
    property testable, and an earlier draft of this row got it wrong by seating a name that
    folds differently, which simply formed its own group and was never reported. Two of the
    three render alike (NFC and NFD) and must be escaped; the third differs by case, renders
    distinctly, and must survive as itself.

    Seated across subfolders, so it runs on every filesystem."""
    v = Vault(str(tmp_path / "vault"))
    nfc = "Example Caf\u00e9 Ltd - Engineering Manager"
    nfd = "Example Cafe\u0301 Ltd - Engineering Manager"
    cased = "EXAMPLE CAF\u00c9 LTD - ENGINEERING MANAGER"
    assert len({_fold_note_name(n) for n in (nfc, nfd, cased)}) == 1, "must be ONE group"
    for folder, name, status in (("Active", nfc, "shortlist"), ("Archive", nfd, "dismiss"),
                                 ("Other", cased, "new")):
        _seat_at(os.path.join(v.leads_dir, folder), name,
                 company="Example Co", status=status, score=1)

    with caplog.at_level("WARNING", logger="sluice.core.vault"):
        v.read_leads()

    msg = next(r.getMessage() for r in caplog.records if "one identity" in r.getMessage())
    assert cased in msg, (
        f"the unambiguous member was escaped along with the ambiguous pair, so a reader "
        f"cannot read it: {msg}")
    assert ascii(nfc) in msg or ascii(nfd) in msg, (
        f"the ambiguous pair was not disambiguated: {msg}")


def test_the_ambiguous_refusal_distinguishes_the_notes_it_names(tmp_path, caplog):
    """`_locate`'s refusal exists to tell an operator WHICH notes to reconcile, and the paths
    it names fold equal by construction -- so two differing only in composition render alike
    and it named one visible string twice. Observed on a seeded store while probing what #299
    does to a store that already holds such a pair, and fixed by routing this message through
    the same helper as every other multi-name one.

    Both notes sit in ONE directory, which is what makes the two PATHS render alike -- seated
    across subfolders the directory tells them apart and there is nothing to disambiguate
    (measured; an earlier draft did exactly that and failed for the wrong reason).

    That forces two gates rather than the usual one, and each is needed for a DIFFERENT step.

    Normalization-sensitivity, because the filesystem must hold the pair at all. Measured on
    macOS with this fixture: one note is seated rather than two, `upsert` returns `updated`,
    and ZERO log records are emitted -- there is no refusal to inspect.

    Case-sensitivity, because a filesystem that folds case resolves the uppercase candidate onto
    a seated note, so `_locate` returns ONE path and there is no ambiguity to report."""
    require_case_sensitive_fs(tmp_path)
    require_normalization_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))
    nfc = "Example Caf\u00e9 Ltd - Engineering Manager"
    nfd = "Example Cafe\u0301 Ltd - Engineering Manager"
    assert nfc != nfd and _fold_note_name(nfc) == _fold_note_name(nfd)
    _seat_at(v.leads_dir, nfc, company="Example Co", status="new", score=1)
    _seat_at(v.leads_dir, nfd, company="Example Co", status="new", score=2)

    with caplog.at_level("WARNING", logger="sluice.core.vault"):
        out = v.upsert(_lead(NFC_CO.upper(), url="https://ex.invalid/9"))

    assert out.outcome == "refused", out.outcome
    msg = next(r.getMessage() for r in caplog.records if "resolves to" in r.getMessage())
    named = msg[msg.rindex("(") + 1:msg.rindex(")")].split(", ")
    assert len(named) == 2, msg
    # As RENDERED, never as bytes: the pair differs in bytes by construction, so a byte
    # comparison passes with the disambiguation gone. That narrowing is the load-bearing one.
    #
    # Basenames rather than whole paths is precision, not necessity -- both notes share one
    # directory here, so the paths differ only in the part compared below. It names the thing
    # a reader must actually tell apart, and keeps the row honest if the fixture ever moves
    # them; an earlier draft DID seat them in separate subfolders, where the directory told
    # them apart and this assertion passed with the fix deleted.
    base = [os.path.basename(n) for n in named]
    assert (unicodedata.normalize("NFC", base[0])
            != unicodedata.normalize("NFC", base[1])), (
        f"the refusal named one visible note twice, so an operator cannot tell which two "
        f"notes to reconcile: {msg}")


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
    v = Vault(str(tmp_path / "vault"))
    _a, b = _seat_pair(v, "Example Co - Engineering Manager")

    with caplog.at_level("WARNING"):
        notes = v.read_leads({"shortlist"})

    assert len(notes) == 1, "the filter itself still applies; only the report is unfiltered"
    hits = [r.getMessage() for r in caplog.records
            if "one identity up to capitalisation" in r.getMessage()]
    assert len(hits) == 1, [r.getMessage() for r in caplog.records]
    assert b in hits[0], "the filtered-out twin must still be named in the report"


def test_the_fold_is_a_full_casefold_not_a_per_character_lowering(tmp_path):
    """`_fold_note_name`'s equivalence CLASS, pinned directly rather than only through the
    paths that consume it.

    Every other row here would stay green with `casefold()` swapped for `lower()`, because
    every fixture in them folds identically under both -- so the roster guard certifies that
    a token is present, not that the fold is the right one. That gap is not hypothetical on
    this branch: `re.IGNORECASE`, which IS a per-character lowering, shipped as the archive
    pre-filter and was measurably a resurrection.

    The sharp s is the reachable witness: `casefold` maps it to a double s and `lower` leaves
    it alone, so a company written one way and the same company written the other are one
    identity under the fold this store promises and two under the weaker one."""
    assert _fold_note_name("Widget \u00df Analyst") == _fold_note_name("Widget SS Analyst")
    assert "Widget \u00df Analyst".lower() != "Widget SS Analyst".lower(), (
        "the fixture no longer discriminates: pick a pair where casefold and lower disagree")
    # And it must NOT reach past case: two genuinely different names stay different.
    assert _fold_note_name("Example Co - A") != _fold_note_name("Example Co - B")


def test_no_code_in_the_vault_module_case_folds_with_a_regex_flag(tmp_path):
    """Module-WIDE, and the reason it exists beside the roster check above rather than
    inside it: that check is a hand-listed set of functions, and a hand list is exactly what
    went stale on this branch -- `reconcile_names` was missing from it while that function
    was minting the pair the fold prevents. This one needs no roster. It asks the whole
    module a question with one right answer.

    `re.IGNORECASE` is a per-character case mapping, so it is a SECOND and NARROWER
    equivalence than `_fold_note_name` wherever a fold changes length. There is no
    legitimate use of it in this module -- every case comparison here is about whether two
    names are one identity -- so a new one anywhere is the drift, wherever it appears.

    Comments are tokenized out: the archive pre-filter carries a long comment explaining why
    the flag is NOT used, and a raw text sweep would fail on the very code that is correct,
    forcing the deletion of the explanation that stops the flag coming back."""
    import io
    import tokenize

    import sluice.core.vault as vault_module

    with open(vault_module.__file__, encoding="utf-8") as f:
        src = f.read()
    code = "\n".join(t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
                     if t.type != tokenize.COMMENT)
    assert "IGNORECASE" not in code, (
        "core/vault.py case-folds with a regex flag somewhere; that is a second, narrower "
        "equivalence than _fold_note_name and shipped once already as a resurrection")
    # SCOPE: the sweep must actually have read the module, or it passes over nothing.
    assert "_fold_note_name" in code and len(code) > 10_000, "the sweep read nothing"


# ── #299: the SECOND axis of the same fold -- Unicode normalization ───────────
#
# `casefold()` is a case mapping and normalizes nothing, so the composed and decomposed
# forms of one name fold APART. Measured on Linux against shipped code: `upsert` returned
# `created, created` for one employer, and a two-accent name seated FOUR notes. The harm is
# #205's, unchanged -- divergent status for one job, and a pair a normalization-insensitive
# replica (every macOS vault) cannot hold, which wedges Syncthing exactly as #298 documents.
NFC_CO = unicodedata.normalize("NFC", "Example Caf\u00e9 Ltd")
NFD_CO = unicodedata.normalize("NFD", "Example Caf\u00e9 Ltd")


def test_the_two_composition_forms_this_file_uses_really_do_differ():
    """The anti-vacuity check for every row below: if the two constants ever hold the same
    value, each of those rows passes while testing nothing at all.

    What it CAN catch is the literal losing its accent -- `Caf\\u00e9` degrading to `Cafe`
    through a mangled escape or an over-zealous scrub -- after which both `normalize` calls
    return the same string and the pair is gone.

    What it CANNOT catch, contrary to what this docstring claimed first time round, is a tool
    NORMALIZING this source file. Both constants are re-derived by `unicodedata.normalize`
    from the same literal, so whichever form the literal is stored in, one comes back NFC and
    the other NFD. That is a property worth having -- it is why the constants are derived
    rather than written out as two literals -- but it means this row is not the guard against
    source normalization that it advertised itself as. There is no such guard, and none is
    needed."""
    assert NFC_CO != NFD_CO
    assert unicodedata.normalize("NFC", NFD_CO) == NFC_CO


def test_two_normalizations_of_one_name_are_one_identity():
    """The defect, at the fold itself. Runs everywhere: this is a pure string comparison,
    so unlike #298 it needs nothing of the filesystem to be falsifiable."""
    assert _fold_note_name(NFC_CO) == _fold_note_name(NFD_CO), (
        "two composition forms of one employer name fold apart, so each seats its own note "
        "with its own status")


def test_the_fold_stays_CANONICAL_and_does_not_reach_for_compatibility():
    """The line not to cross, pinned so a later 'improvement' to NFKD/NFKC reddens here.

    Canonical equivalence means the two strings ARE the same text, so folding it cannot
    merge two real employers. COMPATIBILITY equivalence is a different claim: it would make
    `Widget 2` and a superscript spelling one identity, and every step past canonical says
    two differently-spelled names are one job -- unrecoverable in the direction that matters,
    which is the reasoning `_fold_note_name`'s own docstring gives for staying narrow."""
    superscript = _fold_note_name("Widget \u00b2 Ltd - Analyst")
    ordinary = _fold_note_name("Widget 2 Ltd - Analyst")
    assert superscript != ordinary, (
        "the fold applied compatibility NORMALIZATION: a superscript and its digit are two\n"
        "identities. NFD/NFC only, never NFKD/NFKC. Note this is a ceiling on the\n"
        "NORMALIZATION, not on the resulting equivalence -- `casefold` merges the fi/ff/ffi\n"
        "ligatures on its own, and that is permitted")


def test_a_normalization_variant_rescrape_does_not_resurrect_a_merged_away_lead(tmp_path):
    """The #81 half, and the serious one: a wrong create undoes a human's merge decision, and
    where the surviving twin was already `applied` it means a second application under the
    user's name.

    Runs everywhere, for the same reason the case-variant row beside it does: this pins
    `_archived_match`'s string comparison against the name `merge_cluster` RECORDED, which is
    Python and not the filesystem. The archived note sits under `_merged/` while the
    re-scrape's candidate is an active name, so no path ever collides."""
    v = Vault(str(tmp_path / "vault"))
    _merge_away(v,
                loser=_lead(NFC_CO, url="https://ex.invalid/2"),
                survivor=_lead(NFC_CO, title="Engineering Manager II",
                               search="Engineering Manager II",
                               url="https://ex.invalid/1"))

    again = v.upsert(_lead(NFD_CO, url="https://ex.invalid/2"))

    assert again.outcome == "merged_away", (
        f"a normalization-variant re-scrape resurrected a merged-away lead: {_notes(v)}")


def test_a_normalization_variant_match_without_url_proof_is_not_recorded(tmp_path):
    """Widening the fold must not widen what enters `seen.db`, which has no removal path: a
    suppressed lead with no note anywhere is unrecoverable. The recorded arm is gated on
    `url_proven`, which no normalization can manufacture, so a same-identity RE-POST carrying
    a new url lands on the unproven arm and re-reports until a human acts."""
    v = Vault(str(tmp_path / "vault"))
    _merge_away(v,
                loser=_lead(NFC_CO, url="https://ex.invalid/2"),
                survivor=_lead(NFC_CO, title="Engineering Manager II",
                               search="Engineering Manager II",
                               url="https://ex.invalid/1"))

    again = v.upsert(_lead(NFD_CO, url="https://ex.invalid/999"))

    assert again.outcome == "merged_away_unproven"


def test_two_composition_forms_do_not_seat_two_notes(tmp_path):
    """The write path, end to end. Gated on its OWN filesystem property: macOS conflates the
    pair on APFS in both variants, so `_locate`'s exact-path probe resolves the second form
    onto the first before sluice sees it and the defect cannot exist here."""
    require_normalization_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))

    first = v.upsert(_lead(NFC_CO))
    second = v.upsert(_lead(NFD_CO, url="https://ex.invalid/2"))

    assert first.outcome == "created"
    # `updated`, not merely "not created". A narrowed fold makes `upsert`'s create-race loop
    # exhaust and return `refused` -- a lead that can never be written again -- and every
    # weaker assertion here passes on it. Measured on a case-sensitive volume with byte-exact
    # note matching restored, which is the one property Linux has and APFS lacks.
    assert second.outcome == "updated", (
        f"a composition variant did not resolve onto the seated note: {second.outcome}, "
        f"{_notes(v)}")
    assert len(_notes(v)) == 1, f"normalization-variant duplicate: {_notes(v)}"


def test_the_fold_is_the_DEFINED_caseless_match_not_the_naive_composition():
    """One pair, pinning BOTH halves of #299: the widening itself, and the shape chosen for
    it. `_fold_note_name` must call these two names one identity, and the two folds it was
    chosen over must each call them two -- so this row reddens if the pre-#299 `casefold()`
    is restored OR if the shape is "simplified" to `NFC(casefold(x))`.

    Measured on UCD 15.1.0 and 16.0.0 alike:

        bare casefold()      -> two identities
        NFC(casefold(x))     -> two identities
        NFD(casefold(NFD(x)))-> ONE identity        <- UAX #15 D145, what ships

    The naive form is NARROWER here, so adopting it would seat two notes for one employer:
    the defect this fold exists to close, surviving in a corner.

    A previous pair (U+0390 against its decomposed capital) pinned only the second half --
    bare `casefold()` merges it too. That is not a defect a reader can see, and FOUR reviewers
    read the row as inert on the strength of it, each having checked the bare fold and
    generalised to the naive one. Pinning both removes the ambiguity rather than documenting
    it. Do not swap in a pair without re-measuring all three columns: of the three candidates
    proposed during that review, one was naive-EQUAL and would have pinned nothing."""
    precomposed = "Widget \u1fb7 Ltd - Analyst"
    decomposed = "Widget \u1fbc\u0342 Ltd - Analyst"
    assert precomposed != decomposed, "the pair collapsed; this row would certify nothing"
    assert precomposed.casefold() != decomposed.casefold(), (
        "bare casefold() already merges this pair, so the row no longer pins #299's widening")
    naive = unicodedata.normalize("NFC", precomposed).casefold()
    assert naive != unicodedata.normalize("NFC", decomposed).casefold(), (
        "NFC(casefold(x)) already merges this pair, so the row no longer pins the D145 shape")
    assert _fold_note_name(precomposed) == _fold_note_name(decomposed), (
        "the fold classifies a canonical-caseless pair as two identities; it must be "
        "NFD(casefold(NFD(x))), UAX #15 D145")


def test_the_report_distinguishes_two_names_that_RENDER_identically(tmp_path, caplog):
    """A normalization pair is two byte sequences and one set of glyphs, so the report that
    names them was saying "2 note names ... (X, X)" -- one visible string twice, which reads
    as a bug in sluice rather than a fact about the vault. Measured on a seeded store while
    checking what #299 does to an EXISTING one, which is where it would have shipped from.

    Seated across two SUBFOLDERS so it runs on every filesystem: what a normalization-folding
    filesystem refuses is two such entries in ONE directory, and the sweep groups on the
    basename, so the pair is the same identity to sluice either way -- the same reasoning
    `_seat_pair` records for the case rows.

    Asserts the PROPERTY (the reader can tell the two apart), not the escaping mechanism, so
    a better disambiguation than `ascii()` is free to replace it."""
    v = Vault(str(tmp_path / "vault"))
    nfc = "Example Caf\u00e9 Ltd - Engineering Manager"
    nfd = "Example Cafe\u0301 Ltd - Engineering Manager"
    assert nfc != nfd and str(nfc) == str(nfd).replace(
        "e\u0301", "\u00e9"), "the pair must differ in bytes only"
    _seat_at(os.path.join(v.leads_dir, "Active"), nfc,
             company="Example Co", status="shortlist", score=1)
    _seat_at(os.path.join(v.leads_dir, "Archive"), nfd,
             company="Example Co", status="dismiss", score=2)

    with caplog.at_level("WARNING", logger="sluice.core.vault"):
        v.read_leads()

    msg = next(r.getMessage() for r in caplog.records if "one identity" in r.getMessage())
    names = msg[msg.index("(") + 1:msg.index(")")].split(", ")
    assert len(names) == 2, msg
    # Compared as RENDERED, never as bytes. `names[0] != names[1]` is true of the raw pair
    # by construction -- they differ in bytes, which is the whole premise -- so that
    # assertion passed with the disambiguation DELETED and this row certified nothing.
    # Caught by mutating rather than by reading it. Two strings look the same to a reader
    # exactly when they are canonically equivalent, so that is what is asserted.
    assert (unicodedata.normalize("NFC", names[0])
            != unicodedata.normalize("NFC", names[1])), (
        f"the report named one visible string twice, so a reader cannot tell which two "
        f"notes to act on: {msg}")
    assert "capitalisation" in msg and "normalization" in msg, (
        f"the report must name the axis that actually applies, not only case: {msg}")
