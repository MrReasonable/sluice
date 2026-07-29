"""Per-system path resolution (#80).

Every path sluice owns resolves through `resolve` below, in one order:

    env var  ->  config key  ->  the XDG location

That order is the repo's documented config layering (code default < YAML < env) read
from the other end, and it is stated once here rather than repeated at each call site.

`resolve` performs NO WRITES: it never creates a directory, so RESOLVING a path cannot
touch the disk; the writer that needs a parent creates it. It does read -- the
environment, and (when `legacy` is given) whether the legacy path, the resolved path and each
known companion exist -- so this is "no writes", not "no I/O". The XDG variables are read per call, never snapshotted at import,
because an import-time snapshot is unpatchable by tests.

That is a claim about `resolve` ONLY, and deliberately not about a `--dry-run` as a
whole, which does still write: `ingest run --dry-run` records per-source health, so it
creates `sluice_health.json` under the state root. Measured, not assumed. That matters
here because the legacy check below is keyed on the resolved path NOT existing, so a
writer that creates it silently disarms that path's notice from then on. It is tolerable
for health -- warn-only, and the file is rebuildable telemetry -- and it is exactly why
the two dedup stores no longer create anything on a read (`SeenDb.load`). Health's
dry-run write is left as it is on purpose: whether a dry run should record run history
is a drift-detection question, not a path question, and changing it here would be a
behaviour change smuggled into a path sweep.
"""
import os
import shlex

from sluice.core.log import get_logger

_log = get_logger("paths")

# kind -> (XDG variable, fallback relative to ~). The three XDG base directories this
# tool uses; there is no runtime dir because nothing here is a socket or a lock.
_ROOTS = {
    "config": ("XDG_CONFIG_HOME", "~/.config"),
    "state": ("XDG_STATE_HOME", "~/.local/state"),
    "cache": ("XDG_CACHE_HOME", "~/.cache"),
}

# Where each moving path lived before #80. Every one was `./<basename>`, which is
# precisely what made a `cd` silently repoint them all at once.
#
# Tabulated HERE rather than passed in from each call site, for two reasons. It gives
# the migration ONE home: a call site names only what it wants, and cannot forget the
# legacy half or misspell it into a check that never fires. And these `"./"` literals
# must not survive anywhere else under `sluice/` -- the definition-of-done grep excludes
# this module alone, so a copy left at a call site shows up as drift.
#
# The config file is deliberately absent: an unset SLUICE_CONFIG meant "no config file",
# never "./config.yaml", so there is nothing to migrate from and a `config.yaml` in
# someone's cwd is somebody else's file.
_LEGACY = {
    "seen.db": "./seen.db",
    "track-seen.db": "./track-seen.db",
    "sluice_health.json": "./sluice_health.json",
    "sluice_disabled.json": "./sluice_disabled.json",
    "triage-audit.jsonl": "./triage-audit.jsonl",
    "google_token.json": "./google_token.json",
    "dossiers": "./dossiers",
}


# Files DERIVED from a resolved path by string concatenation, and which therefore have to
# move WITH it. `core/app.py` builds track's `.lastrun` watermark and its #49 dead-letter
# store by appending to `seen_db`, so a remedy naming only the database leaves both behind
# -- and losing either is SILENT. Without the watermark, `_gmail_query` falls back to
# `gmail_lookback_days` INSTEAD of "since the last run", so on an install idle longer than
# that window every receipt in the gap goes unqueried and its lead sits in `applied`
# forever. Without the dead-letter store, `open_entries` reads empty and the whole
# un-acted-on proposal backlog is discarded. Both report as an ordinary successful run.
#
# A refusal that exists to guide a safe migration must name everything the migration has
# to carry; `tests/test_state_file_tiers.py` pins that it does.
_SIDECARS = {
    "track-seen.db": (".lastrun", ".deadletter.db"),
}


def resolve(*, env_var, config_value, kind, name, legacy=None, fatal=False) -> str:
    """Where `name` lives. `kind` is one of `_ROOTS`; an unknown one raises and lists
    the valid names rather than falling through to a default.

    `env_var` is the variable NAME to consult (or None), `config_value` the value a
    config key supplied (or "" when unset -- an empty config value abstains, exactly as
    every other preference in this codebase does).
    """
    if kind not in _ROOTS:
        raise ValueError(
            f"unknown path kind {kind!r}; valid kinds are "
            f"{', '.join(sorted(_ROOTS))}")

    explicit = (os.environ.get(env_var) if env_var else None) or config_value
    if explicit:
        # The caller named a path, so there is nothing to migrate FROM: the legacy
        # check below is deliberately unreachable here. That is what makes an
        # exported env var, a configured value, and an explicit constructor argument
        # all immune to the refusal -- without which relocating a store would refuse
        # to start for every caller that supplies its own path.
        return explicit

    var, fallback = _ROOTS[kind]
    root = os.environ.get(var) or os.path.expanduser(fallback)
    resolved = os.path.join(root, "sluice", name)

    # The table supplies the legacy path; an explicit `legacy=` overrides it, which is
    # how the tests plant a file somewhere they control instead of the real cwd. A name
    # with no entry has nothing to migrate from and skips the check entirely.
    legacy = _LEGACY.get(name) if legacy is None else legacy

    if legacy and os.path.exists(legacy) and not os.path.exists(resolved):
        # Every file the migration has to carry, and in an order that survives being
        # interrupted. Three properties, each measured:
        #
        #   `mkdir -p` first -- the refusal fires BEFORE any writer, so the destination
        #   directory does not exist yet and a bare `mv` fails with "No such file or
        #   directory", moving nothing.
        #
        #   Companions BEFORE the store. The legacy gate is `exists(legacy) and not
        #   exists(resolved)`, keyed on the STORE alone: move it first and a chain that
        #   then fails leaves the companions orphaned AND silences the only notice that
        #   names them, permanently. Moving the store last means any interruption leaves
        #   the refusal armed.
        #
        #   `shlex.quote`, because these paths come from the environment and a home
        #   directory with a space in it otherwise produces a command that does something
        #   else entirely.
        #
        # A companion is named only when it actually exists, so the remedy stays
        # copy-pasteable rather than failing on a file the user never had.
        moves = [(legacy + s, resolved + s) for s in _SIDECARS.get(name, ())
                 if os.path.exists(legacy + s)]
        moves.append((legacy, resolved))
        parent = os.path.dirname(resolved)
        # `-m 700` because this directory also holds the OAuth token, and
        # `_write_token`'s `makedirs(mode=0o700, exist_ok=True)` NO-OPS once it exists:
        # a plain `mkdir -p` here leaves it 0755 permanently. Measured.
        # `mv -n` because only the store's move is gated on the destination being
        # absent; without it a newer companion already at the destination is silently
        # overwritten by an older one.
        steps = ([f"mkdir -p -m 700 {shlex.quote(parent)}"] if parent else [])
        steps += [f"mv -n {shlex.quote(src)} {shlex.quote(dst)}" for src, dst in moves]
        remedy = " && ".join(steps)
        msg = (f"{name} now lives at {resolved}, but a file remains at {legacy}. "
               f"sluice never moves your data -- run:  {remedy}")
        if len(moves) > 1:
            msg += (f"   ({len(moves) - 1} companion file(s) must move with it: leaving "
                    f"them behind silently loses the last-run watermark and the "
                    f"un-acted-on proposal backlog.)")
        linked = [src for src, _ in moves if os.path.islink(src)]
        if linked:
            # `mv` moves the LINK, not its target, so a relative one lands dangling and
            # the next run reads it as "no history yet" rather than refusing again.
            msg += f"   (symlinks -- copy the targets instead: {', '.join(linked)})"
        if fatal:
            # Only the two dedup stores. Continuing with an empty dedup set re-creates
            # every lead a human merged away (#81 -- `_resolve_path` never consults
            # `_merged/`), which can mean a second application under their name. That
            # is irreversible and reports as ordinary `created: N` activity, so refuse
            # rather than warn.
            raise RuntimeError(msg)
        _log.warning(msg)

    return resolved


def config_file() -> str:
    """Where the config file lives: `$SLUICE_CONFIG`, else `<config root>/config.yaml`.

    A function rather than five copies of the same `resolve` call, because all FIVE
    loaders have to agree: each reads its own block of ONE file, so converting four and
    missing the fifth -- or spelling the name differently in one -- gives a config that
    half-loads with nothing raising anywhere. Single-siting makes that impossible rather
    than merely tested for.

    No `config_value`: this resolves the config file itself, so a config key naming it
    could only be read from a file already found. No `legacy` either -- there has never
    been a default config path to migrate from; an unset `SLUICE_CONFIG` meant no config
    file at all, and now means this one if it exists. That is the sweep's only behaviour
    change.
    """
    return resolve(env_var="SLUICE_CONFIG", config_value="", kind="config",
                   name="config.yaml")
