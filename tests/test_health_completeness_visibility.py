"""Completeness that is MEASURED but not classified, and the blind spot that made it necessary.

`blank` (#156) judges a run's company/link completeness against the source's OWN sticky
high-water, and skips any signal whose high-water never cleared `_BLANK_HW_MIN`. That is
right for a board which genuinely does not publish a field -- weworkremotely's extractor
hardcodes an empty company, and firing on it every run would withhold a healthy source's
leads forever. It is silently WRONG for a board that was already broken when its first run
was recorded: the high-water only ever climbs, so such a source never establishes a bar to
fall from and is exempt for good.

That is not hypothetical. Measured 2026-08-27, reed's company high-water was 0.1, taken from
a run whose extractor was already reading the wrong elements -- so the one check that would
have reported the collapse was, by construction, switched off for exactly the source that
needed it. And reed's location, lost on 100% of rows, was measured by nothing at all: the
completeness vocabulary was company and link, both of which reed kept.

Two changes, and the split between them is the load-bearing part:

  - `location_rate` is measured and high-watered, so field loss is visible.
  - `BLANK_SIGNALS` is a strict SUBSET of `RATE_SIGNALS`, so location cannot withhold a run.

The alternative -- lowering the floor, or adding an absolute one -- was rejected rather than
overlooked: `blank` is in `BREAKER_REASONS`, so it bins every lead the source produced, and a
board that legitimately lacks a field would be binned daily. The exemption is surfaced to a
human instead (`ingest list-sources --health`), because nothing local can tell "does not
publish this field" from "stopped reading this field".
"""
from sluice.cli import _build_parser, cmd_list_sources
from sluice.core.config import Config
from sluice.core.health import (
    BLANK_SIGNALS,
    RATE_SIGNALS,
    HealthStore,
    detect_drift,
)
from sluice.ingest.engine import _lead_rates
from sluice.ingest import sources as registry
from sluice.core.leads import Lead


def _leads(n, *, company="Example Co", location="Example Location", url="https://e.invalid/1"):
    return [Lead(source="s", search="x", title=f"Role {i}",
                 company=company, location=location, url=url) for i in range(n)]


# ---- the measured set vs the classifying set ----------------------------------------

def test_the_classifying_set_is_a_strict_subset_of_the_measured_set():
    # The whole safety property in one line: everything `blank` can act on is measured,
    # and something measured is deliberately NOT actionable. Asserting subset alone would
    # pass if the two were equal, which is the state this change exists to leave.
    assert set(BLANK_SIGNALS) < set(RATE_SIGNALS), (
        "BLANK_SIGNALS must be a STRICT subset -- if it equals RATE_SIGNALS then every "
        "measured signal can withhold a run again, which is what adding location must not do"
    )


def test_location_is_measured_but_never_classified_on():
    # Named directly rather than left implied by the subset test: this is the specific
    # decision a future edit is most likely to undo by "tidying" the two tuples into one.
    assert "location_rate" in RATE_SIGNALS
    assert "location_rate" not in BLANK_SIGNALS


def test_lead_rates_reports_location_completeness():
    rates = _lead_rates(_leads(10, location=""))
    assert rates["location_rate"] == 0.0
    assert _lead_rates(_leads(10))["location_rate"] == 1.0


def test_a_total_location_collapse_alone_never_withholds_a_run():
    """The reed shape, and the reason location is not in `BLANK_SIGNALS`.

    A source with a perfect company/link record whose location falls to zero must classify
    healthy: `blank` withholds every lead it fires on, and a lead that kept its title,
    company and link is still worth having.
    """
    signals = {"company_rate": 1.0, "link_rate": 1.0, "location_rate": 0.0}
    highs = {"company_rate": 1.0, "link_rate": 1.0, "location_rate": 1.0}
    priors = dict(signals)
    assert detect_drift("reed", 20, signals, 20.0,
                        rate_highs=highs, rate_priors=priors) is None


def test_a_company_collapse_still_withholds_so_the_subset_did_not_disarm_blank():
    # The other half, and not redundant: the test above passes vacuously if `blank` stopped
    # firing altogether. Same shape, company collapsed instead of location.
    signals = {"company_rate": 0.0, "link_rate": 1.0, "location_rate": 1.0}
    highs = {"company_rate": 1.0, "link_rate": 1.0, "location_rate": 1.0}
    assert detect_drift("reed", 20, signals, 20.0,
                        rate_highs=highs, rate_priors=dict(signals)) == "blank"


# ---- the blind spot itself ----------------------------------------------------------

def test_a_source_whose_high_water_never_cleared_the_floor_is_reported_unguarded():
    h = HealthStore()   # sandboxed by the autouse fixture in conftest
    # reed's real 2026-08-27 state: already broken when first recorded, so its high-water
    # is established AT the broken rate and it can never fall relative to itself.
    h.record("reed", 20, {"company_rate": 0.1, "link_rate": 1.0})
    assert h.unguarded_signals("reed") == ["company_rate"]


def test_the_blind_spot_this_names_is_real_blank_cannot_fire_for_such_a_source():
    """Not a claim about the flag -- a claim about `blank`, executed.

    A source pinned at its own broken high-water stays classified healthy no matter how far
    the rate sits below a sane absolute bar. If this ever returns "blank", the flag has
    become a lie and should be deleted rather than kept.
    """
    h = HealthStore()
    h.record("reed", 20, {"company_rate": 0.1, "link_rate": 1.0})
    highs = h.rate_highs("reed")
    signals = {"company_rate": 0.0, "link_rate": 1.0}   # total collapse, worse than ever
    assert detect_drift("reed", 20, signals, 20.0,
                        rate_highs=highs, rate_priors=dict(signals)) is None


def test_a_healthy_source_is_not_reported_unguarded():
    h = HealthStore()
    h.record("cord", 24, {"company_rate": 1.0, "link_rate": 1.0})
    assert h.unguarded_signals("cord") == []


# A run below `_RATE_ROW_FLOOR` still records the non-rate signals every run carries, so a
# realistic thin run is NOT `{}`. Using an empty dict made the `RATE_SIGNALS` filter in
# `latest_rates` untestable: with the filter deleted, `{}` stays falsy and the tests below
# passed anyway, while a REAL thin run made `rates` truthy and printed UNGUARDED for a source
# that had never been measured. Found by mutation; keep these fixtures realistic.
_THIN_RUN = {"landed_host": "example.invalid", "requested_host": "example.invalid",
             "landed_path": "/jobs", "requested_path": "/jobs"}


def test_latest_rates_walks_back_past_a_run_that_carried_no_rates():
    """`_lead_rates` withholds every rate key below its row floor, so the newest run
    frequently carries none. Reading `runs[-1]` would report a measured source as
    unmeasured -- a completeness collapse where there was only a small page."""
    h = HealthStore()
    h.record("bayt", 20, {"company_rate": 1.0, "link_rate": 1.0})
    h.record("bayt", 3, _THIN_RUN)     # under the row floor: no rate keys
    rates, age = h.latest_rates("bayt")
    assert rates == {"company_rate": 1.0, "link_rate": 1.0}
    # The AGE, not just the values: reporting a one-run-old rate as though it were this
    # run's is the stale-100% failure the vintage exists to remove.
    assert age == 1


def test_latest_rates_reports_a_current_measurement_as_age_zero():
    # 0 is a real answer ("this run"), which is why the not-measured sentinel is -1 and not
    # 0 -- a caller testing truthiness of the age would otherwise read the two the same way.
    h = HealthStore()
    h.record("bayt", 20, {"company_rate": 1.0, "link_rate": 1.0})
    assert h.latest_rates("bayt")[1] == 0


def test_latest_rates_is_empty_for_a_source_that_was_never_measured():
    # Absent, not zero -- the same discipline `rate_highs` states. A source that never
    # cleared the row floor has not been measured at 0%, it has not been measured.
    h = HealthStore()
    h.record("bayt", 3, _THIN_RUN)
    assert h.latest_rates("bayt") == ({}, -1)


# ---- what a human actually reads ----------------------------------------------------

def _health_lines(capsys):
    args = _build_parser().parse_args(["ingest", "list-sources", "--health"])
    assert cmd_list_sources(args, Config()) == 0
    return {ln.split()[0]: ln for ln in capsys.readouterr().out.splitlines() if ln.split()}


def test_the_health_listing_shows_the_completeness_rates(capsys):
    h = HealthStore()
    h.record("reed", 20, {"company_rate": 1.0, "link_rate": 1.0, "location_rate": 1.0})
    line = _health_lines(capsys)["reed"]
    assert "company=100%" in line and "link=100%" in line and "location=100%" in line


def test_the_health_listing_flags_an_unguarded_signal_by_name(capsys):
    h = HealthStore()
    h.record("reed", 20, {"company_rate": 0.1, "link_rate": 1.0, "location_rate": 0.0})
    line = _health_lines(capsys)["reed"]
    assert "company=10%" in line, line
    # By NAME, not a bare flag: "this source has a blind spot" is not actionable, "company
    # is outside the guard" tells a reader which selector to go and look at.
    assert "UNGUARDED(company)" in line, line


def test_an_unmeasured_source_is_reported_unmeasured_not_unguarded(capsys):
    """"Not measured" and "measured and outside the guard" are different claims, and
    printing the first as the second is how a report trains its reader to skip a column.

    The thin run carries REALISTIC non-rate signals. With `{}` the `RATE_SIGNALS` filter in
    `latest_rates` could be deleted and this still passed, while a real thin run printed
    `UNGUARDED(company,link)` for a source nobody had ever measured.
    """
    h = HealthStore()
    h.record("reed", 3, _THIN_RUN)     # below the row floor -- no rate keys recorded
    line = _health_lines(capsys)["reed"]
    assert "UNGUARDED" not in line, line
    # Said out loud rather than left as a blank: with no rate this run, `prior_rate` is None
    # for every key and `blank` cannot fire at all, so the source is genuinely not guarded.
    assert "UNMEASURED" in line, line


def test_a_stale_rate_never_implies_the_guard_is_live(capsys):
    """A rate that is merely STALE is not coverage either.

    `blank` compares THIS run's rate against the high-water, so when the newest run recorded
    no rate it returns None whatever the history says -- executed below, not read off the
    code. An earlier version gated the flag on `latest_rates` being non-empty, so a source
    with a healthy rate from three runs ago rendered with no UNMEASURED and, if its
    high-water was low, a confident UNGUARDED -- both of which describe a guard that is not
    running. Gating on `age != 0` covers the stale case and the never-measured one alike.
    """
    assert detect_drift("reed", 20, dict(_THIN_RUN), 20.0,
                        rate_highs={"company_rate": 1.0, "link_rate": 1.0},
                        rate_priors={"company_rate": 0.0, "link_rate": 0.0}) is None, \
        "blank fired on a run carrying no rate -- the premise of the age gate is wrong"

    h = HealthStore()
    # A LOW high-water, so the stale-rate path would otherwise print UNGUARDED(company).
    h.record("reed", 20, {"company_rate": 0.1, "link_rate": 1.0})
    h.record("reed", 3, _THIN_RUN)
    line = _health_lines(capsys)["reed"]
    assert "UNMEASURED" in line, line
    assert "UNGUARDED" not in line, (
        "a stale rate produced a guarded-ness verdict for a run that recorded no rate: " + line)


def test_a_stale_rate_is_never_printed_as_though_it_were_this_runs(capsys):
    """The failure the vintage exists to remove: a rate up to `_KEEP` runs old rendered as a
    bare percentage reads as a healthy current measurement, which is exactly the reassuring
    answer a rotted extractor produces to the command run to catch it."""
    h = HealthStore()
    h.record("reed", 20, {"company_rate": 1.0, "link_rate": 1.0, "location_rate": 1.0})
    for _ in range(3):
        h.record("reed", 3, _THIN_RUN)
    line = _health_lines(capsys)["reed"]
    assert "company=100%" in line, line
    assert "(3 runs ago)" in line, line


def test_a_board_that_does_not_publish_a_field_can_declare_it_and_stop_the_flag(capsys):
    """Without this, `weworkremotely` and `eighty_k` -- both of which hardcode an empty
    company because the board has none to read -- light UNGUARDED(company) on every
    invocation for ever, and a flag permanently lit on known-benign rows is how a reader
    learns to skip the column."""
    # ACCEPTED GAP, stated rather than papered over. This derives the roster from the
    # declarations, so it cannot see a declaration being REMOVED -- measured: deleting
    # weworkremotely's leaves the suite green, because the source simply drops out of the list
    # being checked. Two derivations from the extractor TEXT were tried and both are wrong:
    # `company:''` appears in cwjobs' and totaljobs' degraded FALLBACK branches while their
    # primary path reads a real company, so keying on it demands a declaration from two boards
    # that publish one perfectly well. Nothing textual distinguishes "this board has no
    # company to read" from "this is the fallback tier".
    #
    # Left uncovered on purpose: the cost of a wrongly-removed declaration is that a cosmetic
    # flag returns to a diagnostic command, visible immediately in the very output this test
    # is about -- not lost data and not a wrong verdict. A fragile text-matching guard that
    # false-fails CI on an unrelated extractor edit is the worse trade. What IS covered below
    # is the MECHANISM: that declaring silences the named field, and only the named field.
    declared = [s.id for s in registry.all_sources()
                if getattr(s, "unpublished_fields", ())]
    assert declared, "no source declares unpublished_fields -- this guard proves nothing"
    h = HealthStore()
    for sid in declared:
        h.record(sid, 20, {"company_rate": 0.0, "link_rate": 1.0})
    lines = _health_lines(capsys)
    for sid in declared:
        assert "UNGUARDED" not in lines[sid], lines[sid]
    # And the declaration is NARROW: it silences only the field named. A source with the
    # same collapsed rate and no declaration still reports, or this would be a blanket mute.
    h.record("reed", 20, {"company_rate": 0.0, "link_rate": 1.0})
    assert "UNGUARDED(company)" in _health_lines(capsys)["reed"]


def test_a_disabled_source_is_not_called_unguarded(capsys):
    """The flag is a claim about a LIVE guard. A disabled source runs nothing, so there is
    no guard for it to be blind -- and `hired`/`bwork`/`theorg` would otherwise carry the
    flag permanently, which is the standing noise #207 objects to."""
    disabled = sorted(s.id for s in __import__(
        "sluice.ingest.sources", fromlist=["*"]).all_sources()
        if not getattr(s, "enabled", True))
    assert disabled, "no disabled source in the registry -- this guard would prove nothing"
    h = HealthStore()
    h.record(disabled[0], 20, {"company_rate": 0.0, "link_rate": 1.0})
    assert "UNGUARDED" not in _health_lines(capsys)[disabled[0]]


def test_the_rate_fragments_are_derived_from_the_roster_not_hand_listed(capsys):
    """Every measured signal must reach the line. A hand-written format string is how a
    fourth signal gets added to `RATE_SIGNALS` and silently never printed."""
    h = HealthStore()
    h.record("reed", 20, {key: 1.0 for key in RATE_SIGNALS})
    line = _health_lines(capsys)["reed"]
    for key in RATE_SIGNALS:
        assert f"{key.removesuffix('_rate')}=" in line, f"{key} is measured but never shown"
