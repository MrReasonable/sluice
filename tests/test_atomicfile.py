import os
import stat
import threading

import pytest

from sluice.core import atomicfile
from sluice.core.atomicfile import file_lock, replace_if


def _same(expected: bytes):
    return lambda current: current == expected


def test_create_is_exclusive_and_makes_the_parent(tmp_path):
    p = tmp_path / "nested" / "f.json"
    assert replace_if(str(p), b"one", fresh=None, tmp_prefix=".t-")
    assert p.read_bytes() == b"one"
    assert not replace_if(str(p), b"two", fresh=None, tmp_prefix=".t-")
    assert p.read_bytes() == b"one"


def test_replace_needs_fresh_to_agree_and_keeps_the_mode(tmp_path):
    p = tmp_path / "f.json"
    p.write_bytes(b"one")
    os.chmod(p, 0o600)
    assert not replace_if(str(p), b"two", fresh=_same(b"other"), tmp_prefix=".t-")
    assert p.read_bytes() == b"one"
    assert replace_if(str(p), b"two", fresh=_same(b"one"), tmp_prefix=".t-")
    assert p.read_bytes() == b"two"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_replace_of_a_missing_file_abstains(tmp_path):
    p = tmp_path / "none.json"
    assert not replace_if(str(p), b"x", fresh=lambda _: True, tmp_prefix=".t-")
    assert not p.exists()


def test_fresh_is_handed_the_raw_bytes(tmp_path):
    p = tmp_path / "f.json"
    raw = b"\xef\xbb\xbfa\r\n\xff"            # BOM, CRLF and a non-UTF-8 byte, unaltered
    p.write_bytes(raw)
    seen = []
    replace_if(str(p), b"x", fresh=lambda b: seen.append(b) or False, tmp_prefix=".t-")
    assert seen == [raw]


def test_the_temp_file_is_made_in_the_targets_directory_and_the_link_survives(
        tmp_path, monkeypatch):
    """The link and its target sit in DIFFERENT directories, so a temp made beside the link
    would be a cross-directory os.replace -- and on a second filesystem, a failed one."""
    target = tmp_path / "dotfiles" / "f.json"
    target.parent.mkdir()
    target.write_bytes(b"one")
    link_dir = tmp_path / "home"
    link_dir.mkdir()
    link = link_dir / "f.json"
    link.symlink_to(target)
    dirs = []
    real_mkstemp = atomicfile.tempfile.mkstemp

    def recording_mkstemp(*a, **kw):
        dirs.append(kw.get("dir"))
        return real_mkstemp(*a, **kw)

    monkeypatch.setattr(atomicfile.tempfile, "mkstemp", recording_mkstemp)
    assert replace_if(str(link), b"two", fresh=_same(b"one"), tmp_prefix=".t-")
    assert dirs == [str(target.parent.resolve())]
    assert link.is_symlink() and target.read_bytes() == b"two"


def test_a_dangling_link_is_created_through(tmp_path):
    target = tmp_path / "dotfiles" / "f.json"
    link = tmp_path / "f.json"
    link.symlink_to(target)
    assert replace_if(str(link), b"one", fresh=None, tmp_prefix=".t-")
    assert link.is_symlink() and target.read_bytes() == b"one"


def test_a_failed_replace_leaves_the_original_and_no_temp(tmp_path, monkeypatch):
    p = tmp_path / "f.json"
    p.write_bytes(b"one")

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(atomicfile.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        replace_if(str(p), b"two", fresh=_same(b"one"), tmp_prefix=".t-")
    assert p.read_bytes() == b"one"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["f.json"]


def test_a_raising_fresh_releases_the_lock_and_leaves_no_temp(tmp_path):
    p = tmp_path / "f.json"
    p.write_bytes(b"one")

    def boom(_):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")

    with pytest.raises(UnicodeDecodeError):
        replace_if(str(p), b"two", fresh=boom, tmp_prefix=".t-")
    assert not file_lock(str(p)).locked()
    assert sorted(x.name for x in tmp_path.iterdir()) == ["f.json"]
    assert replace_if(str(p), b"two", fresh=_same(b"one"), tmp_prefix=".t-")


def test_the_lock_is_one_object_per_resolved_path(tmp_path):
    target = tmp_path / "f.json"
    link = tmp_path / "link.json"
    link.symlink_to(target)
    assert file_lock(str(link)) is file_lock(str(target))
    assert file_lock(str(tmp_path / "a")) is not file_lock(str(tmp_path / "b"))


def test_the_lock_serialises_two_writers(tmp_path):
    """Two threads each replace only if the file still holds the ORIGINAL bytes. Inside `fresh`
    each waits on a two-party barrier: it trips only if BOTH threads are inside `fresh` at once.
    Under the lock the second cannot enter, so the barrier times out for the first and breaks
    for the second -- no overlap -- and exactly one write lands. Without the lock both enter,
    the barrier trips, both pass the check against the same bytes and the second clobbers the
    first."""
    p = tmp_path / "f.json"
    p.write_bytes(b"orig")
    together = threading.Barrier(2, timeout=0.2)
    overlaps = []
    start = threading.Barrier(2)
    results = []

    def fresh(current):
        try:
            together.wait()
            overlaps.append(True)
        except threading.BrokenBarrierError:
            overlaps.append(False)
        return current == b"orig"

    def writer(data):
        start.wait()
        results.append(replace_if(str(p), data, fresh=fresh, tmp_prefix=".t-"))

    threads = [threading.Thread(target=writer, args=(d,)) for d in (b"a", b"b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlaps == [False, False]
    assert sorted(results) == [False, True]


class _FailingAfterReplace:
    """A created file whose write fails AFTER another process replaced the pathname."""

    def __init__(self, real, path, replacement):
        self._real, self._path, self._replacement = real, path, replacement

    def fileno(self):
        return self._real.fileno()

    def write(self, data):
        other = self._path.with_name("other.tmp")
        other.write_bytes(self._replacement)
        os.replace(other, self._path)
        raise OSError("disk full")

    def close(self):
        self._real.close()


@pytest.mark.parametrize("replaced", [True, False], ids=["replaced", "still-ours"])
def test_a_failed_create_removes_only_the_file_it_created(tmp_path, monkeypatch, replaced):
    import builtins
    p = tmp_path / "f.json"

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
        return _FailingAfterReplace(f, p, b"theirs")

    monkeypatch.setattr(atomicfile, "open", fake_open, raising=False)
    with pytest.raises(OSError, match="disk full"):
        replace_if(str(p), b"mine", fresh=None, tmp_prefix=".t-")
    if replaced:
        assert p.read_bytes() == b"theirs"
    else:
        assert not p.exists()
