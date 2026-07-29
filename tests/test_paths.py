"""Per-system path resolution (#80).

Every test here controls its own environment explicitly rather than relying on the
autouse fixture, because these are the tests OF the resolver: a test that inherited
its answer from the sandbox could not tell a working resolver from a broken one.
"""
import os
import subprocess
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


@pytest.mark.parametrize("bad", ["relative/state", "state", "./state", "../state", ""])
@pytest.mark.parametrize("kind,var,tail", [
    ("config", "XDG_CONFIG_HOME", ".config"),
    ("state", "XDG_STATE_HOME", ".local/state"),
    ("cache", "XDG_CACHE_HOME", ".cache"),
])
def test_a_relative_xdg_root_is_ignored(monkeypatch, kind, var, tail, bad):
    """The XDG spec says a relative base directory MUST be ignored, and here that rule
    is the feature itself: `XDG_STATE_HOME=relative/state` resolved to
    `relative/state/sluice/seen.db` -- a CWD-RELATIVE store, the exact class of path #80
    exists to remove, reintroduced through the variable meant to remove it.

    Ignored rather than rejected: a bad value in someone's environment must not stop
    sluice running, and the fallback is always correct.
    """
    monkeypatch.setenv("HOME", "/h")
    monkeypatch.setenv(var, bad)
    got = paths.resolve(env_var=None, config_value="", kind=kind, name="n")
    assert got == f"/h/{tail}/sluice/n"
    assert os.path.isabs(got)


def test_ignoring_a_relative_xdg_root_is_said_out_loud(monkeypatch, tmp_path, caplog):
    """Ignoring it SILENTLY relocates the user's store, which is the harm this module
    exists to prevent rather than a lesser version of it.

    Measured before this warning: `XDG_STATE_HOME=relative/state` with a real two-row
    `seen.db` under `<cwd>/relative/state/sluice/` resolved to the XDG default, loaded an
    EMPTY dedup set, printed nothing and exited 0. Neither the warn tier nor the refusal
    can cover it -- `_LEGACY` holds cwd-relative names, and the abandoned location here is
    wherever the user last ran from.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    out = paths.resolve(env_var=None, config_value="", kind="state", name="seen.db")
    assert "XDG_STATE_HOME" in caplog.text
    assert "relative/state" in caplog.text, "the ignored value is not named"
    assert out in caplog.text or str(tmp_path) in caplog.text, "the used directory is not named"


def test_an_absolute_xdg_root_warns_about_nothing(monkeypatch, tmp_path, caplog):
    # The other arm: the ordinary case must stay quiet, or the warning is noise and gets
    # tuned out exactly when it matters.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    paths.resolve(env_var=None, config_value="", kind="state", name="seen.db")
    assert not caplog.text


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


def test_a_dangling_legacy_store_still_refuses(monkeypatch, tmp_path):
    """`os.path.exists` FOLLOWS the link and calls a broken one absent, so the refusal
    never fired and the run proceeded with an empty dedup set -- the #81 harm this whole
    check exists to prevent, reached by the one shape most likely to occur (the remedy
    itself moves links, and `mv` moves the link rather than its target).
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("SEEN_DB", raising=False)
    legacy = tmp_path / "seen.db"
    legacy.symlink_to(tmp_path / "moved-away.db")
    assert not os.path.exists(legacy) and os.path.lexists(legacy)
    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                      name="seen.db", legacy=str(legacy), fatal=True)
    assert str(legacy) in str(e.value)


def test_a_dangling_destination_still_refuses(monkeypatch, tmp_path):
    """The two sides answer DIFFERENT questions, which is why they use different calls.

    On the legacy side the question is "is there state here", and a broken link is state.
    On the destination side it is "has the migration already happened", and a broken link
    means it has NOT -- so `lexists` there only suppresses the notice while protecting
    nothing, since `[ ! -L DST ]` in the remedy is what actually stops the move. Measured
    with `lexists` on this side: resolve returned silently and left the store behind, with
    no notice naming either path.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("SEEN_DB", raising=False)
    legacy = tmp_path / "seen.db"
    legacy.write_text("dedup state", encoding="utf-8")
    resolved = tmp_path / "state" / "sluice" / "seen.db"
    resolved.parent.mkdir(parents=True)
    resolved.symlink_to(tmp_path / "nowhere.db")

    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                      name="seen.db", legacy=str(legacy), fatal=True)
    assert str(legacy) in str(e.value) and str(resolved) in str(e.value)


def test_the_remedy_refuses_to_move_onto_a_dangling_destination(monkeypatch, tmp_path):
    """Execute the generated command against a dangling destination and assert it moved
    nothing. `[ ! -e DST ]` alone is TRUE for a broken link, so the `mv` ran, clobbered
    the link and left the store somewhere nobody looks.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("SEEN_DB", raising=False)
    legacy = tmp_path / "seen.db"
    legacy.write_text("dedup state", encoding="utf-8")
    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                      name="seen.db", legacy=str(legacy), fatal=True)
    remedy = str(e.value).split("run:", 1)[1].split("   (")[0].strip()

    # Stage the dangling destination the remedy is about to be pointed at.
    resolved = tmp_path / "state" / "sluice" / "seen.db"
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.symlink_to(tmp_path / "nowhere.db")
    # `shell=True` is the POINT: the artefact under test is a shell command string we
    # tell a human to paste, and `[ ! -e ]`/`[ ! -L ]` only mean anything to a shell.
    # The string comes from our own resolver over a tmp_path, not from user input.
    subprocess.run(remedy, shell=True, cwd=tmp_path)  # noqa: S602

    assert legacy.exists(), "the remedy moved the store onto a dangling destination"
    assert open(legacy, encoding="utf-8").read() == "dedup state"


def test_a_dangling_companion_is_still_named_in_the_remedy(monkeypatch, tmp_path):
    """The THIRD `lexists` site, which an aggregate witness could not see.

    One mutant flipping the shared pattern reddened two tests, which read as "both sites
    pinned" -- but per site it is legacy 1, destination 1, sidecar 0. With `exists` here
    the remedy omitted a dangling `.deadletter.db` entirely: the store then moves,
    `lexists(legacy)` goes false, the refusal is disarmed for good, and `open_entries`
    reads empty -- the #49 backlog gone with nothing said.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("TRACK_SEEN_DB", raising=False)
    legacy = tmp_path / "track-seen.db"
    legacy.write_text("watermark", encoding="utf-8")
    companion = tmp_path / "track-seen.db.deadletter.db"
    companion.symlink_to(tmp_path / "moved-away.db")

    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var="TRACK_SEEN_DB", config_value="", kind="state",
                      name="track-seen.db", legacy=str(legacy), fatal=True)
    assert str(companion) in str(e.value), (
        "a dangling companion was left out of the remedy, so following it disarms the "
        "refusal and loses the dead-letter backlog")


def test_the_remedy_says_how_many_companions_must_move(monkeypatch, tmp_path):
    """The sentence, not just the `mv` operands, which is what the sidecar test pins.

    Deleting the whole "N companion file(s) must move with it" clause was measurably
    green: the existing row is satisfied by the companion appearing in the command. The
    sentence is what tells a human that a partial paste LOSES the last-run watermark and
    the un-acted-on proposal backlog -- a command they can read but not weigh.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("TRACK_SEEN_DB", raising=False)
    legacy = tmp_path / "track-seen.db"
    legacy.write_text("watermark", encoding="utf-8")
    (tmp_path / "track-seen.db.lastrun").write_text("2026-07-10", encoding="utf-8")
    (tmp_path / "track-seen.db.deadletter.db").write_text("rows", encoding="utf-8")

    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var="TRACK_SEEN_DB", config_value="", kind="state",
                      name="track-seen.db", legacy=str(legacy), fatal=True)
    msg = str(e.value)
    assert "2 companion file(s)" in msg, "the count is missing or wrong"
    assert "watermark" in msg and "backlog" in msg, (
        "the message names the command but never says what a partial paste costs")


def test_an_orphaned_companion_still_refuses(monkeypatch, tmp_path):
    """A companion is legacy state in its OWN right, not only an escort for the store.

    Keyed on the store alone this went silent in exactly one cell: a `.deadletter.db`
    holding un-acted-on proposals whose `track-seen.db` was already gone resolved quietly,
    and `track run` then read the NEW location and printed `open=0` -- #49's whole backlog
    invisible, with nothing said anywhere.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("TRACK_SEEN_DB", raising=False)
    legacy = tmp_path / "track-seen.db"          # deliberately NOT created
    companion = tmp_path / "track-seen.db.deadletter.db"
    companion.write_text("rows", encoding="utf-8")

    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var="TRACK_SEEN_DB", config_value="", kind="state",
                      name="track-seen.db", legacy=str(legacy), fatal=True)
    msg = str(e.value)
    assert str(companion) in msg
    assert "1 companion file(s)" in msg, "the count is wrong when the store is absent"
    # and the remedy must not try to move a store that is not there
    assert f"mv {legacy} " not in msg, "the remedy moves an absent store, so step one fails"


def test_a_companion_left_behind_by_a_hand_move_still_refuses(monkeypatch, tmp_path):
    """The cell a user reaches by DOING WHAT THE NOTICE SAYS, and the one a first fix missed.

    Gating the companions on the STORE's destination -- `(store or companions) and not
    exists(resolved)` -- closes only the orphan case. Someone who reads the refusal and
    hand-moves just the store leaves `.lastrun` and `.deadletter.db` behind; the
    destination store now exists, the whole clause goes false, and `track run` reads the
    new location and prints `open=0` with #49's backlog sitting at the old path. Measured
    silent. Each file is judged against ITS OWN destination now.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("TRACK_SEEN_DB", raising=False)
    legacy = tmp_path / "track-seen.db"                    # already hand-moved
    resolved = tmp_path / "state" / "sluice" / "track-seen.db"
    resolved.parent.mkdir(parents=True)
    resolved.write_text("moved by hand", encoding="utf-8")
    companion = tmp_path / "track-seen.db.deadletter.db"   # left behind
    companion.write_text("rows", encoding="utf-8")

    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var="TRACK_SEEN_DB", config_value="", kind="state",
                      name="track-seen.db", legacy=str(legacy), fatal=True)
    msg = str(e.value)
    assert str(companion) in msg
    assert "1 companion file(s)" in msg
    # ...and it must not claim a store remains at a path the user already cleared
    assert f"a file remains at {legacy}" not in msg


def test_a_dangling_link_is_not_told_to_run_cp_dash_L(monkeypatch, tmp_path):
    """`cp -L` on a dangling link exits 1 (measured), so offering it for a link that no
    longer resolves sends the user in a circle. Resolvable and broken links get different
    advice, and a broken one is told what is actually wrong.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("SEEN_DB", raising=False)
    legacy = tmp_path / "seen.db"
    legacy.symlink_to(tmp_path / "moved-away.db")
    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                      name="seen.db", legacy=str(legacy), fatal=True)
    msg = str(e.value)
    assert "cp -L" not in msg, "a dangling link was told to run a command that exits 1"
    assert "no longer exists" in msg


def test_a_resolvable_link_is_still_told_to_copy_the_target(monkeypatch, tmp_path):
    # The other arm, so the split above cannot be satisfied by dropping the advice.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("SEEN_DB", raising=False)
    target = tmp_path / "real-seen.db"
    target.write_text("dedup state", encoding="utf-8")
    legacy = tmp_path / "seen.db"
    legacy.symlink_to(target)
    with pytest.raises(RuntimeError) as e:
        paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                      name="seen.db", legacy=str(legacy), fatal=True)
    assert "cp -L" in str(e.value)


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
        # The names `paths.resolve` is bound to IN THIS FILE, read off its own imports
        # rather than hard-coded. `core/app.py` imports it as `_resolve_path` to avoid
        # colliding with `dossier_cache`'s local SSRF resolver, and a sweep matching only
        # the bare name missed both of that file's call sites -- which this test caught
        # the first time it ran. A hard-coded pair would have missed the NEXT alias just
        # as silently, so the aliases are derived too.
        aliases = {"resolve"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "sluice.core.paths":
                aliases |= {a.asname or a.name for a in node.names if a.name == "resolve"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fn_name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if fn_name not in aliases:
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
