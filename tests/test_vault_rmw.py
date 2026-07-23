"""#16 RMW-race safety: content-CAS + atomic replace + bounded re-apply.

Race simulation is deterministic and threadless -- `racing_read` interposes the
module-level `_read` to land one out-of-band edit in the capture->commit window.
"""
import os
import stat

import pytest

from sluice.core.vault import Vault, _atomic_write, _cas_write
from sluice.core.protocols import VaultConflict
from tests.conftest import racing_read


def test_atomic_write_replaces_contents(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("old", encoding="utf-8")
    _atomic_write(str(p), "new")
    assert p.read_text(encoding="utf-8") == "new"
    # no temp siblings left behind
    assert [f.name for f in tmp_path.iterdir()] == ["n.md"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_atomic_write_preserves_mode(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("old", encoding="utf-8")
    os.chmod(p, 0o640)
    _atomic_write(str(p), "new")
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o640


def test_cas_write_commits_when_unchanged(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("a", encoding="utf-8")
    assert _cas_write(str(p), lambda t: t + "b") is True
    assert p.read_text(encoding="utf-8") == "ab"


def test_cas_write_noop_returns_false(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("a", encoding="utf-8")
    assert _cas_write(str(p), lambda t: t) is False  # identity transform -> no write


def test_cas_write_self_heals_when_file_changes_under_it(tmp_path, monkeypatch):
    p = tmp_path / "n.md"
    p.write_text("base\n", encoding="utf-8")
    # Racer appends a line once, in the capture->commit window of our first attempt.
    racing_read(monkeypatch, str(p), lambda: p.write_text("base\nRACER\n", encoding="utf-8"))
    # Our edit appends OURS; re-derived onto the racer's content, both survive.
    assert _cas_write(str(p), lambda t: t + "OURS\n") is True
    body = p.read_text(encoding="utf-8")
    assert "RACER" in body and body.endswith("OURS\n")


def test_cas_write_raises_on_sustained_race(tmp_path, monkeypatch):
    p = tmp_path / "n.md"
    p.write_text("v0\n", encoding="utf-8")
    counter = {"n": 0}
    def churn():
        counter["n"] += 1
        p.write_text(f"v{counter['n']}\n", encoding="utf-8")  # unique content every read
    racing_read(monkeypatch, str(p), churn, once=False)
    with pytest.raises(VaultConflict):
        _cas_write(str(p), lambda t: t + "OURS\n")


def test_cas_write_does_not_return_a_stale_no_op(tmp_path, monkeypatch):
    # A presence/absence transform (append-if-tag-absent). At capture the tag is present
    # (a would-be no-op), but a racer REMOVES it in the capture->decision window. _cas_write
    # must detect the change and re-derive, not return a stale False that leaves the tag gone.
    p = tmp_path / "n.md"
    p.write_text("TAG\n", encoding="utf-8")
    racing_read(monkeypatch, str(p), lambda: p.write_text("plain\n", encoding="utf-8"))
    result = _cas_write(str(p), lambda t: t if "TAG" in t else t + "TAG\n")
    body = p.read_text(encoding="utf-8")
    assert "TAG" in body      # re-derived onto the racer's content
    assert result is True      # a write happened, not a stale no-op


def _leads_dir(tmp_path):
    return tmp_path / "Job Applications" / "Job Leads"


def _seed_note(tmp_path, name="Acme - Analyst.md", extra=""):
    d = _leads_dir(tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        f"---\ncompany: \"Acme\"\nrole: \"Analyst\"\nstatus: new\n{extra}---\n\n# body\n",
        encoding="utf-8")
    return d / name


def test_update_fields_self_heals_a_concurrent_different_key(tmp_path, monkeypatch):
    f = _seed_note(tmp_path)
    v = Vault(str(tmp_path))
    # Racer sets a DIFFERENT key (score) during our status write.
    def racer():
        f.write_text(f.read_text(encoding="utf-8").replace(
            "status: new", "status: new\nscore: 9"), encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    v.update_fields(str(f), {"status": "shortlist"})
    txt = f.read_text(encoding="utf-8")
    assert "status: shortlist" in txt   # ours, re-applied
    assert "score: 9" in txt            # racer's, preserved
    assert "# body" in txt              # body intact


def test_append_body_section_self_heals(tmp_path, monkeypatch):
    f = _seed_note(tmp_path)
    v = Vault(str(tmp_path))
    def racer():
        f.write_text(f.read_text(encoding="utf-8").replace(
            "status: new", "status: shortlist"), encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    assert v.append_body_section(str(f), "<!--t-->", "<!--t-->\nsection") is True
    txt = f.read_text(encoding="utf-8")
    assert "status: shortlist" in txt   # racer's frontmatter edit preserved
    assert "section" in txt             # our append landed


def test_bump_last_seen_does_not_regress_under_a_concurrent_newer_bump(tmp_path, monkeypatch):
    # THE concurrent guarantee (distinct from the sequential monotonic tests in test_vault.py):
    # a newer bump landing mid-write must win, not be regressed by our re-derive.
    f = _seed_note(tmp_path, extra="last_seen: 2026-07-10\n")
    v = Vault(str(tmp_path))
    def racer():
        f.write_text(f.read_text(encoding="utf-8").replace(
            "last_seen: 2026-07-10", "last_seen: 2026-07-15"), encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    v._bump_last_seen(str(f), "2026-07-12")   # older than the racer's concurrent bump
    assert "last_seen: 2026-07-15" in f.read_text(encoding="utf-8")


def test_raced_body_edit_survives_a_concurrent_frontmatter_write(tmp_path, monkeypatch):
    # A racer that touches only FRONTMATTER never exercises whether the BODY survives --
    # the body is untouched by update_fields's transform regardless of whether the write
    # is CAS-safe or a naive whole-file overwrite, so a racer confined to frontmatter
    # passes even against the pre-#16 read-transform-write with no re-derivation at all.
    # The racer here edits the BODY (as Obsidian or a human would), so only the CAS
    # re-derive -- not mere non-interference -- can make both edits survive.
    f = _seed_note(tmp_path)
    v = Vault(str(tmp_path))
    def racer():
        f.write_text(f.read_text(encoding="utf-8") + "RACER BODY LINE\n", encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    v.update_fields(str(f), {"status": "research"})
    txt = f.read_text(encoding="utf-8")
    assert "status: research" in txt      # our frontmatter edit landed
    assert "RACER BODY LINE" in txt       # the racer's body edit survived the re-derive


def test_set_tailored_cv_only_if_absent_skips_when_present(tmp_path):
    f = _seed_note(tmp_path, extra="tailored_cv: EXISTING.pdf\n")
    v = Vault(str(tmp_path))
    assert v.set_tailored_cv(str(f), "NEW.pdf", only_if_absent=True) is False
    assert "EXISTING.pdf" in f.read_text(encoding="utf-8")
    assert "NEW.pdf" not in f.read_text(encoding="utf-8")


def test_set_tailored_cv_overwrites_by_default(tmp_path):
    f = _seed_note(tmp_path, extra="tailored_cv: EXISTING.pdf\n")
    v = Vault(str(tmp_path))
    assert v.set_tailored_cv(str(f), "NEW.pdf") is True
    assert "NEW.pdf" in f.read_text(encoding="utf-8")


def test_normalize_self_heals_a_concurrent_non_status_edit(tmp_path, monkeypatch):
    d = _leads_dir(tmp_path); d.mkdir(parents=True, exist_ok=True)
    f = d / "Acme - Analyst.md"
    f.write_text('---\ncompany: "Acme"\nstatus: "new"\n---\n\n# body\n', encoding="utf-8")
    v = Vault(str(tmp_path))
    def racer():
        f.write_text(f.read_text(encoding="utf-8").replace(
            'company: "Acme"', 'company: "Acme"\nscore: 7'), encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    v.normalize_all_statuses(dry_run=False)
    txt = f.read_text(encoding="utf-8")
    assert "status: new" in txt      # canonicalised, quotes dropped
    assert "score: 7" in txt         # racer's edit preserved


def test_normalize_abstains_when_a_race_introduces_a_conflict(tmp_path, monkeypatch):
    d = _leads_dir(tmp_path); d.mkdir(parents=True, exist_ok=True)
    f = d / "Acme - Analyst.md"
    f.write_text('---\ncompany: "Acme"\nstatus: "new"\n---\n\n# body\n', encoding="utf-8")
    v = Vault(str(tmp_path))
    def racer():  # concurrent edit introduces a DISAGREEING second status line
        f.write_text(f.read_text(encoding="utf-8").replace(
            'status: "new"', 'status: "new"\nstatus: dismiss'), encoding="utf-8")
    racing_read(monkeypatch, str(f), racer)
    summary = v.normalize_all_statuses(dry_run=False)
    txt = f.read_text(encoding="utf-8")
    # abstained: NO write happened at all, so both disagreeing lines survive verbatim
    # (the original stays quoted -- abstain must not canonicalise even the line it agrees
    # with, or a partial rewrite would silently narrow "disagreement" to "trust line 1").
    assert 'status: "new"' in txt and "status: dismiss" in txt
    # An abstained _cas_write (the race made the collapse a no-op against the FRESH,
    # now-conflicting content) must NOT be counted "changed" -- nothing was written this
    # run. It is reported "unchanged", not invisible: the up-front scan's own summary is
    # what a caller sees, and "changed" claiming a write that never happened is exactly
    # the double-count/invisibility bug #16's review fold closes.
    assert summary["changed"] == 0
    assert summary["unchanged"] == 1


def test_normalize_skips_and_reports_a_sustained_race(tmp_path, monkeypatch):
    # A SUSTAINED race (on every read, not just the first) exhausts _cas_write's
    # retries -> VaultConflict. The sweep must not let that escape uncaught (it would
    # abort every note after this one); it logs and reports the note under "skipped",
    # counts it neither changed nor unchanged, and leaves the note unwritten.
    d = _leads_dir(tmp_path); d.mkdir(parents=True, exist_ok=True)
    f = d / "Acme - Analyst.md"
    f.write_text('---\ncompany: "Acme"\nstatus: "new"\n---\n\n# body\n', encoding="utf-8")
    v = Vault(str(tmp_path))
    counter = {"n": 0}
    def churn():
        # Unique content every call (once=False, for exhaustion) -- each write leaves
        # the status line disagreeing in a NEW way, so no attempt ever sees a settled,
        # collapsible file.
        counter["n"] += 1
        f.write_text(
            f'---\ncompany: "Acme"\nstatus: "new"\nstatus: dismiss-{counter["n"]}\n---\n\n# body\n',
            encoding="utf-8")
    racing_read(monkeypatch, str(f), churn, once=False)
    summary = v.normalize_all_statuses(dry_run=False)
    assert "Acme - Analyst.md" in summary["skipped"]
    assert summary["changed"] == 0
    # not rewritten: the ORIGINAL single "new" status line from the up-front scan's
    # capture never got collapsed onto disk (the racer's own churned content did land,
    # since churn writes directly -- but no CAS-committed rewrite from us is in there).
    assert "status: \"new\"\nstatus: dismiss-" in f.read_text(encoding="utf-8")


def test_upsert_absorbs_a_bump_conflict_into_refused(tmp_path, monkeypatch):
    # #16 Task 4: upsert's update branch must not let a sustained _bump_last_seen race
    # (VaultConflict) escape as a raw exception -- it absorbs into the store's existing
    # concurrency-loss vocabulary ("refused"), same as the create-race exhaustion above.
    from sluice.core.leads import Lead
    f = _seed_note(tmp_path, extra="last_seen: 2026-07-10\nurl: \"https://example.invalid/1\"\n")
    v = Vault(str(tmp_path))
    lead = Lead(source="b", search="s", title="Analyst", company="Acme",
                location="", salary="", url="https://example.invalid/1",
                last_seen="2026-07-20")
    counter = {"n": 0}
    def churn():
        # Anchored on the "last_seen: " key prefix (not a bare value split) so the
        # replace cannot be fooled by the date coincidentally appearing elsewhere.
        counter["n"] += 1
        cur = f.read_text(encoding="utf-8")
        prev = cur.split("last_seen: ")[1].split("\n")[0]
        f.write_text(cur.replace(f"last_seen: {prev}", f"last_seen: 2026-08-{counter['n']:02d}"),
                     encoding="utf-8")
    racing_read(monkeypatch, str(f), churn, once=False)
    assert v.upsert(lead) == "refused"   # not an uncaught VaultConflict


def test_upsert_merge_absorbs_a_bump_conflict_into_refused(tmp_path, monkeypatch):
    # F2: the merge branch shares _bump_last_seen_or_refuse with update -- extracting
    # that helper is only a real dedup if BOTH call sites are witnessed, not just update's
    # (deleting the helper's try/except must redden this test too, not merely the one
    # above). Same churn shape as test_upsert_absorbs_a_bump_conflict_into_refused; the
    # only difference is the seeded note/lead pair, set up so _resolve_path returns
    # "merge" (a differing url with NO location on either side -> same_opportunity
    # abstains UNKNOWN) rather than "update" (a matching url -> SAME).
    from sluice.core.leads import Lead
    f = _seed_note(tmp_path, extra="last_seen: 2026-07-10\nurl: \"https://example.invalid/1\"\n")
    v = Vault(str(tmp_path))
    lead = Lead(source="b", search="s", title="Analyst", company="Acme",
                location="", salary="", url="https://example.invalid/2",  # differs -> not SAME
                last_seen="2026-07-20")
    counter = {"n": 0}
    def churn():
        counter["n"] += 1
        cur = f.read_text(encoding="utf-8")
        prev = cur.split("last_seen: ")[1].split("\n")[0]
        f.write_text(cur.replace(f"last_seen: {prev}", f"last_seen: 2026-08-{counter['n']:02d}"),
                     encoding="utf-8")
    racing_read(monkeypatch, str(f), churn, once=False)
    assert v.upsert(lead) == "refused"   # not an uncaught VaultConflict


def test_ingest_sink_survives_a_bump_conflict_and_keeps_the_lead_unrecorded(tmp_path, monkeypatch):
    # Integration: the raced conflict must not escape through VaultSink.write either --
    # the sink catches only OSError, so an uncaught VaultConflict would abort the whole
    # ingest batch. The lead must be counted refused and stay OUT of seen.db so it is
    # retried (and re-reported) next run rather than silently lost.
    from sluice.core.leads import Lead
    from sluice.ingest.sink import VaultSink

    class _SeenSpy:
        def __init__(self): self.saved = []
        def save(self, leads): self.saved.extend(leads)

    f = _seed_note(tmp_path, extra="last_seen: 2026-07-10\nurl: \"https://example.invalid/1\"\n")
    v = Vault(str(tmp_path))
    seen = _SeenSpy()
    sink = VaultSink(v, seen, today=lambda: "2026-07-20")
    conflicting = Lead(source="b", search="s", title="Analyst", company="Acme",
                       location="", salary="", url="https://example.invalid/1")
    counter = {"n": 0}
    def churn():
        counter["n"] += 1
        cur = f.read_text(encoding="utf-8")
        prev = cur.split("last_seen: ")[1].split("\n")[0]
        f.write_text(cur.replace(f"last_seen: {prev}", f"last_seen: 2026-08-{counter['n']:02d}"),
                     encoding="utf-8")
    racing_read(monkeypatch, str(f), churn, once=False)
    counts = sink.write([conflicting])
    assert counts.get("refused") == 1        # counted, batch did not abort
    assert conflicting not in seen.saved     # stays out of seen.db -> retried next run
