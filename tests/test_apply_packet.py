import json
from types import SimpleNamespace

import pytest

from sluice.apply.config import ApplyConfig
from sluice.apply import packet
from sluice.apply.packet import build_packet, render_text, resolve_how_heard
from sluice.core.protocols import CandidateProfile


def _note(**fm):
    return SimpleNamespace(fm=fm, path="/v/Job Leads/Example Northgate - Analyst.md")


def _cfg():
    return ApplyConfig()


def test_listing_host_table():
    assert packet.listing_host("https://uk.linkedin.com/jobs/view/123") == "linkedin"
    assert packet.listing_host("https://uk.indeed.com/rc/clk?jk=1") == "indeed"
    assert packet.listing_host("https://job-boards.greenhouse.io/x/jobs/9") == "greenhouse"
    assert packet.listing_host("https://jobs.ashbyhq.com/x/abc") == "ashby"
    assert packet.listing_host("https://jobs.lever.co/x/abc") == "lever"
    assert packet.listing_host("https://apply.workable.com/x/") == "workable"
    assert packet.listing_host("https://careers.icims.com/x") == "icims"
    assert packet.listing_host("https://x.teamtailor.com/jobs/9") == "teamtailor"
    assert packet.listing_host("https://example-northgate.invalid/careers/em") == "other"


def test_build_packet_cv_path_only_when_staged():
    cfg = _cfg()
    n = _note(company="Example Northgate", role="Analyst", location="Example City", salary="", url="https://example-northgate.invalid/x")
    staged = build_packet(n, cfg, profile=CandidateProfile(), today="2026-08-19", cv_staged=True)
    assert staged["cv_path"] == "./cv-uploads/CV.pdf"
    preview = build_packet(n, cfg, profile=CandidateProfile(), today="2026-08-19", cv_staged=False)
    assert preview["cv_path"] is None
    assert preview["listing_host"] == "other"


def test_render_text_has_rules_and_no_em_dash():
    cfg = _cfg()
    n = _note(company="Example Northgate", role="Analyst", location="Example City", salary="", url="https://example-northgate.invalid/x")
    text = render_text(build_packet(n, cfg, profile=CandidateProfile(), today="2026-08-19", cv_staged=True))
    assert "\u2014" not in text and "--" not in text
    assert "never" in text.lower() and "one-click" in text.lower()
    assert "first name" in text.lower()
    assert "submit" in text.lower()
    assert "job-application-workflow" in text


def test_render_text_preview_mode_no_dashes():
    cfg = _cfg()
    n = _note(company="Example Northgate", role="Analyst", location="", salary="", url="https://example-northgate.invalid/x")
    text = render_text(build_packet(n, cfg, profile=CandidateProfile(), today="2026-08-19", cv_staged=False))
    assert "\u2014" not in text and "--" not in text
    assert "stag" in text.lower()  # still tells the user to stage the CV first


def test_render_json_roundtrips():
    cfg = _cfg()
    n = _note(company="Example Northgate", role="Analyst", location="", salary="", url="https://example-northgate.invalid/x")
    d = json.loads(packet.render_json(build_packet(n, cfg, profile=CandidateProfile(), today="2026-08-19", cv_staged=False)))
    assert d["company"] == "Example Northgate" and d["cv_path"] is None


_SYNTHETIC_WARNED = {
    # Obviously-synthetic tokens, ratcheted by
    # tests/test_fixture_name_neutrality.py's fifth collector
    # (test_every_equal_opportunities_fixture_value_is_an_obvious_synthetic_token), which
    # sweeps the warned-block fixtures in this file for exactly this token SHAPE -- nothing
    # local can tell a real demographic category from an invented one, so the shape is what
    # makes this reviewable.
    "gender_identity": "SYNTHETIC-GENDER_IDENTITY-1",
    "identifies_as_trans": "SYNTHETIC-IDENTIFIES_AS_TRANS-1",
    "ethnicity": "SYNTHETIC-ETHNICITY-1",
    "religion": "SYNTHETIC-RELIGION-1",
    "sexual_orientation": "SYNTHETIC-SEXUAL_ORIENTATION-1",
    "preferred_pronouns": "SYNTHETIC-PREFERRED_PRONOUNS-1",
    "disability": "SYNTHETIC-DISABILITY-1",
    "neurodivergent": "SYNTHETIC-NEURODIVERGENT-1",
    "open_about_orientation_at_work": "SYNTHETIC-OPEN_ABOUT_ORIENTATION_AT_WORK-1",
    # age, marital status and nationality joined the warned block (age/marriage are
    # Equality Act 2010 protected characteristics; nationality maps onto race/national
    # origins). `age` is not a CandidateProfile field (it is derived from
    # date_of_birth), so it cannot carry a fixture value here --
    # test_synthetic_warned_fixtures_cover_exactly_the_warned_roster's pin below
    # accounts for that by comparing against `packet._WARNED_KEYS` directly, not this
    # dict's keys plus "age".
    "marital_status": "SYNTHETIC-MARITAL_STATUS-1",
    "nationality": "SYNTHETIC-NATIONALITY-1",
    "dual_nationality": "SYNTHETIC-DUAL_NATIONALITY-1",
}


def test_synthetic_warned_fixtures_cover_exactly_the_warned_roster():
    # Pin the correspondence rather than leaving it accidental -- a field added to
    # `_WARNED_KEYS` later without a matching fixture here would otherwise go
    # unnoticed by every test below that relies on `_SYNTHETIC_WARNED` for full
    # coverage.
    assert set(_SYNTHETIC_WARNED) == set(packet._WARNED_KEYS)


def test_a_declared_field_reaches_the_packet_and_an_undeclared_one_is_absent():
    p = CandidateProfile(town="Example Town")
    pkt = build_packet(_note(), _cfg(), profile=p, today="2026-08-19", cv_staged=False)
    assert pkt["town"] == "Example Town"
    # ABSENT, not "" -- the form-filling skill must be able to tell "sluice has
    # nothing for this" from "sluice knows this is blank".
    assert "county" not in pkt


def test_age_is_computed_and_omitted_when_the_dob_does_not_parse():
    good = build_packet(_note(), _cfg(), profile=CandidateProfile(date_of_birth="1990-06-15"),
                        today="2026-06-15", cv_staged=False)
    assert good["age"] == 36
    bad = build_packet(_note(), _cfg(), profile=CandidateProfile(date_of_birth="15/06/1990"),
                       today="2026-06-15", cv_staged=False)
    assert "age" not in bad
    assert "date_of_birth" not in bad, "the raw DOB is never a packet key"


@pytest.mark.parametrize("prefer,host,default,expected", [
    ("true",  "greenhouse", "A referral", "greenhouse"),   # lead source wins
    ("true",  "other",      "A referral", "A referral"),   # unresolved host -> default
    # A BLANK host is a separate row from "other", and reachable: build_packet computes
    # it via `listing_host(url)`, which returns "" for a lead whose `url` frontmatter is
    # absent or blank -- the shape a bare `_note()` already produces elsewhere in this
    # file. Without it, deleting `""` from `resolve_how_heard`'s `host not in ("",
    # "other")` tuple is a survivable mutation: the blank host would be returned AS the
    # how_heard value and this sweep would stay green. (CodeRabbit, PR #161.)
    ("true",  "",           "A referral", "A referral"),   # blank host -> default
    ("true",  "",           "",           None),           # blank host, no default
    ("true",  "other",      "",           None),           # nothing to say
    ("false", "greenhouse", "A referral", "A referral"),   # default wins
    ("",      "greenhouse", "A referral", "A referral"),   # blank means no
    ("false", "greenhouse", "",           None),           # nothing to say
])
def test_how_heard_resolves_across_all_three_axes(prefer, host, default, expected):
    p = CandidateProfile(how_heard_default=default, how_heard_detail_from_lead_source=prefer)
    assert resolve_how_heard(p, host) == expected


def test_how_heard_is_omitted_from_the_packet_when_it_resolves_to_none():
    p = CandidateProfile(how_heard_default="", how_heard_detail_from_lead_source="false")
    pkt = build_packet(_note(), _cfg(), profile=p, today="2026-08-19", cv_staged=False)
    assert "how_heard" not in pkt, "omitted, never written as a null"


def test_identity_fields_never_reach_the_packet():
    # render_text's own RULES block says "Use first names only. No real full names
    # in third-party forms." The CV upload is the name/contact channel.
    p = CandidateProfile(forenames="Ada", surname="Example", email="ada@example.invalid",
                         mobile="+44 20 7946 0000", linkedin="https://example.invalid/in/x")
    pkt = build_packet(_note(), _cfg(), profile=p, today="2026-08-19", cv_staged=False)
    for k in ("forenames", "surname", "email", "mobile", "linkedin"):
        assert k not in pkt


def test_render_text_renders_declared_fields_and_omits_undeclared_ones():
    # Asserting only on the packet DICT would pass while the default output path
    # rendered none of it -- render_text is what `apply prep` prints without --json.
    p = CandidateProfile(town="Example Town", right_to_work_uk="Yes")
    out = render_text(build_packet(_note(), _cfg(), profile=p, today="2026-08-19",
                                   cv_staged=False))
    assert "Example Town" in out
    assert "Yes" in out
    assert "county" not in out.lower()


def test_render_text_groups_the_warned_fields_under_their_own_heading():
    p = CandidateProfile(**_SYNTHETIC_WARNED)
    out = render_text(build_packet(_note(), _cfg(), profile=p, today="2026-08-19",
                                   cv_staged=False))
    # Assert the exact heading LINE, not a bare substring like "MONITORING" -- a bare
    # substring cannot tell the heading from a RULES bullet that happens to mention
    # the same word. THIS assertion is the one that reddens if
    # `lines.append(_WARNED_HEADING)` is deleted from render_text (verified by
    # mutation testing); the negative test below cannot catch that deletion --
    # removing the heading only makes ITS "heading absent" assertion more trivially
    # true, see its own comment.
    assert packet._WARNED_HEADING in out
    for value in _SYNTHETIC_WARNED.values():
        assert value in out
    # The heading must precede the values, so what they are is visible where they appear.
    assert out.index(packet._WARNED_HEADING) < min(out.index(v) for v in _SYNTHETIC_WARNED.values())


def test_render_text_omits_the_warned_heading_entirely_when_nothing_is_declared():
    out = render_text(build_packet(_note(), _cfg(), profile=CandidateProfile(),
                                   today="2026-08-19", cv_staged=False))
    # Deleting the heading append does NOT catch here -- it only makes this "heading
    # absent" assertion more trivially true (see the positive test above for that
    # mutation). What THIS assertion catches is the heading being hoisted OUT of
    # `if warned:` so it prints even with nothing declared, or the RULES bullet below
    # re-acquiring the heading's own text.
    assert packet._WARNED_HEADING not in out


def test_every_passthrough_field_reaches_both_the_packet_and_render_text():
    """Guards discoverability, not enumeration. `_PASSTHROUGH_KEYS` is derived from
    `dataclasses.fields(CandidateProfile)`, so a field silently dropping off the
    packet or off render_text's default output would otherwise go unnoticed --
    exactly the "invisible field" failure this task exists to close. Assert the
    SCOPE (the count) first: with 28 fields, a test that only looped over the
    (possibly empty) derived tuple and checked a property of each element would
    pass vacuously if the derivation broke and returned nothing (`all([])` is
    `True`). This is unaffected by the DETAILS/WARNED split: both
    sections still partition `_PASSTHROUGH_KEYS`, so every declared field still
    reaches `out` somewhere, just possibly under a different heading than before."""
    assert len(packet._PASSTHROUGH_KEYS) == 28
    declared = {name: f"SYNTHETIC-{name.upper()}-1" for name in packet._PASSTHROUGH_KEYS}
    p = CandidateProfile(**declared)
    pkt = build_packet(_note(), _cfg(), profile=p, today="2026-08-19", cv_staged=False)
    for name, value in declared.items():
        assert pkt[name] == value, name
    out = render_text(pkt)
    for name, value in declared.items():
        assert value in out, name
    # This is the FULLY populated case -- both the DETAILS and the warned section
    # render here, and the module docstring's "slop-clean (no em dashes)" claim was
    # previously only verified against a blank-profile packet, which never exercises
    # either new section's text.
    assert "\u2014" not in out and "--" not in out


def test_derived_packet_keys_reach_render_text_when_present():
    # age and how_heard are DERIVED packet keys, not CandidateProfile fields, so the
    # scope sweep above (over _PASSTHROUGH_KEYS) cannot see them -- they need their
    # own reachability check. This loop is generic over whatever build_packet
    # actually put in the dict, rather than hand-naming "36"/"A referral" -- a THIRD
    # derived key added later is covered by this loop automatically, PROVIDED the
    # profile constructed below also populates the field it derives from. A derived
    # key sourced from a profile field OTHER than date_of_birth/how_heard_default
    # would be absent from `pkt` here and silently skipped by `if v:`, reopening the
    # reachability gap with this test still green -- this loop is not a substitute
    # for adding that new field to the profile constructed below.
    p = CandidateProfile(date_of_birth="1990-06-15", how_heard_default="A referral")
    pkt = build_packet(_note(), _cfg(), profile=p, today="2026-06-15", cv_staged=False)
    out = render_text(pkt)
    for k, v in pkt.items():
        if v:
            assert str(v) in out, k
