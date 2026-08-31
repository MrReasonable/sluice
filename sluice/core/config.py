"""Layered configuration: code defaults < sluice.yaml < environment.

Every knob has a sane default so sluice runs with no config file at all; a
sluice.yaml overrides pieces of it; env vars win last so ops and offline tests
can override without editing files.
"""
import os
from dataclasses import dataclass, field, fields

from sluice.core.leads import LEAD_LAYOUTS, Lead
from sluice.core.paths import config_file
from sluice.core.urlguard import parse_allow_hosts

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a declared dependency
    yaml = None

# NB no root `locations` here: it was a DEAD key (declared, documented in
# sluice.yaml.example, read by nothing) and is retired outright by #8. The comment it
# carried called it "a loaded gun rather than a live bug, since the first consumer to
# wire it into a search or a gate would have inherited a stranger's 'remote only'".
# `sluice init` would have been that consumer -- a wizard asking for geography and
# writing it into a key nothing reads -- so `refuse_retired_locations` RAISES on it
# rather than letting it be dropped in silence. Geography lives at
# `triage.target_locations`, which is live.


@dataclass
class SourceConfig:
    enabled: bool = True
    tuning: dict = field(default_factory=dict)
    # Optional per-source search override: [[label, url, {params}?], ...]. When set,
    # it replaces the source's built-in example searches, so a user keeps their own
    # (personal) search list in config rather than in the code. Empty = use built-in.
    searches: list = field(default_factory=list)


@dataclass
class Config:
    sources: dict = field(default_factory=dict)  # id -> SourceConfig
    # Which implementation fills each adapter seam. Selection is by NAME, and an unknown
    # name raises at construction listing the valid ones -- it never falls through to a
    # default. For the store seam especially, a quiet wrong default means writing the
    # user's leads somewhere they did not ask for.
    store: str = "vault"
    fetcher: str = "camofox"
    # Where the store keeps the baseline CV. A STORE location, so it lives here: once the
    # store is resolved from the root Config, a `cv.baseline_rel` could not reach the store
    # that has to honour it. It used to work only because cv/engine.py passed it down by
    # hand (`vault.read_baseline(cvcfg.baseline_rel)`), which is the coupling this seam
    # exists to remove. Moving it silently would have been the worse bug -- a user pointing
    # at a curated baseline would get a stale one, with the fabrication gate still green --
    # so load_cv_config RAISES on the old key rather than dropping it.
    baseline_rel: str = "My CV/CV.md"
    # Where the vault lives (#80). It had no config key at all before, so it was
    # settable only by a VAULT_DIR env var that does not survive a new shell. Blank
    # means UNSET: stores/vault.py:_make does `env or this or None` and lets
    # Vault.__init__ keep supplying ./vault, so the store still owns its own default.
    # Unlike everything else #80 swept, the vault deliberately does NOT relocate to an
    # XDG root -- it is the user's Obsidian directory, their data, not sluice's state.
    vault_dir: str = ""
    # The dossier cache directory (#80). ONE root key, replacing the two sub-app keys
    # (triage.dossier_dir, cv.dossier_dir) that both defaulted to the same ./dossiers
    # literal -- so the cache was shared only by coincidence of that literal. Moving
    # one and not the other splits it and cv re-fetches every dossier over the live
    # SSRF-guarded network path, so the sharing is now structural rather than
    # tested-for. Lives on the root Config for the same reason dossier_allow_hosts
    # does: dossier_cache is called from BOTH sub-apps. Blank means UNSET -- resolution
    # is `env or config key or XDG`, and a non-empty default here short-circuits that
    # chain so the XDG location is never reached.
    dossier_dir: str = ""
    # Hosts/CIDRs exempt from the dossier fetcher's SSRF guard (#18). A SAFETY
    # allowlist, not a preference gate: empty means "no exceptions granted", NOT
    # "match nothing" -- an unconfigured install still fetches every public url,
    # because the address rule admits them, not this list. Lives on the root
    # Config, not TriageConfig/CvConfig, because dossier_cache is called from BOTH
    # sub-apps and a security policy that differs between them is a bug.
    dossier_allow_hosts: list = field(default_factory=list)
    notify: dict = field(default_factory=dict)
    # Coarse ingest title filter. Personal, so empty by default: an unconfigured
    # gate passes everything through rather than applying someone else's taste.
    relevance_keep: list = field(default_factory=list)
    relevance_drop: list = field(default_factory=list)
    # Words that decorate a location without locating it, subtracted before #5 compares
    # two postings for a split. Empty by default -> nothing subtracted (abstain).
    location_noise_words: list = field(default_factory=list)
    # Title-noise tokens stripped before #23's dedup clustering compares two roles. Empty by
    # default -> strictest clustering (nothing stripped), erring toward NOT merging (safe).
    dedupe_title_noise_words: list = field(default_factory=list)
    # Days since a lead was last seen in a scrape before it counts as stale (#9). 0 = OFF,
    # and off is the shipped default: "stale" is a judgement, and a shipped non-zero would
    # bin leads on a stranger's idea of it -- the 672ad2a class, where a preference baked
    # into source silently discarded someone's whole job hunt. Lives on the ROOT Config
    # because `leads expire`, cv and apply all read it, and a staleness policy that
    # differed between them would be a bug. NB `ttl_days` (cv/config.py, triage/config.py)
    # is the unrelated DOSSIER CACHE ttl; this name is deliberately distinct from it.
    lead_ttl_days: int = 0
    # Which named folder layout the lead store files notes into (#1). "" = flat, exactly as
    # before this existed; "active_archive" = Active/ + Archive/. Lives on the ROOT Config for the
    # same reason `location_noise_words` does: `Sluice.store()` resolves the store from
    # `self.config`, so a key the STORE must honour cannot sit in a sub-app block. OFF by default
    # -- sluice does not own the layout, it offers one, and an unconfigured install must be
    # byte-identical to the flat store.
    lead_layout: str = ""
    # The floor below which a fetched JD is treated as NOT HAVING ARRIVED (#169), so the
    # dossier cache refuses to persist it and triage refuses to spend a judge call on it.
    # 0 = the band is OFF and is the SHIPPED default: an EMPTY jd always fails (a fact),
    # but a character count is a judgement about what counts as a real posting, and an
    # active value would hand every copier one they never made -- the same rule
    # sluice.yaml.example states at length for `lead_ttl_days`. `job-sluice init` asks.
    # ROOT, not per-sub-app, because triage and cv SHARE one dossier directory: two
    # different floors over one directory means whichever sub-app ran last decides
    # whether an entry exists.
    min_jd_chars: int = 0

    def source(self, id: str) -> SourceConfig:
        """Config for a source id; unlisted sources default to enabled + no tuning."""
        return self.sources.get(id, SourceConfig())


def _str_list(value, name: str) -> list:
    """A list of strings from config, failing loudly. `None`/absent -> []. A YAML SCALAR
    (`location_noise_words: remote`) would otherwise `list()`-explode into single characters
    and silently mis-configure the gate; a clear error at construction is the house style
    (see _select_backend). Rejects a non-list and any non-string entry.

    Use this for a list whose ENTRIES are strings. For one whose entries are anything
    else -- `sources.<id>.searches`, whose entries are themselves lists -- use
    `refuse_wrong_container` below, which checks the container's SHAPE and says
    nothing about what is inside it. The two are not interchangeable in either
    direction: this one rejects every valid `searches` value, and that one accepts a
    list of integers where strings were meant."""
    if value is None:
        return []
    # Names the TYPE, never the VALUE. `relevance_keep`/`relevance_drop` are job-title
    # preferences and `location_noise_words` is geography -- personal, and a config file is
    # one of the few places a user's real ones legitimately live. That is the same
    # reasoning `load_config` gives for routing `dossier_allow_hosts` through
    # `parse_allow_hosts` INSTEAD of this helper, and `urlguard.py` already prints
    # `type(entries).__name__` for it; echoing here made that choice inconsistent with
    # itself (#176).
    #
    # The two arms are SEPARATE, and collapsing them was a real defect: with one message,
    # `relevance_keep: [2024]` said "must be a YAML list of strings, but got a list" --
    # naming the wrong problem and instructing the user to write what they had already
    # written. Dropping the value echo is right; dropping it without splitting the arms
    # took the information away with it. Naming the INDEX and the element's TYPE restores
    # what a user needs while still printing nothing they wrote -- the shape
    # `parse_allow_hosts` and `_merge_denylist` already use.
    if not isinstance(value, list):
        raise ValueError(
            f"{name} must be a YAML list of strings, but got a {type(value).__name__}. "
            f"Write it as `{name}: [first, second]`, or one `- first` per line.")
    bad = [(i, type(x).__name__) for i, x in enumerate(value) if not isinstance(x, str)]
    if bad:
        where = ", ".join(f"index {i} is a {t}" for i, t in bad)
        # No literal example VALUE in the message on purpose: any number named here
        # could coincide with the user's own, which would make the no-echo property
        # untestable and, worse, make a real leak look like boilerplate. Caught by
        # the no-echo test itself, which could not tell the two apart.
        raise ValueError(
            f"{name} must be a YAML list of STRINGS, but {where}. Quote the entry if "
            f"it is meant to be text -- an unquoted year or `true` is a number or a "
            f"boolean to YAML, not a string.")
    return list(value)


def refuse_wrong_container(block: str, key: str, value, default, *,
                           example: str = "") -> None:
    """Refuse a YAML SCALAR given for a field whose CODE DEFAULT is a list or a dict.

    SHAPE only. For a list whose entries must be strings, `_str_list` above is the
    stricter helper and the right one; this checks the container and stops there,
    which is what a field like `sources.<id>.searches` needs (its entries are
    themselves lists, so an element check would reject every valid value).

    Keyed on the DEFAULT's type, never on a field list, so a container field added
    later cannot quietly opt out -- the same shape, and the same reasoning, as the
    quoted-bool guard it is called beside -- which lives in the sub-app loaders, not in
    this file. Shape only, never range: `compose_timeout > 0`
    and the cross-field checks stay where their own reasons live.

    #176. `_str_list` ABOVE already named this exact hazard ("would otherwise
    `list()`-explode into single characters and silently mis-configure the gate") and
    was then wired to two root fields and none of the sub-app ones -- the half-applied
    defensive pattern this codebase treats as worse than none. Measured on the pre-fix
    tree, all three shapes are 672ad2a reached through a YAML typo instead of a shipped
    default:

      * `relevance_drop: senior` loaded as `['s','e','n','i','o','r']`, and
        `is_relevant` then returned False for EVERY title -- the whole scrape binned at
        ingest, before dedup and before a note exists anywhere to notice.
      * `triage.target_locations: remote` loaded as a `str`, and `classify` then kept
        every location: byte-identical to the unconfigured abstain, so a filter the
        user believes they configured does nothing.
      * `cv.fabrication_decoys: Acme` made the CV gate emit `FABRICATED: contains 'A'`
        and hard-block every CV.

    RAISES rather than coercing, and that is the load-bearing choice. Coercion looks
    kinder -- `remote` clearly means `[remote]` -- but it cannot be made safe, because
    the likeliest scalar is the COMMA-SEPARATED one: `job-sluice init` asks for these
    answers "comma-separated", and a user hand-editing YAML repeats that phrasing.
    `target_locations: London, Berlin` coerces to ONE token matching nothing, so every
    located lead is rejected. Coercion converts "the gate abstains" into "the gate
    matches nothing" -- the same bug class, one step further from view. Raising is also
    this repo's unanimous house style: every validator here refuses and names the key,
    and nothing coerces an unvalidated value.

    Never echoes the VALUE. `reject_companies`, `target_locations` and `employers` hold
    a real person's preferences, and `load_config` already declines `_str_list` for
    `dossier_allow_hosts` on exactly this ground.
    """
    # No `isinstance(default, bool)` arm: a bool is not a list or a dict, so the second
    # disjunct already returns for it. Spelling it out read as load-bearing while
    # deciding nothing -- the redundant-guard shape this repo treats as worse than none,
    # because it implies bools need special handling here and they do not.
    if not isinstance(default, (list, dict)):
        return
    if isinstance(value, type(default)):
        return
    qualified = f"{block}.{key}" if block else key
    if isinstance(default, list):
        # `example` is what stops this refusal INSTRUCTING the bug it exists to prevent.
        # The generic wording suits a list of plain strings; for a field whose entries are
        # themselves lists it is actively harmful. Measured on `sources.<id>.searches`,
        # whose entries are `[label, url, {params}?]`: a user following
        # "`searches: [first, second]`" verbatim gets a FLAT two-string list, each string
        # is then indexed `spec[0], spec[1]`, and "My search" becomes label='M', url='y'
        # -- the per-character explosion this whole helper exists to refuse, arrived at by
        # obeying the refusal. A refusal must be answerable without making things worse;
        # this repo already learned that from the CV parser's LOCATION field, where the
        # only actionable reading of the message was to invent a city.
        shape = (f"Write it as `{key}: {example}`."
                 if example else
                 f"Write it as `{key}: [first, second]`, or one `- first` per line.")
        # The "one CHARACTER at a time" warning is TRUE only for a string, which is the
        # common case and the dangerous one. Asserting it for `{key}: 5` or `{key}: true`
        # would be describing a mechanism that does not happen.
        if isinstance(value, str):
            shape += (f" A bare `{key}: value` is a STRING, and sluice would read it one "
                      f"CHARACTER at a time.")
    else:
        shape = f"Write it as `{key}:` followed by indented `name: value` lines."
    raise ValueError(
        f"{qualified} must be a YAML {'list' if isinstance(default, list) else 'mapping'}, "
        f"but got a {type(value).__name__}. {shape}")


# Params keys a search entry's `{params}` element must never be allowed to carry -- they
# collide with a `Lead` IDENTITY field and `_row_to_lead`'s `setattr` loop
# (`ingest/base.py`) applies every params key VERBATIM, with no guard of its own: a
# `params` key named `url` silently REPLACES the scraped url, and `url` is what the
# vault's non-resurrection match records into `seen.db` -- which has no removal path, so a
# poisoned url suppresses a real lead permanently with no note anywhere to reverse it.
# Measured (#212 round 4, arc-r4-001): `{"job_typ": "perm", "url": "PWN"}` (a typo'd key
# beside a colliding one) passes both the length and third-element-is-a-dict checks below
# and leaves `lead.url == "PWN"`.
#
# DERIVED from `Lead`'s own dataclass fields, never hand-listed -- CLAUDE.md's
# derive-don't-hand-list rule, and the reason arc-r4-001 gave for the ORIGINAL deferral
# ("restricting keys is a behaviour change against existing configs") is true only of a
# full ALLOWLIST, not of this narrow denylist: nobody writes `url:`/`company:`/`title:`
# into a search's params deliberately, so refusing those collisions is not a behaviour
# change any real config depends on.
#
# `job_type` is the ONE exclusion, and it is deliberate rather than an oversight: it is
# the SANCTIONED override `_row_to_lead`'s own docstring documents ("a perm search on a
# contract-default source still tags the lead job_type=perm"), and several shipped
# sources' `searches_spec` already rely on it (`ingest/sources/google.py`'s
# `{"job_type": "perm"}}`, `wttj.py`, `wellfound.py`, `escape_city.py`).
#
# What #212 does NOT do: define the full set of params keys #223 is allowed to use for
# anything beyond `job_type` (a pay floor, per the round-4 architect ruling). A params key
# that is neither a Lead-identity collision nor `job_type` is still applied VERBATIM today
# (see `test_a_non_colliding_params_key_is_still_applied_verbatim` in
# `tests/test_base_sources.py`) -- #223's implementer reads THIS function, not the #223
# spec, so that gap is recorded here rather than only on the issue.
_PARAMS_KEY_CLASH = frozenset(f.name for f in fields(Lead)) - {"job_type"}


def validate_search_entry(owner: str, index: int, entry) -> None:
    """Refuse a `sources.<id>.searches`-shaped ENTRY that is not `[label, url]` or
    `[label, url, {params}]` -- the shape a `Search` is built from by indexing
    positionally as `entry[0], entry[1], entry[2]`.

    ONE grammar, THREE call sites (#212 round 2 -- round 1 shipped this same grammar
    written out twice, in `load_config` here and in `ingest/base.py`'s `_mk_search`,
    with nothing binding the two together and #223 already queued to extend it):

      1. `load_config`, over every `sources.<id>.searches` entry -- the PRIMARY rung.
         A user's own config, caught at load time, naming the exact key
         (`sources.{sid}.searches[{index}]`) before `ingest list-sources`/`test-source`/
         `run` ever construct a `Search`.
      2. `ingest/base.py`'s `_mk_search` -- defence in depth for anything that builds a
         `Search` WITHOUT going through `load_config`: a test, a future caller, or a
         source's own `searches_spec` reached via `.searches()`.
      3. `ingest/base.py`'s `BrowserListSource.__post_init__` -- the source CONTRACT
         declaration itself, over the whole `searches_spec`, beside `validate_posting_paths`/
         `validate_reprobed` which already validate that class's other two contract
         fields. Without this rung, a malformed `searches_spec` still constructs fine --
         rungs 1 and 2 only run when `load_config` or `.searches()` is actually called --
         so the registry's per-plugin isolation ("a broken plugin must not sink the rest")
         never gets a chance to run, because nothing raises at the point a broken plugin is
         imported. Rung 3 catches it there instead, at construction, the same reasoning
         `validate_posting_paths`/`validate_reprobed` are already there for.

      Measured, three shapes all survive rung 1's SIBLING container check
      (`refuse_wrong_container`, which only confirms `searches` itself is a list) and then
      explode positionally: `[["OnlyLabel"]]` (too short) raised a bare `IndexError`; the
      natural YAML mapping spelling of an entry (`- label: x` / `url: y`, which parses to a
      dict) raised `KeyError(0)`; and a scalar third element (`["L", "u", "perm"]`, the shape
      a user reaches for un-braced) passed both original rungs and only exploded inside
      `_row_to_lead`'s `{**extra, **search.params}` merge as `TypeError: 'str' object is not
      a mapping` -- `status=error`, naming none of source, key or index.

      This function lives in `core/config.py` rather than beside `validate_posting_paths`/
      `validate_reprobed` in `ingest/base.py`: no `core/` module imports a sub-app AT MODULE
      SCOPE (measured: 22 core modules, 0 such imports). `core/app.py` is the one exception,
      and it is the composition root -- 34 sub-app imports, every one of them LAZY inside a
      method body, wiring sub-apps together at call time. `core/config.py` is the BASE of
      that stack, not a peer of `app.py`: every sub-app config loader and `cli.py` itself
      sit on it, and `load_config` would need the import inside a per-ENTRY validation loop,
      so `app.py`'s lazy-import pattern does not transfer here. Hence the grammar lives in
      `core/config.py` and `ingest/base.py` imports it, never the reverse -- `ingest/base.py`
      already imports `sluice.core.leads`, so this adds no new dependency direction, only a
      new module. `tests/test_core_layering.py` is the executable guard: a subprocess
      witness proves importing every `core/` module never eagerly drags a sub-app into
      `sys.modules`, and a static sweep proves no `core/` module other than `app.py` names
      a sub-app at all -- either spelling, lazy or eager.

    Never echoes the ENTRY. A `sources.<id>.searches` entry is the single most
    preference-dense value in a sluice config -- a label plus a board URL that reliably
    carries target role keywords and `location=` -- and an uncaught `ValueError` is printed
    by `cli.py` as one copy-pasteable line, so it reaches logs, bug reports and pasted
    tracebacks exactly the way `refuse_retired_locations`'s docstring (this same file) warns
    against: "an exception travels further ... than the config file it came from." Reports
    only the OBSERVED SHAPE -- `type(x).__name__`, and `len(x)` for a sized CONTAINER other
    than a bare string -- never the value, and never a mapping's keys either (a YAML mapping's
    keys can be user text too, as the `- label: x` spelling above shows). A bare string's own
    length is withheld too (`got a str`, not `got a str of length 12`): unlike a list or dict's
    length, a string's length is derived from the user's real search text, so reporting it
    would leak a fragment of the very value this function is careful everywhere else never to
    reproduce. The qualified key and index already point the user at the exact line; nothing
    here needs the content to be actionable.

    The third element's KEYS are checked against `_PARAMS_KEY_CLASH` (a `Lead` identity-field
    collision, `job_type` excluded -- see that constant's own comment); a colliding key is
    named in the refusal, because a KEY is structural sluice vocabulary, not the user's search
    text, so naming it does not violate never-echo. What is still UNCHECKED, deliberately
    deferred to #223: which NON-colliding params keys are actually meaningful (`job_type`
    today; a pay floor is the queued #223 case). A typo'd or invented key that does not
    collide with a `Lead` field is still applied VERBATIM by `_row_to_lead`'s `setattr` loop
    -- read this paragraph before "closing" that gap; it is #223's allowlist to design, not a
    round-4 omission.
    """
    qualified = f"{owner}[{index}]"
    shape = "[label, url]` or `[label, url, {params}]"
    if not isinstance(entry, (list, tuple)) or len(entry) not in (2, 3):
        if isinstance(entry, (str, bytes)):
            # A bare string IS the entry here (`sources.<id>.searches: - my search`), and
            # its length would itself leak a fragment of the user's real search text --
            # unlike a list/dict/tuple's length, which says nothing about content.
            # `bytes` for the same reason and not as defensive padding: PyYAML's SafeLoader
            # resolves `!!binary` to bytes, so `- !!binary <base64 of the search>` reaches
            # here and its length is the search text's length just as a str's is.
            observed = f"a {type(entry).__name__}"
        else:
            try:
                observed = f"a {type(entry).__name__} of length {len(entry)}"
            except TypeError:
                # No `len()` at all (an int, a bool, `None`, ...) -- the round-1
                # raw-traceback bug class on a shape `refuse_wrong_container` cannot see
                # (it only checks that `searches` itself is a list, not each entry):
                # `- 5` / `- null` / `- true` under `searches:` all land here.
                observed = f"a {type(entry).__name__}"
        raise ValueError(f"{qualified} must be `{shape}`, got {observed}")
    label, url = entry[0], entry[1]
    if not isinstance(label, str) or not isinstance(url, str):
        bad, bad_type = (("label", type(label).__name__) if not isinstance(label, str)
                          else ("url", type(url).__name__))
        raise ValueError(
            f"{qualified} must be `{shape}` with label and url as strings -- the {bad} "
            f"is a {bad_type}")
    if len(entry) == 3 and entry[2] is not None:
        if not isinstance(entry[2], dict):
            raise ValueError(
                f"{qualified} must be `{shape}` with the third element a `{{params}}` "
                f"mapping -- got a {type(entry[2]).__name__}")
        clashing = _PARAMS_KEY_CLASH & entry[2].keys()
        if clashing:
            # Naming the KEY is fine (never-echo protects the user's search TEXT, and a key
            # is drawn from sluice's own small `Lead`-field vocabulary, never from it) --
            # naming the VALUE would not be, so it never appears here.
            raise ValueError(
                f"{qualified} params key(s) {sorted(clashing)} collide with a Lead identity "
                f"field and would silently replace the scraped value (a `url` key, for "
                f"example, would replace the real url, which drives non-resurrection "
                f"matching recorded permanently in seen.db) -- rename the key")


# The claude-max CLI's LOCATION, which is a deployment fact rather than a preference: the same
# category as CAMOFOX_URL, whose own comment says env overrides it "so offline tests / alt
# sessions need no code change". It earns an env override for the same reason, and #209 is the
# case that forced it -- inside a container there is no `claude` binary and no way to reach one,
# and until now the only route to `claude_max_host` was a mounted config.yaml, because none of
# the three sub-app key pairs was settable any other way.
#
# ONE variable pair for all three sub-apps, not six. The keys are separate so triage, cv and
# track CAN run against different hosts, and that stays true -- but nobody varies WHERE the CLI
# lives per sub-app in the case this exists for, and six variables to express one fact is a
# configuration surface nobody would thank us for. If you genuinely need them to differ, leave
# these unset and use the config keys, which is the documented layering working as intended
# rather than a special case.
_CLAUDE_HOST_ENV = "SLUICE_CLAUDE_HOST"
_CLAUDE_PATH_ENV = "SLUICE_CLAUDE_PATH"


def apply_claude_cli_env(cfg, *, host_attr: str, path_attr: str) -> None:
    """Let the environment say WHERE the claude CLI is, overriding the config block.

    Applied AFTER a loader's YAML pass, never inside it. The sub-app loaders are deliberately
    `hasattr`-filtered `setattr` loops over whatever the block contained, and CLAUDE.md says in
    terms that they must not be "fixed" into naming their own fields -- so this names the two
    attributes explicitly from outside instead of teaching the loop about them.

    `cv` spells its pair `compose_host`/`compose_claude_path` while triage and track use
    `claude_max_host`/`claude_max_path`, which is why the attribute names are parameters rather
    than assumed.

    An EMPTY env var is ignored rather than treated as "no host". Exporting a variable to the
    empty string is how a shell says nothing, and reading it as an instruction to run the CLI
    locally would silently undo a configured remote host -- the quiet-wrong-default class this
    codebase engineers out everywhere else.
    """
    # Fail loudly on a name the config does not have. A bare `setattr` would CREATE the
    # attribute instead, leaving a dead value nothing reads while the env var silently stops
    # reaching that sub-app -- and the test cannot see it, because it hand-lists the same
    # literals it reads back with `getattr`. Same posture `load_cv_config` already takes on a
    # retired `cv.name`.
    for attr in (host_attr, path_attr):
        if not hasattr(cfg, attr):
            raise AttributeError(
                f"{type(cfg).__name__} has no {attr!r}; the claude-CLI env override names a "
                f"field that no longer exists, so it would silently stop applying")
    host = os.environ.get(_CLAUDE_HOST_ENV, "").strip()
    path = os.environ.get(_CLAUDE_PATH_ENV, "").strip()
    if host:
        setattr(cfg, host_attr, host)
    if path:
        setattr(cfg, path_attr, path)


def sub_app_block(block: str, loaded: object) -> dict:
    """Normalise a sub-app's top-level block to a mapping, raising `ValueError` when it
    is anything else.

    Every sub-app loader reads its block as `(yaml.safe_load(f) or {}).get("<block>") or
    {}`, which is only a mapping when the user wrote one. Measured against a real config
    file, the four non-mapping spellings fail in three different, all-wrong ways:

      cv: "hello"        -> AttributeError: 'str' object has no attribute 'get'
      cv: [a, b]         -> AttributeError: 'list' object has no attribute 'get'
      cv: 5 / cv: true   -> TypeError: argument of type 'int' is not a container
      cv: my name is here -> ValueError, but the WRONG one -- `"name" in data` is a
                             SUBSTRING test on a str, so the #133/#107 migration guard
                             fires and tells the user to move a `cv.name` key they
                             never set.

    Two harms, and the third spelling is the worse of them. `doctor` is the command a
    user runs BECAUSE their config is wrong; it guards `load_cv_config()` with `except
    ValueError` precisely so a bad `cv:` block becomes a DEAD row rather than a
    traceback, and `AttributeError`/`TypeError` walk straight through that handler. And
    a wrong diagnosis is worse than a raw traceback: it sends the user to edit a key
    that is not there.

    Normalising HERE rather than widening `doctor`'s `except` clause is what fixes both
    at once -- with `data` guaranteed a mapping, every `"<key>" in data` membership test
    in every loader is a key lookup again rather than a substring match. It is applied
    to all four loaders, not just `cv`: they share the identical read, so guarding one
    would leave the same trap armed in three places (and `doctor` calls
    `load_triage_config`/`load_track_config` unguarded, where a clean `ValueError` is
    still a better failure than an `AttributeError` from deep inside a setattr loop).

    The message names the block and the TYPE found, never the value: a malformed `cv:`
    block usually contains whatever the user was mid-way through typing, and an
    exception travels further than the file it came from -- the same ruling
    `refuse_retired_dossier_dir` and `dossier_allow_hosts` already make.
    """
    if loaded is None or loaded == {}:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(
            f"the `{block}:` block of sluice.yaml must be a mapping of settings, but it "
            f"holds a {type(loaded).__name__}. Check the indentation under `{block}:` -- "
            f"each setting belongs on its own indented line, as `  key: value`.")
    return loaded


def refuse_retired_dossier_dir(block: str, data: dict) -> None:
    """Raise if a sub-app block still carries the retired `dossier_dir` key (#80).

    One helper rather than a raise written out in each loader, so the two cannot drift
    apart or one of them quietly not fire -- and a partial retirement is the bad case
    here, since it is what splits the shared cache.

    Both loaders filter unknown keys with `hasattr`, so without this the key is dropped
    in SILENCE: a user who had pointed cv at its own dossier directory would get a
    different one, with no signal and the fabrication gate still green. Fail loudly at
    construction, this repo's rule precisely because a quiet wrong default is the bug
    class it most consistently engineers out.

    The message names the key and its replacement and NEVER echoes the value. That
    differs deliberately from the `cv.baseline_rel` raise it is modelled on:
    `baseline_rel` is a store-RELATIVE name, while this is a host path usually under a
    home directory, and an exception travels further (logs, bug reports, pasted
    tracebacks) than the config file it came from. `dossier_allow_hosts` above already
    rules that way for the same reason.
    """
    if "dossier_dir" in data:
        raise ValueError(
            f"{block}.dossier_dir has moved to the top level of sluice.yaml. triage and "
            f"cv share ONE dossier cache, and two keys could split it -- with cv then "
            f"re-fetching every dossier over the network. Move it out of the `{block}:` "
            f"block to a root `dossier_dir:` key.")


def refuse_retired_locations(data: dict) -> None:
    """Raise if a config still sets the retired root `locations` key (#8).

    Declared, documented in `sluice.yaml.example`, and read by NOTHING -- the comment it
    carried called it "a loaded gun rather than a live bug, since the first consumer to
    wire it into a search or a gate would have inherited a stranger's 'remote only'".
    `sluice init` would have been that consumer, so the key is retired rather than
    finally populated.

    BOTH spellings, because the loader also honoured `$SLUICE_LOCATIONS`. Raising on the
    file while staying silent on the environment is precisely the asymmetry the
    fail-loudly rule exists to remove: a user who set geography in their shell would
    watch it quietly stop being read, which is the same silent-wrong-default bug class
    this codebase most consistently engineers out.

    The VALUE is never echoed. Geography is personal, and an exception travels further
    (logs, bug reports, pasted tracebacks) than the config file it came from -- the same
    ruling `refuse_retired_dossier_dir` and `dossier_allow_hosts` already make.
    """
    if "locations" in data or "SLUICE_LOCATIONS" in os.environ:
        raise ValueError(
            "the root `locations` key (and $SLUICE_LOCATIONS) was read by nothing and "
            "has been retired. Geography is a triage concern -- move your value to the "
            "`target_locations:` key inside the `triage:` block, and `unset SLUICE_LOCATIONS` "
            "if you exported it.")


def _safe_scalar_repr(value) -> str:
    """`repr(value)` for a genuine SCALAR typo, `type(value).__name__` for a CONTAINER.

    Backs the three `lead_ttl_days`/`lead_layout`/`min_jd_chars` raises below. Each is
    documented as never-echo EXEMPT on the ground that "a TTL, a layout name and a
    character count are not personal" -- true of the value the field is SUPPOSED to hold,
    and false of the value the raise actually sees when a config file is misindented one
    level: a YAML block that was meant to sit under a SIBLING key (`target_locations`,
    `reject_companies`, ...) then becomes THIS key's value, a list or dict carrying that
    sibling's real content, and `!r}` reproduces the whole thing in one copy-pasteable
    `ValueError` (#212 round 4, neu-r4-001 -- measured). A genuine scalar mistype
    (`lead_ttl_days: yes` -> `True`, or a bare typo'd string) keeps the diagnostic repr
    these raises were written to give; only a list/dict -- which these three fields never
    legitimately hold -- switches to the type name.

    `bytes` joins list/dict for the identical reason, not as defensive padding: PyYAML's
    SafeLoader resolves a `!!binary` scalar to `bytes`, so a misindented block tagged
    `!!binary` reaches here as a `bytes` value, and `repr()` reproduces it in full exactly
    as it would a list or dict -- the same gap `validate_search_entry` closed one commit
    earlier for `sources.<id>.searches` entries, on the sibling seam these three raises
    share. None of the three fields ever legitimately holds `bytes` either.
    """
    # ALLOW-LIST, not a deny-list of containers, and that inversion is the whole point.
    # The deny-list spelling shipped three times and was wrong three times: `(list, dict)`
    # missed `bytes` (`!!binary`), then `(list, dict, bytes)` missed `set` and `tuple`
    # (`!!set` is plain SafeLoader, and `_safe_scalar_repr({'a','b'})` reproduced both
    # members) -- each round closing the type that had just been reported and leaving the
    # class open. Naming what is SAFE to reproduce cannot go stale that way: a scalar that
    # is not one of these five cannot be reproduced at all, whatever YAML tag reaches it.
    #
    # These five are the types whose repr cannot carry free text a user wrote: an int, a
    # float, a bool (`lead_ttl_days: yes` -> `True`, the diagnostic these raises exist to
    # give) and None are closed vocabularies, and a `str` here is a scalar typed on THIS
    # key's own line -- never a sibling's misindented block, which arrives as a list or a
    # dict and is exactly what neu-r4-001 measured leaking.
    return repr(value) if isinstance(value, (int, float, bool, str)) or value is None \
        else f"a {type(value).__name__}"


def load_config(path: str | None = None) -> Config:
    data = {}
    path = path or config_file()
    if path and os.path.exists(path) and yaml is not None:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # Before any field is read, so a retired key is reported rather than dropped in the
    # silence a `data.get` would give it.
    refuse_retired_locations(data)

    sources = {}
    # #176: a scalar here reached `.items()` and died with a bare AttributeError
    # naming no key at all -- worse than the list case, which at least produced a
    # value. The per-source check below is the same hazard one level down.
    if data.get("sources") is not None:
        refuse_wrong_container("", "sources", data["sources"], {})
    for sid, sconf in (data.get("sources") or {}).items():
        if sconf is not None:
            refuse_wrong_container("sources", sid, sconf, {})
        sconf = sconf or {}
        if sconf.get("searches") is not None:
            # `example` because a searches ENTRY is itself a list -- see the
            # helper. The generic "[first, second]" wording would tell a user to
            # write the exact flat shape that explodes per character.
            refuse_wrong_container(
                f"sources.{sid}", "searches", sconf["searches"], [],
                example='[["My label", "https://example.invalid/jobs"]]')
            # SHAPE, one level down from the container check just above: that call refuses a
            # scalar `searches:` value but says nothing about what each ENTRY looks like, so
            # `[["OnlyLabel"]]` and the natural YAML mapping spelling of an entry both passed
            # it and reached `_mk_search` as a bare `IndexError`/`KeyError` -- see
            # `validate_search_entry`'s own docstring for the measured reproduction.
            for _index, _entry in enumerate(sconf["searches"]):
                validate_search_entry(f"sources.{sid}.searches", _index, _entry)
        if sconf.get("tuning") is not None:
            refuse_wrong_container(f"sources.{sid}", "tuning", sconf["tuning"], {})
        sources[sid] = SourceConfig(
            enabled=bool(sconf.get("enabled", True)),
            tuning=dict(sconf.get("tuning") or {}),   # guarded by the sid check above
            # #176: same hazard one level down -- a scalar would explode into one
            # search per character and quietly scrape nothing useful. Deliberately
            # NOT `_str_list`: a search entry is a nested LIST -- `[label, url]` with
            # an optional third `{params}` element -- not a string, so that helper's
            # element check would reject every valid value. Shape only is exactly
            # the distinction `refuse_wrong_container` draws.
            searches=list(sconf.get("searches") or []),
        )

    if data.get("notify") is not None:
        refuse_wrong_container("", "notify", data["notify"], {})
    notify = dict(data.get("notify") or {})
    token = os.environ.get("SLUICE_TELEGRAM_TOKEN")
    chat = os.environ.get("SLUICE_TELEGRAM_CHAT")
    if token or chat:  # keep secrets out of the yaml; supply via env
        tele = dict(notify.get("telegram") or {})
        if token:
            tele["token"] = token
        if chat:
            tele["chat_id"] = chat
        notify["telegram"] = tele

    # Validate here so a malformed entry fails at CONSTRUCTION, naming the key and
    # the entry's index -- never its value. Deliberately NOT _str_list: that raises
    # with `got {value!r}`, i.e. the whole list, and a config file is one of the few
    # places a user's real private hostnames legitimately live.
    raw_allow = data.get("dossier_allow_hosts")
    # Pass the RAW value: `list(...)` first would explode a YAML scalar into one
    # entry per character BEFORE parse_allow_hosts' isinstance guard could fire, so
    # `dossier_allow_hosts: myboard` would load silently as seven inert one-character
    # grants on a SAFETY allowlist. That is the bug class `_str_list` exists for.
    parse_allow_hosts([] if raw_allow is None else raw_allow)
    allow = list(raw_allow or [])   # coerce only AFTER validation has passed

    # #9. ABSENT is the abstain case, not an error -- an unconfigured install must load
    # and expire nothing. `bool` is checked FIRST and separately because it SUBCLASSES
    # int: PyYAML resolves yes/on/true to True, so `lead_ttl_days: yes` -- what a user
    # naturally types to turn this feature ON -- would otherwise pass an isinstance(int)
    # check and set a ONE-DAY ttl, marking every lead stale with no error anywhere.
    raw_ttl = data.get("lead_ttl_days")
    raw_ttl = 0 if raw_ttl is None else raw_ttl
    if isinstance(raw_ttl, bool) or not isinstance(raw_ttl, int) or raw_ttl < 0:
        raise ValueError(
            f"lead_ttl_days must be a non-negative integer (0 = off), got "
            f"{_safe_scalar_repr(raw_ttl)}")

    # #1. Validated HERE as well as in `Vault.__init__`, and the two are NOT redundant. A
    # loader-only check is an equivalent mutant for every one of the ~150 direct `Vault(...)`
    # constructions in the suite; a constructor-only check lets a typo in the YAML reach the user
    # as an uncaught ValueError traceback out of `args.func`, where `lead_ttl_days` above renders
    # a `sluice: ...` usage error and exits 2. Same knob shape, same failure surface.
    raw_layout = data.get("lead_layout")
    if raw_layout is not None and raw_layout not in LEAD_LAYOUTS:
        raise ValueError(
            f"lead_layout must be one of "
            f"{', '.join(repr(n) for n in LEAD_LAYOUTS)}, got {_safe_scalar_repr(raw_layout)}")

    # #169. Same shape as lead_ttl_days above, same reason: bool checked FIRST and
    # separately because it SUBCLASSES int, so `min_jd_chars: yes` -- the natural
    # spelling to turn this on -- would otherwise pass isinstance(int) and load as a
    # one-character floor, letting nearly every fetched JD through with no error.
    raw_floor = data.get("min_jd_chars")
    raw_floor = 0 if raw_floor is None else raw_floor
    if isinstance(raw_floor, bool) or not isinstance(raw_floor, int) or raw_floor < 0:
        raise ValueError(
            f"min_jd_chars must be a non-negative integer (0 = off), got "
            f"{_safe_scalar_repr(raw_floor)}")

    # NB this loader names every field EXPLICITLY -- no splat, no loop, unlike the four
    # sub-app loaders' hasattr+setattr loops. A dataclass field added without a line
    # here is therefore dead: it loads as its default whatever the YAML says, silently.
    #
    # That is NOT what happened to triage/config.py's two dead keys, which is a different
    # failure with the same symptom and worth keeping distinct: the triage LOADER read
    # them fine (its loop sets any field the dataclass declares), but nothing downstream
    # ever consulted the result -- app.py read $TRIAGE_AUDIT and $DOSSIER_DIR directly.
    # A key can therefore die at either end, and only enumerating BOTH finds them.
    return Config(sources=sources, notify=notify,
                  store=str(data.get("store") or "vault"),
                  baseline_rel=str(data.get("baseline_rel") or "My CV/CV.md"),
                  vault_dir=str(data.get("vault_dir") or ""),
                  dossier_dir=str(data.get("dossier_dir") or ""),
                  fetcher=str(data.get("fetcher") or "camofox"),
                  # #176: `_str_list`, not `list(...)`. The bare `list()` here was the
                  # SEVEREST instance of the bug this helper was written for and the
                  # one the issue did not name -- these two run at INGEST, before
                  # dedup and before any LLM call, so `relevance_drop: senior` loaded
                  # as six single characters and binned the entire scrape with nothing
                  # written to the vault to notice it.
                  relevance_keep=_str_list(data.get("relevance_keep"),
                                           "relevance_keep"),
                  relevance_drop=_str_list(data.get("relevance_drop"),
                                           "relevance_drop"),
                  location_noise_words=_str_list(data.get("location_noise_words"),
                                                 "location_noise_words"),
                  dedupe_title_noise_words=_str_list(data.get("dedupe_title_noise_words"),
                                                     "dedupe_title_noise_words"),
                  lead_ttl_days=raw_ttl,
                  lead_layout=raw_layout or "",
                  min_jd_chars=raw_floor,
                  dossier_allow_hosts=allow)
