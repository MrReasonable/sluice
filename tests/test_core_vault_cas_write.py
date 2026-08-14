"""_cas_write's intra-process concurrency safety (discovered while implementing
#131's dismiss_lead: the 50-round proof in tests/test_leads_dismiss.py failed
100% of rounds before this fix landed). Two REAL threads, Barrier-synchronized,
racing the SAME path through _cas_write directly -- no mocking of the write
layer, mirrors tests/conformance/test_store_contract.py's own proven technique
for the analogous create-path race."""
import os
import threading

from sluice.core.vault import _cas_write, _lock_for


def test_cas_write_serializes_two_racing_threads_so_neither_write_is_silently_lost(tmp_path):
    path = str(tmp_path / "note.txt")
    for round_no in range(50):
        with open(path, "w") as f:
            f.write("BASE")
        results = []
        barrier = threading.Barrier(2)

        def worker(tag, _results=results, _barrier=barrier):
            _barrier.wait()   # maximise the overlap rather than hoping for it

            def transform(text):
                return text + f"-{tag}"

            _results.append((tag, _cas_write(path, transform)))

        threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with open(path) as f:
            final = f.read()

        # Liveness guard: a worker that raises (VaultConflict, an unexpected
        # exception, anything) never reaches `_results.append(...)`, so
        # `results` would silently end up short -- and the per-entry loop
        # below iterates however many entries ARE there, so it would pass
        # vacuously (0 or 1 entries) while a thread died. Both threads must
        # actually finish and report.
        assert len(results) == 2, (
            f"round {round_no}: expected both worker threads to report a "
            f"result, got {results} -- a thread must have raised instead of "
            f"reaching _results.append (final content: {final!r})")

        # Liveness guard: the per-entry loop below also passes vacuously if
        # _cas_write never actually wrote anything at all (both threads
        # False, final still "BASE") -- assert real work happened.
        wrote_true = [tag for tag, wrote in results if wrote is True]
        assert wrote_true, (
            f"round {round_no}: neither thread reported a committed write, "
            f"got {results} (final content unchanged: {final!r})")

        # Each thread's transform appends its OWN distinct tag, so it is never a
        # no-op relative to ANY prior state -- unlike a "set field to constant"
        # transform, there is no state from which re-deriving against fresh
        # content legitimately produces new == text. With only 2 racing writers
        # and _RMW_RACE_RETRIES retries available, a genuinely correct fix lets
        # the loser re-derive from the winner's fresh content and commit right
        # after, in sequence -- so BOTH are expected to report True here, one
        # writing on top of the other's already-committed change. That is
        # correct, lossless behavior, not a second bug.
        #
        # What must NEVER happen -- and is exactly what the pre-fix race
        # produced, 50/50 rounds, when reproduced with the lock removed -- is a
        # thread reporting a committed write (True) while its OWN tag is absent
        # from the final content, because two threads' os.replace both "won"
        # the stale recheck and the LAST one silently clobbered the other's
        # commit. That is the invariant this asserts: `wrote is True` and "my
        # content actually made it to disk" must always agree.
        for tag, wrote in results:
            if wrote is True:
                assert f"-{tag}" in final, (
                    f"round {round_no}: thread {tag} reported a committed write "
                    f"(True) but its own tag is missing from the final content "
                    f"{final!r} -- its transform was silently discarded by a "
                    f"racing os.replace (results={results})")
            else:
                assert f"-{tag}" not in final, (
                    f"round {round_no}: thread {tag} reported no commit but its "
                    f"tag IS present in {final!r} -- a write landed without "
                    f"being reported (results={results})")


def test_lock_for_resolves_a_symlink_to_the_same_lock_as_its_real_target(tmp_path):
    """Minor #4 (final whole-branch review): _lock_for used os.path.abspath,
    where this SAME module deliberately uses os.path.realpath elsewhere for the
    identical reason (a symlink INSIDE the store). abspath normalizes text but
    not symlinks, so two DIFFERENT textual paths to the SAME real file would
    silently get two different lock objects -- reintroducing the race this lock
    exists to close. A symlinked path and its real target must resolve to the
    ONE SAME lock object (identity, not equality -- two distinct Lock instances
    would still let two threads enter their critical sections concurrently)."""
    real_path = tmp_path / "note.md"
    real_path.write_text("BASE")
    link_path = tmp_path / "link.md"
    os.symlink(real_path, link_path)

    assert _lock_for(str(real_path)) is _lock_for(str(link_path))


def test_cas_write_still_returns_false_on_a_genuine_no_op(tmp_path):
    """The lock must not change _cas_write's existing no-op/return-False
    semantics for the ordinary, uncontended case."""
    path = str(tmp_path / "note2.txt")
    with open(path, "w") as f:
        f.write("SAME")
    assert _cas_write(path, lambda text: text) is False
    with open(path) as f:
        assert f.read() == "SAME"


def test_cas_write_different_paths_do_not_serialize_against_each_other(tmp_path):
    """A lock scoped per-path, not global: two threads writing DIFFERENT files
    concurrently must both complete without waiting on each other.

    Asserted deterministically (Minor #5, final whole-branch review) via a
    shared threading.Barrier(2, timeout=5) waited on INSIDE each transform --
    the prior version asserted a wall-clock bound instead (`elapsed < 0.35`),
    which had already flaked once under full-suite load (this session's own
    Task 9 ledger), plus a dead `entered_critical_section` list appended to but
    never asserted on. Under a correctly per-path lock, both threads reach and
    release the barrier together (they are on DIFFERENT paths and never
    contend), so `barrier.wait()` returns normally for both. Under an
    accidentally-global lock, the second thread could never enter its
    transform while the first holds the lock, so its `barrier.wait()` would
    raise BrokenBarrierError on timeout -- which this test asserts never
    happens, rather than inferring it from wall-clock timing."""
    path_a = str(tmp_path / "a.txt")
    path_b = str(tmp_path / "b.txt")
    for p in (path_a, path_b):
        with open(p, "w") as f:
            f.write("BASE")

    barrier = threading.Barrier(2, timeout=5)
    errors = []

    def transform_factory(tag):
        def transform(text):
            barrier.wait()   # raises BrokenBarrierError if the OTHER thread
                             # never gets here -- e.g. stuck behind a global lock
            return text + f"-{tag}"
        return transform

    def worker(path, tag):
        try:
            _cas_write(path, transform_factory(tag))
        except threading.BrokenBarrierError as e:
            errors.append((tag, e))

    threads = [
        threading.Thread(target=worker, args=(path_a, "A")),
        threading.Thread(target=worker, args=(path_b, "B")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], (
        f"a per-path lock should let two DIFFERENT paths' transforms run "
        f"concurrently, but {[tag for tag, _ in errors]} timed out waiting on "
        f"the shared barrier -- consistent with an accidentally-global lock")
    with open(path_a) as f:
        assert f.read() == "BASE-A"
    with open(path_b) as f:
        assert f.read() == "BASE-B"
