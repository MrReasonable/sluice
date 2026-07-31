"""The two dedup stores REFUSE to start when their file was left behind (#80).

Every other relocated path warns and continues. These two raise, because
warn-and-continue on a dedup store is not a lost file -- it is a duplicate job
application sent under the user's name, reported as ordinary activity:

    resolved db absent -> `SeenDb.load` swallows the error and returns an empty set
    -> every lead reads as unseen -> submitted to `Vault.upsert`, whose
    `_resolve_path` DOES now probe `leads_dir/_merged/` by name before creating
    (#81) -> a merged-away lead is usually recognised there and suppressed rather
    than re-created -- but the probe is name-keyed, so a human-merged duplicate
    whose re-scrape has drifted past every name candidate still slips past it (#81's
    residual, #23 territory, out of scope here) and is CREATED afresh as
    `status: new` -> if its twin was already `applied`, a second application goes
    out, counted as `created: N`.

The refusal is SCOPED, and the two stores are scoped differently. `ingest` refuses only
when the run actually writes dedup state, so `--dry-run` and `--sink json` proceed. Every
`track` command refuses, dry runs INCLUDED, because a track dry run reads the #49
dead-letter store to report what it would do -- against a relocated store it reports
nothing to do, which is a silently wrong answer a human then acts on. `doctor` never
refuses either way.

Each "does not refuse" row below plants the same legacy file as the raising rows --
without that, "did not raise" is satisfied by there being nothing to raise about, and
the row passes no matter what the code does. "Did not raise" is also not the whole
property: see the row asserting a dry run leaves the refusal ARMED.
"""
import io
import os
import pathlib

import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config

_LEGACY_TEXT = "legacy dedup state"


class _FakeTab:
    """Enough of the fetcher seam for `Sluice.ingest` to build its Ctx without a
    browser. It must never actually be driven here: an empty source list means the
    engine has nothing to fetch."""
    def create_tab(self, url): raise AssertionError("no source should be fetched")
    def evaluate(self, tab, js): raise AssertionError("no source should be fetched")
    def close_tab(self, tab): return None


@pytest.fixture
def legacy(monkeypatch, tmp_path):
    """chdir into tmp_path and plant BOTH legacy files, so `./seen.db` and
    `./track-seen.db` are files this test owns rather than whatever sits in the
    developer's cwd."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEEN_DB", raising=False)
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    for name in ("seen.db", "track-seen.db"):
        (tmp_path / name).write_text(_LEGACY_TEXT, encoding="utf-8")
    return tmp_path


def _app():
    return Sluice(Config(), fetcher=_FakeTab())


# ── ingest / seen.db ─────────────────────────────────────────────────────────

def test_ingest_refuses_to_run_with_a_relocated_seen_db(legacy):
    with pytest.raises(RuntimeError) as e:
        _app().ingest([])
    assert "seen.db" in str(e.value) and "mv" in str(e.value)


def test_the_refused_legacy_file_is_neither_moved_nor_touched(legacy):
    with pytest.raises(RuntimeError):
        _app().ingest([])
    assert (legacy / "seen.db").read_text(encoding="utf-8") == _LEGACY_TEXT
    resolved = os.path.join(os.environ["XDG_STATE_HOME"], "sluice", "seen.db")
    assert not os.path.exists(resolved), "sluice never moves your data"


def test_a_dry_run_does_not_refuse(legacy):
    """The refusal cannot be placed "after the dry-run branch" -- there is no such
    position. `seen` is built before the branch and reaches the engine on BOTH sides
    (correctly: a dry run that lied about dedup would be useless), so the decision has
    to be made at construction from the same flags the sink choice uses."""
    _app().ingest([], dry_run=True, out=io.StringIO())


def test_a_dry_run_leaves_the_refusal_armed_for_the_next_real_run(legacy):
    """"Did not refuse" is NOT the whole property, and asserting only that hid a real
    defect: `SeenDb.load` opened the resolved path with `sqlite3.connect`, which creates
    a 0-byte file merely by opening it. `paths.resolve` refuses only while the resolved
    path does not EXIST, so that empty file disarmed the refusal permanently -- and the
    sequence was the cautious one, dry run first and then the real run, which then
    proceeded with an empty dedup set and re-submitted every already-known lead to the
    write path -- resurrecting a merged-away one whose title had drifted past the
    archive probe's name candidates (#81's residual).

    The state directory is pre-created here because that is the ordinary condition: every
    other state file (health, disabled sources, the triage audit, track's db, the OAuth
    token) resolves into that same directory, and each of their writers creates it.
    """
    statedir = pathlib.Path(os.environ["XDG_STATE_HOME"]) / "sluice"
    statedir.mkdir(parents=True, exist_ok=True)
    _app().ingest([], dry_run=True, out=io.StringIO())
    assert not (statedir / "seen.db").exists(), \
        "the dry run created the resolved db, which disarms the refusal below"
    with pytest.raises(RuntimeError):
        _app().ingest([])


def test_a_json_sink_does_not_refuse(legacy):
    # --sink json is an explicit request to skip the vault, so it writes no dedup
    # state either and has nothing to lose. Asserted with the same second half as the
    # dry-run row: not refusing is only half the property, and a run that quietly
    # created the resolved db would disarm the refusal for every later real run.
    statedir = pathlib.Path(os.environ["XDG_STATE_HOME"]) / "sluice"
    statedir.mkdir(parents=True, exist_ok=True)
    _app().ingest([], json_sink=True, out=io.StringIO())
    assert not (statedir / "seen.db").exists()
    with pytest.raises(RuntimeError):
        _app().ingest([])


def test_naming_the_seen_db_explicitly_does_not_refuse(legacy, monkeypatch, tmp_path):
    # Immune BY CONSTRUCTION, not by a rule repeated at each site: an explicit env var
    # short-circuits resolution before the legacy check is reached. Without this
    # property, everyone who already exports SEEN_DB would be refused at startup.
    #
    # The observable half: the run must actually have USED the named path, so the
    # per-system location is never touched and the legacy file is left alone. Without
    # these, "did not raise" would also be satisfied by a run that silently resolved
    # somewhere else entirely.
    mine = tmp_path / "mine.db"
    monkeypatch.setenv("SEEN_DB", str(mine))
    _app().ingest([])
    from sluice.core.seendb import SeenDb
    assert SeenDb().path == str(mine)
    assert not (pathlib.Path(os.environ["XDG_STATE_HOME"]) / "sluice" / "seen.db").exists()
    assert (legacy / "seen.db").read_text(encoding="utf-8") == _LEGACY_TEXT


def test_seen_db_defaults_under_the_state_root(monkeypatch):
    from sluice.core.seendb import SeenDb
    monkeypatch.delenv("SEEN_DB", raising=False)
    assert SeenDb().path == os.path.join(
        os.environ["XDG_STATE_HOME"], "sluice", "seen.db")


def test_an_explicit_seen_db_path_beats_the_env_var(monkeypatch, tmp_path):
    # Same factory-precedence rule as HealthStore: an explicit constructor argument
    # must beat the environment, or the suite's own SeenDb(str(tmp_path / ...))
    # constructions would retarget a developer's real dedup store.
    from sluice.core.seendb import SeenDb
    monkeypatch.setenv("SEEN_DB", str(tmp_path / "from-env.db"))
    assert SeenDb(str(tmp_path / "explicit.db")).path == str(tmp_path / "explicit.db")


# ── track / track-seen.db ────────────────────────────────────────────────────
# Worse than seen.db when it goes wrong: app.py derives the `.lastrun` watermark, the
# seen-message set AND the #49 dead-letter store from this one path, so a wrong cwd
# silently empties the whole backlog of un-acted-on proposals.

@pytest.mark.parametrize("call", [
    lambda app: app.track(),
    lambda app: app.track_confirm(lead="x", to="applied"),
    lambda app: app.track_dismiss(message_id="x"),
], ids=["track", "track_confirm", "track_dismiss"])
def test_every_track_command_refuses_with_a_relocated_db(legacy, call):
    with pytest.raises(RuntimeError) as e:
        call(_app())
    assert "track-seen.db" in str(e.value)


def test_the_loader_alone_never_refuses(legacy):
    """The refusal is NOT in the loader. `doctor()` calls `load_track_config()`, and a
    diagnostic that refuses to report is the opposite of a diagnostic -- the relocated
    file is precisely what someone runs doctor to be told about.
    """
    from sluice.track.config import load_track_config
    assert load_track_config().seen_db.endswith("track-seen.db")


def test_doctor_never_refuses(legacy):
    # ...and asserts doctor actually REPORTED, not merely that it returned. "Did not
    # raise" is also satisfied by a doctor that silently did nothing, which is the same
    # weak shape as the refusal rows that plant no legacy file.
    rep = _app().doctor(offline=True)
    assert rep.checks, "doctor returned without running any check"
    assert hasattr(rep, "exit_code")
