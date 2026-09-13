"""Rejected-Leads audit, restored in two forms: an append-only JSONL (durable,
diff-able, covering both classify-rejects and LLM dismissals) and a rendered
Obsidian note grouped by reason for eyeballing. The note is a generated view that
triage owns and overwrites, never confused with a real lead note."""
import json
import os
import stat
from datetime import date, datetime


def _is_reject(entry: dict) -> bool:
    return entry.get("decision") == "reject" or entry.get("verdict") == "dismiss"


class AuditLog:
    def __init__(self, path: str):
        self.path = path

    def append(self, entry: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def cannot_append(self) -> str:
        """Why an `append` here would fail or is refused, or "" when neither. Creates nothing.

        `triage/engine.py::run` asks before any lead write. A real run writes a lead and
        THEN its audit line, so a log that cannot be appended to raised from `append` one line
        after the lead's write had landed -- measured on a state directory sluice could read
        but not write: the lead was dismissed, the run crashed, and the #223 notice was never
        printed.

        Nothing is created, deliberately. `core/paths.py`'s `_LEGACY` warns about a
        left-behind `./triage-audit.jsonl` only while this path does NOT exist, and a run that
        rejects nothing leaves no file behind. So this reads permissions on the file, or on
        the nearest ancestor that exists, and creates nothing.

        `os.access` answers for the real user, root included, and can still disagree with
        `open`. It does not see every reason a write fails -- a full disk, a quota, an I/O
        error, or a name the filesystem refuses or rewrites (too long, bytes it will not store,
        or the trailing dots and spaces Windows drops) among them -- and those surface as the
        error from `append` they always did. It can also refuse what `open` allows: measured
        on macOS, a directory whose mode denies writing but whose ACL grants adding a file is
        reported "not writable", and the run stops.
        A `..` after a directory that does not exist yet is refused, wherever it would land.

        A special file at the log path is answered from its permissions, except a socket,
        which `open` never accepts. Measured: `/dev/null` appends, and a FIFO appends once
        something reads it and blocks until then.
        """
        path = self.path
        # Paths no file can have. The walk below would answer for a directory instead, and could
        # say yes.
        if not path:
            return "the audit log path is empty"
        if chr(0) in path:
            return "the audit log path contains a NUL byte"
        try:
            os.fsencode(path)
        except UnicodeEncodeError:
            return "the audit log path cannot be encoded as a file name"
        if os.path.basename(path) in ("", os.curdir, os.pardir):
            return f"{path} names a directory, not a file"
        # A dangling link: `open(path, "a")` follows it and creates the target, so the question
        # is whether the TARGET can be created. Measured: `os.access` on the link reads the
        # missing target and answered "not writable" for a link into a writable directory,
        # which would have stopped every run. Bounded, so a loop of links ends here and is
        # reported like any other unwritable path.
        for _ in range(40):
            if not (os.path.islink(path) and not os.path.exists(path)):
                break
            path = os.path.join(os.path.dirname(path), os.readlink(path))
        if path != self.path and not os.path.lexists(path):
            # ...and ONLY the target: `append`'s `makedirs` runs on the link's own directory,
            # which exists, so a target whose directory is missing is never created.
            parent = os.path.dirname(path) or "."
            if not os.path.lexists(parent):
                return f"{parent} does not exist"
            if not os.path.isdir(parent):
                return f"{parent} is not a directory"
            return "" if os.access(parent, os.W_OK | os.X_OK) else f"{parent} is not writable"
        if os.path.lexists(path):
            if os.path.isdir(path):
                return f"{path} is a directory"
            # `exists` first: it is False for a loop of links, where `stat` would raise.
            if os.path.exists(path) and stat.S_ISSOCK(os.stat(path).st_mode):
                return f"{path} is a socket"
            return "" if os.access(path, os.W_OK) else f"{path} is not writable"
        cur = os.path.dirname(path) or "."
        stripped = []
        while not os.path.lexists(cur):
            parent = os.path.dirname(cur) or "."
            if parent == cur:
                return f"no existing directory above {path}"
            stripped.insert(0, os.path.basename(cur))
            cur = parent
        if not os.path.isdir(cur):
            return f"{cur} is not a directory"
        if not os.access(cur, os.W_OK | os.X_OK):
            return f"{cur} is not writable"
        if os.pardir in stripped:
            # `dirname` strips a `..` as text, so the walk above stepped down past it, while
            # `open` resolves it only after `makedirs` has created the directories before it.
            # Where it then lands is not predicted: the path is refused.
            return f"{path} has a `..` after a directory that does not exist yet"
        return ""

    def read_recent(self, days: int, clock=date.today) -> list:
        """Entries within the last `days`. Malformed lines are skipped; an entry
        with no parseable `ts` is included (best-effort, never lost silently)."""
        if not os.path.exists(self.path):
            return []
        cutoff = clock()
        out = []
        for line in open(self.path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                d = datetime.fromisoformat(entry.get("ts", "")).date()
                if (cutoff - d).days > days:
                    continue
            except (ValueError, TypeError):
                pass  # undated -> include
            out.append(entry)
        return out


def render_rejected_note(vault, entries: list, out_relpath: str) -> str:
    rejects = [e for e in entries if _is_reject(e)]
    by_reason: dict = {}
    for e in rejects:
        by_reason.setdefault(e.get("reason", "unspecified"), []).append(e)

    lines = ["---", 'base: "[[Job Leads.base]]"', "generated: true", "---", "",
             "# Rejected Leads (triage audit)", "",
             f"{len(rejects)} rejected leads. Regenerated each triage run.", ""]
    for reason in sorted(by_reason):
        lines.append(f"## {reason} ({len(by_reason[reason])})")
        for e in by_reason[reason]:
            lines.append(
                f"- **{e.get('company','?')}** {e.get('role','')} "
                f"(score {e.get('score','?')}) {e.get('url','')} [{e.get('ts','')}]"
            )
        lines.append("")

    # Was os.path.join(vault.dir, ...): a filesystem join THROUGH the store. `.dir` is
    # not a REQUIRED member of the Store contract, so this is now a store write.
    return vault.write_document(out_relpath, "\n".join(lines))
