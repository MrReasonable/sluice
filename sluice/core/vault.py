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
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import date

from sluice.core import status as _status
from sluice.core.leads import SAME, UNKNOWN, Lead, _norm_url, same_opportunity
from sluice.core.log import get_logger
from sluice.core.protocols import (
    CRITERIA_RELPATH,
    LeadNote,
    MalformedNoteField,
    VaultConflict,
)

_LEADS_SUBDIR = os.path.join("Job Applications", "Job Leads")
_EXP_SUBDIR = os.path.join("Job Applications", "Experience Library")
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
        ALL name candidates (#5's clean `Company - Title`, its `- Location` variant, and the
        `- <title-digest>` variant) go through this one helper, so their truncation can never
        drift -- a suffixed candidate that sanitized or capped differently would mis-key and
        reintroduce a duplicate.

        `.replace` is a length-preserving per-char map, so `stem.replace()[:120]` equals the
        old `[:120].replace()` char for char: candidate 1 stays byte-identical to the old
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

    def _path_for(self, lead: Lead) -> str:
        """The clean `Company - Title` note path (candidate 1 of #5's walk). Unchanged in
        output from before #5, so an existing note is UPDATED in place, never duplicated."""
        name = self._note_name(f"{lead.company} - {lead.title}")
        return os.path.join(self.leads_dir, f"{name}.md")

    def _reconcile(self, fm: dict, lead: Lead, capped: bool) -> str:
        """The ONE verdict, shared by the active walk and #81's archive probe: "update",
        "merge" or "advance". A second copy kept in sync by a comment is the #30 failure
        mode -- a check that must match another check, with prose standing in for the
        guarantee -- so both callers go through here.

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
            return "advance"
        if verdict == SAME:
            return "update"
        if verdict == UNKNOWN:
            return "merge"
        return "advance"

    def _resolve_path(self, lead: Lead) -> tuple[str | None, str]:
        """Walk the nameable candidates and return (path, action), action one of
        "create"/"update"/"merge"/"refuse". Candidate 1 is the clean `Company - Title` name
        (always); a location suffix (only when location is non-empty) and -- when the title
        is CAPPED -- a title-digest suffix add further candidates. Every verdict terminates in
        place EXCEPT DIFFERENT, which advances -- so a note is split only on PROVEN difference,
        never on the absence of evidence. Running out of candidates (every one a note proven
        different) is REFUSE: no path can be written without clobbering a different job, so
        path is None. See #5.

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
        stem = f"{lead.company} - {lead.title}"
        capped = len(_sanitize(stem)) > _CHAR_CAP
        names = [self._note_name(stem)]
        if lead.location:
            names.append(self._note_name(stem, lead.location))
        if capped:
            names.append(self._note_name(stem, _title_digest(lead.title)))
        for name in names:
            path = os.path.join(self.leads_dir, f"{name}.md")
            if not os.path.exists(path):
                return path, "create"
            inner, _ = _split_frontmatter(_read(path))
            action = self._reconcile(_fm_dict(inner), lead, capped)
            if action != "advance":
                return path, action
            # DIFFERENT location, or a capped-title mismatch -> advance to the next candidate
        return None, "refuse"

    def ensure_stfolder(self) -> None:
        """Syncthing silently refuses to sync a vault root missing its .stfolder
        marker (state=idle, never drains). Recreate it defensively."""
        os.makedirs(os.path.join(self.dir, ".stfolder"), exist_ok=True)

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
                      note_tag: str | None = None,
                      require_status: frozenset | None = None) -> bool:
        """Surgically set frontmatter keys (literal YAML scalars), body byte-for-byte
        intact. Optionally append a guarded note to relevance_notes (skipped if note_tag
        is present, so re-runs are idempotent). Routed through _cas_write: the edit is
        re-derived from the CURRENT note on each attempt, so a concurrent writer's other
        keys and body survive. May raise VaultConflict on sustained conflict (#16).

        `require_status` (#9): when given, re-read the status from the FRESH note and
        write nothing unless it is in that set. Returns whether a write happened."""
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
            for key, literal in fields.items():
                inner = _set_fm(inner, key, literal)
            if append_note and note_tag:
                current = _fm_value(inner, "relevance_notes")
                if note_tag not in current:
                    merged = (current + " " + append_note).strip()
                    inner = _set_fm(inner, "relevance_notes", f'"{merged}"')
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

    def sign_off(self, ref, *, accept: bool = True) -> str:
        """Resolve a #60 needs-signoff hold and report the OUTCOME derived from FRESH
        content: 'promoted' | 'discarded' | 'collision' | 'nothing' (the way upsert
        returns a verdict, so the caller never reconstructs it from a stale snapshot).
        With pending_cv present: clear pending_cv + needs_signoff, then -- accept=False
        -> 'discarded'; accept and tailored_cv ABSENT -> set tailored_cv = pending_cv,
        'promoted'; accept but tailored_cv already PRESENT -> leave it (a real CV
        appeared since -- a direct set_tailored_cv), 'collision'. No pending_cv ->
        unchanged, 'nothing'. The tailored_cv check lives inside the transform (atomic
        under CAS, mirroring set_tailored_cv(only_if_absent=...)), so the pointer is
        never clobbered. The returned string is DISTINCT from _cas_write's
        write-happened bool: the collision case WRITES (clears markers) yet is not
        'promoted'. May raise VaultConflict (#16)."""
        outcome = ["nothing"]  # reset per transform run so a CAS retry reports the final branch
        def transform(text: str) -> str:
            outcome[0] = "nothing"
            inner, body = _split_frontmatter(text)
            if inner is None:
                return text
            pending = _fm_value(inner, "pending_cv")
            if not pending:
                return text  # nothing to resolve -> _cas_write no-op
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
        if not os.path.isdir(self.leads_dir):
            return summary
        for name in sorted(os.listdir(self.leads_dir)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(self.leads_dir, name)
            try:
                inner, _ = _split_frontmatter(_read(path))
            except OSError:
                continue
            if inner is None:
                summary["unchanged"] += 1
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
                return self._bump_last_seen_or_refuse(
                    path, lead.last_seen or _today(), "updated", lead.dedup_key)
            if action == "merge":
                # We could not prove same-or-different, so we do NOT split (that would mint a
                # note per scrape -- unbounded). Bump last_seen like an update; the difference
                # is only that the count is reported separately so the merge is visible.
                return self._bump_last_seen_or_refuse(
                    path, lead.last_seen or _today(), "merged", lead.dedup_key)
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
        See docs/.../read-path-dedup-design.md #3."""
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
        merged_dir = os.path.join(self.leads_dir, "_merged")
        os.makedirs(merged_dir, exist_ok=True)
        archived = []
        for ref in loser_refs:
            base = os.path.basename(ref)
            stem = base[:-3] if base.endswith(".md") else base
            dest = os.path.join(merged_dir, base)
            n = 1
            reserved = None
            try:
                # Reserve the destination atomically (O_EXCL fails if taken, so a concurrent
                # archive never collides), then os.replace the loser into our reservation.
                # os.replace is a single atomic move of whatever `ref` names at that instant,
                # so a concurrent atomic save of the loser is ARCHIVED (moved), never deleted,
                # and the reservation means we never overwrite another archived note. This
                # replaces the old os.link + os.unlink pair, which had a window: a concurrent
                # atomic save of the loser landing between the link and the unlink would be
                # deleted by the unlink instead of archived.
                while True:
                    try:
                        fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        os.close(fd)
                        reserved = dest
                        break
                    except FileExistsError:
                        dest = os.path.join(merged_dir, f"{stem}.{n}.md")
                        n += 1
                os.replace(ref, dest)   # atomic; overwrites only our own 0-byte reservation
                reserved = None
                archived.append(dest)
            except OSError as e:
                # per-loser isolation: leave the loser active (it self-heals next run), and
                # clean up an orphaned reservation if the move never happened.
                if reserved:
                    try:
                        os.unlink(reserved)
                    except OSError:
                        pass
                _log.warning("dedupe: could not archive loser %s: %s", ref, e)
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
    tag) would silently return False and drop the needed edit."""
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


def _set_fm(inner: str, key: str, literal: str) -> str:
    """Replace `key:`'s line in a frontmatter block, or append it if absent.
    `literal` is written verbatim, so the caller controls quoting."""
    pat = rf"(?m)^\s*{re.escape(key)}\s*:.*$"
    if re.search(pat, inner):
        return re.sub(pat, f"{key}: {literal}", inner, count=1)
    return f"{inner}\n{key}: {literal}" if inner else f"{key}: {literal}"


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


def _clamp_bytes(s: str, limit: int) -> str:
    """Largest UTF-8 prefix of `s` within `limit` bytes, never splitting a codepoint.
    A non-positive budget holds nothing -> "" (a NEGATIVE slice would instead keep all
    but the last few bytes, silently defeating the cap). Slicing the encoded bytes can
    cut mid-sequence; decode(errors="ignore") then drops the incomplete trailing bytes,
    which IS the 'never split a codepoint' guarantee."""
    if limit <= 0:
        return ""
    return s.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
