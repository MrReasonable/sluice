"""Triage configuration: code defaults overlaid by the `triage:` block of
sluice.yaml. Role/geo/pay rules live here so tuning your preferences is a
config edit, not a code change. Every field has a sane default, so triage runs
with no config file at all."""
import os
from dataclasses import dataclass, field

from sluice.core.config import (apply_claude_cli_env, refuse_retired_dossier_dir,
                                refuse_wrong_container, sub_app_block)
from sluice.core.paths import config_file, resolve

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# Role preferences are entirely personal, so the code ships with NO opinion:
# which titles you want (and which disqualify a role) is yours to declare in the
# `triage:` block of sluice.yaml (see sluice.yaml.example). Empty accept/reject
# lists mean the title gate simply abstains and every lead reaches the LLM judge,
# which reads the criteria from your vault Judging Profile.
_ACCEPT: list = []
_REJECT: list = []
# Geography and company preferences are personal too, so they ship empty as well.
# Empty target_locations means the location gate ABSTAINS (see classify): a fresh
# install must never silently reject every job that is not remote.
_TARGET_LOC: list = []
_REJECT_LOC: list = []
_REJECT_CO: list = []

# #309: the ceiling on `dossier_concurrency`. A field whose stated purpose is not
# bursting a job board with tabs needs a number above which it refuses -- otherwise the
# guard against excess has no limit of its own. 16 is deliberately generous: measured
# gain flattens well below it (the fetches contend on one browser process and one event
# loop), so a value this high is already a mistake, and the point is to catch the typo
# that meant 4, not to price the last increment.
DOSSIER_CONCURRENCY_MAX = 16


@dataclass
class TriageConfig:
    accept_titles: list = field(default_factory=lambda: list(_ACCEPT),
        metadata={"gate_role": "abstain"})
    reject_titles: list = field(default_factory=lambda: list(_REJECT),
        metadata={"gate_role": "abstain"})
    target_locations: list = field(default_factory=lambda: list(_TARGET_LOC),
        metadata={"gate_role": "abstain"})
    reject_locations: list = field(default_factory=lambda: list(_REJECT_LOC),
        metadata={"gate_role": "abstain"})
    reject_companies: list = field(default_factory=lambda: list(_REJECT_CO),
        metadata={"gate_role": "abstain"})
    # One floor per pay BASIS, each judged only against its own (#223). All default 0 =
    # no floor, which is what makes a fresh install abstain rather than bin: a shipped
    # non-zero floor here is the 672ad2a silent-rejection class, and hourly/weekly are in
    # this list precisely BECAUSE their absence was that class -- an unparsed basis fell
    # to `perm_floor_gbp` and `£2,000 per week`, about £104k a year, was rejected as a
    # sub-floor salary.
    #
    # Four knobs rather than a day floor plus conversion constants. A conversion needs
    # shipped hours-per-day and days-per-week numbers, which are an assumption about
    # someone's working pattern -- a preference wearing the clothes of a parsing fact,
    # and wrong for anyone on a four-day week. A floor per basis asks the user for the
    # number they actually have an opinion about, and abstains until they give it.
    contract_floor_gbp_hour: int = 0
    contract_floor_gbp_day: int = 0
    contract_floor_gbp_week: int = 0
    perm_floor_gbp: int = 0
    batch_size: int = 5
    ttl_days: int = 7
    # #309: how many dossier fetches may be in flight at once. The fetch phase is
    # latency-bound -- a page load plus UP TO `dossier_settle_ms` (a root config key, not
    # a triage one) of settle -- so a run's wall clock scales with uncached leads until
    # this is raised. "Up to": `_settle_body` returns as soon as two consecutive reads
    # agree, so a page that renders server-side pays one interval, not the budget. Only
    # a slow client-rendered posting pays the whole thing, which is also the case with
    # the most to gain here.
    #
    # Defaults to 1: one fetch in flight, the pre-#309 fetch RATE. It is NOT pre-#309
    # behaviour in full, and no setting restores that -- #309 hoists the whole fetch
    # phase ahead of the apply phase unconditionally, where the two used to interleave
    # per lead. An interrupted run therefore has paid for every page load while having
    # written fewer vault statuses than the old code would have by the same point.
    #
    # Opt-in rather than opt-out because this drives an anti-fingerprint browser:
    # several leads from one board in a night would otherwise burst concurrent tabs at a
    # single site, which is the behaviour that gets a session flagged. Leads sharing a
    # url are already collapsed to one fetch by cache key, but distinct postings on one
    # host are not: there is no per-host cap yet, so the safe ceiling is a judgement
    # about YOUR lead mix, not a number this can pick.
    dossier_concurrency: int = 1
    # NB no `dossier_dir` here: it was a DEAD key (declared, read by nothing) and is
    # retired outright by #80 in favour of one root `dossier_dir`. load_triage_config
    # RAISES on it rather than letting `hasattr` drop it in silence.
    # Blank, not a path (#80), and load_triage_config fills it in. A non-empty default
    # is always truthy, so it short-circuits `env or config key or XDG` before the XDG
    # location is reached -- the field would never move, silently.
    audit_jsonl: str = ""
    # A single rolling digest, named distinctly from the legacy per-lead
    # "Rejected Leads/" folder so the two do not collide in Obsidian.
    rejected_note: str = "Job Applications/Rejected Leads Audit.md"
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    cheap_model: str = "deepseek-v4-flash"
    claude_max_model: str = "claude-sonnet-4-5"
    claude_max_effort: str = "medium"
    # Host + claude binary path for the ClaudeMaxBackend this sub-app builds.
    # Empty host runs claude_max_path locally; set a host to shell out over ssh.
    claude_max_host: str = ""
    claude_max_path: str = "claude"
    route_borderline: bool = False
    # Off by default (#109): gates the tier-2 (real, no-LLM page-visit) half of
    # blank/placeholder-company resolution independently of --no-llm. An unconfigured install
    # must not start opening real browser tabs against arbitrary third-party sites
    # for its whole needs_review backlog the moment it upgrades -- the same
    # abstain-by-default posture as lead_ttl_days/lead_layout. Tier 1 (free,
    # URL-pattern-only) is unaffected by this knob and always runs.
    company_resolve_fetch: bool = False
    # Off by default (#120): gates tier 3, which hands the page data tier 2 already
    # fetched (no second page visit) to the CHEAP backend instead of two regexes.
    # A SIBLING of company_resolve_fetch, not a widening of it: the two buy different
    # things with different currencies -- the fetch spends a real page load, this
    # spends money -- so an install that already opted into the free-network page
    # visit must not silently start paying for LLM calls the moment it upgrades.
    # STRICTLY narrower than company_resolve_fetch; see load_triage_config's
    # cross-field check below.
    company_resolve_llm: bool = False
    # Off by default (#305): whether a triage run may FETCH live exchange rates once at
    # its start. The pay floors are denominated in one currency and adverts are not, so
    # `classify` converts before comparing -- but the conversion works from a pinned rate
    # table that ships with the release, and a run that never fetches is correct, merely
    # slightly stale.
    #
    # A SIBLING of company_resolve_fetch, and off for the same reason: it spends a real
    # network round trip, and an install that never opted into outbound traffic must not
    # start making it on upgrade. Rates drift a few percent a year, far below the
    # precision a pay floor needs, so leaving this off costs a user almost nothing --
    # which is exactly why the default can afford to be the safe one.
    refresh_fx_rates: bool = False


def load_triage_config(path: str | None = None) -> TriageConfig:
    cfg = TriageConfig()
    path = path or config_file()
    # An INVERTED guard rather than the early `return cfg` this replaced (#80): the
    # resolution below must run on every path out of this function, and the
    # no-config-file case is exactly what a fresh install gets.
    if path and os.path.exists(path) and yaml is not None:
        with open(path, encoding="utf-8") as f:
            data = sub_app_block("triage", (yaml.safe_load(f) or {}).get("triage"))
        refuse_retired_dossier_dir("triage", data)
        # #309. The overlay loop below guards bools and containers, both keyed on the
        # DEFAULT's type -- so an int-defaulted field passes through unchecked, and this
        # one has three bad values a person actually writes:
        #
        #   `true`  -- the natural spelling of "yes, fetch in parallel". PyYAML gives a
        #              real bool, bool SUBCLASSES int, and `True <= 1` is True, so the
        #              run goes SEQUENTIAL: the knob the operator switched on is off,
        #              silently. Checked FIRST, before isinstance(int), for that reason.
        #              Same trap #228 documents for `dossier_settle_ms: yes`.
        #   `"4"`   -- setattr'd as a string, survives construction, then raises a bare
        #              TypeError from the engine's comparison mid-run, after the classify
        #              pass has written to the vault, naming neither key nor file. main()
        #              converts only ValueError, so that reaches the user as a traceback.
        #   `0`/`-2` -- degrade to sequential silently. There is no "off" here: 0 would
        #              be a second spelling of 1.
        #
        # `data.get(...) is not None` rather than membership, matching cv.compose_timeout:
        # the overlay loop skips None, so a valueless key must stay acceptable.
        if data.get("dossier_concurrency") is not None:
            raw = data["dossier_concurrency"]
            if (isinstance(raw, bool) or not isinstance(raw, int)
                    or not 1 <= raw <= DOSSIER_CONCURRENCY_MAX):
                raise ValueError(
                    f"triage.dossier_concurrency must be an integer from 1 to "
                    f"{DOSSIER_CONCURRENCY_MAX}, got {raw!r}. There is no 0 or false: 1 "
                    f"is one fetch in flight, which is the sequential default. Note "
                    f"`true` is a YAML boolean and bool subclasses int, so it would mean "
                    f"1 -- sequential, the exact behaviour this knob exists to change.")
        for k, v in data.items():
            if not hasattr(cfg, k) or v is None:
                continue
            # A field whose CODE DEFAULT is a bool must be given a real YAML boolean.
            # The mirror image of the root loader's lead_ttl_days check (core/config.py):
            # there the hazard is that PyYAML resolves `yes`/`on`/`true` to a real bool
            # which then passes an isinstance(int) test; here it is that a QUOTED
            # `company_resolve_fetch: "false"` is not a YAML boolean at all -- it stays
            # the string "false", which this loop would setattr verbatim and every
            # consumer reads in a boolean context, where a non-empty string is TRUE. So
            # the one spelling a user reaches for to keep a knob OFF is the spelling that
            # silently switches it ON, with nothing anywhere going red. Fail loudly at
            # construction instead, this file's house style.
            #
            # Keyed on the default's type rather than on a hardcoded field list so a bool
            # knob added later cannot quietly opt out of the check. `getattr(cfg, k)` is
            # still the code default here: a YAML mapping yields each key once, so no
            # earlier iteration of this loop has replaced it.
            if isinstance(getattr(cfg, k), bool) and not isinstance(v, bool):
                raise ValueError(
                    f"triage.{k} must be a YAML boolean (true/false), got {v!r}. Quoted, "
                    f'it is a STRING -- and "false" is truthy in Python, so the knob '
                    f"would be switched ON by the value meant to switch it off.")
            # #176, the container sibling of the bool guard above and keyed the same
            # way. These five fields are the PREFERENCE GATES: measured before this
            # existed, `target_locations: remote` loaded as a str and `classify` then
            # kept every location, byte-identical to the unconfigured abstain -- a
            # geography filter the user believes they configured doing nothing at all.
            refuse_wrong_container("triage", k, v, getattr(cfg, k))
            setattr(cfg, k, v)
    # #120: unconditional (not inside the `if path...` block above) because it must
    # run on every LOAD, whether or not a config FILE was present -- the DEFAULT
    # state (both False) has to pass trivially, and a file that sets ONLY
    # company_resolve_llm (leaving company_resolve_fetch at its own False default)
    # must still be caught. Placed after the overlay loop because it needs both
    # keys' FINAL values, and PyYAML yields a mapping's keys in file order -- a
    # check placed inside the loop would pass or fail depending on which key
    # happened to come first in the file.
    #
    # This is not what makes tier 3 SAFE -- resolve.py's tier-3 block sits after the
    # existing `if no_llm or not company_resolve_fetch or not url: return` early
    # exit, so it structurally cannot fire without the fetch regardless of this
    # check. It exists because a config that claims a feature is on while it can
    # never run is the same "declared and read by nothing" class
    # refuse_retired_dossier_dir already guards against for a retired key -- fail
    # loudly at construction, this file's house style.
    if cfg.company_resolve_llm and not cfg.company_resolve_fetch:
        raise ValueError(
            "triage.company_resolve_llm is on but triage.company_resolve_fetch is off. "
            "Tier 3 reads the page data tier 2 fetches, so on its own it can never "
            "fire: the knob would be silently inert, every blank/placeholder-company lead would "
            "stay unresolved, and the config would say otherwise. Set "
            "company_resolve_fetch: true as well, or turn company_resolve_llm off.")
    # AFTER the loop, so `audit_jsonl: ""` in a config file resolves rather than
    # escaping as the empty string the loop just set.
    cfg.audit_jsonl = resolve(env_var="TRIAGE_AUDIT", config_value=cfg.audit_jsonl,
                              kind="state", name="triage-audit.jsonl")
    # Env beats the config block for WHERE the CLI lives -- see the helper for why
    # this is applied here rather than taught to the setattr loop above.
    apply_claude_cli_env(cfg, host_attr="claude_max_host", path_attr="claude_max_path")
    return cfg
