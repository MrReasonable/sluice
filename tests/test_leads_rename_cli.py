"""The facade + CLI for `sluice leads rename` (#151) -- the filename-to-frontmatter rename
pass, wired the same way Task 1's `leads reconcile` wires `Vault.reconcile_layout`
(`tests/test_leads_reconcile_cli.py` is this file's structural template)."""
import argparse
import json as _json
import os
import sqlite3

import pytest

from sluice.cli import cmd_leads_rename
from sluice.core.app import Sluice, StoreCannotRename
from sluice.core.config import Config
from sluice.track.deadletter import DeadLetterDb, Entry, deadletter_path


def _seed(leads_dir, rel, *, company="Unknown", role="Example Role", status="new"):
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


def _cfg(tmp_path, monkeypatch):
    """A vault Config for the store, PLUS a `$SLUICE_CONFIG` yaml naming track.seen_db --
    `rename(apply=True)`/`rename_report()` load track config independently of the `Config`
    object handed to `Sluice()`, exactly the way `Sluice.track()`/`track_confirm()` already do
    (see tests/test_track_app.py's identically-shaped `_cfg`)."""
    monkeypatch.delenv("VAULT_DIR", raising=False)
    vault_dir = tmp_path / "vault"
    seen_db = tmp_path / "track-seen.db"
    cfgp = tmp_path / "cfg.yaml"
    cfgp.write_text(f"vault_dir: {vault_dir}\ntrack:\n  seen_db: {seen_db}\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))
    cfg = Config(vault_dir=str(vault_dir))
    leads = Sluice(cfg).store().leads_dir
    os.makedirs(leads, exist_ok=True)
    return cfg, leads, str(seen_db)


# ── the facade ────────────────────────────────────────────────────────────────
def test_the_facade_report_changes_nothing(tmp_path, monkeypatch):
    cfg, leads, _seen_db = _cfg(tmp_path, monkeypatch)
    src = _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    rep = Sluice(cfg).rename_report()
    assert rep["renames"] == [("Unknown - Example Role", "Example Co - Example Role", ".")]
    assert os.path.isfile(src)
    assert not os.path.exists(os.path.join(leads, "Example Co - Example Role.md"))


def test_the_facade_applies(tmp_path, monkeypatch):
    cfg, leads, _seen_db = _cfg(tmp_path, monkeypatch)
    src = _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    rep = Sluice(cfg).rename(apply=True)
    assert os.path.isfile(os.path.join(leads, "Example Co - Example Role.md"))
    assert not os.path.exists(src)
    # No dead-letter rows existed for this lead, so the migration loop did nothing -- but the
    # bucket is still present and shaped, never omitted on a quiet run.
    assert rep["deadletter"] == {"refiled": 0, "failed": []}


def test_apply_false_delegates_to_rename_report(tmp_path, monkeypatch):
    """`rename(apply=False)` must be the SAME read path as `rename_report()`, not a second
    hand-copied implementation that can drift from it."""
    cfg, leads, _seen_db = _cfg(tmp_path, monkeypatch)
    _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    assert Sluice(cfg).rename(apply=False) == Sluice(cfg).rename_report()


def test_a_store_without_rename_support_fails_loudly_and_names_it(tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_DIR", raising=False)

    class _NoRename:
        def read_leads(self, statuses=None):
            return []

    app = Sluice(Config(vault_dir=str(tmp_path)), store=_NoRename())
    with pytest.raises(StoreCannotRename, match="_NoRename"):
        app.rename_report()


def test_the_cli_renders_a_renameless_store_as_a_usage_error(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("VAULT_DIR", raising=False)

    class _NoRename:
        def read_leads(self, statuses=None):
            return []

    cfg = Config(vault_dir=str(tmp_path))
    monkeypatch.setattr("sluice.core.app.Sluice.store", lambda self: _NoRename())
    assert cmd_leads_rename(_Args(), cfg) == 2
    assert "cannot rename lead notes" in capsys.readouterr().err


# ── the CLI ───────────────────────────────────────────────────────────────────
def test_the_cli_report_exits_zero_and_writes_nothing(tmp_path, capsys, monkeypatch):
    cfg, leads, _seen_db = _cfg(tmp_path, monkeypatch)
    src = _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    assert cmd_leads_rename(_Args(), cfg) == 0
    assert os.path.isfile(src)
    out = capsys.readouterr()
    # Human report to STDOUT (so `| grep` works); trailing summary to STDERR -- the same split
    # `leads reconcile` uses.
    assert "Example Co - Example Role" in out.out
    assert "report only" in out.err


def test_the_cli_apply_renames_and_exits_zero(tmp_path, monkeypatch):
    cfg, leads, _seen_db = _cfg(tmp_path, monkeypatch)
    _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    assert cmd_leads_rename(_Args(apply=True), cfg) == 0
    assert os.path.isfile(os.path.join(leads, "Example Co - Example Role.md"))


def test_unresolved_is_counted_not_listed_in_the_human_report(tmp_path, capsys, monkeypatch):
    """A test that would FAIL if someone "fixed" the deliberate suppression in cmd_leads_rename
    into a full per-item listing: hundreds of unresolved notes would bury the handful of
    actionable renames/collisions/skips."""
    cfg, leads, _seen_db = _cfg(tmp_path, monkeypatch)
    _seed(leads, "Unknown - Example Role.md", company="Unknown", role="Example Role")
    assert cmd_leads_rename(_Args(), cfg) == 0
    out = capsys.readouterr()
    assert "Unknown - Example Role" not in out.out, "unresolved was listed item-by-item"
    assert "unresolved=1" in out.err


def test_the_json_report_carries_every_key(tmp_path, capsys, monkeypatch):
    cfg, leads, _seen_db = _cfg(tmp_path, monkeypatch)
    _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    cmd_leads_rename(_Args(json=True), cfg)
    doc = _json.loads(capsys.readouterr().out)
    assert set(doc) >= {"examined", "renames", "unresolved", "collisions", "ambiguous",
                        "resurrected", "skipped", "deadletter"}
    assert set(doc["deadletter"]) >= {"pending"}


def test_the_subparser_registers_apply_and_has_no_dry_run():
    """No --dry-run: the default IS the dry run (`leads reconcile`/`dedupe`/`expire`'s shape).
    Asserted through the real parser so a flag added later would not ship unnoticed."""
    from sluice.cli import _build_parser
    p = _build_parser()
    ns = p.parse_args(["leads", "rename", "--apply"])
    assert ns.apply is True and ns.func is cmd_leads_rename
    with pytest.raises(SystemExit):
        p.parse_args(["leads", "rename", "--dry-run"])


def test_the_help_text_carries_both_required_warnings():
    """Fetched through the REAL argparse tree, not a hand-typed string -- so a wording edit
    that quietly drops either element is caught here rather than only by human review."""
    from sluice.cli import _build_parser
    p = _build_parser()
    top = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    leads_sp = next(a for a in top.choices["leads"]._actions
                    if isinstance(a, argparse._SubParsersAction))
    desc = leads_sp.choices["rename"].description
    assert "do not run --apply concurrently" in desc.lower()
    assert "resurrected" in desc
    assert "seen.db" in desc


# ── exit-code rule ───────────────────────────────────────────────────────────
def _make_ambiguous(leads, monkeypatch):
    # Two notes already sharing one slug, in different folders -- the pre-existing-collision
    # class `index_by_slug` reports regardless of whether either note needs renaming.
    _seed(leads, "A - Twin.md", company="Example Co", role="Twin")
    _seed(leads, os.path.join("FolderX", "A - Twin.md"), company="Example Co", role="Twin")


def _make_collision(leads, monkeypatch):
    _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    _seed(leads, "Example Co - Example Role.md", company="Example Co", role="Example Role")


def _make_skipped(leads, monkeypatch):
    real = os.path.join(os.path.dirname(leads), "elsewhere.md")
    with open(real, "w", encoding="utf-8") as fh:
        fh.write("---\ncompany: Example Co\nrole: Example Role\nstatus: new\n"
                 "url: \nlast_seen: 2026-01-01\n---\nbody\n")
    os.symlink(real, os.path.join(leads, "Unknown - Example Role.md"))


def _make_resurrected(leads, monkeypatch):
    _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    from sluice.core import vault as vaultmod
    real_move = vaultmod._reserve_and_move

    def racing_move(s, dest_dir, base, **kw):
        # Exactly what a concurrent _atomic_write's os.replace(tmp, path) does when it lands
        # after the rename: the OLD source path exists again (test_leads_rename.py's own
        # fixture for this race, reused here at the facade/CLI layer).
        dest = real_move(s, dest_dir, base, **kw)
        with open(s, "w", encoding="utf-8") as fh:
            fh.write("---\ncompany: Example Co\nrole: Example Role\nstatus: applied\n"
                      "---\nbody\n")
        return dest

    monkeypatch.setattr(vaultmod, "_reserve_and_move", racing_move)


def _make_deadletter_failed(leads, monkeypatch):
    _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")

    def _boom(self, old_slug, new_slug):
        raise RuntimeError("boom")

    monkeypatch.setattr(DeadLetterDb, "rename_lead", _boom)


@pytest.mark.parametrize("make", [
    _make_ambiguous, _make_collision, _make_skipped, _make_resurrected, _make_deadletter_failed,
], ids=["ambiguous", "collisions", "skipped", "resurrected", "deadletter_failed"])
def test_each_failure_bucket_exits_non_zero_under_apply(tmp_path, monkeypatch, make):
    cfg, leads, _seen_db = _cfg(tmp_path, monkeypatch)
    make(leads, monkeypatch)
    assert cmd_leads_rename(_Args(apply=True), cfg) == 1


def test_a_clean_sweep_exits_zero_under_apply(tmp_path, monkeypatch):
    cfg, leads, _seen_db = _cfg(tmp_path, monkeypatch)
    _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    assert cmd_leads_rename(_Args(apply=True), cfg) == 0


def test_unresolved_alone_does_not_cause_a_non_zero_exit(tmp_path, monkeypatch):
    """`unresolved` does NOT count toward the exit code -- same treatment as `reconcile`'s
    `unknown`/`user_filed`: it is a state this pass is DESIGNED to leave alone (the fix belongs
    at the ingest/resolution layer), so counting it would make a correct run exit 1 forever."""
    cfg, leads, _seen_db = _cfg(tmp_path, monkeypatch)
    _seed(leads, "Unknown - Example Role.md", company="Unknown", role="Example Role")
    assert cmd_leads_rename(_Args(apply=True), cfg) == 0


# ── dead-letter wiring ───────────────────────────────────────────────────────
def test_deadletter_rows_are_refiled_end_to_end_and_findable_by_track_confirm(
        tmp_path, monkeypatch):
    cfg, leads, seen_db = _cfg(tmp_path, monkeypatch)
    old_slug = "Unknown - Example Role"
    _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role",
          status="shortlist")
    dl = DeadLetterDb(deadletter_path(seen_db))
    dl.record(Entry(message_id="m1", lead=old_slug, candidates="", ev_type="receipt",
                    proposal="p", hint="h", first_seen="2026-01-01", times_surfaced=1))

    rep = Sluice(cfg).rename(apply=True)
    new_slug = "Example Co - Example Role"
    assert rep["renames"] == [(old_slug, new_slug, ".")]
    assert rep["deadletter"] == {"refiled": 1, "failed": []}
    # The row followed the rename -- it is now filed under the NEW slug, not the old one.
    assert {e.lead for e in dl.open_entries()} == {new_slug}

    # And it is actually FINDABLE by the ordinary confirm path: `track confirm` resolves the
    # lead by its (now current) slug, advances status, and clears the lead's dead-letter rows --
    # which only works if rename_lead genuinely re-filed this row under the new slug.
    out = Sluice(cfg).track_confirm(lead=new_slug, to="applied")
    assert out["ok"] is True
    assert dl.open_entries() == []


def test_an_unreachable_deadletter_store_refuses_the_whole_apply_with_zero_renames(
        tmp_path, monkeypatch):
    """The refuse-before-any-write ordering (#151's own stated requirement): a dead-letter
    store known to be unreachable must not let SOME notes rename while their dead-letter
    migration is known to be impossible."""
    cfg, leads, seen_db = _cfg(tmp_path, monkeypatch)
    src = _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    dl_path = deadletter_path(seen_db)
    os.makedirs(os.path.dirname(dl_path), exist_ok=True)
    with open(dl_path, "w", encoding="utf-8") as fh:
        fh.write("not a sqlite database")  # corrupt: check_reachable's DELETE raises on this

    with pytest.raises(sqlite3.DatabaseError):
        Sluice(cfg).rename(apply=True)

    # ZERO renames landed -- assert the vault itself, not just the raise.
    assert os.path.isfile(src)
    assert not os.path.exists(os.path.join(leads, "Example Co - Example Role.md"))


def test_the_deadletter_failure_remedy_names_the_old_slug_not_the_new_one(
        tmp_path, capsys, monkeypatch):
    """Task 10 review, Finding 1. `DeadLetterDb.rename_lead` is a single `UPDATE ... WHERE
    lead=?` + `commit()` -- on ANY failure the rows are STILL filed under the OLD slug, never
    the new one (see the facade's own docstring, `core/app.py`'s `rename` method). The printed
    remedy's `track dismiss --lead` argument therefore MUST be `old_slug`: naming `new_slug`
    sends the operator to a command that matches zero rows (`clear_lead` returns 0, `track
    dismiss` prints "cleared 0 entries" and exits 0), reading as "handled" while the stray rows
    silently remain.

    A test that only checked *some* slug appeared would pass even with the old, wrong
    `new_slug` wording -- so this asserts old_slug is quoted as the `--lead` ARGUMENT
    specifically, and that new_slug is NOT quoted there (new_slug legitimately still appears
    earlier in the line, in the `old -> new FAILED` phrase)."""
    cfg, leads, _seen_db = _cfg(tmp_path, monkeypatch)
    old_slug = "Unknown - Example Role"
    new_slug = "Example Co - Example Role"
    _make_deadletter_failed(leads, monkeypatch)

    assert cmd_leads_rename(_Args(apply=True), cfg) == 1
    err = capsys.readouterr().err
    assert f'--lead "{old_slug}"' in err, (
        f"remedy did not quote the OLD slug as the --lead argument: {err!r}")
    assert f'--lead "{new_slug}"' not in err, (
        f"remedy quoted the NEW slug as the --lead argument -- matches zero rows: {err!r}")
    assert "OLD slug" in err


def test_a_relocated_seen_db_refuses_apply_with_a_clear_message_not_a_traceback(
        tmp_path, capsys, monkeypatch):
    """Task 10 review, Finding 2 (plan-mandated). `Sluice.rename(apply=True)`'s preflight --
    `load_track_config(refuse_relocated_seen_db=True)` -- refuses via `RuntimeError`
    (`core/paths.py`'s `resolve(..., fatal=True)`) when `track.seen_db` has been left behind at
    its pre-#80 legacy location and nothing names it explicitly. `main()` only catches
    `ValueError` around this dispatch (`cli.py`), so without a catch in `cmd_leads_rename`
    itself this would reach the operator as a raw traceback instead of the plan's required
    "clear message" -- exactly the shape `StoreCannotRename` already gets. Same mechanism as
    `tests/test_path_refusal.py`'s `legacy` fixture, reused here at the CLI layer."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEEN_DB", raising=False)
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)  # no explicit track.seen_db to configure
    # A legacy track-seen.db sitting in the cwd -- `paths.resolve`'s legacy check is keyed on
    # this relative literal (`_LEGACY["track-seen.db"] == "./track-seen.db"`).
    (tmp_path / "track-seen.db").write_text("legacy dedup state", encoding="utf-8")

    vault_dir = tmp_path / "vault"
    cfg = Config(vault_dir=str(vault_dir))
    leads = Sluice(cfg).store().leads_dir
    os.makedirs(leads, exist_ok=True)
    _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")

    assert cmd_leads_rename(_Args(apply=True), cfg) == 2
    err = capsys.readouterr().err
    assert err.startswith("job-sluice: "), f"escaped as something other than a clean message: {err!r}"
    assert "Traceback" not in err
    assert "track-seen.db" in err
    # The preflight fires BEFORE any note is renamed -- ZERO renames landed.
    assert os.path.isfile(os.path.join(leads, "Unknown - Example Role.md"))
    assert not os.path.exists(os.path.join(leads, "Example Co - Example Role.md"))


def test_a_corrupt_deadletter_store_does_not_fail_the_report(tmp_path, monkeypatch):
    """`rename_report()` must be best-effort on the dead-letter side: a report command must not
    fail over a store it isn't writing."""
    cfg, leads, seen_db = _cfg(tmp_path, monkeypatch)
    _seed(leads, "Unknown - Example Role.md", company="Example Co", role="Example Role")
    dl_path = deadletter_path(seen_db)
    os.makedirs(os.path.dirname(dl_path), exist_ok=True)
    with open(dl_path, "w", encoding="utf-8") as fh:
        fh.write("not a sqlite database")

    rep = Sluice(cfg).rename_report()
    assert "error" in rep["deadletter"]
    # The rest of the report is still returned in full -- the corrupt dead-letter store degrades
    # only its own preview, not the vault-side report this command exists to produce.
    assert rep["renames"] == [("Unknown - Example Role", "Example Co - Example Role", ".")]
