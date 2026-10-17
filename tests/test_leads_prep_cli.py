"""`job-sluice leads prep` at the CLI layer (#195): dispatch, streams, exit codes, `--json`.

Spec: `docs/superpowers/specs/2026-10-16-interview-prep-design.md`, section 6 and its Testing
(PR 1) CLI and MCP rows. The facade's own rows live in `tests/test_prep_brief_facade.py`; these
drive `cli.main([...])` the way `tests/test_leads_dismiss_cli.py` does, because a mutant inside
`cmd_leads_prep` (a wrong exit code, an outcome line on the wrong stream, a hidden write read as
success under `--json`) would keep every facade row green.

Seeding reuses the facade file's helpers, always passing the role explicitly from the seeded
`titles` fixture (that file's own autouse role fixture does not run here). No company is typed
in this file: the helpers write the reviewed `Example Ltd` roster value themselves.
"""
import json
import os
import pathlib

import pytest

import sluice.cli as cli
import sluice.core.prepbrief as prepbrief
from sluice.cli import cmd_leads_prep, main
from sluice.core.app import Sluice
from sluice.core.config import load_config
from sluice.core.protocols import VaultConflict
from sluice.core.vault import Vault
from tests.test_prep_brief_facade import _kind_dir, _leads_dir, _ready, _seed, _story

# Every outcome's exit code, per the spec's section 6 table (and its refusals).
EXIT = {"ok": 0, "written": 0, "already_written": 0, "not_writable": 1, "written_hidden": 1,
        "present_unreadable": 1, "conflict": 1, "write_failed": 1, "not_found": 1,
        "ambiguous": 1, "entries_unreadable": 1}
# Captured once at import: a scenario built twice (the `--json` parity rows) must not wrap its
# own earlier spy, or the second fence closes the first and the write reads as `written`.
_REAL_APPEND = Vault.append_body_section
JSON_KEYS = {"outcome", "slug", "status", "candidates", "suggestions", "more_suggestions",
             "brief", "reason", "appended"}


@pytest.fixture
def role(titles):
    return titles[0][0].title()


def _run(root, monkeypatch, *argv):
    monkeypatch.setenv("VAULT_DIR", str(root))
    return main(["leads", "prep", *argv])


def _path(root, slug):
    return next(pathlib.Path(n.ref) for n in Vault(str(root)).read_leads() if n.slug == slug)


def _markdown(root, monkeypatch, slug):
    """The brief the facade renders for `slug`, through the config the CLI itself loads."""
    monkeypatch.setenv("VAULT_DIR", str(root))
    return Sluice(load_config()).prep_brief(lead=slug).markdown


def _fence_first(monkeypatch):
    """A concurrent edit: an unclosed fence lands before EOF, then the real append runs."""
    def spy(self, ref, tag, section_md, **kw):
        path = pathlib.Path(ref)
        path.write_text(path.read_text(encoding="utf-8") + "\n```\n", encoding="utf-8")
        return _REAL_APPEND(self, ref, tag, section_md, **kw)

    monkeypatch.setattr(Vault, "append_body_section", spy)


def _gone_after(monkeypatch):
    """The note moves or goes between the landed append and the read-back."""
    def spy(self, ref, tag, section_md, **kw):
        out = _REAL_APPEND(self, ref, tag, section_md, **kw)
        os.remove(ref)
        return out

    monkeypatch.setattr(Vault, "append_body_section", spy)


def _raising(monkeypatch, exc):
    def boom(self, *a, **k):
        raise exc

    monkeypatch.setattr(Vault, "append_body_section", boom)


# ── scenarios: one per outcome, each built in a fresh root ──────────────────────
# Each returns the argv after `leads prep` (without `--json`). `--json` and plain runs build
# the SAME scenario in two roots, so a write in one cannot change the other's outcome.

def _sc_ok(root, mp, role):
    return ["--lead", _ready(root, role=role)]


def _sc_ok_degraded(root, mp, role):
    return ["--lead", _seed(root, role=role)]   # no story: no_entries, still exit 0


def _sc_written(root, mp, role):
    return ["--lead", _ready(root, role=role), "--write"]


def _sc_already_written(root, mp, role):
    slug = _ready(root, role=role)
    assert _run(root, mp, "--lead", slug, "--write") == 0
    return ["--lead", slug, "--write"]


def _sc_not_writable(root, mp, role):
    return ["--lead", _seed(root, role=role), "--write"]


def _sc_written_hidden(root, mp, role):
    _fence_first(mp)
    return ["--lead", _ready(root, role=role), "--write"]


def _sc_present_unreadable(root, mp, role):
    slug = _ready(root, role=role, extra=(f'notes: "{prepbrief.TAG}"',),
                  tail=f"\n{prepbrief.HEADING}\n\nan older hand-typed brief\n")
    return ["--lead", slug, "--write"]


def _sc_conflict(root, mp, role):
    _raising(mp, VaultConflict("race"))
    return ["--lead", _ready(root, role=role), "--write"]


def _sc_write_failed(root, mp, role):
    _raising(mp, PermissionError(13, "Permission denied", "/abs/vault/note.md"))
    return ["--lead", _ready(root, role=role), "--write"]


def _sc_not_found(root, mp, role):
    _ready(root, role=role)
    return ["--lead", "no such lead", "--write"]


def _sc_not_found_after_write(root, mp, role):
    _gone_after(mp)
    return ["--lead", _ready(root, role=role), "--write"]


def _sc_ambiguous(root, mp, role):
    # Seed once for the default slug, then move that one note's name into two folders.
    name = _ready(root, role=role)
    os.remove(os.path.join(_leads_dir(root), f"{name}.md"))
    for sub in ("Active", "Archive"):
        os.makedirs(os.path.join(_leads_dir(root), sub))
        _seed(root, role=role, name=os.path.join(sub, name))
    return ["--lead", name, "--write"]


def _sc_entries_unreadable(root, mp, role):
    slug = _seed(root, role=role)
    os.makedirs(_kind_dir(root), exist_ok=True)
    pathlib.Path(_kind_dir(root), "Broken.md").write_bytes(b"---\nBest For: x\n---\n\xff\xfe\n")
    return ["--lead", slug]


SCENARIOS = {
    "ok": _sc_ok, "ok_degraded": _sc_ok_degraded, "written": _sc_written,
    "already_written": _sc_already_written, "not_writable": _sc_not_writable,
    "written_hidden": _sc_written_hidden, "present_unreadable": _sc_present_unreadable,
    "conflict": _sc_conflict, "write_failed": _sc_write_failed, "not_found": _sc_not_found,
    "not_found_after_write": _sc_not_found_after_write,
    "ambiguous": _sc_ambiguous, "entries_unreadable": _sc_entries_unreadable,
}
# The scenarios whose name is not their outcome.
_ALIASES = {"ok_degraded": "ok", "not_found_after_write": "not_found"}


def _outcome(name):
    return _ALIASES.get(name, name)


def test_the_scenarios_cover_every_outcome():
    # Scope: the table rows below are only as complete as this roster.
    assert {_outcome(n) for n in SCENARIOS} == set(EXIT)


# ── the parser ──────────────────────────────────────────────────────────────────

def test_the_subparser_registers_lead_write_json_and_no_dry_run_or_apply():
    """A brief is tool-computed, so the default previews and `--write` is the one write flag:
    no `--dry-run` (the default IS the dry run) and no `--apply` (spec Decision 8)."""
    p = cli._build_parser()
    ns = p.parse_args(["leads", "prep", "--lead", "x", "--write", "--json"])
    assert ns.lead == "x" and ns.write is True and ns.json is True
    assert ns.func is cmd_leads_prep
    ns = p.parse_args(["leads", "prep", "--lead", "x"])
    assert ns.write is False and ns.json is False
    for flag in ("--dry-run", "--apply"):
        with pytest.raises(SystemExit):
            p.parse_args(["leads", "prep", "--lead", "x", flag])
    with pytest.raises(SystemExit):
        p.parse_args(["leads", "prep"])   # --lead is required


# ── preview and write ───────────────────────────────────────────────────────────

def test_the_preview_equals_the_write(tmp_path, monkeypatch, capsys, role):
    slug = _ready(tmp_path, role=role)
    md = _markdown(tmp_path, monkeypatch, slug)
    path = _path(tmp_path, slug)
    before = path.read_text(encoding="utf-8")
    assert _run(tmp_path, monkeypatch, "--lead", slug) == 0
    out, err = capsys.readouterr()
    assert out == md + "\n" and err == ""
    assert path.read_text(encoding="utf-8") == before   # a preview writes nothing
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--write") == 0
    out, err = capsys.readouterr()
    assert out == md + "\n"
    assert err == f"leads prep: {slug}: {cli._PREP_WRITTEN}\n"
    sep = "" if before.endswith("\n") else "\n"
    assert path.read_text(encoding="utf-8") == before + sep + "\n" + md + "\n"


def test_a_degraded_preview_prints_and_exits_zero(tmp_path, monkeypatch, capsys, role):
    slug = _seed(tmp_path, role=role)   # no story at all: `no_entries`
    assert _run(tmp_path, monkeypatch, "--lead", slug) == 0
    out, err = capsys.readouterr()
    r = Sluice(load_config()).prep_brief(lead=slug)
    assert [d["code"] for d in r.brief["degradations"]] == [prepbrief.NO_ENTRIES]
    assert out == r.markdown + "\n" and err == ""


# ── each --write outcome's stderr line and exit code (spec section 6) ───────────

def _write_run(tmp_path, monkeypatch, capsys, role, name):
    argv = SCENARIOS[name](tmp_path, monkeypatch, role)
    capsys.readouterr()
    code = _run(tmp_path, monkeypatch, *argv)
    out, err = capsys.readouterr()
    return argv[1], code, out, err


def test_written(tmp_path, monkeypatch, capsys, role):
    slug, code, out, err = _write_run(tmp_path, monkeypatch, capsys, role, "written")
    assert code == 0 and out.startswith(prepbrief.TAGGED_HEADING)
    assert err == f"leads prep: {slug}: written to the note\n"


def test_already_written(tmp_path, monkeypatch, capsys, role):
    slug = _ready(tmp_path, role=role)
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--write") == 0
    written = _path(tmp_path, slug).read_bytes()
    capsys.readouterr()
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--write") == 0
    out, err = capsys.readouterr()
    assert out.startswith(prepbrief.TAGGED_HEADING)
    assert err == (f'leads prep: {slug}: not written -- the note already has an "Interview prep '
                   f'brief (job-sluice)" section; the brief above is a fresh render and may '
                   f'differ from it. Delete that section to write a fresh one\n')
    assert _path(tmp_path, slug).read_bytes() == written


def test_not_writable(tmp_path, monkeypatch, capsys, role):
    slug = _seed(tmp_path, role=role)
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    reason = Sluice(load_config()).prep_brief(lead=slug, write=True).reason
    assert reason
    before = _path(tmp_path, slug).read_bytes()
    capsys.readouterr()
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--write") == 1
    out, err = capsys.readouterr()
    assert out.startswith(prepbrief.TAGGED_HEADING)   # the brief still prints
    assert err == f"leads prep: {slug}: not written -- {reason}\n"
    assert _path(tmp_path, slug).read_bytes() == before


def test_written_hidden(tmp_path, monkeypatch, capsys, role):
    slug, code, out, err = _write_run(tmp_path, monkeypatch, capsys, role, "written_hidden")
    assert code == 1 and out.startswith(prepbrief.TAGGED_HEADING)
    assert err == f"leads prep: {slug}: {cli._PREP_WRITTEN_HIDDEN}\n"
    # The spec's fixed line, verbatim; it names neither consumer, since the hiding fence can sit
    # in any section.
    assert cli._PREP_WRITTEN_HIDDEN == (
        "written, but an unclosed code fence before it now hides it, and whichever section that "
        "fence sits in (the job description or the follow-up answers, say) will read the brief "
        "as its own text until the fence is closed")
    assert "triage" not in err and "cv " not in err


def test_present_unreadable(tmp_path, monkeypatch, capsys, role):
    slug, code, out, err = _write_run(tmp_path, monkeypatch, capsys, role, "present_unreadable")
    assert code == 1 and out.startswith(prepbrief.TAGGED_HEADING)   # the brief still prints
    assert err == (f'leads prep: {slug}: the note carries the interview-prep tag but no readable '
                   f'"Interview prep brief (job-sluice)" section -- fix the note by hand\n')


def test_conflict(tmp_path, monkeypatch, capsys, role):
    slug, code, out, err = _write_run(tmp_path, monkeypatch, capsys, role, "conflict")
    assert code == 1 and out.startswith(prepbrief.TAGGED_HEADING)   # the brief still prints
    assert (f"leads prep: {slug}: conflict -- the note changed while this ran; run the same "
            f"command again\n") in err


def test_write_failed(tmp_path, monkeypatch, capsys, role):
    slug, code, out, err = _write_run(tmp_path, monkeypatch, capsys, role, "write_failed")
    assert code == 1 and out.startswith(prepbrief.TAGGED_HEADING)   # the brief still prints
    assert err == (f"leads prep: {slug}: could not write the note -- PermissionError: "
                   f"Permission denied\n")
    assert "/abs" not in err


def test_not_found_writes_nothing_and_prints_no_brief(tmp_path, monkeypatch, capsys, role):
    argv = _sc_not_found(tmp_path, monkeypatch, role)
    capsys.readouterr()
    assert _run(tmp_path, monkeypatch, *argv) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "leads prep: no lead matching 'no such lead'\n"   # no suggestion matched


def test_not_found_after_a_landed_write_says_it_was_written(tmp_path, monkeypatch, capsys,
                                                            role):
    """The append landed, then the note moved or went before the read-back. The
    slug resolved a moment earlier, so "no lead matching" would deny a write that happened."""
    slug, code, out, err = _write_run(tmp_path, monkeypatch, capsys, role,
                                      "not_found_after_write")
    assert code == 1 and out.startswith(prepbrief.TAGGED_HEADING)
    assert err == f"leads prep: {slug}: {cli._PREP_WRITTEN_GONE}\n"
    assert cli._PREP_WRITTEN_GONE == (
        "written, but the note moved or went before it could be read back, so whether the "
        "brief reads as its own section is unchecked; run the same command again to check")
    assert "no lead matching" not in err


def test_json_carries_appended_only_for_a_landed_write(tmp_path, monkeypatch, capsys, role):
    for name, appended in (("not_found_after_write", True), ("not_found", False),
                           ("written", True), ("already_written", False)):
        root = tmp_path / name
        root.mkdir()
        argv = SCENARIOS[name](root, monkeypatch, role)
        capsys.readouterr()
        _run(root, monkeypatch, *argv, "--json")
        doc = json.loads(capsys.readouterr().out)
        assert (doc["outcome"], doc["appended"]) == (_outcome(name), appended), name
        monkeypatch.setattr(Vault, "append_body_section", _REAL_APPEND)


def test_ambiguous(tmp_path, monkeypatch, capsys, role):
    slug, code, out, err = _write_run(tmp_path, monkeypatch, capsys, role, "ambiguous")
    assert code == 1 and out == ""
    assert f"leads prep: ambiguous: 2 notes claim the slug '{slug}'" in err


def test_entries_unreadable(tmp_path, monkeypatch, capsys, role):
    argv = _sc_entries_unreadable(tmp_path, monkeypatch, role)
    capsys.readouterr()
    assert _run(tmp_path, monkeypatch, *argv) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith(f"leads prep: {argv[1]}: could not read the evidence -- stories: ")
    assert str(tmp_path) not in err


def test_did_you_mean_prints_the_bounded_list(tmp_path, monkeypatch, capsys, role):
    extra = 3
    slugs = [_seed(tmp_path, role=f"{role} {i:02d}", url=f"https://example.invalid/{i}")
             for i in range(prepbrief.SUGGESTION_LIMIT + extra)]
    assert _run(tmp_path, monkeypatch, "--lead", "") == 1
    out, err = capsys.readouterr()
    assert out == ""
    lines = err.splitlines()
    assert lines[0] == "leads prep: no lead matching ''"
    assert lines[1] == "did you mean:"
    assert lines[2:] == ([f"  {s}" for s in sorted(slugs)[:prepbrief.SUGGESTION_LIMIT]]
                         + [f"  ... and {extra} more"])


# ── --json ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_json_is_one_document_with_the_plain_runs_exit_code(tmp_path, monkeypatch, capsys,
                                                            role, name):
    plain_root, json_root = tmp_path / "plain", tmp_path / "json"
    plain_root.mkdir()
    json_root.mkdir()
    plain_argv = SCENARIOS[name](plain_root, monkeypatch, role)
    plain = _run(plain_root, monkeypatch, *plain_argv)
    json_argv = SCENARIOS[name](json_root, monkeypatch, role)
    capsys.readouterr()
    code = _run(json_root, monkeypatch, *json_argv, "--json")
    out, err = capsys.readouterr()
    doc = json.loads(out)   # ONE parseable document, nothing else on stdout
    assert set(doc) == JSON_KEYS
    assert doc["outcome"] == _outcome(name)
    assert code == plain == EXIT[_outcome(name)]
    assert "leads prep:" not in err and "did you mean" not in err   # no outcome line


def test_write_json_on_written_hidden_exits_one_with_only_json(tmp_path, monkeypatch, capsys,
                                                               role):
    _fence_first(monkeypatch)
    slug = _ready(tmp_path, role=role)
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--write", "--json") == 1
    out, err = capsys.readouterr()
    assert json.loads(out)["outcome"] == "written_hidden"
    assert out.count("\n") == 1 and "leads prep:" not in err


def test_del_and_line_separator_survive_as_escaped_json(tmp_path, monkeypatch, capsys, role):
    _story(tmp_path, "Told", body="before\x7fmiddle\u2028after")
    slug = _seed(tmp_path, role=role)
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--json") == 0
    out = capsys.readouterr().out
    assert "\x7f" not in out and "\u2028" not in out
    assert "\\u007f" in out and "\\u2028" in out
    body = json.loads(out)["brief"]["kinds"]["stories"]["entries"][0]["body"]
    assert body == "before\x7fmiddle\u2028after"


# ── the heading has one spelling ────────────────────────────────────────────────

def test_the_heading_has_one_spelling(tmp_path, monkeypatch, capsys, role):
    # Witness: hard-code the heading text in cli.py.
    renamed = "## Renamed brief (job-sluice)"
    slug = _ready(tmp_path, role=role)
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--write") == 0
    monkeypatch.setattr(prepbrief, "HEADING", renamed)
    capsys.readouterr()
    assert _run(tmp_path, monkeypatch, "--lead", slug, "--write") == 0
    already = capsys.readouterr().err
    other = tmp_path / "other"
    other.mkdir()
    argv = _sc_present_unreadable(other, monkeypatch, role)
    assert _run(other, monkeypatch, *argv) == 1
    unreadable = capsys.readouterr().err
    for err, fixed in ((already, "already has an"), (unreadable, "no readable")):
        assert fixed in err
        assert "Renamed brief (job-sluice)" in err and "Interview prep brief" not in err


# ── get_lead's content warning (MCP, PR 1) ──────────────────────────────────────

def test_get_lead_warning_names_the_tagged_heading_and_calls_lead_and_role_scraped():
    from sluice.mcpserver import _GET_LEAD_CONTENT_WARNING, _GET_LEAD_PREP_BRIEF_NOTE
    assert _GET_LEAD_PREP_BRIEF_NOTE in _GET_LEAD_CONTENT_WARNING
    note = _GET_LEAD_PREP_BRIEF_NOTE
    assert prepbrief.TAGGED_HEADING in note
    assert "`- Lead:` and `- Role:` lines" in note and "scraped" in note
    assert "`- Verified: no`" in note and "verify" in note
    assert "tag included, cannot be told apart" in note
