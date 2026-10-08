"""Whether `ingest run` runs a source: the ONE predicate, and the `ingest disable` overlay it
reads beside the config.

A source runs only when all three agree: its module ships it enabled (a retired board is
registered switched off), the config's `sources.<id>.enabled` leaves it on, and the operator's
overlay (`job-sluice ingest disable`) does not name it. `cli.py` decides what `ingest run` and
`ingest list-sources` do with it, and `Sluice.setup_snapshot` decides with it which boards
setup offers a search for. It was spelled twice before, and the setup copy had left the overlay
out, so a board the user had switched off was still offered a search that would never run.

Standard library plus `core/` only, and no browser: `cli.py` imports it at module scope.
"""
import json
import os

from sluice.core.log import get_logger
from sluice.core.paths import resolve

_log = get_logger("ingest")

# Why a source does not run, in the order they are checked. A source ships off before any
# config can switch it on, so "shipped" outranks the other two.
OFF_REASONS = ("shipped", "config", "overlay")


def off_reason(src, config_enabled: bool, disabled) -> str | None:
    """Why `ingest run` would not run `src`, or None when it would. `config_enabled` is the
    config's `sources.<id>.enabled`; `disabled` is the overlay's set of ids."""
    if not getattr(src, "enabled", True):
        return "shipped"
    if not config_enabled:
        return "config"
    if src.id in disabled:
        return "overlay"
    return None


def is_enabled(src, config, disabled) -> bool:
    """`off_reason` against a loaded `Config`."""
    return off_reason(src, config.source(src.id).enabled, disabled) is None


# Resolved on each call so env overrides - and tests' monkeypatch - win; an import-time snapshot
# would be unpatchable. The health path's equivalent resolution lives solely in
# HealthStore.__init__ (sluice/core/health.py) -- see cmd_health/cmd_list_sources.
def disabled_path() -> str:
    return resolve(env_var="SLUICE_DISABLED", config_value="", kind="state",
                   name="sluice_disabled.json")


def load_disabled() -> set:
    """The operator's disabled-source ids. RAISES if the overlay exists but is unusable.

    MISSING -> nothing disabled, the ordinary state. `lexists`, not `exists`: a DANGLING
    SYMLINK is not an absent file, and treating it as one sends `cli._save_disabled` writing
    through the link.

    Anything else raises, and the raise is the point. THE CRITERION IS WHAT THE CALLER
    DOES WITH THE ANSWER, not whether it writes this file back: a caller that only
    REPORTS the answer takes `disabled_or_warn`; one that ACTS on it or WRITES it back
    takes this function. Writing back is the worst case -- a swallowed read there rebuilds
    the overlay from an empty set and destroys every decision the operator made, reporting
    success -- and it is also the only one of the three a guard can enumerate, which
    `test_every_overlay_writer_reads_through_the_raising_loader` does.

    The shape is validated for the reason `_merge_denylist` exists in track/config.py:
    `set(json.load(f))` over a dict yields its KEYS and over a string yields its
    CHARACTERS, so a malformed overlay would silently become a nonsense set of source ids
    rather than an error.
    """
    path = disabled_path()
    if not os.path.lexists(path):
        return set()
    if not os.path.exists(path):
        # Present as a link, absent as a file. Saying so beats the bare
        # "No such file or directory" the open would raise for a path we just proved
        # exists -- the sibling in seendb.py words it the same way.
        raise OSError(
            f"the disabled-sources overlay at {path} is a symlink to something that does "
            f"not exist. Fix or remove the link; removing it re-enables every source you "
            f"had turned off.")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        bad = f"got {type(data).__name__}"
    else:
        # Name the ELEMENT's type, not the container's: reporting "got list" for
        # `["reed", 1]` describes the thing that was RIGHT and hides the thing that
        # was wrong.
        offenders = [x for x in data if not isinstance(x, str)]
        bad = (f"got a list containing {type(offenders[0]).__name__}" if offenders
               else "")
    if bad:
        raise ValueError(
            f"the disabled-sources overlay at {path} must be a JSON list of source ids, "
            f"{bad}. Fix or delete it; deleting it re-enables every source you had "
            f"turned off.")
    return set(data)


def disabled_or_warn() -> set:
    """`load_disabled` for callers that only REPORT the answer: warn, and treat nothing
    as disabled.

    See `load_disabled` for the criterion. #80 newly made this reachable:
    the file used to sit in the cwd and now resolves per-system, so an upgrader's overlay
    is at the old location. `paths.resolve` warns about the move, but that notice names
    the file and not the consequence -- and this is the consequence.

    Warn rather than refuse, deliberately: a re-enabled source costs a wasted scrape and
    is fixed by disabling it again, a different order of harm from the dedup stores
    (a duplicate application, irreversible). It must not be SILENT, though.
    """
    try:
        return load_disabled()
    except (OSError, ValueError) as e:
        _log.warning(
            "could not read the disabled-sources overlay at %s (%s): treating every "
            "source as ENABLED for this run. Any source you disabled will be scraped.",
            disabled_path(), e)
        return set()
