"""The writer for a config file replaced under a freshness check, today sluice's own config
(`core/config.py::write_config_text`).
Not the vault's writer -- `core/vault.py::_atomic_write` replaces a symlink itself rather than its
target, which is right for a note and wrong for a config linked into a dotfiles repository.

Two arms, chosen by `fresh`. `None` creates exclusively -- never-clobber is a property of the
open, not of a check before it. A callable decides whether the CURRENT bytes are still the ones
the caller meant to replace (for the config, a sha of the text the user was shown); only then is
the file replaced, atomically, keeping its mode.

A symlink is resolved and its TARGET replaced in the target's own directory, so a link into a
dotfiles repository survives and the temp file and the target share a filesystem, which
`os.replace` needs. `core/vault.py::_atomic_write` replaces the link itself, which is why this is
not that.

The replace is best-effort, the residual `core/vault.py::_cas_write` states: the lock is
in-process only, so an outside editor that writes between the freshness check and the replace is
overwritten. No portable atomic compare-and-replace exists; the window is the read-check-replace,
not a human's review time."""

import os
import stat
import tempfile
import threading
from collections.abc import Callable

# One lock per resolved file: two threads racing a replace would otherwise both pass the
# freshness check against the same bytes and the second replace would clobber the first. Every
# reader that must see a file between someone else's check and replace (a backup copy) takes it
# too, which is why it is exported rather than private.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def file_lock(path: str) -> threading.Lock:
    """The process-wide lock for `path`, keyed on its resolved form, so a link and its target
    share one lock whichever spelling the caller holds."""
    real = os.path.realpath(path)
    with _locks_guard:
        return _locks.setdefault(real, threading.Lock())


def replace_if(path: str, data: bytes, *, fresh: Callable[[bytes], bool] | None,
               tmp_prefix: str) -> bool:
    """Write `data` to `path`. `fresh=None`: create exclusively, parent directory first.
    Otherwise: replace only when `fresh(current_bytes)` is true. Returns False whenever it
    wrote nothing; raises on an I/O failure, and on whatever `fresh` raises, having written
    nothing and left no temp file.

    `fresh` runs while the path's lock is held, and the lock is not reentrant: a `fresh` that
    writes to the same path or takes its lock (`replace_if`, `file_lock`, a backup copy) waits
    on itself for ever, with no error."""
    real = os.path.realpath(path)
    with file_lock(real):
        if fresh is None:
            return _create(real, data)
        try:
            with open(real, "rb") as f:
                current = f.read()
        except FileNotFoundError:
            return False
        if not fresh(current):
            return False
        # By path, after `fresh`, not from the descriptor read above: a file deleted in between
        # makes this raise and nothing is written. A mode taken from that descriptor would let
        # the replace go ahead and re-create a file someone just removed.
        mode = stat.S_IMODE(os.stat(real).st_mode)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(real) or ".", prefix=tmp_prefix,
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp, mode)
            os.replace(tmp, real)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True


def _create(real: str, data: bytes) -> bool:
    parent = os.path.dirname(real)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        f = open(real, "xb")
    except FileExistsError:
        return False
    # Our exclusive open made the file, so a failure after it leaves a partial that is ours to
    # remove -- whatever the exception type. But ownership at CREATE time is not ownership at
    # CLEANUP time: another process may have replaced the pathname since, and unlinking by name
    # would delete ITS file. So the open handle's identity is kept, and the name is removed only
    # while it still points at that file. A replace landing between that check and the unlink is
    # the residual no portable stdlib call closes.
    mine = None
    try:
        try:
            st = os.fstat(f.fileno())
            mine = (st.st_dev, st.st_ino)
            f.write(data)
        finally:
            f.close()
    except BaseException:
        try:
            now = os.lstat(real)
            if (now.st_dev, now.st_ino) == mine:
                os.unlink(real)
        except OSError:
            pass
        raise
    return True
