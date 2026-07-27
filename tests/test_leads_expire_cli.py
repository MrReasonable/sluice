"""#9 `sluice leads expire` at the CLI LAYER: dispatch, exit codes, and printed output.

This file exists because the Sluice-level tests could not see any of it. Replacing the
whole body of `cmd_leads_expire` with `return 0` left the rest of the suite green, and
three separate mutants inside it -- `is not None` -> truthiness, deleting the unset-knob
branch, and returning 0 instead of 1 on no-match -- each kept every other test passing.
A test that asserts on an argparse Namespace it built itself certifies the parser, not
the command.
"""
import json
from datetime import date

import pytest

from sluice.cli import main
from sluice.core.leads import Lead
from sluice.core.vault import Vault

TODAY = "2026-07-27"
ANCIENT = "2026-01-01"
# Derived, not hardcoded: a literal 207 would silently become a claim about the real
# calendar the moment the pinned clock stopped being applied -- which is exactly how the
# first draft of this file passed for the wrong reason.
_AGE = (date.fromisoformat(TODAY) - date.fromisoformat(ANCIENT)).days


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch):
    """Freeze the production clock. cmd_leads_expire builds its own Sluice, so there is no
    injection point above it -- but `StalenessPolicy` reads whatever `Sluice.staleness()`
    hands it, and that reads `sluice.core.app`'s fallback clock."""
    import sluice.core.app as appmod
    # `raising=True` is load-bearing: with raising=False an absent attribute is silently
    # CREATED, the real clock keeps running, and every date assertion below quietly
    # becomes a claim about the day the suite happens to run.
    monkeypatch.setattr(appmod, "_today", lambda: TODAY)


def _seed(tmp_path, *, status="shortlist", last_seen=ANCIENT, title="Example Role",
          url="https://example.invalid/1", **extra):
    v = Vault(str(tmp_path))
    v.upsert(Lead(source="s", search="q", title=title, company="Example Ltd", url=url))
    note = next(n for n in v.read_leads() if n.fm.get("url", "") == url)
    fields = {"status": status, "last_seen": last_seen, "first_seen": "2025-12-01"}
    fields.update(extra)
    v.update_fields(note.ref, fields)
    return note.slug


def _run(tmp_path, monkeypatch, *argv, ttl=90):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"lead_ttl_days: {ttl}\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfg))
    return main(["leads", "expire", *argv])


# ── the unset knob ───────────────────────────────────────────────────────────

def test_unset_knob_reports_off_and_exits_zero(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    assert _run(tmp_path, monkeypatch, ttl=0) == 0
    assert "staleness is off" in capsys.readouterr().err


def test_unset_knob_still_emits_a_json_document(tmp_path, monkeypatch, capsys):
    # A consumer parsing stdout must not have to tell "no output" from "empty result";
    # the knob-ON empty case prints `[]`, and `leads dedupe --json` always prints one.
    _seed(tmp_path)
    assert _run(tmp_path, monkeypatch, "--json", ttl=0) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_unset_knob_with_a_named_expire_exits_NON_zero(tmp_path, monkeypatch):
    # The user asked for a write and got none. Exiting 0 here is exactly the silent
    # no-op the no-match exit exists to prevent.
    slug = _seed(tmp_path)
    assert _run(tmp_path, monkeypatch, "--expire", slug, ttl=0) == 1


# ── report vs write dispatch ─────────────────────────────────────────────────

def test_bare_command_reports_and_writes_nothing(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path)
    assert _run(tmp_path, monkeypatch) == 0
    out = capsys.readouterr()
    assert slug in out.out
    assert "0 written" in out.err
    assert Vault(str(tmp_path)).read_leads()[0].status == "shortlist"


def test_bare_expire_FLAG_writes(tmp_path, monkeypatch):
    """THE dispatch test. `--expire` with no argument parses to a FALSY [], so
    `if args.expire:` (dedupe's shape) would silently fall through to the read-only
    report branch and exit 0 having written nothing."""
    _seed(tmp_path)
    assert _run(tmp_path, monkeypatch, "--expire") == 0
    assert Vault(str(tmp_path)).read_leads()[0].status == "dismiss"


def test_named_expire_writes_only_the_named_lead(tmp_path, monkeypatch):
    a = _seed(tmp_path, title="Example Role", url="https://example.invalid/1")
    b = _seed(tmp_path, title="Example Role Two", url="https://example.invalid/2")
    assert _run(tmp_path, monkeypatch, "--expire", a) == 0
    by = {n.slug: n.status for n in Vault(str(tmp_path)).read_leads()}
    assert by[a] == "dismiss"
    assert by[b] == "shortlist"


def test_unmatched_named_slug_exits_NON_zero(tmp_path, monkeypatch):
    _seed(tmp_path)
    assert _run(tmp_path, monkeypatch, "--expire", "Nothing - Like This") == 1


def test_a_sign_off_hold_is_refused_and_the_message_names_the_way_out(
        tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, pending_cv="CV-2026.pdf")
    rc = _run(tmp_path, monkeypatch, "--expire")
    err = capsys.readouterr().err
    assert Vault(str(tmp_path)).read_leads()[0].status == "shortlist"
    assert "cv signoff" in err and "--discard" in err, \
        "a permanent refusal must print its remedy, not just the refusal"
    assert rc == 0   # a bulk sweep that refused a held lead is not itself a failure
    assert slug


# ── the json report ──────────────────────────────────────────────────────────

def test_json_report_shape(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, tailored_cv="CV-2026.pdf")
    assert _run(tmp_path, monkeypatch, "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0] == {
        "slug": slug, "status": "shortlist", "last_seen": ANCIENT,
        "first_seen": "2025-12-01", "days": _AGE, "flagged": ["cv"], "refused": None,
    }


def test_json_report_marks_a_refused_lead(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, pending_cv="CV-2026.pdf")
    _run(tmp_path, monkeypatch, "--json")
    assert json.loads(capsys.readouterr().out)[0]["refused"] == "sign-off-hold"
