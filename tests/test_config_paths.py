"""The sub-app loaders resolve their path fields per-system (#80).

Every row here drives a REAL loader rather than `paths.resolve` directly. That is the
point: `tests/test_paths.py` tests the resolver, and a resolver nobody calls passes all
of it. What can only be seen here is whether a loader actually routes its path fields
through it -- and whether a blank default survives one, which is the hazard the last
group pins shut.

`XDG_STATE_HOME` is already pinned into `tmp_path` by the autouse sandbox in
`conftest.py`, so these rows read it rather than setting their own: a row that re-pinned
it would still pass with the sandbox removed, and the sandbox is what stops this file
writing into a developer's real `~/.local/state`.
"""
import os

import pytest

from sluice.track.config import TrackConfig, load_track_config
from sluice.triage.config import TriageConfig, load_triage_config

# (label, dataclass, loader, field, yaml block, basename under the state root).
# The env var each field honours is NOT listed: seen_db and token_path have none
# (table rows #3 and #8), and asserting a shared shape they do not share is how the
# original enumeration for this issue went wrong.
_ROWS = [
    ("track.seen_db", TrackConfig, load_track_config, "seen_db", "track", "track-seen.db"),
    ("track.token_path", TrackConfig, load_track_config, "token_path", "track",
     "google_token.json"),
    ("triage.audit_jsonl", TriageConfig, load_triage_config, "audit_jsonl", "triage",
     "triage-audit.jsonl"),
]
_IDS = [r[0] for r in _ROWS]


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch):
    """Every row here asserts what a FRESH install gets, so the developer's own
    SLUICE_CONFIG (and TRIAGE_AUDIT) must not reach the loader -- it would supply a
    real path and the row would pass for the wrong reason."""
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    monkeypatch.delenv("TRIAGE_AUDIT", raising=False)


@pytest.mark.parametrize("label,cls,loader,fieldname,block,base", _ROWS, ids=_IDS)
def test_path_field_dataclass_default_is_blank(label, cls, loader, fieldname, block, base):
    # Blank is not tidiness: resolution is `env or config key or XDG`, so a non-empty
    # default is ALWAYS truthy and short-circuits the chain before the XDG location is
    # ever reached. The sweep would then move nothing while every test stayed green.
    assert getattr(cls(), fieldname) == ""


@pytest.mark.parametrize("label,cls,loader,fieldname,block,base", _ROWS, ids=_IDS)
def test_unconfigured_path_field_lands_under_the_state_root(
        label, cls, loader, fieldname, block, base):
    # No config file at all -- what a fresh install actually runs.
    expected = os.path.join(os.environ["XDG_STATE_HOME"], "sluice", base)
    assert getattr(loader(None), fieldname) == expected


@pytest.mark.parametrize("label,cls,loader,fieldname,block,base", _ROWS, ids=_IDS)
def test_empty_config_block_still_lands_under_the_state_root(
        label, cls, loader, fieldname, block, base, tmp_path):
    # A config file whose block exists but sets nothing: the overlay loop runs zero
    # times. Resolution must not live INSIDE that loop, or a user with a `track:` block
    # gets a different path from a user without one.
    p = tmp_path / "c.yaml"
    p.write_text(f"{block}: {{}}\n", encoding="utf-8")
    expected = os.path.join(os.environ["XDG_STATE_HOME"], "sluice", base)
    assert getattr(loader(str(p)), fieldname) == expected


@pytest.mark.parametrize("label,cls,loader,fieldname,block,base", _ROWS, ids=_IDS)
def test_configured_path_field_survives_the_loader(
        label, cls, loader, fieldname, block, base, tmp_path):
    # The other direction: blanking the default must cost no override capability. A
    # configured value is returned verbatim -- resolve's config term, not its XDG term.
    mine = tmp_path / "mine" / base
    p = tmp_path / "c.yaml"
    p.write_text(f'{block}:\n  {fieldname}: "{mine}"\n', encoding="utf-8")
    assert getattr(loader(str(p)), fieldname) == str(mine)


@pytest.mark.parametrize("label,cls,loader,fieldname,block,base", _ROWS, ids=_IDS)
def test_a_blank_path_never_escapes_a_loader(
        label, cls, loader, fieldname, block, base, tmp_path):
    """`""` must never reach a consumer, from any of the three ways it can arise.

    It is not merely untidy downstream. `app.py:118`'s `os.makedirs(os.path.dirname(p))`
    has no `or "."`, so `""` raises FileNotFoundError out of `_save_seen`; and
    `deadletter_path("")` names a DIFFERENT #49 store from the one `track run` opened,
    so `track confirm` would report success against an empty database while the real
    entry re-surfaces forever. Both are silent in the direction that matters.

    The third way is the one a caller can trigger deliberately: a user who writes
    `seen_db: ""` in their config. `v is not None` admits it, the loop sets it, and only
    resolution running AFTER the loop turns it back into a real path.
    """
    blank = tmp_path / "blank.yaml"
    blank.write_text(f'{block}:\n  {fieldname}: ""\n', encoding="utf-8")
    empty = tmp_path / "empty.yaml"
    empty.write_text(f"{block}: {{}}\n", encoding="utf-8")
    for cfg in (loader(None), loader(str(empty)), loader(str(blank))):
        assert getattr(cfg, fieldname) != ""
