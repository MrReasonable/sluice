"""#378 in the triage engine: an awaiting lead is skipped and counted; answers reach the judge
on that lead's own dossier copy, never the shared cache file."""
import json
import os
from datetime import datetime

from sluice.core import followups
from sluice.core.dossier import DossierCache
from sluice.core.vault import Vault
from sluice.triage.audit import AuditLog
from sluice.triage.config import TriageConfig
import sluice.triage.engine as eng
from tests.test_triage_engine import _Backend, _fields

TODAY = "2031-03-14"


def _cache(tmp_path):
    return DossierCache(str(tmp_path / "dos"), ttl_days=7,
                        fetcher=lambda lead: {"jd": {"markdown": "an example posting"}},
                        clock=lambda: datetime(2031, 3, 14))


def _write(v, name, fm_lines, body="# Lead\n"):
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(fm_lines) + "\n---\n" + body)


def _run(tmp_path, titles, notes, *, statuses=("research",), limit=0, vault=None):
    accept, _ = titles
    v = vault or Vault(str(tmp_path / "vault"))
    for name, company, url, status, extra, body in notes:
        _write(v, name, _fields(company, accept[0].title(), status, url=url) + list(extra), body)
    cfg = TriageConfig(); cfg.accept_titles = list(accept)
    backend = _Backend()
    report = eng.run(v, cfg, backend, _cache(tmp_path), AuditLog(str(tmp_path / "a.jsonl")),
                     statuses=statuses, limit=limit)
    return report, v, "\n".join(backend.prompts)


def test_an_awaiting_lead_is_not_judged_and_is_counted(tmp_path, titles):
    report, v, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "research",
         [f"followup_asked: {TODAY}"], "# Lead\n"),
        ("b.md", "Example Beta", "https://example.invalid/b", "research", [], "# Lead\n")])
    assert report.counts["awaiting_reply"] == 1
    assert "Example Alpha" not in prompts and "Example Beta" in prompts


def test_limit_is_not_spent_on_an_awaiting_lead(tmp_path, titles):
    # tst-003: the filter runs BEFORE `--limit`. Witness: move it below the `if limit:` slice.
    # The awaiting lead is named first so it sorts first in the read (`a.md` < `b.md`).
    _, _, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "research",
         [f"followup_asked: {TODAY}"], "# Lead\n"),
        ("b.md", "Example Beta", "https://example.invalid/b", "research", [], "# Lead\n")],
        limit=1)
    assert "Example Beta" in prompts


def test_stamp_then_hand_move_to_new_then_research_again_is_judged_next_run(tmp_path, titles):
    # rev-005, the spec's sequence: a stamped lead moved to `new` by hand is judged (the stamp
    # does not hide it), the verdict clears the stale stamp, so when that verdict is `research`
    # the NEXT run judges it again rather than skipping it as awaiting.
    report, v, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "new",
         ["followup_asked: 2031-02-01"], "# Lead\n")], statuses=("new",))
    assert "Example Alpha" in prompts
    note = v.read_leads()[0]
    assert note.fm["followup_asked"] == ""                   # the verdict cleared it
    v.update_fields(note.ref, {"status": "research"})       # stand-in for a research verdict
    report2, _, prompts2 = _run(tmp_path, titles, [], vault=v)
    assert report2.counts["awaiting_reply"] == 0 and "Example Alpha" in prompts2


def test_a_stale_stamp_on_a_new_lead_does_not_hide_it(tmp_path, titles):
    report, v, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "new",
         ["followup_asked: 2026-09-01"], "# Lead\n")], statuses=("new",))
    assert report.counts["awaiting_reply"] == 0 and "Example Alpha" in prompts
    assert v.read_leads()[0].fm["followup_asked"] == ""        # cleared by the verdict


def test_answers_reach_the_judge_for_a_fetched_lead(tmp_path, titles):
    body = "# Lead\n\n" + followups.render_answer("SENTINEL-ANSWER-1 client named", TODAY) + "\n"
    _, _, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "research", [], body)])
    assert "SENTINEL-ANSWER-1" in prompts and "followup_answers" in prompts


def test_answers_reach_the_judge_for_a_section_sourced_lead(tmp_path, titles):
    body = ("# Lead\n\n## Job description\nan example posting from the note\n\n"
            + followups.render_answer("SENTINEL-ANSWER-2", TODAY) + "\n")
    _, _, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "", "research", [], body)])
    assert "SENTINEL-ANSWER-2" in prompts


def test_followup_answers_never_reach_the_dossier_cache_file(tmp_path, titles):
    # Witness: move the answers insertion ahead of get_or_build (into the cached dict).
    body = "# Lead\n\n" + followups.render_answer("SENTINEL-ANSWER-3", TODAY) + "\n"
    _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/a", "research", [], body)])
    cached = [open(os.path.join(tmp_path / "dos", f), encoding="utf-8").read()
              for f in os.listdir(tmp_path / "dos")]
    assert cached and not any("SENTINEL-ANSWER-3" in c or "followup_answers" in c
                              for c in cached)


def test_two_leads_sharing_a_url_only_the_answered_one_carries_answers(tmp_path, titles):
    body = "# Lead\n\n" + followups.render_answer("SENTINEL-ANSWER-4", TODAY) + "\n"
    _, _, prompts = _run(tmp_path, titles, [
        ("a.md", "Example Alpha", "https://example.invalid/same", "research", [], body),
        ("b.md", "Example Beta", "https://example.invalid/same", "research", [], "# Lead\n")])
    dossiers = [json.loads(block) for block in
                (chunk.split("```json\n", 1)[1].split("\n```", 1)[0]
                 for chunk in prompts.split("## Dossier ")[1:])]
    by_company = {d["company"]: d for d in dossiers}
    assert "SENTINEL-ANSWER-4" in by_company["Example Alpha"].get("followup_answers", "")
    assert "followup_answers" not in by_company["Example Beta"]


def test_the_prompt_explains_followup_answers():
    from sluice.triage.prompt import SYSTEM_PROMPT
    assert "followup_answers" in SYSTEM_PROMPT


def test_slim_caps_followup_answers_keeping_the_newest():
    # Final review (minor, folded): answers ride into every judge prompt, so they get the same
    # bound as the JD. The NEWEST answers are kept (they come last in body order) and the cut is
    # marked, so the judge knows earlier replies existed. Witness: drop the cap from slim().
    from sluice.core.dossier import slim
    text = "OLD-SENTINEL " + "x" * 5000 + " NEW-SENTINEL"
    out = slim({"jd": {"markdown": "p"}, "followup_answers": text}, jd_limit=4000)
    assert out["followup_answers"].endswith("NEW-SENTINEL")
    assert "OLD-SENTINEL" not in out["followup_answers"]
    assert out["followup_answers"].startswith("[earlier answers truncated]")
    assert len(out["followup_answers"]) <= 4000 + len("[earlier answers truncated]\n")
    short = slim({"jd": {}, "followup_answers": "short"})
    assert short["followup_answers"] == "short"
