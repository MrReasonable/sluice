"""setup_save end to end through the real SDK, in memory (see test_mcp_verify_evidence.py for
the harness). Every row reads setup_status first, as the coach does, and hands its `version`
to setup_save. Writes are read back from disk, never taken from the tool's report."""
import asyncio
import json
import os
from pathlib import Path

from sluice import mcpserver
from sluice.core.config import Config
from sluice.core.paths import config_file
from sluice.core.protocols import (CANDIDATE_PROFILE_RELPATH, CRITERIA_RELPATH,
                                   ROLE_BRIEF_RELPATH)
from sluice.mcpserver import build_server
from sluice.onboard.plan import build_plan


def _save(changes, *, between=None, version=None, then_doctor=False):
    """setup_status, then `between()` (an edit the user makes meanwhile), then setup_save with
    the version setup_status returned (or `version`). One Client, so the holder rebuild a save
    makes is the one a later call in the same session sees."""
    from mcp import Client

    async def _run():
        async with Client(build_server(Config(), write=True)) as client:
            status = json.loads((await client.call_tool("setup_status", {})).content[0].text)
            if between is not None:
                between()
            r = await client.call_tool("setup_save", {
                "changes": changes,
                "version": status["version"] if version is None else version})
            out = json.loads(r.content[0].text)
            if then_doctor:
                d = await client.call_tool("doctor", {})
                return out, json.loads(d.content[0].text)
            return out

    return asyncio.run(_run())


def _vault():
    return Path(os.environ["VAULT_DIR"])


def _rows(out):
    return {r["change"]: r for r in out["changes"]}


def _assert_profile_row_ok(doctor_out):
    """The Judging Profile row is OK with the exact found-detail. Not `"found" in row`: a MISSING
    profile's detail is "not found -- ...", which contains that word too, so a stale holder
    pointed at a profile-less vault would pass."""
    row = next(c for c in doctor_out["components"] if c["subject"] == "Judging Profile")
    assert (row["state"], row["detail"]) == ("ok", "found")


def _existing_hunt():
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    Path(config_file()).write_text(build_plan({}).config_text)


def _no_discovered_path(out, tmp_path):
    """Whole serialised result: the sandbox root covers the config's directory and the vault."""
    text = json.dumps(out)
    assert str(tmp_path) not in text and os.path.realpath(tmp_path) not in text


# ── writes ───────────────────────────────────────────────────────────────────

def test_a_first_run_with_vault_dir_env_writes_config_and_notes_into_that_vault(tmp_path):
    out = _save([{"kind": "config", "target": "lead_ttl_days", "value": "30"},
                 {"kind": "brief", "target": "Pay structure", "value": "Example pay text."}])
    assert out["outcome"] == "completed" and out["config_written"] is True
    assert {r["outcome"] for r in out["changes"]} == {"written"}
    assert os.environ["VAULT_DIR"] in Path(config_file()).read_text()
    assert "lead_ttl_days: 30" in Path(config_file()).read_text()
    assert (_vault() / CRITERIA_RELPATH).exists()
    assert "Example pay text." in (_vault() / ROLE_BRIEF_RELPATH).read_text()
    assert not any("previous" in r for r in out["changes"]), "a first run replaced nothing"
    _no_discovered_path(out, tmp_path)


def test_an_update_writes_and_reports_what_each_change_replaced(tmp_path):
    from sluice.onboard import review
    _existing_hunt()
    Path(config_file()).write_text(
        Path(config_file()).read_text().replace("# lead_ttl_days:", "lead_ttl_days: 14  #"))
    (_vault() / "Job Applications").mkdir(parents=True)
    (_vault() / CRITERIA_RELPATH).write_text(build_plan({}, profile_answers={
        "who": "Old profile words."}).profile_text)
    (_vault() / CANDIDATE_PROFILE_RELPATH).write_text(build_plan({}, candidate_answers={
        "cv_email": "ada@example.invalid"}).candidate_text)
    (_vault() / ROLE_BRIEF_RELPATH).write_text(review.render_role_brief(
        {"Pay structure": "Old pay words."}))
    out = _save([
        {"kind": "config", "target": "lead_ttl_days", "value": "30"},
        {"kind": "profile", "target": "Who this candidate is", "value": "New profile words."},
        {"kind": "candidate", "target": "cv_email", "value": "example.person@example.invalid"},
        {"kind": "brief", "target": "Pay structure", "value": "New pay words."},
        # Replaces the brief's placeholder, which is not the user's: no `previous`.
        {"kind": "brief", "target": "Sources consulted", "value": "Example source."},
    ])
    rows = _rows(out)
    assert {r["outcome"] for r in rows.values()} == {"written"}, rows
    assert rows["config:lead_ttl_days"]["previous"] == "14"
    assert rows["profile:## Who this candidate is"]["previous"] == "Old profile words."
    assert rows["candidate:email"]["previous"] == "ada@example.invalid"
    assert rows["brief:## Pay structure"]["previous"] == "Old pay words."
    assert "previous" not in rows["brief:## Sources consulted"]
    assert "lead_ttl_days: 30" in Path(config_file()).read_text()
    assert "New profile words." in (_vault() / CRITERIA_RELPATH).read_text()
    assert "New pay words." in (_vault() / ROLE_BRIEF_RELPATH).read_text()
    _no_discovered_path(out, tmp_path)


def test_sending_previous_back_restores_what_was_replaced():
    """The point of `previous`: a correction the user asks for after the save. The restored
    note and config read back as they did before the first save."""
    _existing_hunt()
    Path(config_file()).write_text(
        Path(config_file()).read_text().replace("# lead_ttl_days:", "lead_ttl_days: 14  #"))
    (_vault() / "Job Applications").mkdir(parents=True)
    original = build_plan({}, profile_answers={"who": "Old profile words."}).profile_text
    (_vault() / CRITERIA_RELPATH).write_text(original)
    first = _rows(_save([
        {"kind": "config", "target": "lead_ttl_days", "value": "30"},
        {"kind": "profile", "target": "Who this candidate is", "value": "New profile words."}]))
    second = _save([
        {"kind": "config", "target": "lead_ttl_days",
         "value": first["config:lead_ttl_days"]["previous"]},
        {"kind": "profile", "target": "Who this candidate is",
         "value": first["profile:## Who this candidate is"]["previous"]}])
    assert {r["outcome"] for r in second["changes"]} == {"written"}
    assert (_vault() / CRITERIA_RELPATH).read_text() == original
    from sluice.core.config import load_config
    assert load_config(config_file()).lead_ttl_days == 14


def test_a_list_value_no_restore_could_reproduce_comes_back_not_restorable():
    """inv-001 through the real tool: a list item holding a comma would come back split in
    two, so the row carries `not_restorable` naming the key, and no `previous` the coach could
    send back to write a broader filter."""
    _existing_hunt()
    Path(config_file()).write_text(Path(config_file()).read_text().replace(
        "  # reject_companies:", '  reject_companies: ["Example, Inc.", "Other Co"]  #'))
    row = _rows(_save([{"kind": "config", "target": "reject_companies",
                        "value": "Example Three"}]))["config:reject_companies"]
    assert row["outcome"] == "written" and "previous" not in row
    assert "`triage.reject_companies`" in row["not_restorable"]


def test_a_section_longer_than_one_screen_is_written_in_full():
    """The case the per-change form could never take: a Role Brief section taller than one
    screen (a normal "Sources consulted"). No form, so nothing caps it."""
    _existing_hunt()
    tall = "\n".join(f"Example source {i}: https://example.invalid/{i}" for i in range(80))
    out = _save([{"kind": "brief", "target": "Sources consulted", "value": tall}])
    assert [r["outcome"] for r in out["changes"]] == ["written"]
    assert tall in (_vault() / ROLE_BRIEF_RELPATH).read_text()


def test_a_set_aside_change_is_not_written_and_the_rest_are():
    _existing_hunt()
    out = _save([{"kind": "config", "target": "lead_ttl_days", "value": "yes"},
                 {"kind": "config", "target": "min_jd_chars", "value": "200"}])
    rows = _rows(out)
    assert rows["config:lead_ttl_days"]["outcome"] == "set_aside"
    assert "yes/no word" in rows["config:lead_ttl_days"]["reason"]
    assert rows["config:min_jd_chars"]["outcome"] == "written"
    text = Path(config_file()).read_text()
    assert "min_jd_chars: 200" in text and "lead_ttl_days: " not in text.replace(
        "# lead_ttl_days:", "")


def test_a_control_or_bidi_character_is_set_aside_and_nothing_of_it_is_written():
    _existing_hunt()
    for ch in (chr(0x1b), chr(0x202e)):
        out = _save([{"kind": "brief", "target": "Pay structure", "value": f"Example{ch}text."}])
        (row,) = out["changes"]
        assert row["outcome"] == "set_aside" and "bidirectional" in row["reason"], row
    assert not (_vault() / ROLE_BRIEF_RELPATH).exists()


def test_a_duplicate_change_is_set_aside_and_the_first_is_written():
    _existing_hunt()
    out = _save([{"kind": "config", "target": "lead_ttl_days", "value": "30"},
                 {"kind": "config", "target": "lead_ttl_days", "value": "60"}])
    assert sorted(r["outcome"] for r in out["changes"]) == ["set_aside", "written"]
    assert "lead_ttl_days: 30" in Path(config_file()).read_text()


# ── stale: nothing is written ────────────────────────────────────────────────

def test_a_note_edited_between_status_and_save_is_stale_and_writes_nothing(tmp_path):
    """The whole save is refused, the config change included: the user agreed to a playback
    made against a state that is no longer there."""
    _existing_hunt()
    (_vault() / "Job Applications").mkdir(parents=True)
    (_vault() / CRITERIA_RELPATH).write_text("# Judging Profile\n\n## Who this candidate is\n\nOld.\n")
    config_before = Path(config_file()).read_bytes()

    def edit():
        (_vault() / CRITERIA_RELPATH).write_text("edited in Obsidian\n")

    out = _save([{"kind": "profile", "target": "Who this candidate is", "value": "Example."},
                 {"kind": "config", "target": "lead_ttl_days", "value": "30"}], between=edit)
    assert out["outcome"] == "stale" and out["changes"] == []
    assert out["config_written"] is False
    assert (_vault() / CRITERIA_RELPATH).read_text() == "edited in Obsidian\n"
    assert Path(config_file()).read_bytes() == config_before
    _no_discovered_path(out, tmp_path)


def test_a_config_created_between_status_and_save_on_a_first_run_is_stale(
        tmp_path, monkeypatch):
    """The notes are absent in both reads, but the new config names its own vault: writing
    the note would land it somewhere the user never heard about."""
    monkeypatch.delenv("VAULT_DIR")
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path / "empty")
    chosen, planted = tmp_path / "chosen-vault", tmp_path / "planted-vault"
    planted_text = build_plan({"vault_dir": str(planted)}).config_text

    def plant():
        os.makedirs(os.path.dirname(config_file()), exist_ok=True)
        Path(config_file()).write_text(planted_text)

    out = _save([{"kind": "config", "target": "vault_dir", "value": str(chosen)},
                 {"kind": "brief", "target": "Pay structure", "value": "Example."}],
                between=plant)
    assert out["outcome"] == "stale" and out["config_written"] is False
    assert Path(config_file()).read_bytes() == planted_text.encode()
    for vault in (chosen, planted, tmp_path / "empty" / "vault"):
        assert not (vault / ROLE_BRIEF_RELPATH).exists(), vault
    _no_discovered_path(out, tmp_path)


def test_a_config_removed_between_status_and_save_is_stale(tmp_path):
    _existing_hunt()
    out = _save([{"kind": "brief", "target": "Pay structure", "value": "Example."}],
                between=lambda: os.unlink(config_file()))
    assert out["outcome"] == "stale"
    assert not Path(config_file()).exists()
    assert not (_vault() / ROLE_BRIEF_RELPATH).exists()
    _no_discovered_path(out, tmp_path)


def test_a_config_edited_between_status_and_save_is_stale(tmp_path):
    _existing_hunt()
    edited = Path(config_file()).read_text() + "\n# edited by hand\n"
    out = _save([{"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                between=lambda: Path(config_file()).write_text(edited))
    assert out["outcome"] == "stale"
    assert Path(config_file()).read_text() == edited


def test_a_vault_re_pointed_between_status_and_save_is_stale(tmp_path, monkeypatch):
    """Config present in both reads and the Role Brief absent in both vaults: only the vault in
    the version -- as a digest, so no path travels through the client -- can see it."""
    _existing_hunt()
    first, other = _vault(), tmp_path / "other-vault"
    out = _save([{"kind": "brief", "target": "Pay structure", "value": "Example."}],
                between=lambda: monkeypatch.setenv("VAULT_DIR", str(other)))
    assert out["outcome"] == "stale"
    for vault in (first, other):
        assert not (vault / ROLE_BRIEF_RELPATH).exists(), vault
    _no_discovered_path(out, tmp_path)


def test_a_version_from_before_an_earlier_save_is_stale():
    """A save changes what setup_status reads, so the coach must read it again before the next
    save; re-using the old version writes nothing."""
    _existing_hunt()
    from sluice.core.app import Sluice
    old = mcpserver.setup_status(Sluice.from_config_file())["version"]
    assert _save([{"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                 version=old)["outcome"] == "completed"
    out = _save([{"kind": "config", "target": "min_jd_chars", "value": "200"}], version=old)
    assert out["outcome"] == "stale"
    assert "min_jd_chars: 200" not in Path(config_file()).read_text()


# ── first run and the holder ─────────────────────────────────────────────────

def test_doctor_sees_the_new_vault_in_the_same_server_session(tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_DIR")
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path / "empty")
    vault = tmp_path / "chosen-vault"
    out, doctor_out = _save([{"kind": "config", "target": "vault_dir", "value": str(vault)}],
                            then_doctor=True)
    assert out["config_written"] is True
    assert (vault / CRITERIA_RELPATH).exists()
    assert not (tmp_path / "empty" / "vault").exists()
    # doctor through the REBUILT holder reads the new vault: its Judging Profile row is OK
    # ("found"), where the stale holder's cwd-relative vault would report it missing.
    _assert_profile_row_ok(doctor_out)
    # The resolved vault path is the user's own answer, but the response names the change by
    # key, so not even that path is carried back.
    _no_discovered_path(out, tmp_path)


def test_a_first_run_without_a_vault_sets_everything_aside_until_one_is_named(
        tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_DIR")
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path / "empty")
    vault = tmp_path / "named-vault"
    first = _save([{"kind": "config", "target": "lead_ttl_days", "value": "30"}])
    assert [r["outcome"] for r in first["changes"]] == ["set_aside"]
    assert "where your notes live" in first["changes"][0]["reason"]
    assert not Path(config_file()).exists()
    second, doctor_out = _save([{"kind": "config", "target": "vault_dir", "value": str(vault)},
                                {"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                               then_doctor=True)
    assert {r["outcome"] for r in second["changes"]} == {"written"}
    assert (vault / CRITERIA_RELPATH).exists()
    _assert_profile_row_ok(doctor_out)


def test_a_relative_vault_dir_is_set_aside_and_no_response_carries_a_discovered_path(
        tmp_path, monkeypatch):
    """A relative answer would resolve against the folder the client started the server from;
    it is set aside, and that folder appears in no response."""
    monkeypatch.delenv("VAULT_DIR")
    cwd = tmp_path / "server-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    out = _save([{"kind": "config", "target": "vault_dir", "value": "notes"},
                 {"kind": "config", "target": "lead_ttl_days", "value": "30"}])
    rows = _rows(out)
    assert "relative path" in rows["config:vault_dir"]["reason"]
    assert rows["config:lead_ttl_days"]["outcome"] == "set_aside"
    _no_discovered_path(out, tmp_path)
    assert not Path(config_file()).exists() and not (cwd / "notes").exists()


def test_a_failed_holder_rebuild_keeps_the_outcomes_and_asks_for_a_restart(monkeypatch):
    def boom():
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(mcpserver, "_holder_sluice", boom)
    out = _save([{"kind": "config", "target": "lead_ttl_days", "value": "30"}])
    assert out["config_written"] is True
    assert {r["outcome"] for r in out["changes"]} == {"written"}
    assert out["restart_needed"]


def test_an_unexpected_error_in_the_step_is_a_structured_failure_with_no_path(
        tmp_path, monkeypatch):
    """mcp discards a tool exception's message, so an error escaping the save step would reach
    the client as an opaque failure. It comes back as `failed` instead, naming the error's kind
    but never the path the error carried, and the holder is still rebuilt."""
    secret = str(tmp_path / "somewhere" / "config.yaml")

    def boom(*a, **k):
        raise PermissionError(13, "Permission denied", secret)

    rebuilt = []
    real = mcpserver._holder_sluice
    monkeypatch.setattr(mcpserver, "setup_save_step", boom)
    monkeypatch.setattr(mcpserver, "_holder_sluice", lambda: rebuilt.append(1) or real())
    out = _save([{"kind": "config", "target": "lead_ttl_days", "value": "30"}])
    assert out["outcome"] == "failed"
    assert out["reason"].startswith("PermissionError: Permission denied")
    assert secret not in json.dumps(out) and str(tmp_path) not in json.dumps(out)
    assert rebuilt


def test_a_step_crash_whose_holder_rebuild_also_fails_still_asks_for_a_restart(
        tmp_path, monkeypatch):
    """The crash report is built after the rebuild, from nothing the step returned, so the
    rebuild's failure must be carried across to it. Both errors carry a path; the report names
    neither."""
    secret = str(tmp_path / "somewhere" / "config.yaml")

    def step_boom(*a, **k):
        raise PermissionError(13, "Permission denied", secret)

    def rebuild_boom():
        raise OSError(2, "No such file or directory", secret)

    monkeypatch.setattr(mcpserver, "setup_save_step", step_boom)
    monkeypatch.setattr(mcpserver, "_holder_sluice", rebuild_boom)
    out = _save([{"kind": "config", "target": "lead_ttl_days", "value": "30"}])
    assert out["outcome"] == "failed"
    assert out["restart_needed"].startswith("FileNotFoundError: restart")
    text = json.dumps(out)
    assert secret not in text and str(tmp_path) not in text
    assert os.path.realpath(tmp_path) not in text


def test_a_default_first_run_create_that_fails_is_reported_not_silent():
    """The default Leads view is created on a first run with no change of its own; when that
    create does not land, `artefacts` and `detail` say so instead of leaving it to doctor."""
    from sluice.core.protocols import LEADS_VIEW_RELPATH
    view = _vault() / LEADS_VIEW_RELPATH
    view.mkdir(parents=True)            # a directory where the view file should go
    out = _save([{"kind": "config", "target": "lead_ttl_days", "value": "30"}])
    assert out["config_written"] is True
    assert out["artefacts"]["profile"]["outcome"] == "written"
    assert out["artefacts"]["view"]["outcome"] == "set_aside"
    assert "view" in out["detail"]
    assert set(_rows(out)) == {"config:lead_ttl_days"}


def test_an_unreadable_note_is_failed_while_another_change_is_written(tmp_path):
    """A note that cannot be read is named by setup_status and its change is set aside, while
    a config change in the same save is written."""
    _existing_hunt()
    (_vault() / CANDIDATE_PROFILE_RELPATH).mkdir(parents=True)
    out = _save([{"kind": "candidate", "target": "cv_email",
                  "value": "ada@example.invalid"},
                 {"kind": "config", "target": "lead_ttl_days", "value": "30"}])
    rows = _rows(out)
    assert rows["candidate:email"]["outcome"] == "set_aside"
    assert "could not be read" in rows["candidate:email"]["reason"]
    assert rows["config:lead_ttl_days"]["outcome"] == "written"
    _no_discovered_path(out, tmp_path)


# ── searches and config shapes ───────────────────────────────────────────────

def test_a_multi_line_search_entry_is_a_structured_outcome_and_writes_nothing():
    from tests.test_onboard_edit import MULTILINE_ENTRY as _SYNTHETIC
    # This row runs the real server, whose source registry decides what a search may target, so
    # it genuinely needs a REGISTERED adapter id; the shared fixture text uses a synthetic one.
    MULTILINE_ENTRY = _SYNTHETIC.replace("example-board", "remoteok")
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    Path(config_file()).write_text(MULTILINE_ENTRY)
    out = _save([{"kind": "search", "target": "remoteok", "label": "Third",
                  "url": "https://example.invalid/c"}])
    assert out["outcome"] == "completed"
    (row,) = out["changes"]
    assert row["outcome"] == "set_aside" and "flow form" in row["reason"]
    assert Path(config_file()).read_text() == MULTILINE_ENTRY


def _search_written(out):
    """The search was written AND the file on disk loads it: read back through the loader,
    never taken from the report."""
    from sluice.core.config import load_config
    (row,) = out["changes"]
    assert row["outcome"] == "written", row
    src = load_config(config_file()).sources["remoteok"]
    assert [list(e[:2]) for e in src.searches] == [["Example", "https://example.invalid/s"]]
    assert src.enabled is True and src.tuning == {}


_SEARCH = [{"kind": "search", "target": "remoteok", "label": "Example",
            "url": "https://example.invalid/s"}]


def test_a_first_run_search_creates_its_source_block_and_is_written():
    out = _save(_SEARCH)
    assert out["config_written"] is True
    _search_written(out)


def test_a_search_on_an_existing_config_with_no_block_for_its_source_is_written():
    _existing_hunt()
    assert "remoteok" not in Path(config_file()).read_text()
    _search_written(_save(_SEARCH))
