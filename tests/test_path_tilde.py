"""A `~` in an explicitly-named path is expanded, on every path family (#80 follow-up).

`resolve` expands `~` in its XDG FALLBACK (`os.path.expanduser(fallback)`) but used to
return an env-var / config-key value VERBATIM. Measured harm with `SEEN_DB=~/state/seen.db`
and a real two-row store under `$HOME`:

  1. `resolve` -> the literal `'~/state/seen.db'`.
  2. NOTHING is said. The explicit branch short-circuits before the relocation check, so
     the one mechanism that would have spoken is bypassed by construction.
  3. `SeenDb.load()` -> `set()`. An EMPTY dedup set, indistinguishable from a first run --
     the #81 harm: every already-known lead reads as unseen and is re-submitted to the
     write path, which for a lead whose twin was already `applied` is a second application
     under the user's name.
  4. `SeenDb.save()` -> a LITERAL `~` directory under the CWD (`./~/state/seen.db`), the
     exact class of path #80 exists to remove.
  5. Every run AFTER that one dies in `load()` with `sqlite3.OperationalError: invalid uri
     authority: ~` -- `existing_db_uri` quotes the path, `quote` leaves `~` unreserved, and
     `file://` + `~/state/...` parses `~` as the URI authority. So the store the tool wrote
     in step 4 is one it can never read back, and the message names neither the store nor a
     remedy. That step is NOT specific to `~`: every non-absolute spelling failed the same
     way, so it is fixed where it belongs, in `existing_db_uri` -- see
     `tests/test_paths.py`. Expanding `~` here does not make anything absolute and must not
     be described as if it did.

The absence of any tilde coverage on a `paths.py`-resolved path is why that survived: the
hazard IS tested, but only for the vault, which expands in its own constructor. So this file
drives every family through the REAL entry point a user reaches it by, and
`test_every_resolve_call_site_has_a_tilde_row` fails the build if a new family arrives
without one.

Every row uses an ACTUAL `~` value. `expanduser` is a no-op on absolute and relative paths,
so a row built only from `tmp_path` would pass with the fix reverted and witness nothing.
"""
import ast
import os
import pathlib
import re

import pytest

from sluice.core import paths

# Sandbox `$HOME` is `tmp_path / "home"` (conftest `_pin_paths`), so `~` is a real
# directory this suite owns and an expansion is checkable against it.
_HOME = "home"


def _home(tmp_path) -> pathlib.Path:
    return tmp_path / _HOME


# ── the resolver itself ──────────────────────────────────────────────────────
# Both explicit DOORS, because they are separate terms of one `or` and a fix applied to
# whichever the author had in mind leaves the other returning a literal. The families
# below prove that neither door is theoretical: `seen.db` reaches resolution only by env
# var, `track.seen_db` only by config key.

@pytest.mark.parametrize("kind", ["config", "state", "cache"])
def test_a_tilde_in_the_env_var_door_is_expanded(monkeypatch, tmp_path, kind):
    monkeypatch.setenv("SEEN_DB", "~/state/seen.db")
    out = paths.resolve(env_var="SEEN_DB", config_value="", kind=kind, name="seen.db")
    assert out == str(_home(tmp_path) / "state" / "seen.db")


@pytest.mark.parametrize("kind", ["config", "state", "cache"])
def test_a_tilde_in_the_config_key_door_is_expanded(tmp_path, kind):
    out = paths.resolve(env_var=None, config_value="~/state/seen.db", kind=kind,
                        name="seen.db")
    assert out == str(_home(tmp_path) / "state" / "seen.db")


def test_the_env_var_door_still_outranks_the_config_key_after_expansion(
        monkeypatch, tmp_path):
    """Expansion must not reorder the chain. Both terms carry a tilde, so a fix that
    expanded the config value and returned it would pass every row above."""
    monkeypatch.setenv("SEEN_DB", "~/from-env/seen.db")
    out = paths.resolve(env_var="SEEN_DB", config_value="~/from-config/seen.db",
                        kind="state", name="seen.db")
    assert out == str(_home(tmp_path) / "from-env" / "seen.db")


# ── what must NOT change ─────────────────────────────────────────────────────
# A path-layer fix over-corrects, and the suite does not notice: #1 PR B's symlink guard
# broke the flat default with every test green, because every test built the configured
# layout. These pin the values expansion must leave exactly as they are -- and the last
# two are the ones a later "make it consistent" edit would break.

@pytest.mark.parametrize("value", [
    "/abs/state/seen.db",            # already absolute -- the ordinary configured case
    "relative/state/seen.db",        # relative: unchanged here, see the note below
    "./state/seen.db",
    "../state/seen.db",
    "a~b/seen.db",                   # a tilde that does not LEAD is not a home reference
    "/x/~/seen.db",                  # ...including one mid-path
    # An unknown user: expanduser's own no-op contract. `~user` is the ONE spelling that
    # ignores `$HOME` entirely and reads the real account database, so the sandbox cannot
    # contain it -- `~root` would resolve to a real `/var/root`. Safe here only because
    # the assertion is that the value comes back UNCHANGED, which a real account fails
    # rather than leaks; any future row in this family must keep that shape and must name
    # an account that cannot exist.
    "~nosuchuser9911/seen.db",
])
def test_a_path_without_a_leading_home_reference_is_untouched(value):
    """A RELATIVE explicit path stays relative -- returned as the caller gave it.

    An earlier version of this docstring justified that by pointing at the XDG root being
    "spec-bound to be ignored", which is a non-sequitur: that spec governs `XDG_*_HOME`,
    not `SEEN_DB`. Worse, it read as certifying the relative case as fine, and it was not
    -- every non-absolute spelling died in `existing_db_uri` with `invalid uri authority`,
    exactly as the tilde one did. That is fixed at the point of use (see
    `tests/test_paths.py::test_a_non_absolute_store_still_opens`), which is why the value
    can be left alone HERE without leaving a bug behind: normalising it would change what
    every caller sees, and absolutising at URI-construction time changes nothing.
    """
    assert paths.resolve(env_var=None, config_value=value, kind="state",
                         name="seen.db") == value


@pytest.mark.parametrize("kind,var,tail", [
    ("config", "XDG_CONFIG_HOME", ".config"),
    ("state", "XDG_STATE_HOME", ".local/state"),
    ("cache", "XDG_CACHE_HOME", ".cache"),
])
def test_a_tilde_xdg_root_is_still_ignored_not_expanded(monkeypatch, tmp_path, kind, var,
                                                        tail):
    """The XDG ROOT is a different variable with a different owner, and it must not be
    swept along. The base-directory spec requires a non-absolute base to be IGNORED, and
    `~/state` is not absolute -- so expanding it here would honour a value the spec says
    to drop, in the one module whose job is to obey that spec. Loud already (the ignore
    warns), so nothing is lost by leaving it."""
    monkeypatch.setenv(var, "~/state")
    out = paths.resolve(env_var=None, config_value="", kind=kind, name="n")
    assert out == str(_home(tmp_path) / tail / "sluice" / "n")


# ── every family, through its real entry point ───────────────────────────────
# Not `paths.resolve` directly: a resolver nobody calls passes all of it. What can only be
# seen here is whether the family's OWN door carries a tilde through -- and the doors are
# not uniform, which is the whole reason this is enumerated. `seen.db` has an env var and
# no config key; `track.seen_db` a config key and no env var; `dossiers` and
# `triage-audit.jsonl` have both.
#
# (label, name, env var or None, config-yaml block or None, config key or None, probe)
# `probe(value)` returns the resolved path with `value` supplied through this row's door.

def _probe_config_file(_):
    return paths.config_file()


def _probe_disabled(_):
    from sluice.cli import _disabled_path
    return _disabled_path()


def _probe_health(_):
    from sluice.core.health import HealthStore
    return HealthStore().path


def _probe_seendb(_):
    from sluice.core.seendb import SeenDb
    return SeenDb().path


def _probe_dossiers_env(_):
    from sluice.core.app import Sluice
    return Sluice()._dossier_dir()


def _probe_dossiers_key(cfg_path):
    from sluice.core.app import Sluice
    from sluice.core.config import load_config
    return Sluice(load_config(cfg_path))._dossier_dir()


def _probe_track_seen_db(cfg_path):
    from sluice.track.config import load_track_config
    return load_track_config(cfg_path).seen_db


def _probe_track_token(cfg_path):
    from sluice.track.config import load_track_config
    return load_track_config(cfg_path).token_path


def _probe_triage_audit_env(_):
    from sluice.triage.config import load_triage_config
    return load_triage_config(None).audit_jsonl


def _probe_triage_audit_key(cfg_path):
    from sluice.triage.config import load_triage_config
    return load_triage_config(cfg_path).audit_jsonl


_FAMILIES = [
    ("config.yaml/env", "config.yaml", "SLUICE_CONFIG", None, None, _probe_config_file),
    ("sluice_disabled.json/env", "sluice_disabled.json", "SLUICE_DISABLED", None, None,
     _probe_disabled),
    ("sluice_health.json/env", "sluice_health.json", "SLUICE_HEALTH", None, None,
     _probe_health),
    ("seen.db/env", "seen.db", "SEEN_DB", None, None, _probe_seendb),
    ("dossiers/env", "dossiers", "DOSSIER_DIR", None, None, _probe_dossiers_env),
    ("dossiers/key", "dossiers", None, None, "dossier_dir", _probe_dossiers_key),
    ("track-seen.db/key", "track-seen.db", None, "track", "seen_db", _probe_track_seen_db),
    ("google_token.json/key", "google_token.json", None, "track", "token_path",
     _probe_track_token),
    ("triage-audit.jsonl/env", "triage-audit.jsonl", "TRIAGE_AUDIT", None, None,
     _probe_triage_audit_env),
    ("triage-audit.jsonl/key", "triage-audit.jsonl", None, "triage", "audit_jsonl",
     _probe_triage_audit_key),
]
_FAMILY_IDS = [f[0] for f in _FAMILIES]


@pytest.mark.parametrize("label,name,env,block,key,probe", _FAMILIES, ids=_FAMILY_IDS)
def test_each_path_family_expands_a_tilde_through_its_own_door(
        label, name, env, block, key, probe, monkeypatch, tmp_path):
    value = f"~/planted/{name}"
    cfg_path = None
    if env:
        monkeypatch.setenv(env, value)
    else:
        cfg_path = str(tmp_path / f"{label.replace('/', '_')}.yaml")
        body = (f'{key}: "{value}"\n' if block is None
                else f'{block}:\n  {key}: "{value}"\n')
        pathlib.Path(cfg_path).write_text(body, encoding="utf-8")
    assert probe(cfg_path) == str(_home(tmp_path) / "planted" / name)


def _dotted(node):
    """`sluice.core.paths` from the nested Attribute chain the parser builds for it.

    A dotted call is `Attribute(Attribute(Name))`, so a matcher testing
    `isinstance(fn.value, ast.Name)` can only ever see the one-segment case -- which is
    why the branch written for `import sluice.core.paths` never fired.
    """
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(node.id)
    return ".".join(reversed(parts))


def _resolve_call_site_doors(pkg):
    """Every `(name, 'env'|'key')` door `pkg` opens on `paths.resolve`, from the AST.

    Takes the root as an ARGUMENT so the sweep and its verdict can be driven over
    synthetic source. Every assertion in `_check_roster` guards a regression the tree does
    not currently contain, and such a guard cannot be witnessed by a suite in which the
    regression does not occur -- all of them survived deletion while they could only run
    against `sluice/`. Pointed at a planted file instead, each fails on demand.

    Discovery reads each file's OWN bindings rather than matching the name `resolve`, in
    BOTH import styles -- `from ... import resolve as _resolve_path` (which `core/app.py`
    uses, and where two doors live) and `from sluice.core import paths` + `paths.resolve`
    (which nothing in `sluice/` uses today, but which this very test file uses, so it is
    one import-style edit away). Keyed on the original name alone, rewriting
    `sluice/track/config.py` to the attribute form -- no behaviour change whatever --
    silently dropped its two doors and left the whole file green.

    A call site it cannot READ is returned separately rather than skipped. Skipping is the
    fail-open that makes a sweep certify what it never looked at: a `name=` built from a
    module constant is not a call site without a door, it is a call site whose door is
    unknown, and those are opposite answers.
    """
    doors, unreadable = set(), []
    for py in sorted(pathlib.Path(pkg).rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        # `direct`: local names bound to the FUNCTION. `modules`: dotted prefixes bound to
        # the MODULE, so `paths.resolve` and `sluice.core.paths.resolve` both match.
        direct, modules = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                # `node.level` covers `from .paths import resolve` -- a relative import
                # inside `core/`, which an absolute-only match walks straight past.
                is_paths = mod.endswith("core.paths") or (node.level and
                                                          mod.endswith("paths"))
                if any(a.name == "*" for a in node.names) and (
                        is_paths or mod in ("sluice.core", "sluice")):
                    # Bindings are undecidable from the AST alone. Reported, never ignored.
                    unreadable.append(f"{py.name}:{node.lineno} (star import from {mod})")
                elif is_paths:
                    direct |= {a.asname or a.name for a in node.names
                               if a.name == "resolve"}
                elif mod in ("sluice.core", "sluice") or (node.level and mod == ""):
                    modules |= {a.asname or a.name for a in node.names
                                if a.name == "paths"}
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in ("sluice.core.paths", "sluice.core", "sluice"):
                        # `import sluice.core.paths` binds the ROOT name but is called back
                        # through the full dotted CHAIN, so the chain is the usable key --
                        # binding `"sluice"` alone can never match, which is exactly why
                        # the branch written for this case was inert.
                        modules.add(a.asname or a.name)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) \
                    and node.value.id in direct:
                # `_r = resolve` -- a rebinding is as good as an import alias.
                direct |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.FunctionDef) and node.name == "resolve" and \
                    py.name == "paths.py":
                direct.add("resolve")      # the module's own internal call, in config_file
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            hit = ((isinstance(fn, ast.Name) and fn.id in direct)
                   or (isinstance(fn, ast.Attribute) and fn.attr == "resolve"
                       and _dotted(fn.value) in modules))
            if not hit:
                continue
            kw = {k.arg: k.value for k in node.keywords}
            if not isinstance(kw.get("name"), ast.Constant):
                unreadable.append(f"{py.name}:{node.lineno} (name= is not a literal)")
                continue
            nm = kw["name"].value
            env = kw.get("env_var")
            if isinstance(env, ast.Constant) and env.value:
                doors.add((nm, "env"))
            elif not (isinstance(env, ast.Constant) and env.value is None):
                unreadable.append(f"{py.name}:{node.lineno} (env_var= is not a literal)")
            cv = kw.get("config_value")
            if cv is None:
                unreadable.append(f"{py.name}:{node.lineno} (no config_value=)")
            elif not (isinstance(cv, ast.Constant) and cv.value == ""):
                doors.add((nm, "key"))     # anything but a literal "" is a live config door
    return doors, unreadable


def _check_roster(doors, unreadable, rows):
    """The verdict, extracted from the sweep so synthetic inputs can drive it.

    Equality, not two subset checks. An earlier version read the roster back against
    discovery only for rows whose env door was non-None, which pinned six of the ten:
    `dossiers/key` and `triage-audit.jsonl/key` share a call site with their env twin, so
    they were checked by neither half and could have been deleted, or invented, in
    silence.
    """
    assert not unreadable, (
        f"resolve() call sites this sweep could not read, so it certified nothing about "
        f"them: {unreadable}")
    assert doors, "the sweep found no resolve() call sites at all"
    assert doors == rows, (
        f"the tilde roster and the real call-site doors disagree; only-in-source="
        f"{sorted(doors - rows)} only-in-roster={sorted(rows - doors)}")


def _roster_doors():
    return {(name, "env" if env else "key") for _, name, env, _, _, _ in _FAMILIES}


def test_every_resolve_call_site_has_a_tilde_row():
    """The roster is hand-written, so this pins it against the real source -- both ways."""
    pkg = pathlib.Path(__file__).resolve().parent.parent / "sluice"
    _check_roster(*_resolve_call_site_doors(pkg), _roster_doors())


# ── the sweep's own guards, driven over PLANTED source ───────────────────────
# Each of these was written first as an assertion over `sluice/` alone, and each survived
# its own deletion there: the tree contains none of the shapes they exist to catch, so
# there was nothing for them to fail on. Planting the shape is what turns a claim into a
# check -- CLAUDE.md's "extract the logic so synthetic inputs drive it", applied.

def _plant(tmp_path, body):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "m.py").write_text(body, encoding="utf-8")
    return pkg


def test_the_sweep_sees_the_module_attribute_import_style(tmp_path):
    """`from sluice.core import paths` + `paths.resolve(...)` -- the idiom THIS file uses,
    and one import-style edit away in `sluice/`. Keyed on the bare name alone, rewriting
    `sluice/track/config.py` to it dropped both its doors with the suite green."""
    pkg = _plant(tmp_path, "from sluice.core import paths\n"
                           "paths.resolve(env_var='X', config_value='', kind='state',\n"
                           "              name='planted.db')\n")
    doors, unreadable = _resolve_call_site_doors(pkg)
    assert doors == {("planted.db", "env")}, doors
    assert not unreadable


@pytest.mark.parametrize("label,body", [
    # Every one of these was MISSED before, with no `unreadable` entry either -- so a new
    # call site written in any of them was invisible to discovery AND absent from the
    # roster, leaving `doors == rows` true and the build green. That is the guard's stated
    # job defeated in silence, which is why each is planted rather than argued about.
    ("dotted import", "import sluice.core.paths\n"
                      "sluice.core.paths.resolve(env_var='X', config_value='',\n"
                      "                          kind='state', name='planted.db')\n"),
    ("relative import", "from .paths import resolve\n"
                        "resolve(env_var='X', config_value='', kind='state',\n"
                        "        name='planted.db')\n"),
    ("rebound local", "from sluice.core.paths import resolve\n"
                      "_r = resolve\n"
                      "_r(env_var='X', config_value='', kind='state',\n"
                      "   name='planted.db')\n"),
])
def test_the_sweep_sees_the_less_obvious_import_styles(tmp_path, label, body):
    pkg = _plant(tmp_path, body)
    doors, unreadable = _resolve_call_site_doors(pkg)
    assert doors == {("planted.db", "env")}, f"{label}: {doors}"
    assert not unreadable, f"{label}: {unreadable}"


def test_a_star_import_is_reported_rather_than_assumed_harmless(tmp_path):
    """`from sluice.core.paths import *` makes the local bindings undecidable from the
    AST. Undecidable is not "no call sites here" -- those are opposite answers, and
    guessing the second is how a sweep certifies a file it could not read."""
    pkg = _plant(tmp_path, "from sluice.core.paths import *\n"
                           "resolve(env_var='X', config_value='', kind='state',\n"
                           "        name='planted.db')\n")
    _, unreadable = _resolve_call_site_doors(pkg)
    assert unreadable, "a star import was treated as though it bound nothing"


def test_the_sweep_sees_an_aliased_from_import(tmp_path):
    """The alias half, planted rather than inferred from `core/app.py`."""
    pkg = _plant(tmp_path, "from sluice.core.paths import resolve as _rp\n"
                           "_rp(env_var=None, config_value=cfg.x, kind='state',\n"
                           "    name='planted.db')\n")
    doors, unreadable = _resolve_call_site_doors(pkg)
    assert doors == {("planted.db", "key")}, doors
    assert not unreadable


def test_a_call_site_the_sweep_cannot_read_is_reported_not_skipped(tmp_path):
    """A `name=` it cannot evaluate is a call site whose door is UNKNOWN, not a call site
    with no door -- opposite answers, and skipping silently gives the wrong one."""
    pkg = _plant(tmp_path, "from sluice.core.paths import resolve\n"
                           "NAME = 'planted.db'\n"
                           "resolve(env_var='X', config_value='', kind='state', name=NAME)\n")
    doors, unreadable = _resolve_call_site_doors(pkg)
    assert unreadable, "an unreadable call site was silently skipped"
    with pytest.raises(AssertionError, match="could not read"):
        _check_roster(doors, unreadable, _roster_doors())


def test_the_verdict_fails_on_a_roster_row_no_call_site_backs(tmp_path):
    """The direction the old one-way check could not see: a row invented, or left behind
    after its call site went away, with nothing in the source to match it."""
    pkg = _plant(tmp_path, "from sluice.core.paths import resolve\n"
                           "resolve(env_var='X', config_value='', kind='state',\n"
                           "        name='planted.db')\n")
    doors, unreadable = _resolve_call_site_doors(pkg)
    with pytest.raises(AssertionError, match="only-in-roster"):
        _check_roster(doors, unreadable, doors | {("never-resolved.db", "key")})


def test_the_verdict_fails_when_the_sweep_finds_nothing(tmp_path):
    """The vacuity case: every assertion over an empty set is satisfied, so finding
    nothing must be the failure rather than the pass."""
    pkg = _plant(tmp_path, "x = 1\n")
    doors, unreadable = _resolve_call_site_doors(pkg)
    with pytest.raises(AssertionError, match="no resolve\\(\\) call sites at all"):
        _check_roster(doors, unreadable, set())


# ── the invariant the fix protects ───────────────────────────────────────────

def test_a_tilde_seen_db_loads_the_real_dedup_history(monkeypatch, tmp_path):
    """The harm, end to end, on the store where it is irreversible.

    An empty dedup set makes every already-known lead read as unseen and re-submits it to
    the write path; `Vault.upsert`'s `_merged/` probe is name-keyed, so a re-scrape whose
    title has drifted is CREATED afresh, and if its twin was `applied` that is a second
    application under the user's name. Asserting the resolved STRING would not see this --
    the string was wrong in a way the store read as "first run", silently.
    """
    from sluice.core.leads import Lead
    from sluice.core.seendb import SeenDb

    real = _home(tmp_path) / "state" / "seen.db"
    real.parent.mkdir(parents=True)
    SeenDb(str(real)).save([
        Lead(title="t1", company="c", url="https://example.invalid/1", source="s",
             search="q"),
        Lead(title="t2", company="c", url="https://example.invalid/2", source="s",
             search="q"),
    ])

    monkeypatch.setenv("SEEN_DB", "~/state/seen.db")
    assert len(SeenDb().load()) == 2


def test_a_tilde_seen_db_writes_no_literal_tilde_directory(monkeypatch, tmp_path):
    """The other half of step 4: `save` created `./~/state/seen.db` under whatever
    directory the user happened to run from. A cwd-relative store is the class of path
    #80 exists to remove, and this one the tool created itself."""
    from sluice.core.leads import Lead
    from sluice.core.seendb import SeenDb

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("SEEN_DB", "~/state/seen.db")
    SeenDb().save([Lead(title="t", company="c", url="https://example.invalid/1",
                        source="s", search="q")])
    assert not (cwd / "~").exists(), (
        f"a literal ~ directory was created under the cwd: "
        f"{sorted(p.name for p in cwd.iterdir())}")
    assert (_home(tmp_path) / "state" / "seen.db").exists()


def test_a_tilde_store_is_readable_on_the_second_run(monkeypatch, tmp_path):
    """`existing_db_uri` is `file://` + `quote(path)`, and `quote` leaves `~` unreserved,
    so `file://~/state/...` parses `~` as the URI AUTHORITY and sqlite refuses it. Before
    the fix that made the store written on run 1 permanently unreadable -- measured:
    `sqlite3.OperationalError: invalid uri authority: ~`, naming neither the store nor a
    remedy.

    Expansion is not what closes that -- `expanduser` returns a relative path unchanged,
    and the identical failure had every non-absolute spelling. `existing_db_uri`
    absolutising at the point of use is what closes it, for every spelling at once; this
    row covers the tilde one end to end because that is the route a user reaches it by."""
    from sluice.core.leads import Lead
    from sluice.core.seendb import SeenDb

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEEN_DB", "~/state/seen.db")
    SeenDb().save([Lead(title="t", company="c", url="https://example.invalid/1",
                        source="s", search="q")])
    assert SeenDb().load() == {"https://example.invalid/1"}


# ── the fix's own side-effect ────────────────────────────────────────────────

def test_an_abandoned_literal_tilde_path_is_named(monkeypatch, tmp_path, caplog):
    """Expanding is itself a relocation for anyone who ran the broken version: their
    state sits at a literal `./~/...` and resolution now points elsewhere. This module's
    doctrine is that a store never moves silently, so say so.

    Warn rather than refuse -- and NOT because the abandoned location holds little. An
    earlier version said "at most one run's rows", which is a property of the sqlite URI
    and so true of two families out of nine; measured on `sluice_health.json`, a literal
    `./~/` file accumulated three runs. Refusing is simply not available here: `fatal` is
    read only in the legacy branch, which naming a path deliberately short-circuits, and
    re-opening that door would make every caller that supplies its own path refusable."""
    cwd = tmp_path / "cwd"
    (cwd / "~" / "state").mkdir(parents=True)
    (cwd / "~" / "state" / "seen.db").write_bytes(b"")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("SEEN_DB", "~/state/seen.db")
    with caplog.at_level("WARNING"):
        out = paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                            name="seen.db")
    assert out == str(_home(tmp_path) / "state" / "seen.db")
    # The RENDERED sentence, not two membership checks. Membership is role-blind: swapping
    # the two format arguments left the whole suite green while inverting the meaning, so
    # the notice told the user their LIVE store "also exists relative to the current
    # directory ... remove it by hand" -- advice that destroys the very state the resolver
    # had just pointed at. A message whose halves can trade places is not pinned by
    # asserting both halves are present.
    assert (f"seen.db resolves to {out}, but ~/state/seen.db also exists relative to the "
            f"current directory.") in caplog.text, (
        f"the notice named the two paths in the wrong roles: {caplog.text!r}")
    # It must say MOVE. An earlier draft ended "remove it by hand", and for a dedup
    # database that abandoned file is the ENTIRE history the broken version accumulated --
    # following that instruction starts the next run from an empty dedup set, which is the
    # #81 harm handed to the user as advice. The DIRECTION of the `mv` is asserted too: a
    # remedy naming both paths in the wrong order is worse than no remedy.
    assert "do not delete" in caplog.text, caplog.text
    assert f"mv '~/state/seen.db' {out}" in caplog.text, (
        f"the remedy must move the abandoned file TO the resolved one: {caplog.text!r}")
    assert "rm " not in caplog.text, caplog.text


def test_no_notice_when_nothing_was_left_behind(monkeypatch, tmp_path, caplog):
    """The ordinary case -- a user who simply writes a `~` path -- must stay quiet. A
    warning on every run is a warning nobody reads."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEEN_DB", "~/state/seen.db")
    with caplog.at_level("WARNING"):
        paths.resolve(env_var="SEEN_DB", config_value="", kind="state", name="seen.db")
    assert not re.search(r"WARNING", caplog.text), caplog.text


def test_no_notice_for_an_ordinary_absolute_path_that_exists(monkeypatch, tmp_path,
                                                             caplog):
    """The commonest configuration of all, and the one a half-written gate punishes.

    The notice needs BOTH terms: nothing was expanded here, so nothing was left behind.
    Dropping the `expanded != explicit` half leaves a gate that fires whenever the named
    path merely EXISTS -- which for a working install is every run, telling a user their
    own store was abandoned. Measured: with that term removed the rest of this file stays
    green, so this row is the only thing standing between the fix and that.
    """
    store = tmp_path / "state" / "seen.db"
    store.parent.mkdir(parents=True)
    store.write_bytes(b"")
    monkeypatch.setenv("SEEN_DB", str(store))
    with caplog.at_level("WARNING"):
        out = paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                            name="seen.db")
    assert out == str(store)
    assert not re.search(r"WARNING", caplog.text), caplog.text


def test_the_notice_speaks_when_it_cannot_tell(monkeypatch, tmp_path, caplog):
    """`os.path.lexists` is the fail-open primitive `paths.absent` exists to remove: it
    returns False on ANY OSError, so abandoned state under a directory the user cannot
    traverse read as "nothing there" and the notice stayed silent. Measured at 0o000: zero
    warnings.

    Unreadable and absent are the same answer to `lexists` and opposite answers to the
    user, so the tie is broken towards speaking. This is a warning, not a refusal, so a
    spurious one costs a line of output while a missed one costs the state it names.
    """
    cwd = tmp_path / "cwd"
    (cwd / "~" / "state").mkdir(parents=True)
    (cwd / "~" / "state" / "seen.db").write_bytes(b"")
    (cwd / "~").chmod(0o000)
    try:
        # The PRECONDITION, asserted rather than assumed. As root, or on a filesystem that
        # ignores mode bits, `lstat` succeeds and `_something_is_there` returns True from
        # its BOTTOM return -- the warning fires, this row passes, and the mutation it
        # exists for (`except OSError: return True` -> `return False`) survives green. A
        # guard that degrades into a tautology without saying so is the shape this whole
        # file keeps finding.
        with pytest.raises(PermissionError):
            os.lstat(str(cwd / "~" / "state" / "seen.db"))
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("SEEN_DB", "~/state/seen.db")
        with caplog.at_level("WARNING"):
            paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                          name="seen.db")
        assert "~/state/seen.db" in caplog.text, (
            "state under an untraversable parent went unmentioned")
    finally:
        (cwd / "~").chmod(0o755)


def test_a_fatal_store_still_expands_and_still_does_not_refuse(monkeypatch, tmp_path):
    """`fatal=True` is the two dedup stores' refusal, and it must be unchanged here.

    The explicit branch returns before `fatal` is ever read, which is what keeps a caller
    that names its own path immune to refusing to start. Expansion happens on that same
    branch, so the pairing needs its own row: a fix that moved the expansion below the
    short-circuit would make the loudest path in the module start refusing.
    """
    monkeypatch.setenv("SEEN_DB", "~/state/seen.db")
    out = paths.resolve(env_var="SEEN_DB", config_value="", kind="state",
                        name="seen.db", fatal=True)
    assert out == str(_home(tmp_path) / "state" / "seen.db")


# ── the normalisation convention, enumerated from the source ─────────────────

_EXPANDUSER_SITES = {
    # file -> why a path arriving there is expanded. The VALUE is the point: a new site
    # with no reason is what this pins, because the convention was four unconnected
    # decisions before it was written down, and the first attempt to write it down
    # miscounted them.
    "core/paths.py": "the explicit branch and the XDG fallback -- resolution's own ingress",
    "core/vault.py": "$VAULT_DIR or a constructor argument, at construction",
    "onboard/questions.py": "an answer typed at the wizard prompt",
    "cli.py": "--vault against $VAULT_DIR, and the preset handed to `sluice init`",
}


def test_the_expanduser_roster_matches_the_source():
    """`paths.py`'s module docstring states the normalisation convention, and prose goes
    stale silently -- this branch shipped a version naming FOUR ingress sites when there
    were five, and nothing went red.

    Pinned by FILE rather than line, so ordinary edits do not churn it while a genuinely
    new site still fails. Fails in BOTH directions: a site added with no reason recorded,
    and a reason left behind after its site went away.
    """
    pkg = pathlib.Path(__file__).resolve().parent.parent / "sluice"
    found = {}
    for py in sorted(pkg.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        n = sum(1 for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and _dotted(node.func).endswith("path.expanduser"))
        if n:
            found[str(py.relative_to(pkg))] = n
    assert found, "the expanduser sweep matched nothing -- it stopped finding call sites"
    assert set(found) == set(_EXPANDUSER_SITES), (
        f"the normalisation convention in paths.py's docstring and ARCHITECTURE.md is "
        f"stated over the wrong set of files; only-in-source="
        f"{sorted(set(found) - set(_EXPANDUSER_SITES))} only-in-roster="
        f"{sorted(set(_EXPANDUSER_SITES) - set(found))}")
