import os
import stat

import pytest

from sluice.core.config import write_config_text
from sluice.core.protocols import document_sha


def test_creates_exclusively_with_its_parent_directory(tmp_path):
    p = tmp_path / "nested" / "config.yaml"
    assert write_config_text(str(p), "a: 1\n")
    assert p.read_text() == "a: 1\n"
    assert not write_config_text(str(p), "a: 2\n")
    assert p.read_text() == "a: 1\n"


def test_replaces_only_when_the_sha_matches_and_keeps_the_mode(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("a: 1\n")
    os.chmod(p, 0o640)
    assert not write_config_text(str(p), "a: 2\n", expect_sha=document_sha("other\n"))
    assert p.read_text() == "a: 1\n"
    assert write_config_text(str(p), "a: 2\n", expect_sha=document_sha("a: 1\n"))
    assert p.read_text() == "a: 2\n"
    assert stat.S_IMODE(p.stat().st_mode) == 0o640


def test_writes_through_a_symlink_and_keeps_the_link(tmp_path):
    target = tmp_path / "dotfiles" / "config.yaml"
    target.parent.mkdir()
    target.write_text("a: 1\n")
    link = tmp_path / "config.yaml"
    link.symlink_to(target)
    assert write_config_text(str(link), "a: 2\n", expect_sha=document_sha("a: 1\n"))
    assert link.is_symlink() and target.read_text() == "a: 2\n"


def test_update_of_a_missing_file_abstains(tmp_path):
    assert not write_config_text(str(tmp_path / "none.yaml"), "a: 1\n", expect_sha="0" * 64)


def test_unencodable_text_raises_and_creates_nothing(tmp_path):
    p = tmp_path / "config.yaml"
    # Built with chr() so no raw surrogate or escape sequence has to survive an editor.
    bad = "a: " + chr(0xD800) + "\n"
    with pytest.raises(UnicodeEncodeError):
        write_config_text(str(p), bad)
    assert not p.exists()
    assert write_config_text(str(p), "a: 1\n")


class _FailingAfterReplace:
    """A created file whose write fails AFTER another process replaced the pathname: the
    window between the exclusive create and the cleanup that removes a partial."""

    def __init__(self, real, path, replacement):
        self._real, self._path, self._replacement = real, path, replacement

    def fileno(self):
        return self._real.fileno()

    def write(self, data):
        other = self._path.with_name("other.tmp")
        other.write_text(self._replacement)
        os.replace(other, self._path)          # someone else's config lands at the name
        raise OSError("disk full")

    def close(self):
        self._real.close()


@pytest.mark.parametrize("replaced", [True, False], ids=["replaced", "still-ours"])
def test_a_failed_create_removes_only_the_file_it_created(tmp_path, monkeypatch, replaced):
    """CodeRabbit: the cleanup unlinked by NAME, so a config another process had put at the
    path between the create and the failure was deleted. It is kept; the control shows a
    partial that is still ours is still removed."""
    import builtins

    from sluice.core import atomicfile
    p = tmp_path / "config.yaml"

    def fake_open(path, mode="r", *a, **kw):
        f = builtins.open(path, mode, *a, **kw)
        if mode != "xb":
            return f
        if not replaced:
            class _Fails:
                fileno, close = f.fileno, f.close

                def write(self, data):
                    raise OSError("disk full")
            return _Fails()
        return _FailingAfterReplace(f, p, "theirs: 1\n")

    monkeypatch.setattr(atomicfile, "open", fake_open, raising=False)
    with pytest.raises(OSError, match="disk full"):
        write_config_text(str(p), "a: 1\n")
    if replaced:
        assert p.read_text() == "theirs: 1\n"
    else:
        assert not p.exists()


def test_a_non_utf8_config_raises_on_replace_and_is_left_alone(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b"a: \xff\n")
    with pytest.raises(UnicodeDecodeError):
        write_config_text(str(p), "a: 2\n", expect_sha="0" * 64)
    assert p.read_bytes() == b"a: \xff\n"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["config.yaml"]


def test_an_unencodable_text_raises_even_when_the_sha_is_stale(tmp_path):
    """The encode ruling: raised, not reported as stale, and nothing written."""
    p = tmp_path / "config.yaml"
    p.write_text("a: 1\n")
    bad = "a: " + chr(0xD800) + "\n"
    with pytest.raises(UnicodeEncodeError):
        write_config_text(str(p), bad, expect_sha=document_sha("other\n"))
    assert p.read_text() == "a: 1\n"


def test_the_copy_and_the_replace_take_one_lock(tmp_path, monkeypatch):
    """`keep_config_copy` reads the bytes a replace is about to supersede; under a different
    lock it could copy a file mid-replace. Both must take the lock the shared writer takes --
    through a SYMLINKED config, so a lock keyed on the unresolved spelling would differ."""
    from sluice.core import atomicfile
    from sluice.core import config as config_mod
    # The name config.py calls must BE the shared writer's, not a private lock of its own that
    # the patch below would otherwise hide.
    assert config_mod.file_lock is atomicfile.file_lock
    target = tmp_path / "dotfiles" / "config.yaml"
    target.parent.mkdir()
    target.write_text("a: 1\n")
    p = tmp_path / "config.yaml"
    p.symlink_to(target)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    taken = []
    real_lock = atomicfile.file_lock

    def recording(real):
        lock = real_lock(real)
        taken.append(lock)
        return lock

    monkeypatch.setattr(atomicfile, "file_lock", recording)
    monkeypatch.setattr(config_mod, "file_lock", recording, raising=False)
    assert config_mod.keep_config_copy(str(p), document_sha("a: 1\n"))
    assert write_config_text(str(p), "a: 2\n", expect_sha=document_sha("a: 1\n"))
    assert len(taken) == 2 and taken[0] is taken[1]
