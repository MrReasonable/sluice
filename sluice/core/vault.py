"""Obsidian vault sink - turn Leads into Job Leads notes without ever clobbering
human/agent state.

The old pipeline rewrote every lead note wholesale on each
run, destroying any status/score/notes an agent or a human had set - the fragility
sluice exists to remove. sluice's rule: CREATE a note for a genuinely new lead,
but on re-scrape touch only a `last_seen` marker - never status, never enrichment,
never the body.

Frontmatter mirrors the existing vault notes (company/role/status/source/salary/
role_type/url plus the score + enrichment placeholders the downstream judge fills
in), so sluice leads are indistinguishable from the current pipeline's output.
The engine's Lead model is source-agnostic (title, job_type); the vault schema's
`role`/`role_type` naming is a translation that lives here at the sink boundary.
"""
import copy
import dataclasses
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
from datetime import date

from sluice.core import status as _status
from sluice.core.candidate import contact_block, full_name
from sluice.core.leads import (
    SAME,
    UNKNOWN,
    Lead,
    _norm_url,
    EMPTY_RECONCILE_REPORT,
    NON_ANSWER_COMPANIES,
    fold_company_answer,
    index_by_slug,
    is_placeholder_company,
    layout_subfolder,
    same_opportunity,
)
from sluice.core.log import get_logger
from sluice.core.protocols import (
    CANDIDATE_PROFILE_RELPATH,
    CRITERIA_RELPATH,
    EVIDENCE_KINDS,
    CandidateProfile,
    LeadNote,
    MalformedNoteField,
    UpsertResult,
    VaultConflict,
)

_LEADS_SUBDIR = os.path.join("Job Applications", "Job Leads")
_MYCV_BASELINE = os.path.join("My CV", "CV.md")
_CRITERIA_RELPATH = CRITERIA_RELPATH
# Public: `sluice init` offers this as the vault question's default. Imported by `cli.py` and
# PASSED to the catalogue rather than imported by it -- the pure question data must not depend on
# a concrete store. A second literal for the same default would also take the cwd-relative-path
# DoD grep from 9 to 10. (Spelled without the leading quote character on purpose: that grep
# matches a quote followed by ./, so naming the value in prose here would inflate its own count.)
DEFAULT_VAULT = "./vault"
_DEFAULT_VAULT = DEFAULT_VAULT

_SEP = " - "        # note-name separator; identity-determining, stays a literal (never config)
_SUFFIX_MAX = 40    # max chars of the location suffix on candidate 2; identity-determining literal
_CHAR_CAP = 120     # max chars of a note stem before the byte-clamp; identity-determining literal
_CREATE_RACE_RETRIES = 3  # #16: bounded re-reconciles when a create loses the TOCTOU race
_RMW_RACE_RETRIES = 3  # #16: bounded re-derivations before a modify-write refuses loudly
_MERGED_SUBDIR = "_merged"          # where merge_cluster archives losers (#23)

INBOX_SUBDIR = "_inbox"
"""Where a proposed, unverified entry lands. The vault's own mechanism, NOT on the
Store contract: a SQLite store would use a column, and no consumer outside this
module needs the name."""

VERIFIED_KEY = "verified"
"""The frontmatter key that makes an entry citable by the hard fabrication gate.
Store-managed: `propose_evidence` never writes it and `EvidenceKind.fields` never
lists it."""

# Directories under leads_dir that SLUICE owns, pruned from the scan set. Everything else
# under leads_dir is the user's and is scanned -- a `_`-prefix rule instead would silently
# swallow a user folder named `_archive`, and a lead invisible to read_leads is invisible to
# the write path too, so every note in it is re-created as a duplicate on the next scrape.
# Before this existed `_merged/` was invisible only INCIDENTALLY (os.listdir is
# non-recursive and `_merged` is a directory, so it failed the `.endswith(".md")` test);
# a recursive walk would have surfaced every archived loser and undone #81 outright.
_PRIVATE_SUBDIRS = frozenset({_MERGED_SUBDIR})
# #81: a URL-PROVEN match against an archived note -- the incoming lead and the merged-away
# one carry the same non-empty url. The sink records it in seen.db.
_ARCHIVED = "merged_away"
# #81: every weaker match -- a location-only SAME, or UNKNOWN. Suppressed, NEVER recorded.
_ARCHIVED_UNPROVEN = "merged_away_unproven"
# #81: the note name a loser was SEATED at, stamped INTO the note as merge_cluster archives
# it and read back by the write path's probe. Spelled for a human opening the archived note
# in Obsidian ("archived from note <name>"). Written only as a note is archived -- a note
# restored out of `_merged/` by hand keeps the key, and nothing reads it there.
_ARCHIVED_FROM = "archived_from_note"
# #151: reconcile_names' empty report, matching EMPTY_RECONCILE_REPORT's shape and its own
# deep-copy discipline (see reconcile_names). Kept HERE rather than in core/leads.py, unlike
# EMPTY_RECONCILE_REPORT: that constant lives there specifically so `cmd_leads_reconcile`'s
# knob-unset arm can emit an empty document without importing the concrete vault store (see its
# own comment) -- and reconcile_names has no knob-unset arm to serve, since it runs unconditionally
# regardless of `lead_layout`. Consumers deep-copy it for the same reason: a shallow `dict(...)`
# shares every mutable bucket, which is safe only while each one happens to be overridden.
EMPTY_RENAME_REPORT = {"examined": 0, "renames": [], "unresolved": [], "collisions": [],
                       "ambiguous": {}, "resurrected": [], "skipped": []}

_log = get_logger("core.vault")

# Frontmatter is the `---`-fenced block at the very top of a note. Capture its
# inner text and the body separately so updates can edit one key and leave the
# body byte-for-byte intact.
_FM_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


def _today() -> str:
    return date.today().isoformat()


def _parse_fm_spaced(inner: str | None) -> dict:
    """Frontmatter parse tolerant of spaced keys ('Best For'), quotes, and YAML
    block-list values (Category:\n  - Process\n  - Leadership) - the Experience
    Library format. Block-list items are joined into a comma-separated string so
    every field stays a str. Line-based, stdlib only."""
    out: dict = {}
    last_key = None
    for line in (inner or "").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("- ") and last_key is not None:
            item = stripped[2:].strip().strip('"')
            if item:
                out[last_key] = f"{out[last_key]}, {item}" if out[last_key] else item
            continue
        if ":" in line and not stripped.startswith("-"):
            k, _, val = line.partition(":")
            key = k.strip()
            out[key] = val.strip().strip('"')
            last_key = key
    return out


_SLUG_SAFE = re.compile(r"\A[a-z0-9][a-z0-9-]*\Z")

# The shape of a bundle citation code, kept as its own copy rather than imported:
# core/ must not depend on cv/.
#
# This used to mirror `cv/validate.py`'s `_ID_RE`, and was pinned textually equal to it,
# because that regex was how the gate decided which ids EXISTED -- it parsed them out of
# the rendered bundle text. #174 deleted it: the gate is now handed its ids structurally
# (`cv/bundle.py`'s `bundle_sources`), so there is no pattern in cv/ left to be equal to.
#
# The source of truth is now the GENERATOR, `cv/bundle.py`'s `assign_codes`/`_prefix`:
# `_prefix` coerces any company to exactly two A-Z letters, and `assign_codes` appends a
# per-prefix sequence number. `test_id_shaped_matches_every_generated_code`
# (tests/test_evidence_store.py) pins this pattern against ids that generator actually
# EMITS rather than against another regex -- strictly stronger than the equality it
# replaces, because it also fails if the generator's shape changes without any regex
# being edited.
#
# The direction that matters is unchanged: this must match AT LEAST every code the
# generator can produce. Refusing less than that lets an authored body line carry a
# token the bundle will later treat as a real entry's code.
_ID_SHAPED = re.compile(r"^\[([A-Z]{2}\d+)\]")


def evidence_slug(name: str) -> str:
    """Reduce a user-supplied entry name to a bare filename component, or raise.

    Called at CREATE time only. `propose_evidence` reduces the user's `--name` here
    and files the entry under the result; nothing looks an existing entry up by
    re-running this (see `_evidence_component`, below, for the bug that rule fixes).

    PUBLIC (no leading underscore) rather than a vault-private helper: #164 review
    found `verify`'s `--id` compared a human-typed NAME against `title`, and for an
    entry `propose_evidence` created, `title` IS the reduced slug -- so a name with
    spaces or mixed case could never match its own entry. `core/app.py`'s
    verify_evidence_interactive imports this directly (the same "import a pure
    vault helper straight into the facade" precedent `frontmatter_safe` already
    set) to apply the IDENTICAL reduction as one arm of that comparison, rather than
    hand-duplicating this regex a second place and risking the two drifting apart.
    It is only one arm: an entry added to `_inbox/` by hand has a `title` no
    reduction produces, which is why that filter also compares verbatim.

    The reduction runs FIRST and its result's SHAPE is asserted. The reverse --
    joining the raw name onto the inbox and checking containment afterwards -- makes
    the check unfirable, because no reduced slug contains a separator: an equivalent
    mutant, green forever. Asserting the shape stays falsifiable if the reduction
    itself is ever weakened.

    `os.path.basename(slug) != slug` is INERT under the character class above, not
    load-bearing: `_SLUG_SAFE`'s alphabet (`[a-z0-9-]`) can never produce a `/` or a
    `.`, so for every string that reaches this check `os.path.basename(slug)` already
    equals `slug` -- there is no input, today, that makes the two halves disagree.
    Measured, not argued: deleting this half changes the outcome of nothing the test
    suite exercises. It is kept anyway as defence-in-depth for a FUTURE widening of
    `_SLUG_SAFE` -- to admit a path separator or a dot, say -- which is exactly the
    change this half would then catch and `_SLUG_SAFE` alone would not.
    `test_slug_safe_pattern_is_pinned_so_a_widening_cannot_silently_arm_the_basename_guard`
    (tests/test_evidence_store.py) pins `_SLUG_SAFE.pattern` so such a widening cannot
    pass silently: its failure message names this guard as the thing to re-examine.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:80]
    if not _SLUG_SAFE.match(slug) or os.path.basename(slug) != slug:
        raise ValueError(
            f"evidence entry name {name!r} does not reduce to a usable filename "
            f"component (got {slug!r}) -- use letters and digits")
    return slug


def _evidence_component(name: str) -> str:
    """Assert `name` is ALREADY a bare filename component; return it unchanged.

    The LOOKUP half of the pair whose other half, `evidence_slug`, is the CREATE half.
    Reduction belongs at create time only: `propose_evidence` reduces the user's
    `--name` and files the entry under the result, so from that moment the entry's
    identity IS its on-disk basename, and re-deriving a slug at lookup time can only
    disagree with what is actually there. Measured on the real user path, where
    hand-editing the vault is a first-class workflow: a hand-added
    `_inbox/My Entry.md` is listed by `... list --pending` as `My Entry`, and
    re-slugging that title looked for `my-entry.md` -- so the entry was listed,
    unmatchable by `--id`, and permanently unverifiable behind a raw
    `FileNotFoundError` (#164 whole-branch review, IMPORTANT 2).

    Containment used to be a SIDE EFFECT of that reduction (`_SLUG_SAFE`'s alphabet
    cannot express a separator, so no reduced slug could escape the inbox). Dropping
    the reduction here therefore has to keep the property EXPLICITLY, which is all this
    function is: `os.path.basename(name) != name` is true of exactly the values that
    would escape `os.path.join(inbox, ...)` on the platform actually running -- `../x`,
    `a/b`, an absolute path, and, under `ntpath` (which IS `os.path` on Windows), a
    drive-relative `C:x`.

    That single check is the whole guard, deliberately. Every name `os.listdir` can
    hand back is a bare component by construction, so any STRICTER rule would refuse a
    name the listing had just displayed -- reopening the listed-but-unverifiable bug
    above instead of closing it. So a backslash on POSIX and a leading dot stay
    ordinary filename characters here, and `.`/`..` are accepted because they do not
    escape either: the caller appends `.md`, so `..` names `...md`, still inside the
    inbox.
    """
    if os.path.basename(name) != name:
        raise ValueError(
            f"evidence entry name {name!r} is not a bare filename component -- an "
            f"entry is looked up by the name it is filed under, never by a path")
    return name


def _evidence_entry_path(base: str, filename: str) -> str:
    """Join one evidence entry onto its directory, refusing a symlinked entry FILE.

    `_evidence_dir` closes the DIRECTORY half of this class (every component from the
    vault down); this is the other half, and it is a distinct harm rather than a
    thoroughness point. Measured on the real store: an `_inbox/alpha.md` that is a
    symlink to a file OUTSIDE the vault was read through, `verify_evidence` stamped its
    content and wrote it into the citable directory, and the `os.unlink` removed only
    the LINK -- so the foreign file survived and this is content INJECTION into the
    corpus the hard fabrication gate cites, where the directory case was deletion.

    Every route to an entry's bytes goes through here -- the shared `_evidence_entries`
    listing (so the CITABLE read is bound too, not just the inbox: a symlinked entry in
    the kind directory feeds `cv/bundle.py` foreign content with no promotion involved),
    `read_pending_evidence_text`, and `verify_evidence`'s source -- for exactly the
    reason `_evidence_dir`'s own docstring records: a guard is shared only by living
    where every caller must pass through it.

    Refuse, never resolve, matching the directory guard: `os.path.realpath` would make
    the link structurally invisible, and a store cannot tell a link the user built on
    purpose from one a sync client left behind. The recovery is the same one sentence
    the message carries -- move the real file into the vault.

    `os.path.islink`, so a DANGLING link refuses here too. That is deliberate: it
    inspects the entry itself via lstat, and a dangling link is precisely the state a
    sync client leaves when its target moves, which a `_read` would otherwise surface as
    a bare FileNotFoundError naming a path the user cannot act on.

    The shipped callers all pass `f"{...}.md"`, so `filename` carries its extension;
    this function does not append one, because `_evidence_entries` gets its names from
    `os.listdir` and re-deriving them would reopen the listed-but-unmatchable bug
    `_evidence_component` records.
    """
    path = os.path.join(base, filename)
    if os.path.islink(path):
        raise OSError(
            f"evidence entry {path!r} is a symlink; refusing to read an entry from "
            f"behind it, or to promote content from outside the vault into the "
            f"citable set -- move the real file into the vault")
    return path


def _refuse_citation_shaped_body(body: str) -> None:
    """Refuse a body line shaped like a bundle citation code, or return.

    Written when `cv/validate.py` recovered ids by parsing the rendered bundle text with
    `nums[cur] = set(...)` -- an ASSIGNMENT, not a union -- so such a line REBOUND that
    id's permitted numbers: a fabricated figure beside it cleared the hard gate while the
    entry's genuine metric was reported INVENTED. This was the write-side narrowing; #174
    was named as the close, and #174 has since LANDED. The gate no longer parses, so that
    bypass is gone and this function is no longer what stands between a body line and a
    rebound allowlist.

    It still earns its place, for a smaller and now-accurate reason. `bundle_sources`
    harvests every digit in an entry's own block, and a citation-shaped token in a body
    contributes ITS digits to that entry: a body reading `[NC1] delivered 987 things`
    puts `1` (from `NC1`) into that entry's permitted set. #174's design records that as
    an accepted residual, because closing it in the gate would need a second
    citation-stripping regex that must agree with the renderer's. On the WRITE path there
    is no such cost -- refusing the line outright is exact -- so this closes for authored
    evidence what the gate documents as residual. It does not reach a note a human edits
    in place; the residual stands there.

    Its OWN function because BOTH writes into the citable set need it and only one had
    it. `propose_evidence` (through `_render_evidence_note`) refused such a body;
    `verify_evidence` did not, and an entry can reach promotion without ever passing
    through propose -- a human dropping a file into `_inbox/` is a first-class workflow
    for this tool. Measured (#164 review, M1): a hand-placed body of
    `[NC1] delivered 987 things` verified True and landed citable, and at that time
    rebound NC1's numbers in the bundle the gate read.
    """
    for line in (body or "").splitlines():
        if _ID_SHAPED.match(line.strip()):
            raise ValueError(
                f"body line {line.strip()!r} is shaped like a bundle citation code; "
                f"its digits would join this entry's permitted numbers in the CV "
                f"fabrication gate")


def _render_evidence_note(spec, fields: dict, body: str) -> str:
    """Assemble an entry, refusing anything that would not survive being read back.

    Three guards, each closing a class rather than an enumerated vector:

    1. Unknown keys are rejected BY NAME. The round-trip below cannot catch them --
       it compares value fidelity, and {'verified': '2099-01-01'} round-trips equal
       to itself.
    2. The leading fence is ALWAYS emitted, even for a kind whose fields are all
       blank, so a body opening with its own `---` cannot become the parsed
       frontmatter (_FM_RE is \\A-anchored and non-greedy).
    3. The WHOLE assembled note is re-parsed with the same readers every consumer
       uses, and must yield exactly the fields written. This is what catches a
       newline inside a VALUE, which _parse_fm_spaced turns into a new key.
       onboard/plan.py's _render_candidate/FrontmatterRoundTripError is the same
       pattern; it validates the whole note, and so does this.

    Plus a fourth guard, shared with `verify_evidence` rather than owned here:
    `_refuse_citation_shaped_body` rejects a body line shaped like a bundle citation
    code. It was written as a narrowing of the parse bypass #174 has since closed; what
    it closes NOW is that such a token's own digits would join this entry's permitted
    numbers -- the residual #174's design accepts in the gate and this path can refuse
    outright. See that function's docstring.
    """
    unknown = sorted(set(fields) - set(spec.fields))
    if unknown:
        raise ValueError(
            f"unknown evidence field(s) {', '.join(unknown)}; this kind accepts only "
            f"{', '.join(spec.fields)}")
    _refuse_citation_shaped_body(body)
    want = {k: str(fields.get(k, "")) for k in spec.fields}
    inner = "\n".join(f"{k}: {v}" for k, v in want.items())
    note = f"---\n{inner}\n---\n{body or ''}"
    got = _parse_fm_spaced(_split_frontmatter(note)[0])
    if got != want:
        raise ValueError(
            f"evidence frontmatter does not round-trip: wrote {want}, read back {got} "
            f"-- a field value probably contains a newline or a colon at line start")
    return note


def _reraise(exc: OSError) -> None:
    """os.walk's onerror hook. Its DEFAULT is to SWALLOW the error and yield nothing for a
    directory it could not open, which turns one permissions bit into an invisible subtree:
    every lead in it disappears from read_leads AND from the write path's lookup, so the
    next scrape re-creates all of them. The store already refuses to read an unreadable
    dedup file as empty for the same reason -- this is that rule at directory scale."""
    raise exc


def _is_dir(path: str) -> bool:
    """Does `path` name a directory? NOT os.path.isdir, which swallows EVERY OSError and
    so reads an unreadable path as an absent one -- the same fail-open _reraise removes
    from the walk itself, one rung further up. Only FileNotFoundError is answered False
    (leads_dir before the first upsert: the overwhelmingly common case, and not an error);
    a PermissionError propagates, because a scan set that silently reads as [leads_dir]
    hides every note in every subfolder from read_leads AND from _locate, which re-creates
    all of them."""
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except FileNotFoundError:
        return False


def _is_note_file(path: str) -> bool:
    """Does `path` name a REGULAR FILE? `_locate`'s probe, and the same rule `_is_dir`
    states one rung up: NOT os.path.exists (nor os.path.isfile, which is the same trap in
    the other direction), because both swallow EVERY OSError and so read an unstatable
    path as an absent one.

    Measured, and the reason this exists: a scanned directory at mode `r--` is LISTABLE
    but not STATABLE, so os.walk succeeds, onerror=_reraise never fires, the directory is
    in the scan set -- and every stat inside it raises PermissionError. Under os.path.exists
    that made `_locate` return [], which is the `if not found:` branch: an `applied` note
    with a url-identical archived twin resolved to `merged_away`, the RECORDED arm, so the
    lead entered seen.db (no removal path), was suppressed permanently with its last_seen
    frozen, and the only log line said it had been merged away. Propagating instead reaches
    the ingest sink's `except OSError`, which counts the lead `skipped` and keeps it OUT of
    seen.db for a retry -- the same route `_archived_match` documents for an unreadable
    archive entry.

    REGULAR, not merely present: a directory or a fifo named `<name>.md` is not a note, and
    answering `found` for one sends `_read` at it (IsADirectoryError, mid-walk) instead of
    letting the walk reach its create/archive arms. Absent is answered only for the two
    errors that genuinely mean "no file there" -- FileNotFoundError, and NotADirectoryError
    for a scanned directory replaced by a file under a concurrent writer."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False


def _fold_note_name(name: str) -> str:
    """The identity fold for a note NAME: two names that fold equal name one lead (#205).

    THREE consumers, and the reason this is a function rather than a `.casefold()` at each:
    `_locate` (so a re-scrape under a different company casing resolves to the note already
    on disk instead of minting a sibling), `_archived_match` (so the same re-scrape cannot
    walk past a merged-away loser and RESURRECT it), and `read_leads`' collision report (so
    what the read path calls a collision is exactly what the write path calls one identity).
    A second copy of this rule kept in step by a comment is the #30 failure mode; here it
    would be worse than usual, because the three disagree SILENTLY -- a `_locate` that folds
    against an `_archived_match` that does not is measurably a resurrection.

    CASE ONLY, deliberately, and this is the line not to blur. `_norm_location` folds case
    AND applies NFKD AND drops combining marks AND collapses non-word runs, because it
    compares two values for whether they describe the same PLACE. This compares two
    FILENAMES for whether they are the same note, and every widening past case is a claim
    that two differently-spelled names are one job -- which, applied to a name, silently
    merges two real postings and is unrecoverable in the direction that matters. Unicode
    normalization is a real and SEPARATE axis (a macOS filesystem may hand back NFD for a
    name written NFC), left alone here rather than folded in on the way past: it needs its
    own measurement against a real store, and #205 is about case.

    `casefold`, not `lower`: `lower` is a per-character map that leaves the German sharp s
    alone, so a company written "STRASSE" and one written "Straße" would answer as two
    identities under `lower` and one under `casefold`. Matching `_norm_location`'s choice
    also means the two folds cannot disagree on a value they both see."""
    return name.casefold()


def _holds_a_note(path: str) -> bool:
    """Does `path` hold a `.md` file at ANY depth? The symlink warning's probe.

    RECURSIVE, and that is the whole point: a non-recursive listdir here missed exactly the
    layout this change invites. Measured with `Job Leads/Linked -> <target>` and the note at
    `<target>/2025/Acme - Analyst.md` at `status: applied` -- upsert returned `created`, a
    fresh note appeared at `new`, the applied original stayed untouched behind the link, and
    ZERO records reached the sluice.core.vault logger. The warning claimed to make that
    invisible-subtree harm loud and did not.

    Short-circuits on the FIRST hit rather than counting: the answer is a yes/no (only
    symlinks holding notes are reported at all), and walking a large linked tree to
    exhaustion on every scan -- several times per command -- to produce a number nothing
    branches on would be paid on every run for nothing.

    onerror=_reraise, never os.walk's default: the default SWALLOWS the error and yields
    nothing, so an unreadable target would answer 'no notes here' -- fail-open, and the
    caller reports an unreadable target precisely because it must not. The caller catches
    the OSError and says so."""
    for _, _, filenames in os.walk(path, onerror=_reraise):
        if any(f.endswith(".md") for f in filenames):
            return True
    return False


def _warn_undescended_symlinks(dirpath: str, dirnames: list, probed: set) -> None:
    """Warn about a symlinked subfolder the walk will not descend into (followlinks=False;
    see _walk). Best effort and log-only: it must never raise, because the caller is the one
    definition of the scan set and a warning path that aborts a read would be worse than the
    thing it warns about. `_holds_a_note` DOES raise on an unreadable target, deliberately,
    and the except below is what turns that into the report.

    Only symlinks HOLDING notes are reported, so a user's symlink to a folder of anything
    else stays quiet -- a warning that fires on every walk for a harmless link is one users
    learn to ignore, which is how the real one gets missed.

    `probed` is the store's own set of paths this has already ASKED about, and the
    distinction from "already reported" is the whole point: it is added to before the probe
    runs, so it memoises the expensive half rather than the cheap one. One command walks
    several times (a read, the scan set, one re-derive per create), and the memo has two
    jobs across those walks -- without it a link holding notes would say the same thing a
    dozen times in one run, and, keyed on the REPORT instead, a link holding NO note would
    never be memoised at all and `_holds_a_note` would re-walk its target to exhaustion
    every time. That is the case the short-circuit-on-first-hit inside `_holds_a_note`
    cannot help with: there is no hit to stop at, so the quiet link is the one that pays
    the FULL walk, over and over. Measured on a linked tree holding no `.md`: 5 creates
    plus 2 `read_leads` drove 8 complete walks of it, against 1 now.

    The cost of memoising the probe is that a note appearing behind a quiet link MID-RUN
    goes unreported until the next command. That is the same bound the report-keyed memo
    already accepted in the other direction and the same one the per-STORE scope accepts
    generally -- a later run is a fresh probe, and the condition may genuinely have changed
    either way.

    An unreadable target is reported rather than skipped: the notes behind it are just as
    invisible, and 'cannot tell' must not read as 'nothing there'."""
    for name in dirnames:
        path = os.path.join(dirpath, name)
        if path in probed or not os.path.islink(path):
            continue
        # BEFORE the probe, and unconditionally: see above. Every `continue`/fall-through
        # below has now already recorded it, so no outcome can leave the link unmemoised.
        probed.add(path)
        try:
            holds = _holds_a_note(path)
        except OSError as e:
            _log.warning("vault: %s is a symlink this scan cannot read (%s); any lead in it "
                         "is invisible and would be re-created", path, e)
            continue
        if holds:
            _log.warning("vault: %s is a symlink holding note(s); the scan does not follow "
                         "symlinks, so those leads are invisible and would be re-created "
                         "-- move the folder into the vault instead of linking it", path)


def _is_lead_note(fm: dict) -> bool:
    """Does this file's frontmatter make it a LEAD, as opposed to a note the user keeps
    alongside their leads (interview prep, research)? Once the scan is recursive those
    share the tree, and treating every `.md` as a lead would triage them.

    A file qualifies when EITHER field is present, and is excluded only when BOTH are
    absent -- one surviving field is enough. The threshold sits there and not at "both
    present" because a hand edit that blanks `role` (the #16 threat model, a human in
    Obsidian) must not stop the note being a lead.

    This is the predicate _archived_match already uses, and it is right in both places for
    the SAME reason rather than a mirrored one: skipping too eagerly loses a note that
    really exists. The two COSTS differ, though, and the difference is worth being exact
    about, because the obvious guess is wrong. There, a skipped archive entry stops
    suppressing, so a lead a human merged away is resurrected (#81) -- a duplicate. Here, a
    skipped file is NOT duplicated: `_locate` deliberately does not apply this predicate
    (see there), so the write path still finds the note by name, reconciles onto it and
    bumps its last_seen. Measured with `company` and `role` both blanked at a lead's exact
    candidate name: `read_leads` returned nothing, `_locate` found it, `upsert` returned
    `merged`, and one note remained on disk. So the cost is a SILENT DROP rather than a
    duplicate -- the note sits in the vault being refreshed by every scrape while triage,
    cv, apply and track never see it, and nothing anywhere says so. A duplicate is at least
    visible."""
    return bool(fm.get("company") or fm.get("role"))


# The store contract's note type. `VaultNote` survives as an alias because this module
# is the vault's own, but the type the SEAM speaks is LeadNote: `ref` is an opaque
# handle (a path here, a row id in another store) and `slug` is issued by the store
# rather than re-derived from a filename by four separate callers.
VaultNote = LeadNote


class Vault:
    def __init__(self, dir: str | None = None, *, baseline_rel: str = _MYCV_BASELINE,
                 location_noise_words=(), lead_layout: str = ""):
        # expanduser at CONSTRUCTION, so every route in agrees: the factory's env-or-config value,
        # a direct `Vault(dir)`, and the default below. A literal `~` is never what anyone means by
        # a vault path, and a shell that does not expand it (an env var set in a config file, a
        # systemd unit, a Docker env line) hands one straight through.
        #
        # Measured before this: `VAULT_DIR='~/probevault' sluice init --no-input` wrote
        # `vault_dir: <HOME>/probevault` into the config -- `cmd_init` expands -- while the profile
        # went to a literal `./~/probevault/` under the CWD. Two artefacts naming two different
        # vaults, and triage then reads the config's one, finds no profile, and silently falls back
        # to the shipped default criteria.
        #
        # This is NORMALIZATION, not precedence, so #80's rule still holds: which value wins is
        # decided in `stores/vault.py:_make`, and nothing here reorders that. No `abspath` --
        # a relative vault is legitimate and documented (`./vault` is the default).
        self.dir = os.path.expanduser(dir or os.environ.get("VAULT_DIR", _DEFAULT_VAULT))
        self.leads_dir = os.path.join(self.dir, _LEADS_SUBDIR)
        self.baseline_rel = baseline_rel
        self._name_max_cache: int | None = None
        # Fed raw into same_opportunity -> _compare_locations, which tokenizes it. #5's
        # split policy knob; empty by default (abstain). See core/config.py.
        self._noise = frozenset(location_noise_words or ())
        # #1. Validated HERE, at construction, and by CALLING the pure map rather than re-testing
        # membership: a second copy of "is this a known layout" is a second thing to keep in sync,
        # which is the #30 failure mode. `layout_subfolder` raises and lists the valid names for an
        # unknown one, so a typo'd `lead_layout: activearchive` cannot degrade silently to flat and
        # leave a user believing their vault is being filed when nothing is. The probe status is
        # "new" because that is what a created note carries (see the rendered frontmatter in
        # upsert), so this is the same call `_write_folder` makes -- if it raises there it raises
        # here, at the earliest possible moment, on every command that builds a store.
        layout_subfolder("new", lead_layout)
        self.lead_layout = lead_layout
        # The scan set, computed once per store instance -- re-deriving it per lead is the
        # dominant cost of a run (figures in the design spec). The staleness window is a
        # human filing a note into a NEW subfolder mid-run, which _resolve_path closes on
        # the create arm by re-deriving this from disk before it mints a note; see there
        # for why that window could not be left open.
        self._scan_dirs_cache: list[str] | None = None
        # Symlinked subfolders already PROBED (see _warn_undescended_symlinks) -- not
        # merely the ones reported, which would leave a link holding no note re-walked to
        # exhaustion on every scan. Per STORE, because one command walks several times and
        # a link must not say the same thing a dozen times in one run.
        self._probed_symlinks: set = set()
        # Duplicate slugs already reported by read_leads, on the same discipline as the
        # symlink set above -- but NOT for the same measured reason: no shipped command
        # reads one status set twice through a single store, so this suppresses nothing
        # today and is forward-looking (see read_leads for the enumeration, and for why
        # this is kept where `track/receipt.py` deleted its own unreachable guard). Keyed on
        # (slug, refs), never the slug alone: a LATER read whose filter surfaces a different
        # set of twins at that slug is a different fact and must still be said.
        self._warned_dup_slugs: set = set()

    def _slug_for(self, path: str) -> str:
        """The lead's stable identity. For a markdown vault that is the filename without
        its extension -- exactly what apply/select.py, apply/engine.py, track/classify.py
        and track/engine.py each used to recompute for themselves."""
        name = os.path.basename(path)
        return name[:-3] if name.endswith(".md") else name

    # ── the scan set ─────────────────────────────────────────────────────────
    def _walk(self):
        """Yield (dirpath, filenames) for every scanned directory under leads_dir, with
        `_PRIVATE_SUBDIRS` pruned. Unannotated deliberately: the return type needs
        `Iterator`, and a quoted annotation naming an unimported type is ruff F821.

        THE one definition of the scan set: read_leads, normalize_all_statuses and
        _scan_dirs all consume this, so the exclusion cannot be applied in one place and
        forgotten in another -- and forgetting it in read_leads resurrects every note a
        human merged away (#81).

        The prune is applied only when dirpath IS leads_dir, because leads_dir/_merged is
        the single directory merge_cluster CREATES and _archived_match reads. (It is not the
        only thing merge_cluster writes -- the survivor note is CAS-written wherever it
        already sits, which is anywhere in the scan set. What matters here is that the
        archive is the one DIRECTORY, and that it is always at the top level.) Pruning the
        name at every depth would instead hide a same-named directory the USER made, whose
        notes would then be re-created as duplicates.

        onerror=_reraise, never the default: see there.

        followlinks=False, os.walk's default and deliberately kept. Following would let a
        symlink loop spin the walk forever and would let a link out of the vault pull
        arbitrary directories into the scan set. The cost is that a SYMLINKED subfolder --
        ordinary practice in an Obsidian vault, and this change is what invites users to
        file leads into subfolders at all -- is invisible to read_leads AND to _locate, so
        every lead behind it is silently re-created (measured: a note at `status: applied`
        came back `created`, the original untouched, with no log line). That is exactly the
        invisible-subtree harm _reraise exists to stop, arriving by a different route, so it
        is made loud here: os.walk still LISTS an undescended symlink in `dirnames`, which
        is where it is visible even though it is not followed. `_warn_undescended_symlinks`
        descends the TARGET to decide whether to speak (see `_holds_a_note`) -- a flat
        listing of it left the nested layout, the one a recursive scan invites, as silent as
        before the warning existed."""
        for dirpath, dirnames, filenames in os.walk(self.leads_dir, onerror=_reraise):
            if dirpath == self.leads_dir:
                dirnames[:] = [d for d in dirnames if d not in _PRIVATE_SUBDIRS]
            # After the prune, so a symlinked `_merged/` stays silent: it is pruned from the
            # scan on purpose, and _archived_match's own listdir follows it regardless.
            _warn_undescended_symlinks(dirpath, dirnames, self._probed_symlinks)
            yield dirpath, filenames

    def _scan_dirs(self) -> list[str]:
        """The scan set as a directory list, cached. Falls back to [leads_dir] before that
        directory exists, and does NOT cache that answer: upsert creates leads_dir mid-run,
        so caching 'missing' would leave every later lookup in the same run blind to the
        directory it had just written into.

        Returns a COPY, never the cache object. Handing out the live list makes every caller
        a potential writer of the store's own state: one `dirs = v._scan_dirs()` followed by
        an append or a `.sort()` poisons the scan set for the rest of the instance's life,
        and the damage is exactly the wedge `_resolve_path`'s re-derive exists to prevent --
        a directory list that no longer describes the disk, silently manufacturing `create`
        (a duplicate) or `merged_away` (a permanent `seen.db` row) out of a `_locate` that
        looked in the wrong places. No consumer mutates it today; the point is that none can.

        The cost was measured before choosing, because a copy is paid per `_locate` call and
        `_locate` runs per candidate per lead. It is 0.06us at one directory and 0.11us at
        fifty, against 4.4us and 111us for the `_locate` that wraps it -- 1.4% falling to
        0.1%, because the loop this feeds does one `os.stat` per directory and a list copy
        cannot compete with a syscall. So the allocation is real and it is noise."""
        if not _is_dir(self.leads_dir):
            return [self.leads_dir]
        if self._scan_dirs_cache is None:
            self._scan_dirs_cache = [dirpath for dirpath, _ in self._walk()]
        return list(self._scan_dirs_cache)

    def _rescan_dirs(self) -> list[str]:
        """Re-derive the scan set from disk, cache BYPASSED, and return the fresh list.

        Assigning None rather than calling _walk directly keeps ONE definition of what the
        cached value is (including the never-cache-a-missing-leads_dir rule); a second
        expression that filled the cache itself is the shape that drifts."""
        self._scan_dirs_cache = None
        return self._scan_dirs()

    def _write_folder(self) -> str:
        """The ONE directory a NEW note is created in -- as opposed to `_scan_dirs`, every
        directory a note may be READ from. One field used to be both; separating them is the whole
        of the #1 layout design.

        Resolved through `layout_subfolder` at the status a created note actually carries ("new" --
        see the rendered frontmatter in `upsert`), never by naming Active/ here. Two things follow,
        and both are the point. A created note is BY CONSTRUCTION already in the folder its status
        implies, so `leads reconcile` has nothing to do with a note ingest just made. And there is
        ONE definition of the status->folder map, so a change to it cannot leave the write folder
        pointing somewhere reconcile immediately moves the note out of -- which would relocate
        every freshly-ingested lead on the next pass.

        Under the flat default this returns `self.leads_dir` unchanged, so an unconfigured store is
        byte-identical to the pre-#1 one. A METHOD rather than a cached attribute because it is
        called once per create, which is bounded by the run's lead count, and a cached path is one
        more thing that can disagree with `self.lead_layout`."""
        sub = layout_subfolder("new", self.lead_layout)
        # `sub` is never None here: "new" is canonical, and the layout name was validated at
        # construction. Guarding it anyway would be an unreachable branch wearing a comment
        # claiming it fires -- the shape track/receipt.py deleted.
        return os.path.join(self.leads_dir, sub) if sub else self.leads_dir

    def _locate(self, name: str) -> list[str]:
        """Every path in the scan set holding a note called `name`. A lead's identity is its
        note NAME; which directory it sits in is not part of it, which is what lets a note be
        filed, archived or restored without the next scrape re-creating it.

        Deliberately does NOT apply _is_lead_note. A hand edit that blanked `company` and
        `role` would make the note un-findable here, and un-findable means re-created as a
        duplicate -- the opposite of what the predicate is for on the read path. A non-lead
        file squatting a lead's exact candidate name is reconciled against as though it were
        a lead, which is unchanged from the flat store and neither introduced nor widened.

        Returns a LIST, not the first hit: two notes at one name is ambiguous identity, and
        _resolve_path must refuse rather than pick one. See there.

        The per-candidate probe is `_is_note_file`, never os.path.exists: an unstatable path
        must not read as an absent one here, because absent is the branch that creates and
        that records a merged_away in seen.db. See there for the measured failure.

        Matches a name up to CASE (#205), because a board renders one employer several ways
        and the note name is built from the company string verbatim. Two probes, in this
        order: the exact name, then -- only if that found nothing -- a folded listing. See
        the body for why the order is what makes the second affordable, and for the one
        state it leaves unreported.

        Reads LIVE on every call, both probes. The obvious optimisation -- a name->paths
        index built from the same `_walk` `_scan_dirs` already runs, turning this into a
        dict lookup -- would be a BUG, not a saving. `upsert`'s create-race loop terminates
        only because a re-resolve can SEE a note a concurrent writer (another `ingest run`,
        or a human in Obsidian) created since the last attempt; against a cached filename
        index that note stays invisible, every retry re-derives the same absent candidate,
        and the loop exhausts into a refusal for a lead that was perfectly writable. So only
        the DIRECTORY list is cached, because the set of FOLDERS is what is expensive to
        rediscover while its staleness has one bounded consequence, which `_resolve_path`
        then closes on the create arm.

        The cost is now TWO different shapes, and conflating them is how the cheap half gets
        argued away. The exact probe scales with the DIRECTORY count, not the note count:
        one stat per candidate per scanned directory. The folded probe scales with the NOTE
        count, since it lists each scanned directory -- ~1.9ms against a 3190-note store and
        ~5.5ms at 10000, where the stat probe stays ~7us at both. That second shape is why
        the fold runs SECOND: a steady-state run is overwhelmingly notes that already exist
        at the name being asked for, and those return from the exact probe having listed
        nothing. Earlier measured figures (local disk, and what they become on a network or
        FUSE mount where a stat costs ~1ms) are in
        `docs/superpowers/specs/2026-08-01-vault-subfolders-design.md`; they are a
        single-machine measurement no test pins, so they live with the design rather than
        beside the code. A deep hierarchy on a slow mount is where this design is worst, and
        nothing adapts. (_name_max already acknowledges such mounts, for pathconf.)"""
        found = []
        dirs = self._scan_dirs()
        for dirpath in dirs:
            path = os.path.join(dirpath, f"{name}.md")
            if _is_note_file(path):
                found.append(path)
        if found:
            return found
        # #205: nothing at the EXACT name. Before letting the walk conclude "absent" --
        # the branch that creates, and the branch that records a merged_away in seen.db --
        # look again for a note whose name folds equal (see _fold_note_name). Boards render
        # one employer several ways, the note name is built from the company string
        # verbatim, and without this each spelling seats its own note with its own status:
        # the reported store held one spelling at `shortlist` score 86 while its twin held
        # a `dismiss`, so a dismissal recorded under one spelling did not stop the role
        # returning as `new` under the other. It also wedged replication -- a
        # case-insensitive filesystem cannot hold the pair, and Syncthing reports the folder
        # `state=idle` while never delivering either note.
        #
        # AFTER the exact probe, never instead of it, and the ordering is what keeps this
        # affordable. A steady-state run is overwhelmingly notes that already exist at the
        # name being asked for, and those never reach here: measured over a 3190-note store
        # on a case-sensitive filesystem, the exact hit stays ~7us while this listing costs
        # ~1.9ms (~5.5ms at 10000 notes), scaling with the note count rather than the
        # directory count the stat probe scales with. So the cost lands on the MISS arm --
        # a genuinely new lead, or the case-variant this exists to catch -- which is also
        # the arm already paying _archived_match's listdir.
        #
        # The ordering has a consequence worth stating rather than discovering: when the
        # exact name IS present and a case-variant of it also exists, this never runs, so
        # `upsert` updates the exact one and says nothing about its twin. That state is a
        # store that predates this fix, and it is REPORTED by read_leads (which walks
        # anyway) rather than refused here -- refusing would mean folding on every lookup
        # including the steady-state hit, and `job-sluice leads dedupe` already clusters
        # such a pair and offers the merge (measured), so the remedy exists and only the
        # signal was missing.
        #
        # Lists LIVE, like the stat probe above and for the same reason: `upsert`'s
        # create-race loop terminates only because a re-resolve can SEE a note a concurrent
        # writer created since the last attempt. A cached name index would hide it, every
        # retry would re-derive the same absent candidate, and the loop would exhaust into
        # a refusal for a lead that was perfectly writable. Only the DIRECTORY list is
        # cached (see _scan_dirs), which is what `_resolve_path` re-derives around.
        #
        # `_is_note_file` on the entry, not `e.is_file()`: scandir's own predicate swallows
        # every OSError and answers False, which would read an unstatable path as absent --
        # the exact trap `_is_note_file` exists to close, on the exact branch where reading
        # absent-for-present creates a duplicate or records an irreversible seen.db row.
        want = _fold_note_name(f"{name}.md")
        for dirpath in dirs:
            try:
                with os.scandir(dirpath) as it:
                    entries = [e.path for e in it if _fold_note_name(e.name) == want]
            except OSError:
                # A directory that listed a moment ago and cannot now -- deleted, or
                # permissions changed mid-walk. Skipped rather than propagated, because
                # this is a SECOND look at a set the exact probe above has already reported
                # on: raising here would turn a lookup that legitimately found nothing into
                # a failure, on the arm where the exact-name answer is already in hand.
                continue
            found.extend(p for p in entries if _is_note_file(p))
        return found

    # ── paths ────────────────────────────────────────────────────────────────
    def _name_max(self) -> int:
        """The filesystem's max filename length in BYTES for the leads dir, cached.
        os.pathconf needs an existing path; in the normal flow upsert makes leads_dir
        before _candidate_names runs. A direct _note_name/_candidate_names call before
        the dir exists (e.g. a unit test) just takes the 255 fallback below, which also
        covers filesystems where pathconf is unsupported (some network/FUSE mounts).

        pathconf can also RETURN -1 (a value, not an exception) when NAME_MAX is
        indeterminate. Uncaught, a non-positive limit would drive _note_name's byte
        budget negative and negative-slice every note's name -> a vault-wide rename.
        So anything too small to hold a name plus its extension takes the 255 fallback
        too, not just the exception path."""
        if self._name_max_cache is None:
            try:
                n = os.pathconf(self.leads_dir, "PC_NAME_MAX")
            except (OSError, ValueError, AttributeError):
                n = -1
            self._name_max_cache = n if n > len(b".md") else 255
        return self._name_max_cache

    def _note_name(self, stem: str, suffix: str = "") -> str:
        """The note stem for a lead: sanitized, char-capped, then byte-clamped to NAME_MAX.
        ALL name candidates (#5's clean `Company - Title`, its `- Location` variant, and the
        `- <title-digest>` variant) go through this one helper, so their truncation can never
        drift -- a suffixed candidate that sanitized or capped differently would mis-key and
        reintroduce a duplicate.

        `.replace` is a length-preserving per-char map, so `stem.replace()[:120]` equals the
        old `[:120].replace()` char for char: candidate 1 stays byte-identical to the pre-#5
        `_path_for`, so no existing note's identity moves (zero migration). The suffix is
        bounded to _SUFFIX_MAX BEFORE the stem arithmetic, so the stem budget can never go
        negative (a negative index silently keeps 'all but the last N chars'). The final
        byte-clamp is #24's NAME_MAX safety, applied to every candidate."""
        stem = _sanitize(stem)
        if suffix:
            suffix = _sanitize(suffix)[:_SUFFIX_MAX]
            name = stem[:_CHAR_CAP - len(_SEP) - len(suffix)] + _SEP + suffix
        else:
            name = stem[:_CHAR_CAP]
        return _clamp_bytes(name, self._name_max() - len(b".md"))

    def _reconcile(self, fm: dict, lead: Lead, capped: bool) -> tuple[str, bool]:
        """The ONE verdict, shared by the active walk and #81's archive probe: ("update",
        "merge" or "advance", url_proven). A second copy kept in sync by a comment is the
        #30 failure mode -- a check that must match another check, with prose standing in
        for the guarantee -- so both callers go through here.

        The second element is the EVIDENCE behind the first: True only when a matching
        non-empty url proved the two are the same posting, False when the action rests on
        a location-token overlap or on the absence of evidence. `same_opportunity` folds
        both into one SAME verdict, so the action alone cannot carry the distinction --
        and the archive probe MUST have it, because only a url-proven suppression may be
        recorded in seen.db, which has no removal path. It is RETURNED rather than
        recomputed in the probe for the same reason this function exists at all: a second
        copy of the url comparison is a second thing to keep in sync. The active walk has
        no use for it -- against an ACTIVE note a SAME verdict terminates the walk the
        same way however it was reached -- so `_resolve_path` discards it, and that walk
        stays byte-identical to before this element existed.

        `capped` is the caller's, not re-derived: it measures the CHAR cap on the FULL
        `company - title` stem, which only the caller knows. Deleting the `capped and`
        below leaves the whole suite green except
        test_capped_gate_on_title_lost_is_load_bearing, and it is NOT an equivalent
        mutant -- it makes title_lost fire for short titles, so a human correcting a
        note's `role` in Obsidian turns every later re-scrape into an advance."""
        verdict = same_opportunity(fm, lead, self._noise)
        # A matching non-empty URL is same_opportunity's DEFINITIVE proof of the same
        # posting, so a drifted title tail on a url-stable posting must still update in
        # place rather than mint a digest note per drift.
        url_proven = (bool(lead.url) and bool(fm.get("url"))
                      and _norm_url(lead.url) == _norm_url(fm.get("url", "")))
        # A capped filename can seat a note whose FULL title differs -- only the truncated
        # prefix matched. Treat that as advance, exactly like a proven-different location.
        title_lost = (capped and not url_proven
                      and _title_key(fm.get("role", "")) != _title_key(lead.title))
        if title_lost:
            return "advance", url_proven
        if verdict == SAME:
            return "update", url_proven
        if verdict == UNKNOWN:
            return "merge", url_proven
        return "advance", url_proven

    def _archived_match(self, names, lead: Lead, capped: bool) -> str | None:
        """#81: has a human already merged this lead away? Returns the outcome string --
        `_ARCHIVED` when a matching non-empty url PROVED it, `_ARCHIVED_UNPROVEN` on any
        weaker match -- or None to let the walk create. Both suppress; only the first is
        recorded in seen.db, so the split is the whole reason this returns a string.

        Probes EVERY name candidate, not just the one the walk stopped at: the walk returns
        at its first ABSENT candidate, but the loser may have been archived under its
        location-suffixed or title-digest name, which the walk would never reach.

        The decision is made on a fact the ARCHIVER recorded, never on one reconstructed
        here. `merge_cluster` stamps `archived_from_note` into each loser as it archives it
        -- the note name that loser was actually seated at -- and this probe compares that
        value with the candidate. Reconstructing the name from the archived note's own
        company/role was tried and abandoned: `_sanitize` maps `"` to `-` while `_fm_dict`
        strips a leading/trailing quote, so any component whose edge character is a quote
        no longer re-derives to the name it was seated at; and a human correcting
        `company`/`role` in Obsidian after the merge (the #16 threat model) breaks the
        re-derivation the same way. Both are witnessed on the url-PROVEN arm
        (test_quote_edged_component_still_suppresses,
        test_post_archive_edit_of_role_still_suppresses), so a re-derivation failure there
        does not merely weaken the outcome -- the candidate matches no entry at all and
        the lead is re-created, which is the resurrection this whole probe exists to stop.

        The filename pattern is a cheap PRE-FILTER, never the decision. It is a superset by
        construction: `merge_cluster` derives the archived filename AND the recorded value
        from the same `stem`, so an entry recording `<name>` is called `<name>.md` or
        `<name>.<n>.md` and nothing else. Filtering first keeps the create path at one
        `listdir` plus a read per matching entry instead of a read of the whole archive; it
        is not what makes the match safe, so loosening it cannot resurrect a lead.

        LEGACY entries carry no recorded name and are matched by EXACT filename only:
        `<name>.md`, never `<name>.<n>.md`. THREE populations reach that arm, and the third
        is one this code MANUFACTURES at runtime -- an archive written before the field
        shipped, one whose field a hand edit made unreadable, and one whose stamp FAILED
        (`_stamp_archived_from` swallows its error, deliberately; see there). A fully
        upgraded install is therefore not immune to any of what follows.

        The legacy arm is wrong in BOTH directions, and only one of them is safe:

        - MISS: a collision entry `X - Y.1.md` is not matched by candidate `X - Y`, so a
          re-scrape of the lead it archives is CREATED. That is the direction to fail in --
          a visible duplicate note a human can merge again.
        - WRONG HIT: a candidate genuinely named `X - Y.1` still matches that same entry,
          so a never-seen job whose title ends in `.` plus digits is suppressed. It is
          suppressed on the UNPROVEN arm, though, and that bounds the damage: the entry
          archives a DIFFERENT job, so its url cannot match the never-seen one's, the
          url-proof gate below is not satisfied, and the lead stays out of seen.db and
          re-reports every run until a human acts
          (test_legacy_wrong_hit_is_suppressed_only_on_the_unproven_arm).

        For a genuinely PRE-UPGRADE archive the wrong hit is unavoidable: its filename is
        the only evidence that ever existed for it. That rationale does NOT carry to a
        stamp-failed entry, where `stem` was in hand at the moment of the archive
        (`merge_cluster`) and only the write of it failed. So this residual is bounded by
        "archives whose name was never successfully recorded", NOT by "archives from before
        the upgrade" -- do not read it as the narrower thing.

        A sequential `<stem>.1.md`, `<stem>.2.md` walk is NOT equivalent to the listdir: it
        stops at the first miss, and restoring a note out of `_merged/` -- the documented
        recovery -- punches exactly that hole, hiding every archive behind it."""
        merged_dir = os.path.join(self.leads_dir, _MERGED_SUBDIR)
        try:
            entries = sorted(os.listdir(merged_dir))
        except FileNotFoundError:
            # Never merged: the overwhelmingly common case, and NOT an error. Caught
            # specifically rather than by a bare `except OSError`, which would also swallow
            # an unreadable directory and silently disarm this guard on the vaults where it
            # matters most.
            return None
        for name in names:
            # FOLDED on BOTH sides for #205, rather than the same pattern with
            # `re.IGNORECASE`. The pre-filter has to stay the "cheap superset" the docstring
            # above describes -- widening it cannot admit a decision, only more candidates
            # for the seated-name comparison below to rule on -- and it must be at least as
            # wide as that comparison, or the fold there is unreachable for exactly the
            # population it exists for: an archived loser seated at `EXAMPLE CO - X.md`
            # skipped here is never compared at all.
            #
            # `re.IGNORECASE` looked like it did that and does NOT, which is why this is a
            # fold rather than a flag. IGNORECASE is a simple per-character case mapping
            # while `_fold_note_name` is a full `casefold`, and the two disagree wherever a
            # fold changes LENGTH -- a sharp s against a written-out double s is the
            # reachable case. Measured: the flag left the pre-filter NARROWER than the
            # decision on that population, so the entry was dropped before its recorded name
            # was read and the lead was re-created. That is a resurrection produced by the
            # half-measure meant to prevent one. Folding both sides restores "superset by
            # construction" as a property rather than an assertion.
            #
            # The `.md` suffix folds with everything else, so an entry named `.MD` now
            # matches where the original literal pattern did not. Sluice never writes one;
            # a hand-made one fails toward SUPPRESSION, the recoverable direction, and the
            # recorded-name comparison below still gates every decision.
            folded = re.compile(re.escape(_fold_note_name(name)) + r"(?:\.\d+)?\.md\Z")
            for entry in entries:
                if not folded.match(_fold_note_name(entry)):
                    continue
                path = os.path.join(merged_dir, entry)
                # No `except OSError` here, deliberately. The nearest neighbour, read_leads,
                # does `except OSError: continue` -- copying that shape would make an
                # UNREADABLE archived loser stop suppressing, re-minting the lead as an
                # ordinary `created: N`: resurrection by way of a permissions error.
                #
                # What propagating costs, measured rather than assumed, differs by error:
                # an OSError (a permissions failure, the case above) reaches the sink's
                # `except OSError`, which counts the lead `skipped` and keeps it out of
                # seen.db for a retry next run. A DECODE error does not -- a non-UTF-8
                # archived note raises UnicodeDecodeError, a ValueError, which that clause
                # does not catch, and engine.py calls sink.write OUTSIDE its per-source
                # try, so the run aborts. That is not a new exposure: read_leads' own
                # `_read` sits inside the same `except OSError` and aborts identically on
                # an undecodable ACTIVE note, so every triage/cv/apply/track command
                # already behaves this way. Widening to `except OSError: continue` here to
                # soften it would reintroduce the resurrection above, which is worse.
                inner, _ = _split_frontmatter(_read(path))
                fm = _fm_dict(inner)
                # Is this a NOTE at all? Keyed on company/role, never on url/location: a real
                # note can carry url:"" (google leads) AND a blank location, which is exactly
                # the UNKNOWN case this probe exists to suppress. Testing the same keys the
                # verdict consumes would collapse "is this a note" into "what does it say"
                # and skip a legitimate loser. merge_cluster's own O_EXCL reservation leaves
                # a 0-byte file here if the process dies before os.replace, and its cleanup
                # is best-effort, so this arm is reachable in the field.
                #
                # NEITHER, not EITHER, and that asymmetry is the point. A reviewer proposed
                # skipping when company OR role is missing; it moves the wrong way. Skipping
                # more often means SUPPRESSING less often, so an archived loser whose `role`
                # a hand edit blanked (the #16 threat model -- a human in Obsidian) would be
                # skipped and the lead resurrected. "Neither" fails toward suppression, the
                # recoverable direction, and the seated-name comparison below is what
                # actually gates every downstream decision -- this predicate only has to
                # reject a file with no note in it at all. Both half-blank shapes are pinned
                # as notes (test_company_only_archived_entry_is_still_a_note and its role
                # sibling) so the choice cannot be flipped silently.
                if not _is_lead_note(fm):
                    _log.warning("vault: ignoring unreadable archived note %s", path)
                    continue
                # The name this entry was SEATED at: the fact merge_cluster recorded, or --
                # for an entry carrying none (THREE populations, one of them a failed stamp
                # on an up-to-date install; see the docstring) -- a fallback to its own
                # filename, sound only where that filename carries no collision counter. The
                # `.md` slice is safe because the pattern above required that suffix.
                seated = _archived_from(inner)
                if seated is None:
                    seated = entry[:-len(".md")]
                if _fold_note_name(seated) != _fold_note_name(name):
                    # A collision counter appended to a DIFFERENT note's name, or a legacy
                    # entry whose counter cannot be told from a title that genuinely ends
                    # in `.` plus digits. Either way this archive is not this candidate.
                    continue
                # FOLDED (#205), and this arm is the more serious half of that issue rather
                # than a tidy-up alongside it. Measured on shipped code: merge a lead away,
                # then re-scrape it with the company spelled `EXAMPLE CO` instead of
                # `Example Co` -- outcome `created`. The exact-casing control suppresses
                # correctly, so the guard was working and the re-scrape simply walked past
                # it. That is a silent breach of non-resurrection: it undoes a human's merge
                # decision, and where the surviving twin was already `applied` it means a
                # second application under the user's name.
                #
                # Widening this comparison can only SUPPRESS more, never resurrect more, so
                # it moves in the safe direction by construction. What it must not do is
                # widen what gets RECORDED, and it does not: the `seen.db` arm below is
                # gated on `url_proven` -- a matching non-empty url -- which no amount of
                # name folding can manufacture. A fold-widened match that is not url-proven
                # lands on the UNPROVEN arm, writes nothing, records nothing, and re-reports
                # every run until a human acts.
                action, url_proven = self._reconcile(fm, lead, capped)
                # The RECORDED arm is gated on url-proof, NOT on the bare SAME verdict.
                # `same_opportunity` returns SAME from a matching url OR from a location
                # token overlap, and the second is not identity: a genuinely new
                # requisition at the same company, title and location -- a re-post, a
                # second headcount -- carries a BRAND-NEW url and would otherwise land
                # here, enter seen.db, and be suppressed permanently and invisibly, with
                # no note anywhere and no removal path to undo it. Deleting `and
                # url_proven` restores exactly that bug (witnessed:
                # test_location_only_same_is_unproven_and_stays_out_of_seen_db).
                if action == "update" and url_proven:
                    _log.warning("vault: %r was merged away (archived at %s); not re-created",
                                 lead.dedup_key, path)
                    return _ARCHIVED
                if action in ("update", "merge"):
                    # Weaker than url-proof -- a location-only SAME, or UNKNOWN. Suppress
                    # (the archived note may well BE this job, and minting a duplicate of a
                    # lead a human merged away is the harm #81 exists to stop), but never
                    # record: this arm re-surfaces and re-reports every run until a human
                    # acts, which is the recoverable direction.
                    # "not url-proven", not "evidence inconclusive": since the url-proof
                    # gate this arm ALSO carries a location-only SAME, which is definite
                    # evidence that merely is not identity. Naming the missing proof also
                    # tells the reader what would settle it. The archived path is in the
                    # message because acting on this means opening THAT note.
                    _log.warning("vault: %r may have been merged away (archived at %s, "
                                 "match not url-proven); not re-created", lead.dedup_key, path)
                    return _ARCHIVED_UNPROVEN
        return None

    def _candidate_names(self, company: str, title: str, location: str) -> tuple[list[str], bool]:
        """The name candidates for ONE (company, title, location) triple, and whether that
        triple is CAPPED -- bare, location-suffixed (if `location`), digest-suffixed (if
        capped). This is the ONLY place a lead's note names are constructed; the older
        `_path_for` was a second, partial copy of the bare form and is gone, so the three
        candidates can no longer drift out of step with each other or with candidate 1."""
        stem = f"{company} - {title}"
        capped = len(_sanitize(stem)) > _CHAR_CAP
        names = [self._note_name(stem)]
        if location:
            names.append(self._note_name(stem, location))
        if capped:
            names.append(self._note_name(stem, _title_digest(title)))
        return names, capped

    def _frontmatter_name(self, note) -> tuple[str | None, str | None]:
        """(the name this note's FRONTMATTER would mint, the placeholder the CURRENT name was
        minted from). (None, None) means the current name is not one THIS STORE minted from a
        placeholder company -- leave the note alone entirely. (None, head) means it is, but the
        frontmatter still offers nothing better than the same placeholder.

        The qualification is an exact RE-DERIVATION, never a " - " prefix heuristic: the current
        stem must be byte-identical to one of _candidate_names' own outputs when called with the
        PLACEHOLDER head. That is what makes a human-renamed note invisible to this pass by
        construction, and makes a company that merely CONTAINS " - " impossible to mis-split.

        _candidate_names itself is never touched here and never learns about frontmatter -- it
        keeps deriving names from the scraped Lead, which is what protects every legacy
        archived_from_note stamp in _merged/ (see _archived_match). Re-deriving names from
        frontmatter was tried and abandoned there for exactly this reason (see _archived_match's
        docstring) -- and the FAILURE DIRECTION differs on purpose: there, a failed re-derivation
        resurrects a merged-away lead (fail-open, on the one arm that must never fail open); here,
        a failed re-derivation just leaves the note unrenamed (fail-closed, to the status quo).

        The placeholder-head check is `_is_placeholder_head`, never the bare `is_placeholder_company`
        import: `head` comes from the note's FILENAME stem, which already went through `_sanitize`
        when the note was created, and `_sanitize` maps every filename-illegal character (`/`
        among them) to `-`. A note whose company was "N/A" is therefore seated on disk as
        "N-A - <role>.md", and `is_placeholder_company("N-A")` is False -- "n-a" is not itself a
        NON_ANSWER_COMPANIES member, only "n/a" is (CodeRabbit finding 3, #151). See
        `_is_placeholder_head`'s own docstring for why the fix sanitizes the CANDIDATE side rather
        than trying to invert `_sanitize` on `head`.
        """
        stem = note.slug
        head, sep, _ = stem.partition(_SEP)
        if not sep or not _is_placeholder_head(head):
            return None, None
        role = note.fm.get("role", "")
        location = note.fm.get("location", "")
        if not role:
            return None, None
        minted, _capped = self._candidate_names(head, role, location)
        if stem not in minted:
            return None, None
        company = note.fm.get("company", "")
        if is_placeholder_company(company):
            return None, head
        fresh, _capped = self._candidate_names(company, role, location)
        return fresh[0], head

    def _resolve_path(self, lead: Lead) -> tuple[str | None, str]:
        """The candidate walk (see _resolve_candidates), with the scan set re-derived from
        disk before any verdict reached through `_locate` finding NOTHING is allowed to
        stand -- create and both archive arms.

        Without that re-derive the cached directory list wedges the store PERMANENTLY, which
        is not the bounded cost the cache was justified by. The cache is filled by the FIRST
        `_locate` this store performs -- which is this very walk, on the run's first lead --
        and by nothing else: `read_leads` and `normalize_all_statuses` call `_walk` directly
        and leave it None, and the ingest sink never reads before it writes at all. So from
        the second lead on it is a snapshot, and a human who files a note into a NEW
        subfolder while the run is in progress is invisible to _locate from then on: the
        very next lead of the same identity is CREATED at the root name. Measured, that is
        sluice's OWN duplicate rather than a hand-made one, and it does not converge: from
        the next run on, both twins are visible, the candidate resolves to two notes, and
        `upsert` REFUSES the lead for good while its last_seen stays frozen -- which, with
        `lead_ttl_days` set, then ages it into the stale set and offers a twin for dismissal.
        The create-race loop it was likened to converges instead: a re-resolve SEES the raced
        note and updates it.

        The archive arms need it for the SAME reason and were the arms it first missed:
        `_ARCHIVED` and `_ARCHIVED_UNPROVEN` leave _resolve_candidates from the identical
        `if not found:` branch -- reached precisely when _locate saw nothing, which is what a
        stale list manufactures. Measured with the cache warmed, the note then filed into a
        new subfolder and an archived twin under `_merged/`: the fresh-cache verdict is
        `updated`, the stale-cache verdict `merged_away`. That is the RECORDED arm, so a
        stale list would put into `seen.db` -- which has no removal path -- a lead the fresh
        answer says is sitting right there, suppressing it permanently with its last_seen
        frozen.

        The three arms that DID identify a note are not re-derived, and the honest reason is
        cost, not impossibility. A stale list has two directions and `missed` reports only
        one of them: found NOWHERE. The other is found ONCE where a fresh list finds TWICE,
        and it moves an answer -- measured, cache warmed, a twin hand-filed at
        `Active/<the same name>.md` mid-run: `('update', missed=False)` -> `updated` and
        `('merge', missed=False)` -> `merged`, where the fresh answer is `refused` in both.
        So this is a RESIDUAL, stated rather than closed: closing it means re-deriving on
        the arms that carry a steady-state run, which is the per-lead walk the cache exists
        to remove (247 ms against 2.1 s per 500 updates, in the design spec).

        It is bounded in a way the create-arm wedge was not, which is why the trade goes
        this way. The write is a `last_seen` bump on one of two twins -- never-clobber, no
        note minted, nothing a later run cannot correct -- and the state is not silent:
        `read_leads` warns on it, naming both paths, so every command that reads leads says
        so. What it costs is that the twin enters `seen.db` (`updated` and `merged` are both
        on the sink's allowlist), so INGEST stops re-reporting the ambiguity, and the other
        twin's `last_seen` stays frozen. Sluice does not create this state: it arrives from
        a human with a filesystem, and repairing it belongs to the `leads reconcile` pass.

        Re-deriving per LEAD is the per-lead walk the cache exists to avoid, which is why
        the three identified arms skip it. The arms that pay are rare in a steady-state run,
        so the cost is one extra walk each rather than one per lead (measured in the design
        spec) -- and every one of them pays it, since a stale set is exactly the state in
        which the walk cannot know it is stale. `_ARCHIVED_UNPROVEN` re-reports every run
        until a human acts, so its extra walk recurs; it is one walk per affected lead per
        run, and it stops when the human acts.

        The set is compared, never the list ORDER: `_locate` reads every entry, so an
        order-only difference from one scandir to the next changes no answer and must not
        trigger a redundant second resolve (which would re-run _archived_match's listdir).

        Gated on `missed` -- the CONDITION, reported by `_resolve_candidates` itself -- and
        never on a hand-listed set of outcome strings. That whitelist has already gone stale
        once ON THIS BRANCH: it shipped as `("create",)` and the archive arms were added
        afterwards, with nothing red in between, because a stale scan set is invisible by
        construction -- both lists agree, both are wrong, and the wrong answer looks like a
        real one. The two sets are identical today (every arm reached through `if not
        found:` returns), which is exactly why only the flag can tell them apart tomorrow:
        an outcome added to that branch inherits the re-derive instead of silently opting
        out of it."""
        path, action, missed = self._resolve_candidates(lead)
        if not missed:
            return path, action
        # set(), which COPIES -- and it is kept even though _scan_dirs now returns a copy of
        # its own. The two guard the same failure at different rungs and neither implies the
        # other: _scan_dirs' copy stops a CALLER poisoning the cache, while this one stops
        # THIS comparison aliasing whatever _scan_dirs hands back, which would compare the
        # fresh list to itself the moment either helper was refactored to refresh in place --
        # vacuously equal, and the wedge silently back.
        before = set(self._scan_dirs())
        if set(self._rescan_dirs()) == before:
            return path, action
        path, action, _ = self._resolve_candidates(lead)
        return path, action

    def _resolve_candidates(self, lead: Lead) -> tuple[str | None, str, bool]:
        """Walk the nameable candidates and return (path, action, missed). Against an ACTIVE note,
        action is one of "create"/"update"/"merge"/"refuse". Candidate 1 is the clean
        `Company - Title` name (always); a location suffix (only when location is non-empty)
        and -- when the title is CAPPED -- a title-digest suffix add further candidates. Every
        verdict terminates in place EXCEPT DIFFERENT, which advances -- so a note is split only
        on PROVEN difference, never on the absence of evidence. Running out of candidates
        (every one a note proven different) is REFUSE: no path can be written without
        clobbering a different job, so path is None. See #5.

        A candidate is looked up across the SCAN SET (see _locate), not at one flat path, so
        a note the user filed in a subfolder is found and updated in place. A candidate
        resolving to TWO OR MORE notes is ambiguous identity and refuses -- see _locate.

        Running out of candidates with NONE proven different -- i.e. no active note exists at
        any of them -- does not mean create, though: `_archived_match` (#81) then probes
        `_merged/` by the same candidate names, and action can ALSO come back `_ARCHIVED` or
        `_ARCHIVED_UNPROVEN` (path None, same as refuse) when a human already merged this lead
        away. Only when that probe finds nothing either does the walk fall through to "create".

        `missed` is the third element and the reason it exists: did `_locate` come back EMPTY
        for a candidate? That -- not the outcome string it produced -- is the condition under
        which a stale directory list could have manufactured the answer, so it is what
        `_resolve_path` gates its re-derive on. It is True on every return out of the `if not
        found:` branch and False on every other, INCLUDING both refusals: the ambiguous one
        saw the candidate twice and the exhausted one saw a real note at every candidate, so
        neither was looking at a directory list that might be blind. Reported from here rather
        than inferred by the caller precisely so a NEW outcome added to that branch cannot
        opt out of the re-derive by not appearing in a list somewhere else.

        `capped` closes #5's same-location residual: when the 120-char cap drops part of the
        title, cand1 (and the location candidate, which shares that truncated prefix) can seat
        a DIFFERENT job. A stable digest of the FULL title is then a further discriminator,
        tried AFTER the location suffix -- so LOCATION (proven evidence) stays the primary
        split, a note #5 already placed at its location name is still found in place (no
        migration duplicate), and the digest only catches a same-location title collision the
        location suffix cannot. A short title carries its whole self in cand1, so `capped` is
        False and both the digest candidate and the `title_lost` advance below are dormant --
        short-title resolution is byte-identical to before. (`capped` measures the CHAR cap,
        not the byte-clamp, so a title under 120 chars but over NAME_MAX bytes still merges --
        a negligible ASCII-population sub-residual that fails toward merge, never a clobber.)"""
        names, capped = self._candidate_names(lead.company, lead.title, lead.location)
        for name in names:
            found = self._locate(name)
            if len(found) > 1:
                # Two notes claim one identity, so there is no safe write: bumping either
                # one's last_seen leaves the other to rot unnoticed. Refuse loudly and let
                # the sink keep the lead out of seen.db so it re-reports until a human
                # merges or renames.
                #
                # WHERE the pair comes from used to be answerable with "nothing sluice does
                # produces this -- it arrives by hand, from a copied note or a part-way
                # manual reorganisation". That is no longer true: sluice's own pre-#205
                # creates produced exactly this pair whenever a board changed an employer's
                # capitalisation, because `_locate` compared the name byte-for-byte and each
                # spelling seated its own note. Every store predating the fix still holds
                # those notes.
                #
                # How OFTEN such a pair reaches this arm is narrower than "now it resolves
                # here", which is what an earlier draft of this comment claimed. Measured
                # against a seeded pair: `_locate` probes the exact name FIRST, so a scrape
                # whose casing matches either note on disk returns one path and UPDATES
                # (silently -- the twin is untouched and unmentioned). Only a THIRD casing,
                # matching neither, falls through to the folded probe, sees both, and
                # refuses. A board that keeps sending the spelling that created the note
                # therefore never reaches this line, which is why the standing report on
                # such pairs lives in `read_leads` (walked every command, names `leads
                # dedupe --merge`, which already clusters them) rather than resting on a
                # refusal that fires only on a casing change.
                #
                # "Writes nothing" includes last_seen, and that reaches further than the
                # incoming lead: BOTH notes on disk stop being refreshed -- the real one as
                # much as the stray copy -- for as long as the duplicate sits there. Every
                # later scrape of this identity reaches the same candidate and refuses again,
                # so nothing else refreshes them either. With `lead_ttl_days` set they
                # therefore age into the stale set, where `cv` and `apply` decline them
                # (`skipped-stale`, `stale`) and `sluice leads expire` OFFERS them for
                # dismissal -- so a hand-made duplicate can end up presenting a live job for
                # expiry. The direction is still safe, which is why the refusal stands: the
                # TTL is off by default (`lead_ttl_days: 0` abstains), `leads expire` reports
                # before it writes and needs `--expire` to act at all, and the `dismiss` it
                # would write is triage-owned and reversible -- never a terminal. But the
                # staleness clock running on the SURVIVOR is a consequence of refusing, not
                # of the duplicate, so it is stated here rather than left to be re-derived.
                _log.warning("vault refused lead %r: %r resolves to %d notes (%s)",
                             lead.dedup_key, name, len(found), ", ".join(sorted(found)))
                return None, "refuse", False
            if not found:
                # #81. Returns None, or one of the TWO outcome strings -- never a bool: the
                # url-PROVEN/weaker distinction decides whether the lead enters seen.db,
                # which is irreversible in one direction, so a bool cannot carry it.
                archived = self._archived_match(names, lead, capped)
                if archived:
                    return None, archived, True
                # The WRITE FOLDER, not leads_dir: under `active_archive` a create lands
                # in Active/, which is where a `status: new` note belongs, so the note is
                # already reconciled the moment it exists. `_locate` searched the whole SCAN
                # SET above, so a note the user (or a previous flat install) left at the root
                # was already found and updated in place -- opting in never re-creates an
                # existing lead.
                return os.path.join(self._write_folder(), f"{name}.md"), "create", True
            path = found[0]
            inner, _ = _split_frontmatter(_read(path))
            # The url-proof is DISCARDED here on purpose: against an ACTIVE note a SAME
            # verdict terminates the walk identically however it was reached, so this
            # walk's behaviour is byte-identical to before the second element existed.
            # Only the archive probe splits on it (there the two outcomes differ in
            # whether the lead may enter seen.db, which is irreversible).
            action, _url_proven = self._reconcile(_fm_dict(inner), lead, capped)
            if action != "advance":
                return path, action, False
            # DIFFERENT location, or a capped-title mismatch -> advance to the next candidate
        return None, "refuse", False

    def ensure_stfolder(self) -> None:
        """Syncthing silently refuses to sync a vault root missing its .stfolder
        marker (state=idle, never drains). Recreate it defensively."""
        os.makedirs(os.path.join(self.dir, ".stfolder"), exist_ok=True)

    # ── read ─────────────────────────────────────────────────────────────────
    def read_leads(self, statuses: set | None = None) -> list:
        """Every lead note as a VaultNote (frontmatter parsed, status normalized),
        filtered to `statuses` (compared against the normalized status) when
        given. This is the read seam triage consumes; the sink still writes Leads.

        Walks the SCAN SET (see _walk), not one flat directory, so a note the user filed in
        a subfolder is still a lead. Two consequences worth stating because both are load-
        bearing: `_merged/` is pruned by NAME there rather than surviving on the accident
        that os.listdir is flat (#81), and a file carrying neither company nor role is
        skipped, or a user's interview-prep notes would be triaged as leads.

        Ordered by full path. For a flat store that is byte-identical to the previous
        sorted(os.listdir(...)), so nothing downstream sees an ordering change."""
        out: list = []
        # `_is_dir`, not os.path.isdir. This early return exists for ONE case -- leads_dir
        # before the first upsert, where _walk's onerror=_reraise would otherwise raise a
        # FileNotFoundError at every caller on a fresh vault. os.path.isdir also answers
        # False to a leads_dir it cannot STAT, and that answer is a silent empty read of a
        # vault full of notes: every lead invisible to triage, cv, apply and track, with no
        # error anywhere. Same rule, same reason as _scan_dirs; see _is_dir.
        if not _is_dir(self.leads_dir):
            return out
        want = {_status.normalize(s) for s in statuses} if statuses else None
        paths = []
        for dirpath, filenames in self._walk():
            paths.extend(os.path.join(dirpath, n) for n in filenames if n.endswith(".md"))
        for path in sorted(paths):
            try:
                inner, body = _split_frontmatter(_read(path))
            except OSError:
                continue
            fm = _fm_dict(inner)
            if not _is_lead_note(fm):
                continue
            st = _status.normalize(fm.get("status", ""))
            if want is not None and st not in want:
                continue
            out.append(LeadNote(ref=path, slug=self._slug_for(path),
                                fm=fm, body=body, status=st))
        # The WRITE path refuses an ambiguous candidate and names both colliding paths
        # (_resolve_path); the read path has no such option -- dropping a lead is worse than
        # returning it -- so it is loud instead. On a flat store slug uniqueness held by
        # CONSTRUCTION (one directory cannot hold two files at one basename, and _slug_for is
        # the basename); a recursive scan removes that, and consumers that key a dict on slug
        # equality then keep whichever twin they saw last. Computed per RETURNED list, because
        # that is the set a caller indexes: a twin filtered out by `statuses` is not one of
        # its keys.
        #
        # Deduped per store, on the discipline the symlink warning uses -- but unlike that
        # one this has no measured case, and stating it as though it did was the claim this
        # comment is here to correct. Enumerated across all 11 `read_leads` call sites, no
        # shipped command reads ONE status set twice through a single store: `apply prep
        # --all-shortlist` reads once (select_all) and every `--lead` form reads once
        # (select.resolve), cv/triage/expire/dedupe/confirm read once each, and `track run`'s
        # two reads take DISJOINT sets (APPLICATION_OWNED and {"shortlist"}), so a twin lands
        # in exactly one of them. So the suppression is forward-looking: it costs one set,
        # and the moment a command does read a set twice an unchanged vault would otherwise
        # say the same thing twice -- the noise the empty-symlink case is deliberately kept
        # out of. The key carries the REFS, so it suppresses only a repeat of the SAME fact:
        # a later read whose filter surfaces a third twin at that slug is new information
        # and is still said.
        #
        # `track/receipt.py` legislates the opposite move on an inert guard -- "a guard for a
        # state the code cannot reach is an inert guard, so it is gone rather than kept with a
        # comment claiming it fires" -- and this is KEPT, so the difference has to be stated
        # rather than assumed. Two things separate them.
        #
        # WHERE the impossibility lives. There it is LOCAL and structural: the two tiers are
        # keyed off the sender and disjoint by construction WITHIN that function, so nothing
        # outside it can make the state reachable without editing it, and an author editing it
        # is looking straight at the reason. Here it rests on a survey of EXTERNAL callers --
        # the 11 sites above, in five modules -- so a new command that reads one set twice
        # makes it reachable without touching this file and with nothing going red.
        #
        # WHETHER it can be witnessed. receipt.py's guard could not be, by anything: reaching
        # it meant breaking the disjointness that made it inert, which is exactly why it had
        # become prose rather than a check, and prose is what that ruling is about. This one
        # is exercised through the public read path by TWO tests that read twice on one
        # store, and one of those two WITNESSES the suppression: deleting the `if key in
        # self._warned_dup_slugs: continue` arm reddens
        # test_a_duplicate_slug_is_warned_about_once_per_store and nothing else in the suite
        # (measured; the other reads twice but sees a THIRD twin on the second read, so its
        # two lines are two distinct facts and it stays green either way). So this is a live
        # branch with a real witness, not a claim about one.
        #
        # And the stakes run opposite ways. There the dead branch sat on the path to an
        # irreversible `applied`, wearing a comment that said it fired -- a false sense of
        # safety where being wrong cannot be undone. Here the guard suppresses log noise, its
        # own comment says outright that it suppresses nothing today, and the cost of being
        # wrong in either direction is a repeated line.
        by_slug: dict = {}
        for note in out:
            by_slug.setdefault(note.slug, []).append(note.ref)
        for slug, refs in by_slug.items():
            if len(refs) > 1:
                key = (slug, tuple(sorted(refs)))
                if key in self._warned_dup_slugs:
                    continue
                self._warned_dup_slugs.add(key)
                _log.warning("vault: slug %r is claimed by %d notes (%s); consumers keyed on "
                             "it will see only one", slug, len(refs), ", ".join(sorted(refs)))
        # #205, and a DIFFERENT fact from the one above with a different consequence and a
        # different remedy, which is why it is a second sweep and a second message rather
        # than a widening of the first. Above: several notes at ONE name, so a consumer
        # keyed on slug sees one of them and the others are invisible. Here: several notes
        # whose names differ ONLY by case, so every consumer sees them ALL, as separate
        # leads -- with separate status. That is the harm #205 reports: one spelling held a
        # live `shortlist` at score 86 while its twin held a `dismiss`, so dismissing the
        # role under one spelling did not stop it returning as `new` under the other. It
        # also wedges replication, silently: a case-insensitive filesystem cannot hold the
        # pair, and Syncthing keeps reporting the folder `state=idle` while delivering
        # neither note.
        #
        # REPORTED here rather than refused at the write path. `_locate` probes the exact
        # name first and only folds on a miss (see there), so a store that already holds a
        # pair keeps updating whichever twin the scrape names, silently. Making the write
        # path notice instead would mean folding on every lookup including the steady-state
        # hit -- ~7us to ~1.9ms over a 3190-note store, on every lead. This walk is already
        # being paid, so the report costs one grouping over a list that exists.
        #
        # The remedy already ships and is NOT new UX: `cluster_duplicates` normalizes
        # company and role through `_norm_tokens`, which casefolds, so `job-sluice leads
        # dedupe` already puts such a pair in one cluster and offers `--merge` (measured).
        # Only the signal was missing, so the message names that command rather than
        # describing a repair the user has to invent.
        #
        # Keyed on the FOLD of the slug (`_fold_note_name`, the same rule `_locate` and
        # `_archived_match` resolve by), so what the read path calls a collision is exactly
        # what the write path calls one identity. Groups are reported only when they hold
        # more than one DISTINCT slug: a group whose slugs are all identical is the
        # first sweep's fact, already said above, and saying it twice in two vocabularies
        # would teach a reader to skip both.
        by_fold: dict = {}
        for note in out:
            by_fold.setdefault(_fold_note_name(note.slug), set()).add(note.slug)
        for folded, slugs in by_fold.items():
            if len(slugs) < 2:
                continue
            key = ("case", folded, tuple(sorted(slugs)))
            if key in self._warned_dup_slugs:
                continue
            self._warned_dup_slugs.add(key)
            _log.warning(
                "vault: %d notes differ only by capitalisation (%s); they are one job held "
                "as separate leads with separate status, and a case-insensitive filesystem "
                "cannot sync the set. `job-sluice leads dedupe` clusters them and `--merge` "
                "resolves it", len(slugs), ", ".join(sorted(slugs)))
        return out

    def update_fields(self, ref, fields: dict, *,
                      append_note: str | None = None,
                      note_tag: str | None = None,
                      require_status: frozenset | None = None,
                      require_blank: frozenset | None = None,
                      blank_values: frozenset | None = None,
                      require_unchanged: dict | None = None) -> bool:
        """Surgically set frontmatter keys (literal YAML scalars), body byte-for-byte
        intact. Optionally append a guarded note to relevance_notes (skipped if note_tag
        is present, so re-runs are idempotent). Routed through _cas_write: the edit is
        re-derived from the CURRENT note on each attempt, so a concurrent writer's other
        keys and body survive. May raise VaultConflict on sustained conflict (#16).

        `require_unchanged` (#223): a {key: expected} map re-read from the FRESH note;
        the write is refused unless every key still holds exactly what the caller read.
        `require_blank` asks "is this still empty", which fits a caller filling a hole;
        this fits one REPLACING a value. Values are compared RAW, so pass what
        `note.fm[key]` gave you rather than a normalised form -- a folded value would
        compare against the stored spelling and never match, and the refusal is
        indistinguishable from a no-op to the caller.

        `require_status` (#9): when given, re-read the status from the FRESH note and
        write nothing unless it is in that set. Returns whether a write happened.

        `require_blank` (#109): the same discipline for a NON-status field -- re-read each
        named key from the FRESH note and write nothing unless every one of them is empty.
        It exists because #109 decides "company is blank, so filling it in is safe" from a
        read_leads() snapshot and then spends SECONDS on a tier-2 page fetch before
        writing; a human typing the company into Obsidian inside that window would
        otherwise have it silently replaced by the scraped value. Refusal is on PRESENCE,
        not on inequality, so it also refuses a DIFFERENT value -- which is what separates
        it from the benign already-current no-op this method already reports as False.
        Generalised over field NAMES rather than hardcoded to `company` for the same reason
        `require_status` takes a set: the next unmediated-external-content writer needs the
        same guard, and a second write function would be a second CodeQL sink.

        `blank_values` (#151) widens what `require_blank` accepts as blank: a stored value
        that FOLDS (via `fold_company_answer` -- strip, drop a trailing `.`/`!`, casefold)
        into this set counts as blank alongside empty/whitespace-only, so a note already
        reading a placeholder like "Unknown" or "Confidential" can be repaired the same way
        a genuinely blank one can. Only the fresh STORED value is folded -- `blank_values`
        itself is compared verbatim, so its members must already be pre-folded by the
        caller, exactly as `require_status` takes its own set as already-canonical rather
        than normalizing it too. `core.leads.NON_ANSWER_COMPANIES` is the one production
        caller and is built lowercase with no trailing punctuation for this reason. It
        widens exactly one thing -- membership in the set -- and nothing else: a value that
        merely differs from the one being written is still refused, so a human's real
        answer typed into the same field mid-run is unaffected. Given without
        `require_blank` it gates nothing; the presence check it widens simply never runs.

        Both guards assume a well-formed note: `require_status`/`require_blank` read via
        `_fm_value` (FIRST occurrence of `key:`), while `note.fm` -- what a caller's own
        blank/status check runs against before ever calling this method -- is built via
        `_fm_dict` (LAST occurrence wins on a duplicate key). `_set_fm` cannot itself create a
        duplicate (it replaces the first match or appends if absent), so this only matters for
        a hand-edited note carrying the same key twice. Traced for every 2-occurrence
        combination: the caller's own pre-check (via `note.fm`) always runs first and already
        gates on the SAME field this method re-checks, so the two functions' disagreement is
        never reachable as a silent overwrite through today's single call site -- the worst
        outcome is a write correctly refused. Not a guarantee for any FUTURE call site that
        checks a field this method does not also gate on."""
        def transform(text: str) -> str:
            inner, body = _split_frontmatter(text)
            if inner is None:
                inner, body = "", text
            # Decided HERE, against the fresh bytes -- never by the caller. A caller
            # checking the LeadNote it enumerated is checking a snapshot that is stale by
            # construction, and probed against a real vault that guard is BYTE-IDENTICAL
            # to having no guard at all: both write over an `applied` note. `leads
            # expire`'s read loop is a window in which a lead can enter the application
            # lifecycle (via `apply record` or a #10 receipt), and only a re-read inside
            # the transform can see it. Returning `text` unchanged is a genuine no-op,
            # which _cas_write already reports as False -- no new machinery needed.
            if require_status is not None and \
                    _status.normalize(_fm_value(inner, "status")) not in require_status:
                return text
            # Same freshness rule, same reason (see the docstring): decided HERE against
            # the fresh bytes, never by the caller, because the caller's snapshot predates
            # the window this guard exists to cover. `_counts_as_blank` folds `blank_values`
            # into the presence check too, on the SAME fresh `inner` -- there is no separate
            # read to go stale.
            if require_blank is not None and any(
                    not _counts_as_blank(_fm_value(inner, key), blank_values)
                    for key in require_blank):
                return text
            # Third guard, same freshness rule and the same reason (#223). `require_blank`
            # asks "is this still empty"; this asks "is this still EXACTLY what I read",
            # which is what a caller needs when it is replacing a value rather than
            # filling a hole. Measured before it existed: triage's role_type write-back
            # spends seconds on a dossier fetch between reading the note and writing it,
            # and a human typing their own `role_type` into Obsidian in that window was
            # overwritten -- including the `declared` provenance that is otherwise never
            # overwritten, because the decision not to overwrite was itself made against
            # the stale snapshot.
            #
            # RAW comparison against `_fm_value`, so callers pass the value they READ
            # rather than a normalised form: `_fm_value` and `_fm_dict` strip quotes
            # identically, so `note.fm[key]` and this read agree on a well-formed note.
            # A caller that passed a folded value would compare `contract` against a
            # stored `Contract` and the write would never land -- silently, which is the
            # dangerous direction for a guard that reports refusal and no-op alike.
            if require_unchanged is not None and any(
                    _fm_value(inner, key) != expected
                    for key, expected in require_unchanged.items()):
                return text
            for key, literal in fields.items():
                inner = _set_fm(inner, key, literal)
            if append_note and note_tag:
                current = _fm_value(inner, "relevance_notes")
                if note_tag not in current:
                    # Guarded at the SINK, not at each caller. `append_note` lands in
                    # `relevance_notes`, which is FRONTMATTER despite the parameter reading
                    # like a body append -- and every caller feeds it model output: triage's
                    # `fit_reasoning`/`concerns`/`recommended_next_action`, its classification
                    # `reason`, and app.py's dismiss note. Executed before this guard: a
                    # `fit_reasoning` of "---\nstatus: rejected\n---" broke out of the quoted
                    # scalar and the note re-read as `status: rejected`. Model output could
                    # regress a lead's status, which is the never-regress invariant.
                    #
                    # One guard here closes all three callers AND any future one. Guarding
                    # each caller instead is how `triage/apply.py`'s sibling fields were fixed
                    # while this one -- the same class, one frame down -- was missed.
                    #
                    # `frontmatter_safe` ABSTAINS rather than mangling, and abstaining on the
                    # note must not cost the status write the caller came here to make: the
                    # other fields still land, and this key is simply left as it was.
                    merged = (current + " " + append_note).strip()
                    safe_merged = frontmatter_safe(merged)
                    if safe_merged:
                        inner = _set_fm(inner, "relevance_notes", f'"{safe_merged}"')
                    else:
                        _log.warning(
                            "vault: relevance_notes not appended for %s -- the note text was "
                            "unsafe for frontmatter", ref)
            return f"---\n{inner}\n---\n{body}"
        return _cas_write(ref, transform)

    def append_body_section(self, ref, tag: str, section_md: str) -> bool:
        """Append a markdown section to the body, idempotently: if `tag` is anywhere in
        the FRESH file, do nothing and return False. Routed through _cas_write, so the
        tag re-check runs against current content. May raise VaultConflict (#16)."""
        def transform(text: str) -> str:
            if tag in text:
                return text
            sep = "" if text.endswith("\n") else "\n"
            return f"{text}{sep}\n{section_md}\n"
        return _cas_write(ref, transform)

    # ── cv sub-app reads/writes ──────────────────────────────────────────────
    def _kind(self, kind: str):
        """The EvidenceKind for `kind`, or a raise naming the valid ones.

        Fail loudly at construction, the same rule _select_backend follows. A typo'd
        kind returning [] would buy the `skipped-gate` misreport described in
        read_evidence's contract, paid for with a real backend call.
        """
        try:
            return EVIDENCE_KINDS[kind]
        except KeyError:
            raise ValueError(
                f"unknown evidence kind {kind!r}; valid kinds are "
                f"{', '.join(sorted(EVIDENCE_KINDS))}") from None

    def _evidence_dir(self, kind: str, *, inbox: bool = False) -> str:
        """Resolve one evidence directory, refusing a symlinked one on EVERY path.

        The refusal lives HERE, in the one resolver every read and every write already
        goes through, rather than in a caller's body. It used to sit in
        `propose_evidence`, and that asymmetry was reproducible harm, not a tidiness
        point: with `_inbox -> <somewhere outside the vault>`, `propose_evidence`
        refused, `read_pending_evidence` listed the foreign directory's entries anyway,
        and `verify_evidence` promoted one and then `os.unlink`ed the source -- deleting
        a file outside the vault. Measured end to end (#164 whole-branch review, H1):
        propose refused, the pending listing showed `['alpha']`, verify returned True,
        and the victim file was gone.

        EVERY component below `self.dir` is checked, outermost first -- not an
        enumerated list of the levels someone happened to think of. That distinction is
        this guard's whole history: it began as a check on `_inbox` alone, review found
        the identical harm through a symlinked KIND directory (`_inbox` is then an
        ordinary subdirectory of a foreign tree, so `os.path.islink` on it is False),
        the check grew a second named level, and review then found the identical harm
        one level further out again. Measured on `Job Applications -> <outside the
        vault>` -- the first component of every kind's relpath, and the one level a
        two-name check did not reach: `read_pending_evidence` listed `['alpha']`, `verify_evidence`
        returned True, and its `os.unlink` deleted a file outside the vault. Walking the
        components subsumes both named levels and every future one, so a fourth kind or
        a deeper relpath needs no edit here.

        Outermost first, and the reason only bites when MORE THAN ONE component is a
        link -- a symlinked ancestor with a symlinked `_inbox` nested inside the foreign
        tree. The message carries exactly one instruction ("move the real folder into
        the vault"), and moving the inner folder changes nothing while the ancestor
        still points away, so the outermost link is the only one that instruction can
        act on. With a single link anywhere the two directions agree, which is why the
        test that pins this nests two -- measured, reversing the walk against a
        one-link fixture left the whole suite green.

        `self.dir` ITSELF is deliberately not probed. It is the vault directory the user
        named -- an env var, a config key, or `--vault` -- so a symlink there is the
        user pointing this tool at their own Obsidian folder, not a path escaping a
        boundary they set. The boundary this guard defends is "inside the vault the user
        named", and the vault root is that boundary rather than something within it.

        islink, never realpath: resolving would make the symlink structurally invisible
        (`_inbox -> ..` would put every proposal straight into the citable directory
        with nothing said), which is the mirror of the reason `_write_folder` already
        refuses a symlinked lead write folder. And it runs BEFORE any `os.makedirs`
        by construction now -- a caller cannot name the directory without passing
        through this check first -- rather than by a caller remembering the order.

        ACCEPTED RESIDUAL, stated rather than implied: this is a probe, not a lock. A
        symlink swapped in AFTER the walk and before `verify_evidence`'s `os.unlink`
        still escapes (it needs byte-identical content to survive the compare-and-set
        first). That is the same accepted class as `_cas_write`'s own compare -> replace
        micro-window, and for the same reason -- no portable stdlib call resolves a path
        and operates on it atomically. Closing the ROUTINE case is the claim; a store
        cannot defend against a filesystem being rearranged underneath it mid-call.
        """
        spec = self._kind(kind)
        # This reproduces `_doc_path`'s split-and-rejoin one component at a time, rather
        # than calling it and probing the result, because the guard needs every
        # INTERMEDIATE path and that method only returns the last one. The registry's
        # relpath is the contract's "/"-separated DOCUMENT KEY (#164), so splitting on
        # "/" and re-joining with os.path.join is what makes it resolve on Windows too;
        # a raw os.path.join on the key happens to work on POSIX and never does there.
        components = spec.relpath.split("/") + ([INBOX_SUBDIR] if inbox else [])
        path = self.dir
        for component in components:
            path = os.path.join(path, component)
            if os.path.islink(path):
                raise OSError(
                    f"evidence directory {path!r} is a symlink; refusing to write "
                    f"through it, or to read an entry from behind it -- move the real "
                    f"folder into the vault")
        return path

    def _evidence_entries(self, kind: str, base: str) -> list[dict]:
        """Every `.md` DIRECTLY in `base`, as entry dicts.

        `os.listdir` is flat, so `_inbox/` -- a subdirectory -- is never descended into
        and its entries are invisible here. That is the mechanism, and it is pinned by
        test_verified_only_filters_and_an_inbox_entry_is_invisible_at_both_settings.
        If this ever becomes a recursive walk, it needs a _PRIVATE_SUBDIRS-style prune
        by name, exactly like the lead scan gained at #1 -- without one, every
        unverified proposal becomes citable.

        `_is_dir`, not os.path.isdir: an unreadable directory must be loud, never read
        as empty.
        """
        spec = self._kind(kind)
        # Which frontmatter key fills each text floor key, for THIS kind. Derived from
        # the registry rather than the four hardcoded `fm.get("Company", ...)` lookups
        # this used to carry: those were an identity mapping on title-cased names, and
        # `skills`' own field names collide with none of them -- so `best_for` was the
        # empty string for every skill and cv/bundle.py's rank() scored a `platform`
        # skill zero against the keyword `platform` (#164 review, M3). See
        # `EvidenceKind.floor_map` for what each kind maps and, for `skills`, what it
        # deliberately does NOT.
        sources = spec.floor_sources()
        out = []
        if not _is_dir(base):
            return out
        for name in sorted(os.listdir(base)):
            if not name.endswith(".md"):
                continue
            # Refuses a symlinked entry FILE, the other half of the class
            # `_evidence_dir` closes for directories -- see `_evidence_entry_path`.
            # Reached from the CITABLE listing as well as the pending one, because a
            # symlinked entry sitting in the kind directory feeds the fabrication gate
            # content from outside the vault without any promotion happening at all.
            path = _evidence_entry_path(base, name)
            inner, body = _split_frontmatter(_read(path))
            fm = _parse_fm_spaced(inner)
            out.append({
                "path": path, "title": name[:-3],
                **{floor: fm.get(key, "") for floor, key in sources.items()},
                "verified": fm.get(VERIFIED_KEY) or None, "body": body.strip(),
                # The keys above are a FLOOR, kept so cv/bundle.py's rank() and
                # assign_codes work on every kind unchanged. `fields` carries the kind's
                # OWN frontmatter under its own names, which is where a field with no
                # floor analogue (skills' Proficiency/Evidence/Signal Value) stays
                # reachable.
                "fields": {k: fm.get(k, "") for k in spec.fields},
            })
        return out

    def read_evidence(self, kind: str, verified_only: bool = True) -> list[dict]:
        """See Store.read_evidence."""
        entries = self._evidence_entries(kind, self._evidence_dir(kind))
        return [e for e in entries if e["verified"]] if verified_only else entries

    def read_pending_evidence(self, kind: str) -> list[dict]:
        """See Store.read_pending_evidence. No verified filter, deliberately.

        An `_inbox/` entry CAN carry the key -- a human hand-placing one is a first-class
        workflow for this tool -- and filtering on it would make that entry vanish from
        `<kind> list --pending`, from the queue `verify` offers, and from `doctor`'s
        pending count ALL AT ONCE, while it sits in `_inbox/` doing nothing and NOT
        citable (`read_evidence` cannot see `_inbox/` at all). Invisible in every place
        that could report it is the silent-inert state this reader exists to surface.

        This used to name "a crash between verify's stamp and its unlink" as how such an
        entry arises. Measured (a simulated crash immediately after the citable write):
        that is NOT a path to it -- `verify_evidence` stamps the DESTINATION copy and
        never writes to the source, so the inbox copy survives a crash exactly as it was,
        unstamped, and this method reports it with or without a filter. The hand-placed
        entry above is the reachable case, which is what the test pins."""
        return self._evidence_entries(kind, self._evidence_dir(kind, inbox=True))

    def read_pending_evidence_text(self, kind: str, name) -> str:
        """See Store.read_pending_evidence_text.

        A FRESH read on every call -- `_read`, not a value cached from the listing --
        because this is what `verify_evidence`'s compare-and-set is handed, and a
        snapshot taken when the queue was built is stale by construction (the same
        reason `update_fields`' `require_status` re-reads rather than trusting the
        enumerated LeadNote). Reached through `_evidence_component`, so the read side
        keeps the same containment the write side has and a `name` naming a path
        refuses here too instead of only at promotion time.
        """
        return _read(_evidence_entry_path(self._evidence_dir(kind, inbox=True),
                                          f"{_evidence_component(name)}.md"))

    def propose_evidence(self, kind: str, *, name, fields, body: str = "") -> str:
        """See Store.propose_evidence. This store's opaque handle IS the written path,
        the same way `write_document`'s is -- but the CONTRACT promises only a non-empty
        opaque handle, so no contract-bound caller may treat it as a path.

        NEVER stamps VERIFIED_KEY -- `_render_evidence_note` rejects an undeclared field
        key BY NAME, which is what actually holds that, since `fields` is a caller-supplied
        mapping -- and always lands under INBOX_SUBDIR, which `read_evidence` cannot see.
        Exclusive create, so a taken name refuses rather than overwriting a proposal
        already there -- and a name already taken in the CITABLE set refuses too, see below.
        """
        spec = self._kind(kind)
        slug = evidence_slug(name)
        text = _render_evidence_note(spec, dict(fields or {}), body)
        # The symlink refusal is in `_evidence_dir` itself, not here: it must bind the
        # READ and VERIFY paths too, and a guard in this body did not (see that method's
        # own docstring for the file-deleted-outside-the-vault probe). It still runs
        # before the makedirs below -- now by construction rather than by ordering.
        inbox = self._evidence_dir(kind, inbox=True)
        # A name already taken in the CITABLE set is refused HERE, at propose time, where
        # the user is typing the name and can pick another. This used to probe the inbox
        # ALONE, so `add alpha` after `alpha` had been verified succeeded, and the clash
        # surfaced later, from inside an interactive `verify`, as the promotion's
        # exclusive create raising a bare FileExistsError (#164 review, H2).
        #
        # `lexists`, not `exists`: a DANGLING symlink at that name still makes the
        # promotion's exclusive create fail, so reporting the name free would only defer
        # the identical clash. This is a probe, not a lock -- the exclusive create below
        # and `verify_evidence`'s own are what actually hold the property under a race;
        # this only moves the ordinary case to where it is actionable.
        if os.path.lexists(os.path.join(self._evidence_dir(kind), f"{slug}.md")):
            raise FileExistsError(
                f"a verified {kind} entry is already named {slug!r}; pick another name, "
                f"or edit that entry in the vault directly")
        os.makedirs(inbox, exist_ok=True)
        path = os.path.join(inbox, f"{slug}.md")
        try:
            _write(path, text, exclusive=True)
        except FileExistsError:
            # NAMED, rather than the bare `[Errno 17] File exists: <path>` that an
            # exclusive open() raises -- the caller (a CLI handler, the init wizard)
            # prints this message verbatim, and an errno names neither the entry nor
            # anything to do about it.
            raise FileExistsError(f"{slug!r} is already proposed") from None
        return path

    def verify_evidence(self, kind: str, name, *, today: str, reviewed: str) -> bool:
        """See Store.verify_evidence. True promoted, False abstained.

        `name` is the entry's ON-DISK identity -- the `title` read_pending_evidence
        reported -- and is CHECKED here rather than re-reduced; `_evidence_component`
        records the bug that distinction fixes and the containment it keeps. The
        promoted copy therefore keeps that same basename, so a human who filed
        `My Entry.md` by hand finds `My Entry.md` in the citable directory afterwards
        rather than a renamed note they did not create.

        NOT _reserve_and_move. That primitive moves "whatever `src` names at that
        instant", which is right for merge_cluster (a note moves wholesale, any content
        is fine) and wrong here: a human approved SPECIFIC BYTES, and carrying an edit
        made after that approval would put unreviewed content into the citable set.

        Order matters and is measured:
          1. RESOLVE the source through `_evidence_component` and `_evidence_entry_path`,
             so a `name` that is not a bare component, a symlinked directory anywhere below
             the vault root, and a symlinked entry FILE all refuse before anything is read
             -- and therefore before step 5 could `os.unlink` through one of them. (This
             list used to start at 2, with no step 1 anywhere; the code always had one.)
          2. re-read and compare against `reviewed` -- compare-and-set, the discipline
             update_fields' require_status uses, so a human promotes what they saw.
          3. stamp.
          4. exclusive create at the destination. A taken name refuses HERE, before the
             source is touched, so a routine clash cannot strand a stamped inbox entry.
             The entry therefore never exists in the verified directory unstamped.
          5. unlink the source ONLY while it still matches. This resembles the
             os.link+os.unlink shape _reserve_and_move's docstring records as rejected on
             #23, and the difference is the harm: #23's rejection was that a concurrent
             save landing between the two is DELETED. Here it is KEPT (in the inbox, and
             reported), so the residual is a duplicate, not a loss.
        """
        name = _evidence_component(name)
        # `_evidence_entry_path`, not a raw join: a symlinked source is promoted content
        # from outside the vault, and the unlink below would remove only the link. The
        # refusal has to be HERE and not merely on the listing, for the same reason the
        # directory guard sits in the resolver -- an entry reaches this method by name,
        # and a caller who never listed the inbox never passes the listing's copy.
        src = _evidence_entry_path(self._evidence_dir(kind, inbox=True), f"{name}.md")
        current = _read(src)  # FileNotFoundError propagates: no such pending entry
        if current != reviewed:
            return False
        inner, body = _split_frontmatter(current)
        # Checked HERE too, not only at propose time: this is the moment an entry
        # becomes citable, and an entry can reach it without ever having gone through
        # propose_evidence (see `_refuse_citation_shaped_body` for the measured
        # hand-placed case). Positioned AFTER the compare-and-set, so an entry edited
        # since review is still reported as the abstention it is rather than as this
        # refusal -- the two need different answers from the human.
        _refuse_citation_shaped_body(body)
        stamped = f"---\n{_set_fm(inner or '', VERIFIED_KEY, today)}\n---\n{body}"
        # _evidence_dir(kind), not a second self._doc_path(spec.relpath) spelling of the
        # same directory: two spellings of one path is the exact hazard Task 1's _EXP_SUBDIR
        # existed to remove, and the concrete cost is that a guard living in _evidence_dir --
        # the symlink refusal IS one now -- would silently not cover this write path if it
        # kept its own copy of the path expression. That refusal used to sit in
        # propose_evidence's body instead, which is why sharing the path EXPRESSION was not
        # enough on its own: this method reached the same directory and none of the guard.
        dest_dir = self._evidence_dir(kind)
        os.makedirs(dest_dir, exist_ok=True)
        _write(os.path.join(dest_dir, f"{name}.md"), stamped, exclusive=True)
        # Everything past the citable write is CLEANUP, and the promotion has already
        # happened: the approved bytes are stamped and in the citable directory, and this
        # method's contract is `True` for exactly that. So a source that is GONE by the time
        # this runs -- vanished between the write above and the read below, or between the
        # read and the unlink -- is the end state a successful promotion produces, not a
        # failure. Unguarded, the FileNotFoundError propagated into
        # `Sluice.verify_evidence_interactive`'s per-item `except (OSError, ValueError)`,
        # and the user was told `not promoted: <title> -- it is no longer in the inbox` and
        # given exit 1 for an entry that IS citable with the bytes they approved (round-2
        # review, L2). Reporting a completed promotion as a refusal is the reassuring-in-
        # reverse direction: the natural response is to re-add the entry, which then clashes
        # with the one already there.
        #
        # FileNotFoundError ONLY, and only around the cleanup: an unreadable-but-present
        # source (PermissionError) or a source that turned into a directory is a real
        # surprise the caller must still hear about, and the compare-and-set above -- the
        # part that decides whether anything becomes citable at all -- is deliberately
        # outside this.
        try:
            if _read(src) == current:
                os.unlink(src)
            else:
                _log.warning(
                    "evidence %s/%s was edited after it was approved; it is now verified AND "
                    "still present in the inbox -- review the inbox copy and delete it by hand",
                    kind, name)
        except FileNotFoundError:
            pass
        return True

    def _doc_path(self, rel: str) -> str:
        """Translate a store-contract DOCUMENT KEY into a filesystem path.

        The key is always "/"-separated (see `CRITERIA_RELPATH`); turning that into this platform's
        separator is the filesystem store's business. `os.path.join(*rel.split("/"))` is correct on
        POSIX and on Windows, where the contract's own key must not carry a backslash.
        """
        return os.path.join(self.dir, *rel.split("/"))

    def read_criteria(self) -> str:
        """The user's judging criteria, from their editable source of truth. Returns ""
        when unset; the caller falls back to the shipped (opinion-free) default.

        This was `build_system_prompt(vault.dir)`: triage reached THROUGH the store to a
        filesystem path. `.dir` is not on the Store contract and a SQLite store has none,
        so a second store would have AttributeError'd on the judge's critical path.
        """
        try:
            return _read(self._doc_path(_CRITERIA_RELPATH))
        except OSError:
            return ""

    def read_candidate_profile(self) -> CandidateProfile:
        """See Store.read_candidate_profile. Reads the note's frontmatter once via
        `_fm_dict` and builds a CandidateProfile from the known keys, ignoring
        anything else present.

        `_fm_dict`, not `_parse_fm_spaced` (which `_evidence_entries` uses):
        this note is machine-written and machine-read, and its keys are all
        lowercase-with-underscores by construction. That is a CHOICE, and it has a
        cost -- `_fm_dict`'s key regex is [A-Za-z0-9_]+, so a key it cannot match
        is silently dropped rather than raising. tests/test_vault_candidate_profile.py
        pins that as a tested fact.

        A missing note is an all-blank profile, not a raise. Only the three
        "genuinely absent" errors below are folded into that -- a real
        PermissionError must NOT be caught here: this module's standing rule is
        that an unreadable file is loud, never read as empty (see #81's warning
        at core/paths.py). `_read` (not a raw `open()`) is reused for the same
        reason `read_criteria` reuses it: one file-reading primitive, so a future
        change to how notes are opened cannot silently diverge between readers.
        """
        try:
            text = _read(self._doc_path(CANDIDATE_PROFILE_RELPATH))
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return CandidateProfile()
        # `parse_candidate_profile` (module level, beside `parse_frontmatter`) holds the actual
        # parse; this method's own job is only the file read and the missing-note abstain above.
        return parse_candidate_profile(text)

    def write_document(self, rel: str, text: str, *, only_if_absent: bool = False) -> str:
        """Write a store-managed document (the rejected-leads digest). Returns an opaque
        handle, or `""` when `only_if_absent` found the document already there. Also
        formerly an os.path.join onto `vault.dir`.

        `rel` must stay INSIDE the store. An absolute path makes os.path.join discard
        self.dir entirely, and "../" walks out -- either would let the one wholesale-write
        primitive on a never-clobber contract scribble anywhere on the disk, including over
        `My CV/CV.md`, which is the fabrication gate's ground truth. Not currently
        reachable (the only caller passes a config constant), which is exactly when to
        close it.
        """
        # realpath, not abspath: a symlink INSIDE the store (link -> /etc) would otherwise
        # satisfy commonpath and escape anyway.
        root = os.path.realpath(self.dir)
        path = os.path.realpath(self._doc_path(rel))
        if os.path.isabs(rel) or os.path.commonpath([root, path]) != root:
            raise ValueError(f"write_document: '{rel}' escapes the store root")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if only_if_absent:
            # O_CREAT|O_EXCL, not exists()-then-write: the check and the write are two syscalls,
            # and the racer on the other side is a human editing the note in Obsidian, who takes
            # no lock (#16). An exclusive create makes never-clobber a property of the open.
            try:
                _write(path, text, exclusive=True)
            except FileExistsError:
                return ""
            return path
        _atomic_write(path, text)
        return path

    def read_baseline(self) -> str:
        """Where the baseline CV lives is the store's business (configured on the
        store), not a path a caller passes in."""
        return _read(os.path.join(self.dir, self.baseline_rel))

    def preflight(self) -> dict:
        """`sluice doctor`'s optional Store hook (see core/protocols.py's `Store`
        docstring for the contract this implements and the no-writes rule it must
        honour). Facts only -- `core/doctor.py:classify_store` turns them into
        verdicts.

        `_is_dir`, not `os.path.exists`: it propagates a real PermissionError
        instead of reading an unstatable path as merely absent, the same rule
        every other existence check in this module follows, and for the same
        reason -- a vault doctor cannot even STAT is a fact worth a loud
        failure, not a quiet False.

        `baseline_exists` calls `read_baseline()` itself rather than merely
        stat-checking the path (`_is_note_file` would report a 0-byte or
        permission-denied file as "exists"), because doctor's whole point is
        answering "would a REAL cv run actually succeed here" -- and reading is
        the exact operation a real run performs. Only `(FileNotFoundError,
        IsADirectoryError)` -- both genuinely "no baseline here" -- are read as
        absent; a real PermissionError propagates out of this method entirely
        (to the caller's own broad handler) rather than being folded into a
        quiet False, matching this module's own rule that an unreadable file
        must be loud, never read as empty.

        Deliberately does NOT walk `leads_dir` (2627 notes in the vault this was
        built against): doctor is a preflight meant to run often and cheaply, not
        a second `leads` pass, and nothing a lead-by-lead scan would answer here
        that `read_leads` itself does not already answer for every OTHER command
        that needs it. The three evidence corpora (`read_evidence`/
        `read_pending_evidence`, #164) are the exception -- each is two orders of
        magnitude smaller than `leads_dir`, and their counts answer two things a
        compose depends on: `<kind>_verified` (only verified entries are citable
        by the fabrication gate, so zero is exactly the kind of thing worth
        surfacing before a compose is attempted) and `<kind>_pending` (a
        propose-only write leaves an entry sitting in `_inbox/`, doing nothing,
        until a human runs `job-sluice <kind> verify` -- a silent-inert state
        with no other signal anywhere). Iterates `EVIDENCE_KINDS` rather than
        naming the three kinds here, so a fourth kind needs no edit at this call
        site. A kind whose directories could not be read reports
        `<kind>_error` (the OSError's own text) INSTEAD of that triple, never a
        zero count -- see the loop's own comment for the isolation this buys and
        for why the classification of that fact belongs to `core/doctor.py`.

        `candidate_name_present`/`candidate_contact_present` (#133/#107) are the
        two DERIVED facts `read_candidate_profile()` reduces to via `full_name`/
        `contact_block` -- the identical pair `cv/engine.py`'s `skipped-config`
        refusal already gates a real compose on. Reported here rather than left
        for doctor to read the raw 36-field CandidateProfile itself, for the same
        reason `criteria_present` is a bool rather than the raw criteria text:
        this method answers FACTS about whether a run can proceed, not the
        content a run would use. Computed the same way the other reads above
        are -- `read_candidate_profile()` never raises on a missing note (an
        all-blank CandidateProfile), so no extra try/except is needed here."""
        if not _is_dir(self.dir):
            return {"vault_exists": False}
        try:
            # `.strip()`, not mere existence, and matching `criteria_present` below rather than
            # the older existence-only reading. `cv/engine.py`'s `missing_prerequisites`
            # refuses on `not baseline.strip()`, so an existence-only fact here made doctor and
            # cv disagree on a reachable state: measured, a whitespace-only `My CV/CV.md`
            # reported `baseline_rel  ok  found` while the very next `cv run` refused the same
            # vault. A file of blank lines is a file, but not a CV to tailor.
            baseline_exists = bool(self.read_baseline().strip())
        except (FileNotFoundError, IsADirectoryError):
            baseline_exists = False
        # `experience_total`/`experience_verified` keep their pre-#164 names: doctor
        # already consumes them by name, and a parallel `experience_entries` key
        # would leave two sources for the same fact -- the drift shape this codebase
        # removes on sight. `skills`/`stories` are new kinds, so they get the
        # identical pair under their own names, plus `<kind>_pending` for all three:
        # a captured-but-unreviewed entry is exactly the silent-inert state `doctor`
        # exists to surface (see its own module docstring and classify_store).
        # Iterates EVIDENCE_KINDS rather than naming the three kinds here, so a
        # fourth kind added to the registry needs no edit at this call site.
        counts = {}
        for kind in EVIDENCE_KINDS:
            # PER-KIND isolation, the shape `Sluice.verify_evidence_interactive`'s review
            # loop already uses. One corpus that cannot be read must not take the other
            # rows down with it: measured (round-2 review, H2) with `STAR Stories`
            # symlinked out of the vault, the OSError `_evidence_dir` correctly raises
            # unwound past this loop and out of `preflight` entirely, so `Sluice.doctor`'s
            # catch-all printed a single `store | preflight | DEAD` row and NOTHING else --
            # no baseline row, no Judging Profile row, and no `Candidate Profile | dead |
            # blocks: cv`. A user whose `cv run` says `skipped-config` runs `doctor` to
            # find out why and was told only about a corpus nothing reads.
            #
            # The FAILURE is a fact like the counts are; `core/doctor.py:classify_store`
            # is what turns it into a row, so classification stays there. The count keys
            # for this kind are deliberately NOT set: a `0` a consumer could read as "the
            # corpus is empty" is the quiet wrong default this codebase engineers out, and
            # `<kind>_error` has no such reading.
            #
            # `(OSError, ValueError)`, not `OSError` alone. This shipped as `OSError` alone
            # under a comment claiming the only ValueError these two methods raise is
            # `_kind`'s unknown-kind guard, which indeed cannot fire (this loop's `kind`
            # comes from `EVIDENCE_KINDS` itself) -- but that was not the only one.
            # `_evidence_entries` reads every entry through `_read`, which opens with
            # `encoding="utf-8"` and no `errors=`, so an entry file that is not valid UTF-8
            # raises UnicodeDecodeError, a ValueError SUBCLASS and not an OSError. The vault
            # is a directory a human and a sync client both write into, so that is an
            # ordinary state of the world here, not an internal invariant failure. Measured
            # on the real store: one such file unwound past this loop and out of `preflight`
            # entirely, producing the exact lone `store | preflight | DEAD` row the paragraph
            # above records -- this guard failing in the way it was written to prevent.
            try:
                every = self.read_evidence(kind, verified_only=False)
                pending = self.read_pending_evidence(kind)
            except (OSError, ValueError) as exc:
                counts[f"{kind}_error"] = str(exc)
                continue
            counts[f"{kind}_total"] = len(every)
            counts[f"{kind}_verified"] = sum(1 for e in every if e.get("verified"))
            counts[f"{kind}_pending"] = len(pending)
        profile = self.read_candidate_profile()
        return {
            "vault_exists": True,
            "baseline_exists": baseline_exists,
            "criteria_present": bool(self.read_criteria().strip()),
            **counts,
            "candidate_name_present": bool(full_name(profile).strip()),
            "candidate_contact_present": bool(contact_block(profile).strip()),
        }

    def set_tailored_cv(self, ref, value: str, *, only_if_absent: bool = False) -> bool:
        """Set the tailored_cv frontmatter field, body byte-for-byte intact. When
        `only_if_absent`, do NOT overwrite a tailored_cv that is already present in the
        FRESH content (return False) -- the batch cv path uses this to avoid clobbering a
        CV produced during its compose+render window; the check lives in the transform so
        it is atomic under CAS. Returns whether a write happened. May raise VaultConflict
        (#16, #16 cv long-window)."""
        def transform(text: str) -> str:
            inner, body = _split_frontmatter(text)
            if inner is None:
                inner, body = "", text
            if only_if_absent and _fm_value(inner, "tailored_cv"):
                return text
            inner = _set_fm(inner, "tailored_cv", value)
            return f"---\n{inner}\n---\n{body}"
        return _cas_write(ref, transform)

    def hold_for_signoff(self, ref, *, pending: str, claims: str) -> bool:
        """Stamp a #60 sign-off hold -- pending_cv + needs_signoff -- ONLY IF the note has no
        tailored_cv in FRESH content, mirroring set_tailored_cv(only_if_absent=...). Returns
        whether it stamped: False means a real send-ready CV already exists (a concurrent set,
        or an intentional single-lead re-tailor of an already-CV'd lead), so the flagged CV is
        left inert and the caller reports skipped-has-cv rather than latching the lead behind a
        redundant hold it would then need a manual sign-off to clear. The tailored_cv check is
        inside the CAS transform, so it sees a pointer that appeared during the compose window
        (#16), not the caller's stale snapshot. `claims` is written verbatim (the caller
        json.dumps it). Body byte-intact. May raise VaultConflict (#16)."""
        stamped = [False]  # reset per transform run so a CAS retry reports the final branch
        def transform(text: str) -> str:
            stamped[0] = False
            inner, body = _split_frontmatter(text)
            if inner is None:
                inner, body = "", text
            if _fm_value(inner, "tailored_cv"):
                return text  # a real CV already won; do not latch a redundant hold
            inner = _set_fm(inner, "pending_cv", pending)
            inner = _set_fm(inner, "needs_signoff", claims)
            stamped[0] = True
            return f"---\n{inner}\n---\n{body}"
        _cas_write(ref, transform)
        return stamped[0]

    def sign_off(self, ref, *, accept: bool = True, require_pending: str | None = None) -> str:
        """Resolve a #60 needs-signoff hold and report the OUTCOME derived from FRESH
        content: 'promoted' | 'discarded' | 'collision' | 'nothing' | 'stale' (the way
        upsert returns a verdict, so the caller never reconstructs it from a stale
        snapshot). With pending_cv present: clear pending_cv + needs_signoff, then --
        accept=False -> 'discarded'; accept and tailored_cv ABSENT -> set tailored_cv =
        pending_cv, 'promoted'; accept but tailored_cv already PRESENT -> leave it (a
        real CV appeared since -- a direct set_tailored_cv), 'collision'. No pending_cv
        -> unchanged, 'nothing'. The tailored_cv check lives inside the transform
        (atomic under CAS, mirroring set_tailored_cv(only_if_absent=...)), so the
        pointer is never clobbered. The returned string is DISTINCT from _cas_write's
        write-happened bool: the collision case WRITES (clears markers) yet is not
        'promoted'. May raise VaultConflict (#16).

        `require_pending` (#131 decision 13): when given, compared against the FRESH
        pending_cv value INSIDE this transform -- a mismatch returns 'stale' and
        writes nothing, joining the outcome vocabulary above. A note with NO
        pending_cv still reports 'nothing', which is decided before this comparison
        is reached. This is what makes a caller's confirm-token mechanism CAS-fresh: the
        comparison happens against bytes read at WRITE time, on every CAS retry, never
        against a snapshot the caller captured earlier."""
        outcome = ["nothing"]  # reset per transform run so a CAS retry reports the final branch
        def transform(text: str) -> str:
            outcome[0] = "nothing"
            inner, body = _split_frontmatter(text)
            if inner is None:
                return text
            pending = _fm_value(inner, "pending_cv")
            if not pending:
                return text  # nothing to resolve -> _cas_write no-op
            if require_pending is not None and pending != require_pending:
                outcome[0] = "stale"
                return text  # a mismatch is also a _cas_write no-op -- nothing written
            inner = _del_fm(inner, "pending_cv")
            inner = _del_fm(inner, "needs_signoff")
            if not accept:
                outcome[0] = "discarded"
            elif _fm_value(inner, "tailored_cv"):
                outcome[0] = "collision"  # a real CV won the race; stale markers cleared, pointer kept
            else:
                inner = _set_fm(inner, "tailored_cv", pending)
                outcome[0] = "promoted"
            return f"---\n{inner}\n---\n{body}"
        _cas_write(ref, transform)
        return outcome[0]

    def normalize_all_statuses(self, dry_run: bool = False) -> dict:
        """Canonicalize every lead note's status: fix value drift (dismissed ->
        dismiss) and quoting ("new" -> new), and collapse the DUPLICATE status
        lines the legacy judge writers left behind into a single canonical line.
        A note whose duplicate status lines DISAGREE (e.g. dismiss vs shortlist)
        is left untouched and reported under "conflicts" for manual resolution,
        never auto-guessed. Body untouched; unknown values reported.

        Per-note writes go through _cas_write, so a concurrent edit is re-collapsed from
        fresh content; one conflicting note never aborts the sweep. Three DISTINCT
        concurrent outcomes are counted, not conflated: a race that _cas_write can
        re-derive around still commits (changed); a race that makes the collapse a
        genuine no-op against the FRESH content (e.g. a concurrent editor already
        canonicalized it) is an abstain, counted unchanged -- nothing was written
        this run, so there is nothing to log, and a real disagreement introduced
        after this note's scan is left for the NEXT run's up-front scan to see and
        report; a SUSTAINED race that exhausts _cas_write's retries raises
        VaultConflict, which is logged and the note name recorded under "skipped"
        rather than aborting the sweep or letting the exception escape to the CLI.

        Walks the leads dir directly (rather than through self.read_leads()) so each note
        is read exactly ONCE before the changed/unchanged/conflicts decision -- read_leads()
        builds a LeadNote whose flat fm dict can't carry duplicate status lines anyway, so a
        SECOND raw re-read would be needed regardless. A second, independent re-read here
        would observe whatever a concurrent writer already did between the two reads, so the
        snapshot this decision is based on would silently drift out from under it -- the
        summary must reflect ONE moment in time, with any race after that moment handled by
        _cas_write's own re-derivation, never by an extra read racing without CAS protection."""
        summary = {"changed": 0, "unchanged": 0, "unknown": [], "conflicts": []}
        # `_is_dir`, not os.path.isdir -- the THIRD consumer of the scan set, and the one
        # that WRITES. Same early return and same single reason as read_leads': leads_dir
        # before the first upsert, where _walk's onerror=_reraise would raise on a fresh
        # vault. os.path.isdir also answers False to a leads_dir it cannot STAT, and here
        # that lands worse than a silent empty read: this method rewrites status lines, so
        # a vault full of notes is reported back to the CLI as {"changed": 0, "unchanged":
        # 0} -- a clean sweep that canonicalized nothing. And the False short-circuits
        # BEFORE _walk, so onerror=_reraise never gets the chance to fire. Measured with
        # the parent directory at mode 600: read_leads raised PermissionError while this
        # returned that summary over a vault holding a real note. See _is_dir.
        if not _is_dir(self.leads_dir):
            return summary
        paths = []
        for dirpath, filenames in self._walk():
            paths.extend(os.path.join(dirpath, n) for n in filenames if n.endswith(".md"))
        for path in sorted(paths):
            name = os.path.relpath(path, self.leads_dir)
            try:
                inner, _ = _split_frontmatter(_read(path))
            except OSError:
                continue
            if inner is None:
                summary["unchanged"] += 1
                continue
            # A file that is not a lead is the USER's -- an interview-prep or research note
            # they filed alongside their leads, now that the scan is recursive. Rewriting a
            # `status:` line inside one is a wholesale clobber of content sluice does not
            # own. Unlike read_leads, skipping here costs nothing: there is no lead to lose.
            if not _is_lead_note(_fm_dict(inner)):
                continue
            raws = re.findall(r"(?m)^\s*status\s*:\s*(.*)$", inner)
            norms = [_status.normalize(r.strip()) for r in raws]
            if len(set(norms)) > 1:  # conflicting duplicate statuses -> hands off
                summary["conflicts"].append((name, sorted(set(norms))))
                continue
            canonical = norms[0] if norms else ""
            if not _status.is_canonical(canonical):
                summary["unknown"].append(canonical)
            status_lines = [line for line in inner.split("\n")
                            if re.match(r"^\s*status\s*:", line)]
            already = len(status_lines) == 1 and status_lines[0].strip() == f"status: {canonical}"
            if already:
                summary["unchanged"] += 1
                continue
            if dry_run:
                summary["changed"] += 1  # report intent only; nothing is written
                continue
            try:
                committed = _cas_write(path, _normalize_status_transform)
            except VaultConflict:
                # Retries exhausted against a concurrent writer that never let this
                # note settle. Log for the operator and report it under "skipped"
                # rather than raising -- one sustained race must not abort the sweep
                # or crash the CLI command (#16).
                _log.warning("vault normalize skipped %s: status write raced repeatedly", name)
                summary.setdefault("skipped", []).append(name)
                continue
            if committed:
                summary["changed"] += 1
            else:
                # A concurrent edit made the collapse a no-op against the FRESH
                # content (e.g. a race that itself introduced a disagreement
                # _normalize_status_transform abstains on). Nothing was written
                # this run, so this is unchanged, not changed-but-invisible; a
                # surviving disagreement is caught by the next run's own scan.
                summary["unchanged"] += 1
        return summary

    # ── reconcile (#1) ───────────────────────────────────────────────────────
    def _managed_dirs(self) -> set:
        """The directories reconcile may move a note OUT OF, as paths.

        The leads-dir ROOT, plus every folder the configured layout can file into. Decision 6: a
        lead the user deliberately filed into a folder of their own is REPORTED and left alone,
        because decision 4 ("everything under leads_dir that sluice does not own is the user's")
        has to hold for writes as well as for reads.

        The root is its OWN term and is NOT derivable from the layout map -- that is the whole
        point of spelling it separately, and getting it wrong made this feature inert in an earlier
        draft. Under `active_archive` every canonical status maps to `Active` or `Archive`, so
        `{layout_subfolder(s, layout) for s in CANONICAL}` can never contain `""`; the root was
        silently excluded, every note in a flat vault reported `user_filed` at ".", and nothing
        ever moved -- on the only vault shape a user opting in actually has. The root is managed
        because it is where a PRE-layout vault's notes sit, not because any status implies it.

        The SUBFOLDERS stay derived rather than hand-listed {Active, Archive}: a layout that later
        files into a third folder becomes managed automatically, and a hand-list would leave notes
        stranded there with nothing red.

        `_merged/` is not here and cannot be: it is pruned from the scan set, so `read_leads` never
        yields a note in it (#81)."""
        subs = {layout_subfolder(s, self.lead_layout) for s in _status.CANONICAL}
        return {self.leads_dir} | {os.path.join(self.leads_dir, s) for s in subs if s}

    def reconcile_layout(self, *, apply: bool = False) -> dict:
        """File lead notes into the folders their statuses imply. REPORTS by default; `apply` is
        what moves anything -- the default IS the dry run, which is why there is no `dry_run`
        parameter to be inert (`leads dedupe`/`leads expire` are the same shape).

        The ONLY pass that moves a lead note (decision 2). No pipeline command relocates anything,
        and folder-vs-status drift between runs is harmless because the scan is recursive: a note
        in the "wrong" folder is still read, still written to, still applied for. That is what
        makes this safe to be manual.

        It never writes a note's BYTES -- only its directory entry, via `_reserve_and_move`. No
        status is read-modify-written, no frontmatter key is set, no body is re-rendered.

        That is NOT the same as "never-clobber holds by construction", which an earlier draft of
        this docstring claimed and which is measurably false. `_cas_write` re-reads for freshness
        and then `_atomic_write` calls `os.replace(tmp, path)`; a move landing in that window
        RE-CREATES the source path. The result is two notes at one basename -- one slug, so
        `_locate` returns two, `upsert` REFUSES that lead permanently, both notes' `last_seen`
        freeze, and the status edit is stranded on the resurrected copy while the moved note keeps
        the old one. A wider interleaving instead raises FileNotFoundError out of `_cas_write`,
        i.e. a lost modify-write arriving as an OSError rather than a VaultConflict. This is the
        same class of residual `_resolve_path` states for its cache and `_cas_write` states for its
        compare->replace micro-window: no portable stdlib atomic-conditional-rename exists, so it
        is DOCUMENTED and made LOUD rather than closed. `merge_cluster` shares the primitive but
        not the exposure -- its destination is pruned from the scan set and its basename differs,
        so the same race there yields a visible duplicate rather than a self-collision.

        Made loud two ways: the CLI help says reconcile must not run concurrently with a pipeline
        command, and after an applied sweep this re-reads and reports any basename then claimed by
        two paths into `ambiguous`. That re-read is a single post-sweep SNAPSHOT and so is
        best-effort, not a guarantee: a race landing after it is missed and surfaces on the next
        run instead. It converts the common case from silent into named, which is the whole
        claim.

        FOUR classes are reported and never moved, each for its own reason:

        - `unknown`    -- a non-canonical status. never-regress passes an unrecognized value
          through untouched everywhere else, so the layout must not decide a folder for one.
        - `ambiguous`  -- a slug two or more notes claim. This cannot be REPAIRED here: the slug IS
          the filename, so renaming orphans the note from `_resolve_path`'s candidate walk and the
          next scrape mints a fresh one; and choosing which twin survives is `leads dedupe`'s job,
          via `resolve_merge_status`. Moving one twin would PICK, which is precisely what
          `index_by_slug`, `upsert` and `select_one` all decline to do.
        - `user_filed` -- a lead outside the managed folders (see `_managed_dirs`).
        - `collisions` -- the destination name is taken. Refused, NEVER suffixed: a suffix changes
          the filename, which is the slug, which is the identity.

        Per-note `OSError` isolation, like `merge_cluster`'s per-loser arm. Not atomic across
        notes, deliberately -- an interrupted run leaves partial drift, which is this pass's normal
        input, and re-running converges."""
        # deepcopy, never `dict(CONST, ...)`: a shallow copy shares every mutable bucket, and is
        # safe only while each one happens to be overridden here. The constant's own comment
        # invites adding a bucket, which would then be aliased across every call in the process
        # -- so the single-definition property it exists for would quietly defeat itself.
        summary = copy.deepcopy(EMPTY_RECONCILE_REPORT)
        summary["layout"] = self.lead_layout
        # Decision 7, and it lives HERE rather than in the CLI. Under the flat layout there is
        # nothing to reconcile against, and FLATTENING would drag every lead out of the user's own
        # subfolders -- decision 4 pointed the wrong way. Putting this only in
        # `cmd_leads_reconcile` made the store and its own CLI disagree about what flat means: the
        # store still bucketed every user-filed note while the CLI said "nothing to reconcile", and
        # `Sluice.reconcile()` -- which every non-CLI caller goes through -- inherited the store's
        # answer. A behavioural rule about the layout belongs to the thing that owns the layout.
        if not self.lead_layout:
            return summary
        notes = self.read_leads()      # prunes _merged/ (#81) and skips non-lead files
        managed = self._managed_dirs()
        # `index_by_slug`, never a hand-rolled dict: it is the one sanctioned way in
        # (core/leads.py), it DROPS both twins rather than keeping whichever came last, and the
        # `dropped` mapping it returns IS this pass's ambiguous bucket by construction. The shipped
        # guard tests/test_slug_indexing_discipline.py names `leads reconcile` as its anticipated
        # FIFTH consumer -- it was written for exactly this code -- and it must stay GREEN.
        index, dropped = index_by_slug(notes)
        for slug, twins in dropped.items():
            summary["ambiguous"][slug] = sorted(
                os.path.relpath(t.ref, self.leads_dir) for t in twins)
        moved_anything = False
        for n in index.values():
            # The RAW value, not n.status: read_leads normalizes, and reporting the normalized form
            # for an unrecognized status would show the user a value their note does not contain --
            # the thing they have to go and fix.
            raw = n.fm.get("status", "")
            sub = layout_subfolder(raw, self.lead_layout)
            if sub is None:
                summary["unknown"].append((n.slug, raw))
                continue
            src_dir = os.path.dirname(n.ref)
            if src_dir not in managed:
                summary["user_filed"].append(
                    (n.slug, os.path.relpath(src_dir, self.leads_dir)))
                continue
            dest_dir = os.path.join(self.leads_dir, sub) if sub else self.leads_dir
            if os.path.normpath(src_dir) == os.path.normpath(dest_dir):
                summary["in_place"] += 1
                continue
            base = os.path.basename(n.ref)
            dst_rel = os.path.join(sub, base) if sub else base
            src_rel = os.path.relpath(n.ref, self.leads_dir)
            if not apply:
                summary["moves"].append((n.slug, src_rel, dst_rel))
                continue
            # A SYMLINKED destination silently destroys the lead. `_walk` keeps os.walk's
            # followlinks=False, so a symlinked `Active/` is NOT in the scan set: the moved note
            # leaves read_leads AND _locate, every later scrape resolves the same link and
            # refuses, and the lead is invisible to triage/cv/apply/track for good. Measured on a
            # vault with `Active` symlinked: moves=1, skipped=[], exit 0, ZERO log records, and
            # the lead gone. `_warn_undescended_symlinks` does not cover it -- it recorded the
            # link on the PRE-sweep read, when its target still held no note. This pass is what
            # invites subfolders at all, so it must not be the thing that files a lead out of
            # existence.
            if os.path.islink(dest_dir):
                summary["skipped"].append(
                    (n.slug, f"{sub}/ is a symlink; the scan does not follow symlinks, so a note "
                             f"moved there would be invisible and re-created every run"))
                _log.warning("reconcile: %s NOT moved -- %s is a symlink; move the real folder "
                             "into the vault instead of linking it", src_rel, dst_rel)
                continue
            # makedirs is OUTSIDE the try below on purpose. It raises FileExistsError when the
            # path exists and is NOT a directory (a plain file or dangling symlink named
            # `Archive`), and that try's first arm reads FileExistsError as a destination-name
            # COLLISION -- so the user would be told to "merge or rename by hand" about a path
            # that does not exist, with the real cause never stated and --apply exiting 1 forever.
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError as e:
                summary["skipped"].append((n.slug, str(e)))
                _log.warning("reconcile: could not create %s: %s", dst_rel, e)
                continue
            try:
                # suffix_on_collision=False: see _reserve_and_move. A FileExistsError here is a
                # REFUSAL, not a failure, so it is caught before the generic OSError arm --
                # conflating the two would tell a human to check permissions when what they
                # actually have is two notes at one name.
                _reserve_and_move(n.ref, dest_dir, base, suffix_on_collision=False)
            except FileExistsError:
                summary["collisions"].append((n.slug, dst_rel))
                _log.warning("reconcile: %s -> %s refused: destination is taken (merge or rename "
                             "by hand; a numeric suffix would change the slug)", src_rel, dst_rel)
                continue
            except OSError as e:
                summary["skipped"].append((n.slug, str(e)))
                _log.warning("reconcile: could not move %s -> %s: %s", src_rel, dst_rel, e)
                continue
            summary["moves"].append((n.slug, src_rel, dst_rel))
            moved_anything = True
        if moved_anything:
            # Re-derive the scan-set cache after a sweep that created directories and moved notes
            # into them, so the store's own view matches the disk for any later call on this
            # instance.
            #
            # This is PREVENTION, and an earlier draft of this comment called it mere hygiene on
            # reasoning its own neighbour falsifies. That draft argued a stale set is a strict
            # SUBSET, so `_locate` can only find FEWER notes -- the `missed=True` branch, which
            # `_resolve_path` already re-derives on -- and that the other direction (found ONCE
            # where a fresh list finds TWICE) needs two notes at one name, "which this pass
            # refuses rather than creates". It does create one: the RACE arm four lines below is
            # exactly that state, and it is what `test_a_move_that_races_a_status_write...`
            # constructs.
            #
            # Measured on one store instance in that state. Shipped: `_locate` returns 2 paths and
            # `upsert` REFUSES, which is correct. With this line deleted: the stale set omits the
            # destination folder, `_locate` returns 1, and `upsert` returns `merged` -- writing to
            # the RESURRECTED source note while the real moved note is never touched and its
            # `last_seen` freezes. That is a never-clobber outcome, not a tidiness one.
            #
            # `Sluice.store()` memoizes, so the `Sluice.reconcile()` facade followed by any other
            # pass on the same instance reaches it; today's CLI builds a fresh Sluice per command
            # and does not. Pinned by test_a_raced_move_leaves_the_store_refusing_not_updating.
            self._rescan_dirs()
            # The never-clobber residual (see the docstring), reported by the run that caused it.
            # A move racing a concurrent `_cas_write`'s `os.replace(tmp, path)` re-creates the
            # source path, and the resulting same-basename pair would otherwise surface much later
            # as an unexplained `upsert` refusal with no note anywhere saying why.
            _, raced = index_by_slug(self.read_leads())
            for slug, twins in raced.items():
                summary["ambiguous"].setdefault(slug, sorted(
                    os.path.relpath(t.ref, self.leads_dir) for t in twins))
        return summary

    def reconcile_names(self, *, apply: bool = False) -> dict:
        """File-NAME analogue of reconcile_layout (#1, #151): rename a lead note's BASENAME to
        match its frontmatter once triage has backfilled a real company over a placeholder one.
        A note created with a blank/sentinel company is seated at `" - <role>.md"` or
        `"Unknown - <role>.md"` (issue #151); once the company field is filled in, the
        frontmatter and the filename disagree, and `_resolve_path`'s candidate walk is keyed on
        the FILENAME, never the frontmatter -- so a re-scrape of the same posting mints a SECOND
        note at the fresh company's candidate name instead of finding the existing one. This
        pass closes that gap by renaming the note in place.

        REPORTS by default, exactly like `reconcile_layout`, `leads dedupe` and `leads expire` --
        the default IS the dry run, so there is no `dry_run` parameter to be inert.

        Two deliberate divergences from `reconcile_layout`, both because the two axes this store
        tracks -- WHICH FOLDER a note sits in, and WHAT BASENAME it carries -- are orthogonal:

        - No `lead_layout` gate. A note's basename can be wrong whether or not a layout is
          configured at all; nothing here depends on the status->folder map, so there is nothing
          to abstain over the way `reconcile_layout` abstains under the flat default.
        - No `_managed_dirs()` gate. A note the user filed into their own folder KEEPS that
          folder here -- only its basename changes -- so there is no folder set to restrict the
          scan to.

        It never writes a note's BYTES -- only its directory entry (same directory, new
        basename), via `_reserve_and_move`. No status is read-modify-written, no frontmatter key
        is set, no body is re-rendered: this is a pure rename, same as `reconcile_layout` is a
        pure move.

        `_frontmatter_name` (see there) does the real qualification work: an exact
        RE-DERIVATION of the note's current stem from `_candidate_names`, called with the
        PLACEHOLDER head rather than the fresh company. That is what makes a human-renamed note
        -- or one whose role has drifted since it was seated, without the file being renamed --
        invisible to this pass by construction. `_candidate_names` itself is never touched or
        taught to read frontmatter: doing that was tried and abandoned for `_archived_match` (see
        there) for the mirror-image reason stated in `_frontmatter_name`'s own docstring -- a
        failed re-derivation there resurrects a merged-away lead (fail-open, on the one arm that
        must never fail open), while a failed re-derivation here just leaves a note unrenamed
        (fail-closed, to the status quo).

        The rename target is ALWAYS `_frontmatter_name`'s candidate 1 -- even when the note's
        CURRENT name was seated at a location- or digest-suffixed candidate. Renaming to anything
        but candidate 1 would mint a duplicate on the very next scrape: `_resolve_path` always
        tries candidate 1 first, and a note not sitting there is invisible to that first probe.

        COLLISION HANDLING has THREE layers, because `_reserve_and_move`'s directory-scoped
        `O_EXCL` alone cannot see a note carrying the SAME target name in a DIFFERENT folder --
        exactly the cross-folder duplicate-pair shape issue #151 itself reports, and the one a
        recursive, layout-aware vault makes possible that a flat store never could.

        1. A VAULT-WIDE precheck, `self._locate(target)` -- the store's own existing
           cross-folder lookup primitive. Non-empty -> refuse into `collisions`, move nothing.
           `_reserve_and_move`'s O_EXCL reservation is scoped to ONE directory (the note's own,
           since source dir and destination dir are the SAME directory for a rename), so it
           cannot by itself see a note with the same target basename sitting elsewhere in the
           vault -- this layer is what can. The scan-set cache is re-derived
           (`self._rescan_dirs()`) immediately before this precheck runs, mirroring
           `_resolve_path`'s own re-derive before trusting an absent verdict for the identical
           kind of decision -- see there ("invisible by construction -- both lists agree, both
           are wrong, and the wrong answer looks like a real one"). Without it, a `Sluice`
           instance reused across an `upsert` that creates a NEW folder (`os.makedirs`, which
           runs AFTER `_resolve_path`'s own re-derive) and a later `reconcile_names` call on
           that SAME instance would miss a correctly-named note sitting in that new folder,
           letting this precheck miss the exact collision it exists to catch.
        2. A WITHIN-RUN precheck: two different stale notes in the SAME sweep that would both
           mint the identical target. Both refuse -- neither is picked arbitrarily. This is
           computed as a separate pass over every note's ALREADY-decided target (see below),
           specifically so the outcome cannot depend on which of the two notes a dict/list
           happened to visit first.
        3. `_reserve_and_move(..., suffix_on_collision=False)`, as the LAST word -- reused
           exactly as `reconcile_layout` uses it. A collision surviving to this point is a race
           against a writer OUTSIDE this sweep's own read (the two prechecks above only see the
           vault as it was when this sweep started), and it is refused rather than suffixed: a
           suffix changes the filename, which is the slug, which is the identity a later scrape
           must find again -- auto-suffixing here would silently orphan the note it "protected".

        Every note's target is decided BEFORE any note is actually moved, and both collision
        layers run to completion over the whole batch before the move loop starts. Interleaving
        decide-then-move-then-decide-the-next-one would make layer 2 order-dependent: the first
        of two colliding notes visited would win by accident of iteration order, which is
        exactly the "picked arbitrarily" outcome layer 2 exists to rule out.

        Each `collisions` entry is a THREE-tuple, `(slug, target, reason)` -- unlike
        `reconcile_layout`'s bare `(slug, dst_rel)` -- because the three layers above refuse for
        structurally different causes (a pre-existing duplicate elsewhere in the vault; two
        notes in this very sweep racing each other; a writer outside the sweep winning a race),
        and `skipped` already carries a reason string for the same kind of reason: an operator
        should not have to guess which layer fired from the tuple shape alone.

        ACCEPTED RESIDUAL, stated rather than hidden -- the same posture this codebase takes
        with the CAS micro-window and `reconcile_layout`'s own post-sweep race: layer 2 above
        refuses BOTH notes whenever two stale notes would mint the identical candidate-1 target,
        with no attempt at a candidate-2/3 fallback. "Always candidate 1" is the deliberate
        simplifying rule stated above, not an oversight to unwind casually. Two genuinely
        DISTINCT leads that happen to share company and title (different city, say) and both
        backfill to the identical BARE target are therefore reported as colliding on every run,
        forever, with nothing here telling the operator they are not actually a duplicate pair.
        The remedy is manual: a human renames one of the two notes by hand to a name this pass
        will leave alone (see `_frontmatter_name`'s exact-re-derivation qualification -- any
        name outside its minted set is invisible to this pass by construction).
        Whether this pass should ever try alternate candidates for an ambiguous target is a
        genuine, separate design question, deliberately left open here rather than answered as a
        side effect of this fix.

        The SYMLINK guard is repositioned from `reconcile_layout`'s, which guards the
        DESTINATION DIRECTORY -- unreachable here, since source dir and destination dir are the
        same directory for a rename. `reconcile_layout`'s OWN justification for its guard
        (`os.replace` silently detaches the note by moving the link rather than the file it
        points to) does NOT carry over to this pass, and an earlier draft of this docstring
        claimed it did: reproducing `_reserve_and_move`'s exact sequence (O_EXCL reserve, then
        `os.replace`) against a real relative symlink where dest_dir == source dir -- exactly
        this pass's geometry -- measured, the destination stays a correctly-resolving symlink.
        `os.replace` renames the link's directory ENTRY in place; a relative link's target is
        resolved relative to that same directory, which never moved, so nothing is detached.
        Contrast `reconcile_layout`'s own symlink comment (above, near its guard site), where
        the destination directory genuinely DOES differ and the detachment is real and
        measured. The guard is kept here anyway, for the honest reason instead of the false one:
        a symlinked note is a structure the user deliberately built (one physical note filed
        under two names, say), and this pass does not reorganise it -- routing it to `skipped`
        is conservative, not protective. Checked via `os.path.islink(note.ref)`, before any move
        -- and before either collision layer, so a note that will never move cannot occupy a
        target and falsely flag some OTHER note's rename as a within-run collision.

        The POST-SWEEP RACE PROBE is genuinely different from `reconcile_layout`'s, and copying
        that one verbatim here would be a bug, not a saving. A raced RECONCILE move (folder
        change, SAME basename) re-creates the source at the SAME basename in a DIFFERENT folder
        -- one slug now claimed by two refs, exactly what `index_by_slug`'s `ambiguous` bucket
        already detects on a fresh read. A raced RENAME (same folder, DIFFERENT basename)
        instead re-creates the source at the OLD basename -- a DIFFERENT slug from the new one,
        so `index_by_slug` sees two entirely distinct, individually-UNIQUE slugs and reports
        nothing: the race is invisible to that probe. So this pass runs a narrower probe of its
        own instead: for each note that WAS renamed this sweep, re-check whether its OLD path
        still names a note (`_is_note_file`, never `os.path.exists` -- see that probe's own
        docstring); if so, both halves of the raced pair are now real notes, filed under
        `resurrected` -- deliberately a DIFFERENT bucket from `ambiguous`,
        because the two residuals need different human fixes (merge two notes sharing one name,
        versus investigate why an old name came back at all). The probe call itself is wrapped
        in its own per-note `except OSError`, filing into `skipped` on failure: by the time this
        loop runs, `n` has already been renamed on disk, so letting `_is_note_file`'s own
        (deliberate) OSError propagation escape this loop would abort the whole sweep with real
        renames already landed -- exactly the gap `cmd_leads_rename`'s dispatcher-level comment
        now documents as closed.

        `_rescan_dirs()` IS called once, immediately before layer 1's precheck loop begins (see
        there for why). `reconcile_layout`'s OWN `_rescan_dirs()` call, by contrast, runs AFTER
        an applied sweep: that call exists because a folder MOVE can create a NEW directory the
        cached scan-set list does not yet know about, so re-deriving afterward keeps the store's
        view current for whatever runs next on the same instance. A rename creates no
        directories at all (source dir == dest dir), so there is no new directory for a
        post-sweep re-derive to discover here -- paying a second full walk after the move loop
        would be waste, which is why this pass does not also call it a second time there.
        """
        # deepcopy, never `dict(EMPTY_RENAME_REPORT, ...)`: see EMPTY_RECONCILE_REPORT's own
        # comment -- a shallow copy shares every mutable bucket (the `unresolved`/`renames`/
        # `collisions`/`resurrected`/`skipped` lists and the `ambiguous` dict), which is safe
        # only while each one happens to be overridden, and this docstring's own shape invites
        # adding a bucket that would then be aliased across every call in the process.
        summary = copy.deepcopy(EMPTY_RENAME_REPORT)
        notes = self.read_leads()      # prunes _merged/ (#81) and skips non-lead files
        summary["examined"] = len(notes)
        # Same discipline as reconcile_layout: two or more notes ALREADY claiming one slug
        # (before this pass renames anything) cannot be repaired here -- the slug IS the
        # filename, and renaming one twin without knowing which is the real one would just trade
        # one ambiguity for another. `index_by_slug` is the one sanctioned way to detect and
        # report it, and only the DEDUPLICATED index is processed below.
        index, dropped = index_by_slug(notes)
        for slug, twins in dropped.items():
            summary["ambiguous"][slug] = sorted(
                os.path.relpath(t.ref, self.leads_dir) for t in twins)

        # Phase 1: classify every note and decide its target, WITHOUT moving anything yet (see
        # the docstring for why the whole batch is decided before any move runs). Symlinked
        # notes are pulled out here, before either collision layer, so a note that will never
        # move cannot occupy a target and falsely flag some other note's rename as colliding.
        candidates = []   # [(note, target)] -- notes with a real, computed rename target
        for n in index.values():
            target, head = self._frontmatter_name(n)
            # `is None`, never a falsy check: `head` is the placeholder the CURRENT name was
            # minted from, and for the blank-company population -- this feature's primary
            # target -- that placeholder IS the empty string `""`, which is falsy but NOT
            # None. `if not head` would treat every blank-company note as "not one this store
            # minted" and skip it here even when `target` holds a real, freshly-resolved
            # rename -- silently dropping the whole blank-company population from ever being
            # renamed. Only `target is None and head is None` -- `_frontmatter_name`'s own
            # documented "leave alone entirely" sentinel -- may take this branch.
            if target is None and head is None:
                continue  # not a name THIS STORE minted from a placeholder -- leave it alone
            if target is None:
                # head is not None: the current name IS one this store minted from a
                # placeholder, but the frontmatter offers nothing better yet (still blank, or
                # still a sentinel like "Unknown"). Report and leave untouched -- there is
                # nothing safe to rename TO.
                summary["unresolved"].append((n.slug, n.fm.get("company", "")))
                continue
            if os.path.islink(n.ref):
                # A symlinked note is a structure the user deliberately built, not a
                # detachment hazard here: source dir == dest dir for a rename, so
                # os.replace renames the link's directory entry in place and a relative
                # link keeps resolving correctly afterward -- see the docstring, where
                # `reconcile_layout`'s own symlink guard is the contrasting case whose
                # destination directory genuinely differs and whose detachment is real.
                # This pass simply does not reorganise a structure the user built.
                summary["skipped"].append(
                    (n.slug, "note is a symlink; this pass does not reorganise a "
                             "structure the user deliberately built"))
                continue
            if target == n.slug:
                # The re-derivation reproduced the note's OWN current name -- reachable when
                # the frontmatter company folds to the SANITIZED spelling of a placeholder
                # (e.g. `_sanitize` renders "N/A" as "N-A", which `_frontmatter_name`'s
                # placeholder-head comparison recognises via that same sanitize step, but
                # `is_placeholder_company` does not -- only "n/a"/"na" are members). There is
                # nothing to rename here: the note is already correctly seated. Skipping it
                # BEFORE layer 1 (rather than letting layer 1 find the note as its own
                # blocker) avoids reporting a phantom self-collision on every run forever.
                continue
            candidates.append((n, target))

        # Layer 1: the vault-wide precheck. Runs against the PRE-sweep vault -- nothing in this
        # sweep has moved yet -- so `self._locate` cannot see any of this sweep's own renames
        # landing early and cannot be confused by them.
        #
        # The scan-set cache is re-derived immediately before this loop, mirroring
        # `_resolve_path`'s own re-derive before trusting an absent verdict for the identical
        # kind of decision (see there). Without it, a `Sluice` instance reused across an
        # `upsert` that creates a NEW folder (`os.makedirs`, which runs AFTER `_resolve_path`'s
        # own re-derive) and a later `reconcile_names` call on that SAME instance would miss a
        # correctly-named note sitting in that new folder, letting this precheck miss the exact
        # collision it exists to catch.
        self._rescan_dirs()
        survivors = []
        for n, target in candidates:
            if self._locate(target):
                # _locate is VAULT-WIDE, so this fires for a blocking note in the SAME folder
                # too, not only a genuinely different one -- "elsewhere in the vault" would
                # send an operator looking in the wrong place for a note sitting right next to
                # the one they are reading about. Neutral wording instead, accurate either way.
                summary["collisions"].append(
                    (n.slug, target,
                     "a note is already seated at this name in the vault"))
                continue
            survivors.append((n, target))

        # Layer 2: the within-run precheck. Grouped AFTER layer 1 and BEFORE any move executes,
        # so two notes racing to the same target are compared against EACH OTHER, never against
        # whichever one happened to move first.
        by_target: dict = {}
        for n, target in survivors:
            by_target.setdefault(target, []).append(n)
        to_move = []
        for target, group in by_target.items():
            if len(group) > 1:
                for n in group:
                    summary["collisions"].append(
                        (n.slug, target,
                         "two notes in this sweep both resolve to this target"))
                continue
            to_move.append((group[0], target))

        # Phase 2: execute. Layer 3 (_reserve_and_move's own O_EXCL) is the LAST word -- a
        # collision surviving to here is a genuine race against a writer OUTSIDE this sweep's
        # own read, and it is refused, never suffixed (see the docstring).
        renamed = []   # [(note, target)] actually renamed this sweep, for the race probe below
        for n, target in to_move:
            dest_dir = os.path.dirname(n.ref)
            folder = os.path.relpath(dest_dir, self.leads_dir)
            if not apply:
                summary["renames"].append((n.slug, target, folder))
                continue
            try:
                # suffix_on_collision=False: see _reserve_and_move and the docstring above. A
                # FileExistsError here means a writer OUTSIDE this sweep's own read claimed
                # `target` between layer 1's precheck and this attempt.
                _reserve_and_move(n.ref, dest_dir, f"{target}.md", suffix_on_collision=False)
            except FileExistsError:
                summary["collisions"].append(
                    (n.slug, target, "target claimed by a writer outside this sweep"))
                _log.warning("rename: %s -> %s refused: destination is taken (merge or rename "
                             "by hand; a numeric suffix would change the slug)", n.slug, target)
                continue
            except OSError as e:
                summary["skipped"].append((n.slug, str(e)))
                _log.warning("rename: could not rename %s -> %s: %s", n.slug, target, e)
                continue
            summary["renames"].append((n.slug, target, folder))
            renamed.append((n, target))

        # The post-sweep race probe -- see the docstring for why this is NOT reconcile_layout's
        # index_by_slug re-read. A raced rename re-creates the SOURCE at the OLD basename, a
        # slug of its own that index_by_slug cannot see as a collision; check the old path
        # directly instead. `n.ref` is the note's PRE-sweep path (this LeadNote was never
        # re-read after the move), which is exactly the old path this probe needs.
        if apply:
            for n, target in renamed:
                # _is_note_file, never os.path.exists -- the same rule this file's OWN
                # _is_note_file docstring states: os.path.exists swallows EVERY OSError, so an
                # unstatable old path (a race against an unreadable parent directory) would
                # silently read as "gone", and this is the ONE bucket whose entire purpose is
                # reporting exactly that a genuine resurrection happened.
                #
                # But `_is_note_file` propagating is correct for ITS contract and dangerous
                # for THIS caller's: by the time this loop runs, `n` has already been renamed
                # on disk (it only reaches `renamed` after Phase 2's move succeeded), so an
                # uncaught OSError here would escape reconcile_names with real renames already
                # landed -- and cmd_leads_rename's broad `except OSError` would then print a
                # generic refusal and misreport "nothing renamed" for notes that DID move,
                # skipping the dead-letter migration loop for them too. Isolate per-note, the
                # same way Phase 2's move loop just above isolates FileExistsError/OSError,
                # so one unreadable old path cannot abort the probe for every other note in
                # this sweep.
                try:
                    old_path_reoccupied = _is_note_file(n.ref)
                except OSError as e:
                    summary["skipped"].append(
                        (n.slug, f"could not check the old name for a resurrected note "
                                 f"after renaming to {target!r}: {e}"))
                    _log.warning("rename: resurrection probe for %s -> %s failed: %s",
                                 n.slug, target, e)
                    continue
                if old_path_reoccupied:
                    summary["resurrected"].append((n.slug, target))
        return summary

    # ── upsert ───────────────────────────────────────────────────────────────
    def upsert(self, lead: Lead) -> UpsertResult:
        """Reconcile an incoming lead against the existing notes. Returns an
        UpsertResult whose `outcome` is one of
        "created" | "updated" | "merged" | "refused" | "merged_away" | "merged_away_unproven".
        UPDATE and MERGE bump ONLY last_seen (never-clobber); REFUSE writes nothing, on any of
        FIVE causes, across four `return "refused"` sites. Three are IDENTITY refusals, decided
        before or during the candidate walk: every name candidate is a note proven DIFFERENT
        (#5); one candidate resolves to SEVERAL notes at once (ambiguous identity; see
        _locate); or the note this lead would be written into reads back with neither a company
        nor a role, so it has no identity to seat and no read would ever return it (below).
        The first two share one return site -- `_resolve_path` reports both as "refuse" and the
        log line names both, because at that point they cannot be told apart.

        The other TWO are CONCURRENCY-LOSS refusals, and they are `refused` for a reason worth
        keeping in view: the outcome vocabulary a caller branches on has no separate "lost a
        race" member, and inventing one would put a lead the sink has no rule for into the
        allowlist decision. A sustained create/delete flap exhausts `_CREATE_RACE_RETRIES` (the
        loop below), and a sustained last_seen CAS conflict exhausts `_cas_write`'s retries
        (`_bump_last_seen_or_refuse`, which absorbs `VaultConflict` so no exception crosses the
        ingest sink). Both write nothing and keep the lead OUT of `seen.db`, so it is retried
        next run -- which is the whole reason mapping them here is safe.

        The two "merged_away*" outcomes ALSO write nothing:
        a human already archived this lead as a duplicate (#81), so the incoming scrape is
        suppressed rather than re-created. The two are kept distinct rather than conflated into one
        string -- `_ARCHIVED` is a url-PROVEN match against the archived note,
        `_ARCHIVED_UNPROVEN` is every weaker one (a location-only SAME, or UNKNOWN) --
        and that distinction is what later decides whether the lead may enter the dedup
        store.

        The create is EXCLUSIVE (`_write(..., exclusive=True)`): if a concurrent writer (another `ingest run`,
        or a human/Obsidian) creates the note in the window between _resolve_path's existence
        check and the write, the create raises FileExistsError instead of TRUNCATING that
        note, and we loop to re-reconcile against it. This closes the TOCTOU clobber (#16).
        The loop is bounded because a re-resolve finds the path now occupied, so it terminates
        -- as update/merge, a fresh "created" at the NEXT name candidate (when the raced note
        is a DIFFERENT job, so _resolve_path advances past it), or refuse. Only sustained
        create/delete flapping exhausts the retries, which refuses loudly (writing nothing)
        rather than clobbering or spinning.

        A lead whose note would read back with NEITHER a company nor a role is refused before
        any of that, and the refusal is decided by the predicate the READ applies: the note is
        rendered, its frontmatter split and parsed with `_fm_dict`, and `_is_lead_note` run on
        the result -- the same chain `read_leads` runs, over the very bytes `_write` is about
        to put on disk. Such a note has no identity to reconcile on: every name candidate
        collapses to the bare separator or to punctuation, and `read_leads` then skips it
        because `_is_lead_note` asks for a non-empty `company` or `role` -- so the note exists,
        `created` is reported, the ingest sink writes it into `seen.db` (which has no removal
        path), and NO read in the tool can ever see it again. That is not a store's worst
        outcome, it is the invisible one. Refusing writes nothing and keeps the lead out of
        `seen.db`, so a source that starts emitting these re-reports every run instead of
        filling the vault with unreadable stubs -- the same recoverable direction every other
        refusal here takes. It is a REFUSAL rather than a warned skip for that reason: a
        warning on a `created` still leaves the note and the seen.db row behind.

        Deciding it on the RAW fields is what this replaces, and the reason is not style.
        `_fm_dict` ends in `.strip().strip('"').strip("'")`, so a company of `"` or of `'`
        parses back EMPTY while any truthiness test over the raw field sees it as present.
        Measured on the version this replaces: `company='"'`, `company="'"` and `title='"'`
        were all `created`, `read_leads` returned none of them, and the notes sat on disk as
        `- - .md`, `' - .md` and ` - -.md` with a permanent `seen.db` row apiece. Closing one
        spelling by hand leaves the next one open, because the guard and the read were
        normalising independently; running the read's own chain over the bytes about to be
        written makes "if no read could ever return it, do not write it" hold by construction.
        The rendered string is reused by the create below, so the thing checked and the thing
        written cannot differ. Measured cost: a no-op `updated` upsert goes 92us -> 104us with
        the check present, i.e. ~12us, or ~0.12s added across 10k leads. It is paid on EVERY
        upsert, not only the creates, because a refusal must land before anything touches the
        filesystem -- `test_upsert_refuses_a_lead_with_neither_company_nor_title` pins that,
        snapshotting an untouched tree including the Syncthing marker.

        Visible on `main`, where read_leads returned every `.md` in one flat directory and
        the stub at least showed up. The recursive scan's `_is_lead_note` predicate is what
        made it invisible, so this refusal ships with the change that hid it.

        ONE field is enough, and that half is as load-bearing as the refusal: a company-only
        lead is seated at `Acme - .md` and a title-only one at ` - Analyst.md`, and
        `read_leads` returns BOTH (measured), because `_is_lead_note` is satisfied by either
        field alone. Flipping that `or` refuses them instead -- out of the vault, out of
        `seen.db`, re-reported every run under a warning saying the note reads back blank when
        it does not. The mirror harm of a guard is the guard's own business, so both
        directions are pinned by tests.

        The PARSED values are stripped before `_is_lead_note` sees them, which makes this gate
        deliberately stricter than the read predicate it otherwise mirrors -- and stricter in
        one direction only, since stripping can empty a value but never fill one, so this
        refuses a superset of what `_is_lead_note` rejects and never less. An all-whitespace
        company survives `_fm_dict` as whitespace and is truthy, so without the strip a note is
        seated at `    - .md`: not invisible -- `read_leads` does return it -- but carrying no
        identity to reconcile on, which is the condition this refusal is about. Stricter on the
        WRITE side is the safe asymmetry: a create is the one wholesale write, so declining it
        costs a re-report, while seating an identity-less note costs a permanent `seen.db` row.
        Nothing legitimate is caught -- only an ALL-whitespace value strips to empty, so
        `" Acme "` still creates -- and `ingest/base.py` already coerces and strips both fields
        on the way in (`(row.get(...) or "").strip()`, the only `Lead` construction anywhere in
        `sluice/`), so this is defence for a store driven directly rather than a live scrape.
        A field that is None rather than a string no longer reaches this gate as a None at
        all: `Lead.__post_init__` coerces it to "" at construction, so a direct call passing
        `company=None, title=None` arrives here as two empty strings and is REFUSED by the
        ordinary blank-identity rule above. That coercion is what makes the refusal complete,
        and it had to live there rather than here: this gate decides on the RENDERED
        frontmatter, where `_render_new` writes None as the literal string `None` and
        `_is_lead_note` then reads a perfectly good identity back -- measured, a visible
        `None - None.md` that `read_leads` returned. A store cannot fix that by looking
        harder at the bytes; only the type boundary can.

        Nothing raises on either route, which is the None-tolerance that mattered and is
        unchanged: an AttributeError here (or in `Lead`) is not caught by the sink's
        `except OSError` and would abort the whole ingest run, so the coercion coerces
        rather than rejecting. See `core/leads.py:Lead.__post_init__`.

        `result.slug` is the slug of the note this call resolved to -- populated for
        "created"/"updated"/"merged", empty for "refused"/"merged_away"/
        "merged_away_unproven". Never a different note that merely happens to share
        the same company+title identity -- see UpsertResult's own docstring for why
        that distinction is the whole point of this type."""
        # decision 7, round 3: company/role are the vault's identity key
        # (_candidate_names' stem = f"{company} - {title}") -- forging a frontmatter
        # key via an embedded newline in EITHER must refuse the whole create before any
        # bytes are rendered, never abstain-and-blank (which would silently change
        # which note a later legitimate re-scrape maps onto, splitting one real job
        # into two disconnected notes). Checked on the RAW Lead fields, before
        # _render_new interpolates them: by the time `rendered` exists, an injected
        # newline has already forged whatever key follows it.
        #
        # NARROWER than frontmatter_safe(): only its "not printable" sub-rule (which
        # already rejects \n, the actual key-forging vector -- str.isprintable() covers
        # the whole C0/C1 control class) -- deliberately SKIPPING frontmatter_safe's
        # separate "/\\ structural-character rule, which would refuse
        # test_upsert_still_creates_a_lead_whose_field_merely_CONTAINS_quotes's pinned
        # tolerance for an embedded quote in company/role. Sluice's own line-based
        # _fm_dict/_fm_value reader already tolerates an embedded quote in these two
        # fields today, unguarded -- that tolerance is product behavior this must not
        # regress. OR-based (checked individually), not AND: a naive AND-based check
        # (mirroring the blank-identity gate's own OR-satisfied shape) would let a
        # single-field-unsafe case through, which is exactly the scenario this exists
        # to close.
        if not lead.company.isprintable() or not lead.title.isprintable():
            _log.warning(
                "vault refused lead %r: company or role contains a control character "
                "(e.g. an embedded newline), which could forge a frontmatter key",
                lead.dedup_key)
            return UpsertResult(outcome="refused")
        rendered = self._render_new(lead)
        # `_split_frontmatter` cannot return None for `_render_new`'s output, and if it ever
        # did `_fm_dict(None)` is `{}` -- which refuses. Fails closed either way.
        fm = _fm_dict(_split_frontmatter(rendered)[0])
        if not _is_lead_note({k: v.strip() for k, v in fm.items()}):
            _log.warning("vault refused lead %r: its company and role both read back blank "
                         "from the note it would be written into, so it has no name to be "
                         "seated at and no read would ever return it", lead.dedup_key)
            return UpsertResult(outcome="refused")
        for _ in range(_CREATE_RACE_RETRIES):
            path, action = self._resolve_path(lead)
            if action == "refuse":
                # Loud, not silent, and writes NOTHING -- not the note, and not the leads dir
                # or Syncthing marker (below): no name candidate can be written without
                # clobbering a different job. TWO causes reach here and the message names both,
                # because it cannot tell them apart -- every candidate a note proven DIFFERENT
                # (#5), or one candidate resolving to SEVERAL notes, which _resolve_path has
                # already logged with the colliding paths. The sink counts this and keeps the
                # lead out of seen.db, so it is retried (and re-reported) next run rather than
                # lost. Reachable only pathologically (a note whose frontmatter contradicts its
                # filename, a byte-clamp collapse on a tiny NAME_MAX, or a hand-copied note).
                _log.warning("vault refused lead %r: no name candidate is writable -- every one is "
                             "a note proven different, or one resolves to several",
                             lead.dedup_key)
                return UpsertResult(outcome="refused")
            if action in (_ARCHIVED, _ARCHIVED_UNPROVEN):
                # #81. Beside `refuse`, NOT beside update/merge: those sit AFTER the makedirs
                # below, and a lead that writes nothing must not create the leads dir or the
                # Syncthing marker either. _archived_match has already logged which archive
                # matched. Both strings need this branch -- either one without it falls
                # through to _write(None, ...) and raises TypeError, which the sink's
                # `except OSError` does NOT catch and engine.py calls sink.write outside its
                # per-source try, so the whole ingest run would abort.
                return UpsertResult(outcome=action)
            # Every remaining action WRITES, so make the dir + Syncthing marker now -- after the
            # refusal check, so a DIRECT refusal (the common case, pinned by
            # test_upsert_refuses_and_writes_nothing) leaves the filesystem untouched. (A refusal
            # reached only AFTER a create lost the TOCTOU race may have made these in the racing
            # iteration -- harmless: no lead note is written, the racer already created the dir,
            # and .stfolder is idempotent.)
            # ensure_stfolder lives here and not in __init__: constructing a store must not touch
            # the disk. It used to be a Store method cli.py called by hand, leaking a Syncthing/
            # Obsidian concept into a contract every other store would have had to pretend to honour.
            os.makedirs(self.leads_dir, exist_ok=True)
            self.ensure_stfolder()
            if action == "update":
                outcome = self._bump_last_seen_or_refuse(
                    path, lead.last_seen or _today(), "updated", lead.dedup_key)
                return UpsertResult(outcome=outcome,
                                    slug=self._slug_for(path) if outcome != "refused" else "")
            if action == "merge":
                # We could not prove same-or-different, so we do NOT split (that would mint a
                # note per scrape -- unbounded). Bump last_seen like an update; the difference
                # is only that the count is reported separately so the merge is visible.
                outcome = self._bump_last_seen_or_refuse(
                    path, lead.last_seen or _today(), "merged", lead.dedup_key)
                return UpsertResult(outcome=outcome,
                                    slug=self._slug_for(path) if outcome != "refused" else "")
            # The WRITE FOLDER, made HERE and not beside the leads_dir makedirs above, which sits
            # ABOVE the update/merge/create fan-out and therefore runs on every non-refused
            # outcome. Measured: a second upsert of the same lead reaches that line and returns
            # "updated" -- so repointing it would mint an empty Active/ in the user's vault on a
            # pure last_seen bump of a note that already exists at the root. Only a CREATE needs
            # the write folder to exist. (The leads_dir makedirs stays where it is: update and
            # merge legitimately need the directory, and the Syncthing marker beside it is
            # idempotent.)
            #
            # And it sits OUTSIDE the try below, which is the part that is easy to get wrong:
            # makedirs raises FileExistsError when the path exists and is NOT a directory (a
            # plain file or dangling symlink named `Active`), and that try's arm reads
            # FileExistsError as the #16 create RACE -- so every attempt would burn a retry and
            # the lead would be refused with "create raced repeatedly", a mechanism that never
            # fired. Out here it propagates as an ordinary OSError carrying the real errno and
            # path, which the ingest sink counts `skipped` and keeps out of seen.db for a retry.
            write_dir = self._write_folder()
            # `write_dir != self.leads_dir` FIRST, and it is not a micro-optimisation. Under the
            # flat default the write folder IS leads_dir, and a symlinked leads_dir is perfectly
            # scannable: `os.walk` scandirs its TOP argument directly, and followlinks=False
            # governs descent into the `dirnames` it discovers, not the root it was handed.
            # Measured -- with `Job Applications/Job Leads` symlinked and no layout configured,
            # read_leads returns the note, _locate finds it, and a re-scrape returns `updated`.
            # An earlier draft of this guard omitted the comparison and hard-failed every lead on
            # that working configuration ({'created': 0, 'skipped': 3} through the sink, no note
            # written, every run) with a stated reason that was false for the flat case.
            if write_dir != self.leads_dir and os.path.islink(write_dir):
                # A symlinked write folder is NOT in the scan set (`_walk` keeps os.walk's
                # followlinks=False), so a note created there is invisible to read_leads and to
                # _locate -- and therefore re-created, as a fresh duplicate, on every single run.
                # Refuse loudly rather than write into it; the sink counts this skipped.
                raise OSError(f"lead write folder {write_dir!r} is a symlink; the scan does not "
                              f"follow symlinks, so a note created there would be invisible and "
                              f"re-created every run -- move the real folder into the vault")
            os.makedirs(write_dir, exist_ok=True)
            try:
                # The SAME string the blank-note guard above ran the read's predicate over --
                # re-rendering here would put a second, unchecked set of bytes on disk.
                _write(path, rendered, exclusive=True)
                return UpsertResult(outcome="created", slug=self._slug_for(path))
            except FileExistsError:
                # #16 TOCTOU: a concurrent writer created this note between _resolve_path's
                # existence check and this exclusive create. Truncating it (the old open("w"))
                # would clobber a note we never reconciled against -- never-clobber, under
                # concurrency. Loop to re-resolve against the note that now exists.
                continue
            # A non-FileExistsError OSError (disk full, permissions) propagates out: _write has
            # already removed any 0-byte partial IT created (#24) -- and, crucially, removes it
            # ONLY when its own exclusive open succeeded, so it never unlinks a note a concurrent
            # writer owns. The sink then counts the lead skipped and keeps it out of seen.db.
        # Every attempt lost the create race (the note kept being created then deleted under
        # us). Refuse loudly rather than clobber or spin; the sink keeps it out of seen.db.
        _log.warning("vault could not create lead %r: create raced repeatedly", lead.dedup_key)
        return UpsertResult(outcome="refused")

    def _bump_last_seen_or_refuse(self, path: str, last_seen: str, outcome: str,
                                  dedup_key: str) -> str:
        """Bump last_seen, mapping a sustained CAS conflict to the store's `refused`
        concurrency-loss outcome (like the FileExistsError create-race) so no exception
        crosses the ingest sink. Shared by upsert's update AND merge branches -- they
        differ only in which outcome string a successful bump reports, so this is the
        one place that decision can drift; deleting it un-deduplicates the two branches
        back into the copy this replaces. #16."""
        try:
            self._bump_last_seen(path, last_seen)
        except VaultConflict:
            _log.warning("vault refused lead %r: last_seen bump raced repeatedly", dedup_key)
            return "refused"
        return outcome

    def _bump_last_seen(self, path: str, last_seen: str) -> None:
        """Set the last_seen line inside existing frontmatter, preserving every other key
        and the whole body verbatim. last_seen is MONOTONIC: an incoming stamp older-or-
        equal to the stored one is ignored. Routed through _cas_write, so the monotonic
        decision is re-derived from the FRESH last_seen each attempt -- a concurrent newer
        bump is respected, never regressed (#16). May raise VaultConflict; upsert absorbs
        it (Task 4)."""
        def transform(text: str) -> str:
            inner, body = _split_frontmatter(text)
            if inner is None:
                return f"---\nlast_seen: {last_seen}\n---\n{text}"
            m = re.search(r"(?m)^\s*last_seen\s*:\s*(.*)$", inner)
            if m:
                if last_seen <= m.group(1).strip().strip('"').strip("'"):
                    return text  # older-or-equal: never regress, write nothing
                inner = re.sub(r"(?m)^\s*last_seen\s*:.*$", f"last_seen: {last_seen}", inner)
            else:
                inner = f"{inner}\nlast_seen: {last_seen}"
            return f"---\n{inner}\n---\n{body}"
        _cas_write(path, transform)

    def _safe_or_blank(self, value: str, field_name: str, dedup_key: str) -> str:
        """decision 7: abstain-and-log per field, never raise -- _render_new builds a
        whole note in one call with no per-field channel to report through the way
        update_fields's callers have (url_dropped), and Lead.__post_init__'s own
        discipline is "coerce, never raise": an exception here would abort the whole
        ingest-sink loop for one malformed scraped row, which this codebase's
        per-item isolation discipline forbids."""
        safe = frontmatter_safe(value)
        if value and safe is None:
            _log.warning("vault: lead %r's %s was not frontmatter-safe; blanked",
                         dedup_key, field_name)
        return safe or ""

    def _render_new(self, lead: Lead) -> str:
        first = lead.first_seen or _today()
        last = lead.last_seen or first
        # decision 7: location/salary/role_type/role_type_source/url/source are the 6
        # non-identity interpolated fields -- company/role are guarded separately, one
        # call up, inside upsert's own new pre-check (see there), since they're the
        # vault's IDENTITY key and must refuse the whole create rather than
        # abstain-and-blank.
        location = self._safe_or_blank(lead.location, "location", lead.dedup_key)
        salary = self._safe_or_blank(lead.salary, "salary", lead.dedup_key)
        role_type = self._safe_or_blank(lead.job_type, "role_type", lead.dedup_key)
        # Guarded like the rest even though every value sluice itself writes here comes
        # from a four-member closed set (#223 §2.1): `upsert` is public and takes a
        # `Lead` any caller built, so a `"` in this field would open a second
        # frontmatter key exactly as one in `salary` would.
        role_type_source = self._safe_or_blank(
            lead.job_type_source, "role_type_source", lead.dedup_key)
        url = self._safe_or_blank(lead.url, "url", lead.dedup_key)
        source = self._safe_or_blank(lead.source, "source", lead.dedup_key)
        inner = "\n".join([
            'base: "[[Job Leads.base]]"',
            f'company: "{lead.company}"',
            f'role: "{lead.title}"',
            f'location: "{location}"',
            "status: new",
            "score: 0",
            f'source: "{source}"',
            f'salary: "{salary}"',
            f'role_type: "{role_type}"',
            # Written even when blank (#223 §2.1). A missing key and a blank one both
            # read as `assumed`, so this is not a correctness difference; it is that a
            # note whose schema varies by lead is one a human cannot scan, and the
            # five keys above already ship blank rather than being omitted.
            f'role_type_source: "{role_type_source}"',
            f'url: "{url}"',
            'glassdoor_rating: ""',
            'culture_flags: ""',
            'relevance_notes: ""',
            f"first_seen: {first}",
            f"last_seen: {last}",
        ])
        body = (
            f"# {lead.company} - {lead.title}\n\n"
            f"**Status:** new\n"
            f"**Location:** {location} | **Salary:** {salary}\n"
            f"**URL:** {url}\n"
        )
        return f"---\n{inner}\n---\n\n{body}"

    def merge_cluster(self, survivor_ref, loser_refs, *, alt_urls, first_seen, last_seen):
        """Merge a human-vetted duplicate cluster (#23). Union the audit trail onto
        the survivor -- never touching its status/scores/enrichment/body
        (never-clobber) -- and archive each loser to `_merged/` (reversible,
        invisible to read_leads). Timestamps are RE-DERIVED against the fresh
        survivor inside the CAS transform, so a caller's stale min/max can never
        regress them. The survivor write happens FIRST; losers are archived only on
        its success, so a VaultConflict -- or a MalformedNoteField, when the
        survivor's existing alt_urls is present but not a JSON list of strings --
        archives nothing. A per-loser archive OSError is logged and skipped
        (isolated), so that loser stays in the active view and the next run
        re-merges it -- never counted as merged. Returns the archived loser paths.
        See docs/.../read-path-dedup-design.md #3.

        Each archived loser is then STAMPED with the note name it was seated at
        (#81), so the write path can recognise a re-scrape of it without having to
        reconstruct that name from frontmatter -- a reconstruction three rounds of
        work could not make reliable. The stamp is surgical and comes AFTER the
        loser is counted, so it cannot un-count an archive that really happened."""
        def transform(text: str) -> str:
            inner, body = _split_frontmatter(text)
            if inner is None:
                inner, body = "", text
            existing = _fm_value(inner, "alt_urls")
            current = []
            if existing:
                try:
                    parsed = json.loads(existing)
                except ValueError:
                    parsed = None
                if not isinstance(parsed, list) or not all(isinstance(u, str) for u in parsed):
                    # A malformed alt_urls could be a human hand-edit. The old behaviour
                    # (log + reset to []) silently DISCARDED that value -- exactly the
                    # clobber never-clobber exists to prevent. Raise instead: this fires
                    # BEFORE _atomic_write and BEFORE the archive loop below, so the abort
                    # leaves the survivor's malformed value untouched and archives no
                    # loser (verified by test_malformed_alt_urls_rejects_merge).
                    raise MalformedNoteField(
                        f"{survivor_ref}: alt_urls is not a JSON list of strings: {existing!r}")
                current = parsed
            merged = list(dict.fromkeys([*current, *alt_urls]))   # order-stable union
            inner = _set_fm(inner, "alt_urls", json.dumps(merged))
            fresh_first = _fm_value(inner, "first_seen")
            if first_seen and (not fresh_first or first_seen < fresh_first):
                inner = _set_fm(inner, "first_seen", first_seen)
            fresh_last = _fm_value(inner, "last_seen")
            if last_seen and (not fresh_last or last_seen > fresh_last):
                inner = _set_fm(inner, "last_seen", last_seen)   # monotonic: only advance
            return f"---\n{inner}\n---\n{body}"
        _cas_write(survivor_ref, transform)   # raises VaultConflict/MalformedNoteField BEFORE any archive
        merged_dir = os.path.join(self.leads_dir, _MERGED_SUBDIR)
        os.makedirs(merged_dir, exist_ok=True)
        archived = []
        for ref in loser_refs:
            base = os.path.basename(ref)
            stem = base[:-3] if base.endswith(".md") else base
            try:
                # suffix_on_collision=True: an archived loser's filename is not an identity the
                # write path walks, so a numeric suffix costs nothing -- while failing to archive
                # would leave the loser active and undo #81. See _reserve_and_move.
                dest = _reserve_and_move(ref, merged_dir, base, suffix_on_collision=True)
            except OSError as e:
                # per-loser isolation: leave the loser active (it self-heals next run). The helper
                # has already removed any reservation it created. `continue`, so this loser is
                # neither counted nor stamped.
                _log.warning("dedupe: could not archive loser %s: %s", ref, e)
                continue
            archived.append(dest)
            # #81: record the name this loser was seated at, AFTER it is counted. `stem` is
            # that name, and it is the same `stem` `dest` was built from, so the probe's
            # filename pre-filter and the value it reads cannot disagree. Stamping the note
            # BEFORE the move would put the key on an ACTIVE note -- a note that was never
            # archived from anywhere -- and leave it there if the move then failed.
            _stamp_archived_from(dest, stem)
        return archived


# ── frontmatter helpers (format-preserving) ──────────────────────────────────
def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path: str, text: str, *, exclusive: bool = False) -> None:
    """Write `text` to `path`. `exclusive=True` opens with mode "x" (O_CREAT|O_EXCL, atomic at
    the OS level) so a CREATE cannot silently truncate a note a concurrent writer landed in the
    TOCTOU window between _resolve_path's existence check and here -- "x" raises FileExistsError
    instead, which upsert re-reconciles against (#16). Every other write (last_seen bumps, field
    edits) targets a note already reconciled against, so it uses the default truncating "w".

    On an exclusive create, if the OPEN succeeds but the WRITE then fails, the 0-byte partial we
    created is removed before re-raising -- a lingering partial would be adopted as a real note
    on the next re-scrape (#24). A FAILED open (FileExistsError, or EACCES/ENOSPC) creates
    nothing, so nothing is removed: ownership is 'our open returned a handle', NOT os.path.exists
    (which a race could fool into unlinking a note a concurrent writer just landed)."""
    f = open(path, "x" if exclusive else "w", encoding="utf-8")  # a failed open creates nothing
    try:
        f.write(text)
        f.close()
    except OSError:
        f.close()  # close before unlink: an open file cannot be removed on Windows
        if exclusive:
            try:
                os.unlink(path)  # our exclusive open succeeded, so this partial is OURS
            except OSError as e:
                # Even the cleanup can fail (e.g. the FS remounted read-only). Log rather than
                # swallow: a lingering partial note is a landmine a re-scrape would adopt as real.
                _log.warning("could not remove partial note %s: %s", path, e)
        raise


def _reserve_and_move(src: str, dest_dir: str, base: str, *,
                      suffix_on_collision: bool) -> str:
    """Atomically move the note at `src` into `dest_dir` under the name `base`. Returns the
    destination path actually used.

    The primitive, in ONE place, because two callers need it with different collision policies and
    a second copy is a second thing to keep correct. `os.replace(src, dest)` alone is a single
    atomic move but OVERWRITES `dest`; `os.link(src, dest) + os.unlink(src)` never overwrites but
    has a window in which a concurrent atomic save of `src` -- a human hitting save in Obsidian --
    lands between the two and is DELETED rather than moved. CodeRabbit flagged each in turn on #23.
    The shape that satisfies both: reserve `dest` with O_CREAT|O_EXCL (atomic, so a concurrent
    reserver loses rather than races), then `os.replace` whatever `src` names AT THAT INSTANT into
    it -- so a concurrent save is carried, and the only thing overwritten is our own zero-byte
    reservation.

    COLLISION POLICY is the caller's, and the two are not interchangeable:

    - `suffix_on_collision=True` (merge_cluster) takes `<stem>.<n>.md`. An archived loser's
      filename is not an identity the write path walks, so a suffix costs nothing there, while
      failing to archive would leave the loser active and undo #81.
    - `suffix_on_collision=False` (leads reconcile) raises FileExistsError. A suffix changes the
      FILENAME, which is the slug, which is the IDENTITY: the renamed note matches no candidate
      `_resolve_path` walks, so the next scrape mints a fresh note and orphans the renamed one.
      Refusing that note and reporting it is the only safe answer.

    On any OSError the reservation THIS function created is removed before the error propagates,
    so a failed move never seats a zero-byte file at a real lead's name -- which `_is_note_file`
    would call a note and `_resolve_path` would reconcile against. Ownership is proved by OUR open
    having returned a handle, never by `os.path.exists`: a concurrent writer landing a file in the
    window makes the path exist without us owning it, and unlinking it then is a clobber inside a
    clobber-fix (#16).

    The REFUSAL path reserved nothing, so it cleans up nothing -- unlinking there would delete the
    very note the refusal exists to protect.
    """
    stem = base[:-3] if base.endswith(".md") else base
    dest = os.path.join(dest_dir, base)
    n = 1
    reserved = None
    reserved_id = None
    try:
        while True:
            try:
                fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if not suffix_on_collision:
                    raise            # nothing reserved -> nothing to clean up
                dest = os.path.join(dest_dir, f"{stem}.{n}.md")
                n += 1
                continue
            # Ownership is recorded the INSTANT the open returns a handle, BEFORE anything that
            # can itself fail. `os.close` can raise, and a close that fails still leaves the
            # 0-byte file on disk -- assigning `reserved` after it leaked exactly that file: a
            # zero-byte note seated at a real lead's name, which `_is_note_file` calls a note and
            # `_resolve_path` then reconciles against.
            reserved = dest
            try:
                st = os.fstat(fd)
                reserved_id = (st.st_dev, st.st_ino)
            finally:
                os.close(fd)
            break
        os.replace(src, dest)        # atomic; overwrites only our own 0-byte reservation
        return dest
    except OSError:
        _unlink_reservation(reserved, reserved_id)
        raise


def _unlink_reservation(path: str | None, ident) -> None:
    """Remove a reservation THIS call created -- and only while it is still the same file.

    Ownership is proved by our own open having returned a handle (`path` is set nowhere else),
    but ownership at RESERVE time is not ownership at CLEANUP time. Between the two a concurrent
    writer can `os.replace` its own file onto that name, and unlinking then destroys a note we
    never owned: a clobber inside a clobber-fix, which is the #16 lesson one rung along. So the
    inode recorded from the open fd is compared with what the name resolves to NOW, and a
    mismatch leaves it alone.

    A residual remains between the stat and the unlink -- there is no portable
    unlink-if-same-inode -- so this narrows the window rather than closing it, the same trade
    `_cas_write` documents for its compare->replace gap. `ident` is None only when `fstat` itself
    failed, a window of nanoseconds after the create; the reservation is removed in that case,
    because a leaked zero-byte note at a real lead's name is the likelier harm."""
    if not path:
        return
    try:
        if ident is not None:
            st = os.stat(path)
            if (st.st_dev, st.st_ino) != ident:
                return           # someone else's file now -- never ours to remove
        os.unlink(path)
    except OSError:
        pass


def _atomic_write(path: str, text: str) -> None:
    """Replace `path`'s contents atomically: write a temp sibling, then os.replace.

    os.replace is atomic (rename(2)) on POSIX and Windows, so a concurrent reader/writer
    always sees a whole file, never a torn one -- the write half of #16's modify-path
    safety. The temp is a SAME-DIRECTORY sibling so os.replace stays on one filesystem (a
    cross-device rename raises OSError). A fresh temp is created 0600 by mkstemp, so when
    the target already exists its mode is copied onto the temp before the replace --
    otherwise a modify-write would narrow the note's permissions. On any failure the temp
    is removed before re-raising.

    Metadata beyond the mode is NOT carried across the inode swap, by design. The mode is
    the security-relevant permission (CWE-732) and is preserved. uid/gid is left to
    os.replace: for the single-user Obsidian vault this store targets the note's owner and
    the writing process are the same user, so it is unchanged -- and a non-privileged
    process could not restore a foreign uid via os.chown anyway. ACLs and security xattrs
    (e.g. macOS Finder tags) are not part of a note's content that sluice manages, are not
    portably copyable in the standard library (os.*xattr is Linux-only; ACLs have no stdlib
    API), and shutil.copystat is unusable here because it would also copy the OLD mtime onto
    a note we just modified -- freezing the timestamp Syncthing/Obsidian watch for changes.
    The atomic replace is required for the torn-file safety this module's CAS depends on, so
    the inode swap is not optional; the metadata trade-off is the accepted cost.

    A FRESH create (no pre-existing `path`, so `mode` stays None) instead lands at
    mkstemp's own 0600 rather than the umask-default mode `_write(exclusive=True)` gives
    a newly-created lead note. That is a deliberate, harmless narrowing: the only caller
    that can reach this function with no pre-existing target is `write_document`'s
    rejected-leads digest, a store-managed document rather than a lead note, and every
    other create path in this module goes through `_write`, not here."""
    d = os.path.dirname(path) or "."
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        mode = None
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".sluice-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_write_locks: dict[str, threading.Lock] = {}
_write_locks_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    """One process-lifetime lock per resolved path (#131): closes the
    INTRA-process race _cas_write's own recheck cannot catch on its own --
    two threads in the SAME process can both pass "unchanged since capture"
    before either commits, so both report a successful write while only the
    LAST os.replace actually lands, silently discarding the other's
    transform. Reproduced 100% of 200 rounds under a synchronized
    threading.Barrier before this fix.

    Deliberately per-PATH, not one global lock: two writers to DIFFERENT
    notes must never contend, only two racing the SAME note.

    Deliberately in-memory, not a file-based advisory lock: this closes only
    the NEW intra-process gap #131's long-lived, genuinely multi-threaded
    `mcp serve` process introduces (MCPServer dispatches sync tool calls to
    separate worker threads, confirmed empirically) -- it does not attempt
    to protect against a DIFFERENT PROCESS (a human editing the note by hand
    in Obsidian, a second CLI invocation), which is #16's own already-
    accepted external risk, unchanged by this fix and still caught (when the
    race is sustained) by the existing VaultConflict-after-retries path. An
    in-memory lock needs no stale-lock recovery -- a crashed process simply
    drops it -- which a file-based lock would need and which is exactly the
    kind of cross-platform, crash-recovery design surface this fix
    deliberately stays out of.

    The registry grows by one entry per unique path ever PASSED to
    _cas_write for the life of the process (including calls that end up
    being no-ops); for a personal job-vault's note count (hundreds, not
    millions) this is not worth adding eviction for."""
    # realpath, not abspath (Minor #4, final whole-branch review): this
    # module deliberately uses realpath elsewhere for the identical reason
    # (see write_document's own "realpath, not abspath: a symlink INSIDE the
    # store..." comment) -- the whole point of this lock is ONE lock object
    # per real file, and abspath normalizes text but not symlinks, so two
    # textual paths to the same file would otherwise get two different
    # locks, silently reintroducing the race this function exists to close.
    resolved = os.path.realpath(path)
    with _write_locks_guard:
        lock = _write_locks.setdefault(resolved, threading.Lock())
    return lock


def _cas_write(path: str, transform, *, retries: int = _RMW_RACE_RETRIES) -> bool:
    """Apply a surgical edit under compare-and-set. `transform(current_text) -> new_text`
    is re-derived from the CURRENT bytes each iteration. Commit (atomic replace) only if
    the file is byte-unchanged since capture; otherwise re-derive from the fresh content
    and retry. Returns True if a change was committed, False if the transform was a no-op
    (new == text -- an older-or-equal last_seen, an already-present tag, an only_if_absent
    field already set). Raises VaultConflict after `retries` lost races. This is the
    modify-path twin of upsert's create-race loop (#16). The second _read is NOT redundant
    with the first: an external process can write during `transform`, and the freshness
    check now guards BOTH outcomes -- the no-op AND the commit -- so a decision is never
    made against the stale capture. Without it, a presence/absence transform (e.g.
    append-if-tag-absent) that reads as a no-op against the STALE text (tag present at
    capture) but is NOT a no-op against the LIVE text (a racer concurrently removed the
    tag) would silently return False and drop the needed edit.

    Serialized per-path via _lock_for (#131): held across the WHOLE call, not
    per-retry-attempt, so two IN-PROCESS threads can never both be inside the
    read-transform-recheck-write cycle for the same path at once -- see
    _lock_for's own docstring for why this is in-memory and per-path, and
    what it does and does not protect against.

    `transform` MUST be pure text -> text and MUST NOT call back into any
    store write method for the same path: the lock is a plain (non-reentrant)
    Lock, so a nested call deadlocks. An RLock is deliberately NOT used --
    it would let a nested write satisfy its own freshness check inside an
    outer transaction and silently defeat the CAS invariant this exists for."""
    lock = _lock_for(path)
    with lock:
        for _ in range(retries):
            text = _read(path)
            new = transform(text)
            if _read(path) != text:
                continue  # changed under us since capture -> re-derive from the fresh content
            if new == text:
                return False  # genuine no-op on the CURRENT content
            _atomic_write(path, new)
            return True
        raise VaultConflict(path)


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_inner, body). frontmatter_inner is None when the note
    has no leading `---` block."""
    m = _FM_RE.match(text)
    if not m:
        return None, text
    return m.group(1), m.group(2)


def _fm_value(inner: str | None, key: str) -> str:
    """First value for `key` in a frontmatter block, stripped of quotes."""
    if not inner:
        return ""
    m = re.search(rf"(?m)^\s*{re.escape(key)}\s*:\s*(.*)$", inner)
    return m.group(1).strip().strip('"').strip("'") if m else ""


def _counts_as_blank(value: str, blank_values: frozenset | None) -> bool:
    """Whether `value` (a fresh `_fm_value` read) satisfies `update_fields`'s
    `require_blank` guard: genuinely empty, or -- when `blank_values` names a set --
    a fold-match against it (#151). Only `value` -- the fresh stored side -- is folded
    through `fold_company_answer`, so "Unknown", "Unknown.", " unknown " and "UNKNOWN!"
    are the same value to this check, exactly as they already are to the resolution gate
    that decided the write was safe. `blank_values` members are compared VERBATIM, never
    folded -- mirroring `_status.normalize` folding only the stored status and taking
    `require_status` as already-canonical -- so every member the caller passes MUST
    already be pre-folded (`core.leads.NON_ANSWER_COMPANIES` is; see its own definition).
    An unfolded member silently never matches anything, which is why this asymmetry is
    stated here rather than left implicit. Anything else, including a value that merely
    differs from the one being written, is NOT blank -- the whole point of require_blank
    is refusal on presence, and this helper only ever narrows what counts as absent,
    never what counts as a difference."""
    if not value.strip():
        return True
    return blank_values is not None and fold_company_answer(value) in blank_values


def _set_fm(inner: str, key: str, literal: str) -> str:
    """Replace `key:`'s line in a frontmatter block, or append it if absent.
    `literal` is written verbatim, so the caller controls quoting.

    The REPLACEMENT is a callable, not an f-string, and that is the whole point:
    `re.sub` interprets backslash escapes in a STRING replacement template, so a
    literal carrying one was rewritten on its way through this function rather
    than written. All three arms were measured: `"Foo\\Bar Ltd"` raised
    `re.PatternError: bad escape \\B` (and `re.PatternError` is not
    `VaultConflict`, so triage's `except VaultConflict` could not catch it and one
    scraped company killed a whole batch mid-run); `"Foo\\nBar"` silently became a
    real newline and split the frontmatter; `"Foo\\g<0>Bar"` silently expanded to
    the matched line. A callable replacement is substituted verbatim, so all three
    are now closed HERE, once, for every caller -- it is not the writer's problem.

    What DOES remain the caller's problem is anything structural inside the quoted
    scalar it hands over, because this layer cannot tell a wrapping quote from an
    embedded one: a blanket character check here would reject the quotes every
    existing quoted caller (`glassdoor_rating`, `culture_flags`) relies on. A
    caller writing unmediated external content (a scraped page, a parsed email, a
    CLI value that may have been pasted rather than typed) therefore still needs
    its own pre-quote guard; see `frontmatter_safe` below."""
    pat = rf"(?m)^\s*{re.escape(key)}\s*:.*$"
    if re.search(pat, inner):
        return re.sub(pat, lambda _m: f"{key}: {literal}", inner, count=1)
    return f"{inner}\n{key}: {literal}" if inner else f"{key}: {literal}"


_FRONTMATTER_UNSAFE_CHARS = ('"', "\\")


def frontmatter_safe(candidate: str | None) -> str | None:
    """Whether `candidate` may be quoted and written verbatim as a frontmatter
    scalar via `_set_fm` (`key: "<candidate>"`): returns it unchanged when safe,
    `None` (abstain, never guess or mangle) otherwise.

    Three independent rejections:
     * falsy / all-whitespace -- nothing worth writing.
     * not printable -- `str.isprintable()` rejects the C0/C1 control class,
       U+0085 NEL, and every Zl/Zp separator: characters sluice's own line-based
       `_fm_dict`/`_fm_value` (split on "\\n", `(?m)`) never sees, but a real YAML
       parser reading the note back either refuses outright or (for NEL) silently
       folds to a space. Measured against PyYAML 6.0.3.
     * `"`/`\\` -- structural inside the double-quoted scalar `_set_fm`'s callers
       write: `"` closes the scalar early (`ParserError`); `\\` opens a YAML
       escape sequence -- some sequences raise (`ScannerError`), others (`\\n`)
       silently become a real newline inside the value.

    Every `_set_fm` caller writing unmediated external content needs this first:
    `_set_fm` itself cannot tell a wrapping quote from an embedded one, so a
    blanket check inside it would reject the quotes every quoted caller relies
    on (see `_set_fm`'s docstring)."""
    if not candidate or not candidate.strip() or not candidate.isprintable():
        return None
    return None if any(c in candidate for c in _FRONTMATTER_UNSAFE_CHARS) else candidate


def _archived_from(inner: str | None) -> str | None:
    """The note name `merge_cluster` recorded for an archived loser (#81), or None when the
    entry carries no readable one -- a LEGACY archive from before the field shipped, a value
    a hand edit broke, or a stamp that FAILED at merge time (`_stamp_archived_from` swallows
    its own error by design; see there, and `_archived_match`'s docstring for why that third
    source is a RUNTIME population on an otherwise fully upgraded install, not only a
    pre-upgrade concern). Every one of those collapses to None on purpose, deliberately not
    counted here since a fixed count is exactly what went stale once already: the probe then
    falls back to exact-filename matching, which fails toward creating a visible duplicate
    rather than toward suppressing a real job.

    Deliberately NOT read through `_fm_dict`/`_fm_value`: both end in
    `.strip('"').strip("'")`, which eats a real edge character. `_sanitize` maps `"` out of
    a note name but NOT `'`, so a company or title whose edge character is an apostrophe
    would come back shortened -- and a shortened name compares unequal to the candidate it
    was seated at, which is precisely the reconstruction failure this field exists to
    remove. The value is written by `json.dumps`, so `json.loads` returns it exactly."""
    if not inner:
        return None
    m = re.search(rf"(?m)^\s*{re.escape(_ARCHIVED_FROM)}\s*:\s*(.*)$", inner)
    if not m:
        return None
    try:
        value = json.loads(m.group(1).strip())
    except ValueError:
        return None
    return value if isinstance(value, str) else None


def _stamp_archived_from(path: str, seated: str) -> None:
    """Record, inside a note just archived to `_merged/`, the name it was seated at (#81).

    Surgical, through `_cas_write`/`_set_fm`: never-clobber binds here as much as anywhere
    else, so the loser keeps its status, scores, enrichment and body byte-for-byte. Written
    with `json.dumps` -- valid YAML for Obsidian, and an exact round trip back through
    `_archived_from`, which a bare quoted literal is not.

    Best effort by design, because both alternatives are worse at the caller. The move has
    already happened and the loser is already counted merged: UN-COUNTING it reports a
    COMPLETED merge as `partial` (`app.py`) and invites a re-merge of a note no longer in
    the active view, while letting the error OUT escapes `dedupe_merge`'s
    `except (VaultConflict, MalformedNoteField)` and discards the whole per-cluster results
    list.

    The cost is real, and it is not only a missed suppression. An unstamped entry joins the
    LEGACY population `_archived_match` matches by exact filename, which is wrong in BOTH
    directions: a stamp-failed `X - Y.1.md` stops suppressing the lead it archives (a
    visible duplicate -- the safe direction), AND it starts matching a never-seen job
    genuinely titled `Y.1`, on the SAME arm (a url match, or a location overlap when the
    urls don't match) -- irreversible. This is therefore a RUNTIME
    source of legacy entries on an otherwise fully upgraded install, not a pre-upgrade
    concern -- `_archived_match`'s docstring enumerates it as such."""
    def transform(text: str) -> str:
        inner, body = _split_frontmatter(text)
        if inner is None:
            # No frontmatter block at all means no company/role either, and the probe skips
            # such an entry whatever this field would say. Inventing a block would be a
            # wholesale rewrite of a file we do not understand -- leave it exactly as it is.
            return text
        inner = _set_fm(inner, _ARCHIVED_FROM, json.dumps(seated, ensure_ascii=False))
        return f"---\n{inner}\n---\n{body}"
    try:
        _cas_write(path, transform)
    except (OSError, VaultConflict) as e:
        _log.warning("dedupe: could not record the archived name for %s: %s", path, e)


def _del_fm(inner: str, key: str) -> str:
    """Remove `key`'s line(s) from a frontmatter block; return `inner` unchanged if
    absent. The counterpart to _set_fm, which only replaces/appends -- sign_off (#60)
    needs a true delete to clear a resolved marker. Line-based (like
    _collapse_status_lines) so it leaves no stray blank line and cannot disturb the body."""
    pat = re.compile(rf"^\s*{re.escape(key)}\s*:")
    return "\n".join(ln for ln in inner.split("\n") if not pat.match(ln))


def _collapse_status_lines(inner: str, canonical: str) -> str:
    """Return `inner` with every status line removed and a single canonical
    `status: <value>` line placed where the first one was (or appended). Fixes the
    legacy duplicate-status-key corruption without disturbing any other key."""
    out, inserted = [], False
    for line in inner.split("\n"):
        if re.match(r"^\s*status\s*:", line):
            if not inserted:
                out.append(f"status: {canonical}")
                inserted = True
            # drop any further status lines
        else:
            out.append(line)
    if not inserted:
        out.append(f"status: {canonical}")
    return "\n".join(out)


def _normalize_status_transform(text: str) -> str:
    """Collapse a note's status lines to their single canonical value, recomputed from the
    CURRENT text. Abstain (return text unchanged -> a _cas_write no-op) when the fresh
    status lines DISAGREE: a concurrent edit that introduced a conflict must be reported,
    never auto-guessed (never-regress). #16: derive from fresh, never from the snapshot."""
    inner, body = _split_frontmatter(text)
    if inner is None:
        return text
    norms = [_status.normalize(r.strip())
             for r in re.findall(r"(?m)^\s*status\s*:\s*(.*)$", inner)]
    if len(set(norms)) > 1:
        return text
    canonical = norms[0] if norms else ""
    return f"---\n{_collapse_status_lines(inner, canonical)}\n---\n{body}"


def _fm_dict(inner: str | None) -> dict:
    """Parse a frontmatter block into a flat dict. Simple line-based `key: value`
    parse (the vault notes are flat), values stripped of surrounding quotes."""
    out: dict = {}
    if not inner:
        return out
    for line in inner.split("\n"):
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def parse_frontmatter(text: str) -> dict:
    """Public wrapper over `_fm_dict` for callers OUTSIDE this module.

    Called by `onboard/plan.py`'s `_render_candidate` (Task 6): it renders a Candidate Profile
    note and verifies its own output round-trips through the REAL reader before returning it, via
    `FrontmatterRoundTripError`. Exposing the reader here is strictly better than a second
    frontmatter parser in `onboard/`, which would drift from this one -- and drift is exactly what
    that verification exists to catch.

    Takes a WHOLE note (with its `---` fences), not the inner block, so a caller
    verifies the same bytes it is about to write.
    """
    return _fm_dict(_split_frontmatter(text)[0])


def parse_candidate_profile(text: str) -> CandidateProfile:
    """Text (a whole Candidate Profile note, fences and all) -> a CandidateProfile, filtered to
    known fields. The pure half of `Vault.read_candidate_profile`, which is this function plus a
    file read -- factored out so a caller with TEXT that never touched a file (`cmd_init`'s
    freshly-rendered `plan.candidate_text`) can run it through the exact same parse the note will
    be read back through, rather than a second predicate over a different domain that could
    silently drift the moment a candidate question's answer key is not mapped into
    `onboard/plan.py`'s `_CANDIDATE_KEY_BY_ANSWER` -- see `cli.py::cmd_init`'s write-block comment
    for the deadlock that drift once caused end to end. Do not add a THIRD frontmatter parser
    anywhere for this -- `_fm_dict` plus `parse_frontmatter` are the only two, and this is a thin
    CandidateProfile-shaped wrapper over the first, not a new one.
    """
    fm = parse_frontmatter(text)
    known = {f.name for f in dataclasses.fields(CandidateProfile)}
    return CandidateProfile(**{k: v for k, v in fm.items() if k in known})


def _title_key(t: str) -> str:
    """Whitespace-normalised title, used for BOTH the capped-collision comparison and its
    digest, so trailing/duplicate spaces from parsing variance neither spuriously split a
    >120-char title nor let two spellings of it drift to different digests."""
    return " ".join(t.split())


def _title_digest(t: str) -> str:
    """A short, STABLE discriminator for a title the 120-char filename cap truncated -- same
    title always yields the same digest, so a re-scrape re-keys to the same note (unlike a
    per-scrape URL hash, which would mint one note per run). Closes #5's same-prefix residual
    when the location does not already split the two titles."""
    return hashlib.sha256(_title_key(t).encode("utf-8")).hexdigest()[:8]


def _sanitize(s: str) -> str:
    """Map every character illegal in a filename to '-': the path separators `/ \\` (a
    scraped `../` or `..\\..\\` company/title must not traverse out of the leads dir), the
    rest of the Windows-reserved set `< > : " | ? *`, and the C0 control chars \\x00-\\x1f
    (illegal on Windows, hostile to Syncthing/Obsidian). `re.sub` maps each matched char to
    one '-', so the result stays LENGTH-PRESERVING -- candidate 1 is byte-identical for
    every existing note, since real company/title/location strings contain none of these.

    On POSIX `< > " | ? *` were previously legal and passed through, so an existing note
    whose name contains one would migrate (rename -> duplicate on next scrape). That
    population is ~0 (job strings almost never carry these; #5's location suffixes are
    brand new), which is why the fix is applied rather than narrowed. See #5, #44."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", s)


# Every NON_ANSWER_COMPANIES member, run through the SAME `_sanitize` a company string goes
# through on its way into a filename stem, then casefolded so the comparison in
# `_is_placeholder_head` is case-insensitive like `is_placeholder_company` itself. Computed
# once at import time -- `_sanitize` must already be defined above this line for that to work,
# which is why this sits here rather than beside `_is_placeholder_head`'s call site up in
# `_frontmatter_name`. ~19 members, never mutated at runtime.
_SANITIZED_NON_ANSWERS = frozenset(_sanitize(c).casefold() for c in NON_ANSWER_COMPANIES)


def _is_placeholder_head(head: str) -> bool:
    """Is `head` -- text `_frontmatter_name` pulled from a note's FILENAME stem -- a placeholder
    company, once the filename's own `_sanitize` pass is accounted for?

    `is_placeholder_company` alone is not enough here, and that gap is exactly CodeRabbit
    finding 3 (#151): `head` is text already run through `_sanitize` when the note was
    created, so a company of "N/A" is seated on disk as "N-A - <role>.md" (`_sanitize` maps
    the filename-illegal `/` to `-`). `is_placeholder_company("N-A")` folds and compares
    against the UNSANITIZED members of NON_ANSWER_COMPANIES, and "n-a" is not one of them --
    only "n/a" is -- so that note was invisible to the rename pass forever, even after its
    frontmatter company was correctly backfilled.

    The fix sanitizes the CANDIDATE side instead of trying to invert `_sanitize` on `head`:
    `_sanitize` is lossy (`/`, `\\`, `:`, `"`, `|`, `?`, `*` and every C0 control char all
    collapse to the same `-`), so there is no single original string to recover from "N-A"
    even in principle -- comparing forward, not inverting backward, is the only sound
    direction. `fold_company_answer`, not a bare `.casefold()`, folds `head` the same way
    every other placeholder comparison in this codebase folds a candidate (strip, then
    trailing `.`/`!`, then casefold), so this stays in step if that folding rule ever changes."""
    return is_placeholder_company(head) or fold_company_answer(head) in _SANITIZED_NON_ANSWERS


def _clamp_bytes(s: str, limit: int) -> str:
    """Largest UTF-8 prefix of `s` within `limit` bytes, never splitting a codepoint.
    A non-positive budget holds nothing -> "" (a NEGATIVE slice would instead keep all
    but the last few bytes, silently defeating the cap). Slicing the encoded bytes can
    cut mid-sequence; decode(errors="ignore") then drops the incomplete trailing bytes,
    which IS the 'never split a codepoint' guarantee."""
    if limit <= 0:
        return ""
    return s.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
