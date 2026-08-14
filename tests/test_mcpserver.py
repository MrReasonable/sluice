"""sluice/mcpserver.py's plain tool functions, called directly against a real
`Sluice`/`Vault` -- no MCP protocol machinery, no `build_server()`/`serve()`. Fixture
notes are built through `Vault.upsert` so their slugs are REAL store-issued filenames,
matching `tests/test_leads_expire.py`'s own rationale for doing the same (a hand-written
slug format could pass here while the shipped command matches nothing).
"""
import dataclasses
import pathlib

import sluice.mcpserver as mcpserver_mod
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import UNTRUSTED_SCRAPED_CONTENT_WARNING, Lead
from sluice.core.vault import Vault
from sluice.mcpserver import (
    apply_record,
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
    try:
        list_leads(_app(tmp_path), statuses=["not-a-real-status"])
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "not-a-real-status" in str(e)
        assert "shortlist" in str(e)  # a real canonical status, proving the valid set is named


def test_list_leads_unknown_status_error_names_every_bad_status_not_just_the_first(tmp_path):
    try:
        list_leads(_app(tmp_path), statuses=["nope-one", "nope-two"])
        assert False, "expected a ValueError"
    except ValueError as e:
        # deferred-minor #6: `unknown` is the full sorted set of bad statuses -- the
        # message must name ALL of them, not just unknown[0].
        assert "nope-one" in str(e)
        assert "nope-two" in str(e)


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
    try:
        list_leads(_app(tmp_path), limit=-1)
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "-1" in str(e)


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

    monkeypatch.setattr(mcpserver_mod, "build_server", lambda config: _FakeMcpServer())
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

    monkeypatch.setattr(mcpserver_mod, "serve", lambda config: None)
    args = _build_parser().parse_args(["mcp", "serve"])
    assert cmd_mcp_serve(args, Config()) == 0


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
