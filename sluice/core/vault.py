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
from sluice.core.leads import Lead, _norm_url
from sluice.core.protocols import LeadNote

_LEADS_SUBDIR = os.path.join("Job Applications", "Job Leads")
_EXP_SUBDIR = os.path.join("Job Applications", "Experience Library")
_MYCV_BASELINE = os.path.join("My CV", "CV.md")
_CRITERIA_RELPATH = os.path.join("Job Applications", "Judging Profile.md")
_DEFAULT_VAULT = "./vault"

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
    def __init__(self, dir: str | None = None, *, baseline_rel: str = _MYCV_BASELINE):
        self.dir = dir or os.environ.get("VAULT_DIR", _DEFAULT_VAULT)
        self.leads_dir = os.path.join(self.dir, _LEADS_SUBDIR)
        self.baseline_rel = baseline_rel

    def _slug_for(self, path: str) -> str:
        """The lead's stable identity. For a markdown vault that is the filename without
        its extension -- exactly what apply/select.py, apply/engine.py, track/classify.py
        and track/engine.py each used to recompute for themselves."""
        name = os.path.basename(path)
        return name[:-3] if name.endswith(".md") else name

    # ── paths ────────────────────────────────────────────────────────────────
    def _path_for(self, lead: Lead) -> str:
        """Match the old pipeline's naming exactly, so an existing note for
        the same company+role is UPDATED in place rather than duplicated."""
        safe = f"{lead.company} - {lead.title}"[:120].replace("/", "-").replace(":", "-")
        return os.path.join(self.leads_dir, f"{safe}.md")

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
        """Create a new note or bump last_seen on an existing one. Returns
        "created" | "updated"."""
        os.makedirs(self.leads_dir, exist_ok=True)
        # The vault ensures its own Syncthing marker, on the WRITE path. This used to be
        # a Store method cli.py called by hand, which leaked a Syncthing/Obsidian concept
        # into a contract every other store would have had to pretend to implement. It
        # belongs here and not in __init__: constructing a store must not touch the disk.
        self.ensure_stfolder()
        path = self._path_for(lead)
        if os.path.exists(path):
            self._bump_last_seen(path, lead.last_seen or _today())
            return "updated"
        _write(path, self._render_new(lead))
        return "created"

    def _bump_last_seen(self, path: str, last_seen: str) -> None:
        """Set the last_seen line inside existing frontmatter, preserving every
        other key, its value formatting, and the whole body verbatim."""
        inner, body = _split_frontmatter(_read(path))
        if inner is None:  # note without frontmatter - leave body, add a header
            inner, body = f"last_seen: {last_seen}", _read(path)
        elif re.search(r"(?m)^\s*last_seen\s*:.*$", inner):
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


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


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


def _clamp_bytes(s: str, limit: int) -> str:
    """Largest UTF-8 prefix of `s` within `limit` bytes, never splitting a codepoint.
    Slicing the encoded bytes can cut mid-sequence; decode(errors="ignore") then drops
    the incomplete trailing bytes, which IS the 'never split a codepoint' guarantee."""
    return s.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
