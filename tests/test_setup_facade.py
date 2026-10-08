import dataclasses
import os
import re
from pathlib import Path

from sluice.core import app as app_mod
from sluice.core.app import Sluice
from sluice.core.paths import config_file
from sluice.core.protocols import CRITERIA_RELPATH, ArtefactWrite, document_sha
from sluice.onboard.plan import build_plan

ROOT = Path(__file__).resolve().parent.parent


def test_the_loader_roster_is_every_top_level_config_loader():
    found = set()
    for py in (ROOT / "sluice").rglob("*.py"):
        found |= set(re.findall(r"^def (load_\w*config)\(", py.read_text(), re.M))
    assert found, "discovery matched nothing"
    assert {fn.__name__ for _, fn in app_mod._config_loaders()} == found


def test_snapshot_reads_config_and_notes_raw_and_names_unreadable_ones(tmp_path):
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    Path(config_file()).write_text("lead_ttl_days: 30\r\n", newline="")
    s = Sluice.from_config_file()
    vault = s.store().dir
    os.makedirs(os.path.join(vault, "Job Applications", "Candidate Profile.md"))
    snap = s.setup_snapshot()
    assert snap.config_text == "lead_ttl_days: 30\r\n"
    assert snap.settings["lead_ttl_days"] == 30 and snap.defaults["lead_ttl_days"] == 0
    assert "candidate" in snap.unreadable and vault not in snap.unreadable["candidate"]
    assert snap.vault_from_env is True and "remoteok" in snap.source_ids


def test_snapshot_without_a_config_file(tmp_path):
    snap = Sluice.from_config_file().setup_snapshot()
    assert snap.config_text is None and snap.notes["profile"] is None


def test_settings_flatten_sources_per_source():
    out = app_mod._config_settings(
        'sources:\n  "remoteok":\n    searches:\n      - ["A", "https://example.invalid/1"]\n')
    assert out["sources.remoteok.searches"] == [["A", "https://example.invalid/1"]]
    assert "sources" not in out


def test_a_loader_refusing_unparsable_yaml_is_a_valueerror_naming_the_loader():
    import pytest
    with pytest.raises(ValueError, match="load_config"):
        app_mod._config_settings("triage:\n  accept_titles: [unclosed\n")


def test_a_loader_error_keeps_the_temp_directory_out_of_its_message():
    import tempfile

    import pytest
    with pytest.raises(ValueError) as ei:
        app_mod._config_settings("triage:\n  accept_titles: [unclosed\n")
    msg = str(ei.value)
    tmp = tempfile.gettempdir()
    assert tmp not in msg and os.path.realpath(tmp) not in msg
    assert "load_config" in msg and "config.yaml" in msg


def _cfg(text):
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    Path(config_file()).write_text(text, newline="")


def test_a_change_to_an_undeclared_setting_is_refused_and_nothing_is_written():
    old = build_plan({}).config_text
    _cfg(old)
    bad = old.replace("# lead_ttl_days:", "lead_ttl_days: 9  #").replace(
        "# min_jd_chars:", "min_jd_chars: 5  #")
    out = Sluice.from_config_file().apply_setup(
        [ArtefactWrite("config", bad, document_sha(old), ("lead_ttl_days",),
                       (("lead_ttl_days", 9),))])
    assert out["config"].status == "set_aside" and "min_jd_chars" in out["config"].reason
    assert Path(config_file()).read_text() == old


def test_backend_must_reach_all_three_stages():
    from sluice.onboard import edit
    old = build_plan({}).config_text
    _cfg(old)
    two = edit.set_key(edit.set_key(old, "triage.backend", '"anthropic"'), "cv.backend",
                       '"anthropic"')
    settings = ("triage.backend", "cv.backend", "track.backend")
    expect = tuple((s, "anthropic") for s in settings)
    out = Sluice.from_config_file().apply_setup(
        [ArtefactWrite("config", two, document_sha(old), settings, expect)])
    assert out["config"].status == "set_aside" and "track.backend" in out["config"].reason


def test_an_answer_equal_to_the_value_in_force_is_written():
    from sluice.onboard import edit
    old = build_plan({}).config_text
    _cfg(old)
    same = edit.set_key(old, "lead_ttl_days", "0")
    out = Sluice.from_config_file().apply_setup(
        [ArtefactWrite("config", same, document_sha(old), ("lead_ttl_days",),
                       (("lead_ttl_days", 0),))])
    assert out["config"].status == "written"


def test_a_stale_config_is_a_conflict():
    _cfg("lead_ttl_days: 1\n")
    out = Sluice.from_config_file().apply_setup(
        [ArtefactWrite("config", "lead_ttl_days: 2\n", document_sha("other\n"),
                       ("lead_ttl_days",), (("lead_ttl_days", 2),))])
    assert out["config"].status == "conflict"


def test_a_config_removed_after_the_version_check_is_a_conflict():
    """te-001: setup_save's version check catches a config removed before it, so this arm is
    reached only in the window between that check and the write. A conflict, never `failed`,
    and nothing is created in the file's place."""
    old = build_plan({}).config_text
    _cfg(old)
    s = Sluice.from_config_file()
    os.remove(config_file())
    new = old.replace("# lead_ttl_days:", "lead_ttl_days: 9  #")
    out = s.apply_setup([ArtefactWrite("config", new, document_sha(old), ("lead_ttl_days",),
                                       (("lead_ttl_days", 9),))])
    assert (out["config"].status, out["config"].reason) == ("conflict",
                                                            "the config file is gone")
    assert not os.path.exists(config_file())


def test_a_first_run_creates_config_then_notes_in_the_new_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_DIR")
    (tmp_path / "empty").mkdir()
    monkeypatch.chdir(tmp_path / "empty")
    vault = tmp_path / "chosen-vault"
    plan = build_plan({"vault_dir": str(vault)})
    out = Sluice.from_config_file().apply_setup([
        ArtefactWrite("config", plan.config_text, None, ("vault_dir",),
                      (("vault_dir", str(vault)),)),
        ArtefactWrite("profile", plan.profile_text, None),
        ArtefactWrite("view", plan.view_text, None)])
    assert {k: v.status for k, v in out.items()} == {"config": "written", "profile": "written",
                                                     "view": "written"}
    assert (vault / CRITERIA_RELPATH).read_text() == plan.profile_text
    assert not (tmp_path / "empty" / "vault").exists()


def test_notes_are_withheld_when_a_first_run_config_is_not_created(tmp_path, monkeypatch):
    planted = "lead_ttl_days: 7\n"
    _cfg(planted)     # appeared between the read and the write
    plan = build_plan({"vault_dir": str(tmp_path / "v")})
    out = Sluice.from_config_file().apply_setup([
        ArtefactWrite("config", plan.config_text, None, ("vault_dir",), ()),
        ArtefactWrite("profile", plan.profile_text, None)])
    assert out["config"].status == "conflict" and out["profile"].status == "set_aside"
    assert Path(config_file()).read_text() == planted


def test_a_first_run_with_vault_dir_env_passes_the_check_end_to_end():
    """Through review.build_writes' REAL output, not a hand-built write: this is the path a
    hand-built row hid (vault_dir missing from the allowed settings)."""
    from sluice.onboard import review
    s = Sluice.from_config_file()
    snap = s.setup_snapshot()
    parsed, _ = review.parse_changes([{"kind": "config", "target": "lead_ttl_days",
                                       "value": "30"}])
    units, _ = review.propose(parsed, snap)
    writes, aside = review.build_writes(units, snap, env_vault=os.environ["VAULT_DIR"])
    out = s.apply_setup(writes)
    assert aside == [] and out["config"].status == "written", out
    assert out["profile"].status == "written" and out["view"].status == "written"


def test_a_config_that_cannot_be_reloaded_after_writing_withholds_the_notes(monkeypatch):
    plan = build_plan({})
    real = Sluice.from_config_file()
    monkeypatch.setattr(Sluice, "from_config_file",
                        classmethod(lambda cls: (_ for _ in ()).throw(ValueError("bad key"))))
    out = real.apply_setup([
        ArtefactWrite("config", plan.config_text, None, ("vault_dir",), ()),
        ArtefactWrite("profile", plan.profile_text, None)])
    assert out["config"].status == "written"
    assert out["profile"].status == "set_aside" and "could not be loaded" in out["profile"].reason


def test_one_note_failing_does_not_stop_another():
    s = Sluice.from_config_file()
    os.makedirs(os.path.join(s.store().dir, "Job Applications", "Role Brief.md"))
    out = s.apply_setup([ArtefactWrite("brief", "x", "0" * 64),
                         ArtefactWrite("profile", "# p\n", None)])
    assert out["brief"].status == "failed" and out["profile"].status == "written"


def test_a_faulty_editor_that_flips_an_unrelated_key_is_caught(monkeypatch):
    from sluice.onboard import edit, review
    old = build_plan({}).config_text
    _cfg(old)
    real_set = edit.set_key
    monkeypatch.setattr(edit, "set_key", lambda text, dotted, rendered: real_set(
        real_set(text, dotted, rendered), "min_jd_chars", "5"))
    s = Sluice.from_config_file()
    snap = s.setup_snapshot()
    parsed, _ = review.parse_changes([{"kind": "config", "target": "lead_ttl_days",
                                       "value": "30"}])
    units, _ = review.propose(parsed, snap)
    writes, _ = review.build_writes(units, snap)
    out = s.apply_setup(writes)
    assert out["config"].status == "set_aside" and "min_jd_chars" in out["config"].reason
    assert Path(config_file()).read_text() == old


def test_snapshot_keeps_a_dotted_source_id_whole(monkeypatch):
    """The snapshot slices `sources.` and `.searches` off; splitting on "." would keep only
    the id's first segment."""
    real = app_mod._config_settings
    monkeypatch.setattr(app_mod, "_config_settings", lambda text: {
        **real(text), "sources.example.dotted.searches": [["A", "https://example.invalid/1"]]})
    snap = Sluice.from_config_file().setup_snapshot()
    assert snap.searches["example.dotted"] == [["A", "https://example.invalid/1"]]
    assert "example" not in snap.searches


def test_a_note_whose_store_write_abstains_is_a_conflict_and_is_untouched():
    """The store-level CAS the consent guarantee rests on: write_document returning "" (stale
    sha on an update, an existing note on a create) must report `conflict`, never `written`."""
    s = Sluice.from_config_file()
    note = Path(s.store().dir) / CRITERIA_RELPATH
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Judging Profile\n\nedited in Obsidian\n")
    out = s.apply_setup([ArtefactWrite("profile", "# new\n", document_sha("# shown\n"))])
    assert out["profile"].status == "conflict"
    assert "changed after setup_status read it" in out["profile"].reason
    out = s.apply_setup([ArtefactWrite("profile", "# new\n", None)])
    assert out["profile"].status == "conflict"
    # inv-004: an abstained CREATE says the note is already there, never that it "changed
    # after setup_status read it" -- on a first run no old text was read to have changed.
    assert "already exists in the chosen vault" in out["profile"].reason
    assert note.read_text() == "# Judging Profile\n\nedited in Obsidian\n"


def test_a_hand_written_four_space_block_is_never_left_broken():
    """edit.py inserts a missing key at two spaces; in a block indented by four that is a
    file no loader reads. The config check must catch it before the write."""
    from sluice.onboard import review
    hand = 'triage:\n    resolve_backend: ""\n'
    _cfg(hand)
    s = Sluice.from_config_file()
    snap = s.setup_snapshot()
    parsed, _ = review.parse_changes([{"kind": "config", "target": "accept_titles",
                                       "value": "Example Title"}])
    units, aside = review.propose(parsed, snap)
    assert aside == [] and len(units) == 1
    writes, aside = review.build_writes(units, snap)
    assert aside == [] and [w.artefact for w in writes] == ["config"]
    out = s.apply_setup(writes)
    assert out["config"].status == "failed" and "load_config" in out["config"].reason
    assert Path(config_file()).read_text() == hand


def test_snapshot_names_an_unreadable_config_without_its_path(tmp_path):
    s = Sluice.from_config_file()
    os.makedirs(config_file())          # a directory where the file should be: unreadable
    snap = s.setup_snapshot()
    assert snap.config_text is None and "config" in snap.unreadable
    from sluice.onboard.review import status_view
    text = repr(snap.unreadable) + repr(status_view(snap))
    assert str(tmp_path) not in text and os.path.realpath(tmp_path) not in text
    assert status_view(snap)["artefacts"]["config"] == "unreadable"


def test_without_path_replaces_the_real_spelling_behind_a_symlink(tmp_path):
    """macOS symlinks its temp root, so PyYAML can report the REAL path of a file opened by its
    link; both spellings must go, whichever the host uses."""
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    path = str(tmp_path / "link" / "config.yaml")
    resolved = os.path.realpath(path)
    assert resolved != path, "the symlink did not make two spellings; this row tests nothing"
    msg = app_mod._without_path(f'in "{resolved}", line 2; also "{path}"', path)
    assert msg == 'in "config.yaml", line 2; also "config.yaml"'


def test_a_search_creating_its_source_block_may_not_set_a_non_default_enabled_or_tuning():
    """A search that creates a source's block declares that source's `enabled` and `tuning` at
    their DEFAULTS, so the check stays exact: the same declared write carrying any other value
    for either is refused, and nothing is written."""
    from sluice.onboard import review
    old = build_plan({}).config_text
    _cfg(old)
    s = Sluice.from_config_file()
    snap = s.setup_snapshot()
    parsed, _ = review.parse_changes([{"kind": "search", "target": "remoteok",
                                       "label": "Example", "url": "https://example.invalid/s"}])
    units, _ = review.propose(parsed, snap)
    (w,), _ = review.build_writes(units, snap)
    assert '"remoteok":\n    searches:\n' in w.text
    for extra in ("    enabled: false\n", "    tuning: {page_size: 5}\n"):
        bad = w.text.replace('"remoteok":\n', '"remoteok":\n' + extra)
        assert bad != w.text
        out = Sluice.from_config_file().apply_setup([dataclasses.replace(w, text=bad)])
        key = extra.split(":")[0].strip()
        assert out["config"].status == "set_aside", extra
        assert f"sources.remoteok.{key}" in out["config"].reason
        assert Path(config_file()).read_text() == old
    out = s.apply_setup([w])
    assert out["config"].status == "written"
