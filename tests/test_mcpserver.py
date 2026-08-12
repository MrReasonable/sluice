"""sluice/mcpserver.py's plain tool functions, called directly against a real
`Sluice`/`Vault` -- no MCP protocol machinery, no `build_server()`/`serve()`. Fixture
notes are built through `Vault.upsert` so their slugs are REAL store-issued filenames,
matching `tests/test_leads_expire.py`'s own rationale for doing the same (a hand-written
slug format could pass here while the shipped command matches nothing).
"""
import dataclasses

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.vault import Vault
from sluice.mcpserver import doctor, get_lead, health, list_leads


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


def test_list_leads_rejects_an_unknown_status_naming_the_valid_set(tmp_path):
    try:
        list_leads(_app(tmp_path), statuses=["not-a-real-status"])
        assert False, "expected a ValueError"
    except ValueError as e:
        assert "not-a-real-status" in str(e)
        assert "shortlist" in str(e)  # a real canonical status, proving the valid set is named


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
    """The AST sweep: every mcp-named Import/ImportFrom node in sluice/cli.py and
    sluice/mcpserver.py must be a DESCENDANT of build_server()'s own FunctionDef --
    not merely "build_server's body contains at least one such node" (a stray
    top-level `import mcp` sitting ALONGSIDE a correctly-guarded one must still fail
    this)."""
    import ast
    import inspect

    import sluice.cli as cli_mod
    import sluice.mcpserver as mcpserver_mod

    def _bad_mcp_imports(module, allowed_func_name=None):
        tree = ast.parse(inspect.getsource(module))
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

    assert _bad_mcp_imports(cli_mod) == [], "sluice/cli.py must carry no mcp import"
    assert _bad_mcp_imports(mcpserver_mod, allowed_func_name="build_server") == [], (
        "sluice/mcpserver.py must import mcp only inside build_server()'s own "
        "function body (directly, or via a helper nested inside it)")


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
