"""Sluice.dismiss_lead(): resolution, CAS guards, note_appended idempotency, and the
50-round real-concurrency proof (#131 decisions 4, 5, 6, 17)."""
import pathlib
import threading

import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.vault import Vault


def _lead(company="Example Ltd", title="Example Role", url="https://example.invalid/1"):
    return Lead(source="s", search="q", title=title, company=company, url=url)


def _seed(tmp_path, *, status="shortlist", company="Example Ltd", title="Example Role",
          url="https://example.invalid/1", **extra):
    v = Vault(str(tmp_path))
    v.upsert(_lead(company=company, title=title, url=url))
    note = next(n for n in v.read_leads() if n.fm.get("url", "") == url)
    v.update_fields(note.ref, {"status": status, **extra})
    return note.slug


def _app(tmp_path):
    return Sluice(Config(), store=Vault(str(tmp_path)))


# ── resolution ──────────────────────────────────────────────────────────────────

def test_not_found(tmp_path):
    result = _app(tmp_path).dismiss_lead(lead="nothing here", reason="no fit")
    assert result.outcome == "not_found"


def test_exact_match_only_a_substring_fragment_is_not_found(tmp_path):
    """Mutation: swap the exact-equality check for slug_matches (substring) and
    confirm THIS test, not another, goes red."""
    slug = _seed(tmp_path, company="Example Northgate", title="Analyst")
    fragment = "Northgate"
    assert fragment in slug   # sanity: fragment IS a real substring
    result = _app(tmp_path).dismiss_lead(lead=fragment, reason="no fit")
    assert result.outcome == "not_found"


# ── validation ──────────────────────────────────────────────────────────────────

def test_raises_on_a_blank_reason(tmp_path):
    slug = _seed(tmp_path)
    with pytest.raises(ValueError, match="reason"):
        _app(tmp_path).dismiss_lead(lead=slug, reason="   ")


def test_raises_on_an_unsafe_reason_naming_it(tmp_path):
    slug = _seed(tmp_path)
    with pytest.raises(ValueError, match="reason"):
        _app(tmp_path).dismiss_lead(lead=slug, reason='bad"; status: applied')
    # nothing written
    assert Vault(str(tmp_path)).read_leads()[0].status == "shortlist"


# ── the write ───────────────────────────────────────────────────────────────────

def test_dismissed_from_each_triage_owned_status(tmp_path):
    for status in ("new", "shortlist", "research", "needs_review", "dismiss"):
        slug = _seed(tmp_path / status, status=status)
        result = Sluice(Config(), store=Vault(str(tmp_path / status))).dismiss_lead(
            lead=slug, reason="no fit")
        assert result.outcome == ("unchanged" if status == "dismiss" else "dismissed"), status


def test_refused_signoff_hold_names_the_remedy_lead(tmp_path):
    slug = _seed(tmp_path, status="shortlist", pending_cv='"CV_deadbeef.pdf (2026-08-14)"')
    result = _app(tmp_path).dismiss_lead(lead=slug, reason="no fit")
    assert result.outcome == "refused_signoff_hold"
    assert result.slug == slug
    text = pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text()
    assert "status: dismiss" not in text


def test_same_day_repeat_is_unchanged_and_note_appended_is_false(tmp_path):
    slug = _seed(tmp_path, status="shortlist")
    app = _app(tmp_path)
    first = app.dismiss_lead(lead=slug, reason="no fit", note_tag="[dismiss FIXED]")
    second = app.dismiss_lead(lead=slug, reason="different reason", note_tag="[dismiss FIXED]")
    assert first.outcome == "dismissed" and first.note_appended is True
    assert second.outcome == "unchanged" and second.note_appended is False
    text = pathlib.Path(Vault(str(tmp_path)).read_leads()[0].ref).read_text()
    assert text.count("[dismiss FIXED]") == 1
    assert "different reason" not in text   # the second reason was suppressed by its own tag


# ── CAS proofs ──────────────────────────────────────────────────────────────────

def test_cas_proof_refuses_on_a_status_that_changed_between_resolve_and_write(tmp_path, monkeypatch):
    """Testing item 3: dismiss_lead resolves a note, then a DIFFERENT writer moves it
    out of TRIAGE_OWNED before dismiss_lead's own write lands. require_status must
    catch this from FRESH bytes, not the resolution snapshot. Mutation: deleting
    require_status= must independently turn this test red."""
    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="research")
    app = _app(tmp_path)
    real_update_fields = Vault.update_fields

    def _racing_update_fields(self, ref, fields, **kwargs):
        real_update_fields(self, ref, {"status": "applied"})   # simulated concurrent writer
        return real_update_fields(self, ref, fields, **kwargs)

    monkeypatch.setattr(Vault, "update_fields", _racing_update_fields)
    result = app.dismiss_lead(lead=slug, reason="no fit")
    assert result.outcome == "refused_status"
    assert result.status == "applied"
    text = pathlib.Path(v.read_leads()[0].ref).read_text()
    assert "status: dismiss" not in text
    assert "status: applied" in text


def test_cas_proof_refused_status_is_reported_even_when_the_drifted_status_is_non_canonical(
        tmp_path, monkeypatch):
    """Round-2 review finding: the diagnostic re-read used to filter on CANONICAL.
    `_status.normalize` passes an unrecognised value through unchanged, so a
    hand-edited status like 'on hold' is outside CANONICAL and the drifted note
    was filtered OUT of the re-read -- both the ref lookup and the slug fallback
    then missed it, `fresh` stayed None, and the code fell back to the STALE
    pre-write snapshot's status, reporting the more benign `unchanged` for a
    write require_status genuinely refused. Mutation: reinstating the CANONICAL
    filter on `fresh_notes` must independently turn this test red."""
    slug = _seed(tmp_path, status="research")
    app = _app(tmp_path)
    real_update_fields = Vault.update_fields

    def _racing_update_fields(self, ref, fields, **kwargs):
        real_update_fields(self, ref, {"status": "on hold"})   # non-canonical drift
        return real_update_fields(self, ref, fields, **kwargs)

    monkeypatch.setattr(Vault, "update_fields", _racing_update_fields)
    result = app.dismiss_lead(lead=slug, reason="no fit")
    assert result.outcome == "refused_status"
    assert result.status == "on hold"


def test_cas_proof_refuses_on_a_pending_cv_that_appeared_between_resolve_and_write(tmp_path, monkeypatch):
    """Symmetric CAS proof for require_blank. Mutation: deleting require_blank= must
    independently turn this test red."""
    v = Vault(str(tmp_path))
    slug = _seed(tmp_path, status="shortlist")
    app = _app(tmp_path)
    real_update_fields = Vault.update_fields

    def _racing_update_fields(self, ref, fields, **kwargs):
        real_update_fields(self, ref, {"pending_cv": '"CV_deadbeef.pdf (2026-08-14)"'})
        return real_update_fields(self, ref, fields, **kwargs)

    monkeypatch.setattr(Vault, "update_fields", _racing_update_fields)
    result = app.dismiss_lead(lead=slug, reason="no fit")
    assert result.outcome == "refused_signoff_hold"
    text = pathlib.Path(v.read_leads()[0].ref).read_text()
    assert "status: dismiss" not in text
    assert "pending_cv:" in text


# ── the 50-round real-concurrency proof (Testing item 12a, decision 17) ─────────

def test_50_rounds_of_real_concurrent_dismissal_exactly_one_wins(tmp_path):
    """The guard's ACTUAL safety proof -- real threads, real file I/O, no mocking of
    the write layer, mirroring tests/conformance/test_store_contract.py's own
    proven Barrier technique. NOT the SDK sanity check (that's a different tier,
    tests/functional/test_mcp_contract.py, Task 12)."""
    for round_no in range(50):
        round_dir = tmp_path / f"r{round_no}"
        v = Vault(str(round_dir))
        v.upsert(_lead())
        note = next(n for n in v.read_leads() if n.fm.get("url", "") == "https://example.invalid/1")
        v.update_fields(note.ref, {"status": "shortlist"})
        slug = note.slug
        app = Sluice(Config(), store=Vault(str(round_dir)))
        results, barrier = [], threading.Barrier(2)

        def dismiss(i, _app=app, _slug=slug, _results=results, _barrier=barrier):
            _barrier.wait()     # maximise the overlap rather than hoping for it
            _results.append(_app.dismiss_lead(lead=_slug, reason=f"reason-{i}"))

        threads = [threading.Thread(target=dismiss, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        outcomes = sorted(r.outcome for r in results)
        assert outcomes == ["dismissed", "unchanged"], (
            f"round {round_no}: expected exactly one dismissed and one unchanged, "
            f"got {[r.outcome for r in results]}")
        by_outcome = {r.outcome: r for r in results}
        assert by_outcome["dismissed"].note_appended is True
        assert by_outcome["unchanged"].note_appended is False
        text = pathlib.Path(Vault(str(round_dir)).read_leads()[0].ref).read_text()
        assert text.count("[dismiss ") == 1
