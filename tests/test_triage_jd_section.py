"""triage reads the lead note's own job description (#369, spec §2). On a REAL DossierCache
with a counting fetcher that returns an EMPTY JD (tst-201): the existing stand-in caches
return a JD whatever the lead holds, so a section row run on them stays green with the
feature deleted. Every lead's company is already set, so company resolution never fetches
and every fetcher call is the prefetch's."""
import logging
import os
from datetime import datetime

import pytest

from sluice.core.dossier import DossierCache
from sluice.core.jdsection import HEADING
from sluice.core.vault import Vault
from sluice.triage.audit import AuditLog
from sluice.triage.config import TriageConfig
import sluice.triage.engine as eng
from tests.test_triage_engine import _Backend, _fields

_SENTINEL = "SENTINEL-POSTING-7f3a placeholder duties for an example role."


class _Counting:
    def __init__(self):
        self.calls = 0

    def __call__(self, lead):
        self.calls += 1
        return {"jd": {"markdown": ""}}


def _cache(tmp_path, fetcher, floor=0):
    return DossierCache(str(tmp_path / "dos"), ttl_days=7, fetcher=fetcher,
                        clock=lambda: datetime(2026, 10, 11), min_jd_chars=floor)


def _write(v, name, fm_lines, body):
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(fm_lines) + "\n---\n" + body)


def _section(text=_SENTINEL):
    return f"# Lead\n\n{HEADING}\n{text}\n\n## Notes\nmine\n"


def _run(tmp_path, titles, notes, *, floor=0, concurrency=1):
    accept, _ = titles
    v = Vault(str(tmp_path / "vault"))
    for name, company, url, body in notes:
        _write(v, name, _fields(company, accept[0].title(), url=url), body)
    cfg = TriageConfig(); cfg.accept_titles = list(accept)
    fetcher, backend = _Counting(), _Backend()
    report = eng.run(v, cfg, backend, _cache(tmp_path, fetcher, floor),
                     AuditLog(str(tmp_path / "audit.jsonl")), statuses=("new",),
                     dossier_concurrency=concurrency)
    after = {n.fm["company"]: n for n in v.read_leads()}
    return report, after, fetcher, "\n".join(backend.prompts)


def test_a_section_lead_with_no_url_is_judged_from_the_section(tmp_path, titles):
    report, after, fetcher, prompts = _run(
        tmp_path, titles, [("a.md", "Example Alpha", "", _section())])
    assert after["Example Alpha"].status == "shortlist"
    assert _SENTINEL in prompts
    assert fetcher.calls == 0


def test_a_url_lead_with_a_section_costs_no_fetch(tmp_path, titles):
    _, after, fetcher, prompts = _run(
        tmp_path, titles, [("a.md", "Example Alpha", "https://example.invalid/1", _section())])
    assert fetcher.calls == 0 and _SENTINEL in prompts
    assert after["Example Alpha"].status == "shortlist"


def test_control_the_same_lead_without_a_section_fetches_once(tmp_path, titles):
    _, after, fetcher, _ = _run(
        tmp_path, titles, [("a.md", "Example Alpha", "https://example.invalid/1", "# Lead\n")])
    assert fetcher.calls == 1
    assert after["Example Alpha"].status == "unjudgeable"


def test_an_empty_section_is_no_section(tmp_path, titles):
    _, after, fetcher, _ = _run(
        tmp_path, titles,
        [("a.md", "Example Alpha", "https://example.invalid/1", f"{HEADING}\n\n## Notes\n")])
    assert fetcher.calls == 1
    assert after["Example Alpha"].status == "unjudgeable"
    assert "no job description was fetched" in after["Example Alpha"].fm["relevance_notes"]


@pytest.mark.parametrize("concurrency", [1, 4])
def test_two_notes_sharing_a_cache_key_each_get_their_own_section(tmp_path, titles,
                                                                   concurrency):
    url = "https://example.invalid/shared"
    cache = _cache(tmp_path, _Counting())
    # tst-007: assert the shared key FIRST, or the row could pass on two distinct keys.
    assert cache.cache_key({"url": url}) == cache.cache_key({"url": url, "company": "x"})
    _, after, fetcher, prompts = _run(
        tmp_path, titles,
        [("a.md", "Example Alpha", url, _section("ALPHA-ONLY posting text here.")),
         ("b.md", "Example Beta", url, _section("BETA-ONLY posting text here."))],
        concurrency=concurrency)
    assert fetcher.calls == 0
    assert "ALPHA-ONLY" in prompts and "BETA-ONLY" in prompts
    assert {after["Example Alpha"].status, after["Example Beta"].status} == {"shortlist"}


def test_a_section_read_that_raises_skips_only_that_lead(tmp_path, titles, monkeypatch):
    real = eng.jdsection.read

    def flaky(body):
        if "BROKEN" in body:
            raise ValueError("reader bug")
        return real(body)
    monkeypatch.setattr(eng.jdsection, "read", flaky)
    report, after, _, prompts = _run(
        tmp_path, titles,
        [("a.md", "Example Alpha", "", _section("BROKEN marker text here.")),
         ("b.md", "Example Beta", "", _section())])
    assert any(f.startswith("section ") and "reader bug" in f for f in report.failures)
    assert not any(f.startswith("dossier ") for f in report.failures)
    assert after["Example Beta"].status == "shortlist"
    assert after["Example Alpha"].status == "new"


def test_a_short_section_is_unjudgeable_with_the_note_reason(tmp_path, titles):
    _, after, fetcher, _ = _run(
        tmp_path, titles, [("a.md", "Example Alpha", "", _section())], floor=1000)
    assert fetcher.calls == 0
    assert after["Example Alpha"].status == "unjudgeable"
    assert ("the job description in the note is shorter than min_jd_chars (1000)"
            in after["Example Alpha"].fm["relevance_notes"])


def test_a_section_that_states_a_pay_basis_is_observed_like_a_fetched_posting(tmp_path, titles):
    # inv-401, owner's ruling (a): a job description in the note IS the posting, for the
    # #223 role_type observation too -- a pasted posting saying "contract" corrects a
    # blank-provenance role_type exactly as the same text fetched would. _fields seeds
    # role_type "permanent" with no role_type_source, which ranks below `observed`.
    _, after, fetcher, _ = _run(
        tmp_path, titles,
        [("a.md", "Example Alpha", "", _section(_SENTINEL + " This is a fixed-term contract."))])
    assert fetcher.calls == 0
    assert after["Example Alpha"].fm["role_type"] == "contract"
    assert after["Example Alpha"].fm["role_type_source"] == "observed"


def test_duplicate_sections_log_a_warning(tmp_path, titles, caplog):
    body = f"{HEADING}\nfirst text\n## X\n{HEADING}\nsecond\n"
    with caplog.at_level(logging.WARNING):
        _run(tmp_path, titles, [("a.md", "Example Alpha", "", body)])
    assert any("sections; the first was used" in r.getMessage() for r in caplog.records)


def test_a_section_build_that_raises_is_a_failed_dossier_for_that_lead_only(
        tmp_path, titles, monkeypatch):
    # tst-003: get_or_build raising on the SUPPLIED path is reduced to _FetchFailed for that
    # lead alone -- reported as `dossier <ref>: ...`, the lead left untouched -- and the
    # next lead is still judged from its own section.
    real = DossierCache.get_or_build

    def flaky(self, fm, **kw):
        if "BROKEN" in kw.get("supplied_jd", ""):
            raise RuntimeError("cache bug")
        return real(self, fm, **kw)
    monkeypatch.setattr(DossierCache, "get_or_build", flaky)
    report, after, fetcher, _ = _run(
        tmp_path, titles,
        [("a.md", "Example Alpha", "", _section("BROKEN marker text here.")),
         ("b.md", "Example Beta", "", _section())])
    assert any(f.startswith("dossier ") and "cache bug" in f for f in report.failures)
    assert after["Example Alpha"].status == "new"
    assert after["Example Beta"].status == "shortlist"
    assert fetcher.calls == 0
