"""In-session setup keeps a durable copy of every setup artefact a save REPLACES.

`previous` in setup_save's report restores a value within the chat; once the chat ends it is
gone, while the coach invites the user to come back later if something looks wrong. So before
`Sluice.apply_setup` replaces a setup note or the config, it keeps the artefact's prior bytes:
a note's under the vault's `_setup_backups/` folder (`core/vault.py::SETUP_BACKUP_RELDIR`), the
config's beside the resolved config file. Every row reads the result back from disk."""
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sluice.core import backup
from sluice.core.app import Sluice
from sluice.core.paths import config_file
from sluice.core.protocols import (CANDIDATE_PROFILE_RELPATH, CRITERIA_RELPATH,
                                   ArtefactWrite, document_sha)
from sluice.core.vault import SETUP_BACKUP_RELDIR
from sluice.onboard.plan import build_plan

# CRLF and a non-ASCII character: a copy taken through a text decode/encode with the default
# newline would not be byte-identical, and that is the property "exact copy" claims.
OLD_NOTE = "# Judging Profile\r\n\r\nOld words café.\r\n".encode("utf-8")


def _vault() -> Path:
    return Path(os.environ["VAULT_DIR"])


def _backups() -> Path:
    return _vault().joinpath(*SETUP_BACKUP_RELDIR.split("/"))


def _seed_note(rel=CRITERIA_RELPATH, data=OLD_NOTE) -> str:
    p = _vault().joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return document_sha(data.decode("utf-8"))


def _seed_config(text=None, *, mode=0o640, at=None) -> tuple[str, str]:
    text = build_plan({}).config_text if text is None else text
    path = at or config_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(text, newline="")
    os.chmod(path, mode)
    return text, document_sha(text)


def _ttl_write(old, sha, ttl=30):
    new = old.replace("# lead_ttl_days:", f"lead_ttl_days: {ttl}  #")
    assert new != old
    return ArtefactWrite("config", new, sha, ("lead_ttl_days",), (("lead_ttl_days", ttl),))


def _config_copies(directory) -> list[str]:
    return sorted(n for n in os.listdir(directory) if n.endswith(".bak"))


# ── notes ────────────────────────────────────────────────────────────────────

def test_a_replaced_note_leaves_an_exact_byte_copy_and_takes_the_new_text():
    sha = _seed_note()
    out = Sluice.from_config_file().apply_setup(
        [ArtefactWrite("profile", "# Judging Profile\n\nNew words.\n", sha)])
    assert out["profile"].status == "written", out
    assert (_vault() / CRITERIA_RELPATH).read_text() == "# Judging Profile\n\nNew words.\n"
    copies = sorted(_backups().iterdir())
    assert len(copies) == 1
    assert copies[0].read_bytes() == OLD_NOTE
    # The outcome names the copy by its key INSIDE the vault, never by a filesystem path.
    assert out["profile"].kept == f"{SETUP_BACKUP_RELDIR}/{copies[0].name}"
    assert not os.path.isabs(out["profile"].kept)
    assert copies[0].name.startswith("Judging Profile ") and copies[0].suffix == ".md"


def test_a_first_run_create_keeps_no_copy():
    out = Sluice.from_config_file().apply_setup(
        [ArtefactWrite("profile", "# Judging Profile\n", None)])
    assert out["profile"].status == "written" and out["profile"].kept == ""
    assert not _backups().exists()


def test_a_note_whose_copy_cannot_be_written_is_not_replaced(monkeypatch):
    sha = _seed_note()

    def refuse(*_a, **_k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(backup, "write_copy", refuse)
    out = Sluice.from_config_file().apply_setup(
        [ArtefactWrite("profile", "# Judging Profile\n\nNew words.\n", sha)])
    assert out["profile"].status == "failed", out
    assert (_vault() / CRITERIA_RELPATH).read_bytes() == OLD_NOTE
    assert "copy" in out["profile"].reason
    assert str(_vault()) not in out["profile"].reason


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="needs POSIX permissions that bind the caller")
def test_an_unwritable_backup_folder_leaves_the_note_unreplaced():
    sha = _seed_note()
    _backups().mkdir(parents=True)
    os.chmod(_backups(), 0o500)
    try:
        out = Sluice.from_config_file().apply_setup(
            [ArtefactWrite("profile", "# Judging Profile\n\nNew words.\n", sha)])
    finally:
        os.chmod(_backups(), 0o700)
    assert out["profile"].status == "failed", out
    assert (_vault() / CRITERIA_RELPATH).read_bytes() == OLD_NOTE
    assert list(_backups().iterdir()) == []


def test_a_note_changed_since_it_was_read_is_neither_copied_nor_replaced():
    _seed_note()
    stale = document_sha("something the coach read earlier")
    out = Sluice.from_config_file().apply_setup(
        [ArtefactWrite("profile", "# Judging Profile\n\nNew words.\n", stale)])
    assert out["profile"].status == "conflict"
    assert (_vault() / CRITERIA_RELPATH).read_bytes() == OLD_NOTE
    assert not _backups().exists() or list(_backups().iterdir()) == []


def test_two_saves_in_the_same_instant_keep_two_copies(monkeypatch):
    """The clock is pinned, so the two names can differ only by the unique suffix."""
    fixed = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
    monkeypatch.setattr(backup, "_now", lambda: fixed)
    first = OLD_NOTE
    sha = _seed_note(data=first)
    s = Sluice.from_config_file()
    assert s.apply_setup([ArtefactWrite("profile", "second", sha)])["profile"].status == "written"
    assert s.apply_setup([ArtefactWrite("profile", "third", document_sha("second"))])[
        "profile"].status == "written"
    copies = sorted(_backups().iterdir())
    assert len(copies) == 2
    assert {c.read_bytes() for c in copies} == {first, b"second"}


def test_copies_sort_by_the_time_they_were_taken(monkeypatch):
    times = iter([datetime(2026, 1, 2, 3, 4, 5, 900000, tzinfo=timezone.utc),
                  datetime(2026, 1, 2, 3, 4, 6, 100000, tzinfo=timezone.utc)])
    monkeypatch.setattr(backup, "_now", lambda: next(times))
    sha = _seed_note()
    s = Sluice.from_config_file()
    s.apply_setup([ArtefactWrite("profile", "second", sha)])
    s.apply_setup([ArtefactWrite("profile", "third", document_sha("second"))])
    copies = sorted(_backups().iterdir())
    assert [c.read_bytes() for c in copies] == [OLD_NOTE, b"second"]


def test_the_backup_folder_is_never_read_as_a_lead_or_a_setup_note():
    """Copies of the setup notes, the Leads view among them, sit in the vault after a save.
    None of them may surface as a lead, and the setup notes read back as the NEW text."""
    lead_shaped = (b"---\ncompany: Example Co\nrole: Example Role\nstatus: new\n"
                   b"base: \"[[Job Leads.base]]\"\n---\n\nbody\n")
    sha_p = _seed_note(CRITERIA_RELPATH, lead_shaped)
    sha_c = _seed_note(CANDIDATE_PROFILE_RELPATH, lead_shaped)
    s = Sluice.from_config_file()
    out = s.apply_setup([ArtefactWrite("profile", "# Judging Profile\n", sha_p),
                         ArtefactWrite("candidate", "# Candidate Profile\n", sha_c)])
    assert {o.status for o in out.values()} == {"written"}
    assert len(list(_backups().iterdir())) == 2
    store = s.store()
    assert store.read_leads() == []
    snap = s.setup_snapshot()
    assert snap.notes["profile"] == "# Judging Profile\n"
    assert snap.notes["candidate"] == "# Candidate Profile\n"


# ── config ───────────────────────────────────────────────────────────────────

def test_a_replaced_config_leaves_an_exact_copy_with_the_configs_mode():
    # A umask that would strip the group bit: the copy's mode must come from the config, not
    # from whatever the open's mode survives the umask as.
    old, sha = _seed_config(mode=0o640)
    prior = os.umask(0o077)
    try:
        out = Sluice.from_config_file().apply_setup([_ttl_write(old, sha)])
    finally:
        os.umask(prior)
    assert out["config"].status == "written", out
    assert "lead_ttl_days: 30" in Path(config_file()).read_text()
    d = os.path.dirname(config_file())
    copies = _config_copies(d)
    assert len(copies) == 1
    copy = os.path.join(d, copies[0])
    assert Path(copy).read_bytes() == old.encode("utf-8")
    assert stat.S_IMODE(os.stat(copy).st_mode) == 0o640
    assert out["config"].kept == copies[0]
    assert copies[0].startswith(os.path.basename(config_file()) + ".")


def test_a_symlinked_config_is_copied_beside_its_real_file(tmp_path):
    real = str(tmp_path / "dotfiles" / "sluice.yaml")
    old, sha = _seed_config(mode=0o600, at=real)
    os.makedirs(os.path.dirname(config_file()), exist_ok=True)
    os.symlink(real, config_file())
    out = Sluice.from_config_file().apply_setup([_ttl_write(old, sha)])
    assert out["config"].status == "written", out
    assert os.path.islink(config_file())
    copies = _config_copies(os.path.dirname(real))
    assert len(copies) == 1 and copies[0].startswith("sluice.yaml.")
    assert Path(os.path.dirname(real), copies[0]).read_bytes() == old.encode("utf-8")
    assert stat.S_IMODE(os.stat(os.path.join(os.path.dirname(real), copies[0])).st_mode) == 0o600
    assert _config_copies(os.path.dirname(config_file())) == []


def test_a_config_whose_copy_cannot_be_written_is_not_replaced(monkeypatch):
    old, sha = _seed_config()

    def refuse(*_a, **_k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(backup, "write_copy", refuse)
    out = Sluice.from_config_file().apply_setup([_ttl_write(old, sha)])
    assert out["config"].status == "failed", out
    assert Path(config_file()).read_text() == old
    assert "copy" in out["config"].reason
    assert os.path.dirname(config_file()) not in out["config"].reason


def test_a_first_run_config_create_keeps_no_copy():
    text = build_plan({}).config_text
    out = Sluice.from_config_file().apply_setup([ArtefactWrite("config", text, None)])
    assert out["config"].status == "written", out
    assert out["config"].kept == ""
    assert _config_copies(os.path.dirname(config_file())) == []


def test_two_config_saves_in_the_same_instant_keep_two_copies(monkeypatch):
    fixed = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
    monkeypatch.setattr(backup, "_now", lambda: fixed)
    old, sha = _seed_config()
    s = Sluice.from_config_file()
    first = _ttl_write(old, sha, 30)
    assert s.apply_setup([first])["config"].status == "written"
    second = ArtefactWrite("config", first.text.replace("lead_ttl_days: 30", "lead_ttl_days: 9"),
                           document_sha(first.text), ("lead_ttl_days",), (("lead_ttl_days", 9),))
    assert s.apply_setup([second])["config"].status == "written"
    d = os.path.dirname(config_file())
    assert {Path(d, c).read_text() for c in _config_copies(d)} == {old, first.text}
