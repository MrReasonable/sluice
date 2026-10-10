# Shared compare-and-set writer (PR 1 of `mcp install`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract `core/config.py::write_config_text`'s create-and-replace logic into one general
writer, `core/atomicfile.py::replace_if`, with no behaviour change, and move the "mcp not
installed" message into a leaf module, so PR 2 (`job-sluice mcp install`) has both without a
second writer.

**Architecture:** `core/atomicfile.py` owns the per-path in-process lock table, the exclusive
create (with its inode-checked cleanup) and the freshness-checked replace (symlink target
replaced in the target's own directory, temp file plus `os.replace`, mode kept). Freshness is a
caller-supplied `fresh(current_bytes) -> bool`, so `write_config_text` keeps its text sha and PR 2
can compare raw bytes. `core/config.py::write_config_text` and `keep_config_copy` become callers.
`sluice/mcpextra.py` holds one string constant and imports nothing.

**Tech Stack:** Python 3.12+, stdlib only (`os`, `stat`, `tempfile`, `threading`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-10-mcp-install-design.md` (sections "Write safety" — the
writer paragraph — and "Delivery", PR 1).

## Global Constraints

- `sluice/` is standard-library only; `core/atomicfile.py` imports nothing from `sluice`.
- No behaviour change: `tests/test_config_write.py` and the `setup_save` tests in
  `tests/test_mcpserver.py` and `tests/functional/test_mcp_setup_save.py` pass, with assertions
  unchanged (one monkeypatch TARGET moves; see Task 2's ruling).
- Comments explain WHY; match the surrounding density. Never cite a line number in a comment or
  docstring; cite `file.py::symbol` (`tests/test_citation_drift.py` enforces it).
- Conventional commits; every commit message ends with the line
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.
- Run tests with `.venv/bin/python -m pytest` from the worktree root (create the venv first if missing:
  `uv venv .venv && uv pip install --python .venv/bin/python -e ".[test]"`).
- Before any mutation witness: commit, then
  `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`;
  mutate by DELETING or MOVING, never adding; restore with `git checkout -- <file>`.

## Review Focus

1. A config file holding non-UTF-8 bytes when setup replaces it: today `write_config_text`
   raises `UnicodeDecodeError` and writes nothing; it must still (Task 2, Step 1).
2. A `fresh` callback that raises: the lock must be released and no temp file left, so the next
   write to that path works (Task 1).
3. A dangling symlink at the config path on create: today the target is created through the link
   and the link survives; it must still (Task 1).
4. `keep_config_copy` and `write_config_text` racing on one file: both must take the SAME lock
   object for the resolved path, or a copy can be taken of bytes the replace is mid-way through
   superseding (Task 2).
5. A replace whose `os.replace` fails: the original is untouched and no `.tmp` is left beside it
   (Task 1).

---

### Task 1: `core/atomicfile.py` — lock, exclusive create, fresh-checked replace

**Files:**
- Create: `sluice/core/atomicfile.py`
- Test: `tests/test_atomicfile.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `file_lock(real: str) -> threading.Lock` — one lock per RESOLVED path, process-wide.
  - `replace_if(path: str, data: bytes, *, fresh: Callable[[bytes], bool] | None, tmp_prefix: str) -> bool`
    — resolves `path` with `os.path.realpath`, takes `file_lock(real)`, then: `fresh is None`
    creates exclusively (parent directories first; `False` if the file exists); otherwise reads
    the current bytes (`False` if missing), returns `False` unless `fresh(current)` is true, and
    replaces atomically keeping the mode. Returns `True` only when it wrote.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_atomicfile.py`:

```python
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
    assert not file_lock(os.path.realpath(p)).locked()
    assert sorted(x.name for x in tmp_path.iterdir()) == ["f.json"]
    assert replace_if(str(p), b"two", fresh=_same(b"one"), tmp_prefix=".t-")


def test_the_lock_is_one_object_per_resolved_path(tmp_path):
    target = tmp_path / "f.json"
    link = tmp_path / "link.json"
    link.symlink_to(target)
    assert file_lock(os.path.realpath(link)) is file_lock(os.path.realpath(target))
    assert file_lock(str(tmp_path / "a")) is not file_lock(str(tmp_path / "b"))


def test_the_lock_serialises_two_writers(tmp_path):
    """Two threads each replace only if the file still holds the ORIGINAL bytes. Each `fresh`
    waits up to half a second for the other to arrive inside `fresh` too; under the lock the
    second cannot, so the two never overlap and exactly one write lands. Without the lock both
    pass the check against the same bytes and the second clobbers the first."""
    p = tmp_path / "f.json"
    p.write_bytes(b"orig")
    inside = threading.Semaphore(0)
    overlaps = []
    start = threading.Barrier(2)
    results = []

    def fresh(current):
        inside.release()
        # Two releases means both threads are inside `fresh` at once.
        overlaps.append(inside.acquire(timeout=0.5) and inside.acquire(timeout=0.5))
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_atomicfile.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'sluice.core.atomicfile'`.

- [ ] **Step 3: Write the module**

Create `sluice/core/atomicfile.py`:

```python
"""The one writer for a file sluice replaces in place rather than appends to: the config file
(`core/config.py::write_config_text`) and, from `job-sluice mcp install`, another tool's config.

Two arms, chosen by `fresh`. `None` creates exclusively -- never-clobber is a property of the
open, not of a check before it. A callable decides whether the CURRENT bytes are still the ones
the caller meant to replace (a sha the user was shown, the bytes a backup copied); only then is
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


def file_lock(real: str) -> threading.Lock:
    """The process-wide lock for an already-resolved path (`os.path.realpath`)."""
    with _locks_guard:
        return _locks.setdefault(real, threading.Lock())


def replace_if(path: str, data: bytes, *, fresh: Callable[[bytes], bool] | None,
               tmp_prefix: str) -> bool:
    """Write `data` to `path`. `fresh=None`: create exclusively, parent directory first.
    Otherwise: replace only when `fresh(current_bytes)` is true. Returns False whenever it
    wrote nothing; raises on an I/O failure, and on whatever `fresh` raises, having written
    nothing and left no temp file."""
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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_atomicfile.py -q`
Expected: all pass (12 passed: 11 functions, the last parametrized twice — count from the output,
not from this line).

- [ ] **Step 5: Commit, then witness the lock and the target-directory temp**

```bash
git add sluice/core/atomicfile.py tests/test_atomicfile.py
git commit -m "refactor(core): add atomicfile.replace_if, one create-or-replace writer

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Witness A — delete the lock: in `replace_if`, replace the line `    with file_lock(real):` with
`    if True:` (a deletion of the lock's effect at the same indentation). Run
`.venv/bin/python -m pytest tests/test_atomicfile.py::test_the_lock_serialises_two_writers -q`.
Expected: FAIL (`overlaps` contains `True`, or both results `True`). Restore:
`git checkout -- sluice/core/atomicfile.py`.

Witness B — temp beside the link: change `dir=os.path.dirname(real) or "."` to
`dir=os.path.dirname(path) or "."` (moves the argument from the resolved to the given path). Run
`.venv/bin/python -m pytest tests/test_atomicfile.py::test_the_temp_file_is_made_in_the_targets_directory_and_the_link_survives -q`.
Expected: FAIL on `dirs ==`. Restore: `git checkout -- sluice/core/atomicfile.py`.

Record both results in the ledger.

---

### Task 2: `write_config_text` and `keep_config_copy` call the shared writer

**Files:**
- Modify: `sluice/core/config.py` (`_config_write_locks`, `_config_write_locks_guard`,
  `_config_write_lock`, `keep_config_copy`, `write_config_text`)
- Modify: `tests/test_config_write.py` (`test_a_failed_create_removes_only_the_file_it_created`:
  the monkeypatch target only)
- Modify: `tests/test_mcpserver.py` (`_FILE_WRITE_CALLS`)
- Test: `tests/test_config_write.py` (two new tests)

**Interfaces:**
- Consumes: `file_lock(real: str) -> threading.Lock`,
  `replace_if(path, data: bytes, *, fresh, tmp_prefix) -> bool` from Task 1.
- Produces: `write_config_text(path: str, text: str, *, expect_sha: str | None = None) -> bool`
  and `keep_config_copy(path: str, expect_sha: str) -> str`, signatures and behaviour unchanged.

**Ruling (recorded here, ledger it):** the spec says the existing config tests pass
"unchanged". `test_a_failed_create_removes_only_the_file_it_created` monkeypatches
`sluice.core.config.open`; once the create moves, that patch reaches nothing and the test fails
for a reason unrelated to behaviour. Its monkeypatch TARGET moves to `sluice.core.atomicfile`;
every assertion stays. Cost if wrong: one test edit a reviewer must read.

- [ ] **Step 1: Write the two new tests (Review Focus 1 and 4)**

Append to `tests/test_config_write.py`:

```python
def test_a_non_utf8_config_raises_on_replace_and_is_left_alone(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b"a: \xff\n")
    with pytest.raises(UnicodeDecodeError):
        write_config_text(str(p), "a: 2\n", expect_sha="0" * 64)
    assert p.read_bytes() == b"a: \xff\n"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["config.yaml"]


def test_the_copy_and_the_replace_take_one_lock(tmp_path, monkeypatch):
    """`keep_config_copy` reads the bytes a replace is about to supersede; under a different
    lock it could copy a file mid-replace. Both must take the lock the shared writer takes."""
    from sluice.core import atomicfile
    from sluice.core import config as config_mod
    p = tmp_path / "config.yaml"
    p.write_text("a: 1\n")
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
```

- [ ] **Step 2: Run them**

Run: `.venv/bin/python -m pytest tests/test_config_write.py -q`
Expected: `test_a_non_utf8_config_raises_on_replace_and_is_left_alone` PASSES (today's behaviour,
pinned before the move — that is its job); `test_the_copy_and_the_replace_take_one_lock` FAILS
with `assert len(taken) == 2` (`taken == []`: config.py still uses its own lock table).

- [ ] **Step 3: Move `config.py` onto the shared writer**

In `sluice/core/config.py`:

1. Delete `_config_write_locks`, `_config_write_locks_guard`, `_config_write_lock` and the
   two-line comment above them.
2. Add `from sluice.core.atomicfile import file_lock, replace_if` with the module's other
   `sluice.core` imports (check the top of the file for where they sit, and whether `tempfile`
   and `threading` are still used elsewhere in the file before removing their imports:
   `grep -n "tempfile\.\|threading\." sluice/core/config.py`).
3. In `keep_config_copy`, replace `with _config_write_lock(real):` with `with file_lock(real):`.
   Leave the rest of its body as is.
4. Replace `write_config_text`'s body (keep its signature and docstring; amend the docstring's
   last paragraph as shown) with:

```python
def write_config_text(path: str, text: str, *, expect_sha: str | None = None) -> bool:
    """The config file's one writer (in-session setup), over `core/atomicfile.py::replace_if`,
    which resolves a symlink and replaces its TARGET in the target's own directory, so a link
    into a dotfiles repository survives.

    No `expect_sha`: create exclusively (O_EXCL), parent directory first -- never-clobber is a
    property of the open. With it: replace only when the current text hashes to it, keeping the
    file's mode. Returns False whenever it wrote nothing.

    The update arm is best-effort, the residual `replace_if` states: an outside editor that
    writes between the sha check and the replace is overwritten."""
    # Encode BEFORE anything touches the disk: an unencodable text (a lone surrogate) must fail
    # while nothing exists, or the empty file a create left would read as "someone else created
    # it" to every later create and setup could never write a config.
    data = text.encode("utf-8")
    if expect_sha is None:
        return replace_if(path, data, fresh=None, tmp_prefix=".sluice-config-")
    # The sha is over the TEXT, as the review form recorded it (`document_sha`); decoding the
    # raw bytes is what reading with newline="" did, so a CRLF config still compares truly and
    # a non-UTF-8 one still raises rather than being replaced.
    return replace_if(path, data, fresh=lambda current: document_sha(
        current.decode("utf-8")) == expect_sha, tmp_prefix=".sluice-config-")
```

5. In `keep_config_copy`'s docstring, the sentence "The symlink is resolved exactly as
   `write_config_text` resolves it" stays true; leave it.

- [ ] **Step 4: Move the one monkeypatch target, and add the writer to the onboard sweep**

In `tests/test_config_write.py`, `test_a_failed_create_removes_only_the_file_it_created`:
replace the line `from sluice.core import config as config_mod` with
`from sluice.core import atomicfile`, and change
`monkeypatch.setattr(config_mod, "open", fake_open, raising=False)` to
`monkeypatch.setattr(atomicfile, "open", fake_open, raising=False)`. No other line changes.

In `tests/test_mcpserver.py`, add `"replace_if"` to `_FILE_WRITE_CALLS` (a new write helper
name the onboard tripwire must recognise, as its comment requires: "A new write shape here
should be added to the sweep in the same change that needs it").

- [ ] **Step 5: Run the config tests and the setup tests**

Run: `.venv/bin/python -m pytest tests/test_config_write.py tests/test_atomicfile.py tests/test_mcpserver.py tests/functional/test_mcp_setup_save.py -q`
Expected: all pass.

- [ ] **Step 6: Commit and witness the shared lock**

```bash
git add sluice/core/config.py tests/test_config_write.py tests/test_mcpserver.py
git commit -m "refactor(core): write_config_text and keep_config_copy use atomicfile

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
```

Witness: in `keep_config_copy`, replace `with file_lock(real):` with `if True:`. Run
`.venv/bin/python -m pytest tests/test_config_write.py::test_the_copy_and_the_replace_take_one_lock -q`.
Expected: FAIL (`len(taken) == 1`). Restore: `git checkout -- sluice/core/config.py`.

---

### Task 3: `sluice/mcpextra.py` — the "mcp not installed" message, one home

**Files:**
- Create: `sluice/mcpextra.py`
- Modify: `sluice/mcpserver.py` (`build_server`'s `except ImportError` arm)
- Test: `tests/test_mcpserver.py` (two new tests, beside the existing `McpNotInstalled` test)

**Interfaces:**
- Consumes: nothing.
- Produces: `sluice.mcpextra.NOT_INSTALLED: str` — exactly
  `"the 'mcp' package is not installed -- run `pip install job-sluice[mcp]`"`. PR 2's
  `mcp install` prints it and exits 2, as `mcp serve` does.

- [ ] **Step 1: Write the failing tests**

Find the existing test that drives `build_server` without `mcp` (search
`grep -n "McpNotInstalled" tests/test_mcpserver.py`) and add beside it:

```python
def test_the_not_installed_message_has_one_home():
    """`mcp install` prints the same message `mcp serve` raises; one constant, so the two
    cannot drift into naming different install commands."""
    import inspect

    from sluice import mcpextra, mcpserver
    assert mcpextra.NOT_INSTALLED == (
        "the 'mcp' package is not installed -- run `pip install job-sluice[mcp]`")
    src = inspect.getsource(mcpserver.build_server)
    assert "McpNotInstalled(mcpextra.NOT_INSTALLED)" in src
    assert "pip install job-sluice[mcp]" not in src


def test_mcpextra_imports_nothing():
    """`mcp install` reads the message before deciding anything; importing it must not load
    the store, the backends or the mcp package. Run in a fresh interpreter so this file's own
    imports cannot satisfy it."""
    import subprocess
    import sys
    code = ("import sys, sluice.mcpextra; "
            "bad = sorted(m for m in sys.modules if m.startswith(('sluice.core', 'sluice.onboard',"
            " 'mcp')) or m == 'sluice.mcpserver'); print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_mcpserver.py -q -k "one_home or imports_nothing"`
Expected: both FAIL — `ModuleNotFoundError: No module named 'sluice.mcpextra'` (the first as an
error in the test body, the second via the subprocess's non-zero exit).

- [ ] **Step 3: Create the module and use it**

Create `sluice/mcpextra.py`:

```python
"""What to tell a user whose install lacks the `mcp` extra. Its own module, importing nothing,
so `job-sluice mcp install` can say it before loading anything -- `sluice.mcpserver` imports the
store and the onboard package at module scope -- and `mcp serve` says the identical words."""

NOT_INSTALLED = "the 'mcp' package is not installed -- run `pip install job-sluice[mcp]`"
```

In `sluice/mcpserver.py`, add `from sluice import mcpextra` with the module's other `sluice`
imports, and change the `except ImportError as e:` arm in `build_server` to:

```python
    except ImportError as e:
        raise McpNotInstalled(mcpextra.NOT_INSTALLED) from e
```

- [ ] **Step 4: Run them to verify they pass, then the MCP suites**

Run: `.venv/bin/python -m pytest tests/test_mcpserver.py tests/functional/test_mcp_contract.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add sluice/mcpextra.py sluice/mcpserver.py tests/test_mcpserver.py
git commit -m "refactor(mcp): give the mcp-not-installed message one importable home

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 4: Docs, full suite, lint

**Files:**
- Modify: `docs/ARCHITECTURE.md` (the `core/` module list)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: nothing new.

- [ ] **Step 1: Document the module**

In `docs/ARCHITECTURE.md`, under `## \`core/\``, after the `config.py` bullet (it ends with the
sentence about `write_config_text` replacing a symlinked config's TARGET), add:

```markdown
- `atomicfile.py`: the one writer for a file replaced in place. `replace_if` creates exclusively
  (`fresh=None`) or replaces only when a caller-supplied `fresh(current_bytes)` agrees, resolving
  a symlink and replacing its TARGET in the target's own directory, keeping the mode, under one
  in-process lock per resolved path (`file_lock`), which a backup copy of the same file takes
  too. `config.py::write_config_text` is its caller for the config file; the lock is in-process
  only, so an outside editor writing between the check and the replace is overwritten.
```

In the `config.py` bullet itself, the sentence "`write_config_text` is the config file's one
writer" stays true (it is the config's writer, built on `atomicfile`); append " (over
`atomicfile.py::replace_if`)" after "the config file's one writer".

- [ ] **Step 2: Full suite, both PATHs, and lint**

Run:
```bash
.venv/bin/python -m pytest -q > /tmp/pr1-suite.txt 2>&1; echo "exit=$?"; tail -3 /tmp/pr1-suite.txt
env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q > /tmp/pr1-suite-bare.txt 2>&1; echo "exit=$?"; tail -3 /tmp/pr1-suite-bare.txt
.venv/bin/ruff check sluice tests scripts
```
Expected: `exit=0` twice; ruff `All checks passed!`. (If `ruff` is missing:
`uv pip install --python .venv/bin/python ruff==0.15.21`.)

- [ ] **Step 3: Commit**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs(architecture): describe core/atomicfile.py

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

## After the tasks

Final whole-branch review per the executing skill, then `/review-pr` and the merge gate as usual.
The PR body states: no behaviour change; one monkeypatch target moved (named); the two spec
commits ride along (`docs/superpowers/specs/2026-10-10-mcp-install-design.md`) since the spec
argues PR 1's shape.
