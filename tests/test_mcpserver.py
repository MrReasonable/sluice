"""sluice/mcpserver.py's plain tool functions, called directly against a real
`Sluice`/`Vault` -- no MCP protocol machinery, no `build_server()`/`serve()`. Fixture
notes are built through `Vault.upsert` so their slugs are REAL store-issued filenames,
matching `tests/test_leads_expire.py`'s own rationale for doing the same (a hand-written
slug format could pass here while the shipped command matches nothing).
"""
import ast
import dataclasses
import pathlib

import pytest

import sluice.mcpserver as mcpserver_mod
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import UNTRUSTED_SCRAPED_CONTENT_WARNING, Lead
from sluice.core.protocols import Store
from sluice.core.vault import Vault
from sluice.mcpserver import (
    apply_record,
    create_lead,
    cv_run,
    cv_signoff,
    dismiss_lead,
    doctor,
    get_lead,
    health,
    list_leads,
)


def _lead(company="Example Ltd", title="Example Role", url="https://example.invalid/1"):
    return Lead(source="s", search="q", title=title, company=company, url=url)


def _seed(tmp_path, *, status="shortlist", company="Example Ltd", title="Example Role",
          url="https://example.invalid/1", **extra):
    """Create one lead note through a real Vault and set its status/extra fields.
    Returns its store-issued slug."""
    v = Vault(str(tmp_path))
    v.upsert(_lead(company=company, title=title, url=url))
    note = next(n for n in v.read_leads() if n.fm.get("url", "") == url)
    v.update_fields(note.ref, {"status": status, **extra})
    return note.slug


def _app(tmp_path):
    return Sluice(Config(), store=Vault(str(tmp_path)))


# ── list_leads ───────────────────────────────────────────────────────────────

def test_list_leads_returns_a_curated_summary_never_the_body(tmp_path):
    slug = _seed(tmp_path, status="shortlist", first_seen="2026-01-01", last_seen="2026-02-01")
    out = list_leads(_app(tmp_path))
    assert out["count"] == 1
    assert out["truncated"] is False
    row = out["leads"][0]
    assert row["slug"] == slug
    assert row["status"] == "shortlist"
    assert row["company"] == "Example Ltd"
    assert row["role"] == "Example Role"
    assert row["first_seen"] == "2026-01-01"
    assert row["last_seen"] == "2026-02-01"
    assert row["tailored_cv"] is False
    assert "body" not in row and "fm" not in row


def test_list_leads_non_empty_result_carries_an_untrusted_content_warning(tmp_path):
    # company/role/url are scraped from a third-party job posting too, same threat
    # class as get_lead's fm/body (a smaller surface, but not zero) -- asserted
    # against the real shared constant, matching get_lead's own test.
    _seed(tmp_path, status="shortlist")
    out = list_leads(_app(tmp_path))
    assert out["count"] == 1
    assert out["content_warning"] == mcpserver_mod._LIST_LEADS_CONTENT_WARNING
    assert UNTRUSTED_SCRAPED_CONTENT_WARNING in out["content_warning"]


def test_list_leads_empty_result_carries_no_content_warning(tmp_path):
    # Nothing scraped, nothing to warn about.
    out = list_leads(_app(tmp_path))
    assert out["count"] == 0
    assert "content_warning" not in out


def test_list_leads_filters_by_status(tmp_path):
    # DISTINCT titles, not just distinct urls: Vault identity is company+title(+location)
    # (see `Vault._candidate_names`, `stem = f"{company} - {title}"`) -- url plays no part
    # in it, so two leads sharing a title would collide onto ONE note and this would
    # seed only one lead instead of two, passing (or failing) for the wrong reason.
    _seed(tmp_path, status="shortlist", title="Example Role", url="https://example.invalid/1")
    _seed(tmp_path, status="dismiss", title="Example Role Two", url="https://example.invalid/2")
    out = list_leads(_app(tmp_path), statuses=["shortlist"])
    assert out["count"] == 1
    assert out["leads"][0]["status"] == "shortlist"


def test_list_leads_accepts_a_non_canonical_status_alias_like_the_rest_of_the_cli(tmp_path):
    # "dismissed" is a real alias sluice/core/status.py's _ALIASES normalizes to the
    # canonical "dismiss" -- sluice/core/vault.py's read_leads already accepts it via
    # the same normalize() call, so list_leads rejecting it would be a real divergence
    # from what the rest of the CLI accepts (minor finding #4).
    _seed(tmp_path, status="dismiss", title="Example Role", url="https://example.invalid/1")
    out = list_leads(_app(tmp_path), statuses=["dismissed"])
    assert out["count"] == 1
    assert out["leads"][0]["status"] == "dismiss"


def test_list_leads_rejects_an_unknown_status_naming_the_valid_set(tmp_path):
    with pytest.raises(ValueError) as exc_info:
        list_leads(_app(tmp_path), statuses=["not-a-real-status"])
    assert "not-a-real-status" in str(exc_info.value)
    assert "shortlist" in str(exc_info.value)  # a real canonical status, proving the valid set is named


def test_list_leads_unknown_status_error_names_every_bad_status_not_just_the_first(tmp_path):
    with pytest.raises(ValueError) as exc_info:
        list_leads(_app(tmp_path), statuses=["nope-one", "nope-two"])
    # deferred-minor #6: `unknown` is the full sorted set of bad statuses -- the
    # message must name ALL of them, not just unknown[0].
    assert "nope-one" in str(exc_info.value)
    assert "nope-two" in str(exc_info.value)


def test_list_leads_limit_truncates_and_reports_truncated(tmp_path):
    # Same distinct-title reasoning as test_list_leads_filters_by_status above.
    _seed(tmp_path, status="shortlist", title="Example Role", url="https://example.invalid/1")
    _seed(tmp_path, status="shortlist", title="Example Role Two", url="https://example.invalid/2")
    out = list_leads(_app(tmp_path), limit=1)
    assert out["count"] == 1
    assert out["truncated"] is True


def test_list_leads_no_limit_returns_everything_untruncated(tmp_path):
    # Same distinct-title reasoning as test_list_leads_filters_by_status above.
    _seed(tmp_path, status="shortlist", title="Example Role", url="https://example.invalid/1")
    _seed(tmp_path, status="shortlist", title="Example Role Two", url="https://example.invalid/2")
    out = list_leads(_app(tmp_path))
    assert out["count"] == 2
    assert out["truncated"] is False


def test_list_leads_negative_limit_raises_rather_than_reporting_a_false_truncated(tmp_path):
    # minor finding #5: `limit is not None and len(notes) > limit` is True for a
    # negative limit even against an EMPTY store, which is a wrong `truncated` flag,
    # not a real truncation. Raise loudly instead, the same way an unknown status does.
    with pytest.raises(ValueError, match="-1"):
        list_leads(_app(tmp_path), limit=-1)


def test_list_leads_empty_statuses_list_falls_through_to_no_filter(tmp_path):
    # deferred-minor #7: `if statuses:` is falsy for `[]`, so an explicit empty list
    # behaves identically to `None` -- the entire backlog, not zero leads. Pinned as
    # intentional (matches None's "no filter" semantics) rather than left unrecorded.
    _seed(tmp_path, status="shortlist", title="Example Role", url="https://example.invalid/1")
    out = list_leads(_app(tmp_path), statuses=[])
    assert out["count"] == 1


def test_list_leads_surfaces_the_cv_flags(tmp_path):
    _seed(tmp_path, status="shortlist", tailored_cv="CV_deadbeef.pdf (2026-07-09)",
          needs_signoff="unsupported claim", pending_cv="CV_deadbeef.pdf (2026-07-09)")
    row = list_leads(_app(tmp_path))["leads"][0]
    assert row["tailored_cv"] is True
    assert row["needs_signoff"] is True
    assert row["pending_cv"] is True


# ── get_lead ─────────────────────────────────────────────────────────────────

def test_get_lead_not_found(tmp_path):
    assert get_lead(_app(tmp_path), "nothing here") == {"outcome": "not_found"}


def test_get_lead_found_returns_full_frontmatter_and_body(tmp_path):
    slug = _seed(tmp_path, status="shortlist")
    out = get_lead(_app(tmp_path), slug)
    assert out["outcome"] == "found"
    assert out["slug"] == slug
    assert out["status"] == "shortlist"
    assert out["fm"]["company"] == "Example Ltd"
    assert "body" in out


def test_get_lead_found_carries_an_untrusted_content_warning(tmp_path):
    # `fm`/`body` are scraped from a third-party job posting -- an MCP client's calling
    # agent must be told, structurally, not just via the tool's own docstring, that this
    # is data to read and never an instruction to follow. Asserted against the REAL
    # shared constant (`core.leads.UNTRUSTED_SCRAPED_CONTENT_WARNING`), the same one
    # `sluice/triage/resolve.py`'s prompt uses for the identical class of content handed
    # to the triage LLM judge -- not a loose substring check, so the two mitigations
    # cannot silently drift into two different phrasings of the same warning (they
    # already did once: the first version of this field dropped "whatever it says
    # about itself", the clause that specifically defeats a self-referential injection).
    slug = _seed(tmp_path, status="shortlist")
    out = get_lead(_app(tmp_path), slug)
    assert out["outcome"] == "found"
    assert out["content_warning"] == mcpserver_mod._GET_LEAD_CONTENT_WARNING
    assert UNTRUSTED_SCRAPED_CONTENT_WARNING in out["content_warning"]
    assert "whatever it says about itself" in out["content_warning"]


def test_get_lead_not_found_and_ambiguous_carry_no_content_warning(tmp_path):
    # There is no lead content in either outcome, so nothing to warn about -- the field
    # is scoped to the ONE outcome that actually returns fm/body.
    assert "content_warning" not in get_lead(_app(tmp_path), "nothing here")
    _seed(tmp_path, company="Example Northgate", title="Analyst", url="https://example.invalid/1")
    _seed(tmp_path, company="Example Northgate", title="Analyst Two",
          url="https://example.invalid/2")
    out = get_lead(_app(tmp_path), "Example Northgate")
    assert out["outcome"] == "ambiguous"
    assert "content_warning" not in out


def test_get_lead_ambiguous_names_every_candidate_and_picks_none(tmp_path):
    slug1 = _seed(tmp_path, company="Example Northgate", title="Analyst",
                  url="https://example.invalid/1")
    slug2 = _seed(tmp_path, company="Example Northgate", title="Analyst Two",
                  url="https://example.invalid/2")
    out = get_lead(_app(tmp_path), "Example Northgate")
    assert out["outcome"] == "ambiguous"
    assert sorted(out["candidates"]) == sorted([slug1, slug2])


def test_get_lead_matches_across_every_status_not_just_shortlist(tmp_path):
    slug = _seed(tmp_path, status="dismiss")
    out = get_lead(_app(tmp_path), slug)
    assert out["outcome"] == "found"
    assert out["status"] == "dismiss"


# ── doctor ───────────────────────────────────────────────────────────────────

def test_doctor_defaults_to_offline_and_matches_sluice_doctor_offline(tmp_path):
    # mcpserver.doctor exposes no `probe` seam (an MCP client cannot inject a python
    # callable), so this cannot inject a fake round-trip the way tests/test_doctor.py
    # does. Proven indirectly instead: the default call must reproduce
    # `sluice.doctor(offline=True)` exactly. If the wrapper's default were secretly
    # False, this would risk a live backend round-trip (a keyless claude-max primary
    # needs no credentials, so it IS "known and testable" in the live branch) and the
    # two reports would very likely diverge or the test would hang.
    app = Sluice(Config(), store=Vault(str(tmp_path)))
    out = doctor(app)
    expected = app.doctor(offline=True)
    assert out == {**dataclasses.asdict(expected), "exit_code": expected.exit_code(strict=False)}


# ── health ───────────────────────────────────────────────────────────────────

def test_health_wraps_health_report_as_asdict_sources(tmp_path):
    app = Sluice(Config(), store=Vault(str(tmp_path)))
    out = health(app)
    assert out == {"sources": [dataclasses.asdict(s) for s in app.health_report()]}


# ── import-guard tests (item 3 of the design's Testing section) ──────────────
#
# These prove DIFFERENT things about the same overall property ("mcp leaks nowhere
# outside build_server()"), despite sounding redundant. An executed reproduction
# during plan review proved the runtime guard below CANNOT detect a stray top-level
# `mcp` import once an earlier test in THIS FILE has already imported
# `sluice.mcpserver` unguarded (which every test above does) -- by the time the
# runtime guard's patch is installed, the stray import already ran and succeeded,
# invisibly. The AST sweep is the SOLE test that can catch that. See
# docs/superpowers/specs/2026-08-12-mcp-server-design.md's Testing section, item 3,
# for the full argument.

def test_mcp_imported_nowhere_outside_build_server():
    """The AST sweep, REPO-WIDE: every mcp-named Import/ImportFrom node in EVERY
    .py file under sluice/ must be a DESCENDANT of build_server()'s own FunctionDef
    -- not merely "build_server's body contains at least one such node" (a stray
    top-level `import mcp` sitting ALONGSIDE a correctly-guarded one must still fail
    this) -- and only sluice/mcpserver.py itself gets that exemption at all; every
    other file must carry ZERO mcp-named imports anywhere.

    Widened per important finding #2 of the final whole-branch review: the actual
    rule (.rulesync/rules/CLAUDE.md) is repo-wide -- "nothing outside `job-sluice
    mcp serve` may cause it to load; a bare install never imports it" -- not "only
    cli.py and mcpserver.py". A future `from mcp import ...` dropped into some other
    sluice/ module would have passed the old two-file sweep silently; this one
    still catches it. Node-classification logic (Import/ImportFrom detection,
    mcp-name matching, build_server-subtree allowlisting) is unchanged from the
    original two-file version -- only WHICH FILES get swept changed."""
    import ast
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sluice_dir = repo_root / "sluice"
    mcpserver_path = sluice_dir / "mcpserver.py"

    def _bad_mcp_imports(tree, allowed_func_name=None):
        allowed_ids = set()
        if allowed_func_name is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == allowed_func_name:
                    allowed_ids = {id(n) for n in ast.walk(node)}
                    break
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == "mcp" or name.startswith("mcp."):
                    if id(node) not in allowed_ids:
                        bad.append(name)
        return bad

    checked = 0
    for path in sorted(sluice_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        checked += 1
        if path == mcpserver_path:
            bad = _bad_mcp_imports(tree, allowed_func_name="build_server")
            assert bad == [], (
                "sluice/mcpserver.py must import mcp only inside build_server()'s "
                "own function body (directly, or via a helper nested inside it)")
        else:
            bad = _bad_mcp_imports(tree)
            assert bad == [], f"{path} must carry no mcp import at all: {bad!r}"
    # SCOPE, not just non-empty -- a broken/empty glob would pass this test vacuously.
    # sluice/ carries 100+ .py files as of #105; 50 is a conservative floor, matching
    # tests/test_docs_claims.py's own sluice/-wide sweep's floor.
    assert checked >= 50, f"the sweep read only {checked} files under sluice/"


def test_cmd_mcp_serve_degrades_to_rc2_when_mcp_is_absent(monkeypatch, capsys):
    """The runtime guard: `mcp` genuinely absent (not merely `sys.modules["mcp"] =
    None`, which a reproduction during plan review proved silently no-ops once `mcp`
    is a genuine test-extra dependency cached elsewhere in the same session) must
    degrade `cmd_mcp_serve` to an rc-2 usage error, never an uncaught traceback."""
    import builtins

    real_import = builtins.__import__

    def _raise_for_mcp(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError(f"simulated: {name} not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _raise_for_mcp)

    from sluice.cli import _build_parser, cmd_mcp_serve
    from sluice.core.config import Config

    args = _build_parser().parse_args(["mcp", "serve"])
    assert cmd_mcp_serve(args, Config()) == 2
    assert "job-sluice[mcp]" in capsys.readouterr().err


# ── serve() coverage (important finding #1) ───────────────────────────────────
#
# Neither of the two tests above exercises `serve()` itself: `build_server()` is
# covered directly (import-guard tests above) and via the SDK's in-memory Client
# (tests/functional/test_mcp_contract.py), and cmd_mcp_serve's FAILURE path (mcp
# absent) is covered above -- but nothing asserted `serve()` actually calls
# `.run("stdio")`, nor that cmd_mcp_serve's SUCCESS path returns 0. A typo'd
# transport literal (e.g. "studio") would raise `ValueError: Unknown transport:
# studio`, which sluice/cli.py's broad `except ValueError` around main()'s whole
# dispatch converts into an ordinary-looking `job-sluice: Unknown transport:
# studio` / rc 2 -- indistinguishable from a malformed-config usage error, on the
# one command whose entire job is starting a server.

def test_serve_builds_the_server_and_runs_it_over_stdio(monkeypatch):
    """`serve()`'s only two responsibilities: build once via `build_server()`, then
    run the result over the LITERAL string "stdio" -- not some other transport a
    typo could introduce. `build_server` is monkeypatched to a recorder so this
    never touches the real `mcp` package or blocks on a real `.run()`."""
    calls = []

    class _FakeMcpServer:
        def run(self, transport):
            calls.append(transport)

    monkeypatch.setattr(mcpserver_mod, "build_server", lambda config, write=False: _FakeMcpServer())
    mcpserver_mod.serve(object())
    assert calls == ["stdio"]


def test_cmd_mcp_serve_returns_0_on_the_success_path(monkeypatch):
    """Complements test_cmd_mcp_serve_degrades_to_rc2_when_mcp_is_absent above,
    which only covers the McpNotInstalled failure path. `mcpserver.serve` itself is
    monkeypatched (rather than `build_server`) because `cmd_mcp_serve` calls
    `mcpserver.serve(config)` directly -- the point here is proving cmd_mcp_serve's
    own success-path return value, not re-proving serve()'s internals (covered by
    the test directly above)."""
    from sluice.cli import _build_parser, cmd_mcp_serve
    from sluice.core.config import Config

    monkeypatch.setattr(mcpserver_mod, "serve", lambda config, write=False: None)
    args = _build_parser().parse_args(["mcp", "serve"])
    assert cmd_mcp_serve(args, Config()) == 0


def test_build_parser_mcp_serve_write_flag_sets_args_write_true():
    """`--write` must parse to `args.write is True` -- the sole channel `cmd_mcp_serve`
    reads to decide whether to thread `write=True` into `mcpserver.serve`. Bare `mcp
    serve` (no flag) defaults to False, already covered implicitly by every other test
    in this file that calls `_build_parser().parse_args(["mcp", "serve"])`."""
    from sluice.cli import _build_parser

    args = _build_parser().parse_args(["mcp", "serve", "--write"])
    assert args.write is True


def test_cmd_mcp_serve_threads_write_true_to_serve(monkeypatch):
    """Closes a gap neither test_serve_builds_the_server_and_runs_it_over_stdio nor
    test_cmd_mcp_serve_returns_0_on_the_success_path covers: both only ever exercise
    write=False (the lambdas' own default). A regression that silently dropped
    `write=args.write` from cmd_mcp_serve's call to `mcpserver.serve` -- reverting to
    a bare `mcpserver.serve(config)` -- would still pass every other test in this
    file, since `serve`'s own `write` parameter defaults to False too. This spy
    asserts the actual kwarg VALUE threaded through end to end (parsed args ->
    cmd_mcp_serve -> serve), not just that the call succeeds."""
    from sluice.cli import _build_parser, cmd_mcp_serve
    from sluice.core.config import Config

    captured = {}

    def _spy(config, write=False):
        captured["write"] = write

    monkeypatch.setattr(mcpserver_mod, "serve", _spy)
    args = _build_parser().parse_args(["mcp", "serve", "--write"])
    assert cmd_mcp_serve(args, Config()) == 0
    assert captured["write"] is True


# ── dismiss_lead ─────────────────────────────────────────────────────────────

def test_dismiss_lead_tool_not_found(tmp_path):
    out = dismiss_lead(_app(tmp_path), "nothing here", "no fit")
    assert out == {"outcome": "not_found"}


def test_dismiss_lead_tool_out_of_scope_for_an_applied_lead(tmp_path):
    slug = _seed(tmp_path, status="applied")
    out = dismiss_lead(_app(tmp_path), slug, "no fit")
    assert out["outcome"] == "out_of_scope"
    assert out["slug"] == slug
    assert out["status"] == "applied"


def test_dismiss_lead_tool_ambiguous_names_slug_candidates(tmp_path):
    """A genuine slug COLLISION -- two notes at the SAME basename in different
    subfolders -- reachable only via the recursive scan (#1); a flat directory
    cannot hold two files at one name, and upsert's own identity logic (company+
    title) cannot construct this on its own. Fixture technique verified directly
    against tests/test_apply_select.py's own `_vault_subfolders` helper (its
    `test_select_all_still_sends_an_unambiguous_lead`), the established pattern
    for this exact class of fixture elsewhere in the suite."""
    import pathlib
    leads = pathlib.Path(tmp_path) / "Job Applications" / "Job Leads"
    fm = ('company: "Example Northgate"\nrole: "Analyst"\nstatus: shortlist\n'
         'url: "https://example.invalid/1"')
    for sub in ("Active", "Archive"):
        d = leads / sub
        d.mkdir(parents=True)
        (d / "Example Northgate - Analyst.md").write_text("---\n" + fm + "\n---\n\nBODY\n")
    slug = "Example Northgate - Analyst"
    out = dismiss_lead(_app(tmp_path), slug, "no fit")
    assert out["outcome"] == "ambiguous"
    assert out["candidates"] == [slug, slug]


def test_dismiss_lead_tool_dismissed_carries_note_appended(tmp_path):
    slug = _seed(tmp_path, status="shortlist")
    out = dismiss_lead(_app(tmp_path), slug, "no fit")
    assert out == {"outcome": "dismissed", "slug": slug, "status": "dismiss",
                   "note_appended": True}


def test_dismiss_lead_tool_refused_signoff_hold_names_the_remedy(tmp_path):
    slug = _seed(tmp_path, status="shortlist", pending_cv='"CV_deadbeef.pdf (2026-08-14)"')
    out = dismiss_lead(_app(tmp_path), slug, "no fit")
    assert out["outcome"] == "refused_signoff_hold"
    assert "cv_signoff" in out["detail"] and "discard=true" in out["detail"]


def test_dismiss_lead_tool_conflict_on_a_sustained_vault_conflict(tmp_path, monkeypatch):
    """Round-5 review finding (PR #132): dismiss_lead's `conflict` outcome had zero
    coverage at the MCP tool layer -- mirrors tests/test_leads_dismiss.py's app-layer
    test_dismiss_lead_returns_conflict_on_a_sustained_vault_conflict through this
    tool's own passthrough shape."""
    from sluice.core.protocols import VaultConflict

    slug = _seed(tmp_path, status="shortlist")

    def boom(*a, **k):
        raise VaultConflict("x")
    monkeypatch.setattr(Vault, "update_fields", boom)

    out = dismiss_lead(_app(tmp_path), slug, "no fit")
    assert out == {"outcome": "conflict", "slug": slug}


# ── apply_record ─────────────────────────────────────────────────────────────

def test_apply_record_tool_not_found(tmp_path):
    out = apply_record(_app(tmp_path), "nothing here")
    assert out == {"outcome": "not_found"}


def test_apply_record_tool_out_of_scope_for_a_new_lead(tmp_path):
    slug = _seed(tmp_path, status="new")
    out = apply_record(_app(tmp_path), slug)
    assert out["outcome"] == "out_of_scope"
    assert out["status"] == "new"


def test_apply_record_tool_ambiguous_names_slug_candidates(tmp_path):
    """Round-2 review finding: this branch (a hand-inlined, must-stay-in-sync-by-
    hand copy of apply.select.resolve, per its own comment) had zero coverage --
    deleting it left the suite green while the fallthrough leaked two absolute
    local file paths into `outcome` instead of the {"outcome", "candidates"} shape
    every sibling tool uses. Same genuine slug-COLLISION fixture technique as
    test_dismiss_lead_tool_ambiguous_names_slug_candidates above."""
    import pathlib
    leads = pathlib.Path(tmp_path) / "Job Applications" / "Job Leads"
    fm = ('company: "Example Northgate"\nrole: "Analyst"\nstatus: shortlist\n'
         'url: "https://example.invalid/1"')
    for sub in ("Active", "Archive"):
        d = leads / sub
        d.mkdir(parents=True)
        (d / "Example Northgate - Analyst.md").write_text("---\n" + fm + "\n---\n\nBODY\n")
    slug = "Example Northgate - Analyst"
    out = apply_record(_app(tmp_path), slug)
    assert out == {"outcome": "ambiguous", "candidates": [slug, slug]}


def test_apply_record_tool_recorded_with_quoted_ats_and_dropped_flags(tmp_path):
    slug = _seed(tmp_path, status="shortlist")
    out = apply_record(_app(tmp_path), slug, ats='greenhouse"; status: applied',
                       url="https://x/apply")
    assert out["outcome"] == "recorded"
    assert "ats" not in out["fields"]
    assert out["ats_dropped"] is True
    assert out["fields"]["applied_url"] == "https://x/apply"


def test_apply_record_tool_raced_when_the_note_left_shortlist_between_read_and_write(
        tmp_path, monkeypatch):
    """Round-5 review finding (PR #132): record()'s `raced` outcome (require_status's
    fresh re-check failing, #131 decision 8) reached this tool's `if not out["ok"]:
    return {"outcome": out["reason"]}` passthrough with zero coverage -- mirrors
    tests/test_apply_record_cli.py's
    test_cmd_apply_record_prints_a_distinct_message_on_raced technique exactly, down
    to this tool's own passthrough shape."""
    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="shortlist")
    note = v.read_leads({"shortlist"})[0]          # STALE snapshot
    v.update_fields(note.ref, {"status": "applied"})  # a "concurrent" writer wins first

    monkeypatch.setattr("sluice.apply.select.resolve", lambda vault, s: [note])

    out = apply_record(_app(tmp_path), slug)
    assert out == {"outcome": "raced"}


def test_apply_record_tool_conflict_on_a_sustained_vault_conflict(tmp_path, monkeypatch):
    """Round-5 review finding (PR #132): record()'s `conflict` outcome (a sustained
    VaultConflict, #16) reached this tool's passthrough with zero coverage either --
    mirrors tests/test_apply_record.py::test_record_returns_conflict_on_vault_conflict
    through this tool's own passthrough shape."""
    from sluice.core.protocols import VaultConflict

    slug = _seed(tmp_path, status="shortlist")

    def boom(*a, **k):
        raise VaultConflict("x")
    monkeypatch.setattr(Vault, "update_fields", boom)

    out = apply_record(_app(tmp_path), slug)
    assert out == {"outcome": "conflict"}


# ── cv_run ───────────────────────────────────────────────────────────────────

def _cv_app(store, cv_out="unused", audit_out="supported\tx\tSF1"):
    """A Sluice wired for a real (non-dry-run) compose_cv call. backend/renderer
    are injected via the constructor's seam-override mechanism (the same one
    store= already uses) -- FakeBackend/FakeRenderer are imported from
    tests/test_cv_engine.py, this module's own established fakes for exactly
    this purpose, not reinvented here."""
    from tests.test_cv_engine import FakeBackend, FakeRenderer
    return Sluice(Config(), store=store, backend=FakeBackend(cv_out, audit_out),
                 renderer=FakeRenderer())


def test_cv_run_tool_not_found(tmp_path):
    app = _cv_app(Vault(str(tmp_path)))
    out = cv_run(app, "nothing here")
    assert out == {"outcome": "not_found"}


def test_cv_run_tool_out_of_scope_for_a_dismissed_lead(tmp_path):
    slug = _seed(tmp_path, status="dismiss")
    app = _cv_app(Vault(str(tmp_path)))
    out = cv_run(app, slug)
    assert out["outcome"] == "out_of_scope"
    assert out["status"] == "dismiss"


def test_cv_run_tool_ambiguous_names_slug_candidates(tmp_path):
    """Round-2 review finding: this branch (the same hand-inlined-resolve shape as
    apply_record's ambiguous branch) had zero coverage -- deleting it left the
    suite green while `results[0]` silently picked one arbitrary candidate's
    skipped-ambiguous verdict, dropping the candidates list entirely. compose_cv's
    own skipped-ambiguous refusal fires before any backend/render call, so the
    FakeBackend/FakeRenderer this fixture wires are never invoked either way."""
    import pathlib
    leads = pathlib.Path(tmp_path) / "Job Applications" / "Job Leads"
    fm = ('company: "Example Northgate"\nrole: "Analyst"\nstatus: shortlist\n'
         'url: "https://example.invalid/1"')
    for sub in ("Active", "Archive"):
        d = leads / sub
        d.mkdir(parents=True)
        (d / "Example Northgate - Analyst.md").write_text("---\n" + fm + "\n---\n\nBODY\n")
    slug = "Example Northgate - Analyst"
    app = _cv_app(Vault(str(tmp_path)))
    out = cv_run(app, slug)
    assert out == {"outcome": "ambiguous", "candidates": [slug, slug]}


def test_cv_run_tool_skipped_needs_signoff_for_a_lead_already_holding_pending_cv(monkeypatch):
    """The single most important test in this slice (Testing item 6): proves the
    #60 latch survives the MCP path unweakened. run_one checks pending_cv BEFORE
    any dossier fetch or compose (verified directly, sluice/cv/engine.py:88), so
    a minimal store carrying just read_leads/read_experience_entries/read_baseline
    is enough -- the fabrication gate never reaches far enough to need more."""
    from tests.test_cv_engine import FakeCache, Note, _cfg

    note = Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst",
                "pending_cv": "CV_deadbeef.pdf (2026-08-14)"},
               path="Job Applications/Job Leads/Example Foundry - Analyst.md")

    class _MinimalCvStore:
        def read_leads(self, statuses=None):
            return [note]
        def read_experience_entries(self, verified_only=True):
            return []
        def read_baseline(self):
            return "BASELINE"

    app = _cv_app(_MinimalCvStore())
    monkeypatch.setattr(app, "dossier_cache", lambda *a, **k: FakeCache())
    monkeypatch.setattr("sluice.cv.config.load_cv_config", _cfg)

    out = cv_run(app, "Example Foundry - Analyst")
    assert out["outcome"] == "skipped-needs-signoff"
    assert "content_warning" not in out
    assert "text" not in out and "cv" not in out and "content" not in out


def test_cv_run_tool_skipped_selection_for_a_non_shortlist_lead(monkeypatch):
    """decision 4: cv_run's own resolution scopes to {"shortlist"} ONLY (unlike
    cv_signoff's wide TRIAGE_OWNED scope) -- a "research" lead is out_of_scope, not
    skipped-selection, since compose_cv's store.read_leads({"shortlist"}) never
    even fetches it. Confirms the out_of_scope path, not run_one's own internal
    (unreachable-via-cv_run) skipped-selection branch.

    Two corrections to the fixture verified directly against the real contracts it
    stands in for, both load-bearing for this test to prove what its own docstring
    claims (neither affects any other test in this file):

    (1) `_MinimalCvStore.read_leads` must actually FILTER by `statuses`, mirroring
        `Vault.read_leads` (sluice/core/vault.py:944-958). A `read_leads` that
        returns the note unconditionally would let compose_cv's OWN
        `store.read_leads({"shortlist"})` find this "research" note anyway, so
        `results` would be non-empty and cv_run would return the note's real
        `run_one` verdict (skipped-selection) instead of ever reaching the
        out_of_scope fallback this test exists to prove.
    (2) The note needs a real `.status` attribute, not just `fm["status"]`.
        `out_of_scope_verdict` reads `n.status` directly (core/leads.py:397) --
        the real `LeadNote` contract's own field (core/protocols.py), distinct
        from `.fm`. tests/test_cv_engine.py's `Note` stand-in never needed one
        (run_one only reads `fm.get("status")`), so it is set here explicitly
        rather than widening that shared fixture for one caller.
    """
    from tests.test_cv_engine import FakeCache, Note, _cfg

    note = Note({"status": "research", "company": "Example Foundry", "role": "Analyst"},
               path="Job Applications/Job Leads/Example Foundry - Analyst.md")
    note.status = "research"

    class _MinimalCvStore:
        def read_leads(self, statuses=None):
            if statuses and note.fm.get("status") not in statuses:
                return []
            return [note]
        def read_experience_entries(self, verified_only=True):
            return []
        def read_baseline(self):
            return "BASELINE"

    app = _cv_app(_MinimalCvStore())
    monkeypatch.setattr(app, "dossier_cache", lambda *a, **k: FakeCache())
    monkeypatch.setattr("sluice.cv.config.load_cv_config", _cfg)

    out = cv_run(app, "Example Foundry - Analyst")
    assert out["outcome"] == "out_of_scope"
    assert out["status"] == "research"


def test_cv_run_tool_bad_backend_raises_value_error_naming_valid_choices(tmp_path):
    """decision 14: `backend` is unvalidated a SECOND time in this module (no
    duplicate copy of the choice set), but the resulting `BackendError` from
    `Sluice.backend` must not leak past this tool as a second exception type --
    the design doc's Error Handling section states cv_run's bad backend joins
    `ValueError`, matching every other malformed-input field in this file.
    `Sluice.backend`'s role guard runs BEFORE checking a constructor override
    (see its own docstring), so this raises even through `_cv_app`'s
    FakeBackend/FakeRenderer overrides -- used here only so the renderer
    construction that `compose_cv` also runs does not require WeasyPrint."""
    app = _cv_app(Vault(str(tmp_path)))
    with pytest.raises(ValueError) as exc_info:
        cv_run(app, "nothing here", backend="bogus")
    message = str(exc_info.value)
    assert "bogus" in message
    for choice in ("auto", "primary", "fallback", "claude-max", "deepseek"):
        assert choice in message


def test_cv_run_tool_accepts_every_valid_backend_choice(tmp_path):
    """Companion to the bad-backend test above: every value `_BackendRole`'s
    `Literal` enum advertises to a schema-validated MCP client must actually pass
    `Sluice.backend`'s role guard, or the schema would be lying about what the
    tool accepts. `_cv_app`'s FakeBackend constructor override short-circuits
    AFTER the role guard (`Sluice.backend`'s own ordering), so this proves the
    guard accepts every value without needing real backend credentials."""
    app = _cv_app(Vault(str(tmp_path)))
    for choice in ("auto", "primary", "fallback", "claude-max", "deepseek"):
        assert cv_run(app, "nothing here", backend=choice) == {"outcome": "not_found"}


# ── cv_signoff ───────────────────────────────────────────────────────────────

def test_cv_signoff_tool_discard_returns_claims_with_content_warning(tmp_path):
    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="shortlist")
    note = v.read_leads()[0]
    v.hold_for_signoff(note.ref, pending="CV_deadbeef.pdf (2026-08-14)",
                       claims='["unsupported claim"]')
    out = cv_signoff(_app(tmp_path), slug, discard=True)
    assert out["outcome"] == "discarded"
    assert out["claims"] == ["unsupported claim"]
    assert "content_warning" in out


def test_cv_signoff_tool_needs_confirmation_writes_nothing(tmp_path):
    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="shortlist")
    note = v.read_leads()[0]
    v.hold_for_signoff(note.ref, pending="CV_deadbeef.pdf (2026-08-14)",
                       claims='["unsupported claim"]')
    out = cv_signoff(_app(tmp_path), slug)
    assert out["outcome"] == "needs_confirmation"
    assert out["pending_cv"] == "CV_deadbeef.pdf (2026-08-14)"
    assert out["claims"] == ["unsupported claim"]
    assert "confirm_token" in out and out["confirm_token"]
    text = pathlib.Path(v.read_leads()[0].ref).read_text()
    assert "pending_cv:" in text and "tailored_cv:" not in text   # NOTHING written


def test_cv_signoff_tool_token_promotes_on_a_second_call(tmp_path):
    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="shortlist")
    note = v.read_leads()[0]
    v.hold_for_signoff(note.ref, pending="CV_deadbeef.pdf (2026-08-14)",
                       claims='["unsupported claim"]')
    app = _app(tmp_path)
    first = cv_signoff(app, slug)
    second = cv_signoff(app, slug, confirm_token=first["confirm_token"])
    assert second["outcome"] == "promoted"
    text = pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text()
    assert "tailored_cv:" in text and "pending_cv:" not in text


def test_cv_signoff_tool_stale_token_after_a_re_hold_writes_nothing(tmp_path):
    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="shortlist")
    note = v.read_leads()[0]
    v.hold_for_signoff(note.ref, pending="CV_first.pdf (2026-08-14)",
                       claims='["claim one"]')
    app = _app(tmp_path)
    first = cv_signoff(app, slug)
    # A re-compose interleaves: a NEW hold with different pending/claims lands before
    # the second call arrives.
    v.sign_off(note.ref, accept=False)   # discard the first hold
    v.hold_for_signoff(note.ref, pending="CV_second.pdf (2026-08-14)",
                       claims='["claim two"]')
    second = cv_signoff(app, slug, confirm_token=first["confirm_token"])
    assert second["outcome"] == "stale_confirmation"
    assert second["pending_cv"] == "CV_second.pdf (2026-08-14)"
    assert "confirm_token" in second
    text = pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text()
    assert "tailored_cv:" not in text   # still nothing promoted


def test_cv_signoff_tool_resolves_a_held_lead_in_dismiss_status(tmp_path):
    """decision 4: cv_signoff keeps sign_off_cv's deliberately WIDE TRIAGE_OWNED
    resolution scope, unlike cv_run's shortlist-only shortlist."""
    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="dismiss")
    note = v.read_leads()[0]
    v.hold_for_signoff(note.ref, pending="CV_deadbeef.pdf (2026-08-14)",
                       claims='["unsupported claim"]')
    out = cv_signoff(_app(tmp_path), slug, discard=True)
    assert out["outcome"] == "discarded"


def test_cv_signoff_tool_not_found(tmp_path):
    out = cv_signoff(_app(tmp_path), "nothing here")
    assert out == {"outcome": "not_found"}


def test_cv_signoff_tool_ambiguous_names_slug_candidates(tmp_path):
    """A genuine slug COLLISION -- two notes at the SAME basename in different
    subfolders -- same fixture technique as test_dismiss_lead_tool_ambiguous_
    names_slug_candidates above (itself verified against tests/test_apply_select.py's
    own `_vault_subfolders` helper). sign_off_cv's own ambiguous check
    (`len(notes) > 1`) runs BEFORE any pending_cv read or confirm callback (sluice/
    core/app.py's sign_off_cv, right after the TRIAGE_OWNED-scoped slug_matches scan),
    so neither note needs a sign-off hold for this to trigger -- a bare call with no
    discard/confirm_token is enough."""
    import pathlib
    leads = pathlib.Path(tmp_path) / "Job Applications" / "Job Leads"
    fm = ('company: "Example Northgate"\nrole: "Analyst"\nstatus: shortlist\n'
         'url: "https://example.invalid/1"')
    for sub in ("Active", "Archive"):
        d = leads / sub
        d.mkdir(parents=True)
        (d / "Example Northgate - Analyst.md").write_text("---\n" + fm + "\n---\n\nBODY\n")
    slug = "Example Northgate - Analyst"
    out = cv_signoff(_app(tmp_path), slug)
    assert out["outcome"] == "ambiguous"
    assert out["candidates"] == [slug, slug]


def test_cv_signoff_tool_second_call_threads_require_pending_into_the_write(tmp_path, monkeypatch):
    """Proves the CAS-freshness half of the confirm-token mechanism actually fires,
    not just that a promoting second call happens to succeed (which
    test_cv_signoff_tool_token_promotes_on_a_second_call already covers, but that test
    alone would keep passing even if a future refactor silently dropped
    sign_off_cv's `effective_require_pending = pending` auto-derivation -- nothing
    interleaves in that test to expose the gap). A spy on the REAL Vault.sign_off
    (not a fake return) records the `require_pending` kwarg it was actually called
    with on the promoting write, and asserts it equals the pending value this exact
    call captured -- proving the second call's own require_pending is genuinely
    threaded through, not merely that no exception was raised.

    `Vault.sign_off`'s real signature (sluice/core/vault.py) is
    `sign_off(self, ref, *, accept=True, require_pending=None)`, matched exactly
    below rather than guessed."""
    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="shortlist")
    note = v.read_leads()[0]
    v.hold_for_signoff(note.ref, pending="CV_deadbeef.pdf (2026-08-14)",
                       claims='["unsupported claim"]')
    app = _app(tmp_path)
    first = cv_signoff(app, slug)

    seen = {}
    real_sign_off = Vault.sign_off
    def _spy(self, ref, *, accept=True, require_pending=None):
        seen["require_pending"] = require_pending
        return real_sign_off(self, ref, accept=accept, require_pending=require_pending)
    monkeypatch.setattr(Vault, "sign_off", _spy)

    second = cv_signoff(app, slug, confirm_token=first["confirm_token"])
    assert second["outcome"] == "promoted"
    assert seen["require_pending"] == "CV_deadbeef.pdf (2026-08-14)"


def test_cv_signoff_tool_vault_stale_outcome_carries_a_detail(tmp_path, monkeypatch):
    """Minor #8 (final whole-branch review): the VAULT-level "stale" outcome
    (require_pending's CAS check failing INSIDE Vault.sign_off's own transform --
    distinct from the token-level "stale_confirmation" tested above, which never
    reaches the store at all) used to return a bare {"outcome": "stale", "slug":
    ...} with no explanation, unlike its needs_confirmation/stale_confirmation
    siblings. Reproduced deterministically: Vault.sign_off is wrapped to
    re-stamp pending_cv to a DIFFERENT value immediately before the real call
    runs -- so the require_pending THIS confirm_token was bound to no longer
    matches the FRESH content the store re-reads inside its own CAS transform."""
    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="shortlist")
    note = v.read_leads()[0]
    v.hold_for_signoff(note.ref, pending="CV_first.pdf (2026-08-14)",
                       claims='["claim one"]')
    app = _app(tmp_path)
    first = cv_signoff(app, slug)

    real_sign_off = Vault.sign_off

    def _racing_sign_off(self, ref, *, accept=True, require_pending=None):
        self.hold_for_signoff(ref, pending="CV_second.pdf (2026-08-14)",
                              claims='["claim two"]')   # simulated concurrent re-compose
        return real_sign_off(self, ref, accept=accept, require_pending=require_pending)

    monkeypatch.setattr(Vault, "sign_off", _racing_sign_off)
    second = cv_signoff(app, slug, confirm_token=first["confirm_token"])
    assert second["outcome"] == "stale"
    assert second["slug"] == slug
    assert "detail" in second and second["detail"]
    text = pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text()
    assert "tailored_cv:" not in text   # nothing promoted


def test_cv_signoff_tool_conflict_on_a_sustained_vault_conflict(tmp_path, monkeypatch):
    """Round-5 review finding (PR #132): the SAME gap shape as dismiss_lead's and
    apply_record's own conflict-passthrough gaps above -- Vault.sign_off's `conflict`
    outcome (a sustained write race, #16, distinct from the CAS-fresh `stale` outcome
    tested above) reached this tool's generic `out = {"outcome": result.outcome, ...}`
    passthrough with zero coverage. Forced at the store method (mirrors
    tests/functional/test_cv.py::test_cv_signoff_conflict_returns_1's CLI-layer
    technique) rather than a real race, so the test is deterministic. discard=True
    reaches the write directly, with no confirm_token round-trip needed."""
    from sluice.core.protocols import VaultConflict

    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="shortlist")
    note = v.read_leads()[0]
    v.hold_for_signoff(note.ref, pending="CV_deadbeef.pdf (2026-08-14)",
                       claims='["unsupported claim"]')

    def _always_conflicts(self, ref, *, accept=True, require_pending=None):
        raise VaultConflict(ref)
    monkeypatch.setattr(Vault, "sign_off", _always_conflicts)

    out = cv_signoff(_app(tmp_path), slug, discard=True)
    assert out == {"outcome": "conflict", "slug": slug}
    text = pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text()
    assert "pending_cv:" in text and "tailored_cv:" not in text   # hold intact


def test_confirm_token_is_keyed_and_not_forgeable_from_a_bare_sha256(tmp_path):
    """Minor #7 (final whole-branch review): _confirm_token used to be an
    UNKEYED sha256(json.dumps(...)) -- against the adversarial-agent threat
    model this whole feature is designed for, pending_cv and the flagged claims
    are both readable via get_lead, so an agent capable of computing a sha256
    could construct a valid confirm_token WITHOUT ever making the real first
    cv_signoff call. Proves the fix: replaying the OLD unkeyed formula against
    the exact (slug, pending, claims) tuple the real needs_confirmation
    response captured does NOT reproduce the real token, and that forged value
    is correctly rejected as stale_confirmation (never promoted)."""
    import hashlib
    import json

    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="shortlist")
    note = v.read_leads()[0]
    v.hold_for_signoff(note.ref, pending="CV_deadbeef.pdf (2026-08-14)",
                       claims='["unsupported claim"]')
    app = _app(tmp_path)
    first = cv_signoff(app, slug)
    real_token = first["confirm_token"]

    forged = hashlib.sha256(
        json.dumps([slug, first["pending_cv"], first["claims"]],
                  sort_keys=True).encode("utf-8")).hexdigest()
    assert forged != real_token

    second = cv_signoff(app, slug, confirm_token=forged)
    assert second["outcome"] == "stale_confirmation"
    text = pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text()
    assert "tailored_cv:" not in text   # the forged token never promoted anything


def test_a_non_ascii_confirm_token_is_a_mismatch_not_a_crash(tmp_path):
    """Round-2 review finding: hmac.compare_digest raises TypeError on a non-ASCII
    str -- it cannot compare code points outside ASCII in constant time. An MCP
    client is untrusted and can send any string as confirm_token, so this must
    report a plain mismatch (stale_confirmation), never propagate a TypeError."""
    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="shortlist")
    note = v.read_leads()[0]
    v.hold_for_signoff(note.ref, pending="CV_deadbeef.pdf (2026-08-14)",
                       claims='["unsupported claim"]')
    app = _app(tmp_path)
    cv_signoff(app, slug)

    second = cv_signoff(app, slug, confirm_token="café" * 16)
    assert second["outcome"] == "stale_confirmation"
    text = pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text()
    assert "tailored_cv:" not in text


# ── dismiss_lead remedy formatting ──────────────────────────────────────────

def test_dismiss_lead_tool_refused_signoff_hold_remedy_is_valid_json_and_python(tmp_path):
    """Minor #10 (final whole-branch review): the remedy string used to mix a
    Python repr()-style single-quoted lead value with a lowercase JSON-literal
    `true`, which is valid as neither pure Python nor pure JSON and is not
    directly copy-pasteable. json.dumps(slug) makes the whole example a
    consistent, double-quoted, correctly-escaped style."""
    slug = _seed(tmp_path, status="shortlist", pending_cv='"CV_deadbeef.pdf (2026-08-14)"')
    out = dismiss_lead(_app(tmp_path), slug, "no fit")
    assert out["outcome"] == "refused_signoff_hold"
    expected = f'cv_signoff(lead="{slug}", discard=true)'
    assert expected in out["detail"]


# ── create_lead ──────────────────────────────────────────────────────────────

def test_create_lead_tool_reports_created_with_slug(tmp_path):
    out = create_lead(_app(tmp_path), title="Example Role", company="Example Ltd",
                      url="https://example.invalid/1")
    assert out == {"outcome": "created", "slug": "Example Ltd - Example Role"}


def test_create_lead_tool_reports_the_collision_detail_on_updated(tmp_path):
    app = _app(tmp_path)
    create_lead(app, title="Example Role", company="Example Ltd",
               url="https://example.invalid/1")
    out = create_lead(app, title="Example Role", company="Example Ltd",
                      url="https://example.invalid/1")
    assert out["outcome"] == "updated"
    assert "NOT recorded" in out["detail"]


def test_create_lead_tool_reports_the_collision_detail_on_merged(tmp_path):
    # When company+title collide but URLs differ, Vault._reconcile returns "merged"
    # (inconclusive evidence it's the same posting). Same semantics as "updated":
    # only last_seen bumped, incoming data NOT recorded.
    app = _app(tmp_path)
    create_lead(app, title="Example Role", company="Example Ltd",
               url="https://example.invalid/1")
    out = create_lead(app, title="Example Role", company="Example Ltd",
                      url="https://example.invalid/DIFFERENT")
    assert out["outcome"] == "merged"
    assert "NOT recorded" in out["detail"]


def test_create_lead_tool_reports_merged_away_detail(tmp_path):
    """Round-2 review finding: merged_away had zero coverage at the MCP tool layer
    -- does _DETAIL actually attach, does 'slug' stay absent from the response."""
    v = Vault(str(tmp_path))
    survivor = Lead(source="s", search="q", title="Survivor Role", company="Example Foundry",
                    url="https://example.invalid/survivor")
    loser = Lead(source="s", search="q", title="Loser Role", company="Example Foundry",
                url="https://example.invalid/loser")
    assert v.upsert(survivor).outcome == "created"
    assert v.upsert(loser).outcome == "created"
    notes = {n.fm.get("url"): n for n in v.read_leads()}
    v.merge_cluster(notes[survivor.url].ref, [notes[loser.url].ref],
                    alt_urls=[loser.url], first_seen="2026-01-01", last_seen="2026-01-01")

    out = create_lead(_app(tmp_path), title="Loser Role", company="Example Foundry",
                      url="https://example.invalid/loser")
    assert out["outcome"] == "merged_away"
    assert "slug" not in out
    assert "nothing new was written" in out["detail"]


def test_create_lead_tool_raises_valueerror_for_an_unsafe_field(tmp_path):
    with pytest.raises(ValueError, match="company"):
        create_lead(_app(tmp_path), title="Example Role", company="Bad\nCompany",
                    url="https://example.invalid/1")


def test_create_lead_tool_refused_reports_no_slug(tmp_path):
    out = create_lead(_app(tmp_path), title=" ", company=" ",
                      url="https://example.invalid/1")
    assert out["outcome"] == "refused"
    assert "slug" not in out


# ── isolation sweep (decision 2) ─────────────────────────────────────────────
#
# Widened per important finding #2 of the final whole-branch review: the ORIGINAL
# sweep walked only `ast.ImportFrom` nodes and checked module names against an
# allow-list, which missed two real violation shapes -- proven by running the
# original sweep's exact logic against a synthetic module containing both:
#
#   1. `import sluice.core.vault as v` -- an `ast.Import` node, entirely
#      invisible to a sweep that only walks `ast.ImportFrom`.
#   2. A direct store-WRITE call, e.g. `sluice.store().update_fields(...)` --
#      reachable with NO new import at all, since mcpserver.py already calls
#      `sluice.store()` eight times for reads. An import-only sweep, however
#      widened, can never catch this on its own.
#
# `_isolation_violations` is the ONE checker exercised three ways below: against
# the REAL mcpserver.py source (must report nothing), and against two synthetic
# snippets built to contain exactly one of the two gaps each (must report
# exactly that one violation) -- proving the checker actually fires on what it
# claims to catch, not merely that it passes vacuously against clean code.

_ISOLATION_ALLOWED_MODULES = frozenset({
    "sluice.core.app", "sluice.core.leads", "sluice.core.status",
})

# Every WRITE method on the Store protocol (sluice/core/protocols.py), DERIVED off
# that Protocol's own `vars()` rather than hand-listed -- round-4 review finding: the
# previous version's comment already claimed this and the literal set happened to
# agree, but a future write method added to Store would silently miss this sweep
# with no test failure to say so. Its read-only members (read_leads,
# read_experience_entries, read_baseline, read_criteria; the optional preflight
# hook, which is never declared in the class body at all) are excluded by name,
# since a read reaching this deep is exactly what the module-allow-list above
# already permits via Sluice's own store() access.
_STORE_READ_METHODS = frozenset({
    "read_leads", "read_experience_entries", "read_baseline", "read_criteria",
})
_STORE_WRITE_METHODS = frozenset(
    name for name, member in vars(Store).items()
    if not name.startswith("_") and callable(member)
) - _STORE_READ_METHODS
assert _STORE_WRITE_METHODS, (
    "Store protocol introspection found no write methods -- the derivation above is "
    "broken, not the protocol; the isolation sweep would silently guard nothing")


def _isolation_violations(tree) -> list:
    """Every isolation-boundary violation in `tree`: a `sluice.`-prefixed
    import (via `import` OR `from ... import`) outside
    `_ISOLATION_ALLOWED_MODULES`, or a call to a method named in
    `_STORE_WRITE_METHODS` anywhere in the module."""
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and \
                node.module.startswith("sluice."):
            if node.module not in _ISOLATION_ALLOWED_MODULES:
                bad.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sluice.") and \
                        alias.name not in _ISOLATION_ALLOWED_MODULES:
                    bad.append(f"import {alias.name}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in _STORE_WRITE_METHODS:
            bad.append(f"call to .{node.func.attr}(...)")
    return bad


def test_mcpserver_imports_from_sluice_only_within_an_explicit_allow_list():
    """The isolation sweep: sluice/mcpserver.py may import from `sluice.` ONLY the
    modules in `_ISOLATION_ALLOWED_MODULES` (via either import form) and must
    contain no direct call to a Store write method -- proving, structurally, that
    no write tool can reach a lower-level write path (cv.engine, vault,
    apply.record) directly instead of through a Sluice method. Asserts on SCOPE
    too (>=N imported NAMES examined across every `sluice.`-prefixed
    `ImportFrom` statement, not just >=1 import statement), so a broken matcher
    cannot pass vacuously -- mirrors the existing mcp-import sweep's shape from
    #105. Counting individual `alias` nodes rather than `ImportFrom` statements
    matters here: this module's final shape has only 3 such statements
    (`sluice.core.app`, `sluice.core.leads`, `sluice.core.status`) but 7 names
    imported across them (Sluice; UNTRUSTED_SCRAPED_CONTENT_WARNING,
    UNTRUSTED_DERIVED_CONTENT_WARNING, out_of_scope_verdict, slug_matches;
    CANONICAL, TRIAGE_OWNED, normalize) -- counting statements alone would make
    this assertion far too easy to satisfy vacuously with a near-empty file."""
    import inspect

    tree = ast.parse(inspect.getsource(mcpserver_mod))
    bad = _isolation_violations(tree)
    assert bad == [], (
        f"sluice/mcpserver.py isolation-sweep violation(s): {bad!r} -- every "
        f"write must route through a Sluice method, never a lower-level module "
        f"import or a direct store-write call")
    seen = sum(len(node.names) for node in ast.walk(tree)
              if isinstance(node, ast.ImportFrom) and node.module
              and node.module.startswith("sluice."))
    assert seen >= 5, "the sweep examined suspiciously few sluice. imports -- broken matcher?"


def test_isolation_sweep_catches_a_bare_sluice_dotted_import():
    """Mutation-proof #1 (important finding #2): `import sluice.core.vault as v`
    is an `ast.Import` node, invisible to a sweep that only walks
    `ast.ImportFrom` -- the ORIGINAL shape of this sweep."""
    src = "import sluice.core.vault as v\n\ndef f():\n    return v.Vault('.')\n"
    assert _isolation_violations(ast.parse(src)) == ["import sluice.core.vault"]


def test_isolation_sweep_catches_a_direct_store_write_call_with_no_new_import():
    """Mutation-proof #2 (important finding #2): mcpserver.py ALREADY
    legitimately calls `sluice.store()` eight times for reads, so a write
    reachable through it needs no new import at all -- an import-only sweep can
    never catch this regardless of how wide its allow-list is."""
    src = "def f(sluice):\n    return sluice.store().update_fields(None, {})\n"
    assert _isolation_violations(ast.parse(src)) == ["call to .update_fields(...)"]


# ── write-flag registration ──────────────────────────────────────────────────

def test_build_server_accepts_a_write_parameter():
    """Only the signature is checked here. The behavioural proof that write=False
    omits the five write tools from tools/list lives at the SDK layer
    (tests/functional/test_mcp_contract.py, Task 12)."""
    import inspect

    from sluice.mcpserver import build_server
    assert "write" in inspect.signature(build_server).parameters
