import dataclasses
import importlib
from pathlib import Path

from sluice.apply.config import ApplyConfig
from sluice.core.config import Config, SourceConfig, load_config
from sluice.cv.config import CvConfig, load_cv_config
from sluice.track.config import TrackConfig
from sluice.triage.config import TriageConfig, load_triage_config


def test_cv_defaults_carry_no_pii():
    # CvConfig ships with entirely neutral defaults: no owner name, no contact
    # info, no employer roster, no fabrication decoys, no personal filename or
    # prefix map baked into source. A blocklist of real names would defeat the
    # point of this test in a public repo (it would just relist the PII it's
    # guarding against), so this asserts structural neutrality instead:
    # personal values only ever arrive via the `cv:` block of sluice.yaml
    # (see sluice.yaml.example), never hardcoded here.
    c = CvConfig()
    assert c.name == "Your Name"
    assert c.contact == ""
    assert c.employers == []
    assert c.fabrication_decoys == []
    assert c.negatives == []
    assert c.prefix_map == {}
    assert c.neutral_filename == "CV.pdf"


def test_triage_defaults_carry_no_pii():
    # TriageConfig ships with NO geo or company preference. target_locations was
    # once ["remote"], which is not neutral: classify rejects anything that does not
    # match it, so a fresh install silently binned every job with a location on it.
    t = TriageConfig()
    assert t.reject_companies == []
    assert t.target_locations == []
    assert t.reject_locations == []
    # Title and pay preferences are equally personal. These were guarded only in
    # test_triage_config.py, so this file -- the one the docs and the review agents
    # point at as THE neutrality guard -- did not actually cover them.
    assert t.accept_titles == []
    assert t.reject_titles == []
    assert t.contract_floor_gbp_day == 0
    assert t.perm_floor_gbp == 0


def test_ingest_defaults_carry_no_preference(monkeypatch):
    # The root Config gates ingest, and its defaults were NOT guarded here at all:
    # `locations` shipped as ["Remote"] (the same geo-preference-in-source shape as
    # the 672ad2a bug), and relevance_keep/relevance_drop had no assertion anywhere
    # in the suite -- a regression to relevance_keep = ["engineer"] would have shipped
    # green. An unset gate must express no opinion.
    c = Config()
    assert c.locations == []
    assert c.relevance_keep == []
    assert c.relevance_drop == []
    assert c.location_noise_words == []   # #5 gate abstains: no noise subtracted by default
    assert c.dedupe_title_noise_words == []   # #23: strictest clustering, abstain toward not-merging
    # baseline_rel moved here from CvConfig (only the store can honour it, and
    # Sluice.store() only ever sees the root Config). The assertion had to move WITH it:
    # the refactor deleted it from the CvConfig test and nothing replaced it, so a
    # regression to an absolute personal path would have shipped green. Caught by review.
    assert c.baseline_rel == "My CV/CV.md"
    assert not c.baseline_rel.startswith("/"), \
        "baseline_rel must be RELATIVE to the store: an absolute path is someone's machine"
    # The adapter selectors name shipped implementations, never a person's setup.
    assert c.store == "vault"
    assert c.fetcher == "camofox"

    # ...and the same must hold through the real loader with no config file, which is
    # what a fresh install actually gets. Both env overrides are cleared: without this
    # the assertion would silently read the developer's own SLUICE_CONFIG and pass for
    # the wrong reason.
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    monkeypatch.delenv("SLUICE_LOCATIONS", raising=False)
    loaded = load_config(None)
    assert loaded.locations == []
    assert loaded.relevance_keep == []
    assert loaded.relevance_drop == []
    assert loaded.location_noise_words == []


def test_config_overlay_restores_neutralized_defaults(tmp_path, monkeypatch):
    """Neutralizing the code defaults must not cost override capability: a
    sluice.yaml with triage: and cv: blocks should still fully round-trip
    through load_triage_config()/load_cv_config(), proving the owner (or
    anyone else) can restore their own real values via a git-ignored local
    config file."""
    p = tmp_path / "sluice.local.yaml"
    p.write_text(
        "triage:\n"
        "  reject_companies: [acme]\n"
        "  target_locations: [jenningsfort, baldwinberg]\n"
        "  reject_locations: [india]\n"
        "cv:\n"
        "  name: \"Someone\"\n"
        "  negatives: [\"X\"]\n"
        "  prefix_map: {Foo: FO}\n"
    )
    monkeypatch.setenv("SLUICE_CONFIG", str(p))

    tcfg = load_triage_config()
    assert tcfg.reject_companies == ["acme"]
    assert tcfg.target_locations == ["jenningsfort", "baldwinberg"]
    assert tcfg.reject_locations == ["india"]

    ccfg = load_cv_config()
    assert ccfg.name == "Someone"
    assert ccfg.negatives == ["X"]
    assert ccfg.prefix_map == {"Foo": "FO"}


# --- #26: the unguarded-preference SWEEP -------------------------------------
# The four tests above are an ENUMERATION: they assert named fields, so they ship
# green on any preference key nobody named. That has escaped TWICE (see the
# comments at `locations` and `baseline_rel` above). The sweep below closes the
# class: EVERY list-defaulting field on EVERY config dataclass must default empty,
# so the next list-typed preference cannot ship a stranger's taste baked into
# source. It is strictly ADDITIVE -- it removes none of the assertions above. A
# loop-only rewrite that dropped the str-typed checks (baseline_rel not absolute,
# store/fetcher) or the loader half would be the very `:51-54` escape #26 cites,
# recurring inside its own fix, so those stay hand-written and untouched.

# Explicit, reviewable roster of every config dataclass a fresh install builds.
# test_swept_configs_covers_every_config_dataclass pins that this list is COMPLETE:
# an enumeration of DATACLASSES ships green on a config nobody named -- #26's own
# critique, one level up -- so a new sub-app's *Config must be added here or CI
# reddens. It is deliberately broader than the four sub-app load targets: ApplyConfig
# (all-str today, like TrackConfig) and the nested SourceConfig (whose `searches`
# override is an abstain-preference) are configs too, and a list preference landing
# on either must not ship green for want of a name in this list.
_SWEPT_CONFIGS = [Config, SourceConfig, TriageConfig, CvConfig, TrackConfig, ApplyConfig]


def _list_defaulting_fields(cls):
    """(name, default) for each field of `cls` whose zero-arg default is a list.
    Keyed on the default VALUE via isinstance, read off an instance so a
    default_factory is resolved -- NOT on the annotation. `f.type is list` is
    annotation-keyed, and `list[str]` is a types.GenericAlias (not `list`), so an
    annotation-keyed sweep silently misses the first `list[str]` field written;
    today every field is bare `list`, so such a sweep would LOOK live while being
    inert. dict-typed defaults (TrackConfig.ats_relay_domains, legitimately
    non-empty) are excluded: the sweep is list-only by construction."""
    obj = cls()
    for f in dataclasses.fields(cls):
        value = getattr(obj, f.name)
        if isinstance(value, list):
            yield f.name, value


def test_every_list_defaulting_config_field_defaults_empty():
    # An unconfigured preference gate must ABSTAIN (pass every lead through), not
    # match-nothing -- getting this backwards silently binned an entire job hunt
    # once (672ad2a). So every list-defaulting field, across every config, is empty.
    offenders = {
        f"{cls.__name__}.{name}": value
        for cls in _SWEPT_CONFIGS
        for name, value in _list_defaulting_fields(cls)
        if value != []
    }
    assert offenders == {}, (
        "every list-defaulting config field must default empty (an unconfigured "
        "preference gate abstains); these ship a non-empty default -- a stranger's "
        f"taste baked into source: {offenders}"
    )


def _discover_config_dataclasses():
    """Every module-level @dataclass named *Config in a sluice */config.py file.
    Globs the source tree (deterministic, and NOT pkgutil.walk_packages, which
    would import ingest source modules that drive Camofox) and imports only
    config.py modules -- each imports just os + a guarded yaml, so this stays
    offline. The __module__ guard counts only dataclasses DEFINED in the module,
    never one imported into it."""
    pkg = Path(__file__).resolve().parent.parent / "sluice"
    found = {}
    for path in sorted(pkg.rglob("config.py")):
        dotted = ".".join(path.relative_to(pkg.parent).with_suffix("").parts)
        module = importlib.import_module(dotted)
        for name, obj in vars(module).items():
            if (name.endswith("Config") and dataclasses.is_dataclass(obj)
                    and getattr(obj, "__module__", None) == module.__name__):
                found[name] = obj
    return found


def test_swept_configs_covers_every_config_dataclass():
    # The sweep is only as complete as _SWEPT_CONFIGS, so pin that the roster is
    # EVERY *Config dataclass in a sluice */config.py module (discovery's enforced
    # scope -- a *Config defined outside a config.py escapes both, but config.py is
    # the convention every sub-app follows). This closes two ways the sweep could
    # silently narrow: adding a sub-app config without sweeping it reddens here,
    # and narrowing the roster to [Config] -- the root-only trap, where
    # dataclasses.fields(Config) never reaches TriageConfig.target_locations (the
    # actual 672ad2a site) -- reddens here too.
    discovered = set(_discover_config_dataclasses().values())
    assert discovered == set(_SWEPT_CONFIGS), (
        "_SWEPT_CONFIGS must be every *Config dataclass in a sluice */config.py "
        f"module; drift between discovered={sorted(c.__name__ for c in discovered)} "
        f"and swept={sorted(c.__name__ for c in _SWEPT_CONFIGS)}"
    )


def test_sweep_keys_on_the_default_value_not_the_annotation():
    # Trap 2, pinned permanently: the sweep must catch a `list[str]`-annotated field,
    # not just bare `list`. `list[str]` is a types.GenericAlias, so a sweep keyed on
    # `f.type is list` would miss it. Build a throwaway dataclass carrying BOTH
    # annotation shapes with non-empty defaults; the value-keyed helper must surface
    # both. If the helper ever regressed to annotation-keying, `parametrized` would
    # drop out and this assertion would fail.
    @dataclasses.dataclass
    class _Sample:
        bare: list = dataclasses.field(default_factory=lambda: ["x"])
        parametrized: list[str] = dataclasses.field(default_factory=lambda: ["y"])
        scalar: str = "not-a-list"

    assert dict(_list_defaulting_fields(_Sample)) == {"bare": ["x"], "parametrized": ["y"]}
