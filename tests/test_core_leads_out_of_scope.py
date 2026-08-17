"""core.leads.out_of_scope_verdict (#131 decision 15): a pure re-read distinguishing
"this lead exists, just outside this tool's accepted scope" from "this lead never
existed at all" -- authorizes nothing, decides nothing the caller's own resolution
didn't already decide."""
from types import SimpleNamespace

from sluice.core.leads import UNTRUSTED_SCRAPED_CONTENT_WARNING, out_of_scope_verdict, slug_matches


def _note(slug, status, company="Example Ltd", role="Example Role"):
    return SimpleNamespace(slug=slug, status=status, fm={"company": company, "role": role})


def test_none_when_no_note_falls_outside_accepted_and_matches():
    notes = [_note("Example Ltd - Example Role", "shortlist")]
    # Non-vacuity: this must be the IN-SCOPE arm, not the zero-match arm -- both
    # return None. The matcher DOES match; `accepted` is what excludes the note.
    assert slug_matches(notes[0], "Example Ltd - Example Role")
    assert out_of_scope_verdict(notes, "Example Ltd - Example Role", matcher=slug_matches,
                                accepted=frozenset({"shortlist"})) is None


def test_out_of_scope_when_exactly_one_match_falls_outside_accepted():
    notes = [_note("Example Ltd - Example Role", "applied")]
    result = out_of_scope_verdict(notes, "Example Ltd - Example Role", matcher=slug_matches,
                                  accepted=frozenset({"shortlist"}))
    assert result["outcome"] == "out_of_scope"
    assert result["slug"] == "Example Ltd - Example Role"
    assert result["status"] == "applied"
    assert "detail" in result
    # slug/detail are derived from scraped company/role -- round-2 review finding:
    # every MCP write tool returns this dict verbatim, so it needs the same
    # untrusted-content signal get_lead/list_leads carry.
    assert UNTRUSTED_SCRAPED_CONTENT_WARNING in result["content_warning"]


def test_none_when_two_or_more_matches_fall_outside_accepted():
    """Ambiguity is the CALLER's own not_found/ambiguous verdict's business, not
    this function's -- it only adds a NEW outcome for the exactly-one-match case."""
    notes = [_note("Example Ltd - Example Role", "applied"),
            _note("Example Ltd - Example Role Two", "applied", role="Example Role Two")]
    # Non-vacuity: this must be the TWO-match arm, not the zero-match arm --
    # both return None.
    assert sum(1 for n in notes if slug_matches(n, "Example Ltd")) == 2
    assert out_of_scope_verdict(notes, "Example Ltd", matcher=slug_matches,
                                accepted=frozenset({"shortlist"})) is None


def test_respects_the_matchers_own_semantics_exact_vs_substring():
    """dismiss_lead's exact-equality matcher must not be widened to a substring
    match by this shared helper."""
    notes = [_note("Example Northgate - Analyst", "applied")]
    exact_matcher = lambda n, w: n.slug == w
    assert out_of_scope_verdict(notes, "Northgate", matcher=exact_matcher,
                                accepted=frozenset({"new"})) is None
    assert out_of_scope_verdict(notes, "Example Northgate - Analyst", matcher=exact_matcher,
                                accepted=frozenset({"new"}))["outcome"] == "out_of_scope"
