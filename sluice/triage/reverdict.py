"""The one-shot acknowledgement behind #223 §2.1's re-verdict notice.

`triage run` stops consulting a `role_type` whose provenance is untrusted, and every
note written before #223 is untrusted by construction. On an accumulated vault that is
not one lead changing verdict -- it is a batch, all at once, on the first run after an
upgrade. `dismiss` is not in `DEFAULT_TRIAGE_STATUSES`, so a lead dismissed on the new
basis is never re-selected and the user never sees it again.

So the first run that WOULD apply it prints the affected leads and writes nothing.
`--dry-run` is not sufficient on its own, because it requires the user to know to use it.

**The acknowledgement records that a NOTICE WAS SHOWN, never merely that a run
happened.** A run with nothing to announce must not spend it: a user who later syncs an
old vault in, or configures `perm_floor_gbp` for the first time, would otherwise be
re-verdicted in silence -- which is the entire harm this exists to prevent.

**Keyed per VAULT, not per install.** The notice is a claim about one vault's
accumulated notes. A single global flag meant acknowledging on vault A silenced it for
vault B, which then re-verdicted in silence -- the same harm, through a door the first
version left open. The file maps a hash of each vault's scope to the date its notice was
shown; the date is read by nothing and exists so a human who finds the file can tell what
it is.

A marker FILE rather than a row in an existing store. The two dedup stores REFUSE when
relocated, and rightly -- an empty dedup set
re-submits every known lead. This one must do the opposite: a relocated or missing marker
means "show the notice again", which costs one skipped run and nothing else. Wrong in the
loud direction by construction, so it takes no part in the #81 relocation machinery.
"""
import hashlib
import json
import os
from datetime import date

from sluice.core.log import get_logger
from sluice.core.paths import resolve

_log = get_logger("triage.reverdict")

def _path(path: str | None = None) -> str:
    # `path or resolve(...)`, in that order, for the reason `HealthStore` and `SeenDb`
    # both state: an explicit argument must beat the environment, or every test passing
    # a tmp_path would silently retarget a developer's real state file and stay green
    # while doing it.
    #
    # NO env var and NO config key, so this file has no `~`-bearing door and needs no
    # row in tests/test_path_tilde.py's roster -- the XDG fallback is the only way it
    # resolves. `name=` is spelled as a LITERAL rather than lifted to a constant because
    # that sweep reads these call sites with `ast`, and a name it cannot read is a call
    # site it certifies nothing about; it says so, by name, rather than passing.
    return path or resolve(env_var=None, config_value="", kind="state",
                           name="role_type_reverdict_ack.json")


def _key(scope: str) -> str:
    """Which VAULT this acknowledgement is about.

    The notice is a claim about one vault's accumulated notes, so the marker has to be
    keyed on one too. A single global flag meant acknowledging on vault A silenced the
    notice for vault B, which then re-verdicted in silence -- the exact harm, reached
    through a door the first version left open.

    A hash rather than the scope itself, for the reason `dedup_key` gives: the scope
    carries the user's own directory layout, and this file lives outside the vault.

    Hashed VERBATIM (#324). The value is `Sluice._reverdict_scope`'s output, `vault:<dir>`
    -- a scope string, not a path -- and this function used to `abspath` it so that
    `./vault` and its absolute spelling shared a key. A string with no leading `/` gets the
    process cwd prepended, so the key was per (vault, cwd): a scheduled run and a hand-run
    one start in different directories, and each new cwd re-showed the notice, wrote
    nothing and exited 0. Every test passed a bare path, where `abspath` is correct, so
    none of them could see it. The directory is resolved where it is still a path, in
    `_reverdict_scope`, and nothing here interprets the scope -- so no path function
    belongs here: each would parse a string this function does not own.
    """
    return hashlib.sha256((scope or "").encode("utf-8")).hexdigest()[:16]


def acknowledged(scope: str, path: str | None = None) -> bool:
    """Has the re-verdict notice already been shown FOR THIS VAULT?

    `scope` is the store identity `Sluice._reverdict_scope` computes, compared exactly as
    given (see `_key`).

    Any unreadable or malformed marker reads as NOT acknowledged. Failing toward showing
    the notice again is the cheap direction -- it costs one skipped run -- while failing
    the other way silently re-verdicts a vault, which is unrecoverable once `dismiss`
    drops those leads out of the default selection.
    """
    target = _path(path)
    try:
        with open(target, encoding="utf-8") as f:
            shown = json.load(f)
        return isinstance(shown, dict) and _key(scope) in shown
    except (OSError, ValueError):
        return False


def acknowledge(scope: str, path: str | None = None, *, today=None) -> bool:
    """Record that the notice was shown. Returns whether it LANDED, and never raises.

    The return value is load-bearing rather than informational. The caller returns early
    -- doing nothing at all -- on the strength of "the user will see this again next
    run", and the marker is the only thing that makes the next run different from this
    one. Measured against a read-only state directory: without this signal the notice
    re-showed and `run()` returned early on every invocation, forever, so triage never
    triaged again. That is worse than the harm the notice exists to prevent, and much
    harder to diagnose -- the command exits 0 and looks like it simply had nothing to do.
    """
    target = _path(path)
    try:
        # Read-modify-write, so acknowledging vault B does not forget vault A. A
        # malformed existing file is REPLACED rather than merged into: `acknowledged`
        # already reads one as "not shown", so keeping it would strand every vault it
        # names in a permanent notice loop.
        try:
            with open(target, encoding="utf-8") as f:
                shown = json.load(f)
            if not isinstance(shown, dict):
                shown = {}
        except (OSError, ValueError):
            shown = {}
        shown[_key(scope)] = (today or date.today()).isoformat()
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(shown, f)
        return True
    except OSError as e:
        _log.warning("triage: could not record the role_type re-verdict notice at %s "
                     "(%s)", target, e)
        return False
