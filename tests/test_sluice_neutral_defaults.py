import dataclasses
import importlib
import re
from pathlib import Path

import pytest

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
    # relevance_keep/relevance_drop had no assertion anywhere in the suite, so a
    # regression to relevance_keep = ["engineer"] would have shipped green. An unset
    # gate must express no opinion.
    #
    # The root `locations` key was guarded here too, for shipping as ["Remote"] -- the
    # same geo-preference-in-source shape as the 672ad2a bug. It is now RETIRED (#8):
    # nothing ever read it, so setting it raises rather than defaulting to anything.
    # Geography is guarded on the live key instead, `triage.target_locations`, asserted
    # in test_triage_defaults_carry_no_pii above; the refusal itself is pinned in
    # tests/test_config_retired_locations.py. A retired key cannot carry a preference,
    # which is strictly stronger than an empty default.
    c = Config()
    assert c.relevance_keep == []
    assert c.relevance_drop == []
    assert c.location_noise_words == []   # #5 gate abstains: no noise subtracted by default
    assert c.dedupe_title_noise_words == []   # #23: strictest clustering, abstain toward not-merging
    # #18: covered by the value-keyed sweep below as a list-defaulting field, and it
    # must default empty -- but its "empty" is INVERTED relative to every other entry
    # here. For accept_titles, empty means "pass everything through"; for this SAFETY
    # allowlist it means "grant no exceptions", and public urls stay fetchable because
    # of the address rule, not this list. Do not read the sweep as licence to loosen
    # the guard.
    assert c.dossier_allow_hosts == []
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
    # what a fresh install actually gets. SLUICE_CONFIG is cleared because otherwise the
    # assertion would silently read the developer's own config and pass for the wrong
    # reason. SLUICE_LOCATIONS is cleared for a different reason since #8 retired that
    # key: an exported value now RAISES, so a developer with one set would see a red
    # test here rather than a silent override.
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    monkeypatch.delenv("SLUICE_LOCATIONS", raising=False)
    loaded = load_config(None)
    assert loaded.relevance_keep == []
    assert loaded.relevance_drop == []
    assert loaded.location_noise_words == []
    assert loaded.dedupe_title_noise_words == []   # #23: the loader default, not just Config()


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


def test_dedupe_title_noise_words_round_trips_through_load_config(tmp_path):
    # #23 nitpick: the abstain-by-default assertions above cover Config() and the
    # no-file loader path; this closes the other direction -- a user who DOES set
    # dedupe_title_noise_words in sluice.yaml must get it back verbatim, so the
    # neutral default costs nothing in override capability (same shape as the
    # triage/cv overlay test above).
    p = tmp_path / "sluice.local.yaml"
    p.write_text("dedupe_title_noise_words: [\"remote\", \"hybrid\"]\n")
    loaded = load_config(str(p))
    assert loaded.dedupe_title_noise_words == ["remote", "hybrid"]


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


_EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "sluice.yaml.example"


# ── #1: the lead layout ──────────────────────────────────────────────────────
# `lead_layout` needs its OWN guards for the same reason `lead_ttl_days` does, one type along.
# The #26/#63 sweep below is value-keyed on LIST-defaulting fields
# (`isinstance(getattr(cls(), f.name), list)`), so a `str` field is invisible to it, and
# `test_path_keys_dataclass_defaults_are_blank` derives only fields ending `_dir`. Measured:
# setting the default to "active_archive" leaves the #26/#63 SWEEP -- and every other row in this
# file -- green; the only thing that reddens is the guard immediately below. (An earlier draft of
# this comment said "leaves this entire file green", which its own new guards falsify. The
# blindness being recorded is the SWEEP's, not the file's.)
#
# It belongs HERE and not in the feature's own test file because how a user organises their job
# hunt is a PREFERENCE, and this is the file the docs and the review agents sweep for shipped
# preferences. This file's own comments record what it cost when a preference was guarded
# somewhere reviewers do not look.

def test_lead_layout_dataclass_default_is_flat():
    assert Config().lead_layout == ""


def test_lead_layout_loader_default_is_flat(monkeypatch):
    # load_config names every field explicitly (no splat, no loop), so the loader default is an
    # INDEPENDENT literal that the dataclass assertion above does not constrain.
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert load_config(None).lead_layout == ""


def test_the_example_config_ships_lead_layout_commented():
    """`sluice.yaml.example` is a CATALOGUE, and this file is COPIED. An ACTIVE
    `lead_layout: active_archive` would hand every copier a filing decision they never made and
    silently start relocating their notes -- the `lead_ttl_days`/`locations` precedent, stated in
    the example file itself. Asserted through yaml.safe_load, which is blind to a comment: an
    active key would appear in the parsed document."""
    import yaml
    # Through _EXAMPLE_PATH, never a cwd-relative Path(...): a neutrality guard must not be
    # contingent on where you stand. Measured -- run from tests/, the bare form fails only here.
    text = _EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "lead_layout:" in text, "lead_layout must be documented at all"
    assert "lead_layout" not in (yaml.safe_load(text) or {}), \
        "lead_layout must ship COMMENTED, not active"


# ── #9: lead staleness ───────────────────────────────────────────────────────
# lead_ttl_days needs its OWN guard. The #26/#63 sweep below is value-keyed on
# LIST-defaulting fields, because "empty list == abstain" is universal. `0 == abstain`
# is NOT universal for ints -- the dossier-cache `ttl_days: int = 7` in cv/config.py and
# triage/config.py is a legitimate non-zero default where 0 would mean "never cache" --
# so widening that sweep to every int field would false-positive on it. Verified twice
# during review: adding `lead_ttl_days: int = 90` to Config left the full suite green.

def test_lead_ttl_days_dataclass_default_is_off():
    assert Config().lead_ttl_days == 0


def test_lead_ttl_days_loader_default_is_off(monkeypatch):
    # load_config names every field explicitly (no splat, no loop), so the loader default
    # is an INDEPENDENT literal that the dataclass assertion above does not constrain.
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert load_config(None).lead_ttl_days == 0


def test_lead_ttl_days_absent_key_abstains_rather_than_raising(tmp_path, monkeypatch):
    # ABSENT is the abstain case, not an error: an unconfigured install must load.
    p = tmp_path / "c.yaml"
    p.write_text("store: vault\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    assert load_config(None).lead_ttl_days == 0


def test_lead_ttl_days_configured_value_round_trips(tmp_path, monkeypatch):
    # Every other test here pins the OFF state, which a permanently-zero knob would also
    # satisfy. This one pins that a CONFIGURED value survives the loader.
    p = tmp_path / "c.yaml"
    p.write_text("lead_ttl_days: 90\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    assert load_config(None).lead_ttl_days == 90


@pytest.mark.parametrize("value", ["yes", "on", "true", "True"])
def test_lead_ttl_days_rejects_yaml_booleans(tmp_path, monkeypatch, value):
    # bool subclasses int, and PyYAML resolves yes/on/true to True. A plain isinstance
    # check therefore admits `lead_ttl_days: yes` -- the natural thing to type to turn
    # this feature ON -- as a valid int, setting a ONE-DAY ttl: every lead stale, cv and
    # apply refusing everything, expire proposing the whole vault, with no error at all.
    # An abstain inversion reached by typing the obvious thing: the 672ad2a class.
    p = tmp_path / "c.yaml"
    p.write_text(f"lead_ttl_days: {value}\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    with pytest.raises(ValueError, match="lead_ttl_days"):
        load_config(None)


@pytest.mark.parametrize("value", ["-1", "'90'", "1.5", "[90]"])
def test_lead_ttl_days_rejects_negative_and_non_int(tmp_path, monkeypatch, value):
    p = tmp_path / "c.yaml"
    p.write_text(f"lead_ttl_days: {value}\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    with pytest.raises(ValueError, match="lead_ttl_days"):
        load_config(None)


# ── #80: the two root path keys ──────────────────────────────────────────────
# vault_dir and dossier_dir are str-typed, so the value-keyed sweep above misses them
# BY DESIGN (it is list-only, and deliberately so -- see the #9 note). They carry two
# distinct guarantees at once, either of which failing is silent:
#   * NEUTRALITY -- a non-empty shipped default is a directory on whoever wrote it,
#     the `baseline_rel` shape asserted 200 lines up;
#   * MECHANISM -- paths.resolve is `env or config or XDG`, so a config term that is
#     ALWAYS truthy short-circuits before XDG is ever reached, and the whole
#     per-system sweep goes inert with every test still green.

def test_path_keys_dataclass_defaults_are_blank():
    # DERIVED from the dataclass, not hand-listed. Mutation-witnessed as inadequate:
    # adding a fourth `*_dir` field with a non-empty default left the hand-written pair
    # green, which is the same enumeration failure the sweep above exists to close.
    c = Config()
    keys = [f.name for f in dataclasses.fields(Config) if f.name.endswith("_dir")]
    assert keys, "no root *_dir field found -- this guard would be vacuous"
    offenders = {k: getattr(c, k) for k in keys if getattr(c, k) != ""}
    assert offenders == {}, (
        "a root path key must default blank -- a non-empty default is always truthy, so "
        "it short-circuits `env or config or XDG` and the per-system location is never "
        f"reached, with nothing going red: {offenders}")


def test_path_keys_loader_defaults_are_blank(monkeypatch):
    # load_config names every field explicitly, so the loader default is an
    # INDEPENDENT literal that the dataclass assertion above does not constrain --
    # the same split the lead_ttl_days pair above exists for.
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    loaded = load_config(None)
    assert loaded.vault_dir == ""
    assert loaded.dossier_dir == ""


def test_example_config_ships_lead_ttl_days_off():
    # sluice.yaml.example is COPIED VERBATIM by the documented quickstart, and this same
    # file ships ACTIVE illustrative non-zero pay floors two blocks away -- so the
    # nearest local convention is the unsafe one. A copied non-zero silently switches on
    # the cv and apply refusals, neither of which is human-gated the way --expire is.
    # test_config_example.py guards only the sub-app blocks, so a root key is otherwise
    # unguarded entirely.
    text = _EXAMPLE_PATH.read_text(encoding="utf-8")
    active = [ln for ln in text.splitlines()
              if ln.strip().startswith("lead_ttl_days:")]
    assert all(ln.split(":", 1)[1].strip() == "0" for ln in active), \
        "lead_ttl_days must ship commented out (or 0) in sluice.yaml.example"
    assert "lead_ttl_days" in text, "the knob must be documented in the example config"


# ── #109: tier-2 company resolution's opt-in gate ────────────────────────────
# company_resolve_fetch needs its OWN guard, same reasoning as lead_ttl_days above:
# turning it on lets a blank-company lead trigger a REAL page visit, so an
# unconfigured install must never start doing that unprompted the moment it
# upgrades. Unlike lead_ttl_days this is a genuine bool field (no int/bool
# YAML-resolution hazard), so no extra validation is needed -- only the default.

def test_company_resolve_fetch_dataclass_default_is_off():
    assert TriageConfig().company_resolve_fetch is False


def test_company_resolve_fetch_loader_default_is_off(monkeypatch):
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert load_triage_config(None).company_resolve_fetch is False


def test_the_example_config_ships_company_resolve_fetch_commented():
    import yaml
    text = _EXAMPLE_PATH.read_text(encoding="utf-8")
    assert "company_resolve_fetch:" in text, "company_resolve_fetch must be documented at all"
    doc = yaml.safe_load(text) or {}
    assert "company_resolve_fetch" not in (doc.get("triage") or {}), \
        "company_resolve_fetch must ship COMMENTED, not active"


# ── #80: the example config must ship no machine-specific path ───────────────

# `key: value` on a line that may be commented out, or a block-sequence item (`- value`).
# The key class allows digits and hyphens: `[a-z_]+` alone would skip `oauth2_path:`.
# Anchored to the repo root, not the cwd. Three rows read this file, and a cwd-relative
# `Path("sluice.yaml.example")` makes every one of them FileNotFoundError when pytest is
# invoked from a subdirectory -- loud rather than silent, but a neutrality guard should
# not be contingent on where you happen to stand.

_EXAMPLE_SETTING = re.compile(r"^\s*#?\s*(?:([A-Za-z0-9_-]+):|-)(.*)$")

# A value is machine-specific if it is rooted at `/` or `~` -- but only AFTER quotes and
# list punctuation come off. `value.startswith(("/", "~"))` against the raw text was the
# whole bug this pair had on its first attempt: `vault_dir: "~/my-vault"` starts with a
# quote, so it sailed through, and the leak gate does not back that case up because it
# never greps `~` at all. The second pattern catches a home path anywhere inside a flow
# list or an inline comment-free tail, e.g. `dirs: [~/a, ~/b]`.
_ROOTED = re.compile(r"""^["'\[\s-]*[/~]""")
# `~/...` anywhere, or an absolute `/...` that is NOT a URL scheme's `//`. The colon is
# deliberately absent from the second alternative's delimiter class and `(?!/)` guards the
# double slash: without both, every `https://` in the example config reads as a filesystem
# path. Executed against the real file -- four false positives before, none after.
_ROOTED_ANYWHERE = re.compile(
    r"""(?:^|[\s'"\[{,:])~/"""            # a home-relative path anywhere
    r"""|(?:^|[\s'"\[{,])/(?![/\s])"""    # ...or an absolute one, but not a URL's //
    r"""|\$\{?HOME\b"""                    # ...or $HOME / ${HOME}, equally an opinion
    r"""|(?:^|[\s'"\[{,])[A-Za-z]:[\\/]""")  # ...or a Windows drive-letter root


def _setting_value(line):
    """The value of one example-config line, or None if the line is not a setting.

    Shared by the file sweep and the predicate rows below, deliberately: a helper that
    re-implemented this was measured unable to see a change to the real extraction, which
    is the copy-certifies-the-copy failure a reviewer found in the sibling guard one round
    earlier.
    """
    m = _EXAMPLE_SETTING.match(line)
    return None if m is None else m.group(2).strip()


def _example_setting_values():
    """(line, value) for EVERY setting in sluice.yaml.example, COMMENTS INCLUDED.

    Two properties, both learned the hard way. Comments are the whole point: the model
    for this scan (test_example_config_ships_lead_ttl_days_off) uses
    `ln.strip().startswith(...)`, which EXCLUDES comment lines -- and the path keys ship
    commented out, so that shape would be vacuous in the good state AND the bad one
    (`all()` over an empty list passes either way).

    And it sweeps every key, not just `*_dir:` ones. Keyed on the SHAPE of the value
    rather than on a list of key names, because the names are the part nobody remembers
    to update: `baseline_rel` is a path too, and a `*_dir`-only scan read as though it
    covered the file while leaving that one open.

    KNOWN LIMITS, measured rather than assumed, so nobody reads this as total coverage:
    a block-scalar body (`vault_dir: |` then an indented path on the NEXT line) is not
    seen, because this is line-oriented. `$HOME/...` and a Windows drive-letter root ARE
    now caught, after a reviewer pointed out that documenting them was the wrong call --
    they are machine-specific opinions exactly as `~/` is. Still missed: a `file:///` URL, whose first slash is
    preceded by the colon this pattern excludes so that `https://` does not read as a
    path. None of those shapes appears in the example file. The leak gate backstops the `/Users|/home`
    ones but never greps `~`, so the block-scalar tilde case has no second line of
    defence. Left as limits rather than chased, because each costs a YAML parser or a
    second pattern language, and the shapes the example file actually uses are covered.
    """
    text = _EXAMPLE_PATH.read_text(encoding="utf-8")
    out = []
    for line in text.splitlines():
        value = _setting_value(line)
        if value is None:
            continue
        # The trailing comment is NOT stripped. It used to be, which left
        # `vault_dir: vault  # e.g. ~/mine` -- the file's own idiom -- unswept; measured
        # against the real file, keeping the comment yields zero offenders, so the
        # coverage is free. (Full-line prose comments are excluded by _EXAMPLE_SETTING
        # not matching at all, which is what the strip was mistakenly credited with.)
        out.append((line, value))
    return out


def test_example_config_documents_every_root_path_key():
    # DERIVED from the dataclass, not hand-listed: a third root `*_dir` key added
    # without a line in the example would otherwise ship undocumented, and the sweep
    # below would pass over it in silence.
    keys = [f.name for f in dataclasses.fields(Config) if f.name.endswith("_dir")]
    assert keys, "no root *_dir key found -- the sweep below would be vacuous"
    text = _EXAMPLE_PATH.read_text(encoding="utf-8")
    missing = [k for k in keys if k not in text]
    assert not missing, f"root path keys undocumented in sluice.yaml.example: {missing}"


def test_example_config_ships_no_absolute_or_home_path():
    # sluice.yaml.example is COPIED VERBATIM by the documented quickstart, so a shipped
    # `/Users/someone/vault` is both a copied-in wrong answer and a person's machine
    # name in a public repo. Same rule the baseline_rel assertion applies one file over.
    values = _example_setting_values()
    # The paired "it discovered something" assertion. Without it the whole row is
    # satisfied by a broken extractor: witnessed by three reviewers independently --
    # move one character in _EXAMPLE_SETTING and a planted `vault_dir: ~/real` passes
    # green. Pinned to the ROOT path keys rather than a bare count, so a drift that
    # still matches 60 lines but loses the path ones also reddens.
    assert values, "the example-config extractor matched nothing -- this row is vacuous"
    keys = [f.name for f in dataclasses.fields(Config) if f.name.endswith("_dir")]
    found_keys = {m.group(1) for m in
                  (_EXAMPLE_SETTING.match(line) for line, _ in values)
                  if m and m.group(1)}
    missing = [k for k in keys if k not in found_keys]
    assert not missing, (
        f"the extractor no longer sees the root path keys it exists to check: {missing}")

    offenders = [line for line, value in values
                 if _ROOTED.match(value) or _ROOTED_ANYWHERE.search(value)]
    assert not offenders, (
        "sluice.yaml.example must ship no absolute or home-relative path -- it is "
        f"copied verbatim by the quickstart, and it is someone's machine: {offenders}")


# Every line form `sluice.yaml.example` actually uses. The scope assertion in the sweep
# above pins that the ROOT `*_dir` keys are seen -- but both of those ship COMMENTED and
# at column 0, so it is satisfied by the commented arm alone. Two reviewers found the
# same hole from opposite sides: drop `\s*` and the indented sub-app keys vanish
# (`triage.audit_jsonl`, `track.seen_db`, `track.token_path` are all indented AND
# commented); require `#` and the UNCOMMENTED arm vanishes, which is the arm a real
# machine-specific value would ship on. Neither drift reddened anything, and the leak
# gate is no backstop because it never greps `~`.
#
# So the forms are pinned directly, against the regex, rather than inferred from which
# keys happen to be present today.
_EXAMPLE_LINE_FORMS = [
    ("active at column 0", "store: vault", "vault"),
    ("active and indented", "  batch_size: 5", "5"),
    ("commented at column 0", "# vault_dir: x", "x"),
    ("commented and indented", "  # seen_db: x", "x"),
    ("block-sequence item", "  - remote", "remote"),
]


@pytest.mark.parametrize("label,line,expected", _EXAMPLE_LINE_FORMS,
                         ids=[f[0] for f in _EXAMPLE_LINE_FORMS])
def test_the_example_extractor_matches_every_line_form(label, line, expected):
    m = _EXAMPLE_SETTING.match(line)
    assert m, f"{label}: the extractor no longer matches {line!r}"
    assert m.group(2).strip() == expected


def test_the_example_file_really_contains_both_commented_and_active_settings():
    """...and that the forms above are not hypothetical.

    Pinning the regex alone would still pass if the FILE stopped exercising a form, at
    which point the sweep silently covers less than it reads as covering.
    """
    lines = [line for line, _ in _example_setting_values()]

    def _count(commented, indented):
        return sum(1 for ln in lines
                   if (ln.lstrip().startswith("#")) is commented
                   and (ln.startswith((" ", "\t"))) is indented)

    # Each COMBINATION independently. An aggregate `commented and active and indented`
    # passes while, say, no commented-and-indented line exists at all -- and that is the
    # class every sub-app path key belongs to.
    missing = [f"commented={c} indented={i}"
               for c in (True, False) for i in (True, False)
               if _count(c, i) == 0]
    assert not missing, (
        f"the example file no longer exercises these line forms: {missing} -- the sweep "
        "covers less than it reads as covering")


# The DETECTOR half. `_EXAMPLE_LINE_FORMS` above pins what the extractor SEES; nothing
# pinned what the predicate FLAGS, so both patterns could be replaced with never-matching
# ones and the suite stayed green -- with planted machine-specific values shipping clean.
# Same vacuity class as the extractor gap, one stage downstream, found the round after.
_MUST_FLAG = [
    ("bare home", "vault_dir: ~/my-vault"),
    ("quoted home", 'vault_dir: "~/my-vault"'),
    ("single-quoted home", "vault_dir: '~/my-vault'"),
    ("bare absolute", "vault_dir: /Users/someone/vault"),
    ("quoted absolute", 'vault_dir: "/opt/vault"'),
    ("home in a flow list", "dirs: [~/a, ~/b]"),
    ("absolute in a flow list", "dirs: [rel, /abs/b]"),
    ("absolute in a mapping", "vault_dir: {path: /abs/x}"),
    ("home in a trailing comment", "vault_dir: vault  # e.g. ~/mine"),
    ("block-sequence item", "  - ~/from-a-list"),
    ("indented and commented", "  # seen_db: ~/mine/seen.db"),
    # The four below are what `_ROOTED` ALONE catches: each is a home or root that is not
    # followed by the `/` its sibling's first alternative needs. Without them the whole
    # `_ROOTED` pattern could be replaced with a never-matching one and the suite stayed
    # green -- measured.
    ("the home directory itself", "vault_dir: ~"),
    ("quoted home directory", 'vault_dir: "~"'),
    ("another user's home", "vault_dir: ~someone/vault"),
    ("double-slash root", "vault_dir: //server/share"),
    # ...and these three are the inverse: `_ROOTED_ANYWHERE` only, via the alternatives
    # added for them. They are not rooted at `/` or `~` at all.
    ("env expansion", "vault_dir: $HOME/vault"),
    ("braced env expansion", "vault_dir: ${HOME}/vault"),
    ("windows drive letter", "vault_dir: C:\\Users\\someone\\vault"),
]
_MUST_NOT_FLAG = [
    ("relative", "vault_dir: ./vault"),
    ("bare word", "store: vault"),
    ("store-relative path", "baseline_rel: My CV/CV.md"),
    ("https url", "homepage: https://example.invalid/a/b"),
    ("http url with port", "homepage: http://h:1/p"),
    ("slash in prose", "note: a CIDR / bare IP"),
    ("number", "auto_apply_min: 0.75"),
    ("placeholder", "claude_max_host: <your-claude-host>"),
]


def _flags(line):
    value = _setting_value(line)
    assert value is not None, f"the extractor did not even see {line!r}"
    return bool(_ROOTED.match(value) or _ROOTED_ANYWHERE.search(value))


@pytest.mark.parametrize("label,line", _MUST_FLAG, ids=[f[0] for f in _MUST_FLAG])
def test_the_example_predicate_flags_every_machine_specific_shape(label, line):
    assert _flags(line), f"{label}: a machine-specific value would ship undetected"


@pytest.mark.parametrize("label,line", _MUST_NOT_FLAG, ids=[f[0] for f in _MUST_NOT_FLAG])
def test_the_example_predicate_spares_every_legitimate_shape(label, line):
    # The other direction matters as much: a guard that flags `https://` or `My CV/CV.md`
    # gets switched off, and then it guards nothing at all.
    assert not _flags(line), f"{label}: false positive on a legitimate value"
