"""Rejected-Leads audit, restored in two forms: an append-only JSONL (durable,
diff-able, covering both classify-rejects and LLM dismissals) and a rendered
Obsidian note grouped by reason for eyeballing. The note is a generated view that
triage owns and overwrites, never confused with a real lead note."""
import json
import os
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
    # not on the Store contract, so this is now a store write.
    return vault.write_document(out_relpath, "\n".join(lines))
