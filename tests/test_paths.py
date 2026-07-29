"""Per-system path resolution (#80).

Every test here controls its own environment explicitly rather than relying on the
autouse fixture, because these are the tests OF the resolver: a test that inherited
its answer from the sandbox could not tell a working resolver from a broken one.
"""
import os
from pathlib import Path

import pytest

from sluice.core import paths


def test_state_file_uses_xdg_state_home(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/x/state")
    assert paths.resolve(env_var=None, config_value="", kind="state",
                         name="seen.db") == "/x/state/sluice/seen.db"


@pytest.mark.parametrize("kind,var,root", [
    ("config", "XDG_CONFIG_HOME", "/x/cfg"),
    ("state", "XDG_STATE_HOME", "/x/state"),
    ("cache", "XDG_CACHE_HOME", "/x/cache"),
])
def test_each_kind_resolves_under_its_own_xdg_root(monkeypatch, kind, var, root):
    monkeypatch.setenv(var, root)
    assert paths.resolve(env_var=None, config_value="", kind=kind,
                         name="n") == f"{root}/sluice/n"


@pytest.mark.parametrize("kind,tail", [
    ("config", ".config"), ("state", ".local/state"), ("cache", ".cache"),
])
def test_falls_back_under_home_when_xdg_unset(monkeypatch, kind, tail):
    """The macOS-relevant branch: XDG_* is conventionally unset there, so this is
    the path most users actually get."""
    for v in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HOME", "/h")
    assert paths.resolve(env_var=None, config_value="", kind=kind,
                         name="n") == f"/h/{tail}/sluice/n"


def test_env_var_wins_over_both_config_and_xdg(monkeypatch):
    """Sets env AND config on the SAME call, so swapping the two operands in the
    chain reddens this. A row that sets only one cannot see the inversion."""
    monkeypatch.setenv("XDG_STATE_HOME", "/x/state")
    monkeypatch.setenv("SEEN_DB", "/from/env")
    assert paths.resolve(env_var="SEEN_DB", config_value="/from/config",
                         kind="state", name="seen.db") == "/from/env"


def test_config_value_wins_over_xdg_when_env_unset(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/x/state")
    monkeypatch.delenv("SEEN_DB", raising=False)
    assert paths.resolve(env_var="SEEN_DB", config_value="/from/config",
                         kind="state", name="seen.db") == "/from/config"


def test_empty_config_value_abstains(monkeypatch):
    """`""` means unset, so resolution falls through to XDG -- the blanked-default
    property the whole sweep depends on. A non-empty default short-circuits here and
    the XDG location is never reached."""
    monkeypatch.setenv("XDG_STATE_HOME", "/x/state")
    monkeypatch.delenv("SEEN_DB", raising=False)
    assert paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                         name="seen.db") == "/x/state/sluice/seen.db"


def test_resolving_creates_nothing(monkeypatch, tmp_path):
    """No writes at resolution time, or `--dry-run` would touch the disk."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    out = paths.resolve(env_var=None, config_value="", kind="state", name="seen.db")
    assert not os.path.exists(os.path.dirname(out))
    assert not os.path.exists(out)


def _legacy_setup(monkeypatch, tmp_path):
    """A planted legacy file and a resolved location that does not exist yet.

    Every negative row below plants the SAME file: without it, "does not raise" is
    satisfied by there being nothing to raise about, and the row passes for the wrong
    reason no matter what the resolver does.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("SEEN_DB", raising=False)
    legacy = tmp_path / "seen.db"
    legacy.write_text("dedup state", encoding="utf-8")
    return str(legacy)


def test_legacy_file_warns_and_uses_the_resolved_path(monkeypatch, tmp_path, caplog):
    legacy = _legacy_setup(monkeypatch, tmp_path)
    out = paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                        name="seen.db", legacy=legacy)
    assert out == str(tmp_path / "state" / "sluice" / "seen.db")
    assert legacy in caplog.text and out in caplog.text


def test_legacy_file_is_never_moved(monkeypatch, tmp_path):
    """Never-move, pinned rather than asserted in a comment."""
    legacy = _legacy_setup(monkeypatch, tmp_path)
    out = paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                        name="seen.db", legacy=legacy)
    assert open(legacy, encoding="utf-8").read() == "dedup state"
    assert not os.path.exists(out)


def test_fatal_legacy_raises_naming_both_paths(monkeypatch, tmp_path):
    legacy = _legacy_setup(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                      name="seen.db", legacy=legacy, fatal=True)
    assert legacy in str(e.value) and "mv" in str(e.value)
    assert open(legacy, encoding="utf-8").read() == "dedup state"


def test_fatal_legacy_silent_when_env_var_names_a_path(monkeypatch, tmp_path):
    legacy = _legacy_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("SEEN_DB", "/named/by/env")
    assert paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                         name="seen.db", legacy=legacy, fatal=True) == "/named/by/env"


def test_fatal_legacy_silent_when_config_names_a_path(monkeypatch, tmp_path):
    legacy = _legacy_setup(monkeypatch, tmp_path)
    assert paths.resolve(env_var="SEEN_DB", config_value="/named/by/config",
                         kind="state", name="seen.db", legacy=legacy,
                         fatal=True) == "/named/by/config"


def test_legacy_silent_when_the_resolved_path_already_exists(monkeypatch, tmp_path):
    legacy = _legacy_setup(monkeypatch, tmp_path)
    resolved = tmp_path / "state" / "sluice" / "seen.db"
    resolved.parent.mkdir(parents=True)
    resolved.write_text("migrated", encoding="utf-8")
    assert paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                         name="seen.db", legacy=legacy, fatal=True) == str(resolved)


def test_unknown_kind_raises_and_lists_the_valid_ones():
    """Fail loudly at construction: an unknown name raises and names the valid set
    rather than falling through to a default."""
    with pytest.raises(ValueError) as e:
        paths.resolve(env_var=None, config_value="", kind="data", name="n")
    assert "data" in str(e.value)
    for valid in ("config", "state", "cache"):
        assert valid in str(e.value)


# ── the legacy table ─────────────────────────────────────────────────────────
# The cwd-relative locations these paths had before #80 live in `paths`, not at seven
# call sites. That is what gives the migration one home -- and it is why the
# definition-of-done grep for surviving `"./"` literals excludes this module: they must
# survive HERE and nowhere else under sluice/.

# (name, kind) for every path the sweep moves. PINNED: a new moving path with no table
# entry silently loses its migration warning, and nothing else would notice.
_MOVING = [
    ("seen.db", "state"), ("track-seen.db", "state"),
    ("sluice_health.json", "state"), ("sluice_disabled.json", "state"),
    ("triage-audit.jsonl", "state"), ("google_token.json", "state"),
    ("dossiers", "cache"),
]


def test_every_legacy_entry_is_the_cwd_relative_form_of_its_own_name():
    # Not a coincidence worth leaving implicit: every one of these was `./<basename>`,
    # which is what made a `cd` silently repoint them all at once. Asserting the whole
    # mapping (rather than spot-checking one) means an entry that drifts to some other
    # basename -- a legacy check that then never fires -- reddens here.
    assert paths._LEGACY == {name: f"./{name}" for name, _ in _MOVING}


def _resolve_call_names():
    """Every `name=` literal passed to `resolve(...)` anywhere under `sluice/`.

    DISCOVERED, not hand-listed. `_MOVING` above and `_LEGACY` are two literals the same
    author edits together, so comparing them catches a typo and nothing else: a NEW
    `resolve(name=...)` call site with no `_LEGACY` entry leaves both sides unchanged,
    the migration warning for that path silently never fires, and everything stays green
    -- exactly the case `_LEGACY`'s own comment claims nothing would notice.
    """
    import ast
    names = set()
    for path in sorted((Path(__file__).resolve().parent.parent / "sluice").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fn_name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            # `_resolve_path` too: `core/app.py` imports `resolve` under an alias to
            # avoid colliding with `dossier_cache`'s local SSRF resolver. A sweep that
            # matched only the bare name silently missed both of app.py's call sites --
            # caught when this test first ran, which is the whole argument for
            # discovering rather than hand-listing.
            if fn_name not in ("resolve", "_resolve_path"):
                continue
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    names.add(kw.value.value)
    return names


def test_every_resolve_call_site_has_a_legacy_entry_or_is_deliberately_exempt():
    called = _resolve_call_names()
    assert called, "found no resolve(name=...) call sites -- the sweep would be vacuous"
    # The config file is the one deliberate exemption: an unset SLUICE_CONFIG meant "no
    # config file", never "./config.yaml", so there is nothing to migrate from. Listing
    # it here rather than in `_LEGACY` keeps the exemption explicit and reviewable.
    exempt = {"config.yaml"}
    missing = called - set(paths._LEGACY) - exempt
    assert not missing, (
        "these paths resolve through paths.resolve but have no _LEGACY entry, so a user "
        f"upgrading is never told their old file was left behind: {sorted(missing)}")
    stale = set(paths._LEGACY) - called
    assert not stale, (
        f"_LEGACY names paths nothing resolves any more: {sorted(stale)}")


@pytest.mark.parametrize("name,kind", _MOVING, ids=[n for n, _ in _MOVING])
def test_the_table_supplies_the_legacy_path_when_the_caller_names_none(
        monkeypatch, tmp_path, caplog, name, kind):
    # The production call sites pass no `legacy=` at all, so a table entry that is
    # missing or misspelled makes the warning unreachable while every explicit-legacy
    # row above still passes. chdir is what lets `./<name>` be a planted file rather
    # than whatever happens to sit in the developer's cwd.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    (tmp_path / name).write_text("legacy", encoding="utf-8")
    out = paths.resolve(env_var=None, config_value="", kind=kind, name=name)
    assert name in caplog.text and out in caplog.text
    assert (tmp_path / name).read_text(encoding="utf-8") == "legacy"   # never moved


def test_the_config_file_has_no_legacy_entry(monkeypatch, tmp_path, caplog):
    # #1 is the one moving path with nothing to migrate FROM: an unset SLUICE_CONFIG
    # meant "no config file", not "./config.yaml". A `config.yaml` sitting in someone's
    # cwd is somebody else's file, and warning about it would be noise on every run.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    (tmp_path / "config.yaml").write_text("store: vault\n", encoding="utf-8")
    assert paths.config_file() == str(tmp_path / "cfg" / "sluice" / "config.yaml")
    assert caplog.text == ""
