"""sluice/mcpserver.py -- a Model Context Protocol server: a second front-end over
`Sluice`, exposing four read-only tools (list_leads, get_lead, doctor, health) to an
MCP client (e.g. Claude Code) over stdio (#105).

The `mcp` package is imported in exactly ONE place: inside `build_server()`'s own
function body. See docs/superpowers/plans/2026-08-12-mcp-server.md's Global Constraints
for why `build_server()` (not `serve()`) is that place, and this module's own
`tests/test_mcpserver.py` / `tests/functional/test_mcp_contract.py` for how that is
enforced and proven.
"""
import dataclasses

from sluice.core.app import Sluice
from sluice.core.leads import slug_matches
from sluice.core.status import CANONICAL


class McpNotInstalled(RuntimeError):
    """Raised by `build_server()` when the `mcp` package's import fails.
    `cmd_mcp_serve` (cli.py) catches this specifically and turns it into a usage
    error naming the extra to install -- never a bare `except ImportError`, which
    could misattribute an unrelated import failure deep inside a later tool call."""


def list_leads(sluice: Sluice, statuses: list | None = None, limit: int | None = None) -> dict:
    """Every lead matching `statuses` (or every lead, unfiltered), as a curated
    per-lead summary -- never the full frontmatter or body, so a large backlog
    cannot flood one response. Raises ValueError, naming the valid set, on an
    unrecognized status -- never silently returns [] for a typo."""
    if statuses:
        unknown = sorted(set(statuses) - CANONICAL)
        if unknown:
            raise ValueError(
                f"unknown status {unknown[0]!r} (expected one of {sorted(CANONICAL)})")
    notes = sluice.store().read_leads(set(statuses) if statuses else None)
    truncated = limit is not None and len(notes) > limit
    if limit is not None:
        notes = notes[:limit]
    leads = [{
        "slug": n.slug, "status": n.status,
        "company": n.fm.get("company", ""), "role": n.fm.get("role", ""),
        "url": n.fm.get("url", ""),
        "first_seen": n.fm.get("first_seen", ""), "last_seen": n.fm.get("last_seen", ""),
        "tailored_cv": bool(n.fm.get("tailored_cv")),
        "needs_signoff": bool(n.fm.get("needs_signoff")),
        "pending_cv": bool(n.fm.get("pending_cv")),
    } for n in notes]
    return {"leads": leads, "count": len(leads), "truncated": truncated}


def get_lead(sluice: Sluice, lead: str) -> dict:
    """Resolve `lead` by substring match over every lead (not status-scoped), the
    same rule `cv`/`apply` use for `--lead`. Never guesses an identity: zero matches
    -> not_found, two-or-more -> ambiguous (candidates named, nothing picked),
    exactly one -> the full frontmatter + body (the single-lead detail view)."""
    notes = [n for n in sluice.store().read_leads() if slug_matches(n, lead)]
    if not notes:
        return {"outcome": "not_found"}
    if len(notes) > 1:
        return {"outcome": "ambiguous", "candidates": sorted(n.slug for n in notes)}
    n = notes[0]
    return {"outcome": "found", "slug": n.slug, "status": n.status, "fm": n.fm, "body": n.body}


def doctor(sluice: Sluice, offline: bool = True) -> dict:
    """Preflight backends, renderer, store artefacts and gate posture. Offline by
    default: an agent calling this tool casually must not trigger unbudgeted live
    spend. `exit_code` is `DoctorReport.exit_code(strict=False)`, the CLI's own
    default -- the full report is already in the response, so an agent can apply
    its own strictness policy over the raw checks."""
    report = sluice.doctor(offline=offline)
    out = dataclasses.asdict(report)
    out["exit_code"] = report.exit_code(strict=False)
    return out


def health(sluice: Sluice) -> dict:
    """Per-source scrape baseline + retire state, sorted by source id."""
    return {"sources": [dataclasses.asdict(s) for s in sluice.health_report()]}
