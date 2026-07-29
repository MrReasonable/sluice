"""The two dedup stores REFUSE to start when their file was left behind (#80).

Every other relocated path warns and continues. These two raise, because
warn-and-continue on a dedup store is not a lost file -- it is a duplicate job
application sent under the user's name, reported as ordinary activity:

    resolved db absent -> `SeenDb.load` swallows the error and returns an empty set
    -> every lead reads as unseen -> `Vault._resolve_path` builds candidates only
    under `leads_dir` and never consults `leads_dir/_merged/` (#81, true today and
    out of scope) -> every human-merged duplicate whose posting is still live is
    CREATED afresh as `status: new` -> if its twin was already `applied`, a second
    application goes out, counted as `created: N`.

The refusal has to be SCOPED, though: it must never fire on a command that reads.
Each "does not refuse" row below plants the same legacy file as the raising rows --
without that, "did not raise" is satisfied by there being nothing to raise about, and
the row passes no matter what the code does.
"""
import io
import os

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


def test_a_json_sink_does_not_refuse(legacy):
    # --sink json is an explicit request to skip the vault, so it writes no dedup
    # state either and has nothing to lose.
    _app().ingest([], json_sink=True, out=io.StringIO())


def test_naming_the_seen_db_explicitly_does_not_refuse(legacy, monkeypatch, tmp_path):
    # Immune BY CONSTRUCTION, not by a rule repeated at each site: an explicit env var
    # short-circuits resolution before the legacy check is reached. Without this
    # property, everyone who already exports SEEN_DB would be refused at startup.
    monkeypatch.setenv("SEEN_DB", str(tmp_path / "mine.db"))
    _app().ingest([])


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
    _app().doctor(offline=True)
