"""Per-system path resolution (#80).

Every path sluice owns resolves through `resolve` below, in one order:

    env var  ->  config key  ->  the XDG location

That order is the repo's documented config layering (code default < YAML < env) read
from the other end, and it is stated once here rather than repeated at each call site.

`resolve` performs NO WRITES: it never creates a directory, so RESOLVING a path cannot
touch the disk; the writer that needs a parent creates it. It does read -- the
environment, and (when `legacy` is given) whether the legacy path, the resolved path and
each known companion exist -- so this is "no writes", not "no I/O". The XDG variables are read per call, never snapshotted at import,
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

NORMALISATION, stated once here because it was four separate decisions with no shared
home and two of them read as contradicting each other:

    expanduser at INGRESS -- wherever a path first arrives from outside. FIVE sites, and
    `tests/test_path_tilde.py` enumerates them from the source rather than trusting this
    list, because the first version of this paragraph said four and was wrong the day it
    was written: this module's explicit branch and its XDG fallback, `Vault.__init__`,
    `onboard/questions.py`, and `cli.py` (both its `--vault`-versus-`$VAULT_DIR`
    comparison and the preset it hands `sluice init`).
    abspath ONLY where the value outlives the cwd it was read in -- either written down
    or compared. `questions.py` and `cli.py`'s preset write the answer into a config
    file; `cli.py`'s comparison needs two spellings to be judged equal. Neither is true
    of a path this module returns, so it does not abspath, and a relative explicit value
    is handed back exactly as the caller wrote it.
    At CONSUMPTION, neither -- with one exception that is not really one: a path being
    turned into a `file://` URI must be absolute or its first segment becomes the URI
    authority, so `existing_db_uri` joins the cwd on there. That changes the URI, never
    the path any caller sees, which is why it does not break the rule above. It JOINS
    rather than `abspath`s, because tidying `..` lexically is how it would start naming a
    different file from the writer.

`vault.py`'s "No abspath -- a relative vault is legitimate" and `questions.py`'s
"Absolute, always" look opposed and are not: the first is a path used in place, the
second a path written down for later. That is the whole distinction.
"""
import os
import shlex
import urllib.parse

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


def broken_ancestor(path: str) -> str | None:
    """The nearest ancestor that exists as a NAME but does not resolve, else None.

    A dangling link ON THE WAY to a store makes `os.lstat(store)` raise ENOENT -- the same
    exception a genuinely-absent store raises -- so without this a relocated store reads as
    "first run" and the caller proceeds with nothing.

    A MISSING ancestor is not broken: `<state>/sluice/` legitimately does not exist before
    anything is written, so walk past those and judge the first one that exists.
    """
    # Starts at the PARENT, which is equivalent to starting at `path` only because every
    # caller enters from inside an `except FileNotFoundError` arm on `os.lstat(path)` --
    # `path` itself is already known not to lstat. Measured: with a dangling store this
    # returns None where starting at `path` returns the store itself, so a caller that has
    # not made that check would get a different answer with nothing going red.
    cur = os.path.dirname(path)
    while cur:
        try:
            os.lstat(cur)                     # exists as a name?
        except FileNotFoundError:
            parent = os.path.dirname(cur)
            if parent == cur:
                # Termination insurance, not a reachable case: `/` always lstats, so the
                # loop exits above before `dirname` can reach its own fixed point.
                # Deleting this line is measurably green -- it is here so the walk cannot
                # spin on a path shape nobody has thought of, not because one is known.
                return None
            cur = parent
            continue
        try:
            os.stat(cur)                      # ...and does it resolve?
        except FileNotFoundError:
            return cur
        return None
    return None


def absent(path: str, *, what: str, why: str = "") -> bool:
    """True if `path` genuinely does not exist yet; RAISES if it is unreachable.

    Lives HERE, in one copy, because it was written twice and diverged twice. `SeenDb` and
    `DeadLetterDb` ask the same question of the same kind of file, and each fix landed on
    one store and missed the other: the `lexists`->`lstat` EACCES fix, and then the
    dangling-ANCESTOR guard. Both misses had the same shape -- a store read as absent, the
    caller proceeded with an empty set, and nothing was said. `what` and `why` supply only
    WORDING, so each store still says which state this is and what refusing costs --
    detection is shared, the message is not.

    `lstat`/`stat`, NOT `os.path.lexists`/`exists`: those return False on ANY OSError, so a
    store under a directory the user cannot traverse read as "absent". Only
    FileNotFoundError means absent; EACCES and friends propagate.
    """
    try:
        os.lstat(path)
    except FileNotFoundError:
        broken = broken_ancestor(path)
        if broken is not None:
            raise FileNotFoundError(
                f"{what} at {path} sits under {broken}, a symlink to something that does "
                f"not exist. {why} Fix or remove the link.".replace("  ", " ")) from None
        return True
    try:
        os.stat(path)          # the name exists; does it resolve?
    except FileNotFoundError:
        raise FileNotFoundError(
            f"{what} at {path} is a symlink to something that does not exist. {why} Fix "
            f"or remove the link.".replace("  ", " ")) from None
    return False


def existing_db_uri(path: str) -> str:
    """A `sqlite3.connect(..., uri=True)` target that CANNOT create the file.

    `sqlite3.connect` creates a 0-byte file merely by opening one, and that file is enough
    to disarm this module's relocation notice permanently -- the check below is keyed on
    the resolved path NOT existing. A caller that has already established the file exists
    still races: the check and the open are two syscalls. `mode=rw` turns that race into a
    loud error instead of a silent creation, and SQLite falls back to read-only for a 0444
    store, so reading one is unaffected.

    `file://` with an EMPTY AUTHORITY, not a bare `file:`. A leading `//` is a legal POSIX
    path and env vars are passed through unnormalised, so `file:` + `//var/...` parses
    `var` as a URI authority and raises `invalid uri authority` -- breaking a store that
    opened fine before a URI was involved. `quote` so a real `?`, `#`, `%` or space cannot
    silently address a different file.

    ABSOLUTE before quoting, for the same reason from the other direction: an authority is
    whatever follows `//`, so a RELATIVE path puts its first segment there. Measured, every
    non-absolute spelling failed identically -- `relative/seen.db` -> `invalid uri
    authority: relative`, `./x` -> `: .`, `../x` -> `: ..`, `~/x` -> `: ~`. The write path
    is a plain `sqlite3.connect(self.path)` and never had the problem, so a `SEEN_DB` the
    tool wrote happily on one run became unreadable on the next, with a message naming
    neither the file nor a remedy. `abspath` and not `expanduser`: `absent()` above and
    `save()` both take the path as given, so a `~` is a literal directory name to them and
    must stay one here -- expanding only here would open a DIFFERENT file from the one the
    caller just checked, trading a loud failure for a silent disagreement.

    `join(getcwd(), ...)` and NOT `os.path.abspath`, which is `normpath(join(...))` and
    collapses `..` LEXICALLY. Every other operation on this same value lets the OS resolve
    it -- `absent()`'s `lstat`, `save()`'s `sqlite3.connect(self.path)` -- and after a
    symlinked component the two answers differ. Measured with `abspath` here and a store
    at `link/../real/seen.db`: the writer created `<tmp>/real/seen.db` while the URI
    addressed `<tmp>/cwd/real/seen.db`, so the read either raised on a store `absent()`
    had just called present or, with anything sitting at the lexical path, returned an
    EMPTY dedup set -- the #81 harm, reintroduced by the fix for it. `core/vault.py` had
    already chosen `realpath` over `abspath` for this reason. Joining leaves the `..` in
    place for the kernel to resolve, which is the whole point: the job here is to make the
    path ABSOLUTE, not to tidy it.

    A leading `//` survives the join (POSIX leaves it implementation-defined), so the
    empty-authority property above is untouched; `tests/test_paths.py` pins that, and pins
    that the URI opens the same file the writer used.
    """
    return ("file://" + urllib.parse.quote(os.path.join(os.getcwd(), path))
            + "?mode=rw")


def _something_is_there(path: str) -> bool:
    """True unless `path` is definitively absent. Ties break towards YES.

    NOT `os.path.lexists`, for the reason `absent` above spells out at length: it returns
    False on ANY OSError, so a file under a directory the user cannot traverse reads as
    "nothing there". Measured with an abandoned store under a 0o000 parent -- `lexists`
    said False and the notice below stayed silent, which is the same fail-open shape that
    reached two dedup stores before `absent` was single-sited.

    It does not RAISE the way `absent` does, though, and the difference is the caller's
    stake. `absent` guards a read whose wrong answer is an empty dedup set, so it stops the
    run; this guards a WARNING, where a wrong answer costs either a spurious line of output
    or a missed one. Those are not equal, so the tie goes to speaking, and an unreadable
    parent is reported rather than either swallowed or turned into a crash.
    """
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True          # cannot tell -- say so rather than assume nothing is there
    return True


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
        #
        # EXPANDED, like the fallback two paragraphs down. Returning this term verbatim
        # while expanding the other made one resolver answer two ways, and the literal
        # half was silent in every direction that matters. Measured with
        # `SEEN_DB=~/state/seen.db` against a real two-row store: `SeenDb.load` read an
        # EMPTY dedup set -- indistinguishable from a first run, and the #81 harm, since
        # every already-known lead then reads as unseen and is re-submitted to the write
        # path -- and `save` then created a literal `~` directory under the CWD, the
        # exact class of path #80 exists to remove. Nothing was said at either step,
        # because the short-circuit above skips the one check that speaks. `Vault` has
        # expanded at construction all along, naming the same threat model (an env var
        # set in a config file, a systemd unit, a Docker env line), so this makes the
        # explicit door agree with the two places that already got it right.
        #
        # Expansion does NOT make the result absolute, and an earlier draft of this
        # comment claimed it did. `expanduser` no-ops on a relative path and on
        # `~unknownuser/...`, so a relative explicit value stays relative and is returned
        # as given -- honouring what the caller asked for. Normalising it into an absolute
        # path would be a behaviour change smuggled into a bug fix, and it is not needed:
        # the one place non-absoluteness actually broke something was the SQLite URI, and
        # `existing_db_uri` absolutises there, at the point of use, where it can do so
        # without changing what any caller sees. (Two reviewers caught the false claim
        # independently; every non-absolute spelling reproduced the same failure the
        # tilde one did, so the sentence had been certifying a live bug as a design.)
        expanded = os.path.expanduser(explicit)
        # Expanding is itself a relocation for anyone who ran the version that did not,
        # so it cannot be done silently in the one module whose doctrine is that a store
        # never moves without saying so.
        #
        # WARNED, not refused, and the reason is NOT that the abandoned location holds
        # little -- an earlier draft said "at most one run's writes", which is a property
        # of `existing_db_uri` and so true only of the two sqlite stores. Measured on
        # `sluice_health.json`: a literal `./~/state/` file read and wrote across three
        # consecutive runs and accumulated all three. The reason is that refusing is not
        # available here and should not be: `fatal` is consulted only in the legacy branch,
        # which naming a path deliberately short-circuits, so a refusal would have to
        # re-open the door that keeps every caller supplying its own path immune. Nothing
        # in this case is irreversible either -- the state is still on disk, at a path this
        # message names. The `_LEGACY` machinery cannot cover it: its paths are
        # cwd-relative literals keyed on `name`, and the abandoned location here is
        # whatever the unexpanded value spelled.
        # MOVE, never "remove". An earlier draft ended "check it and remove it by hand",
        # and for `seen.db`/`track-seen.db` that abandoned file IS the dedup history --
        # every run of the broken version wrote to it, and the expanded location is empty.
        # A user who followed that advice would start the next run from an empty dedup set:
        # the #81 harm, delivered as the tool's own instruction. `_LEGACY` twenty lines
        # below hands out a `mv` chain for exactly this reason, so this says the same
        # thing, `shlex.quote`d because these paths come from the environment and a home
        # directory with a space in it otherwise produces a command that does something
        # else. `[ ! -e ]` first, for the same reason it guards that chain: a destination
        # that already exists must stop the command rather than be moved onto.
        if expanded != explicit and _something_is_there(explicit):
            _log.warning(
                "%s resolves to %s, but %s also exists relative to the current directory. "
                "An earlier sluice took that literally and may have written state there -- "
                "for a dedup database that file is your whole history, so do not delete "
                "it. sluice never moves your data; if the new location is empty, run:  "
                "[ ! -e %s ] && mv %s %s",
                name, expanded, explicit,
                shlex.quote(expanded), shlex.quote(explicit), shlex.quote(expanded))
        return expanded

    var, fallback = _ROOTS[kind]
    # A RELATIVE value is ignored, which the XDG base-directory spec requires and which
    # this feature exists to enforce: `XDG_STATE_HOME=relative/state` otherwise resolved
    # to `relative/state/sluice/seen.db`, a cwd-relative store -- the exact class of path
    # #80 removes, reintroduced through the very variable meant to remove it. Measured.
    # It is ignored rather than rejected because a bad value in the environment must not
    # stop a user running sluice at all, and the fallback is always correct.
    root = os.environ.get(var) or ""
    if root and not os.path.isabs(root):
        # SAID OUT LOUD, which is the whole point. Ignoring it silently relocates the
        # user's store: measured, `XDG_STATE_HOME=relative/state` with a real two-row
        # `seen.db` under `<cwd>/relative/state/sluice/` resolved to the XDG default,
        # loaded an EMPTY dedup set, printed nothing and exited 0 -- the #81 harm, in the
        # one module whose doctrine is that a store never moves silently. `_LEGACY` cannot
        # cover it either, since the abandoned location is wherever the user last ran
        # from, so neither the warn tier nor the refusal can see it.
        _log.warning(
            "%s is %r, a relative path. The XDG base-directory spec requires a relative "
            "base to be ignored, so sluice is using %s instead -- if you have state under "
            "the relative location, move it or set %s to an absolute path.",
            var, root, os.path.expanduser(fallback), var)
    if not os.path.isabs(root):
        root = os.path.expanduser(fallback)
    resolved = os.path.join(root, "sluice", name)

    # The table supplies the legacy path; an explicit `legacy=` overrides it, which is
    # how the tests plant a file somewhere they control instead of the real cwd. A name
    # with no entry has nothing to migrate from and skips the check entirely.
    legacy = _LEGACY.get(name) if legacy is None else legacy

    # `lexists` on the LEGACY side, `exists` on the destination, and the asymmetry is
    # deliberate. `exists` follows the link and calls a broken one absent: measured, a
    # legacy `seen.db` that is a dangling symlink skipped the refusal entirely and the run
    # proceeded with an empty dedup set -- the #81 harm the refusal exists to prevent.
    #
    # The destination stays `exists` because the two sides answer different questions.
    # Here it asks "has the migration already happened", and a DANGLING destination means
    # it has not: `lexists` there only SUPPRESSES the notice, and protects nothing, since
    # `[ ! -L DST ]` in the remedy below is what actually stops the move. Measured with a
    # real legacy file and a broken destination link: `lexists` returned silently and left
    # the store where it was, while `exists` refuses and names both paths.
    # EVERY FILE IS JUDGED ON ITS OWN, store and companions alike. Two cells went silent
    # before this, and the second was reached by a user doing exactly what the notice told
    # them to. Keyed on the store alone, an orphaned `.deadletter.db` whose `track-seen.db`
    # had gone resolved quietly. Keying the companions on the STORE's destination instead
    # -- `(store or companions) and not exists(resolved)` -- fixed only half of it: someone
    # who read the notice and hand-moved just the store left `.lastrun` and `.deadletter.db`
    # behind, the destination store now existed, and the whole clause went false again.
    # Both times `track run` then read the new location and printed `open=0`, with #49's
    # backlog sitting at the old path and nothing said anywhere.
    #
    # A per-file test is also the only one that stays true as `_SIDECARS` grows: a
    # companion needing to move has nothing to do with whether the STORE still does.
    # TWO questions, and conflating them broke the halt-on-collision property. What the
    # remedy must MOVE is every companion present at the legacy path, including one whose
    # destination is already occupied -- its `[ ! -e DST ]` step is what stops the chain
    # BEFORE the store moves, leaving the refusal armed. Drop it from the list and the
    # chain runs clean, the store lands beside a foreign watermark, and the refusal is
    # disarmed for good. What decides whether to SPEAK is the narrower set: a companion
    # that is not yet at the destination.
    present = [s for s in _SIDECARS.get(name, ()) if legacy and os.path.lexists(legacy + s)]
    stranded = [s for s in present if not os.path.exists(resolved + s)]
    store_stranded = bool(legacy) and os.path.lexists(legacy) and not os.path.exists(resolved)
    if store_stranded or stranded:
        # Every file the migration has to carry, and in an order that survives being
        # interrupted. Three properties, each measured:
        #
        #   `mkdir -p` first -- the refusal fires BEFORE any writer, so the destination
        #   directory does not exist yet and a bare `mv` fails with "No such file or
        #   directory", moving nothing.
        #
        #   Companions BEFORE the store: move the store first and a chain that then fails
        #   leaves the companions orphaned, and -- before the gate above also counted them
        #   -- silenced the only notice that names them, permanently. Moving the store
        #   last means any interruption leaves the refusal armed.
        #
        #   `shlex.quote`, because these paths come from the environment and a home
        #   directory with a space in it otherwise produces a command that does something
        #   else entirely.
        #
        # A companion is named only when it actually exists, so the remedy stays
        # copy-pasteable rather than failing on a file the user never had. The STORE gets
        # the same treatment, because the gate above now also fires for an orphaned
        # companion -- an unconditional `mv` of an absent store would hand the user a
        # command whose first step fails.
        moves = [(legacy + s, resolved + s) for s in present]
        if store_stranded:
            moves.append((legacy, resolved))
        parent = os.path.dirname(resolved)
        # `chmod` SEPARATELY, not `mkdir -m`: the mode flag applies only to directories
        # mkdir creates, and by the time a user runs this the directory usually exists --
        # every state writer creates it at 0755 on the way past. It also holds the OAuth
        # token, whose `makedirs(mode=0o700, exist_ok=True)` no-ops for the same reason,
        # so without the explicit chmod the credential's parent stays 0755 permanently.
        #
        # `[ ! -e DST ] && mv` rather than `mv -n`: `-n` SKIPS an existing destination and
        # exits 0, so the `&&` chain carries on and moves the store anyway -- leaving an
        # old store beside a foreign watermark, with `lexists(legacy)` now false and the
        # refusal disarmed for good. A skip is not an interruption. The test returns
        # non-zero, so the chain stops BEFORE the store move and the next run says so
        # again.
        #
        # `-e` AND `-L`, because `-e` follows the link: a DANGLING destination link is
        # `! -e` and would be moved onto, which both destroys the link and leaves the
        # store somewhere nobody looks. `-L` is the only test that sees it.
        steps = ([f"mkdir -p {shlex.quote(parent)} && chmod 700 {shlex.quote(parent)}"]
                 if parent else [])
        steps += [f"[ ! -e {shlex.quote(dst)} ] && [ ! -L {shlex.quote(dst)} ] && "
                  f"mv {shlex.quote(src)} {shlex.quote(dst)}"
                  for src, dst in moves]
        remedy = " && ".join(steps)
        # Counted from `companions`, never `len(moves) - 1`: that arithmetic assumed the
        # store was always one of the moves, and a companion can now open the gate alone,
        # so it would report one fewer than there are.
        #
        # Branched on `store_stranded`, not on whether the store EXISTS at the legacy
        # path. The two differ in the cell this message is most likely to be read in --
        # store already moved, companions left behind -- where the old test said "a file
        # remains at {legacy}" of a store that had been dealt with, and sent the reader
        # looking for the wrong file.
        if store_stranded:
            msg = (f"{name} now lives at {resolved}, but a file remains at {legacy}. "
                   f"sluice never moves your data -- run:  {remedy}")
            if present:
                msg += (f"   ({len(present)} companion file(s) must move with it: "
                        f"leaving them behind silently loses the last-run watermark and "
                        f"the un-acted-on proposal backlog.)")
        else:
            # Says only what it has checked. Whether the store was moved by hand or never
            # existed is not knowable here, and a companion left behind is the point
            # either way. "still hold state" is dropped for a dangling one -- it holds
            # nothing, and the later clause already says so.
            msg = (f"{name}: {len(stranded)} companion file(s) remain beside {legacy} "
                   f"and are not at {resolved} -- these carry the last-run watermark and "
                   f"the un-acted-on proposal backlog, so leaving them loses both. "
                   f"sluice never moves your data -- run:  {remedy}")
        # The `[ ! -e ]` guards stop the chain without printing anything, so a user who
        # gets a bare non-zero exit needs to be told what it means. Said here rather than
        # wrapped into the shell: an echo per step turns a copy-pasteable line into a
        # thicket of quoting, which is its own hazard.
        msg += ("   (if any destination already exists the command stops without moving "
                "anything further -- deal with that file by hand, then re-run.)")
        # SPLIT by whether the link still resolves, because the advice differs and one of
        # the two does not work: `cp -L` on a dangling link exits 1 (measured), so telling
        # someone to run it on a broken link sends them in a circle.
        linked = [src for src, _ in moves if os.path.islink(src)]
        resolvable = [s for s in linked if os.path.exists(s)]
        dangling = [s for s in linked if not os.path.exists(s)]
        if resolvable:
            # `mv` moves the LINK, not its target, so a relative one lands dangling.
            # Every reader that matters now RAISES on that (`lexists` in both dedup
            # loaders and in the dead-letter store), except `_load_lastrun`, which
            # swallows OSError by design -- so a dangling `.lastrun` reads as "no prior
            # run" and silently narrows the receipt window.
            msg += (f"   (symlinked -- copy the targets rather than the links, e.g. "
                    f"cp -L: {', '.join(resolvable)})")
        if dangling:
            msg += (f"   (already broken -- these point at something that no longer "
                    f"exists, so there is nothing to copy: find the real file or remove "
                    f"the link: {', '.join(dangling)})")
        if fatal:
            # Only the two dedup stores. An empty dedup set makes every already-known
            # lead read as unseen, so it is silently re-submitted to the write path.
            # `Vault._resolve_path` DOES now consult `_merged/` (#81) and recognises a
            # merged-away lead by name, so the common case self-heals rather than
            # resurrecting -- but the probe is name-keyed, and a re-scrape whose title
            # has drifted past every name candidate still slips past it (#23 territory,
            # out of scope here) and is CREATED afresh. If its twin was already
            # `applied`, that is a second application under the user's name. That is
            # irreversible and reports as ordinary `created: N` activity, so refuse
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

    The `~` fix has a transition this path cannot warn about, and the reason is worth
    stating rather than discovering. `SLUICE_CONFIG='~/sluice.yaml'` used to resolve to
    the literal, `load_config` reads a missing file as NO CONFIG AT ALL, and so the whole
    file was ignored in silence; it now resolves and takes effect. If that config names
    explicit state paths, those keys take `resolve`'s explicit branch, which short-circuits
    the relocation check by construction -- so state at the old XDG location is orphaned
    with nothing said. Measured.

    That is not a new rule: a config key naming a state path has ALWAYS short-circuited
    the check, which is what keeps every caller supplying its own path immune to refusing
    to start. What is new is one more config becoming active. `resolve`'s orphan notice
    cannot cover it either -- that notice fires on state sluice WROTE at the literal path,
    and this is a file sluice only ever READ, so there is nothing at `./~/sluice.yaml` to
    find.

    Nothing can be said AT THIS PATH, then: a config that was being ignored and has just
    activated is indistinguishable here from an ordinary run with a working config, so a
    warning would fire forever for every user with a `~` in this variable. That is a claim
    about the config FILE only, and deliberately not about the harm. The orphaned STATE is
    detectable, one layer down and keyed differently -- an explicit path that does not
    exist while the XDG default for the same `name` does. That pair is false for a working
    install and false for a first run, so it does not fire forever, and it would catch
    this transition through the state keys rather than through the config file. It is not
    implemented here because it would warn for every explicitly-named path, not only the
    ones this bug reaches, which is a change to what #80 promises callers who name their
    own paths rather than part of fixing a tilde. Recorded so the next reader inherits the
    open question and not a false impossibility.
    """
    return resolve(env_var="SLUICE_CONFIG", config_value="", kind="config",
                   name="config.yaml")
