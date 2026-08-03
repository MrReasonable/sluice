"""The facade + CLI for `sluice leads reconcile` (#1)."""
import json as _json
import os

import pytest

from sluice.cli import cmd_leads_reconcile
from sluice.core.app import Sluice, StoreHasNoLayout
from sluice.core.config import Config
from sluice.core.leads import ACTIVE_SUBDIR


def _seed(leads_dir, rel, *, company="Example Ltd", role="Example Role", status="new"):
    path = os.path.join(leads_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"---\ncompany: {company}\nrole: {role}\nstatus: {status}\n"
                 f"url: \nlast_seen: 2026-01-01\n---\nbody\n")
    return path


class _Args:
    def __init__(self, **kw):
        self.apply = kw.get("apply", False)
        self.json = kw.get("json", False)


def _cfg(tmp_path, monkeypatch, layout="active_archive"):
    monkeypatch.delenv("VAULT_DIR", raising=False)
    cfg = Config(vault_dir=str(tmp_path), lead_layout=layout)
    leads = Sluice(cfg).store().leads_dir
    os.makedirs(leads, exist_ok=True)
    return cfg, leads


# ── the facade ────────────────────────────────────────────────────────────────
def test_the_facade_report_changes_nothing(tmp_path, monkeypatch):
    cfg, leads = _cfg(tmp_path, monkeypatch)
    src = _seed(leads, "A - Live.md", role="Live", status="shortlist")
    rep = Sluice(cfg).reconcile_report()
    assert len(rep["moves"]) == 1
    assert os.path.isfile(src)


def test_the_facade_applies(tmp_path, monkeypatch):
    cfg, leads = _cfg(tmp_path, monkeypatch)
    _seed(leads, "A - Live.md", role="Live", status="shortlist")
    Sluice(cfg).reconcile(apply=True)
    assert os.path.isfile(os.path.join(leads, ACTIVE_SUBDIR, "A - Live.md"))


def test_a_store_without_a_layout_fails_loudly_and_names_it(tmp_path, monkeypatch):
    """The capability check. It is inert today -- `vault` is the only registered store -- but
    unlike track/receipt.py's deleted branch it is WITNESSABLE through the public seam, which is
    the discriminator read_leads' duplicate-slug comment sets out."""
    monkeypatch.delenv("VAULT_DIR", raising=False)

    class _NoLayout:
        def read_leads(self, statuses=None):
            return []

    # Through the PUBLIC seam-override kwarg: `Sluice.__init__` validates the name against _SEAMS,
    # so this also proves the injection point is real.
    app = Sluice(Config(vault_dir=str(tmp_path), lead_layout="active_archive"),
                 store=_NoLayout())
    with pytest.raises(StoreHasNoLayout, match="_NoLayout"):
        app.reconcile_report()


def test_the_cli_renders_a_layoutless_store_as_a_usage_error(tmp_path, capsys, monkeypatch):
    """The guard's whole stated benefit is "a sentence instead of a traceback", and raising alone
    does not deliver it: `cli.py:main` catches only ValueError around `load_config` and then
    returns `args.func(args, config)` bare, so a RuntimeError would propagate as an uncaught
    traceback and the user would swap one traceback for another. rc 2 matches the usage-error
    convention main's config arm and cmd_init already use."""
    monkeypatch.delenv("VAULT_DIR", raising=False)

    class _NoLayout:
        def read_leads(self, statuses=None):
            return []

    cfg = Config(vault_dir=str(tmp_path), lead_layout="active_archive")
    monkeypatch.setattr("sluice.core.app.Sluice.store", lambda self: _NoLayout())
    assert cmd_leads_reconcile(_Args(), cfg) == 2
    assert "no folder layout" in capsys.readouterr().err


# ── the CLI ───────────────────────────────────────────────────────────────────
def test_the_cli_report_exits_zero_and_writes_nothing(tmp_path, capsys, monkeypatch):
    cfg, leads = _cfg(tmp_path, monkeypatch)
    src = _seed(leads, "A - Live.md", role="Live", status="shortlist")
    assert cmd_leads_reconcile(_Args(), cfg) == 0
    assert os.path.isfile(src)
    out = capsys.readouterr()
    # The report goes to STDOUT (dedupe's shape, so `| grep` works); the summary to stderr.
    assert "A - Live" in out.out
    assert "report only" in out.err


def test_the_cli_apply_moves_and_exits_zero(tmp_path, monkeypatch):
    cfg, leads = _cfg(tmp_path, monkeypatch)
    _seed(leads, "A - Live.md", role="Live", status="shortlist")
    assert cmd_leads_reconcile(_Args(apply=True), cfg) == 0
    assert os.path.isfile(os.path.join(leads, ACTIVE_SUBDIR, "A - Live.md"))


def test_an_apply_that_refused_a_note_exits_non_zero(tmp_path, monkeypatch):
    """A silent 0 on a write the user asked for and did not get is the no-op this report-first
    shape exists to avoid -- the `_FAILED` rule cmd_leads_expire states."""
    cfg, leads = _cfg(tmp_path, monkeypatch)
    _seed(leads, os.path.join(ACTIVE_SUBDIR, "A - Twin.md"), role="Twin", status="shortlist")
    _seed(leads, "A - Twin.md", role="Twin", status="dismiss")
    assert cmd_leads_reconcile(_Args(apply=True), cfg) == 1


def test_an_unset_layout_says_so_and_reports_zero(tmp_path, capsys, monkeypatch):
    """Decision 7, and the `lead_ttl_days: 0` precedent verbatim: NOT '0 to move', which is
    indistinguishable from 'nothing is out of place' and would let a user believe a knob they
    never configured is filing their vault."""
    cfg, _leads = _cfg(tmp_path, monkeypatch, layout="")
    assert cmd_leads_reconcile(_Args(), cfg) == 0
    assert "lead_layout is unset" in capsys.readouterr().err


def test_an_unset_layout_exits_non_zero_when_apply_was_asked_for(tmp_path, monkeypatch):
    cfg, _leads = _cfg(tmp_path, monkeypatch, layout="")
    assert cmd_leads_reconcile(_Args(apply=True), cfg) == 1


def test_the_unset_layout_json_arm_still_emits_a_document(tmp_path, capsys, monkeypatch):
    """A consumer parsing stdout must not have to tell 'no output' from 'empty result' --
    cmd_leads_expire's rule. And it must carry the SAME key set as the layout-ON arm, or a bucket
    added later would leave this document short with nothing saying so."""
    cfg, _leads = _cfg(tmp_path, monkeypatch, layout="")
    cmd_leads_reconcile(_Args(json=True), cfg)
    doc = _json.loads(capsys.readouterr().out)
    assert doc["layout"] == ""
    assert set(doc) >= {"layout", "moves", "in_place", "ambiguous", "unknown",
                        "user_filed", "collisions", "skipped"}


def test_the_json_report_carries_every_bucket(tmp_path, capsys, monkeypatch):
    cfg, leads = _cfg(tmp_path, monkeypatch)
    _seed(leads, "A - Live.md", role="Live", status="shortlist")
    cmd_leads_reconcile(_Args(json=True), cfg)
    doc = _json.loads(capsys.readouterr().out)
    assert set(doc) >= {"layout", "moves", "in_place", "ambiguous", "unknown",
                        "user_filed", "collisions", "skipped"}


def test_the_subparser_registers_apply_and_has_no_dry_run():
    """No --dry-run: the default IS the dry run, and a flag that does nothing is drift (`leads
    dedupe` and `leads expire` are the same shape). Asserted through the real parser, because a
    flag added later would otherwise ship unnoticed."""
    from sluice.cli import _build_parser
    p = _build_parser()
    ns = p.parse_args(["leads", "reconcile", "--apply"])
    assert ns.apply is True and ns.func is cmd_leads_reconcile
    with pytest.raises(SystemExit):
        p.parse_args(["leads", "reconcile", "--dry-run"])
