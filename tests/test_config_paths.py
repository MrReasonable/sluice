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
import fnmatch
import importlib
import os
import pathlib

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


# ── the config FILE itself (table row #1) ────────────────────────────────────
# Every loader that reads $SLUICE_CONFIG, each with a key only it reads, so a row
# passing proves THAT loader found the file rather than some other one having done so.
# The roster is PINNED, and test_config_loader_roster_is_complete asserts it is also
# COMPLETE: converting four loaders and missing the fifth gives a config file that
# half-loads with no error anywhere, which is the failure this pair exists to prevent.
_CONFIG_LOADERS = [
    ("load_config", "sluice.core.config", "lead_ttl_days: 13\n", "lead_ttl_days", 13),
    ("load_triage_config", "sluice.triage.config", "triage:\n  batch_size: 11\n",
     "batch_size", 11),
    ("load_cv_config", "sluice.cv.config", "cv:\n  ttl_days: 12\n", "ttl_days", 12),
    ("load_apply_config", "sluice.apply.config", "apply:\n  neutral_name: Example.pdf\n",
     "neutral_name", "Example.pdf"),
    ("load_track_config", "sluice.track.config", "track:\n  gmail_lookback_days: 9\n",
     "gmail_lookback_days", 9),
]
_LOADER_IDS = [r[0] for r in _CONFIG_LOADERS]


def _loader(module, name):
    return getattr(importlib.import_module(module), name)


def _plant_config(root):
    """Write one config file carrying every loader's block, at the XDG location."""
    p = pathlib.Path(root) / "sluice" / "config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(r[2] for r in _CONFIG_LOADERS), encoding="utf-8")
    return p


@pytest.mark.parametrize("name,module,block,fieldname,value", _CONFIG_LOADERS,
                         ids=_LOADER_IDS)
def test_loader_reads_the_config_file_under_xdg(name, module, block, fieldname, value):
    # The sweep's ONE behaviour change: an unset SLUICE_CONFIG used to mean "no config
    # file", and now means "read $XDG_CONFIG_HOME/sluice/config.yaml if it exists".
    _plant_config(os.environ["XDG_CONFIG_HOME"])
    assert getattr(_loader(module, name)(), fieldname) == value


@pytest.mark.parametrize("name,module,block,fieldname,value", _CONFIG_LOADERS,
                         ids=_LOADER_IDS)
def test_sluice_config_env_still_beats_the_xdg_file(name, module, block, fieldname,
                                                    value, tmp_path, monkeypatch):
    # env > config > XDG holds for the config file too. BOTH files exist and carry the
    # SAME key with DIFFERENT values, so this cannot pass by one of them being absent --
    # and it asserts the winner's value rather than merely "not the loser's".
    _plant_config(os.environ["XDG_CONFIG_HOME"])
    other = tmp_path / "explicit.yaml"
    text, expected = _override(block, fieldname, value)
    other.write_text(text, encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(other))
    assert getattr(_loader(module, name)(), fieldname) == expected


@pytest.mark.parametrize("name,module,block,fieldname,value", _CONFIG_LOADERS,
                         ids=_LOADER_IDS)
def test_explicit_path_argument_still_beats_both(name, module, block, fieldname, value,
                                                 tmp_path):
    # The `path=` argument is how tests and doctor name a file directly; the XDG
    # fallback must not reach past it. SLUICE_CONFIG is unset by the autouse fixture,
    # so the loser here is specifically the XDG file.
    _plant_config(os.environ["XDG_CONFIG_HOME"])
    other = tmp_path / "explicit.yaml"
    text, expected = _override(block, fieldname, value)
    other.write_text(text, encoding="utf-8")
    assert getattr(_loader(module, name)(str(other)), fieldname) == expected


def _override(block, fieldname, value):
    """(yaml text, expected) for the same block carrying a DIFFERENT value."""
    other = value + 1 if isinstance(value, int) else "Other.pdf"
    return block.replace(f"{fieldname}: {value}", f"{fieldname}: {other}"), other


def test_load_star_config_glob_matches_the_root_loader_too():
    # Pinned because it is counter-intuitive and has already been got wrong: the
    # obvious glob `load_*_config` does NOT match `load_config`. A discovery that used
    # it would silently skip the ROOT loader while still reddening on a sub-app one --
    # reading as proof of exactly the completeness it lacks.
    assert not fnmatch.fnmatch("load_config", "load_*_config")
    assert all(fnmatch.fnmatch(n, "load*config") for n in _LOADER_IDS)


def test_config_loader_roster_is_complete():
    # The roster above is an ENUMERATION, so it ships green on a loader nobody named.
    # Discovery closes that: every `load*config` function DEFINED in a sluice */config.py
    # module must be listed. Globs the source tree and imports only config.py modules
    # (each imports os, a guarded yaml and core.paths), so this stays offline -- the same
    # discipline as _discover_config_dataclasses in test_sluice_neutral_defaults.py.
    pkg = pathlib.Path(__file__).resolve().parent.parent / "sluice"
    discovered = {}
    for path in sorted(pkg.rglob("config.py")):
        dotted = ".".join(path.relative_to(pkg.parent).with_suffix("").parts)
        module = importlib.import_module(dotted)
        for name, obj in vars(module).items():
            if (fnmatch.fnmatch(name, "load*config") and callable(obj)
                    and getattr(obj, "__module__", None) == module.__name__):
                discovered[name] = module.__name__
    assert discovered, "discovery found no config loaders at all"
    assert discovered == {n: m for n, m, *_ in _CONFIG_LOADERS}


# ── the call sites that resolve their own path (table rows #4, #5, #6) ───────

def test_health_store_defaults_under_the_state_root(monkeypatch):
    # The autouse fixture clears SLUICE_CONFIG/TRIAGE_AUDIT but not this one, and a
    # developer with SLUICE_HEALTH exported would otherwise see this row pass against
    # their own file.
    monkeypatch.delenv("SLUICE_HEALTH", raising=False)
    from sluice.core.health import HealthStore
    assert HealthStore().path == os.path.join(
        os.environ["XDG_STATE_HOME"], "sluice", "sluice_health.json")


def test_health_store_still_honours_its_env_var(monkeypatch, tmp_path):
    from sluice.core.health import HealthStore
    monkeypatch.setenv("SLUICE_HEALTH", str(tmp_path / "h.json"))
    assert HealthStore().path == str(tmp_path / "h.json")


def test_an_explicit_health_path_beats_the_env_var(monkeypatch, tmp_path):
    # Precedence belongs in the FACTORY, never ahead of an explicit constructor
    # argument. Putting the env read first here would retarget every
    # `HealthStore(str(tmp_path / ...))` in the suite at a developer's real file, green
    # in CI throughout -- the mistake this design made once for the vault and caught in
    # review. `path or resolve(...)` also means resolve is not even CALLED when a
    # caller names a path, so an explicit caller cannot trip the migration warning.
    from sluice.core.health import HealthStore
    monkeypatch.setenv("SLUICE_HEALTH", str(tmp_path / "from-env.json"))
    assert HealthStore(str(tmp_path / "explicit.json")).path == str(
        tmp_path / "explicit.json")


def test_disabled_overlay_defaults_under_the_state_root(monkeypatch):
    from sluice.cli import _disabled_path
    monkeypatch.delenv("SLUICE_DISABLED", raising=False)
    assert _disabled_path() == os.path.join(
        os.environ["XDG_STATE_HOME"], "sluice", "sluice_disabled.json")


def test_disabled_overlay_still_honours_its_env_var(monkeypatch, tmp_path):
    from sluice.cli import _disabled_path
    monkeypatch.setenv("SLUICE_DISABLED", str(tmp_path / "d.json"))
    assert _disabled_path() == str(tmp_path / "d.json")


def test_triage_writes_its_audit_where_the_config_says(tmp_path, monkeypatch):
    """`triage.audit_jsonl` was a DEAD KEY: declared on TriageConfig and read by
    nothing, because `Sluice.triage` read $TRIAGE_AUDIT directly with its own literal
    default. A user setting it in YAML changed nothing, silently -- the exact defect
    class this sweep exists to remove. One resolution site, in the loader, is what
    makes it live; this row drives the real command to prove the loader's value is
    what actually reaches the AuditLog.
    """
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    import sluice.triage.audit as audit_mod

    mine = tmp_path / "mine" / "audit.jsonl"
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text(f'triage:\n  audit_jsonl: "{mine}"\n', encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "dossiers"))

    seen = []

    class _Audit(audit_mod.AuditLog):
        def __init__(self, path):
            seen.append(path)
            super().__init__(path)

    monkeypatch.setattr(audit_mod, "AuditLog", _Audit)
    Sluice(Config()).triage(no_llm=True)
    assert seen == [str(mine)]


# ── one dossier cache, one root key (table row #7) ───────────────────────────

class _NullCache:
    """Faithful enough for a --no-llm triage and a dry-run compose: neither reaches a
    cache miss, but both hold the object."""
    def get_or_build(self, lead): return {"jd": {"markdown": ""}}


def _dossier_dirs_used(app, monkeypatch):
    """The directory each sub-app hands to dossier_cache, in call order."""
    seen = []

    def _capture(dossier_dir, ttl_days):
        seen.append(dossier_dir)
        return _NullCache()

    monkeypatch.setattr(app, "dossier_cache", _capture)
    app.triage(no_llm=True)
    app.compose_cv(all_shortlist=True, dry_run=True)
    return seen


def _app(tmp_path, monkeypatch, **kw):
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("DOSSIER_DIR", raising=False)
    return Sluice(Config(**kw))


def test_triage_and_cv_share_one_dossier_directory(tmp_path, monkeypatch):
    # NOT red-first, and labelled rather than presented as a witness: two keys both
    # defaulting to "./dossiers" made this true by coincidence of the literal already.
    # What changes is WHY it is true -- one root key and one resolution, so it cannot
    # come apart. A partial sweep here is worse than none: split the cache and cv
    # re-fetches every dossier over the live SSRF-guarded network path.
    used = _dossier_dirs_used(_app(tmp_path, monkeypatch), monkeypatch)
    assert len(used) == 2 and used[0] == used[1]


def test_unconfigured_dossier_dir_lands_under_the_cache_root(tmp_path, monkeypatch):
    # CACHE, not state: a dossier is a re-fetchable copy of a job ad, so losing it
    # costs a refetch, not data.
    used = _dossier_dirs_used(_app(tmp_path, monkeypatch), monkeypatch)
    assert used == [os.path.join(os.environ["XDG_CACHE_HOME"], "sluice", "dossiers")] * 2


def test_the_root_dossier_dir_key_reaches_both_sub_apps(tmp_path, monkeypatch):
    mine = str(tmp_path / "mine-dossiers")
    used = _dossier_dirs_used(_app(tmp_path, monkeypatch, dossier_dir=mine), monkeypatch)
    assert used == [mine, mine]


def test_dossier_dir_env_var_beats_the_root_key(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dossier_dir=str(tmp_path / "from-config"))
    monkeypatch.setenv("DOSSIER_DIR", str(tmp_path / "from-env"))
    used = _dossier_dirs_used(app, monkeypatch)
    assert used == [str(tmp_path / "from-env")] * 2


@pytest.mark.parametrize("block,loader_name,module", [
    ("cv", "load_cv_config", "sluice.cv.config"),
    ("triage", "load_triage_config", "sluice.triage.config"),
])
def test_retired_sub_app_dossier_dir_raises(block, loader_name, module, tmp_path):
    # The cv.baseline_rel precedent: these loaders filter unknown keys with `hasattr`,
    # so a retired key would otherwise be dropped in SILENCE -- and a user who had
    # pointed cv at its own dossier dir would get a different one with no signal.
    secret = tmp_path / "somewhere-personal"
    p = tmp_path / "c.yaml"
    p.write_text(f'{block}:\n  dossier_dir: "{secret}"\n', encoding="utf-8")
    with pytest.raises(ValueError) as e:
        _loader(module, loader_name)(str(p))
    assert f"{block}.dossier_dir" in str(e.value) and "dossier_dir:" in str(e.value)
    # Unlike baseline_rel (a store-RELATIVE name), this is a host path usually under a
    # home directory, so the message must not echo it -- core/config.py already rules
    # that way for dossier_allow_hosts. An exception travels further than a config file.
    assert str(secret) not in str(e.value)


# ── vault_dir: precedence in the FACTORY (table row #9) ──────────────────────
# The vault is the one path that deliberately does NOT relocate: it is the user's
# Obsidian directory, their data, not sluice's state. What it gains is a config key --
# before this it was settable only by an env var that does not survive a new shell, so
# #8's wizard would have had nowhere to persist what it prompts for.

def _store_dir(monkeypatch, **kw):
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    monkeypatch.delenv("VAULT_DIR", raising=False)
    return Sluice(Config(**kw)).store().dir


def test_the_vault_dir_config_key_reaches_the_store(monkeypatch, tmp_path):
    assert _store_dir(monkeypatch, vault_dir=str(tmp_path / "mine")) == str(
        tmp_path / "mine")


def test_vault_dir_env_var_beats_the_config_key(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "from-env"))
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    app = Sluice(Config(vault_dir=str(tmp_path / "from-config")))
    assert app.store().dir == str(tmp_path / "from-env")


def test_an_unset_vault_dir_keeps_the_store_s_own_default(monkeypatch):
    # Deliberately NOT an XDG location, so this row cannot be folded into the XDG
    # rows above: doing that would be satisfiable only by relocating the vault.
    assert _store_dir(monkeypatch) == "./vault"


def test_an_explicit_vault_argument_still_beats_the_env_var(monkeypatch, tmp_path):
    """The reason precedence lives in `_make` and not in `Vault.__init__`.

    Putting the env read ahead of the `dir` parameter would make VAULT_DIR beat an
    EXPLICIT constructor argument -- retargeting the ~150 positional
    `Vault(str(tmp_path))` constructions in this suite at a developer's real vault,
    green in CI throughout. The constructor keeps `dir or env or default`; the factory
    decides what to inject.
    """
    from sluice.core.vault import Vault
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "from-env"))
    assert Vault(str(tmp_path / "explicit")).dir == str(tmp_path / "explicit")


def test_no_production_code_builds_a_sub_app_config_directly():
    """The blank path defaults are only safe because the LOADER is the only way in.

    `TrackConfig()` built by hand holds `seen_db == ""`, and `deadletter_path("")` is
    `.deadletter.db` in the cwd -- a DIFFERENT #49 store from the one `track run` opened,
    so a confirm against it would report success while the real entry re-surfaces
    forever. Nothing in production does that today, and this pins that it stays true
    rather than leaving it to whoever adds the next caller. Enumerated from the source,
    not asserted about the callers I happen to remember.

    The root `Config` is deliberately NOT in scope: `Sluice(Config())` is a supported
    construction, which is exactly why `vault_dir`/`dossier_dir` resolve in the
    composition root instead of in `load_config`.
    """
    import ast

    watched = {"TrackConfig", "TriageConfig", "CvConfig", "ApplyConfig"}
    pkg = pathlib.Path(__file__).resolve().parent.parent / "sluice"
    offenders, seen_any = [], False
    for path in sorted(pkg.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Local binding -> the config class it actually names, read off this file's own
        # imports. `from sluice.track.config import TrackConfig as _TC` would otherwise
        # walk straight past a hard-coded name list -- witnessed green before this.
        bound = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name in watched:
                        bound[a.asname or a.name] = a.name
            # ...and a class DEFINED here binds its own name: each `config.py` defines
            # its config rather than importing it, so import-resolution alone saw
            # nothing at all -- which the non-vacuity assertion below caught on its
            # first run.
            elif isinstance(node, ast.ClassDef) and node.name in watched:
                bound[node.name] = node.name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # BOTH shapes. `id` alone missed `from sluice.track import config as m;
            # m.TrackConfig()` -- the sibling sweep in test_paths.py caught that same
            # shape via `attr`, so this one was fixed asymmetrically against it. The
            # `bound` lookup resolves a renamed direct import (`TrackConfig as _TC`);
            # the `in watched` fallback resolves attribute access through any module
            # alias, where the class name is the attribute rather than a local binding.
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            real = bound.get(called) or (called if called in watched else None)
            if real is None:
                continue
            seen_any = True
            # its own loader is the one legitimate construction
            if path.name == "config.py":
                continue
            offenders.append(f"{path.relative_to(pkg.parent)}:{node.lineno} {real}()")
    assert seen_any, ("found no sub-app config construction anywhere -- the sweep is "
                      "vacuous, which is how it would pass if the walk broke")
    assert not offenders, (
        "a sub-app config must come from its loader, which is what fills in the blank "
        f"path defaults; these build one directly: {offenders}")


@pytest.mark.parametrize("label,source,expected", [
    ("plain", "from sluice.track.config import TrackConfig\nTrackConfig()\n", True),
    ("renamed import", "from sluice.track.config import TrackConfig as _TC\n_TC()\n", True),
    ("module attribute", "from sluice.track import config as m\nm.TrackConfig()\n", True),
    ("dotted module", "import sluice.track.config\nsluice.track.config.TrackConfig()\n", True),
    ("unrelated call", "from sluice.core.config import Config\nConfig()\n", False),
], ids=lambda v: v if isinstance(v, str) and " " in v or isinstance(v, str) else str(v))
def test_the_config_construction_matcher_sees_every_call_shape(label, source, expected):
    """A self-test of the walk in the sweep above, over synthetic sources.

    The sweep runs over `sluice/`, where today every watched construction happens to be
    a plain name inside its own module. That made two of its branches unexercised: the
    renamed-import branch was measurably INERT (deleting it reddened nothing), and the
    module-attribute shape was missed outright while the sibling sweep in test_paths.py
    caught it. A guard whose branches no production code reaches is a guard nobody has
    tested, so the shapes are exercised here directly rather than waiting for one to
    appear in `sluice/` -- by which time the miss would already have shipped.
    """
    import ast

    watched = {"TrackConfig", "TriageConfig", "CvConfig", "ApplyConfig"}
    tree = ast.parse(source)
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name in watched:
                    bound[a.asname or a.name] = a.name
        elif isinstance(node, ast.ClassDef) and node.name in watched:
            bound[node.name] = node.name
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        real = bound.get(called) or (called if called in watched else None)
        if real is not None:
            hits.append(real)
    assert bool(hits) is expected, f"{label}: matcher saw {hits!r}"
