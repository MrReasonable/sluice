"""#16 RMW-race safety: content-CAS + atomic replace + bounded re-apply.

Race simulation is deterministic and threadless -- `racing_read` interposes the
module-level `_read` to land one out-of-band edit in the capture->commit window.
"""
import os
import stat

import pytest

from sluice.core.vault import _atomic_write, _cas_write
from sluice.core.protocols import VaultConflict
from tests.conftest import racing_read


def test_atomic_write_replaces_contents(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("old", encoding="utf-8")
    _atomic_write(str(p), "new")
    assert p.read_text(encoding="utf-8") == "new"
    # no temp siblings left behind
    assert [f.name for f in tmp_path.iterdir()] == ["n.md"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_atomic_write_preserves_mode(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("old", encoding="utf-8")
    os.chmod(p, 0o640)
    _atomic_write(str(p), "new")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o640


def test_cas_write_commits_when_unchanged(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("a", encoding="utf-8")
    assert _cas_write(str(p), lambda t: t + "b") is True
    assert p.read_text(encoding="utf-8") == "ab"


def test_cas_write_noop_returns_false(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("a", encoding="utf-8")
    assert _cas_write(str(p), lambda t: t) is False  # identity transform -> no write


def test_cas_write_self_heals_when_file_changes_under_it(tmp_path, monkeypatch):
    p = tmp_path / "n.md"
    p.write_text("base\n", encoding="utf-8")
    # Racer appends a line once, in the capture->commit window of our first attempt.
    racing_read(monkeypatch, str(p), lambda: p.write_text("base\nRACER\n", encoding="utf-8"))
    # Our edit appends OURS; re-derived onto the racer's content, both survive.
    assert _cas_write(str(p), lambda t: t + "OURS\n") is True
    body = p.read_text(encoding="utf-8")
    assert "RACER" in body and body.endswith("OURS\n")


def test_cas_write_raises_on_sustained_race(tmp_path, monkeypatch):
    p = tmp_path / "n.md"
    p.write_text("v0\n", encoding="utf-8")
    counter = {"n": 0}
    def churn():
        counter["n"] += 1
        p.write_text(f"v{counter['n']}\n", encoding="utf-8")  # unique content every read
    racing_read(monkeypatch, str(p), churn, once=False)
    with pytest.raises(VaultConflict):
        _cas_write(str(p), lambda t: t + "OURS\n")
