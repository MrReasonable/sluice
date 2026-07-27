"""#9: `sluice leads expire` -- report-first, human-gated staleness dismissal.

Fixture notes are built through `Vault.upsert` so their slugs are REAL store-issued
filenames (`Example Ltd - Example Role`), not hand-written hyphenated strings. That
distinction is load-bearing: the report prints the store slug and `--expire` matches it
exactly, so a test that invented its own slug format would pass while the shipped command
matched nothing.
"""
import pytest

from sluice.cli import _build_parser
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.protocols import VaultConflict
from sluice.core.vault import Vault

TODAY = "2026-07-27"
ANCIENT = "2026-01-01"      # 207 days before TODAY
FRESH = "2026-07-20"        # 7 days before TODAY


def _lead(company="Example Ltd", title="Example Role", url="https://example.invalid/1"):
    return Lead(source="s", search="q", title=title, company=company, url=url)


def _app(tmp_path, ttl=90, **kw):
    return Sluice(Config(lead_ttl_days=ttl), store=Vault(str(tmp_path)),
                  today=lambda: TODAY, **kw)


def _seed(tmp_path, *, status="shortlist", last_seen=ANCIENT, first_seen="2025-12-01",
          title="Example Role", company="Example Ltd", url="https://example.invalid/1",
          **extra):
    """Create one lead note and set its status/dates. Returns its store slug."""
    v = Vault(str(tmp_path))
    v.upsert(_lead(company=company, title=title, url=url))
    # By URL, not by slug prefix: `Example Ltd - Example Role Two.md` sorts BEFORE
    # `Example Ltd - Example Role.md` (space < dot), so a prefix-and-take-last lookup
    # silently re-seeds the wrong note.
    note = next(n for n in v.read_leads() if n.fm.get("url", "") == url)
    fields = {"status": status, "last_seen": last_seen, "first_seen": first_seen}
    fields.update(extra)
    v.update_fields(note.ref, fields)
    return note.slug


# ── the off state ────────────────────────────────────────────────────────────

def test_unset_ttl_reports_nothing(tmp_path):
    # An unconfigured install must expire nothing at all. Reporting "0 stale" here would
    # be indistinguishable from "nothing is stale" and would let a user believe a knob
    # they never set is protecting them -- the CLI prints a distinct message instead.
    _seed(tmp_path, last_seen="2020-01-01")
    assert _app(tmp_path, ttl=0).expire_report() == []


def test_unset_ttl_writes_nothing_even_when_asked(tmp_path):
    slug = _seed(tmp_path, last_seen="2020-01-01")
    app = _app(tmp_path, ttl=0)
    app.expire(slugs=[])
    assert Vault(str(tmp_path)).read_leads()[0].status == "shortlist"
    assert slug  # the note exists; it simply was never stale


# ── the report ───────────────────────────────────────────────────────────────

def test_stale_lead_is_reported_with_its_age_and_prior_status(tmp_path):
    slug = _seed(tmp_path, status="shortlist", last_seen=ANCIENT)
    report = _app(tmp_path).expire_report()
    assert [r.slug for r in report] == [slug]
    assert report[0].status == "shortlist"
    assert report[0].days == 207
    assert report[0].first_seen == "2025-12-01"


def test_report_writes_nothing(tmp_path):
    _seed(tmp_path)
    _app(tmp_path).expire_report()
    assert Vault(str(tmp_path)).read_leads()[0].status == "shortlist"


def test_fresh_lead_is_not_reported(tmp_path):
    _seed(tmp_path, last_seen=FRESH)
    assert _app(tmp_path).expire_report() == []


@pytest.mark.parametrize("status", ["new", "shortlist", "research", "needs_review"])
def test_every_expirable_triage_status_is_reported(tmp_path, status):
    _seed(tmp_path, status=status)
    assert [r.status for r in _app(tmp_path).expire_report()] == [status]


@pytest.mark.parametrize("status", ["new", "shortlist", "research", "needs_review"])
def test_every_expirable_triage_status_is_actually_WRITTEN(tmp_path, status):
    # The report parametrize above only proves each status is SEEN. `require_status` is
    # the write-side guard, and it is the same set -- so a mismatch between the read
    # filter and the guard would abstain silently on three of these four.
    slug = _seed(tmp_path, status=status)
    assert _app(tmp_path).expire(slugs=[]) == [(slug, "dismissed")]
    assert Vault(str(tmp_path)).read_leads()[0].status == "dismiss"


def test_EXPIRABLE_is_derived_from_the_status_vocabulary(tmp_path):
    # Pins the derivation rather than the values: _EXPIRABLE is both the read filter and
    # the require_status never-regress guard, so a hand-maintained copy drifting from
    # core/status.py would silently narrow or widen both at once.
    from sluice.core import status as _status
    from sluice.core.app import _EXPIRABLE
    assert _EXPIRABLE == frozenset(_status.TRIAGE_OWNED) - {"dismiss"}
    assert not (_EXPIRABLE & frozenset(_status.APPLICATION_OWNED)), \
        "an expirable status must never be application-owned"


def test_already_dismissed_lead_is_skipped(tmp_path):
    # `dismiss` is the destination, so re-reporting it is noise.
    _seed(tmp_path, status="dismiss")
    assert _app(tmp_path).expire_report() == []


@pytest.mark.parametrize("status", ["applied", "phone_screen", "interview", "offer",
                                    "rejected", "accepted", "withdrawn"])
def test_application_owned_lead_is_never_enumerated(tmp_path, status):
    # never-regress: a lead you have engaged with is not stale because the posting went
    # quiet. These are never even READ, so the guard here is structural.
    _seed(tmp_path, status=status, last_seen="2020-01-01")
    assert _app(tmp_path).expire_report() == []


# ── the write ────────────────────────────────────────────────────────────────

def test_bulk_expire_dismisses_the_reported_set(tmp_path):
    _seed(tmp_path, title="Example Role", url="https://example.invalid/1")
    _seed(tmp_path, title="Example Role Two", url="https://example.invalid/2")
    outcomes = _app(tmp_path).expire(slugs=[])
    assert sorted(o for _, o in outcomes) == ["dismissed", "dismissed"]
    assert {n.status for n in Vault(str(tmp_path)).read_leads()} == {"dismiss"}


def test_expire_records_the_prior_status_in_the_audit_note(tmp_path):
    _seed(tmp_path, status="shortlist", last_seen=ANCIENT)
    _app(tmp_path).expire(slugs=[])
    note = Vault(str(tmp_path)).read_leads()[0]
    notes = note.fm.get("relevance_notes", "")
    assert "Was: shortlist" in notes, "the prior status is the only record of what to restore"
    assert "207d" in notes and "lead_ttl_days=90" in notes


def test_audit_note_is_idempotent_within_a_day(tmp_path):
    """The note_tag guard, exercised DIRECTLY.

    Going through `expire` twice cannot test this: the second sweep finds the lead already
    `dismiss`, so it is not reported and no second write is attempted -- the test would
    pass with note_tag removed entirely. Drive the same tagged write twice instead.
    """
    _seed(tmp_path, status="shortlist")
    v = Vault(str(tmp_path))
    ref = v.read_leads()[0].ref
    tag = "[expire 2026-07-27]"
    for _ in range(2):
        v.update_fields(ref, {"status": "dismiss"},
                        append_note=f"{tag} stale: ...", note_tag=tag)
    notes = Vault(str(tmp_path)).read_leads()[0].fm.get("relevance_notes", "")
    assert notes.count(tag) == 1, "a same-day re-run must not append the audit note twice"


# ── slug matching ────────────────────────────────────────────────────────────

def test_named_slug_matches_EXACTLY_not_by_substring(tmp_path):
    # `slug_matches` is a SUBSTRING match whose two existing callers already disagree
    # about ambiguity. Neither behaviour is acceptable for a bulk status write: a user
    # typing the narrow form is choosing the SAFER option and must not hit more leads.
    short = _seed(tmp_path, title="Example Role", url="https://example.invalid/1")
    long_ = _seed(tmp_path, title="Example Role Senior", url="https://example.invalid/2")
    assert short in long_ or long_.startswith(short), "fixture must actually be a prefix"

    _app(tmp_path).expire(slugs=[short])

    by_slug = {n.slug: n.status for n in Vault(str(tmp_path)).read_leads()}
    assert by_slug[short] == "dismiss"
    assert by_slug[long_] == "shortlist", "an exact match must not sweep up its superstrings"


def test_unmatched_named_slug_reports_no_match_and_writes_nothing(tmp_path):
    _seed(tmp_path)
    outcomes = _app(tmp_path).expire(slugs=["Nothing - Like This"])
    assert outcomes == [("Nothing - Like This", "no-match")]
    assert Vault(str(tmp_path)).read_leads()[0].status == "shortlist"


def test_named_slug_that_is_not_stale_is_refused(tmp_path):
    # --expire narrows the reported set; it is not a licence to dismiss any lead by name.
    slug = _seed(tmp_path, last_seen=FRESH)
    outcomes = _app(tmp_path).expire(slugs=[slug])
    assert outcomes == [(slug, "no-match")]
    assert Vault(str(tmp_path)).read_leads()[0].status == "shortlist"


# ── the sign-off hold ────────────────────────────────────────────────────────

def test_pending_cv_lead_is_refused_by_bulk(tmp_path):
    # Dismissing it strands the hold permanently: sign_off_cv resolves through
    # read_leads({"shortlist"}), so `cv signoff` and `--discard` both stop finding it.
    slug = _seed(tmp_path, pending_cv="CV-2026.pdf")
    outcomes = _app(tmp_path).expire(slugs=[])
    assert outcomes == [(slug, "refused-signoff")]
    assert Vault(str(tmp_path)).read_leads()[0].status == "shortlist"


def test_pending_cv_lead_is_refused_when_named_explicitly(tmp_path):
    slug = _seed(tmp_path, pending_cv="CV-2026.pdf")
    assert _app(tmp_path).expire(slugs=[slug]) == [(slug, "refused-signoff")]


def test_needs_signoff_WITHOUT_pending_cv_is_NOT_refused(tmp_path):
    # Vault.sign_off returns a no-op without pending_cv and only clears needs_signoff on
    # that same branch. Refusing on needs_signoff alone would strand such a note forever
    # behind a message whose escape hatch (`cv signoff --discard`) does nothing at all.
    slug = _seed(tmp_path, needs_signoff='["a claim"]')
    assert _app(tmp_path).expire(slugs=[]) == [(slug, "dismissed")]


def test_tailored_cv_is_flagged_but_not_refused(tmp_path):
    # A completed CV strands nothing when the lead is dismissed -- it is informational
    # (you spent a compose on this one), not a refusal.
    slug = _seed(tmp_path, tailored_cv="CV-2026.pdf")
    report = _app(tmp_path).expire_report()
    assert report[0].flagged == ["cv"]
    assert report[0].refused is None
    assert _app(tmp_path).expire(slugs=[]) == [(slug, "dismissed")]


# ── concurrency ──────────────────────────────────────────────────────────────

def test_a_lead_that_becomes_applied_MID_SWEEP_is_not_dismissed(tmp_path, monkeypatch):
    """The ONLY witness for `require_status`.

    An `is_application_owned(note.status)` guard on the enumerated LeadNote is
    byte-identical to having NO guard: the snapshot is stale by construction. The racer
    must fire on the ENUMERATION read, because `racing_read` returns pre-edit bytes --
    installed any later, even a fresh-re-read guard would still see `shortlist`.
    """
    from tests.conftest import racing_read
    slug = _seed(tmp_path, status="shortlist", last_seen=ANCIENT)
    v = Vault(str(tmp_path))
    ref = v.read_leads()[0].ref

    def _apply_concurrently():
        Vault(str(tmp_path)).update_fields(ref, {"status": "applied"})

    racing_read(monkeypatch, ref, _apply_concurrently)
    outcomes = _app(tmp_path).expire(slugs=[])

    assert Vault(str(tmp_path)).read_leads()[0].status == "applied", \
        "a lead that entered the application lifecycle mid-sweep must survive"
    # And it must be REPORTED as not-dismissed. Discarding this return value left
    # `"dismissed" if wrote else "skipped"` -> `"dismissed"` passing the whole suite:
    # the lead was correctly protected and then announced as `expire: 1 dismissed`,
    # exit 0. A silent failure reporting success, behind a test named for the race.
    assert outcomes == [(slug, "skipped")]


def test_vault_conflict_on_one_lead_does_not_abort_the_sweep(tmp_path, monkeypatch):
    _seed(tmp_path, title="Example Role", url="https://example.invalid/1")
    _seed(tmp_path, title="Example Role Two", url="https://example.invalid/2")
    app = _app(tmp_path)
    store = app.store()
    real = store.update_fields
    calls = {"n": 0}

    def flaky(ref, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise VaultConflict(ref)
        return real(ref, fields, **kw)

    monkeypatch.setattr(store, "update_fields", flaky)
    outcomes = dict((s, o) for s, o in app.expire(slugs=[]))
    assert sorted(outcomes.values()) == ["conflict", "dismissed"], \
        "one conflicting note must not abort the sweep over the rest"


# ── the CLI parse layer ──────────────────────────────────────────────────────

def test_bare_expire_flag_parses_to_the_bulk_case():
    """THE PARSE-LAYER TEST. Every test above sits at the Sluice.expire() level and would
    stay green through a broken parser.

    `leads dedupe`'s `--merge nargs="+"` REQUIRES an argument, so a bare `--expire` would
    be an argparse error rather than the bulk case; and dedupe's `if args.merge:` dispatch
    would drop it anyway, because a bare flag parses to a FALSY []."""
    p = _build_parser()
    assert p.parse_args(["leads", "expire"]).expire is None
    assert p.parse_args(["leads", "expire", "--expire"]).expire == []
    assert p.parse_args(["leads", "expire", "--expire", "A", "B"]).expire == ["A", "B"]


def test_expire_dispatch_distinguishes_absent_from_bulk():
    # `is not None`, never truthiness: [] is the bulk case and is falsy.
    p = _build_parser()
    assert (p.parse_args(["leads", "expire"]).expire is not None) is False
    assert (p.parse_args(["leads", "expire", "--expire"]).expire is not None) is True


# ── the sign-off hold must stay reachable from EVERY triage status ───────────

@pytest.mark.parametrize("status", ["new", "shortlist", "research", "needs_review",
                                    "dismiss"])
def test_a_held_lead_can_be_discharged_from_any_triage_status(tmp_path, status):
    """`cv signoff --discard` must find a held lead wherever triage has left it.

    `dismiss` is the case that matters and the one a narrower lookup misses: it is the
    single triage verdict `_EXPIRABLE` omits (being expire's own destination), and it is
    the most likely demotion for a lead a re-run of `sluice triage run` has reconsidered.
    Resolved over anything narrower, `cv signoff` reports no match for a hold that
    demonstrably exists, and the pending CV is unresolvable except by hand-editing
    frontmatter.
    """
    slug = _seed(tmp_path, status=status, pending_cv="CV-2026.pdf",
                 needs_signoff='["a claim"]')
    got = _app(tmp_path).sign_off_cv(lead=slug, accept=False)
    assert got == (slug, "discarded"), f"a held lead at status={status} was unreachable"
    fm = Vault(str(tmp_path)).read_leads()[0].fm
    assert "pending_cv" not in fm and "needs_signoff" not in fm
