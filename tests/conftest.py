"""Shared synthetic fixtures.

Role preferences are personal. The suite must not encode any real person's target
or anti-target titles, so it generates its own fictional lists with a fixed seed:
deterministic enough to assert on, and revealing nothing about whoever runs sluice.
"""
import logging
import os
import unicodedata

import pytest
from faker import Faker

# Every variable `paths.resolve` consults as `env_var=`, i.e. the rung that outranks the
# XDG pins below. Hand-listed HERE so the fixture stays trivial, and pinned against the
# source by `test_path_sandbox.py::test_the_sandbox_covers_every_path_env_var` -- adding a
# seventh in `sluice/` without adding it here reddens there rather than silently opening
# the sandbox for whoever happens to have it exported.
PATH_ENV_VARS = (
    "DOSSIER_DIR",
    "SEEN_DB",
    "SLUICE_CONFIG",
    "SLUICE_DISABLED",
    "SLUICE_HEALTH",
    "TRIAGE_AUDIT",
)


@pytest.fixture(autouse=True)
def _pin_paths(tmp_path, monkeypatch, request):
    """Sandbox every per-system path the suite can resolve (#80).

    Before #80 the defaults were cwd-relative and the suite ran in `tmp_path`, so it
    could not reach a real home directory. Afterwards it can, and would pass while
    doing it -- which is why this is autouse rather than opt-in.

    All five names are load-bearing, but not all on the same machine, and the two config
    ones do NOT substitute for each other: `$XDG_CONFIG_HOME` and `~` are consecutive
    rungs of ONE fallback chain, so which is consulted depends on the environment. macOS
    and CI conventionally leave `XDG_*` unset, so there `HOME` does the work and deleting
    the XDG pins alone leaves the suite green; on a machine that EXPORTS
    `XDG_CONFIG_HOME` the reverse holds, and that pin is the only thing between the
    neutrality guard and a real config. Pin both, and witness each with its OWN variable
    aimed at a planted config -- deleting one and watching for green proves nothing,
    because the other masks it.
    `VAULT_DIR` was never pinned at all -- a developer with it exported ran the whole
    suite against their real vault.

    `tests/harness/config.py` pins every per-path env var itself, so the e2e and
    functional tiers never reach these; that is why this cannot be the only guard, and
    why `tests/test_path_sandbox.py` asserts the property directly.
    """
    for var, sub in (("XDG_CONFIG_HOME", "config"),
                     ("XDG_STATE_HOME", "state"),
                     ("XDG_CACHE_HOME", "cache")):
        monkeypatch.setenv(var, str(tmp_path / "xdg" / sub))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    # The rung ABOVE all of those. `paths.resolve` consults `env_var` FIRST, so any of
    # these outranks every pin set above: a developer with `SLUICE_CONFIG` exported ran
    # the suite against their real config file, and `TRIAGE_AUDIT` against a real audit
    # log -- the same shape as the `VAULT_DIR` hole, one rung higher. Every existing
    # sandbox test passes `env_var=None`, so none of them could see it.
    for var in PATH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Not a PATH var, same hole. `Sluice.doctor` reads these to report which browser profile
    # an ingest run will drive, so a developer -- or a CI runner -- with CAMOFOX_SESSION
    # exported gets an extra DEGRADED row in every doctor test that does not monkeypatch it.
    # That is precisely the 2026-08-15 incident config, on the machine most likely to have it.
    for var in ("CAMOFOX_USER", "CAMOFOX_SESSION", "CAMOFOX_URL"):
        monkeypatch.delenv(var, raising=False)
    # Same hole, and the only one here that reaches the NETWORK. `core/log.py`'s
    # `_resolve_telegram` reads these two BEFORE consulting Config, so an empty `Config()`
    # does not protect anything: a developer (or a CI runner) with both exported would
    # have `_notify_reporting` build a real sender and POST to Telegram. Four commands
    # call it -- `ingest run` (degraded report), `triage run`, `cv run` and `track run`'s
    # failure path -- so every CLI-level test of those that reaches a non-empty result
    # sends a live request. The suite is meant to be fully offline, and this was the one
    # remaining way it was not.
    #
    # Both are deleted, not just one: `_resolve_telegram` needs BOTH to return credentials,
    # so clearing either would be enough to stop the send -- and would therefore hide the
    # other from any test written to prove this pin works.
    for var in ("SLUICE_TELEGRAM_TOKEN", "SLUICE_TELEGRAM_CHAT"):
        monkeypatch.delenv(var, raising=False)
    # SLUICE_LOG_LEVEL, same hole, and it needs TWO steps rather than one (#144).
    #
    # `get_logger` sets a logger's level exactly ONCE -- the whole body is guarded on
    # `if not logger.handlers` -- so by the time any test runs, every `sluice.*` logger
    # created at import already has the developer's exported level baked in. Deleting the
    # env var only affects loggers created AFTER this point, which is almost none of them.
    #
    # Measured: `SLUICE_LOG_LEVEL=CRITICAL python -m pytest` gave 34 failures across ten
    # files, because a bare `caplog.at_level("WARNING")` with no `logger=` only raises the
    # ROOT logger's level, and `get_logger` sets `propagate = False`, so a suppressed record
    # never reaches caplog's handler at all. Fixed here rather than by adding `logger=` to
    # 29 call sites: the defect is that the suite reads an ambient env var, not that any one
    # test forgot an argument, and a per-site fix leaves the next bare call to reintroduce it.
    monkeypatch.delenv("SLUICE_LOG_LEVEL", raising=False)
    # `setLevel()`, NOT `obj.level = ...`. Assigning the attribute directly leaves
    # `Logger._cache` populated, and `isEnabledFor` reads that cache -- so a logger whose
    # cache was warmed while the ambient level was CRITICAL keeps answering False for
    # WARNING even after the level says INFO, and `logger.warning(...)` is still dropped.
    # Whether that bites depends on whether anything logged before the reset, i.e. on test
    # ORDER: the fix worked here only because the cache happened to be cold. `setLevel`
    # clears the cache, which is the entire reason it exists rather than being a property.
    _levels = [(obj, obj.level) for name, obj in list(logging.Logger.manager.loggerDict.items())
               if name.startswith("sluice.") and isinstance(obj, logging.Logger)]
    for obj, _old in _levels:
        obj.setLevel(logging.INFO)
    # Restored through setLevel too, so the next test does not inherit a stale cache either.
    request.addfinalizer(lambda: [obj.setLevel(old) for obj, old in _levels])


def _title_pool(n=60):
    fake = Faker("en_GB")
    Faker.seed(20260713)          # fixed: assertions must be reproducible
    seen, out = set(), []
    for _ in range(n):
        t = fake.job().lower()
        if "," in t or t in seen:  # keep single-phrase titles, no duplicates
            continue
        seen.add(t)
        out.append(t)
    return out


def _disjoint(pool, k=3):
    """Two lists with no substring overlap, so accept/reject cannot collide."""
    accept = pool[:k]
    reject = [t for t in pool[k:]
              if not any(a in t or t in a for a in accept)][:k]
    return accept, reject


@pytest.fixture(scope="session")
def titles():
    """(accept, reject): synthetic, disjoint, fictional job titles."""
    return _disjoint(_title_pool())


@pytest.fixture
def cfg_titles(titles):
    """A TriageConfig carrying the synthetic lists, plus a permissive geography."""
    from sluice.triage.config import TriageConfig
    accept, reject = titles
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)
    cfg.reject_titles = list(reject)
    cfg.target_locations = ["testville"]
    return cfg


# Synthetic, token-disjoint placeholder locations. #5 makes `location` a note-name
# discriminator that tests assert into filenames, so these must be clearly FICTIONAL
# (never a real place, however a seeded Faker city might land) and pairwise token-disjoint,
# so any two read DIFFERENT under _compare_locations. NATO-phonetic placeholders, matching
# the Aye/Bee/Cee convention for synthetic companies. "Delta" is skipped -- a real
# geographic term (river deltas) and a real airline brand, unlike the other four, which
# name neither. Module-level (importable) because the bare `_lead()` helpers in test_vault.py
# and the conformance suite cannot receive a fixture.
LOCATIONS = ("Alfa", "Bravo", "Charlie", "Foxtrot")


@pytest.fixture
def locations():
    """Four synthetic, token-disjoint placeholder locations (the LOCATIONS constant)."""
    return list(LOCATIONS)


def _cannot_unread_a_dir():
    # TWO platforms where chmod does not do what a mode-bit test needs, and they fail in
    # OPPOSITE directions. As uid 0 the mode bits do not bind, so the directory stays
    # readable and the test passes VACUOUSLY -- the dangerous direction. On Windows chmod
    # cannot remove read access from a directory at all, so the walk succeeds and the test
    # fails outright, which is noise rather than a finding. geteuid is absent on Windows;
    # -1 never equals 0, so the order of these two terms does not matter.
    return os.name == "nt" or getattr(os, "geteuid", lambda: -1)() == 0


# Shared by every vault test that takes read or traverse permission away from a directory.
# Module-level here rather than copied into each test file: two copies of a platform
# predicate kept in step by a comment is the shape that drifts, and a skipif that drifts
# toward "run it" on uid 0 passes vacuously.
UNREADABLE_DIR = pytest.mark.skipif(
    _cannot_unread_a_dir(), reason="chmod binds neither uid 0 nor Windows")


def require_case_sensitive_fs(tmp_path):
    """Skip unless the filesystem under `tmp_path` distinguishes case. PROBED, never
    inferred from the platform: the subject is what the filesystem does with two names
    differing only in case, so asking it directly is the only answer that cannot be wrong.
    The probe writes into a dedicated subdirectory so it cannot collide with a vault the
    caller has already built.

    THE RULE FOR CALLERS, rather than a list of them, which would go stale: gate on needing
    the filesystem to DISTINGUISH two names, never on merely mentioning a pair. A row that
    needs the WRITE PATH to mint or resolve a same-directory pair belongs here, because
    there the defect genuinely does not exist on a case-insensitive filesystem -- `_locate`'s
    stat already resolves the variant, so the row would pass without exercising anything.

    Getting that wrong in the other direction was the more damaging mistake. This gate was
    originally applied to every row that mentioned a pair, on the belief that a
    case-insensitive filesystem cannot hold one at all. It cannot hold one in a SINGLE
    DIRECTORY; across two subfolders the pair exists everywhere, and since `_slug_for` is the
    basename and both `read_leads` sweeps group on it, that is the same identity to sluice.
    Over-gated, deleting the entire capitalisation sweep reddened nothing on macOS, and
    regressing the folded probe to a bare `except OSError` -- the arm that creates and
    records an irreversible `seen.db` row -- reddened nothing either. A gate that hides a
    guard is worse than no guard, because it looks like coverage.

    Module-level here rather than copied per file, for the same reason as `UNREADABLE_DIR`
    beside it: two copies of a filesystem predicate kept in step by a comment is the shape
    that drifts, and this one drifting toward "run it" makes a row pass vacuously."""
    probe = tmp_path / "_case_probe"
    probe.mkdir(exist_ok=True)
    (probe / "CaseProbe").write_text("")
    collides = (probe / "caseprobe").exists()
    for p in probe.iterdir():
        p.unlink()
    probe.rmdir()
    if collides:
        pytest.skip(
            "needs a case-sensitive filesystem: this row exercises the WRITE path minting "
            "or resolving two names that differ only by case in ONE directory, and a "
            "case-insensitive filesystem (macOS APFS by default) resolves them to one note "
            "before sluice sees them -- so the defect does not exist to be caught. Rows "
            "that only need the collided STATE are seated across subfolders and run here. "
            "CI (ubuntu-latest) is case-sensitive and runs everything."
        )


def require_normalization_sensitive_fs(tmp_path):
    """Skip unless the filesystem under `tmp_path` distinguishes two Unicode NORMALIZATIONS
    of one name. A SECOND probe rather than a parameter on the case one, because the two
    properties are independent and this machine has them in OPPOSITE states: measured
    2026-09-09, a case-sensitive APFS volume created with `hdiutil -fs "Case-sensitive APFS"`
    distinguishes `CaseProbe` from `caseprobe` and STILL conflates the composed and
    decomposed forms of one accented name. So `require_case_sensitive_fs` passing says
    nothing at all about this axis, and gating a normalization row on it would run that row
    against a filesystem that cannot hold the pair -- green, and certifying nothing.

    The pair is written with an explicit ESCAPE rather than a literal accented character,
    and the assertion below fails closed if the two forms ever compare equal. A literal
    would be unreadable in the one file where the distinction IS the subject -- and worse,
    this docstring's first draft used two literals that landed in DIFFERENT normalizations,
    so a sentence naming NFC and NFD was true only by accident and could not be checked by
    reading it. A tool that normalized this source would silently collapse the pair and
    leave a probe that always reports "distinguishes".

    Practically this skips on macOS in BOTH filesystem configurations and runs on Linux, so
    CI is the only place the rows gated on it execute. That is said in the skip reason
    rather than left for a reader to infer from a silent green tick."""
    probe = tmp_path / "_nfc_probe"
    probe.mkdir(exist_ok=True)
    nfc = unicodedata.normalize("NFC", "Caf\u00e9")
    nfd = unicodedata.normalize("NFD", "Caf\u00e9")
    assert nfc != nfd, "the probe's own pair must differ before it can test anything"
    (probe / nfc).write_text("")
    collides = (probe / nfd).exists()
    for entry in probe.iterdir():
        entry.unlink()
    probe.rmdir()
    if collides:
        pytest.skip(
            "needs a normalization-sensitive filesystem: this row needs two composition "
            "forms of one name to be two paths, and macOS conflates them on APFS in both "
            "its case-sensitive and case-insensitive variants -- so the pair cannot be "
            "seated here and the defect does not exist to be caught. CI (ubuntu-latest) "
            "is normalization-sensitive and runs everything."
        )


def racing_read(monkeypatch, target_path, on_race, *, once=True):
    """Interpose sluice.core.vault._read to simulate a concurrent writer landing in the
    capture->commit window (#16), without threads. `on_race()` performs one out-of-band
    edit to the file. It fires after the FIRST read of target_path (once=True) or on
    EVERY read (once=False, for exhaustion -- on_race must then change the content each
    call). The read returns the PRE-edit bytes, so it is robust to a mutant that deletes
    _cas_write's second (compare) read. Returns the fired-state dict."""
    import sluice.core.vault as vaultmod
    real_read = vaultmod._read
    state = {"fired": False}
    def racer(path):
        text = real_read(path)
        if str(path) == str(target_path) and (not once or not state["fired"]):
            state["fired"] = True
            on_race()
        return text
    monkeypatch.setattr(vaultmod, "_read", racer)
    return state


class DnsUsedInTests(BaseException):
    """Raised when a test tries to resolve a hostname.

    Subclasses BaseException, NOT Exception, and that is load-bearing. A plain
    Exception would be swallowed on the dossier path by whichever consumer calls
    it -- cv/engine.py's per-item `except Exception` (which proceeds with an
    empty JD) or triage/engine.py's per-item `except Exception` (which records
    report.failures and skips the lead). An implementer who forgot to inject
    `resolve_host=` at one of the three test wiring sites would therefore see a
    GREEN suite that was doing real DNS on every run. That happened in review,
    which is why this exists at all.
    """


@pytest.fixture(scope="session", autouse=True)
def _forbid_dns():
    """Make socket.getaddrinfo raise for the whole session.

    Verified before writing: the suite performs zero DNS today, so this changes
    nothing that currently passes. monkeypatch is function-scoped, so the
    set/restore is done by hand.
    """
    import socket
    real = socket.getaddrinfo

    def _raise(*args, **kwargs):
        raise DnsUsedInTests(
            "tests must not resolve DNS -- inject resolve_host= instead")

    socket.getaddrinfo = _raise
    try:
        yield
    finally:
        socket.getaddrinfo = real


def make_composable(vault):
    """Give a vault the two config-level preconditions `cv run` requires (#242).

    A baseline CV and one verified `experience` entry. Before #242 an empty vault reached the
    composer and failed later, so a test that only wanted to exercise compose_cv's WIRING
    (backend threading, dossier paths, MCP tool shapes) could use a bare tmp_path. That is now
    refused before any spend, correctly -- so those tests need a vault that could actually
    produce a CV, which is also the more honest fixture.

    Deliberately NOT keyed on `skills`/`stories`: the gate licenses nothing from them, so
    `missing_prerequisites` does not require them and neither does this.
    """
    import os

    os.makedirs(os.path.join(vault.dir, os.path.dirname(vault.baseline_rel)), exist_ok=True)
    with open(os.path.join(vault.dir, vault.baseline_rel), "w", encoding="utf-8") as fh:
        fh.write("# CV\n\nPROFILE\n\nWORK EXPERIENCE\n")
    if vault.read_evidence("experience"):
        return vault                       # idempotent: a second call must not re-propose
    vault.propose_evidence("experience", name="alpha", fields={})
    pending = {e["title"]: e for e in vault.read_pending_evidence("experience")}
    with open(pending["alpha"]["path"], encoding="utf-8") as fh:
        raw = fh.read()
    vault.verify_evidence("experience", "alpha", today="2026-09-03", reviewed=raw)
    return vault
