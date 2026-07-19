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
import os
import re
from datetime import date

from sluice.core import status as _status
from sluice.core.leads import SAME, UNKNOWN, Lead, _norm_url, same_opportunity
from sluice.core.log import get_logger
from sluice.core.protocols import LeadNote

_LEADS_SUBDIR = os.path.join("Job Applications", "Job Leads")
_EXP_SUBDIR = os.path.join("Job Applications", "Experience Library")
_MYCV_BASELINE = os.path.join("My CV", "CV.md")
_CRITERIA_RELPATH = os.path.join("Job Applications", "Judging Profile.md")
_DEFAULT_VAULT = "./vault"

_SEP = " - "        # note-name separator; identity-determining, stays a literal (never config)
_SUFFIX_MAX = 40    # max chars of the location suffix on candidate 2; identity-determining literal
_CHAR_CAP = 120     # max chars of a note stem before the byte-clamp; identity-determining literal
_CREATE_RACE_RETRIES = 3  # #16: bounded re-reconciles when a create loses the TOCTOU race

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


# The store contract's note type. `VaultNote` survives as an alias because this module
# is the vault's own, but the type the SEAM speaks is LeadNote: `ref` is an opaque
# handle (a path here, a row id in another store) and `slug` is issued by the store
# rather than re-derived from a filename by four separate callers.
VaultNote = LeadNote


class Vault:
    def __init__(self, dir: str | None = None, *, baseline_rel: str = _MYCV_BASELINE,
                 location_noise_words=()):
        self.dir = dir or os.environ.get("VAULT_DIR", _DEFAULT_VAULT)
        self.leads_dir = os.path.join(self.dir, _LEADS_SUBDIR)
        self.baseline_rel = baseline_rel
        self._name_max_cache: int | None = None
        # Fed raw into same_opportunity -> _compare_locations, which tokenizes it. #5's
        # split policy knob; empty by default (abstain). See core/config.py.
        self._noise = frozenset(location_noise_words or ())

    def _slug_for(self, path: str) -> str:
        """The lead's stable identity. For a markdown vault that is the filename without
        its extension -- exactly what apply/select.py, apply/engine.py, track/classify.py
        and track/engine.py each used to recompute for themselves."""
        name = os.path.basename(path)
        return name[:-3] if name.endswith(".md") else name

    # ── paths ────────────────────────────────────────────────────────────────
    def _name_max(self) -> int:
        """The filesystem's max filename length in BYTES for the leads dir, cached.
        os.pathconf needs an existing path; in the normal flow upsert makes leads_dir
        before _path_for runs. A direct _path_for call before the dir exists (e.g. a
        unit test) just takes the 255 fallback below, which also covers filesystems
        where pathconf is unsupported (some network/FUSE mounts).

        pathconf can also RETURN -1 (a value, not an exception) when NAME_MAX is
        indeterminate. Uncaught, a non-positive limit would drive _path_for's byte
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
        BOTH name candidates (#5's clean `Company - Title` and its `- Location` variant) go
        through this one helper, so their truncation can never drift -- a candidate 2 that
        sanitized or capped differently would mis-key and reintroduce a duplicate.

        `.replace` is a length-preserving per-char map, so `stem.replace()[:120]` equals the
        old `[:120].replace()` char for char: candidate 1 stays byte-identical to the old
        `_path_for`, so no existing note's identity moves (zero migration). The suffix is
        bounded to _SUFFIX_MAX BEFORE the stem arithmetic, so the stem budget can never go
        negative (a negative index silently keeps 'all but the last N chars'). The final
        byte-clamp is #24's NAME_MAX safety, applied to both candidates."""
        stem = _sanitize(stem)
        if suffix:
            suffix = _sanitize(suffix)[:_SUFFIX_MAX]
            name = stem[:_CHAR_CAP - len(_SEP) - len(suffix)] + _SEP + suffix
        else:
            name = stem[:_CHAR_CAP]
        return _clamp_bytes(name, self._name_max() - len(b".md"))

    def _path_for(self, lead: Lead) -> str:
        """The clean `Company - Title` note path (candidate 1 of #5's walk). Unchanged in
        output from before #5, so an existing note is UPDATED in place, never duplicated."""
        name = self._note_name(f"{lead.company} - {lead.title}")
        return os.path.join(self.leads_dir, f"{name}.md")

    def _resolve_path(self, lead: Lead) -> tuple[str | None, str]:
        """Walk the nameable candidates and return (path, action), action one of
        "create"/"update"/"merge"/"refuse". Candidate 1 is the clean `Company - Title`
        name (always); candidate 2 adds the location suffix (only when location is
        non-empty). Every verdict terminates in place EXCEPT DIFFERENT, which advances --
        so a note is split only on PROVEN difference, never on the absence of evidence.
        Running out of candidates (every one a note proven different) is REFUSE: no path
        can be written without clobbering a different job, so path is None. See #5."""
        stem = f"{lead.company} - {lead.title}"
        names = [self._note_name(stem)]
        if lead.location:
            names.append(self._note_name(stem, lead.location))
        for name in names:
            path = os.path.join(self.leads_dir, f"{name}.md")
            if not os.path.exists(path):
                return path, "create"
            inner, _ = _split_frontmatter(_read(path))
            verdict = same_opportunity(_fm_dict(inner), lead, self._noise)
            if verdict == SAME:
                return path, "update"
            if verdict == UNKNOWN:
                return path, "merge"
            # DIFFERENT -> advance to the next nameable candidate
        return None, "refuse"

    def ensure_stfolder(self) -> None:
        """Syncthing silently refuses to sync a vault root missing its .stfolder
        marker (state=idle, never drains). Recreate it defensively."""
        os.makedirs(os.path.join(self.dir, ".stfolder"), exist_ok=True)

    # ── dedup ────────────────────────────────────────────────────────────────
    def existing_keys(self) -> set[str]:
        """Every lead note's url, normalized into the same key space as
        Lead.dedup_key, so the engine can drop already-vaulted leads."""
        keys: set[str] = set()
        if not os.path.isdir(self.leads_dir):
            return keys
        for name in os.listdir(self.leads_dir):
            if not name.endswith(".md"):
                continue
            try:
                inner, _ = _split_frontmatter(_read(os.path.join(self.leads_dir, name)))
            except OSError:
                continue
            url = _fm_value(inner, "url")
            if url:
                keys.add(_norm_url(url))
        return keys

    # ── read ─────────────────────────────────────────────────────────────────
    def read_leads(self, statuses: set | None = None) -> list:
        """Every lead note as a VaultNote (frontmatter parsed, status normalized),
        filtered to `statuses` (compared against the normalized status) when
        given. This is the read seam triage consumes; the sink still writes Leads."""
        out: list = []
        if not os.path.isdir(self.leads_dir):
            return out
        want = {_status.normalize(s) for s in statuses} if statuses else None
        for name in sorted(os.listdir(self.leads_dir)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(self.leads_dir, name)
            try:
                inner, body = _split_frontmatter(_read(path))
            except OSError:
                continue
            fm = _fm_dict(inner)
            st = _status.normalize(fm.get("status", ""))
            if want is not None and st not in want:
                continue
            out.append(LeadNote(ref=path, slug=self._slug_for(path),
                                fm=fm, body=body, status=st))
        return out

    def update_fields(self, ref, fields: dict, *,
                      append_note: str | None = None,
                      note_tag: str | None = None) -> None:
        """Surgically set frontmatter keys (values are literal YAML scalars) and
        leave the body byte-for-byte intact. Optionally append a guarded note to
        relevance_notes: skipped if note_tag is already present, so re-runs are
        idempotent. Callers control quoting (matching _render_new's literal lines)."""
        inner, body = _split_frontmatter(_read(ref))
        if inner is None:
            inner, body = "", _read(ref)
        for key, literal in fields.items():
            inner = _set_fm(inner, key, literal)
        if append_note and note_tag:
            current = _fm_value(inner, "relevance_notes")
            if note_tag not in current:
                merged = (current + " " + append_note).strip()
                inner = _set_fm(inner, "relevance_notes", f'"{merged}"')
        _write(ref, f"---\n{inner}\n---\n{body}")

    def append_body_section(self, ref, tag: str, section_md: str) -> bool:
        """Append a markdown section to the note body, idempotently: if `tag` is
        already anywhere in the file, do nothing and return False. Body/frontmatter
        otherwise untouched. Callers embed `tag` in `section_md` (e.g. an HTML
        comment) so re-runs are detected."""
        text = _read(ref)
        if tag in text:
            return False
        sep = "" if text.endswith("\n") else "\n"
        _write(ref, f"{text}{sep}\n{section_md}\n")
        return True

    # ── cv sub-app reads/writes ──────────────────────────────────────────────
    def read_experience_entries(self, verified_only: bool = True) -> list[dict]:
        """Experience Library entries as dicts. `_inbox/` (agent-authored candidates)
        is never read. verified_only keeps entries carrying a truthy `verified:` field."""
        base = os.path.join(self.dir, _EXP_SUBDIR)
        out = []
        if not os.path.isdir(base):
            return out
        for name in sorted(os.listdir(base)):
            if not name.endswith(".md"):
                continue  # skips the _inbox/ subdir
            path = os.path.join(base, name)
            inner, body = _split_frontmatter(_read(path))
            fm = _parse_fm_spaced(inner)
            entry = {
                "path": path, "title": name[:-3],
                "company": fm.get("Company", ""), "category": fm.get("Category", ""),
                "best_for": fm.get("Best For", ""), "metrics": fm.get("Metrics", ""),
                "verified": fm.get("verified") or None, "body": body.strip(),
            }
            if verified_only and not entry["verified"]:
                continue
            out.append(entry)
        return out

    def read_criteria(self) -> str:
        """The user's judging criteria, from their editable source of truth. Returns ""
        when unset; the caller falls back to the shipped (opinion-free) default.

        This was `build_system_prompt(vault.dir)`: triage reached THROUGH the store to a
        filesystem path. `.dir` is not on the Store contract and a SQLite store has none,
        so a second store would have AttributeError'd on the judge's critical path.
        """
        try:
            return _read(os.path.join(self.dir, _CRITERIA_RELPATH))
        except OSError:
            return ""

    def write_document(self, rel: str, text: str) -> str:
        """Write a store-managed document (the rejected-leads digest). Returns an opaque
        handle. Also formerly an os.path.join onto `vault.dir`.

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
        path = os.path.realpath(os.path.join(root, rel))
        if os.path.isabs(rel) or os.path.commonpath([root, path]) != root:
            raise ValueError(f"write_document: '{rel}' escapes the store root")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write(path, text)
        return path

    def read_baseline(self) -> str:
        """Where the baseline CV lives is the store's business (configured on the
        store), not a path a caller passes in."""
        return _read(os.path.join(self.dir, self.baseline_rel))

    def set_tailored_cv(self, ref, value: str) -> None:
        """Additive: set the tailored_cv frontmatter field, body byte-for-byte intact."""
        self.update_fields(ref, {"tailored_cv": value})

    def normalize_all_statuses(self, dry_run: bool = False) -> dict:
        """Canonicalize every lead note's status: fix value drift (dismissed ->
        dismiss) and quoting ("new" -> new), and collapse the DUPLICATE status
        lines the legacy judge writers left behind into a single canonical line.
        A note whose duplicate status lines DISAGREE (e.g. dismiss vs shortlist)
        is left untouched and reported under "conflicts" for manual resolution,
        never auto-guessed. Body untouched; unknown values reported."""
        summary = {"changed": 0, "unchanged": 0, "unknown": [], "conflicts": []}
        for note in self.read_leads():
            inner, body = _split_frontmatter(_read(note.ref))
            if inner is None:
                summary["unchanged"] += 1
                continue
            raws = re.findall(r"(?m)^\s*status\s*:\s*(.*)$", inner)
            norms = [_status.normalize(r.strip()) for r in raws]
            if len(set(norms)) > 1:  # conflicting duplicate statuses -> hands off
                summary["conflicts"].append(
                    (os.path.basename(note.ref), sorted(set(norms))))
                continue
            canonical = norms[0] if norms else ""
            if not _status.is_canonical(canonical):
                summary["unknown"].append(canonical)
            status_lines = [line for line in inner.split("\n")
                            if re.match(r"^\s*status\s*:", line)]
            already = len(status_lines) == 1 and status_lines[0].strip() == f"status: {canonical}"
            if already:
                summary["unchanged"] += 1
            else:
                summary["changed"] += 1
                if not dry_run:
                    _write(note.ref,
                           f"---\n{_collapse_status_lines(inner, canonical)}\n---\n{body}")
        return summary

    # ── upsert ───────────────────────────────────────────────────────────────
    def upsert(self, lead: Lead) -> str:
        """Reconcile an incoming lead against the existing notes. Returns one of
        "created" | "updated" | "merged" | "refused". UPDATE and MERGE bump ONLY last_seen
        (never-clobber); REFUSE writes nothing -- every name candidate is a note proven
        DIFFERENT, so writing would clobber a different job. See #5.

        The create is EXCLUSIVE (`_write(..., exclusive=True)`): if a concurrent writer (another `ingest run`,
        or a human/Obsidian) creates the note in the window between _resolve_path's existence
        check and the write, the create raises FileExistsError instead of TRUNCATING that
        note, and we loop to re-reconcile against it. This closes the TOCTOU clobber (#16).
        The loop is bounded because a re-resolve finds the path now occupied, so it terminates
        -- as update/merge, a fresh "created" at the NEXT name candidate (when the raced note
        is a DIFFERENT job, so _resolve_path advances past it), or refuse. Only sustained
        create/delete flapping exhausts the retries, which refuses loudly (writing nothing)
        rather than clobbering or spinning."""
        for _ in range(_CREATE_RACE_RETRIES):
            path, action = self._resolve_path(lead)
            if action == "refuse":
                # Loud, not silent, and writes NOTHING -- not the note, and not the leads dir
                # or Syncthing marker (below): every name candidate is a note proven DIFFERENT,
                # so any write would clobber a different job. The sink counts this and keeps the
                # lead out of seen.db, so it is retried (and re-reported) next run rather than
                # lost. Reachable only pathologically (a note whose frontmatter contradicts its
                # filename, or a byte-clamp collapse on a tiny NAME_MAX). See #5.
                _log.warning("vault refused lead %r: every name candidate is a note proven different",
                             lead.dedup_key)
                return "refused"
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
                self._bump_last_seen(path, lead.last_seen or _today())
                return "updated"
            if action == "merge":
                # We could not prove same-or-different, so we do NOT split (that would mint a
                # note per scrape -- unbounded). Bump last_seen like an update; the difference
                # is only that the count is reported separately so the merge is visible.
                self._bump_last_seen(path, lead.last_seen or _today())
                return "merged"
            try:
                _write(path, self._render_new(lead), exclusive=True)
                return "created"
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
        return "refused"

    def _bump_last_seen(self, path: str, last_seen: str) -> None:
        """Set the last_seen line inside existing frontmatter, preserving every
        other key, its value formatting, and the whole body verbatim.

        last_seen is MONOTONIC: an incoming stamp OLDER-OR-EQUAL to the stored one is
        ignored, never written back. The note WAS seen on the newer date; a board that
        re-lists a role carrying a stale date must not drag the marker into the past.
        A MISSING last_seen (first sighting, or a legacy note without one) always writes.
        ISO YYYY-MM-DD sorts lexicographically = chronologically, so a plain string
        compare IS the date compare. The upsert outcome ("updated"/"merged") is decided
        by _resolve_path and is unaffected by whether the stamp actually moved."""
        inner, body = _split_frontmatter(_read(path))
        if inner is None:  # note without frontmatter - leave body, add a header
            inner, body = f"last_seen: {last_seen}", _read(path)
        else:
            m = re.search(r"(?m)^\s*last_seen\s*:\s*(.*)$", inner)
            if m:
                if last_seen <= m.group(1).strip().strip('"').strip("'"):
                    return  # older-or-equal: never regress, write nothing
                inner = re.sub(r"(?m)^\s*last_seen\s*:.*$", f"last_seen: {last_seen}", inner)
            else:
                inner = f"{inner}\nlast_seen: {last_seen}"
        _write(path, f"---\n{inner}\n---\n{body}")

    def _render_new(self, lead: Lead) -> str:
        first = lead.first_seen or _today()
        last = lead.last_seen or first
        inner = "\n".join([
            'base: "[[Job Leads.base]]"',
            f'company: "{lead.company}"',
            f'role: "{lead.title}"',
            f'location: "{lead.location}"',
            "status: new",
            "score: 0",
            f'source: "{lead.source}"',
            f'salary: "{lead.salary}"',
            f'role_type: "{lead.job_type}"',
            f'url: "{lead.url}"',
            'glassdoor_rating: ""',
            'culture_flags: ""',
            'relevance_notes: ""',
            f"first_seen: {first}",
            f"last_seen: {last}",
        ])
        body = (
            f"# {lead.company} - {lead.title}\n\n"
            f"**Status:** new\n"
            f"**Location:** {lead.location} | **Salary:** {lead.salary}\n"
            f"**URL:** {lead.url}\n"
        )
        return f"---\n{inner}\n---\n\n{body}"


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


def _set_fm(inner: str, key: str, literal: str) -> str:
    """Replace `key:`'s line in a frontmatter block, or append it if absent.
    `literal` is written verbatim, so the caller controls quoting."""
    pat = rf"(?m)^\s*{re.escape(key)}\s*:.*$"
    if re.search(pat, inner):
        return re.sub(pat, f"{key}: {literal}", inner, count=1)
    return f"{inner}\n{key}: {literal}" if inner else f"{key}: {literal}"


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


def _clamp_bytes(s: str, limit: int) -> str:
    """Largest UTF-8 prefix of `s` within `limit` bytes, never splitting a codepoint.
    A non-positive budget holds nothing -> "" (a NEGATIVE slice would instead keep all
    but the last few bytes, silently defeating the cap). Slicing the encoded bytes can
    cut mid-sequence; decode(errors="ignore") then drops the incomplete trailing bytes,
    which IS the 'never split a codepoint' guarantee."""
    if limit <= 0:
        return ""
    return s.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
