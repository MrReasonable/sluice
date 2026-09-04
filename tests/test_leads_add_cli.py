"""`job-sluice leads add` at the CLI layer (#241): dispatch, exit codes, printed
output.

An app-level test over `Sluice.create_lead` cannot certify this command -- the
facade is already covered by tests/test_leads_create.py, and a mutant inside
`cmd_leads_add` (a swapped exit code, a dropped outcome arm, a flag wired to the
wrong parameter) keeps every one of those green. Same rationale
tests/test_leads_dismiss_cli.py states for its own existence.

The outcome table is the whole point of the command: `create_lead` forwards
`Vault.upsert`'s six-member vocabulary verbatim, and #241's Done-when is written
against reporting what upsert actually returned rather than assuming `created`.
All six arms are exercised here.
"""
import argparse
import os

import pytest

from sluice.cli import _build_parser, main
from sluice.core.leads import Lead
from sluice.core.seendb import SeenDb
from sluice.core.vault import Vault
from tests.conftest import LOCATIONS

_URL = "https://example.invalid/jobs/1"


def _run(tmp_path, monkeypatch, *argv):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    return main(["leads", "add", *argv])


def _add(tmp_path, monkeypatch, *argv, url=_URL, company="Example Ltd",
         role="Example Role"):
    return _run(tmp_path, monkeypatch, "--url", url, "--company", company,
                "--role", role, *argv)


def _notes(tmp_path):
    return Vault(str(tmp_path)).read_leads()


def _leads_add_action(dest):
    """The named argparse action off the REAL `leads add` parser, walked through
    argparse's own `_SubParsersAction`/`.choices` -- the same private-API shape
    tests/test_docs_claims.py's `_command_tree` uses, so this cannot drift from what
    `--help` prints."""
    top = next(a for a in _build_parser()._actions
               if isinstance(a, argparse._SubParsersAction))
    leads = top.choices["leads"]
    sub = next(a for a in leads._actions if isinstance(a, argparse._SubParsersAction))
    return next(a for a in sub.choices["add"]._actions if a.dest == dest)


# ── Done-when 1: added twice reports created then updated, one note ──────────

def test_added_twice_reports_created_then_updated_and_leaves_one_note(
        tmp_path, monkeypatch, capsys):
    assert _add(tmp_path, monkeypatch) == 0
    assert "created" in capsys.readouterr().err

    # Same url, so the comparison is url-PROVEN same posting -> "updated", not the
    # weaker "merged". Both are a bare last_seen bump; only the reported string
    # differs, which is exactly the distinction this command must not flatten.
    assert _add(tmp_path, monkeypatch) == 0
    assert "updated" in capsys.readouterr().err

    assert len(_notes(tmp_path)) == 1


def test_created_prints_the_slug_so_it_can_be_passed_to_a_later_command(
        tmp_path, monkeypatch, capsys):
    """The slug is `UpsertResult`'s own answer, never a guess -- and it is what a
    user types into `cv run --lead` / `leads dismiss --lead` next, so a command
    that reports the outcome without it leaves them grepping the vault for it."""
    assert _add(tmp_path, monkeypatch) == 0
    assert "Example Ltd - Example Role" in capsys.readouterr().err


def test_updated_says_nothing_else_was_recorded_and_names_both_provenance_keys(
        tmp_path, monkeypatch, capsys):
    """A second add carrying corrections bumps last_seen and NOTHING else
    (never-clobber). Silence here reads as "your new values were saved".

    Two properties, and the second is the one that was wrong. The message must not
    ENUMERATE the dropped fields: it listed url/location/salary and omitted
    --role-type, which is dropped identically, so a user re-adding to supply a
    missing pay basis was told only about fields they had not touched. And the
    remedy must name `role_type_source` as well as `role_type` -- hand-setting the
    basis while the provenance stays blank leaves triage refusing to act on it, so
    the edit looks done and changes nothing.
    """
    _add(tmp_path, monkeypatch, "--salary", "100")
    capsys.readouterr()
    assert _add(tmp_path, monkeypatch, "--salary", "999",
                "--role-type", "contract") == 0
    err = capsys.readouterr().err
    assert "NOTHING else you passed was recorded" in err
    assert "role_type_source" in err
    fm = _notes(tmp_path)[0].fm
    assert fm.get("salary") == "100"
    # The dropped field the old message never mentioned -- pinned, so the claim and
    # the behaviour cannot drift apart again.
    assert fm.get("role_type") == ""


def test_merged_is_reported_as_merged_and_not_flattened_into_updated(
        tmp_path, monkeypatch, capsys):
    """Same company+role, DIFFERENT url, no location on either side: the store
    cannot prove same-or-different, so it merges rather than splitting. Exit 0 --
    a note exists and last_seen was bumped -- but the word must survive."""
    assert _add(tmp_path, monkeypatch) == 0
    capsys.readouterr()
    assert _add(tmp_path, monkeypatch, url="https://example.invalid/jobs/2") == 0
    err = capsys.readouterr().err
    assert "merged" in err
    assert len(_notes(tmp_path)) == 1

    # This is the ONE exit-0 outcome where the posting the user typed is recorded
    # nowhere -- the url goes with everything else -- so the message must name the way
    # out, as both merged_away arms do. Without it a user whose second job really is
    # different is told "merged" and left with `apply` pointed at the first one.
    assert "company or role" in err
    assert _notes(tmp_path)[0].fm.get("url") == _URL   # url B was NOT recorded


def test_the_stated_remedy_works_from_the_state_that_actually_produces_merged(
        tmp_path, monkeypatch):
    """Run the advice the `merged` message gives, from the state that GENERATES it.

    The first version of this test seeded the first add WITH a location and asserted a
    differing `--location` split the pair -- true, and worthless, because it chose the
    one starting state in which the advice works. From a first add with no location
    (the common case, and the one that yields `merged` at all), a differing
    `--location` merges for ever: `_compare_locations` needs BOTH sides non-blank to
    report DIFFERENT, and never-clobber means the stored blank is never filled in.
    Measured: four adds at four locations, one note, location still "".

    company+role is the note's identity, so changing either always seats a new note.
    That is what the message now says, and this runs it.
    """
    assert _add(tmp_path, monkeypatch) == 0                       # no location
    assert _add(tmp_path, monkeypatch, url="https://example.invalid/jobs/2") == 0

    # The advice the message does NOT give, from this state -- pinned so the string
    # cannot quietly revert to recommending it.
    assert _add(tmp_path, monkeypatch, "--location", LOCATIONS[0],
                url="https://example.invalid/jobs/3") == 0
    assert len(_notes(tmp_path)) == 1, "a differing --location split a blank-location note"

    # The advice it does give.
    assert _add(tmp_path, monkeypatch, url="https://example.invalid/jobs/4",
                role="Example Role (Night Shift)") == 0
    notes = _notes(tmp_path)
    assert len(notes) == 2, f"the stated remedy did not seat a second note: {notes}"
    assert "https://example.invalid/jobs/4" in {n.fm.get("url") for n in notes}


def test_every_outcome_message_leads_with_that_outcomes_own_name():
    """The whole command is "report what upsert actually returned", so every message
    must OPEN with the store's own word for what happened.

    Structural, not six string comparisons, because the defect this catches is a
    CLASS: `merged_away` and `merged_away_unproven` both opened with "refused", which
    silently collapsed the six-member vocabulary into three at the only place a user
    reads it -- and left a #81 non-resurrection indistinguishable from a blank
    identity for anyone (or any script) reading the first token. Per-arm assertions
    elsewhere in this file pin the EXPLANATIONS; this pins the shape they all share,
    so a seventh arm added later cannot quietly reintroduce it.
    """
    from sluice.cli import _ADD_DETAIL

    wrong = {k: v for k, v in _ADD_DETAIL.items() if not v.startswith(k)}
    assert not wrong, (
        "these outcome messages do not open with their own outcome name, so the "
        f"vocabulary the command exists to report is not what it prints: {wrong}")


# ── Done-when 2: a merged-away twin is not re-created ────────────────────────

def _merge_away(tmp_path, *, loser_url):
    """Archive a loser under `_merged/` the way a human's `leads dedupe --merge`
    does, so the next add meets #81's non-resurrection probe."""
    v = Vault(str(tmp_path))
    survivor = Lead(source="s", search="q", title="Survivor Role",
                    company="Example Foundry", url="https://example.invalid/survivor")
    loser = Lead(source="s", search="q", title="Loser Role",
                 company="Example Foundry", url=loser_url)
    assert v.upsert(survivor).outcome == "created"
    assert v.upsert(loser).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes[survivor.url].ref, [notes[loser.url].ref],
                    alt_urls=[loser_url], first_seen="2026-01-01",
                    last_seen="2026-01-01")


def test_a_merged_away_lead_is_not_recreated_and_the_remedy_is_named(
        tmp_path, monkeypatch, capsys):
    """#81. A wrong `created` here undoes a human's merge decision, and if the
    surviving twin was already `applied` it means a second application under
    their name. Non-zero, because nothing was written -- and the message must
    name the ONE recovery action (move the note back out of `_merged/`), since
    otherwise this is a permanent refusal with no stated way forward."""
    loser_url = "https://example.invalid/loser"
    _merge_away(tmp_path, loser_url=loser_url)
    before = len(_notes(tmp_path))

    assert _add(tmp_path, monkeypatch, url=loser_url, company="Example Foundry",
                role="Loser Role") == 1
    err = capsys.readouterr().err
    # The DISTINGUISHING phrase, not the shared "_merged/" token. Asserting only on
    # what all three no-write arms have in common let two real mutants live: swapping
    # this arm's message for `merged_away_unproven`'s, and swapping `refused`'s for
    # this one's -- the latter telling a user whose company and role were blank to go
    # and move a note out of an archive they never used.
    assert "covers this exact url" in err
    assert len(_notes(tmp_path)) == before


def test_a_merged_away_lead_matched_on_weaker_evidence_is_also_not_recreated(
        tmp_path, monkeypatch, capsys):
    """The `merged_away_unproven` arm: same archived identity, a DIFFERENT url, so
    the match is not url-proven. It writes nothing either, and must not be
    flattened into the proven arm -- the two differ in whether the ingest sink may
    ever record them, so a command that prints one word for both teaches the user
    a distinction the store does not make."""
    _merge_away(tmp_path, loser_url="https://example.invalid/loser")
    before = len(_notes(tmp_path))

    assert _add(tmp_path, monkeypatch, url="https://example.invalid/other",
                company="Example Foundry", role="Loser Role") == 1
    err = capsys.readouterr().err
    assert "_merged/" in err
    assert "weaker evidence" in err          # distinguishing: see the proven arm above
    assert len(_notes(tmp_path)) == before


# ── seen.db (decision 11) ────────────────────────────────────────────────────

def test_does_not_touch_seen_db(tmp_path, monkeypatch):
    """seen.db has no removal path, so a hand-added lead recorded there would
    silently suppress the later genuine scrape of the same posting for ever.
    `create_lead` calls `store.upsert` directly rather than through VaultSink;
    this pins that the CLI did not reintroduce the sink on the way past."""
    monkeypatch.setenv("SEEN_DB", str(tmp_path / "seen.db"))
    assert _add(tmp_path, monkeypatch) == 0
    assert not os.path.exists(tmp_path / "seen.db")
    assert SeenDb(str(tmp_path / "seen.db")).load() == set()


# ── refusals and usage errors ────────────────────────────────────────────────

def test_a_blank_identity_is_refused_and_exits_1(tmp_path, monkeypatch, capsys):
    """Whitespace-only company AND role reach upsert's own blank-identity gate,
    which refuses rather than seating a note no read could ever return."""
    assert _add(tmp_path, monkeypatch, company=" ", role=" ") == 1
    err = capsys.readouterr().err
    # "refused" alone is shared with both merged_away arms, which say it too.
    assert "read back blank" in err
    assert "_merged/" not in err, "a blank identity must not be blamed on the archive"
    assert _notes(tmp_path) == []


def test_a_non_http_url_is_a_usage_error(tmp_path, monkeypatch, capsys):
    """`create_lead` raises ValueError naming the bad field; main()'s own handler
    turns that into `job-sluice: ...` and exit 2."""
    assert _add(tmp_path, monkeypatch, url="ftp://example.invalid/1") == 2
    # The BAD-FIELD LIST, not a bare "url": every such message ends "...and url must be
    # present and http(s))", so `"url" in err` passes even when the raise names a
    # completely different field -- measured, with `['salary']` reported for a bad url.
    assert "['url']" in capsys.readouterr().err


def test_an_unsafe_field_is_a_usage_error_and_is_named(tmp_path, monkeypatch, capsys):
    assert _add(tmp_path, monkeypatch, "--salary", 'a " b') == 2
    assert "['salary']" in capsys.readouterr().err


def test_url_company_and_role_are_all_required(tmp_path, monkeypatch):
    """argparse's own exit 2. `url` is required at the facade too (a hand-added
    lead is apply-eligible by construction), but company and role are the vault's
    identity key and only argparse asks for them."""
    for argv in (["--company", "Example Ltd", "--role", "Example Role"],
                 ["--url", _URL, "--role", "Example Role"],
                 ["--url", _URL, "--company", "Example Ltd"]):
        monkeypatch.setenv("VAULT_DIR", str(tmp_path))
        try:
            main(["leads", "add", *argv])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"{argv} was accepted with a field missing")


# ── role_type: a closed set, and the provenance it earns ─────────────────────

def test_an_unknown_role_type_is_a_usage_error_listing_the_accepted_names(
        tmp_path, monkeypatch, capsys):
    """A typo must refuse the whole command, not half-succeed.

    `normalise_role_type` warns and returns "" for an unrecognised value. The warning
    is visible (the default level is INFO), so the hazard is not silence: it is that
    the command would go on to print `created` and leave a note whose pay basis is
    blank, and `triage` then judges the salary against the wrong floor. Nothing is
    written here, and the accepted spellings are listed.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    try:
        main(["leads", "add", "--url", _URL, "--company", "Example Ltd",
              "--role", "Example Role", "--role-type", "flexi-time"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("an unknown --role-type was accepted")
    err = capsys.readouterr().err
    assert "contract" in err and "permanent" in err
    assert _notes(tmp_path) == []


@pytest.mark.parametrize("spelling,canonical", [("perm", "permanent"),
                                                ("freelance", "contract"),
                                                ("fte", "permanent"),
                                                ("interim", "contract"),
                                                # argparse tests `choices` against the
                                                # RAW argument, so these four needed
                                                # `type=fold_role_type` as well as the
                                                # derived list. #223 measured casings
                                                # like these in real vault contents.
                                                ("Contract", "contract"),
                                                ("PERM", "permanent"),
                                                ("fixed-term", "contract"),
                                                ("day-rate", "contract")])
def test_every_spelling_the_facade_honours_is_accepted_here_too(
        tmp_path, monkeypatch, spelling, canonical):
    """The accepted set is DERIVED from `roletype._ALIASES`, not the two canonical
    values, and this is why. 11 of the 13 spellings the facade maps are aliases, so
    pinning argparse to `contract|permanent` made this CLI refuse input the MCP write
    tool over the SAME facade accepts -- `--role-type perm` and `--role-type
    freelance` among them, both of which a person would reasonably type."""
    assert _add(tmp_path, monkeypatch, "--role-type", spelling) == 0
    fm = _notes(tmp_path)[0].fm
    assert fm.get("role_type") == canonical
    assert fm.get("role_type_source") == "declared"


def test_the_parser_accepts_role_types_by_folding_into_the_facades_own_table():
    """Anti-drift on BOTH halves of how `--role-type` is validated.

    `choices` alone does not describe what the CLI accepts, and naming this test after
    the key set would over-claim: argparse runs `type` FIRST, so the accepted input is
    the fold's preimage of that set (`Contract`, `PERM`, `fixed-term` all reach it).
    Both parts therefore have to be pinned, and pinning only the first is how the
    casing half of the narrowing survived its own fix.

    Compared against the live parser rather than a literal, so adding an alias needs no
    edit here while replacing the derivation with a hand-listed pair reddens.
    """
    from sluice.core.roletype import _ALIASES, fold_role_type

    action = _leads_add_action("role_type")
    assert set(action.choices) == set(_ALIASES)
    assert action.type is fold_role_type, (
        "choices is compared against the RAW argument, so without the fold the parser "
        "refuses casings and punctuations the facade maps correctly")


def test_a_typed_role_type_lands_as_declared_provenance(tmp_path, monkeypatch):
    """The user typed it, so it is `declared` -- the provenance the relevance gate
    is allowed to act on (#223 2.1), unlike a value merely assumed from a board."""
    assert _add(tmp_path, monkeypatch, "--role-type", "contract") == 0
    fm = _notes(tmp_path)[0].fm
    assert fm.get("role_type") == "contract"
    assert fm.get("role_type_source") == "declared"


def test_an_omitted_role_type_stays_blank_with_no_provenance(tmp_path, monkeypatch):
    """Empty means abstain: an unstated basis is the honest answer for most leads,
    and stamping `declared` over a blank would claim the user said something."""
    assert _add(tmp_path, monkeypatch) == 0
    fm = _notes(tmp_path)[0].fm
    assert fm.get("role_type") == ""
    assert fm.get("role_type_source") == ""


# ── the note the user actually gets ──────────────────────────────────────────

def test_the_optional_fields_reach_the_note(tmp_path, monkeypatch):
    assert _add(tmp_path, monkeypatch, "--location", LOCATIONS[0],
                "--salary", "100") == 0
    fm = _notes(tmp_path)[0].fm
    assert fm.get("location") == LOCATIONS[0]
    assert fm.get("salary") == "100"
    assert fm.get("url") == _URL


def test_the_lead_lands_at_status_new_so_triage_can_pick_it_up(tmp_path, monkeypatch):
    """The whole point of #241: an added lead must be something `triage run` will
    classify, otherwise a new install still cannot reach the rest of the pipeline."""
    assert _add(tmp_path, monkeypatch) == 0
    assert _notes(tmp_path)[0].status == "new"


def test_source_is_manual_and_there_is_no_flag_to_change_it(tmp_path, monkeypatch):
    """`source` is read by triage as a source-PLUGIN ID (`resolve.py` looks it up in
    the registry to run that board's `company_from_url` extractor, and uses it as a
    board-name guard). `manual` matches no registered source, so that tier abstains.
    A `--source` flag would let `--source reed` point reed's URL extractor at a
    non-reed url and mint a confident, wrong company -- so the flag does not exist,
    and this pins that it stays that way."""
    assert _add(tmp_path, monkeypatch) == 0
    assert _notes(tmp_path)[0].fm.get("source") == "manual"

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    try:
        main(["leads", "add", "--url", _URL, "--company", "Example Ltd",
              "--role", "Example Role", "--source", "reed"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("--source was accepted")


def test_an_unrecognised_store_outcome_does_not_traceback_after_a_write(
        tmp_path, monkeypatch, capsys):
    """A seventh outcome could only come from a store change, and this line runs AFTER
    the store has decided -- so a KeyError here would show a traceback for a write that
    may well have landed, leaving the user unable to tell. Prints the word the store
    said and exits non-zero (fail closed). Note the sibling front-end is NOT the
    precedent for that exit code: `mcpserver.create_lead` also declines to raise, but
    its `if result.outcome in _DETAIL` returns an unknown outcome with no explanation
    and no failure signal."""
    from sluice.core.app import CreateLeadResult

    import sluice.core.app as app_mod
    monkeypatch.setattr(app_mod.Sluice, "create_lead",
                        lambda self, **kw: CreateLeadResult(outcome="something_new"))
    assert _add(tmp_path, monkeypatch) == 1
    assert "something_new" in capsys.readouterr().err
