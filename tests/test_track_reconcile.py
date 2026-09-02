import tempfile, pathlib
from datetime import datetime, timezone
import pytest
from sluice.core.protocols import VaultConflict
from sluice.core.vault import Vault
from sluice.track.config import TrackConfig
from sluice.track.classify import Event, classify
from tests.test_track_classify import RaisingBackend
from sluice.track.ics import IcsEvent
from sluice.track import reconcile as R
from tests.test_track_google_client import FakeGoogleClient


def _vault_with(slug, status):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / f"{slug}.md").write_text(f'---\ncompany: "X"\nrole: "Analyst"\nstatus: {status}\n---\n\nBODY\n')
    v = Vault(root)
    note = [n for n in v.read_leads() if n.slug == slug][0]
    return v, {slug: note}, str(leads / f"{slug}.md")


def _shortlist_with(slug, url, company="Example", status="shortlist"):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / f"{slug}.md").write_text(
        f'---\ncompany: "{company}"\nrole: "Analyst"\nurl: "{url}"\nstatus: {status}\n---\n\nBODY\n')
    v = Vault(root)
    note = [n for n in v.read_leads() if n.slug == slug][0]
    return v, {slug: note}, str(leads / f"{slug}.md")


def _receipt_ev(tier, slug, sender="jobs@example.com", subject="Thanks for applying", conf=0.9):
    return Event(type="receipt", receipt_tier=tier, lead_slug=slug, confidence=conf,
                 sender=sender, subject=subject, summary="application received")


def _ics():
    return IcsEvent(uid="u1", summary="Screen", start=datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
                    end=datetime(2026, 7, 20, 10, 30, tzinfo=timezone.utc))


def test_interview_with_ics_auto_advances_and_calendars():
    v, notes, path = _vault_with("Example Tidal - EM", "applied")
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=_ics(),
               materials=["Deck"], links=["https://x/deck"])
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.action == "applied" and res.status_to == "interview"
    assert res.calendar == "created" and res.materials_written is True
    text = pathlib.Path(path).read_text()
    assert "status: interview" in text and "interview_date" in text and "Deck" in text


def test_a_guessed_instant_is_flagged_on_the_result_so_the_digest_can_report_it():
    # A naive DTSTART books at an ASSUMED UTC. The log line says so, but under cron stderr is
    # usually discarded, so the count has to ride the result out to the digest -- otherwise
    # the only surviving evidence of a possibly-wrong hour is a calendar entry that looks
    # entirely ordinary.
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    naive = IcsEvent(uid="u1", summary="Screen", start=datetime(2026, 7, 20, 10, 0),
                     tzid_unresolved="Nowhere/Notreal")
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=naive)
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.calendar == "created" and res.calendar_assumed_tz is True


def test_a_guessed_instant_is_still_flagged_when_the_assumed_zone_is_not_utc():
    # The flag means "this instant was ASSUMED", not "assumed UTC". A configured zone makes
    # the guess better-informed, never certain -- the invite still stated no instant. Naming
    # or gating this on UTC would silence the warning for exactly the people who bothered to
    # configure the key, and the entry would still be a guess.
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    cfg = TrackConfig(calendar_assumed_timezone="Europe/Berlin")
    naive = IcsEvent(uid="u1", summary="Screen", start=datetime(2026, 7, 20, 10, 0),
                     tzid_unresolved="Nowhere/Notreal")
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=naive)
    c = FakeGoogleClient(events=[])
    res = R.reconcile(ev, notes, v, cfg, c)
    assert res.calendar == "created" and res.calendar_assumed_tz is True
    # ...and the booking really did use the configured zone, end to end.
    assert c.inserted[0]["start"]["timeZone"] == "Europe/Berlin"


def test_a_dry_run_still_counts_the_guess_so_the_preview_can_warn():
    # `calendar_added` counts a dry run's would-be writes too, so this counter matches its
    # sibling rather than diverging. The CLI changes the VERB ("would be booked"), not the
    # count -- gating the count here would make a preview silently omit the warning that is
    # the whole reason a human reads a dry run.
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    naive = IcsEvent(uid="u1", summary="Screen", start=datetime(2026, 7, 20, 10, 0))
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=naive)
    c = FakeGoogleClient(events=[])
    res = R.reconcile(ev, notes, v, TrackConfig(), c, dry_run=True)
    assert res.calendar == "created" and res.calendar_assumed_tz is True
    assert not c.inserted, "a dry run must not write"


def test_a_resolved_instant_is_not_flagged():
    # The counter must stay at zero on the ordinary path, or the digest warning fires on
    # every run and stops meaning anything.
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=_ics())
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.calendar == "created" and res.calendar_assumed_tz is False


def test_cancellation_ics_does_not_advance():
    v, notes, path = _vault_with("Example Tidal - EM", "interview")
    ics = _ics(); ics.method = "CANCEL"
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=ics)
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.status_to is None and "status: interview" in pathlib.Path(path).read_text()


def test_interview_link_with_a_structural_character_is_dropped_but_status_still_advances():
    # #111: ev.links[0] is parsed out of an inbound email -- untrusted, same class as
    # resolve.py's scraped company. A structural character must not corrupt the note's
    # frontmatter; the interview signal itself (status/date/materials) still lands.
    v, notes, path = _vault_with("Example Tidal - EM", "applied")
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=_ics(),
               links=['https://x/deck"; status: applied'])
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.action == "applied" and res.status_to == "interview"
    text = pathlib.Path(path).read_text()
    assert "status: interview" in text
    assert "interview_link" not in text


def test_soft_rejection_proposes_not_auto():
    v, notes, path = _vault_with("Example Tidal - EM", "phone_screen")
    ev = Event(lead_slug="Example Tidal - EM", type="rejection", confidence=0.7, summary="on file")  # below auto_reject_min
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "proposed" and "status: phone_screen" in pathlib.Path(path).read_text()


def test_specific_high_conf_rejection_auto():
    v, notes, path = _vault_with("Example Tidal - EM", "phone_screen")
    ev = Event(lead_slug="Example Tidal - EM", type="rejection", confidence=0.95, summary="not moving forward")
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "applied" and res.status_to == "rejected"
    assert "status: rejected" in pathlib.Path(path).read_text()


def test_ambiguous_lead_proposes():
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    ev = Event(lead_slug=None, candidates=["A", "B"], type="interview", confidence=0.9, ics=_ics())
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "proposed"


def test_never_regress_refuses():
    v, notes, path = _vault_with("Example Tidal - EM", "offer")
    ev = Event(lead_slug="Example Tidal - EM", type="phone_screen", confidence=0.9, ics=_ics())
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert res.status_to is None and "status: offer" in pathlib.Path(path).read_text()


def test_unknown_event_proposes_with_an_honest_label_never_skipped():
    # A classification we could not make (#40) must surface for a human. It is NOT the
    # not_job/update shape that reconcile silently skips, so it proposes -- and with an
    # honest label ("classification failed"), not the misleading "unmatched/ambiguous"
    # that the generic unmatched path would attach.
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    ev = Event(lead_slug=None, type="unknown", summary="")
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "proposed"
    assert "classification failed" in res.proposal


def test_applied_lead_with_unclassifiable_mail_is_not_silently_unchanged():
    # The exact #40 failure, end to end: a rejection email whose classification THROWS used to
    # become a confident not_job -> reconcile skipped it -> the lead sat at `applied` forever.
    # Now classify yields `unknown`, reconcile proposes it for review, and the note is untouched
    # -- surfaced, never skipped, never regressed.
    v, notes, path = _vault_with("Example Tidal - EM", "applied")
    msg = {"headers": {"from": "hr@x", "subject": "Re: your application"}, "body_text": "",
           "thread_id": "t1", "attachments": [], "message_id": "m1"}
    ev = classify(msg, list(notes.values()), RaisingBackend(), TrackConfig())
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient())
    assert res.action == "proposed"
    assert "status: applied" in pathlib.Path(path).read_text()


def test_receipt_proof_advances_shortlist_to_applied():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    assert res.action == "applied" and res.status_to == "applied"
    text = pathlib.Path(path).read_text()
    assert "status: applied" in text and "## Application receipt" in text


def test_receipt_below_confidence_floor_proposes():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst", conf=0.5)  # below auto_apply_min
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    assert res.action == "proposed" and "status: shortlist" in pathlib.Path(path).read_text()


def test_receipt_corroborated_proposes_not_advances():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("corroborated", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    assert res.action == "proposed"
    text = pathlib.Path(path).read_text()
    assert "status: shortlist" in text and "## Application receipt" not in text  # absence-of-write


def test_receipt_ambiguous_proposes_neither():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = Event(type="receipt", receipt_tier="corroborated", lead_slug=None,
               candidates=["Example - Analyst", "Example - Manager"], confidence=0.9)
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    assert res.action == "proposed" and "status: shortlist" in pathlib.Path(path).read_text()


def test_receipt_cannot_regress_non_shortlist():
    # A receipt whose matched note is already at interview must NOT advance/regress it,
    # and must not PROPOSE it either -- see the next test for why proposing is its own
    # defect rather than a harmless fallback. #136 Task 5c: this branch now files the
    # receipt's evidence on the note (never-clobber additive-only) rather than going
    # fully silent -- see test_receipt_for_already_applied_lead_is_skipped_not_proposed
    # for the full "why", pinned once there rather than repeated at every status this
    # branch can be reached from.
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1", status="interview")
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    assert res.status_to is None and "status: interview" in pathlib.Path(path).read_text()
    assert res.action == "skipped"
    assert res.receipt_stamped is True
    assert "## Application receipt" in pathlib.Path(path).read_text()


def test_receipt_for_already_applied_lead_is_skipped_not_proposed():
    # A matched note that can_apply already rules out must not be proposed: the only
    # runnable form of a receipt proposal is `track confirm --to applied`, which routes
    # through that SAME predicate and is refused forever, while the dead-letter row it
    # creates re-surfaces on every future run -- #49's un-runnable-hint shape. The
    # commonest producer is a second receipt for a lead this same run already advanced.
    #
    # #136 Task 5c (behavior change from this branch's prior form, which recorded
    # NOTHING here): this is the STEADY-STATE case -- a lead reaches `applied` before its
    # receipt arrives, so this fires on every ordinary confirmation, not an edge case.
    # Silence here would drop the #40 safety cover: if the model mislabelled a genuine
    # REJECTION as "receipt" and it happens to domain-match, the lead would sit at
    # `applied` forever with zero trace anywhere. So the evidence is now filed on the
    # note -- additive-only (frontmatter byte-identical), never the status.
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1",
                                  status="applied")
    before = pathlib.Path(path).read_text()
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    assert res.action == "skipped" and res.proposal is None
    assert res.status_from == "applied" and res.status_to is None
    assert res.receipt_stamped is True
    after = pathlib.Path(path).read_text()
    assert "## Application receipt" in after
    # Additive-only, never-clobber: everything ABOVE the appended section -- frontmatter
    # included -- is byte-identical to before. This is also the witness for "truly
    # additive": had the stamp path accidentally written through `status` (a regression
    # this repo has hit before via a shared write helper), this line would catch it, since
    # `before` was captured with status already at `applied` and any second write to that
    # key would still read `status: applied` today but would no longer be BYTE-IDENTICAL
    # if the write touched key order, quoting, or a trailing key.
    assert after.startswith(before)


def test_receipt_idempotent_no_double_evidence():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst"); ev.message_id = "m1"
    R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    # Re-read the now-applied note; a second identical receipt must not double-write.
    note2 = [n for n in v.read_leads() if n.slug == "Example - Analyst"][0]
    ev2 = _receipt_ev("proof", "Example - Analyst"); ev2.message_id = "m1"
    R.reconcile(ev2, {}, v, TrackConfig(), FakeGoogleClient(),
                receipt_by_slug={"Example - Analyst": note2})
    assert pathlib.Path(path).read_text().count("## Application receipt") == 1


def test_receipt_advance_writes_no_interview_fields():
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst"); ev.links = ["https://example.com/portal"]
    R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    text = pathlib.Path(path).read_text()
    assert "interview_date" not in text and "interview_link" not in text


def test_receipt_dry_run_reports_advance_but_writes_nothing():
    # dry_run must report the WOULD-BE outcome (so callers can preview it) while the
    # vault stays untouched -- `--dry-run` writing to a real note is a serious defect,
    # not a cosmetic one, so this pins the `if not dry_run:` guard on the write itself
    # rather than trusting the returned result alone.
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    before = pathlib.Path(path).read_text()
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), True, receipt_by_slug=sl)
    assert res.action == "applied" and res.status_to == "applied"
    after = pathlib.Path(path).read_text()
    assert after == before  # byte-unchanged: no frontmatter edit, no evidence section
    assert "status: shortlist" in after and "## Application receipt" not in after


def test_receipt_evidence_survives_a_status_write_conflict():
    # Write ORDER is load-bearing. Status-then-evidence meant a VaultConflict (#16) on
    # the evidence append left the lead already `applied` -- out of the shortlist set
    # match_receipt searches -- so no later run could re-attach the evidence and it was
    # lost unrecoverably. Evidence-then-status makes a conflict on EITHER write leave the
    # lead in `shortlist`: engine.run's per-message except skips seen.add and the whole
    # message retries next run.
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")

    class ConflictOnStatus(Vault):
        def update_fields(self, ref, fields, **kwargs):
            # **kwargs, not a bare (self, ref, fields): #136 Task 5d's require_status=
            # kwarg is now part of the real call this override stands in for, and a
            # narrower signature would raise TypeError instead of the VaultConflict this
            # test means to simulate.
            raise VaultConflict("concurrent edit")

    boom = ConflictOnStatus(v.dir)
    ev = _receipt_ev("proof", "Example - Analyst"); ev.message_id = "m1"
    with pytest.raises(VaultConflict):
        R.reconcile(ev, {}, boom, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    text = pathlib.Path(path).read_text()
    assert "status: shortlist" in text                  # never left the retryable state
    assert "## Application receipt" in text             # evidence already durable

    # ...and the retry completes it, without double-writing the evidence (idempotent by tag).
    v2 = Vault(v.dir)
    note2 = [n for n in v2.read_leads() if n.slug == "Example - Analyst"][0]
    ev2 = _receipt_ev("proof", "Example - Analyst"); ev2.message_id = "m1"
    res = R.reconcile(ev2, {}, v2, TrackConfig(), FakeGoogleClient(),
                      receipt_by_slug={"Example - Analyst": note2})
    text2 = pathlib.Path(path).read_text()
    assert res.action == "applied" and "status: applied" in text2
    assert text2.count("## Application receipt") == 1


def test_receipt_confidence_floor_is_inclusive():
    # The design specifies >=, i.e. a receipt AT the floor still advances; a boundary
    # value is required because 0.5/0.9 (the other tests' confidences) sit strictly
    # off the floor and can't distinguish >= from >.
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    ev = _receipt_ev("proof", "Example - Analyst", conf=TrackConfig().auto_apply_min)
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    assert res.action == "applied" and res.status_to == "applied"
    assert "status: applied" in pathlib.Path(path).read_text()


# ── #136 Task 5c/5d: a domain-matched receipt that cannot advance an in-flight
# lead still files its evidence, via _skip_with_evidence -----------------------


def test_a_domain_matched_receipt_for_an_inflight_lead_stamps_evidence_additively():
    """The steady-state case (#136): a lead reaches `applied` before its receipt
    arrives, so match_receipt's tier is real (proof or corroborated -- the branch under
    test does not care which) but can_apply already refuses the note. Going quiet used
    to record nothing anywhere; now the SAME _stamp_receipt helper the auto-advance
    path uses files the evidence, additive-only, status untouched.

    Witnessed by hand: deleting the `_stamp_receipt(...)` call from
    `_skip_with_evidence` turns this test RED (no section written, receipt_stamped
    stays False). Reverted after confirming."""
    v, notes, path = _vault_with("Example Tidal - EM", "applied")
    before = pathlib.Path(path).read_text()
    ev = _receipt_ev("corroborated", "Example Tidal - EM")
    ev.message_id = "r1"
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=notes)
    assert res.action == "skipped"
    assert res.receipt_stamped is True
    after = pathlib.Path(path).read_text()
    assert after.count("## Application receipt <!--track-receipt-r1-->") == 1
    # Additive-only: everything that existed before is still there, byte-for-byte,
    # including the frontmatter -- this is also the witness for "truly additive": had
    # the stamp path accidentally routed through a status-touching write, `before`
    # (captured pre-call, status already `applied`) would no longer be a strict PREFIX
    # of `after` even though a bare substring check on `status: applied` would still
    # pass.
    assert after.startswith(before)


def test_a_domain_matched_receipt_stamp_is_idempotent_across_two_reconcile_calls():
    """append_body_section's idempotency (already pinned for the auto-advance call site
    by test_receipt_idempotent_no_double_evidence), exercised through the NEW
    quiet-skip call site: the same message_id reconciled twice against the same
    in-flight note must leave exactly one section, never two."""
    v, notes, path = _vault_with("Example Tidal - EM", "applied")
    ev = _receipt_ev("proof", "Example Tidal - EM")
    ev.message_id = "r1"
    R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=notes)
    ev2 = _receipt_ev("proof", "Example Tidal - EM")
    ev2.message_id = "r1"
    R.reconcile(ev2, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=notes)
    assert pathlib.Path(path).read_text().count("## Application receipt") == 1


def test_a_domain_matched_receipt_dry_run_reports_the_stamp_but_writes_nothing():
    """Per _stamp_materials' existing pattern, a dry run reports what it WOULD do
    (receipt_stamped is True) while leaving the vault untouched -- a `--dry-run` that
    writes to a real note is a serious defect, not a cosmetic one.

    Witnessed by hand: deleting the `if dry_run: return True` line from
    `_stamp_receipt` turns this test RED (the note changes under dry-run). Reverted
    after confirming."""
    v, notes, path = _vault_with("Example Tidal - EM", "applied")
    before = pathlib.Path(path).read_text()
    ev = _receipt_ev("corroborated", "Example Tidal - EM")
    ev.message_id = "r1"
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), True, receipt_by_slug=notes)
    assert res.action == "skipped"
    assert res.receipt_stamped is True
    assert pathlib.Path(path).read_text() == before


def test_receipt_auto_advance_refuses_when_the_note_left_shortlist_between_read_and_write():
    """The CAS proof (mirrors tests/test_apply_record.py::
    test_record_require_status_refuses_when_the_note_left_shortlist_between_read_and_write,
    #136 Task 5d): can_apply's own check reads a SNAPSHOT (note.status, resolved before
    this call) -- byte-identical to no guard at all against a concurrent writer.
    require_status re-reads FRESH inside the CAS transform. Simulated here by writing
    "applied" to disk directly, between when `note` was resolved (still says
    "shortlist") and when reconcile's auto-advance branch writes.

    The race must not silently "succeed" over the concurrent writer: reconcile must
    neither report action="applied" (a lie -- nothing changed) nor overwrite
    last_signal on a note it no longer owns. It falls through to the same
    quiet-skip-and-stamp shape Task 5c uses for a domain-matched receipt that cannot
    advance -- which, as of the fresh read, this now is.

    Witnessed by hand: deleting `require_status=frozenset({"shortlist"})` from the
    auto-advance write turns this test RED -- the race is no longer caught,
    `last_signal` is written over the concurrent writer's status change, and
    res.action reads "applied" despite the stale read. Reverted after confirming."""
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    note = sl["Example - Analyst"]                       # STALE snapshot: still "shortlist"
    v.update_fields(note.ref, {"status": "applied"})     # a "concurrent" writer wins first
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    assert res.action != "applied"
    assert res.action == "skipped"
    assert res.receipt_stamped is True
    text = pathlib.Path(path).read_text()
    assert "last_signal" not in text     # reconcile's own attempted write never landed
    assert "status: applied" in text     # left exactly where the concurrent writer put it
    assert "## Application receipt" in text   # evidence still filed, per Task 5c's fallback


def test_receipt_auto_advance_race_reports_the_stamps_real_result_not_a_hardcoded_true():
    """Review finding for #136: the race-fallback call site passed a hardcoded
    `stamped=True` to `_skip_with_evidence` rather than the auto-advance branch's OWN
    `_stamp_receipt` return -- correct for the common case (this run's stamp genuinely
    landed), but wrong for a message reprocessed AFTER an earlier run's race: if that
    earlier run's status write raised VaultConflict (a SUSTAINED conflict, not the
    require_status refusal this test's sibling above exercises) after its own stamp had
    already landed, the message retries next run, `seen.add` never having fired. On the
    retry, THIS run's own `_stamp_receipt` call is a genuine no-op (the tag is already on
    the note from the earlier run) and correctly returns False -- but the old code
    discarded that return and hardcoded True regardless, so `res.receipt_stamped` lied
    about whether THIS call wrote anything.

    Simulated by pre-seeding the note with the exact tag `_stamp_receipt` would use for
    this event (`_receipt_ev` sets no `message_id`, so the tag falls back to `ev.type`:
    `track-receipt-receipt`), so `append_body_section`'s idempotency check makes this
    run's own append a real no-op before the race is even triggered.

    Witnessed by hand: reverting the call site to `stamped=True` (discarding
    `receipt_stamped`'s real value) turns this test RED -- `res.receipt_stamped` reads
    True even though no NEW evidence was written this call. Reverted after confirming."""
    v, sl, path = _shortlist_with("Example - Analyst", "https://example.com/careers/1")
    pre_existing = ("## Application receipt <!--track-receipt-receipt-->\n"
                    "- Received: 2026-01-01\n- From: jobs@example.com\n"
                    "- Subject: Thanks for applying\n- Match: proof")
    note = sl["Example - Analyst"]
    v.append_body_section(note.ref, "track-receipt-receipt", pre_existing)
    v.update_fields(note.ref, {"status": "applied"})     # a "concurrent" writer wins first
    ev = _receipt_ev("proof", "Example - Analyst")
    res = R.reconcile(ev, {}, v, TrackConfig(), FakeGoogleClient(), receipt_by_slug=sl)
    assert res.action == "skipped"
    assert res.receipt_stamped is False    # THIS call wrote no new evidence -- it was already there
    text = pathlib.Path(path).read_text()
    assert text.count("## Application receipt") == 1   # still exactly one section, not doubled


def test_a_body_that_names_a_different_day_than_the_header_books_nothing():
    """#202 defect 2: on the invite that misfiled an interview, the structured `When:`
    header said day A while the message's own rendered body said day B -- and the header
    was the wrong one. That was true of the FIRST message on its own, before any question
    of ordering, so a run that saw only it still had the correct date available and threw
    it away.

    Neither source is trusted over the other, because a coin-flip reproduces the failure
    half the time. Nothing is booked and the run says so. The status advance still
    happens: an interview genuinely was scheduled, and that half is not in doubt -- the
    same split the `unresolved`/`foreign` arm beside this one already makes.
    """
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    c = FakeGoogleClient(events=[])
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
               ics=_ics(),                       # header: 2026-07-20
               when="2026-07-23T10:00:00+00:00")  # body: three days later
    res = R.reconcile(ev, notes, v, TrackConfig(), c)

    assert not c.inserted and not c.updated, "booked an appointment on a disputed date"
    assert res.needs_review == "calendar-date-conflict"
    assert res.action == "applied" and res.status_to == "interview"


def test_a_body_agreeing_with_the_header_books_normally():
    """The control. Same shape, same fields -- only the body's day matches."""
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    c = FakeGoogleClient(events=[])
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
               ics=_ics(), when="2026-07-20T10:00:00+00:00")
    res = R.reconcile(ev, notes, v, TrackConfig(), c)
    assert res.calendar == "created" and res.needs_review in (None, "")
    assert c.inserted


def test_a_body_time_on_the_same_day_is_not_a_conflict():
    """Only the DAY is arbitrated. The header is authoritative for the time of day, and
    treating a differing minute as unsettled would refuse nearly every real invite --
    the model reads a rendered body, not a clock.
    """
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    c = FakeGoogleClient(events=[])
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
               ics=_ics(), when="2026-07-20T16:45:00+00:00")
    res = R.reconcile(ev, notes, v, TrackConfig(), c)
    assert res.calendar == "created" and c.inserted


def test_an_unreadable_body_datetime_is_not_a_conflict():
    """`when` is model output, so it can be anything. Unreadable means we cannot compare,
    which is not the same as a disagreement -- refusing there would block bookings on a
    parse failure and reintroduce the harm from the other side."""
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    c = FakeGoogleClient(events=[])
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
               ics=_ics(), when="next Tuesday afternoon")
    res = R.reconcile(ev, notes, v, TrackConfig(), c)
    assert res.calendar == "created" and c.inserted


def test_a_disputed_date_is_not_stamped_onto_the_note_either():
    """The calendar refusal is worth nothing if the same disputed date is written to the
    note instead -- and the note is the MORE durable of the two.

    `_advance` prefers `ev.when` over the ics start, so the conflict arm was stamping the
    model's reading of the body as `interview_date` while reporting that nothing was
    booked. It is irreversible: the lead is already at `interview`, so
    `can_advance("interview", "interview")` is False and no later run, and no
    `track confirm`, can correct it.

    NEITHER date is written, not "the header one" -- both are disputed, and picking the
    header here would be the same coin-flip the calendar arm refuses to make.
    """
    v, notes, path = _vault_with("Example Tidal - EM", "applied")
    c = FakeGoogleClient(events=[])
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
               ics=_ics(), when="2026-07-22T14:00:00+00:00")
    res = R.reconcile(ev, notes, v, TrackConfig(), c)

    text = pathlib.Path(path).read_text()
    assert "interview_date" not in text, (
        "a date nobody can vouch for was written to the note while the run reported "
        "that nothing was booked")
    # The advance itself is still right: an interview genuinely was scheduled.
    assert "status: interview" in text
    assert res.needs_review == "calendar-date-conflict"


def test_an_undisputed_interview_still_stamps_its_date():
    """The control -- withholding the stamp must be scoped to the conflict, or every
    ordinary invite loses its date."""
    v, notes, path = _vault_with("Example Tidal - EM", "applied")
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
               ics=_ics(), when="2026-07-20T10:00:00+00:00")
    R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert "interview_date" in pathlib.Path(path).read_text()


def _floating_conflict_event():
    """A floating (zone-less) header against an aware body reading, one hour earlier.

    Whether those two name the same DAY depends entirely on which zone the floating
    header is read in -- which is the point.
    """
    ics = IcsEvent(uid="u1", summary="Screen", start=datetime(2026, 7, 15, 23, 30))
    return Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9,
                 ics=ics, when="2026-07-15T22:30:00+00:00")


def test_the_date_conflict_follows_the_configured_zone_not_the_hosts():
    """A floating DTSTART has no zone, so SOMETHING has to supply one, and it must be
    `calendar_assumed_timezone` -- the knob this module already uses for exactly that --
    rather than whatever `astimezone()` picks up from the machine.

    Reading it from the host made the same invite under the same config book on one
    machine and route to a human on another, and it is invisible in CI, which runs UTC.

    The two configs must DISAGREE on this pair, or the assertion would pass on a check
    that never consults the config at all.
    """
    v, notes, _ = _vault_with("Example Tidal - EM", "applied")
    utc = TrackConfig(); utc.calendar_assumed_timezone = "UTC"
    gulf = TrackConfig(); gulf.calendar_assumed_timezone = "Asia/Dubai"

    # Read as UTC the two land on the same day; read as UTC+4 the body crosses midnight.
    r_utc = R.reconcile(_floating_conflict_event(), notes, v, utc, FakeGoogleClient(events=[]))
    v2, notes2, _ = _vault_with("Example Tidal - EM", "applied")
    r_gulf = R.reconcile(_floating_conflict_event(), notes2, v2, gulf, FakeGoogleClient(events=[]))

    assert r_utc.needs_review in (None, ""), "same day under UTC -- nothing to arbitrate"
    assert r_gulf.needs_review == "calendar-date-conflict"


def _vault_with_fields(slug, status, extra):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    (leads / f"{slug}.md").write_text(
        f'---\ncompany: "X"\nrole: "Analyst"\nstatus: {status}\n{extra}\n---\n\nBODY\n')
    v = Vault(root)
    note = [n for n in v.read_leads() if n.slug == slug][0]
    return v, {slug: note}, str(leads / f"{slug}.md")


def test_a_disputed_date_leaves_an_existing_value_alone():
    """A conflict must not become a licence to DELETE.

    An earlier round cleared `interview_date` here, reasoning that a value carried over
    from a previous stage is misleading under the new status. It cannot tell that value
    apart from one a human typed in Obsidian or an operator set with
    `track confirm --when`: there is no provenance key on this field, and all three
    writers look identical afterwards. Measured, it destroyed an operator-typed date, and
    nothing could restore it -- `can_advance("interview", "interview")` is False, so no
    later invite reaches `_advance`, and `track confirm --to interview` refuses.

    Never-clobber decides it: the vault is the user's own directory and hand-editing it is
    a first-class workflow, so a value sluice cannot prove it owns is not sluice's to
    remove. The staleness this replaced was a REPORTING problem, and it is answered by
    reporting -- the hint now says the existing date is untouched and may pre-date the
    invite. A visible stale value is recoverable; a deleted one is not.
    """
    v, notes, path = _vault_with_fields("Example Tidal - EM", "phone_screen",
                                        'interview_date: "2026-07-10T09:00:00"')
    ics = IcsEvent(uid="u1", summary="Screen",
                   start=datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc))
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=ics,
               when="2026-08-24T14:00:00+00:00")
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))

    text = pathlib.Path(path).read_text()
    assert res.needs_review == "calendar-date-conflict"
    assert 'interview_date: "2026-07-10T09:00:00"' in text, (
        "a value sluice cannot prove it wrote was destroyed")
    assert "2026-08-21" not in text and "2026-08-24" not in text, (
        "neither disputed candidate may be recorded")
    assert "status: interview" in text


def test_a_date_conflict_does_not_ALSO_come_out_as_a_proposal():
    """Round 2. `_NEEDS_REVIEW_HINT`'s own comment records why there is no collision
    guard at the record site: "every reason here comes from a branch that files nothing
    else". `calendar-date-conflict` broke that -- it leaves `calendar` at `none`, so a
    lead that cannot advance took the `proposed` arm too, and engine.run recorded twice
    for one message: the calendar row overwrote the proposal row, while `rep.proposed`
    had already counted a proposal no row records.

    Restore the invariant at the source rather than adding the guard back.

    Named for the ACTION, which is what it inspects. The row COUNT it originally claimed
    never differed either way -- `_record_replacing` replaces by message_id, so the
    calendar row silently overwrote the proposal row -- and what actually differs is the
    `rep.proposed` counter, which only `engine.run` produces. That is asserted by
    `tests/test_track_unresolved_routing.py::test_a_date_conflict_on_a_lead_that_cannot
    _advance_counts_no_proposal`.
    """
    v, notes, _ = _vault_with("Example Tidal - EM", "shortlist")   # cannot advance
    ics = IcsEvent(uid="u1", summary="Screen",
                   start=datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc))
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=ics,
               when="2026-08-24T14:00:00+00:00")
    res = R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))

    assert res.needs_review == "calendar-date-conflict"
    assert res.action != "proposed", (
        "a conflict must not ALSO file a proposal -- two rows for one message, and the "
        "second overwrites the first")


def test_a_disputed_date_does_not_destroy_an_operator_typed_date():
    """The case that settles it, driven through the field an operator actually sets.

    `track confirm --when` and a human editing the note in Obsidian both land in
    `interview_date` with no marker distinguishing them from a value sluice wrote. If a
    conflict may clear the field, it may delete either of those, irreversibly and without
    saying so -- the hint speaks only about the calendar.
    """
    v, notes, path = _vault_with_fields("Example Tidal - EM", "applied",
                                        'interview_date: "2026-09-25T15:00:00"')
    ics = IcsEvent(uid="u1", summary="Screen",
                   start=datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc))
    ev = Event(lead_slug="Example Tidal - EM", type="interview", confidence=0.9, ics=ics,
               when="2026-08-24T14:00:00+00:00")
    R.reconcile(ev, notes, v, TrackConfig(), FakeGoogleClient(events=[]))
    assert 'interview_date: "2026-09-25T15:00:00"' in pathlib.Path(path).read_text()
