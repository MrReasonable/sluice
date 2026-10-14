import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from scripts.triage_evidence_eval import fixtures, score
from tests.conftest import LOCATIONS, _title_pool
from tests.test_fixture_name_neutrality import (_REVIEWED_FIXTURE_IDENTITIES,
                                                _REVIEWED_SKILL_VALUES)

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "scripts" / "triage_evidence_eval" / "fixtures.json"
# A human must read the fixture diff before re-pinning: JD bodies and the profile are free
# text no roster can enumerate (the #27 lesson), so this digest is what summons the reader.
_FIXTURE_DIGEST = "b7f16fd298d31f8e2367858e2c24f0c3619c32f130160b499bf0db99b56d7025"


def _f():
    return fixtures.load(FIX)


def test_fixtures_load_enough_to_measure():
    f = _f()
    roles = [lead.role for lead in f.leads]
    assert roles.count("gap") >= 3 and "borderline" in roles and "no_support" in roles
    assert len(f.record["experience"]) >= 1 and len(f.record["skills"]) >= 1


def test_fixture_identities_are_synthetic():
    # Rosters cover only the enumerable fields: lead company, title and location, and the
    # record's Company, skill title/Label and Tools items. NOT checked against any roster:
    # the experience entry titles, their Category, Best For and Metrics fields, the skill
    # entries' Proficiency and Domain, each lead's gap_term, every JD body and the profile.
    # Those are free text no roster can enumerate, so the pinned digest in
    # test_fixture_digest_is_pinned is what makes a human read them on any change.
    f = _f()
    companies = [lead.company for lead in f.leads] + [
        e["fields"]["Company"] for e in f.record["experience"]]
    assert companies
    assert [c for c in companies if c not in _REVIEWED_FIXTURE_IDENTITIES] == []
    skills = [e["title"] for e in f.record["skills"]] + [
        e["fields"].get("Label") for e in f.record["skills"] if e["fields"].get("Label")]
    tools = [t.strip() for e in f.record["experience"]
             for t in e["fields"].get("Tools", "").split(",") if t.strip()]
    assert skills and tools, "nothing enumerated -- the roster check would certify nothing"
    assert [v for v in skills + tools if v not in _REVIEWED_SKILL_VALUES] == []
    pool = set(_title_pool())
    assert [lead.title for lead in f.leads if lead.title not in pool] == []
    assert [lead.location for lead in f.leads if lead.location not in LOCATIONS] == []


def test_no_lead_is_at_an_employer_in_the_record():
    """A lead at a company the record lists reads as a rehire, which can lift the
    with-record score for a reason unrelated to the gap and mask a penalty."""
    f = _f()
    employers = {e["fields"]["Company"].casefold() for e in f.record["experience"]}
    assert employers, "no record employer enumerated -- the check would certify nothing"
    assert [lead.id for lead in f.leads if lead.company.casefold() in employers] == []


def test_every_url_anywhere_in_the_fixtures_is_an_example_domain():
    """Swept over the RAW file, not just the `url` keys: a JD body can carry a link too."""
    raw = FIX.read_text(encoding="utf-8")
    urls = re.findall(r"https?://[^\s\"')]+", raw)
    assert urls, "no url found -- the sweep would certify nothing"
    assert {urlparse(u).hostname for u in urls} <= {"example.com", "example.org",
                                                    "example.net"}


def test_record_kinds_are_real_judge_kinds():
    """A misspelt kind (`skill`) would be dropped silently by `record.get(kind, [])`."""
    from sluice.core.protocols import EVIDENCE_KINDS
    judged = {kind for kind, spec in EVIDENCE_KINDS.items() if spec.judge_fields}
    assert judged and set(_f().record) <= judged


def test_each_gap_term_is_in_its_jd():
    assert [lead.id for lead in _f().leads
            if lead.gap_term.lower() not in lead.jd.lower()] == []


def _mentions(term, text):
    # Whole-word and case-insensitive: a bare substring would let a short record value
    # such as `data` match inside an unrelated word and fail the check for nothing.
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


def _covered_terms(record):
    """Everything the record could be read as covering: each entry's title and Label, and
    each judge-read field value, a comma-listed one (Tools) split into its items. The kinds'
    `judge_fields` are read from the registry, never hand-listed."""
    from sluice.core.protocols import EVIDENCE_KINDS
    terms = set()
    for kind, entries in record.items():
        for entry in entries:
            fields = entry["fields"]
            terms |= {entry["title"], fields.get("Label") or ""}
            for key in EVIDENCE_KINDS[kind].judge_fields:
                terms |= {item.strip() for item in str(fields.get(key) or "").split(",")}
    return {t for t in terms if t.strip()}


def _win_terms(profile):
    """The profile's win-pattern terms, read off its `Win pattern:` sentence against the
    reviewed skill roster the fixture values are drawn from."""
    found = re.search(r"Win pattern: ([^.]*)\.", profile)
    assert found, "the profile carries no `Win pattern:` sentence to read terms from"
    return {v for v in _REVIEWED_SKILL_VALUES if _mentions(v, found.group(1))}


def test_a_no_support_lead_names_a_win_term_and_nothing_the_record_covers():
    """The no-support lead exists so a gap penalty cannot be netted out by credit for a
    supported requirement (spec section 7). That holds only if the record covers NONE of
    its JD, while the profile alone still has a reason to shortlist it."""
    f = _f()
    leads = [lead for lead in f.leads if lead.role == "no_support"]
    assert leads, "no no_support lead found -- the check would certify nothing"
    covered = _covered_terms(f.record)
    assert {"Examplelang", "data services"} <= covered, covered     # scope of the sweep
    wins = _win_terms(f.profile)
    folded = {t.casefold() for t in covered}
    assert [w for w in wins if w.casefold() not in folded], (
        f"every win term {wins} is covered by the record: no lead can be no-support")
    assert [(lead.id, t) for lead in leads for t in sorted(covered)
            if _mentions(t, lead.jd)] == []
    assert [lead.id for lead in leads if not any(_mentions(w, lead.jd) for w in wins)] == []


def test_fixture_digest_is_pinned():
    raw = json.loads(FIX.read_text(encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    assert digest == _FIXTURE_DIGEST, (
        "scripts/triage_evidence_eval/fixtures.json changed. Read the diff for any real "
        "employer, place or person before re-pinning this digest.")


def _v(verdict, s, concerns=()):
    return {"verdict": verdict, "relevance_score": s, "concerns": list(concerns)}


_LEAD = fixtures.Lead(id="x", company="Example Alpha", title=_title_pool()[0],
                      location="Alfa", url="https://example.com/x", jd="needs widgets",
                      expected="shortlist", gap_term="widgets", role="gap")


def test_pass():
    base = [_v("shortlist", 86)] * 5
    rec = [_v("shortlist", 88, ["no verified evidence for widgets"])] * 5
    assert score.lead_result(base, rec, _LEAD) == "PASS"


def test_invalid_when_baseline_misses_expected():
    assert score.lead_result([_v("research", 70)] * 5, [_v("shortlist", 90)] * 5,
                             _LEAD) == "INVALID"


@pytest.mark.parametrize("bad", [None, _v("unjudgeable", 0)])
def test_invalid_when_baseline_unjudgeable_or_missing(bad):
    # Both arms: a missing run and an `unjudgeable` one each crash or mis-score if the guard goes.
    base = [_v("shortlist", 86)] * 4 + [bad]
    assert score.lead_result(base, [_v("shortlist", 86, ["widgets"])] * 5, _LEAD) == "INVALID"


def test_fail_on_a_lower_band():
    # Baseline spread 10 (median 85), with-record median 84: the score check passes
    # (84 >= 75), so ONLY the band check can fail this -- deleting it must turn this red.
    base = [_v("shortlist", s) for s in (80, 90, 85, 85, 85)]
    rec = [_v("research", 84, ["widgets"])] * 5
    assert score.lead_result(base, rec, _LEAD) == "FAIL"


def test_fail_on_a_drop_beyond_the_baseline_spread():
    base = [_v("shortlist", s) for s in (86, 87, 88, 86, 87)]      # spread 2
    rec = [_v("shortlist", 84, ["widgets"])] * 5                   # median 84 < 87 - 2
    assert score.lead_result(base, rec, _LEAD) == "FAIL"


@pytest.mark.parametrize("bad", [None, _v("unjudgeable", 0)])
def test_fail_when_with_record_is_unjudgeable_or_missing(bad):
    base = [_v("shortlist", 86)] * 5
    rec = [_v("shortlist", 86, ["widgets"])] * 4 + [bad]
    assert score.lead_result(base, rec, _LEAD) == "FAIL"


def test_fail_when_the_gap_is_not_named_by_a_majority():
    base = [_v("shortlist", 86)] * 5
    rec = [_v("shortlist", 86, ["widgets"])] * 2 + [_v("shortlist", 86)] * 3
    assert score.lead_result(base, rec, _LEAD) == "FAIL"


def test_pass_when_the_gap_is_named_by_a_bare_majority():
    # 3 of 5 is the smallest majority: the boundary the FAIL row above sits just under.
    base = [_v("shortlist", 86)] * 5
    rec = [_v("shortlist", 86, ["widgets"])] * 3 + [_v("shortlist", 86)] * 2
    assert score.lead_result(base, rec, _LEAD) == "PASS"


def _leads(*roles):
    return [fixtures.Lead(id=f"{r}{i}", company="Example Alpha", title=_title_pool()[0],
                          location="Alfa", url="https://example.com/x", jd="j",
                          expected="shortlist", gap_term="j", role=r)
            for i, r in enumerate(roles)]


def test_overall():
    ls = _leads("gap", "gap", "gap", "borderline", "no_support")
    ids = [lead.id for lead in ls]
    assert score.overall(dict.fromkeys(ids, "PASS"), ls) == "PASS"
    assert score.overall({**dict.fromkeys(ids, "PASS"), ids[0]: "FAIL"}, ls) == "FAIL"
    assert score.overall({**dict.fromkeys(ids, "PASS"), ids[3]: "INVALID"}, ls) == "INCONCLUSIVE"
    assert score.overall({**dict.fromkeys(ids, "PASS"), ids[4]: "INVALID"}, ls) == "INCONCLUSIVE"
    assert score.overall({**dict.fromkeys(ids, "INVALID"), ids[3]: "PASS",
                          ids[4]: "PASS"}, ls) == "INCONCLUSIVE"    # < half valid


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_overall_is_inconclusive_when_any_of_several_borderline_leads_is_invalid(order):
    """Both orders: a one-lead-per-role index keeps whichever borderline lead comes LAST,
    so only the order with the INVALID lead first would expose it."""
    ls = _leads("gap", "gap", "gap", "borderline", "borderline", "no_support")
    ids = [lead.id for lead in ls]
    results = dict.fromkeys(ids, "PASS")
    results[ids[3 + order[0]]] = "INVALID"
    assert score.overall(results, ls) == "INCONCLUSIVE"


def test_run_refuses_without_explicit_go_ahead(monkeypatch, capsys):
    from scripts.triage_evidence_eval import run
    from sluice.core.app import Sluice

    # A refusal that stopped firing would go on to build the configured backend and call
    # it, which on a machine with a provider set up is a real, paid call. Both are stubbed
    # to raise, so a regression here fails offline instead of spending one.
    def no_live_call(*a, **k):
        raise AssertionError("reached the backend: the missing --yes was not refused")
    monkeypatch.setattr(Sluice, "backend", no_live_call)
    monkeypatch.setattr(run, "_judge_all", no_live_call)
    with pytest.raises(SystemExit) as e:
        run.main([])
    assert e.value.code == 2 and "--yes" in capsys.readouterr().err


def test_fail_when_a_with_record_run_has_no_score():
    base = [_v("shortlist", 86)] * 5
    unscored = {"verdict": "shortlist", "concerns": ["widgets"]}
    rec = [_v("shortlist", 86, ["widgets"])] * 4 + [unscored]
    assert score.lead_result(base, rec, _LEAD) == "FAIL"


def test_invalid_when_a_baseline_score_is_not_an_int():
    base = [_v("shortlist", 86)] * 4 + [_v("shortlist", "86")]
    assert score.lead_result(base, [_v("shortlist", 86, ["widgets"])] * 5, _LEAD) == "INVALID"


def test_invalid_when_a_baseline_score_is_a_bool():
    # `bool` subclasses `int`, so `True` passes a bare isinstance check and would be scored
    # as 1: the explicit bool refusal in `score._valid` is the only thing that rejects it.
    base = [_v("shortlist", 86)] * 4 + [_v("shortlist", True)]
    assert score.lead_result(base, [_v("shortlist", 86, ["widgets"])] * 5, _LEAD) == "INVALID"


class _FakeBackend:
    """Answers with one malformed item (no `verdict`) and one good one. Offline: nothing
    here reaches a provider."""

    def __init__(self, items):
        self.items = items

    def complete(self, prompt):
        from sluice.core.backends import Completion
        return Completion(json.dumps(self.items))


def test_judge_all_drops_a_malformed_verdict_and_keeps_the_good_one():
    from scripts.triage_evidence_eval import run
    leads = _leads("gap", "borderline")
    bad, good = leads
    backend = _FakeBackend([
        {"lead_id": bad.id, "relevance_score": 50},                     # no verdict
        {"lead_id": f"  {good.id} ", "verdict": "shortlist", "relevance_score": "88"},
    ])
    got = run._judge_all(leads, "SYSTEM", backend)
    assert got[bad.id] is None
    # Normalised by the engine's gate: the padded id stripped, the string score coerced.
    assert got[good.id]["lead_id"] == good.id
    assert got[good.id]["verdict"] == "shortlist"
    assert got[good.id]["relevance_score"] == 88


@pytest.mark.parametrize("repeats", ["4", "0"])
def test_run_refuses_an_even_or_zero_repeat_count(monkeypatch, capsys, repeats):
    from scripts.triage_evidence_eval import run
    from sluice.core.app import Sluice

    # With `--yes` on the line, a guard that stopped refusing would go on to build the
    # configured backend and call it. Both are stubbed to raise, so a regression here
    # fails offline instead of spending a real call.
    def no_live_call(*a, **k):
        raise AssertionError("reached the backend: --repeats was not refused")
    monkeypatch.setattr(Sluice, "backend", no_live_call)
    monkeypatch.setattr(run, "_judge_all", no_live_call)
    with pytest.raises(SystemExit) as e:
        run.main(["--repeats", repeats, "--yes"])
    assert e.value.code == 2 and "odd" in capsys.readouterr().err


def test_out_is_written_before_scoring(monkeypatch, tmp_path):
    """The calls are the paid part: a scorer that raises must leave the verdicts on disk."""
    from scripts.triage_evidence_eval import run
    from sluice.core.app import Sluice
    monkeypatch.setattr(Sluice, "backend", lambda self, **kw: object())
    monkeypatch.setattr(run, "_judge_all",
                        lambda leads, prompt, backend: {lead.id: None for lead in leads})

    def boom(*a, **k):
        raise RuntimeError("scorer broke")
    monkeypatch.setattr(run.score, "lead_result", boom)
    out = tmp_path / "eval.json"
    with pytest.raises(RuntimeError):
        run.main(["--yes", "--repeats", "1", "--out", str(out)])
    runs = json.loads(out.read_text())["runs"]
    assert set(runs) == {"baseline", "with_record"} and len(runs["baseline"]) == 1


def test_an_outage_keeps_the_earlier_repeats_and_scores_nothing(monkeypatch, tmp_path, capsys):
    """Never reaches a real backend: `Sluice.backend` returns an inert object (`main`
    always builds one, so a stub that raised would stop the run before the loop under
    test), and `_judge_all`, the only thing that would call it, is the stub raising the
    outage on the second repeat."""
    from scripts.triage_evidence_eval import run
    from sluice.core.app import Sluice
    from sluice.core.backends import BackendError
    from sluice.triage.judge import JudgeAborted

    monkeypatch.setattr(Sluice, "backend", lambda self, **kw: object())
    calls = []

    def judge_all(leads, prompt, backend):
        calls.append(prompt)
        if len(calls) == 2:
            raise JudgeAborted([], BackendError("503 after retries"))
        return {lead.id: None for lead in leads}
    monkeypatch.setattr(run, "_judge_all", judge_all)

    def no_scoring(*a, **k):
        raise AssertionError("an aborted run must not be scored")
    monkeypatch.setattr(run.score, "lead_result", no_scoring)
    out = tmp_path / "eval.json"
    code = run.main(["--yes", "--repeats", "3", "--out", str(out)])
    assert code == 3          # the README's outage code; 1 is the eval-FAIL code
    runs = json.loads(out.read_text())["runs"]
    assert len(runs["baseline"]) == 1 and runs["with_record"] == []
    assert "outage after 1 of 6" in capsys.readouterr().err
