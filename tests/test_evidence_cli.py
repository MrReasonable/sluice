"""The nine evidence commands (#164, Task 7): `job-sluice {experience,skills,stories}
{add,list,verify}`, built from ONE loop over `EVIDENCE_KINDS` rather than nine hand-written
parsers -- so the CLI's three groups cannot drift from the store's three kinds, and a fourth
kind later is one registry entry rather than three more copy-pasted blocks.

The tests above this comment only ever parse argv -- they never dispatch. Task 7's review
(round 2) found that meant NOTHING actually called `cmd_evidence_add`/`_list`/`_verify`
(`grep -rn cmd_evidence tests/` found one docstring mention and nothing else): the ten
per-field `add` flags are OPT_OUT of `tests/functional/test_cli_contract.py`'s dead-flag sweep
(a dynamic `getattr` it cannot statically prove is read), so with no behavioural test either,
those flags were checked by NOTHING -- proven by replacing
`fields = {f: getattr(args, field_dest(f)) or "" for f in spec.fields}` with `fields = {}` and
watching the whole suite stay green. Everything below actually CALLS the handlers, through
`sluice.cli.main` end to end (parser + dispatch), the same pattern
`tests/test_leads_dismiss_cli.py` uses and for the identical reason stated in its own
docstring: an app-level test alone cannot certify the command, since a mutant inside the
handler itself could keep every app-level test green.
"""
import os

import pytest

from sluice.cli import _build_parser, main
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.protocols import EVIDENCE_KINDS
from sluice.core.vault import Vault
from sluice.evidence.commands import field_flag


def test_every_kind_gets_add_list_and_verify():
    parser = _build_parser()
    for kind in EVIDENCE_KINDS:
        for verb in ("add", "list", "verify"):
            args = parser.parse_args([kind, verb] + (["--name", "x"] if verb == "add" else []))
            assert getattr(args, "func", None) is not None, f"{kind} {verb} has no handler"


def test_add_exposes_one_flag_per_user_field_and_no_verified_flag():
    """The flags are DERIVED from EvidenceKind.fields, which is why `verified` must
    never appear there: the loop would generate --verified, the one flag decision 2
    says exists nowhere."""
    parser = _build_parser()
    args = parser.parse_args(["skills", "add", "--name", "x", "--signal-value", "s"])
    assert args.signal_value == "s"
    with pytest.raises(SystemExit):
        parser.parse_args(["skills", "add", "--name", "x", "--verified", "2099-01-01"])


def test_verify_offers_no_bulk_flag():
    """No --all, no --yes: this is the gate's trust root, and a bulk flag is the
    --verified hole one level up."""
    parser = _build_parser()
    for bulk in ("--all", "--yes"):
        with pytest.raises(SystemExit):
            parser.parse_args(["skills", "verify", bulk])


def test_every_kind_gets_its_own_group_derived_not_hand_listed():
    """Enumerate from the registry, not a hand-list -- a fourth kind must show up here for
    free, and a typo'd kind name must not silently pass because it happens to match a
    hand-written literal (see the plan's "ENUMERATE, don't hand-list" lesson)."""
    parser = _build_parser()
    top = next(a for a in parser._actions
              if a.__class__.__name__ == "_SubParsersAction")
    for kind in EVIDENCE_KINDS:
        assert kind in top.choices, f"{kind!r} has no top-level parser"


# ── behavioural coverage: CALLS the handlers, not just the parser (Task 7 review, CRITICAL 1) ──

def test_add_writes_the_per_field_flag_values_into_frontmatter(tmp_path, monkeypatch):
    """The one test that MUST go red under `fields = {}` (mutation-verified in the fix
    report): the per-field flags are OPT_OUT of the dead-flag sweep because they are
    read via a dynamic `getattr`, so this is the only thing standing between a silently
    dropped flag and a green suite."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["skills", "add", "--name", "widget", "--proficiency", "expert",
                "--domain", "backend", "--evidence", "shipped X",
                "--signal-value", "high"]) == 0
    entries = Vault(str(tmp_path)).read_pending_evidence("skills")
    assert len(entries) == 1
    assert entries[0]["fields"] == {
        "Proficiency": "expert", "Domain": "backend",
        "Evidence": "shipped X", "Signal Value": "high",
    }


def test_add_promises_citability_only_for_the_corpus_the_gate_reads(tmp_path, monkeypatch,
                                                                    capsys):
    """#164 review, M2 -- the `add` handler's half of the same over-claim.

    Every kind's confirmation line said "run `job-sluice <kind> verify` to make it
    citable", while the gate LICENSES `experience` alone. Keyed on
    `EvidenceKind.cited_by_gate` and swept over the whole registry, so no kind can be
    fixed while its siblings keep the false line -- and so a flag change carries
    this row rather than failing it.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    cited = [k for k, s in EVIDENCE_KINDS.items() if s.cited_by_gate]
    assert cited, "no kind is flagged cited_by_gate -- the positive half is vacuous"
    for kind, spec in EVIDENCE_KINDS.items():
        assert main([kind, "add", "--name", "alpha"]) == 0
        out = capsys.readouterr().out
        if spec.cited_by_gate:
            assert "to make it citable" in out, kind
        else:
            assert "to mark it reviewed" in out, kind
            assert "citable" not in out, kind


def test_add_exits_1_and_prints_to_stderr_on_a_taken_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["skills", "add", "--name", "widget", "--proficiency", "expert"]) == 0
    capsys.readouterr()
    assert main(["skills", "add", "--name", "widget", "--proficiency", "other"]) == 1
    err = capsys.readouterr().err
    assert "already proposed" in err


def test_add_exits_1_and_prints_to_stderr_on_a_name_that_does_not_reduce(tmp_path, monkeypatch,
                                                                        capsys):
    """A name of all punctuation reduces to the empty slug -- `evidence_slug` raises,
    and `add_evidence` must surface that as a named exit 1, not a traceback."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["skills", "add", "--name", "###", "--proficiency", "expert"]) == 1
    err = capsys.readouterr().err
    assert "does not reduce to a usable filename" in err


def test_add_names_a_body_file_error_and_exits_1_instead_of_crashing(tmp_path, monkeypatch,
                                                                     capsys):
    """Task 7 review, IMPORTANT 2: `--body-file`'s `open()` used to sit outside the
    handler's try/except entirely, so a missing file crashed with a raw traceback
    instead of a named exit 1 like every other failure this handler reports."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    missing = str(tmp_path / "nope.txt")
    assert main(["skills", "add", "--name", "widget", "--proficiency", "expert",
                "--body-file", missing]) == 1
    err = capsys.readouterr().err
    assert "could not read --body-file" in err
    assert missing in err
    # And nothing was proposed -- a failed read must not still write a half-formed entry.
    assert Vault(str(tmp_path)).read_pending_evidence("skills") == []


def test_list_prints_the_verified_set_and_pending_prints_the_inbox_set(tmp_path, monkeypatch,
                                                                       capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["skills", "add", "--name", "alpha", "--proficiency", "expert"]) == 0
    assert main(["skills", "add", "--name", "beta", "--proficiency", "novice"]) == 0
    capsys.readouterr()

    # Promote "alpha" directly through the facade: verify's own interactive gate is
    # covered separately below and in tests/test_app_operations.py, so a fake asker
    # here is the isolation `--no-input`/`TtyAsker` tests deliberately keep elsewhere.
    class _YesAsker:
        interactive = True

        def confirm(self, prompt):
            return True

    Sluice(Config()).verify_evidence_interactive(kind="skills", asker=_YesAsker(),
                                                 only="alpha", today="2026-08-22")

    assert main(["skills", "list"]) == 0
    verified_out = capsys.readouterr().out
    assert "alpha" in verified_out and "beta" not in verified_out

    assert main(["skills", "list", "--pending"]) == 0
    pending_out = capsys.readouterr().out
    assert "beta" in pending_out and "alpha" not in pending_out


def test_experience_list_surfaces_the_skills_field(tmp_path, monkeypatch, capsys):
    """#168 Task 10: `experience list` is the resolving command core/doctor.py's skills
    reconciliation rows point a user at, so the `Skills:` value itself has to be
    visible somewhere a plain listing shows it -- the reconciliation rows never carry
    it themselves (core/doctor.py's own "no doctor row carries user-authored text"
    rule).

    Written directly into the vault rather than through `add`, so the entry is
    CITABLE (`verified:` set) from the start and `list` (no `--pending`) shows it --
    `add` alone only ever files a pending entry."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    exp = Vault(str(tmp_path))._evidence_dir("experience")
    os.makedirs(exp, exist_ok=True)
    with open(os.path.join(exp, "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nCompany: Example Alpha\nCategory: \nBest For: \nMetrics: \n"
                 "Skills: Example Widget, Example Framework\n"
                 "verified: 2026-08-25\n---\nBody.\n")

    assert main(["experience", "list"]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "Skills: Example Widget, Example Framework" in out


def test_experience_list_omits_a_blank_skills_line(tmp_path, monkeypatch, capsys):
    """Blank is absent (SC5, cv/bundle.py:_skill_items): an entry with no `Skills:`
    annotation -- the common case for every note that predates #168 -- must not print
    a bare trailing "Skills: " on every line, which would be noise on every entry
    rather than a signal on the ones that actually declare one."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    exp = Vault(str(tmp_path))._evidence_dir("experience")
    os.makedirs(exp, exist_ok=True)
    with open(os.path.join(exp, "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nCompany: Example Alpha\nCategory: \nBest For: \nMetrics: \n"
                 "verified: 2026-08-25\n---\nBody.\n")

    assert main(["experience", "list"]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "Skills:" not in out


def test_experience_list_omits_a_whitespace_only_skills_line(tmp_path, monkeypatch,
                                                             capsys):
    """The blank case's other spelling, and the QUOTED form is the one that matters --
    measured, not assumed. An unquoted `Skills:    ` never reaches this code as
    whitespace at all: `_parse_fm_spaced` hands back `''` for it, so a test written that
    way passes with or without the fix. `Skills: "   "` survives verbatim, and that is a
    shape a human editing their own Obsidian vault can produce.

    It must read as ABSENT, because it already does everywhere else -- `_skill_items`
    splits on commas and drops each item that is empty after stripping, so this entry
    declares no skill to the bundle at all. Measured before the fix: it printed a bare
    `Skills:` suffix with nothing after it.

    A second note carries a real annotation, so the assertion cannot pass because the
    listing showed no skills field at all."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    exp = Vault(str(tmp_path))._evidence_dir("experience")
    os.makedirs(exp, exist_ok=True)
    with open(os.path.join(exp, "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nCompany: Example Alpha\nCategory: \nBest For: \nMetrics: \n"
                 'Skills: "   "\nverified: 2026-08-25\n---\nBody.\n')
    with open(os.path.join(exp, "beta.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nCompany: Example Beta\nCategory: \nBest For: \nMetrics: \n"
                 "Skills: Example Widget\nverified: 2026-08-25\n---\nBody.\n")

    assert main(["experience", "list"]) == 0
    out = capsys.readouterr().out
    lines = {ln.split("  ")[0]: ln for ln in out.splitlines() if ln.strip()}
    assert "alpha" in lines and "beta" in lines, out
    # Bound to locals before the assertions, and asserted without a trailing message.
    # `tests/test_fixture_name_neutrality.py`'s skills collector is colon-anchored and
    # scans comments too, so asserting the label directly against a SUBSCRIPT expression
    # made it read the rest of that source line as a declared skill value -- a false
    # positive with no fixture behind it, the same shape `dict(Skills=...)` avoids
    # elsewhere in this suite. Deliberately not reproduced here for that same reason.
    alpha, beta = lines["alpha"], lines["beta"]
    assert "Skills:" not in alpha
    # SCOPE: the surfacing still works, so the line above is not passing because the
    # whole field stopped being printed.
    assert "Skills: Example Widget" in beta


def test_skills_list_does_not_show_a_skills_field(tmp_path, monkeypatch, capsys):
    """The `skills` kind declares no `Skills` frontmatter field at all
    (`EVIDENCE_KINDS["skills"].fields`) -- experience's `Skills:` surfacing must not
    leak onto a listing of an unrelated kind. The premise is asserted explicitly
    rather than assumed, so a future registry edit that DID give `skills` a `Skills`
    field would fail this test for the right reason instead of the wrong one."""
    assert "Skills" not in EVIDENCE_KINDS["skills"].fields
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["skills", "add", "--name", "alpha", "--proficiency", "expert"]) == 0
    capsys.readouterr()

    class _YesAsker:
        interactive = True

        def confirm(self, prompt):
            return True

    Sluice(Config()).verify_evidence_interactive(kind="skills", asker=_YesAsker(),
                                                 only="alpha", today="2026-08-22")
    assert main(["skills", "list"]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "Skills:" not in out


def test_the_cli_gate_itself_ignores_a_skills_field_the_registry_does_not_declare(
        monkeypatch, capsys):
    """The test above drives a REAL vault, where `core/vault.py`'s `_evidence_entries`
    already never populates `fields["Skills"]` for a kind that does not declare it
    (`{k: fm.get(k, "") for k in spec.fields}`) -- so it cannot tell the CLI's OWN
    `"Skills" in spec.fields` gate (`cmd_evidence_list`) apart from that upstream
    invariant. Measured: forcing `show_skills = True` unconditionally in
    `cmd_evidence_list` left every test in this file GREEN, including the one above,
    whose own docstring claims to guard exactly this leak.

    This test drives `cmd_evidence_list` through a STUB `Sluice.list_evidence` that
    violates the upstream invariant on purpose -- a `Skills` key on a `skills`-kind
    entry, a shape the real Vault never produces -- so only the CLI's own gate stands
    between that key and the printed line."""
    from sluice.core.app import Sluice

    monkeypatch.setattr(
        Sluice, "list_evidence",
        lambda self, *, kind, pending=False: [
            {"title": "alpha", "verified": "2026-08-25",
             "fields": {"Skills": "Example Ghost"}}])
    assert main(["skills", "list"]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "Skills:" not in out


def test_verify_with_a_non_matching_id_prints_to_stderr_and_exits_1(tmp_path, monkeypatch,
                                                                    capsys):
    """R11's CLI half: the facade's `not_found` key must actually reach the terminal as
    a refusal, not just exist in the report dict tests/test_app_operations.py checks."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["skills", "add", "--name", "alpha", "--proficiency", "expert"]) == 0
    capsys.readouterr()
    assert main(["skills", "verify", "--id", "ghost"]) == 1
    err = capsys.readouterr().err
    assert "no pending entry matching 'ghost'" in err


def _hand_add(tmp_path, kind, basename, inner="Proficiency: expert"):
    """Drop an entry into `_inbox/` the way a human editing the vault does -- no `add`,
    no slugging, whatever filename they chose. Returns the inbox directory."""
    inbox = Vault(str(tmp_path))._evidence_dir(kind, inbox=True)
    os.makedirs(inbox, exist_ok=True)
    with open(os.path.join(inbox, f"{basename}.md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\n{inner}\n---\nBody text.\n")
    return inbox


def test_a_hand_added_entry_is_listed_and_its_displayed_title_is_what_id_matches(
        tmp_path, monkeypatch, capsys):
    """#164 whole-branch review, IMPORTANT 2 -- the CLI half, through `main` end to end.

    A hand-added `_inbox/My Entry.md` is displayed by `list --pending` as `My Entry`.
    `--id "My Entry"` then had to reduce that to `my-entry`, match nothing, and exit 1
    saying no pending entry matched -- against a title the previous command had just
    printed. Exit 0 with `pending: My Entry` is the whole assertion: the id matched
    (`not_found` empty), and this run is non-interactive (pytest's stdin is never a tty,
    the same route a piped or CI invocation takes) so nothing was promoted, which is
    exactly right. The promotion itself is asserted at the facade and at the store,
    where an interactive asker can be injected.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    _hand_add(tmp_path, "skills", "My Entry")

    assert main(["skills", "list", "--pending"]) == 0
    assert "My Entry" in capsys.readouterr().out

    assert main(["skills", "verify", "--id", "My Entry"]) == 0
    out = capsys.readouterr()
    assert "pending: My Entry" in out.out
    assert "no pending entry matching" not in out.err


def test_verify_names_an_unreadable_pending_entry_instead_of_crashing(tmp_path, monkeypatch,
                                                                     capsys):
    """`cmd_evidence_verify` had NO except clause at all, so every OSError out of the
    store reached the user as a raw traceback -- `main`'s own `except ValueError` does
    not cover them.

    Driven with a real vault state rather than an injected raise: a dangling symlink in
    `_inbox/` is what a sync client leaves behind when the target moves.

    Which OSError this fixture produces changed when the entry-file symlink refusal
    landed, and the docstring is corrected rather than the fixture. It used to reach
    `_read` and raise FileNotFoundError; `_evidence_entry_path` now refuses the symlink
    first (`os.path.islink` is True of a dangling link -- it lstats the entry, never the
    target). Both are an OSError out of the store on the command's first read, which is
    the handler this test exists for, so every assertion below is unchanged. The message
    must name the file either way, since "something is missing" with no path is not
    actionable in a vault the user edits by hand.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    inbox = Vault(str(tmp_path))._evidence_dir("skills", inbox=True)
    os.makedirs(inbox, exist_ok=True)
    os.symlink(str(tmp_path / "moved-away.md"), os.path.join(inbox, "ghost.md"))

    assert main(["skills", "verify"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("skills verify: "), f"not a named refusal: {err!r}"
    assert "ghost.md" in err
    assert "Traceback" not in err


def test_list_names_an_unreadable_pending_entry_instead_of_crashing(tmp_path, monkeypatch,
                                                                    capsys):
    """The identical exposure one command over: `list` reaches the same
    `read_pending_evidence`, so leaving it bare would have kept the traceback on the
    command a user runs FIRST. (Closing a gap class for one instance does not close it
    for the identical instance beside it.)

    Same fixture, and the same correction as the test above: the OSError now comes from
    `_evidence_entry_path`'s symlink refusal rather than from `_read`'s
    FileNotFoundError. The handler under test is unchanged, and so are the assertions."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    inbox = Vault(str(tmp_path))._evidence_dir("skills", inbox=True)
    os.makedirs(inbox, exist_ok=True)
    os.symlink(str(tmp_path / "moved-away.md"), os.path.join(inbox, "ghost.md"))

    assert main(["skills", "list", "--pending"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("skills list: "), f"not a named refusal: {err!r}"
    assert "ghost.md" in err


def test_add_refuses_a_name_already_verified_and_names_the_clash(tmp_path, monkeypatch,
                                                                 capsys):
    """#164 review, H2b -- the CLI half. `add` used to probe the inbox alone, so this
    second `add` returned 0 and the clash only appeared later, mid-`verify`, as a raw
    errno. The two clash messages must stay tellable apart: "already proposed" means the
    user already did this, "already named" means the name is spent."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["skills", "add", "--name", "widget", "--proficiency", "expert"]) == 0

    class _YesAsker:
        interactive = True

        def confirm(self, prompt):
            return True

    Sluice(Config()).verify_evidence_interactive(kind="skills", asker=_YesAsker(),
                                                 today="2026-08-22")
    capsys.readouterr()

    assert main(["skills", "add", "--name", "widget", "--proficiency", "other"]) == 1
    err = capsys.readouterr().err
    assert "already named 'widget'" in err
    assert "already proposed" not in err, "the wrong clash was reported"
    assert "Errno" not in err
    assert Vault(str(tmp_path)).read_pending_evidence("skills") == []


def test_verify_reports_a_failed_entry_by_name_and_still_promotes_the_rest(tmp_path,
                                                                          monkeypatch,
                                                                          capsys):
    """#164 review, H2 -- the CLI half, through `main` end to end.

    Before per-item isolation the user saw exactly one line,
    `experience verify: [Errno 17] File exists: <path>`, and exit 1: the entries this
    same run had already promoted went unreported, and the queue behind the failing entry
    was never offered. Driven through a real TTY-ish asker by patching `sys.stdin.isatty`,
    since `cmd_evidence_verify` is the layer that chooses the asker and this row is about
    what that layer PRINTS.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    base = os.path.join(str(tmp_path), "Job Applications", "Experience Library")
    os.makedirs(os.path.join(base, "_inbox"))
    with open(os.path.join(base, "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nCompany: Alpha\nverified: 2026-01-01\n---\nAlready citable.\n")
    with open(os.path.join(base, "_inbox", "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nCompany: Alpha\n---\nA clashing proposal.\n")
    assert main(["experience", "add", "--name", "november", "--company", "Beta"]) == 0
    capsys.readouterr()

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sluice.onboard.ask.TtyAsker.confirm", lambda self, prompt: True)

    assert main(["experience", "verify"]) == 1
    out = capsys.readouterr()
    # What SUCCEEDED is still reported, on stdout, alongside the failure.
    assert "verified: november" in out.out
    assert "not promoted: alpha" in out.err
    assert "already exists" in out.err
    assert "Errno" not in out.err
    assert {e["title"] for e in Vault(str(tmp_path)).read_evidence("experience")} == \
        {"alpha", "november"}


def test_verify_tells_the_user_an_entry_changed_while_they_were_reviewing_it(tmp_path,
                                                                             monkeypatch,
                                                                             capsys):
    """#164 review, H4 -- the CLI half of the abstention.

    `cmd_evidence_verify`'s `report["unchanged"]` loop had no test at all: deleting it
    left the suite green, so a user whose entry was saved over in Obsidian mid-review
    would have been told nothing whatsoever -- exit 0, no output, and the entry silently
    still pending, which reads exactly like a successful run that had nothing to do.

    The message is asserted on STDERR specifically. This is not a promotion the user
    asked for and did not get by accident; it is work they must redo, and stdout is the
    stream a caller pipes into a list of what became citable.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["skills", "add", "--name", "alpha", "--proficiency", "expert"]) == 0
    capsys.readouterr()
    target = os.path.join(Vault(str(tmp_path))._evidence_dir("skills", inbox=True),
                          "alpha.md")

    def _edit_then_yes(self, prompt):
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\nan edit made while the human was reading\n")
        return True

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sluice.onboard.ask.TtyAsker.confirm", _edit_then_yes)

    assert main(["skills", "verify"]) == 0
    out = capsys.readouterr()
    assert "changed since you reviewed it, not promoted: alpha" in out.err
    assert "verified: alpha" not in out.out
    assert Vault(str(tmp_path)).read_evidence("skills") == []


def test_verify_under_a_non_interactive_terminal_promotes_nothing_and_says_so(tmp_path,
                                                                             monkeypatch,
                                                                             capsys):
    """pytest's captured stdin is never a tty, so `main()` here drives
    `cmd_evidence_verify` down the same NoInputAsker path a piped/CI invocation takes --
    no faking required, matching the existing "isatty() is always False under pytest"
    reasoning already recorded in sluice/onboard/ask.py."""
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["skills", "add", "--name", "alpha", "--proficiency", "expert"]) == 0
    capsys.readouterr()
    assert main(["skills", "verify"]) == 0
    out = capsys.readouterr()
    assert "pending: alpha" in out.out
    assert "needs an interactive terminal" in out.err
    assert Vault(str(tmp_path)).read_evidence("skills", verified_only=True) == []


def test_no_command_message_names_a_taxonomy_word(tmp_path, monkeypatch, capsys):
    """#164 review, L2. `tests/onboard_prose.py` sweeps MODULE-LEVEL prompt constants, so it
    reaches `evidence/wizard.py` (whose prompts were hoisted for exactly that) and nothing in
    `evidence/commands.py`, whose user-facing messages are in-body f-strings. Two docstrings
    disclosed that as an accepted gap and named hoisting as the fix.

    Hoisting is the wrong fix here, and `tests/functional/test_init.py`'s
    `test_the_commands_own_report_names_no_exemplar` is the precedent for the right one:
    `cmd_init`'s own report is swept WHERE IT RUNS, because a driven command's real output
    is the thing a user reads, and a roster of constants is only a proxy for it. This file
    already drives all nine commands through `main` under capsys, so sweeping the output
    costs one assertion and no restructuring -- and unlike a constant roster it also covers
    the interpolated halves (a store error message, an entry title, a kind name).

    SCOPE. This sweeps what the sequence below actually PRINTS. A message on a branch no
    row here reaches is still unswept -- which is why the markers are asserted one per
    message rather than as a single "output is non-empty": a sweep over nothing passes, and
    so does a sweep over one command's output while another prints something else entirely.

    Round-2 review, M1 and L1. AST-extracting every `print()` literal in `commands.py` and
    replaying the ORIGINAL sequence showed five sites whose text never reached the swept
    output: the `--body-file` read error, the `list` error, and all three lines of the
    INTERACTIVE verify report (`verified:`, `changed since you reviewed it`, `not
    promoted:`). All neutral today, but a planted taxonomy word there could not be caught,
    while this docstring implied otherwise. Three phases now reach them:

      1. non-interactive, one vault -- the original rows plus an unreadable `--body-file`.
      2. interactive, same vault -- three pending entries whose outcomes are one PROMOTION,
         one MID-REVIEW EDIT and one CLASH with an already-citable name, so all three
         report lines run in a single `verify`.
      3. a SECOND vault whose evidence directories are symlinked out of it -- the store
         error `list` and `verify` name, which cannot be produced in a healthy vault.

    Phase 2 also closes L1: `Sluice.verify_evidence_interactive`'s own
    `verify this entry? [y/N]` prompt lives in `sluice/core/app.py`, which no prose sweep
    walks, and it is a real user-facing prompt on the operation that grants citability. The
    patched `confirm` records what it was SHOWN and that text is swept with the rest.
    """
    import os as _os

    from sluice.onboard.questions import expresses_a_preference

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    missing_body_file = str(tmp_path / "no-such-body-file.txt")
    by_kind = {kind: [] for kind in EVIDENCE_KINDS}

    for kind, spec in EVIDENCE_KINDS.items():
        field = field_flag(spec.fields[0])
        # Phase 1. Every non-interactive message branch these three handlers have: a
        # successful propose, all three refusals, an empty listing, a populated one, the
        # non-interactive verify report, and the no-such-id refusal.
        main([kind, "add", "--name", "alpha", field, "a value"])
        main([kind, "add", "--name", "alpha", field, "a value"])
        main([kind, "add", "--name", "###", field, "a value"])
        main([kind, "add", "--name", "delta", "--body-file", missing_body_file])
        main([kind, "list"])
        main([kind, "list", "--pending"])
        main([kind, "verify"])
        main([kind, "verify", "--id", "ghost"])
        by_kind[kind].append(capsys.readouterr())

    # Phase 2 setup, done for every kind BEFORE the asker is made interactive, so phase 1's
    # `no verified ...` listing above still runs against an empty citable set.
    prompts = []
    for kind, spec in EVIDENCE_KINDS.items():
        main([kind, "add", "--name", "bravo", field_flag(spec.fields[0]), "a value"])
        main([kind, "add", "--name", "charlie", "--body", "edit me while reading"])
        # A citable entry at `alpha`, hand-placed rather than proposed: `propose_evidence`
        # refuses a name already taken in the citable set, which is the point -- this is the
        # clash that reaches the promotion instead, and it is what makes `not promoted:` run.
        citable = Vault(str(tmp_path))._evidence_dir(kind)
        _os.makedirs(citable, exist_ok=True)
        with open(_os.path.join(citable, "alpha.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nverified: 2026-01-01\n---\nAlready citable.\n")
        capsys.readouterr()

    # Which kind the `verify` loop below is on, so `_confirm` can find that kind's own
    # inbox. A one-element list rather than a `nonlocal`, matching the mutable-cell idiom
    # `Vault.hold_for_signoff` already uses for the same "a nested function must report
    # back" reason.
    kind_now = [None]

    def _confirm(self, prompt):
        """Records the prompt (L1) and edits `charlie` mid-review (the `unchanged` arm).

        Keyed on the BODY text rather than on call order: `read_pending_evidence` sorts by
        filename today, but a test that silently exercised the wrong entry if that ever
        changed is the vacuous shape this file's other docstrings warn about.
        """
        prompts.append(prompt)
        if "edit me while reading" in prompt:
            target = _os.path.join(Vault(str(tmp_path))._evidence_dir(kind_now[0], inbox=True),
                                   "charlie.md")
            with open(target, "a", encoding="utf-8") as fh:
                fh.write("\nan edit made while the human was reading\n")
        return True

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sluice.onboard.ask.TtyAsker.confirm", _confirm)
    for kind in EVIDENCE_KINDS:
        kind_now[0] = kind
        main([kind, "verify"])
        by_kind[kind].append(capsys.readouterr())

    # Phase 3. A vault whose evidence directories point OUTSIDE it: `Vault._evidence_dir`
    # refuses, correctly, and that refusal is the only way to reach the store-error arms of
    # `list` and `verify` -- neither is producible in a healthy vault.
    second = tmp_path / "second-vault"
    outside = tmp_path / "outside-the-vault"
    outside.mkdir()
    monkeypatch.setenv("VAULT_DIR", str(second))
    for kind, spec in EVIDENCE_KINDS.items():
        link = second.joinpath(*spec.relpath.split("/"))
        link.parent.mkdir(parents=True, exist_ok=True)
        _os.symlink(str(outside), str(link))
        main([kind, "list"])
        main([kind, "verify"])
        by_kind[kind].append(capsys.readouterr())

    text = "".join(c.out + c.err for caps in by_kind.values() for c in caps) \
        + "\n".join(prompts)
    for kind, caps in by_kind.items():
        assert len(caps) == 3, f"{kind} did not reach all three phases"
        assert all(c.out or c.err for c in caps), f"a phase produced no output at all for {kind}"
        # Per kind, not merely somewhere in the pooled text: a store-error arm reached for
        # one kind and silently skipped for another would otherwise pass.
        assert f"{kind} list: " in caps[2].err
        assert f"{kind} verify: " in caps[2].err
    for marker in ("proposed: ", "already proposed", "does not reduce to a usable filename",
                   "could not read --body-file", "no verified ", "  [pending]",
                   "pending: alpha", "needs an interactive terminal",
                   "no pending entry matching", "verify this entry?",
                   "verified: bravo", "changed since you reviewed it, not promoted: charlie",
                   "not promoted: alpha", "is a symlink"):
        assert marker in text, f"the sweep never reached the message containing {marker!r}"
    assert not expresses_a_preference(text)


# ── the init wizard (#164, Task 8): seeds the corpus, never verifies it ──

from sluice.evidence.wizard import collect_evidence  # noqa: E402 -- grouped with its own tests


def _config_for(tmp_path, monkeypatch):
    """A Config whose store really does land in `tmp_path`.

    `Config(vault_dir=str(tmp_path))` alone did NOT, and read as isolation it did not
    provide. `stores/vault.py:_make` is env-first ON PURPOSE (its own docstring explains
    why an env var must not beat an explicit `Vault(...)` argument), and `conftest.py`'s
    autouse sandbox exports `VAULT_DIR` for every test in the suite -- so the wizard rows
    below wrote into the conftest vault while this helper named `tmp_path`. Measured:
    `Sluice(Config(vault_dir=A)).store().dir` is the environment's path, not `A`.

    Their assertions held anyway, because one `Sluice` did both the writing and the
    reading -- so this is a helper that lied rather than a bug -- but a later row added
    beside them that seeds or inspects `tmp_path` directly would have found an empty
    directory. `monkeypatch.setenv`, the idiom every other row in this file and in
    tests/test_app_operations.py already uses, makes the name and the destination agree.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    return Config()


class _ScriptedAsker:
    interactive = True

    def __init__(self, texts, confirms):
        self.texts, self.confirms = list(texts), list(confirms)

    def ask_text_plain(self, prompt):
        return self.texts.pop(0) if self.texts else ""

    def confirm(self, prompt):
        return self.confirms.pop(0) if self.confirms else False


def test_the_wizard_proposes_into_the_inbox_and_never_verifies(tmp_path, monkeypatch):
    """`confirms` declines the FIRST kind offered (`experience`, EVIDENCE_KINDS'
    insertion order) before accepting the second (`skills`) -- a hand-picked kind
    name in the assertion below must track the registry's real order, not an
    assumed one, or this test silently exercises the wrong branch.

    The last assertion is `_config_for`'s own witness: every other row here reads back
    through the SAME `Sluice` that wrote, which is exactly why the helper's env-first
    hole stayed invisible. Naming the destination on disk is what makes the helper's
    promise falsifiable -- drop its `monkeypatch.setenv` and this line goes red while
    the rest of the file stays green.
    """
    s = Sluice(_config_for(tmp_path, monkeypatch))
    asker = _ScriptedAsker(texts=["alpha", "P", "D", "E", "S"],
                           confirms=[False, True, False, False])
    collected = collect_evidence(asker, s)
    assert collected["skills"] == ["alpha"]
    assert s.list_evidence(kind="skills", pending=False) == [], \
        "the wizard made an entry citable without a separate verify"
    assert len(s.list_evidence(kind="skills", pending=True)) == 1
    inbox = tmp_path.joinpath(*EVIDENCE_KINDS["skills"].relpath.split("/")) / "_inbox"
    assert [p.name for p in sorted(inbox.glob("*.md"))] == ["alpha.md"], \
        "the entry did not land in the vault this test names"


class _RecordingAsker(_ScriptedAsker):
    """A `_ScriptedAsker` that also keeps every prompt it was shown, so a test can
    assert what the user was TOLD, not merely what survived."""

    def __init__(self, texts, confirms):
        super().__init__(texts, confirms)
        self.prompts = []

    def ask_text_plain(self, prompt):
        self.prompts.append(prompt)
        return super().ask_text_plain(prompt)


def test_the_wizard_carries_on_past_an_entry_it_could_not_capture(tmp_path, monkeypatch):
    """#164 review, M5. `collect_evidence`'s per-item `except (ValueError, OSError,
    FileExistsError)` had no test, so narrowing it survived -- and the two things it
    catches fail in opposite directions, so a narrowing to EITHER member alone leaves a
    real traceback escaping `job-sluice init` and taking the whole interview with it,
    mid-way, after the user has already typed several entries.

    Both members are exercised in one interview for that reason: a duplicate name
    (FileExistsError -- an OSError subclass, so `except (ValueError, FileExistsError)`
    is not a narrowing this would catch, but `except ValueError` is) and a name that
    does not reduce to a filename (ValueError, so `except OSError` is caught too).

    Three assertions. The interview reaches `bravo`, i.e. it continued past both
    failures. Both reasons were SHOWN -- a counting-only except, which the wizard's own
    comment already warns about, would satisfy the first assertion while telling the
    user nothing. And the first `alpha` survives unmodified, so the refused duplicate
    did not overwrite the entry it clashed with.
    """
    s = Sluice(_config_for(tmp_path, monkeypatch))
    asker = _RecordingAsker(
        texts=["alpha", "P", "D", "E", "S", "first body",
               "alpha", "P", "D", "E", "S", "second body", "",
               "###", "P", "D", "E", "S", "third body", "",
               "bravo", "P", "D", "E", "S", "fourth body"],
        # decline `experience`, accept `skills`, "add another" after each SUCCESS only
        # (a failure `continue`s without asking), decline `stories`.
        confirms=[False, True, True, False, False],
    )
    collected = collect_evidence(asker, s)

    assert collected["skills"] == ["alpha", "bravo"], "the interview aborted at a failure"
    shown = " ".join(asker.prompts)
    assert "already proposed" in shown, "the duplicate-name reason was never shown"
    assert "does not reduce to a usable filename" in shown, "the bad-name reason was hidden"
    [alpha] = [e for e in s.list_evidence(kind="skills", pending=True)
               if e["title"] == "alpha"]
    assert alpha["body"] == "first body", "the refused duplicate overwrote its clash"


def test_the_wizard_writes_nothing_without_a_terminal(tmp_path, monkeypatch):
    """`_NoInput` mirrors a real asker's SHAPE (`confirm`/`ask_text_plain` both exist)
    rather than omitting them, and each RAISES if called. A fake missing those methods
    entirely would still turn red if the `asker.interactive` gate in `collect_evidence`
    were ever deleted -- but for the wrong reason (AttributeError: no attribute
    'confirm'), which proves only that the fake is incomplete, not that nothing was
    asked. Raising here means a deleted gate is caught by an assertion that says
    exactly that: something was asked when nothing should have been (mutation-verified
    in the #164 Task 8 report)."""
    class _NoInput:
        interactive = False

        def confirm(self, prompt):
            raise AssertionError("collect_evidence asked a non-interactive asker to confirm")

        def ask_text_plain(self, prompt):
            raise AssertionError("collect_evidence asked a non-interactive asker for text")

    s = Sluice(_config_for(tmp_path, monkeypatch))
    assert collect_evidence(_NoInput(), s) == {}
    for kind in EVIDENCE_KINDS:
        assert s.list_evidence(kind=kind, pending=True) == []


def test_the_wizard_captures_an_optional_body_for_every_kind(tmp_path, monkeypatch):
    """FIX 4 (Task 8 review): `core/protocols.py`'s own comment on `stories` says
    Situation/Task/Action/Result live in the BODY, not in `spec.fields` -- a wizard that
    never asked for one would let a user create, and then `verify` as citable, a STAR
    story containing no story. Body capture is the SAME call site for every kind (see
    `_BODY_PROMPT_BY_KIND` in wizard.py), not a stories-special-case in the loop, so this
    exercises it through `skills` -- the entry the OTHER wizard test already captures --
    rather than adding a second kind-specific test."""
    s = Sluice(_config_for(tmp_path, monkeypatch))
    asker = _ScriptedAsker(
        texts=["alpha", "P", "D", "E", "S", "a body of evidence"],
        confirms=[False, True, False, False],
    )
    collect_evidence(asker, s)
    entries = s.list_evidence(kind="skills", pending=True)
    assert len(entries) == 1
    assert entries[0]["body"] == "a body of evidence"


def test_the_wizard_leaves_the_body_blank_when_the_user_skips_it(tmp_path, monkeypatch):
    """The uniform body prompt must not force a body: a blank answer is `add_evidence`'s
    own default, exercised here with the SAME script the first wizard test uses (which
    never has a body left in its texts list -- `_ScriptedAsker.ask_text_plain` returns
    "" once its texts are exhausted, the same EOF-safe shape `TtyAsker._read` uses)."""
    s = Sluice(_config_for(tmp_path, monkeypatch))
    asker = _ScriptedAsker(texts=["alpha", "P", "D", "E", "S"],
                           confirms=[False, True, False, False])
    collect_evidence(asker, s)
    entries = s.list_evidence(kind="skills", pending=True)
    assert len(entries) == 1
    assert entries[0]["body"] == ""
