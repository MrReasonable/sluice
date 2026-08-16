"""Track configuration: code defaults overlaid by the `track:` block of sluice.yaml.
Every field has a sane default so track runs with no config file."""
import os
from dataclasses import dataclass, field

from sluice.core.paths import config_file, resolve

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

_ATS_RELAY_DOMAINS = {
    "greenhouse.io": "greenhouse", "ashbyhq.com": "ashby", "lever.co": "lever",
    "workable.com": "workable", "icims.com": "icims", "teamtailor.com": "teamtailor",
    "myworkday.com": "workday", "smartrecruiters.com": "smartrecruiters",
}

# The job boards sluice itself scrapes, keyed by their REGISTRABLE domain and valued by
# the `sluice/ingest/sources/` plugin id. Together with _ATS_RELAY_DOMAINS these are the
# hosts shared by MANY employers, which `track/receipt.py` refuses to read as proof of
# any one of them: a lead's `url` is the URL it was INGESTED from, so for a board-sourced
# lead that host is the board, not the employer. Registrable form on purpose -- the
# matcher suffix-matches, so "indeed.com" covers "uk.indeed.com" and
# "welcometothejungle.com" covers "app.welcometothejungle.com", and one key per board
# survives a source switching subdomains. google.com is here deliberately: sluice's
# google source ingests Google-jobs aggregator links, and google.com is about as far
# from employer-identifying as a host gets.
_JOB_BOARD_DOMAINS = {
    "80000hours.org": "eighty_k", "b-work.io": "bwork", "bayt.com": "bayt",
    "cord.com": "cord", "cwjobs.co.uk": "cwjobs", "escapethecity.org": "escape_city",
    "google.com": "google", "gulftalent.com": "gulftalent", "hackajob.co": "hackajob",
    "hired.com": "hired", "indeed.com": "indeed", "jobserve.com": "jobserve",
    "linkedin.com": "linkedin", "naukrigulf.com": "naukrigulf", "reed.co.uk": "reed",
    "remoteok.com": "remoteok", "theorg.com": "theorg", "totaljobs.com": "totaljobs",
    "wellfound.com": "wellfound", "welcometothejungle.com": "wttj",
    "weworkremotely.com": "weworkremotely", "workinstartups.com": "workinstartups",
}

# Config keys whose shipped default is a SAFETY DENYLIST: a user block MERGES over the
# default instead of replacing it. Replacing is the wrong direction of failure here --
# adding one in-house ATS would silently drop the other eight shipped entries and make
# the proof tier MORE permissive, the opposite of what the person adding a relay wants
# (and the opposite of what sluice.yaml.example's "add your own" promises). Deliberately
# scoped to these two dict keys: every scalar knob keeps plain last-wins overlay.
_MERGED_DENYLISTS = {"ats_relay_domains": _ATS_RELAY_DOMAINS,
                     "job_board_domains": _JOB_BOARD_DOMAINS}


def _merge_denylist(key: str, value):
    """Validate a denylist override, then merge it OVER the shipped default.

    The `isinstance(v, dict)` test this replaces was a silent fall-through: a non-mapping
    override skipped the merge and took the plain-setattr branch instead, REPLACING the
    denylist. `ats_relay_domains: []` therefore emptied a safety list outright and
    `ats_relay_domains: 'oops'` turned it into a four-"entry" string whose keys are the
    characters o/p/s. Both WIDEN the proof tier -- a host that no longer reads as
    multi-tenant can prove which employer a receipt concerns -- which is the exact failure
    direction the merge exists to close, and the same class of bug (a user override
    replacing a shipped default) this branch already fixed once.

    So: fail loudly at construction, naming the key and the valid shape, rather than
    retaining-or-emptying either way. Retaining silently would be no better -- the user's
    entry would vanish and their in-house ATS would go unrecognised with no signal.

    Only the KEYS are load-bearing: `receipt._suffix_match` iterates them and never reads
    a value (labels are documentation for whoever opens the config). A non-str key is
    therefore not merely untidy -- it raises TypeError out of `host.endswith("." + k)` at
    match time, inside engine.run's per-message `except`, which swallows it WITHOUT
    adding the id to `seen`: a permanently re-failing poison message. Values stay
    unconstrained on purpose; nothing reads them, so rejecting a non-string label would
    be a gratuitous refusal."""
    valid = f"{key}:\n    host.example.invalid: your-label"
    if not isinstance(value, dict):
        raise ValueError(f"track.{key} must be a mapping of host -> label, "
                         f"got {type(value).__name__}. Valid form:\n  {valid}")
    for k in value:
        # Report only the offending KEY, never the whole user block: a config file is the
        # one place a user's real job-hunt hosts legitimately live, and an exception
        # message travels further (logs, bug reports) than the file does.
        if not (isinstance(k, str) and k.strip()):
            raise ValueError(f"track.{key} keys must be non-empty host strings, "
                             f"got {k!r} ({type(k).__name__}). Valid form:\n  {valid}")
    # User entries win on a key collision (relabelling a shipped domain is fine), but no
    # user block can DROP a shipped safety entry.
    return {**_MERGED_DENYLISTS[key], **value}


@dataclass
class TrackConfig:
    # Blank, not a path (#80), and load_track_config fills both in. Resolution is
    # `env or config key or XDG`, so a non-empty default is ALWAYS truthy: it
    # short-circuits the chain before the XDG location is reached, the field never
    # moves, and every test stays green while it does not. Neither honours an env var
    # -- there has never been one for either -- so the config key is the only override.
    token_path: str = ""
    # The worst of the cwd-relative paths. app.py derives THREE things from it: the
    # `.lastrun` watermark, the seen-message set, and the #49 dead-letter store. Run
    # track from another directory and the entire backlog of un-acted-on proposals
    # silently disappears, reported as an ordinary run.
    seen_db: str = ""
    gmail_lookback_days: int = 2
    gmail_extra_query: str = ""
    calendar_lookahead_days: int = 45
    calendar_match_minutes: int = 30          # start-proximity window for dedup
    # Hard TOTAL caps on what one run will read, across pages. Configurable because they are
    # deployment-specific -- mailbox volume and calendar density -- and because the calendar
    # one is CORRECTNESS-bearing since #137: hitting it makes `sync_event` answer
    # `unresolved`, which means the interview is not booked until a human intervenes.
    #
    # `calendar_max_events` is also COUPLED to a key that is already configurable: the window
    # is 2 * calendar_lookahead_days with `singleEvents=True` expanding recurrences, so
    # doubling the lookahead doubles the event count against this bound. Leaving it a literal
    # meant the remedy the digest offered ("reduce calendar_lookahead_days") traded one loss
    # class for #146's, with no way to just raise the ceiling.
    #
    # Both default to the pre-existing literals, so an install that sets neither behaves
    # exactly as before.
    gmail_max_messages: int = 500
    calendar_max_events: int = 2500
    # The zone to assume for a DTSTART that carries no usable one -- legal RFC 5545 floating
    # time, a date-only VALUE=DATE, or a TZID this host cannot resolve. Such an invite states
    # a wall-clock and no instant, so SOMETHING has to be assumed to book it at all, and the
    # assumption is only ever a guess. An IANA key like `Europe/London`.
    #
    # Whenever that guess is WRITTEN -- a `created` or `updated` calendar outcome, dry runs
    # included -- calendar_sync warns and the run digest counts it. A `present` outcome is
    # deliberately silent: nothing was written, so there is no entry whose hour could be
    # wrong, and warning there would train the reader to ignore the line.
    #
    # The shipped default is UTC: neutral, correct for nobody in particular, and identical to
    # the behaviour before this key existed. Set it to your own zone and the guess becomes
    # right for the invites you actually receive -- a floating invite in your inbox is far
    # more likely to be in your local time than in UTC. An unresolvable value warns once and
    # falls back to UTC rather than raising, so a typo cannot start dropping messages.
    calendar_assumed_timezone: str = "UTC"
    # Which backend fills each role. Track had no selectors while its backend was
    # hardcoded; it needs them now that construction is config-driven, and matches
    # the triage/cv defaults.
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    claude_max_model: str = "claude-sonnet-4-5"
    claude_max_effort: str = "medium"
    # Host + claude binary path for the ClaudeMaxBackend this sub-app builds.
    # Empty host runs claude_max_path locally; set a host to shell out over ssh.
    claude_max_host: str = ""
    claude_max_path: str = "claude"
    cheap_model: str = "deepseek-v4-flash"
    auto_status_min: float = 0.75             # min confidence to auto-advance a scheduling/offer signal
    auto_reject_min: float = 0.9              # stricter bar to auto-reject (F4)
    auto_apply_min: float = 0.75              # min receipt-classification confidence to auto-advance shortlist->applied on a domain-PROOF match
    ats_relay_domains: dict = field(default_factory=lambda: dict(_ATS_RELAY_DOMAINS))
    job_board_domains: dict = field(default_factory=lambda: dict(_JOB_BOARD_DOMAINS))


# Integer keys where a value of 0, a negative, or a YAML boolean silently disables the thing
# the key controls. NOT "introduced by making the caps configurable", which an earlier version
# of this comment claimed: `calendar_lookahead_days: no` -> `False` -> `timedelta(days=0)` is a
# ZERO-LENGTH search window, so `_find_ours` finds nothing, every interview is double-booked
# and every cancellation left in place. Same one-word typo, same guard, and it predates this
# PR entirely -- so the guard covers the pre-existing keys too rather than only the new ones.
_POSITIVE_INT_KEYS = ("gmail_max_messages", "calendar_max_events",
                      "calendar_lookahead_days", "gmail_lookback_days",
                      "calendar_match_minutes")


def _positive_int(key: str, value):
    """A cap must be a real integer >= 1.

    `bool` FIRST and separately: `bool` subclasses `int`, and PyYAML resolves `no`/`off`/
    `false` to `False`, so `gmail_max_messages: no` passed an `isinstance(int)` test and then
    made `min(value, 500)` evaluate to 0 -- a one-word typo that stops track reading any mail
    at all, reported as an ordinary empty run. Measured, not theorised.

    Raises rather than clamping: a cap the user wrote and we silently replaced is the same
    class of lie as the value we are rejecting.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"track.{key} must be a positive integer, got {value!r} "
            f"({type(value).__name__}). Note YAML reads bare no/off/false as a BOOLEAN.")
    if value < 1:
        raise ValueError(
            f"track.{key} must be >= 1, got {value}. A cap of {value} makes every run read "
            f"nothing, which is indistinguishable from an empty mailbox.")
    return value


def load_track_config(path: str | None = None, *,
                      refuse_relocated_seen_db: bool = False) -> TrackConfig:
    cfg = TrackConfig()
    path = path or config_file()
    # An INVERTED guard rather than the early `return cfg` this replaced (#80). The
    # resolution below has to run on every path out of this function: the no-config-file
    # case is precisely what a fresh install gets, and an early return would leave it
    # holding the blank defaults -- `""` reaching `os.makedirs(os.path.dirname(""))` and
    # `deadletter_path("")`.
    if path and os.path.exists(path) and yaml is not None:
        with open(path, encoding="utf-8") as f:
            data = (yaml.safe_load(f) or {}).get("track") or {}
        for k, v in data.items():
            if not (hasattr(cfg, k) and v is not None):
                continue
            if k in _POSITIVE_INT_KEYS:
                setattr(cfg, k, _positive_int(k, v))
            elif k in _MERGED_DENYLISTS:
                # Merge, never replace -- see _MERGED_DENYLISTS. No `isinstance` test
                # guarding the branch: a non-mapping value must RAISE, not fall through
                # to the plain setattr below, which would replace the safety denylist
                # with whatever the user wrote (see _merge_denylist).
                setattr(cfg, k, _merge_denylist(k, v))
            else:
                setattr(cfg, k, v)
    # AFTER the loop, so a user who writes `seen_db: ""` gets a resolved path rather than
    # the empty string the loop just set (`v is not None` admits it).
    cfg.token_path = resolve(env_var=None, config_value=cfg.token_path,
                             kind="state", name="google_token.json")
    # `refuse_relocated_seen_db` defaults OFF, and doctor() is why: it calls this loader,
    # and a diagnostic that refuses to start is the opposite of a diagnostic -- the
    # relocated file is exactly what someone runs doctor to be told about. Only
    # Sluice.track/track_confirm/track_dismiss, which read or write the dedup state,
    # ask for the refusal.
    #
    # A flag on the resolve call rather than a separate checking helper, deliberately.
    # The helper would need to re-derive whether this value was CONFIGURED or merely
    # resolved -- a second resolution site, and the property that makes an explicit
    # path immune (`env or config` short-circuits before the legacy check) would have
    # to be reimplemented rather than inherited. Same reasoning as `update_fields`'
    # `require_status`: a parameter on the existing function, not a second function.
    cfg.seen_db = resolve(env_var=None, config_value=cfg.seen_db,
                          kind="state", name="track-seen.db",
                          fatal=refuse_relocated_seen_db)
    return cfg
