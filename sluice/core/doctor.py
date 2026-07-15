"""sluice doctor: prove the configured backends are actually usable.

Pure, zero-I/O core. The impure half -- resolving creds, building a provider,
running a one-token round-trip -- lives in `Sluice.doctor` (core/app.py); the
formatting and exit-code plumbing live in `cli.py`. This module owns only the
rules: what backends are configured (enumeration), and given a set of resolved
facts about one of them, is it ok / degraded / dead (classification).

The classification is ROLE-AWARE, and that is the whole point. The default
install ships a keyless `deepseek` fallback, which `_make_fallback` already
treats as a sanctioned degrade to primary-only -- so a keyless *fallback* is
`degraded` (exit 0), while a keyless *primary* (a run cannot happen) is `dead`.
A backend whose credentials ARE present but whose round-trip fails is `dead`
regardless of role: that is the silently-non-functional fallback this tool
exists to catch -- the one you believe in and never test until the primary
dies.
"""
from dataclasses import dataclass, field

# The three states, as bare strings so callers (cli formatter, exit_code) and
# tests share one vocabulary without importing an enum.
OK = "ok"
DEGRADED = "degraded"
DEAD = "dead"

# The round-trip prompt. Tiny on purpose -- a per-token backend costs a token or
# two to answer it, and the answer is discarded (only "did complete() raise?"
# matters). Deliberately NOT paired with a tight max_tokens cap: the
# OpenAI-compatible backend treats finish_reason=length as a hard error, so
# capping the completion would manufacture a false `dead`.
PROBE_PROMPT = "Reply with the single word: ok"


@dataclass(frozen=True)
class RoleUse:
    """One (sub-app, role) pair that references a backend target. A single
    target can be referenced by several -- e.g. the shared deepseek fallback is
    used by triage, cv, and track."""
    subapp: str
    role: str  # "primary" | "fallback"


@dataclass
class BackendTarget:
    """One distinct configured backend, after deduping identical
    (provider, model, host) across sub-apps and roles. `claude_path` is only
    meaningful for the claude-max CLI; `host` is "" for a local backend."""
    provider: str
    model: str
    host: str
    claude_path: str
    uses: list = field(default_factory=list)  # list[RoleUse]

    @property
    def is_primary(self) -> bool:
        """True if ANY use is a primary. A backend that serves as a primary
        anywhere must satisfy the strict primary rule (a keyless primary is
        dead), even if it is also used as a fallback elsewhere."""
        return any(u.role == "primary" for u in self.uses)


@dataclass
class BackendCheck:
    target: BackendTarget
    state: str
    detail: str
    elapsed: float | None = None  # round-trip seconds, when one was run


@dataclass
class DoctorReport:
    checks: list  # list[BackendCheck]

    def exit_code(self, *, strict: bool = False) -> int:
        """Non-zero iff a run-blocking backend is dead. `--strict` additionally
        fails on any degraded backend (the cron mode that enforces a believed-in
        fallback)."""
        if any(c.state == DEAD for c in self.checks):
            return 1
        if strict and any(c.state == DEGRADED for c in self.checks):
            return 1
        return 0


def enumerate_targets(triage_cfg, cv_cfg, track_cfg) -> list:
    """Every sub-app × role backend, deduped by (provider, model, host).

    Apply is absent: it is offline by contract and has no backend. The fallback
    leg carries host="" and claude_path="claude" because that is exactly how
    `_make_fallback` builds it -- it does NOT forward the primary's host/path --
    so doctor probes what a real run would actually build.

    Effort is deliberately NOT part of the dedup key: it changes cost/quality,
    not whether the backend works, so triage(medium)+cv(max) fold into one
    claude-max probe. A per-sub-app MODEL override does split, preserving the
    per-sub-app "is this a live model id" check. `claude_path` IS in the key so
    two claude-max backends pointing at different binaries never collapse.
    """
    specs = [
        # (subapp, role, provider, model, host, claude_path)
        ("triage", "primary", triage_cfg.primary_backend, triage_cfg.claude_max_model,
         triage_cfg.claude_max_host, triage_cfg.claude_max_path),
        ("triage", "fallback", triage_cfg.fallback_backend, triage_cfg.cheap_model, "", "claude"),
        ("cv", "primary", cv_cfg.primary_backend, cv_cfg.compose_model,
         cv_cfg.compose_host, cv_cfg.compose_claude_path),
        ("cv", "fallback", cv_cfg.fallback_backend, cv_cfg.cheap_model, "", "claude"),
        ("track", "primary", track_cfg.primary_backend, track_cfg.claude_max_model,
         track_cfg.claude_max_host, track_cfg.claude_max_path),
        ("track", "fallback", track_cfg.fallback_backend, track_cfg.cheap_model, "", "claude"),
    ]
    by_key: dict = {}  # (provider, model, host, claude_path) -> BackendTarget, insertion-ordered
    for subapp, role, provider, model, host, claude_path in specs:
        # claude_path is IN the key (rev-001): two claude-max backends that share
        # provider/model/host but shell different `claude` binaries are genuinely
        # different checks -- collapsing them would probe only the first path and
        # report a false `ok` for the second. For a per-token backend claude_path
        # is the harmless "claude" default and does not over-split.
        key = (provider, model, host, claude_path)
        target = by_key.get(key)
        if target is None:
            target = BackendTarget(provider=provider, model=model, host=host,
                                   claude_path=claude_path)
            by_key[key] = target
        target.uses.append(RoleUse(subapp, role))
    return list(by_key.values())


def classify(target, *, known, needs_key, key_present, key_var, cli_present,
             offline, probe_error) -> BackendCheck:
    """The rules table, as a pure function of already-resolved facts.

    - known:       provider name is in the backend registry (else a config typo)
    - needs_key:   this provider authenticates with an API key (claude-max: no)
    - key_present: that key was resolved in THIS process
    - key_var:     the env var name, for the detail message
    - cli_present: for a local (no-host) claude-max checked offline, whether the
                   `claude` binary is on PATH; None when not applicable
    - offline:     config-only mode (no round-trip was attempted)
    - probe_error: the BackendError message if a live round-trip ran and failed,
                   else None
    """
    if not known:
        return BackendCheck(target, DEAD, f"unknown backend '{target.provider}'")
    if needs_key and not key_present:
        if target.is_primary:
            return BackendCheck(target, DEAD, f"{key_var} unset")
        return BackendCheck(target, DEGRADED, f"{key_var} unset — primary-only")
    if offline:
        if cli_present is False:
            return BackendCheck(
                target, DEAD, f"CLI '{target.claude_path}' not on PATH")
        return BackendCheck(target, OK, "(offline: not round-tripped)")
    if probe_error is not None:
        return BackendCheck(target, DEAD, probe_error)
    return BackendCheck(target, OK, "round-trip ok")


def format_roles(uses: list) -> str:
    """Group a target's uses by role for display, primaries first:
    "primary · triage, cv, track; fallback · cv"."""
    by_role: dict = {}
    for u in uses:
        by_role.setdefault(u.role, []).append(u.subapp)
    parts = []
    for role in ("primary", "fallback"):
        subs = by_role.get(role)
        if subs:
            parts.append(f"{role} · {', '.join(subs)}")
    return "; ".join(parts)
