"""`leads followups`, `leads mark-asked`, `leads answer` at the CLI layer (#378)."""
import json
import pathlib

import pytest

from sluice.cli import main
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.vault import Vault


@pytest.fixture(autouse=True)
def _role(titles):
    # Titles come from the seeded faker pool, never typed by hand (neu-002).
    global _TITLES
    _TITLES = [t.title() for t in titles[0]]


def _seed(tmp_path, *, status="research", title=None,
          url="https://example.invalid/1", **extra):
    v = Vault(str(tmp_path))
    v.upsert(Lead(source="s", search="q", title=title or _TITLES[0], company="Example Ltd",
                  url=url))
    note = next(n for n in v.read_leads() if n.fm.get("url", "") == url)
    v.update_fields(note.ref, {"status": status, **extra})
    return note.slug


def _main(tmp_path, monkeypatch, *argv):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    return main(["leads", *argv])


def test_mark_asked_then_followups_groups_it(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, triage_next='"ask recruiter: client name"')
    _seed(tmp_path, url="https://example.invalid/2", title=_TITLES[1])
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead", slug) == 0
    assert slug in capsys.readouterr().err
    assert _main(tmp_path, monkeypatch, "followups") == 0
    out = capsys.readouterr().out
    assert "Never asked (1):" in out and "Asked, waiting (1):" in out
    assert "ask recruiter: client name" in out


def test_a_slug_with_spaces_and_punctuation_resolves_exactly(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, title=f"{_TITLES[0]}, ({_TITLES[1]})")
    assert " " in slug
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead", slug) == 0
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead", slug[:-3]) == 1


def test_followups_json_shape(tmp_path, monkeypatch, capsys):
    # tst-002: the counts, not only the keys -- `asked` reporting the row total stayed green
    # against a keys-only check.
    slug = _seed(tmp_path, first_seen="2026-10-01")
    _seed(tmp_path, url="https://example.invalid/2", title=_TITLES[1], first_seen="2026-10-01",
          followup_asked="2026-10-05")
    assert _main(tmp_path, monkeypatch, "followups", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"total", "asked", "oldest_days", "rows"}
    assert data["total"] == 2 and data["asked"] == 1
    assert slug in {r["slug"] for r in data["rows"]}
    assert set(data["rows"][0]) == {"slug", "company", "role", "age_days", "next_action",
                                    "asked", "waited_days"}


def test_followups_on_an_empty_queue_says_so(tmp_path, monkeypatch, capsys):
    assert _main(tmp_path, monkeypatch, "followups") == 0
    out = capsys.readouterr().out
    assert "No research leads" in out and "Never asked" not in out


def test_followups_hints_at_expiry_only_when_ttl_is_off(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    _main(tmp_path, monkeypatch, "followups")
    assert "lead_ttl_days" in capsys.readouterr().out
    # The converse arm (tst-004). Witness: delete `if not config.lead_ttl_days`.
    from sluice.cli import cmd_leads_followups, _build_parser
    cfg = Config(); cfg.lead_ttl_days = 30
    cmd_leads_followups(_build_parser().parse_args(["leads", "followups"]), cfg)
    assert "lead_ttl_days" not in capsys.readouterr().out


def test_a_hand_typed_asked_mark_prints_a_question_mark(tmp_path, monkeypatch, capsys):
    # tst-005, Review Focus 1 at the CLI: a non-date stamp still counts as asked, and its
    # wait prints `?` -- never `None`. Witness: format `r.waited_days` directly.
    _seed(tmp_path, followup_asked="yes")
    _main(tmp_path, monkeypatch, "followups")
    assert "asked ?d ago" in capsys.readouterr().out


def test_an_unparseable_age_prints_a_question_mark(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, first_seen="sometime")
    _main(tmp_path, monkeypatch, "followups")
    assert f"- {slug} (?d)" in capsys.readouterr().out


def test_answer_clears_and_exits_zero(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path)
    _main(tmp_path, monkeypatch, "mark-asked", "--lead", slug)
    assert _main(tmp_path, monkeypatch, "answer", "--lead", slug, "--text", "client named") == 0
    assert Vault(str(tmp_path)).read_leads()[0].fm["followup_asked"] == ""


def test_answer_blank_text_is_a_usage_error(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path)
    assert _main(tmp_path, monkeypatch, "answer", "--lead", slug, "--text", "  ") == 2


def test_refusals_exit_one_and_name_the_status(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path, status="shortlist")
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead", slug) == 1
    assert "status=shortlist" in capsys.readouterr().err
    assert _main(tmp_path, monkeypatch, "answer", "--lead", slug, "--text", "x") == 1
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead", "nothing") == 1


def test_ambiguous_exits_one(tmp_path, monkeypatch, capsys):
    leads = pathlib.Path(tmp_path) / "Job Applications" / "Job Leads"
    for sub in ("Active", "Archive"):
        (leads / sub).mkdir(parents=True)
        (leads / sub / f"Example Ltd - {_TITLES[0]}.md").write_text(
            '---\ncompany: "Example Ltd"\nstatus: research\n---\n\nB\n')
    assert _main(tmp_path, monkeypatch, "mark-asked", "--lead",
                 f"Example Ltd - {_TITLES[0]}") == 1
    assert "ambiguous" in capsys.readouterr().err


def test_an_answer_the_judge_cannot_see_exits_one_and_says_why(tmp_path, monkeypatch, capsys):
    slug = _seed(tmp_path)
    ref = Vault(str(tmp_path)).read_leads()[0].ref
    pathlib.Path(ref).write_text(pathlib.Path(ref).read_text(encoding="utf-8")
                                 + "\n```\nunclosed\n")
    assert _main(tmp_path, monkeypatch, "answer", "--lead", slug, "--text", "x") == 1
    assert "code fence" in capsys.readouterr().err


@pytest.mark.parametrize("cmd, method, result, phrase", [
    ("mark-asked", "mark_asked",
     dict(outcome="refused_unwritable", slug="S", status="research"), "edit it in the note"),
    ("mark-asked", "mark_asked", dict(outcome="conflict", slug="S"), "conflict"),
    ("answer", "record_answer",
     dict(outcome="answered_not_cleared", slug="S", status="applied", appended=True),
     "moved to status=applied"),
    ("answer", "record_answer",
     dict(outcome="refused_unwritable", slug="S", status="research", appended=True),
     "could not be cleared safely"),
    ("answer", "record_answer",
     dict(outcome="answered_hidden", slug="S", status="research", appended=True), "code fence"),
    ("answer", "record_answer", dict(outcome="conflict", slug="S", appended=True),
     "the answer WAS recorded"),
])
def test_every_failure_outcome_exits_one_and_says_why(tmp_path, monkeypatch, capsys, cmd,
                                                       method, result, phrase):
    # tst-001: the facade's outcomes are pinned in tests/test_followups_facade.py; this pins the
    # step that turns each into an exit code, which is what a script or cron reads. Exit 0 on
    # any of these would report a write that did not land, or a lead still skipped, as success.
    from sluice.core.app import AnswerResult, AskResult, Sluice
    cls = AskResult if method == "mark_asked" else AnswerResult
    monkeypatch.setattr(Sluice, method, lambda self, **kw: cls(**result))
    argv = ["--lead", "S"] + (["--text", "x"] if cmd == "answer" else [])
    assert _main(tmp_path, monkeypatch, cmd, *argv) == 1
    assert phrase in capsys.readouterr().err
