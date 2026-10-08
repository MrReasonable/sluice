"""Durable copies of what in-session setup replaces.

`setup_save` hands the replaced text back in its response (`previous`), which restores it only
while the chat lasts; the coach also invites the user to come back later if something looks
wrong. So before `Sluice.apply_setup` replaces a setup note or the config file, the store
(`core/vault.py::Vault.keep_document_copy`) or the config writer
(`core/config.py::keep_config_copy`) keeps the artefact's prior bytes through `write_copy`.

All copies are kept: they are small and they are the user's, and nothing here decides which of
the user's old wording is safe to lose. Nothing reads a copy back automatically either -- a
restore is the user's (or the coach's, from `previous`) to make.

The name is `<prefix><UTC time to the microsecond>-<random suffix><suffix>`. The time makes the
copies of one artefact sort in the order they were taken; the random suffix keeps two copies
taken in the same instant apart, and the create is exclusive besides, so a collision can never
overwrite an earlier copy -- it draws a new suffix instead."""
import os
import secrets
from datetime import datetime, timezone

# Attempts at a fresh suffix before a collision is reported. Six hex digits make a collision
# within one microsecond already improbable; the bound exists so a broken suffix (or a test
# pinning it) fails loudly rather than looping.
_ATTEMPTS = 8


def _now() -> datetime:
    """The clock, its own function so a test can pin two copies to one instant."""
    return datetime.now(timezone.utc)


def _stamp() -> str:
    # UTC, never the host zone: a copy taken across a DST change or by a machine in another
    # zone must still sort in the order it was taken. No colon, which Windows refuses in a name.
    return f"{_now().strftime('%Y%m%dT%H%M%S.%fZ')}-{secrets.token_hex(3)}"


def write_copy(directory: str, prefix: str, suffix: str, data: bytes,
               mode: int | None = None) -> str:
    """Create a new file in `directory` holding exactly `data`, and return its NAME (never a
    path: callers phrase where it went relative to the vault or the config file).

    Exclusive (O_CREAT|O_EXCL), so an existing file is never touched. `mode`, when given, is set
    on the descriptor after the open, because the open's own mode is narrowed by the umask and
    the config's copy must carry the config's exact mode (it may hold a credential). The data is
    fsynced before returning, since the caller replaces the original next and a copy that is
    not yet on disk is no copy at all after a crash.

    A failure after the open removes the partial -- but only while the name still points at the
    file this call created (the inode the open returned), the ownership rule
    `core/config.py::write_config_text` follows. A half-written copy left behind would read as a
    good backup."""
    last = None
    for _ in range(_ATTEMPTS):
        name = f"{prefix}{_stamp()}{suffix}"
        path = os.path.join(directory, name)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                         0o600 if mode is None else mode)
        except FileExistsError as exc:
            last = exc
            continue
        mine = None
        try:
            st = os.fstat(fd)
            mine = (st.st_dev, st.st_ino)
            if mode is not None and hasattr(os, "fchmod"):
                os.fchmod(fd, mode)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            try:
                now = os.lstat(path)
                if (now.st_dev, now.st_ino) == mine:
                    os.unlink(path)
            except OSError:
                pass
            raise
        os.close(fd)
        return name
    raise last
