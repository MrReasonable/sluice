"""#402: giving up on a lead that keeps coming back `unjudgeable`.

The counter (`triage_unjudgeable_count`) rides triage's own status write in
`triage/apply.py`, the engine drops the cached page on a landed judge `unjudgeable` so every
count is a fresh fetch, and a lead at or above `triage.unjudgeable_give_up_after` is withheld
from the fetch/judge path after the free classify pass. Driven through the real `Vault`, so a
row sees what `_fm_dict` hands production rather than a hand-built dict."""
import os
import pathlib

import pytest

import sluice.triage.apply as apply_mod
from sluice.core.vault import Vault
from sluice.triage.apply import apply_classification, apply_verdict, unjudgeable_count

TODAY = "2031-03-14"
KEY = "triage_unjudgeable_count"
UNJUDGEABLE = {"verdict": "unjudgeable", "relevance_score": 0,
               "fit_reasoning": "the page is a sign-in wall, not a posting"}
SHORTLIST = {"verdict": "shortlist", "relevance_score": 90,
             "fit_reasoning": "a strong match"}


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    """Fix triage's clock to a date the host cannot produce (as test_triage_blocked does)."""
    class _Date(apply_mod.date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(TODAY)
    monkeypatch.setattr(apply_mod, "date", _Date)


def _note(v, name, status, extra=()):
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(['company: "Example Ltd"', f"status: {status}",
                                     "score: 60", 'relevance_notes: ""', *extra])
                + "\n---\n# body\n")
    return _reread(v, name)


def _reread(v, name):
    return next(n for n in v.read_leads() if os.path.basename(str(n.ref)) == name)


def _text(note):
    return pathlib.Path(note.ref).read_text(encoding="utf-8")


def _key_lines(note):
    """The counter key's own line plus any indented lines under it, verbatim."""
    lines = _text(note).splitlines()
    out = []
    for i, ln in enumerate(lines):
        if ln.startswith(f"{KEY}:"):
            out.append(ln)
            out += [x for x in lines[i + 1:] if x.startswith((" ", "\t"))][:10]
            break
    return out


# ── the parser, through a real note ─────────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    (None, 0),
    (f'{KEY}: ""', 0),
    (f"{KEY}:", 0),
    (f"{KEY}: 0", 0),
    (f"{KEY}: 3", 3),
    (f'{KEY}: "3"', 3),          # _fm_dict strips the quotes before the parser sees them
    (f"{KEY}: '3'", 3),
    (f"{KEY}:\n  - one\n  - two", 0),   # a block LIST reads blank on the key's own line
    (f"{KEY}: five", None),
    (f"{KEY}: -1", None),
    (f"{KEY}: 3.0", None),
    (f"{KEY}: true", None),
    (f"{KEY}: |\n  3", None),     # a block SCALAR reads as its indicator
])
def test_unjudgeable_count_reads_a_real_note(tmp_path, line, expected):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "unjudgeable", [line] if line else [])
    assert unjudgeable_count(note.fm) == expected


# ── counting ─────────────────────────────────────────────────────────────────────────────

def test_counting_is_off_unless_the_caller_asks(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new")
    assert apply_verdict(v, note, UNJUDGEABLE, {}) == "applied"
    assert KEY not in _text(note)
    note = _note(v, "B.md", "new")
    assert apply_classification(v, note, "unjudgeable", "nothing arrived") == "applied"
    assert KEY not in _text(note)


@pytest.mark.parametrize("producer", ["judge", "pre_gate"])
def test_an_unjudgeable_result_counts_from_new_and_from_unjudgeable(tmp_path, producer):
    v = Vault(str(tmp_path))

    def once(note):
        if producer == "judge":
            return apply_verdict(v, note, UNJUDGEABLE, {}, count_unjudgeable=True)
        return apply_classification(v, note, "unjudgeable", "nothing arrived",
                                    count_unjudgeable=True)
    assert once(_note(v, "A.md", "new")) == "applied"
    note = _reread(v, "A.md")
    assert note.status == "unjudgeable" and unjudgeable_count(note.fm) == 1
    assert once(note) == "applied"
    assert unjudgeable_count(_reread(v, "A.md").fm) == 2


def test_a_research_lead_is_stamped_and_not_counted(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "research")
    assert apply_verdict(v, note, UNJUDGEABLE, {}, count_unjudgeable=True) == "blocked"
    assert KEY not in _text(note)
    note = _note(v, "B.md", "research")
    assert apply_classification(v, note, "unjudgeable", "nothing arrived",
                                count_unjudgeable=True) == "blocked"
    assert KEY not in _text(note)


def test_a_lead_moved_out_mid_judge_is_not_counted(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "unjudgeable", [f"{KEY}: 1"])
    v.update_fields(note.ref, {"status": "shortlist"})     # a human moves it mid-judge
    assert apply_verdict(v, note, UNJUDGEABLE, {}, count_unjudgeable=True) == "unchanged"
    assert unjudgeable_count(_reread(v, "A.md").fm) == 1


def test_a_count_reset_mid_judge_is_not_restored(tmp_path):
    # The race the counting CAS exists for: the documented reset is deleting/blanking the key,
    # and a stale k+1 landing after it would undo the reset. Two racing COUNTS cannot witness
    # it -- both would write the same k+1. Only the counter loses: the verdict still lands.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new", [f"{KEY}: 2"])               # snapshot: 2
    v.update_fields(note.ref, {KEY: '""'})                       # the human resets it
    assert apply_verdict(v, note, UNJUDGEABLE, {}, count_unjudgeable=True) == "applied"
    after = _reread(v, "A.md")
    assert after.status == "unjudgeable" and unjudgeable_count(after.fm) == 0


def test_a_count_reset_mid_classify_is_not_restored(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new", [f"{KEY}: 2"])
    v.update_fields(note.ref, {KEY: '""'})
    assert apply_classification(v, note, "unjudgeable", "nothing arrived",
                                count_unjudgeable=True) == "applied"
    after = _reread(v, "A.md")
    assert after.status == "unjudgeable" and unjudgeable_count(after.fm) == 0


def test_a_block_list_counter_is_kept_and_the_status_lands(tmp_path):
    # A block list reads blank in the snapshot but multi-line fresh, so the counter's CAS
    # refuses. Only the counter lost: the retry without it lands the status, and the list is
    # byte-identical. Without the retry this lead would be re-judged, paid for and refused on
    # every run with nothing reported.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new", [f"{KEY}:", "  - mine", "  - also mine"])
    before = _key_lines(note)
    assert apply_verdict(v, note, UNJUDGEABLE, {}, count_unjudgeable=True) == "applied"
    after = _reread(v, "A.md")
    assert after.status == "unjudgeable"
    assert _key_lines(after) == before


def test_a_count_typed_into_an_absent_key_mid_judge_stands(tmp_path):
    # The snapshot has no key; a human types a count while the lead is being judged. A stale
    # increment would write 1 over it, so the counting write carries its CAS on a blank
    # snapshot too, and the retry lands the verdict without touching the human's value.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new")
    v.update_fields(note.ref, {KEY: "5"})
    assert apply_verdict(v, note, UNJUDGEABLE, {}, count_unjudgeable=True) == "applied"
    after = _reread(v, "A.md")
    assert after.status == "unjudgeable" and unjudgeable_count(after.fm) == 5


def test_a_retry_does_not_bypass_the_other_guards(tmp_path):
    # The retry drops ONLY the counter. A lead that left new/unjudgeable mid-judge still
    # refuses the retried write, so nothing lands and the human's values stand.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new", [f"{KEY}: 1"])
    v.update_fields(note.ref, {KEY: "4", "status": "shortlist"})
    assert apply_verdict(v, note, UNJUDGEABLE, {}, count_unjudgeable=True) == "unchanged"
    after = _reread(v, "A.md")
    assert after.status == "shortlist" and unjudgeable_count(after.fm) == 4


@pytest.mark.parametrize("value", ["five", "-1", "3.0", "true"])
def test_an_unreadable_counter_is_left_and_the_status_lands(tmp_path, value):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new", [f"{KEY}: {value}"])
    assert apply_verdict(v, note, UNJUDGEABLE, {}, count_unjudgeable=True) == "applied"
    after = _reread(v, "A.md")
    assert after.status == "unjudgeable"
    assert _key_lines(after) == [f"{KEY}: {value}"]


def test_a_hand_typed_triage_next_block_survives_a_counting_verdict(tmp_path):
    # Guards the _HAND_EDITABLE_KEYS merge: a counter with its own preserve set would REPLACE
    # the framing keys' #329 protection on the very write that carries triage_next.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new", ["triage_next:", "  - call the recruiter", "  - ask why"])
    assert apply_verdict(v, note, UNJUDGEABLE, {}, count_unjudgeable=True) == "applied"
    text = _text(_reread(v, "A.md"))
    assert "triage_next:\n  - call the recruiter\n  - ask why\n" in text


# ── clearing ─────────────────────────────────────────────────────────────────────────────

def test_a_real_verdict_clears_a_count(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "unjudgeable", [f"{KEY}: 3"])
    assert apply_verdict(v, note, SHORTLIST, {}) == "applied"
    assert unjudgeable_count(_reread(v, "A.md").fm) == 0


def test_a_reject_classification_clears_a_count(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "unjudgeable", [f"{KEY}: 3"])
    assert apply_classification(v, note, "reject", "company rule") == "applied"
    after = _reread(v, "A.md")
    assert after.status == "dismiss" and unjudgeable_count(after.fm) == 0


def test_a_real_verdict_adds_no_key_to_an_uncounted_lead(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "new")
    assert apply_verdict(v, note, SHORTLIST, {}) == "applied"
    assert KEY not in _text(note)
    note = _note(v, "B.md", "new")
    assert apply_classification(v, note, "reject", "company rule") == "applied"
    assert KEY not in _text(note)


def test_the_counter_and_triage_blocked_clear_together(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "unjudgeable", [f"{KEY}: 2", "triage_blocked: 2031-03-01"])
    assert apply_verdict(v, note, SHORTLIST, {}) == "applied"
    after = _reread(v, "A.md")
    assert unjudgeable_count(after.fm) == 0
    assert (after.fm.get("triage_blocked") or "") == ""


@pytest.mark.parametrize("value", ["five", "-1"])
def test_an_unreadable_counter_survives_a_real_verdict(tmp_path, value):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "unjudgeable", [f"{KEY}: {value}"])
    assert apply_verdict(v, note, SHORTLIST, {}) == "applied"
    assert _key_lines(_reread(v, "A.md")) == [f"{KEY}: {value}"]


def test_a_count_edited_mid_judge_survives_the_clearing_verdict(tmp_path):
    # The clear keeps a CAS, matching triage_blocked's: a value the human typed while the
    # lead was being judged stands, and the verdict lands without the clear.
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "unjudgeable", [f"{KEY}: 3"])
    v.update_fields(note.ref, {KEY: "5"})
    assert apply_verdict(v, note, SHORTLIST, {}) == "applied"
    after = _reread(v, "A.md")
    assert unjudgeable_count(after.fm) == 5 and after.status == "shortlist"


def test_a_count_edited_mid_classify_survives_the_clearing_classification(tmp_path):
    v = Vault(str(tmp_path))
    note = _note(v, "A.md", "unjudgeable", [f"{KEY}: 3"])
    v.update_fields(note.ref, {KEY: "5"})
    assert apply_classification(v, note, "reject", "company rule") == "applied"
    after = _reread(v, "A.md")
    assert unjudgeable_count(after.fm) == 5 and after.status == "dismiss"


# ── the engine: counting, giving up, evicting ───────────────────────────────────────────
# Two-lead harness: a recording fetcher over a COLD cache (so "not fetched" means something),
# a judge that answers per slug and records every prompt (so "not judged" means something),
# and filenames chosen so `read_leads`' path order is the order a row needs.
import json  # noqa: E402
import re  # noqa: E402
from datetime import datetime  # noqa: E402

import sluice.triage.engine as eng  # noqa: E402
from sluice.core.backends import Completion  # noqa: E402
from sluice.core.dossier import DossierCache  # noqa: E402
from sluice.core.jdsection import HEADING  # noqa: E402
from sluice.triage.audit import AuditLog  # noqa: E402
from sluice.triage.config import TriageConfig  # noqa: E402
from tests.test_triage_engine import _DOSSIER_ID, _fields  # noqa: E402

CHROME = "please sign in to continue"          # passes jd_arrived at the shipped floor of 0


class _Judge:
    """Answers `verdicts[slug]` (default `unjudgeable`) for every lead it is handed."""
    last_backend = "primary"

    def __init__(self, verdicts=None):
        self.verdicts = verdicts or {}
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        out = []
        for i in re.findall(_DOSSIER_ID, prompt):
            v = self.verdicts.get(i, "unjudgeable")
            out.append({"lead_id": i, "verdict": v,
                        "relevance_score": 90 if v == "shortlist" else 0,
                        "fit_reasoning": "a sign-in wall" if v == "unjudgeable" else "fits"})
        return Completion(json.dumps(out))

    def saw(self, slug):
        return any(slug in p for p in self.prompts)


class _Rig:
    def __init__(self, tmp_path, titles, jd=CHROME, jd_by_url=None):
        self.tmp = tmp_path
        self.accept = titles[0]
        self.v = Vault(str(tmp_path / "vault"))
        self.leads = os.path.join(self.v.dir, "Job Applications", "Job Leads")
        os.makedirs(self.leads, exist_ok=True)
        self.calls = []
        self.jd, self.jd_by_url = jd, jd_by_url or {}

        def fetcher(lead):
            self.calls.append(lead["url"])
            return {"jd": {"markdown": self.jd_by_url.get(lead["url"], self.jd)}}
        self.cache = DossierCache(str(tmp_path / "dos"), ttl_days=7, fetcher=fetcher,
                                  clock=lambda: datetime(2031, 3, 14))

    def lead(self, slug, status, count=None, company="Example Ltd", body="# Lead\n",
             url=None):
        extra = [f"{KEY}: {count}"] if count is not None else []
        url = url or f"https://example.invalid/{slug}"
        with open(os.path.join(self.leads, f"{slug}.md"), "w", encoding="utf-8") as f:
            f.write("---\n" + "\n".join(_fields(company, self.accept[0].title(), status,
                                                url=url) + extra)
                    + "\n---\n" + body)
        return url

    def run(self, n, judge=None, **kw):
        cfg = TriageConfig()
        cfg.accept_titles = list(self.accept)
        cfg.unjudgeable_give_up_after = n
        for k, val in kw.pop("cfg", {}).items():
            setattr(cfg, k, val)
        judge = judge or _Judge()
        kw.setdefault("statuses", ("new", "unjudgeable"))
        report = eng.run(self.v, cfg, judge, self.cache,
                         AuditLog(str(self.tmp / "audit.jsonl")), **kw)
        return report, judge

    def note(self, slug):
        return next(n for n in self.v.read_leads() if n.slug == slug)

    def count(self, slug):
        return unjudgeable_count(self.note(slug).fm)


def test_both_producers_count_through_the_engine(tmp_path, titles):
    rig = _Rig(tmp_path, titles, jd_by_url={"https://example.invalid/a-empty": ""})
    rig.lead("a-empty", "new")          # pre-judge gate: nothing arrived
    rig.lead("b-chrome", "new")         # judge: page chrome, judged unjudgeable
    report, _ = rig.run(3)
    assert report.unjudgeable_by == {"pre_gate": 1, "judge": 1}
    assert rig.count("a-empty") == 1 and rig.count("b-chrome") == 1
    rig.run(3)
    assert rig.count("a-empty") == 2 and rig.count("b-chrome") == 2


def test_a_given_up_lead_is_neither_fetched_nor_judged(tmp_path, titles):
    rig = _Rig(tmp_path, titles)
    gone = rig.lead("a-gone", "unjudgeable", count=2)
    ctrl = rig.lead("b-control", "unjudgeable", count=1)
    report, judge = rig.run(2)
    assert gone not in rig.calls and not judge.saw("a-gone")
    assert ctrl in rig.calls and judge.saw("b-control")
    assert report.counts["given_up"] == 1
    assert report.counts["keep"] == 1          # the control only: a given-up lead is not a keep
    assert rig.count("a-gone") == 2            # untouched
    assert rig.count("b-control") == 2         # counted, and now gives up next run


@pytest.mark.parametrize("case", ["new_status", "pasted_jd", "below_threshold", "setting_off"])
def test_each_condition_of_giving_up_on_its_own(tmp_path, titles, case):
    rig = _Rig(tmp_path, titles)
    status, count, body, n = "unjudgeable", 2, "# Lead\n", 2
    if case == "new_status":
        status = "new"
    elif case == "pasted_jd":
        body = f"# Lead\n\n{HEADING}\n\nA pasted posting for an example role.\n"
    elif case == "below_threshold":
        count = 1
    else:
        n = 0
    rig.lead("a-lead", status, count=count, body=body)
    report, judge = rig.run(n)
    assert judge.saw("a-lead")
    assert report.counts["given_up"] == 0


def test_a_section_that_cannot_be_read_is_not_given_up(tmp_path, titles, monkeypatch):
    rig = _Rig(tmp_path, titles)
    rig.lead("a-lead", "unjudgeable", count=5)

    def boom(body):
        raise ValueError("unreadable section")
    monkeypatch.setattr(eng.jdsection, "read", boom)
    report, _ = rig.run(2)
    assert report.counts["given_up"] == 0
    # It went on to the prefetch, whose own handling reports the unreadable section.
    assert any("section" in f and "unreadable section" in f for f in report.failures)


def test_giving_up_applies_under_dry_run_and_an_explicit_status(tmp_path, titles):
    rig = _Rig(tmp_path, titles)
    url = rig.lead("a-gone", "unjudgeable", count=2)
    report, judge = rig.run(2, dry_run=True)
    assert report.counts["given_up"] == 1 and url not in rig.calls and not judge.saw("a-gone")
    report, judge = rig.run(2, statuses=("unjudgeable",))
    assert report.counts["given_up"] == 1 and url not in rig.calls


def test_given_up_leads_do_not_use_up_the_limit(tmp_path, titles):
    rig = _Rig(tmp_path, titles)
    rig.lead("a-gone", "unjudgeable", count=2)          # sorts first
    rig.lead("b-gone", "unjudgeable", count=4)
    c = rig.lead("c-live", "new")
    d = rig.lead("d-live", "new")
    report, judge = rig.run(2, limit=1)
    assert rig.calls == [c] and d not in rig.calls
    assert judge.saw("c-live") and not judge.saw("d-live")
    assert report.counts["given_up"] == 2               # both were still classified


def test_a_given_up_lead_still_meets_a_reject_rule(tmp_path, titles):
    rig = _Rig(tmp_path, titles)
    url = rig.lead("a-gone", "unjudgeable", count=5, company="Example Ltd")
    report, _ = rig.run(2, cfg={"reject_companies": ["example ltd"]})
    note = rig.note("a-gone")
    assert note.status == "dismiss" and unjudgeable_count(note.fm) == 0
    assert report.counts["given_up"] == 0 and url not in rig.calls


def test_a_result_on_the_notes_own_section_counts_nothing(tmp_path, titles):
    # A judge `unjudgeable` on a dossier built from the note's own section: no fetch happened.
    rig = _Rig(tmp_path, titles)
    rig.lead("a-chrome-section", "new",
             body=f"# Lead\n\n{HEADING}\n\nA pasted page that is not really a posting.\n")
    rig.run(3)
    assert rig.note("a-chrome-section").status == "unjudgeable"
    assert rig.count("a-chrome-section") == 0


def test_a_short_pasted_section_counts_nothing(tmp_path, titles, monkeypatch):
    # The pre-gate's short-section arm: a section under min_jd_chars is `unjudgeable` without
    # any fetch, so it must not count towards a per-fetch threshold.
    rig = _Rig(tmp_path, titles)
    rig.cache.min_jd_chars = 500
    rig.lead("a-short", "new", body=f"# Lead\n\n{HEADING}\n\nToo short.\n")
    report, judge = rig.run(3)
    assert rig.note("a-short").status == "unjudgeable"
    assert report.unjudgeable_by["pre_gate"] == 1
    assert rig.count("a-short") == 0 and not judge.saw("a-short")


def test_a_judge_unjudgeable_drops_the_cached_page_so_every_count_is_a_fetch(tmp_path, titles):
    rig = _Rig(tmp_path, titles)
    bad = rig.lead("a-chrome", "new")
    good = rig.lead("b-good", "new")
    judge = _Judge({"b-good": "shortlist"})
    rig.run(3, judge=judge)
    rig.run(3, judge=judge, statuses=("new", "unjudgeable", "shortlist"))
    assert rig.calls.count(bad) == 2           # evicted, so fetched again
    assert rig.calls.count(good) == 1          # still cached
    assert rig.count("a-chrome") == 2


def _seed(rig, url, slug):
    """Pre-seed a cache entry at the lead's own key, so a deletion is visible."""
    rig.cache.get_or_build(rig.note(slug).fm)
    path = rig.cache._path(rig.note(slug).fm)
    assert os.path.exists(path)
    rig.calls.clear()
    return path


def test_a_refused_research_lead_keeps_its_cached_page(tmp_path, titles):
    rig = _Rig(tmp_path, titles)
    url = rig.lead("a-research", "research")
    path = _seed(rig, url, "a-research")
    report, _ = rig.run(3, statuses=("research",))
    assert report.blocked_research == 1 and os.path.exists(path)


def test_a_pasted_jd_lead_never_reaches_forget(tmp_path, titles, monkeypatch):
    rig = _Rig(tmp_path, titles)
    url = rig.lead("a-pasted", "new",
                   body=f"# Lead\n\n{HEADING}\n\nA pasted page that is not a posting.\n")
    path = _seed(rig, url, "a-pasted")
    rig.run(3)
    assert rig.note("a-pasted").status == "unjudgeable" and os.path.exists(path)


@pytest.mark.parametrize("how", ["setting_off", "dry_run"])
def test_the_cached_page_is_kept_when_the_feature_is_off_or_previewing(tmp_path, titles, how):
    rig = _Rig(tmp_path, titles)
    url = rig.lead("a-chrome", "new")
    path = _seed(rig, url, "a-chrome")
    if how == "setting_off":
        rig.run(0)
        assert rig.note("a-chrome").status == "unjudgeable"
        assert KEY not in _text(rig.note("a-chrome"))
    else:
        rig.run(3, dry_run=True)
    assert os.path.exists(path)


def test_a_twin_sharing_the_cache_key_refetches(tmp_path, titles):
    rig = _Rig(tmp_path, titles)
    shared = "https://example.invalid/shared"
    rig.lead("a-chrome", "new", url=shared)
    rig.lead("b-twin", "new", url=shared)
    rig.run(3, judge=_Judge({"b-twin": "shortlist"}))
    assert rig.calls == [shared]                       # one grouped fetch for both
    rig.run(3, judge=_Judge({"b-twin": "shortlist"}),
            statuses=("new", "unjudgeable", "shortlist"))
    assert rig.calls == [shared, shared]               # the entry was dropped for both


def test_a_failed_eviction_is_reported_and_the_run_continues(tmp_path, titles, monkeypatch):
    rig = _Rig(tmp_path, titles)
    rig.lead("a-chrome", "new")
    rig.lead("b-chrome", "new")

    def refuse(lead):
        raise PermissionError("read-only cache")
    monkeypatch.setattr(rig.cache, "forget", refuse)
    report, _ = rig.run(3)
    assert sum("read-only cache" in f for f in report.failures) == 2
    assert rig.count("a-chrome") == 1 and rig.count("b-chrome") == 1


# ── the digest ───────────────────────────────────────────────────────────────────────────

def test_the_digest_names_given_up_leads_and_how_to_retry_them():
    from sluice.cli import _format_triage_digest
    report = eng.TriageReport(judged=0, backend=None, failures=[])
    report.counts["given_up"] = 2
    out = _format_triage_digest(report)
    assert "2 given up after repeated unjudgeable results" in out
    assert "delete triage_unjudgeable_count" in out


def test_a_no_llm_run_does_not_report_a_given_up_lead_as_waiting_for_a_judge(tmp_path, titles):
    # End to end, so the row sees the engine's own counting: a given-up lead counted under
    # `keep` as well would read "passed the pre-gate but no judge ran, so they are still
    # waiting" -- the opposite of what happened to it.
    from sluice.cli import _format_triage_digest
    rig = _Rig(tmp_path, titles)
    rig.lead("a-gone", "unjudgeable", count=2)
    report, _ = rig.run(2, no_llm=True)
    assert report.counts["keep"] == 0 and report.counts["given_up"] == 1
    out = _format_triage_digest(report)
    assert "still waiting" not in out and "given up" in out


def test_with_the_setting_off_neither_producer_writes_a_count(tmp_path, titles):
    rig = _Rig(tmp_path, titles, jd_by_url={"https://example.invalid/a-empty": ""})
    rig.lead("a-empty", "new")          # pre-judge gate
    rig.lead("b-chrome", "new")         # judge
    report, _ = rig.run(0)
    assert report.unjudgeable_by == {"pre_gate": 1, "judge": 1}
    for slug in ("a-empty", "b-chrome"):
        note = rig.note(slug)
        assert note.status == "unjudgeable" and KEY not in _text(note)


@pytest.mark.parametrize("value", ["five", "-1", "3.0"])
def test_an_unreadable_counter_is_judged_not_given_up(tmp_path, titles, value):
    rig = _Rig(tmp_path, titles)
    url = rig.lead("a-lead", "unjudgeable", count=value)
    report, judge = rig.run(1)
    assert report.counts["given_up"] == 0
    assert url in rig.calls and judge.saw("a-lead")


def test_a_given_up_lead_is_not_sent_to_company_resolution(tmp_path, titles):
    # Resolution can visit the page (tier 2); a given-up lead's page has stopped being a
    # posting. Its classification still lands -- needs_review -- and clears the count.
    rig = _Rig(tmp_path, titles)
    url = rig.lead("a-gone", "unjudgeable", count=5, company="")
    rig.run(2, cfg={"company_resolve_fetch": True})
    assert url not in rig.calls
    note = rig.note("a-gone")
    assert note.status == "needs_review" and unjudgeable_count(note.fm) == 0
