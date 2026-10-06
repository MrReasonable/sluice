"""cv/validate.py::check_selection -- the hard checks over the model's text (#364 spec §6.1).

Synthetic names throughout: Examplelang / Examplelangscript for a name inside a longer
token, Exampleco for a capitalised tool beside the lowercase word exampleco-live.
"""
from sluice.core.layout import Slot
from sluice.core.protocols import CvLayout, LayoutRole
from sluice.cv.reply import Bullet
from sluice.cv.selection import Selection
from sluice.cv.validate import EntryFacts, check_selection, entry_facts

ALPHA = LayoutRole("Example Alpha", "01/2020", "present")
BETA = LayoutRole("Example Beta", "01/2015", "12/2019")
SLOTS = (Slot("R1", ALPHA, ("EA1",), None), Slot("R2", BETA, ("EB1",), None))


def _facts(**over):
    base = {
        "EA1": EntryFacts(frozenset({"3", "8"}), (), ("Grew the team", "Grew 3 to 8."),
                          "role", ("Example Alpha",)),
        # Tools passed by keyword: the fixture-name sweep reads a `tools=` value, never a
        # position (tests/test_fixture_name_neutrality.py::_is_skill_kwarg).
        "EB1": EntryFacts(frozenset({"12"}),
                          tools=("Examplelang3", "Exampleco", "examplecoach"),
                          texts=("Built the platform", "Ran Examplelang3 on 12 nodes."),
                          placement="role", role_headings=("Example Beta",)),
    }
    base.update(over)
    return base


def _check(profile="I build reliable systems.", r1=(), r2=(), facts=None, decoys=()):
    sel = Selection(profile, {"R1": tuple(r1), "R2": tuple(r2)}, ())
    return check_selection(sel, SLOTS, facts or _facts(), decoys=decoys)


def _b(text, *cites):
    return Bullet(text, tuple(cites))


def test_a_clean_selection_has_no_findings():
    assert _check(r1=[_b("Grew the team from 3 to 8", "EA1")],
                  r2=[_b("Ran Examplelang3 on 12 nodes", "EB1")]) == []


def test_an_uncited_bullet_is_refused():
    assert _check(r1=[_b("Grew the team")]) == ["UNCITED BULLET: R1 bullet 1: Grew the team"]


def test_a_cite_to_no_entry_is_refused():
    assert _check(r1=[_b("Grew the team", "ZZ9")]) == [
        "BAD CITATION ['ZZ9']: not bundle entries - R1 bullet 1: Grew the team"]


def test_a_cite_from_another_role_is_refused_and_the_same_cite_in_its_own_role_is_clean():
    assert _check(r2=[_b("Grew the team from 3 to 8", "EA1")]) == [
        "WRONG EMPLOYER: R2 bullet 1 cites EA1, which belongs to Example Alpha - "
        "Grew the team from 3 to 8"]
    assert _check(r1=[_b("Grew the team from 3 to 8", "EA1")]) == []


def test_only_the_ineligible_cite_of_a_mixed_bullet_is_named():
    assert _check(r1=[_b("Grew the team", "EA1", "EB1")]) == [
        "WRONG EMPLOYER: R1 bullet 1 cites EB1, which belongs to Example Beta - Grew the team"]


def test_the_wrong_employer_message_says_why_an_entry_fits_nowhere():
    facts = _facts(EN1=EntryFacts(frozenset(), (), (), "unmatched", ()),
                   EZ1=EntryFacts(frozenset(), (), (), "blank", ()))
    assert _check(r1=[_b("Did a thing", "EN1"), _b("Did more", "EZ1")], facts=facts) == [
        "WRONG EMPLOYER: R1 bullet 1 cites EN1, which is not on your CV - Did a thing",
        "WRONG EMPLOYER: R1 bullet 2 cites EZ1, which has no company - Did more"]


def test_a_figure_absent_from_the_cited_entries_is_refused():
    assert _check(r1=[_b("Grew the team from 3 to 40", "EA1")]) == [
        "INVENTED METRIC ['40'] not in ['EA1']: Grew the team from 3 to 40"]


def test_a_bracketed_figure_is_still_scanned_when_it_reaches_the_check():
    # The bullet-side twin of test_a_profile_non_id_bracketed_number_is_flagged: nothing
    # here strips a bracket, so a figure inside one cannot hide (#364 spec §6.1).
    assert _check(r1=[_b("Cut costs [40%] across the team", "EA1")]) == [
        "INVENTED METRIC ['40'] not in ['EA1']: Cut costs [40%] across the team"]


def test_figures_in_other_scripts_are_checked_and_normalised():
    assert _check(r1=[_b("Grew the team from 3 to ８", "EA1")]) == []
    assert _check(r1=[_b("Grew to ４０", "EA1")]) == [
        "INVENTED METRIC ['40'] not in ['EA1']: Grew to ４０"]


def test_an_entry_in_native_digits_licenses_the_ascii_figure():
    facts = _facts(EA1=EntryFacts(frozenset({"500"}), (), ("", "Served ٥٠٠ users."),
                                  "role", ("Example Alpha",)))
    assert _check(r1=[_b("Served 500 users", "EA1")], facts=facts) == []


def test_entry_facts_normalise_an_entrys_native_digits():
    # Through entry_facts, so the ENTRY side's normalisation is what is under test (the row
    # above hands check_selection figures already normalised).
    from sluice.cv import bundle as B
    b = B.build_bundle([{"title": "Served users", "company": "Example Alpha", "metrics": "",
                         "body": "Served ٥٠٠ users."}], [], [], {"Example Alpha": "EA"})
    assert entry_facts(b, CvLayout(roles=(ALPHA,)))["EA1"].figures == frozenset({"500"})


def test_a_figure_only_an_uncited_entry_carries_is_refused():
    # The gate's core rule: a bullet's figure must come from an entry IT cites. 12 is EB1's
    # figure; this bullet cites EA1 only. Every other INVENTED METRIC row uses a figure no
    # entry carries, so a gate licensing EVERY entry's figures would pass them all.
    assert _check(r1=[_b("Grew the team from 3 to 12", "EA1")]) == [
        "INVENTED METRIC ['12'] not in ['EA1']: Grew the team from 3 to 12"]


def test_a_figure_inside_a_cited_tool_name_is_not_a_metric():
    assert _check(r2=[_b("Ran Examplelang3 across the estate", "EB1")]) == []


def test_a_licensed_name_never_launders_a_longer_figure():
    assert _check(r2=[_b("Ran Examplelang30 nodes", "EB1")]) == [
        "INVENTED METRIC ['30'] not in ['EB1']: Ran Examplelang30 nodes"]


def test_a_fabricated_figure_beside_a_licensed_tool_is_still_refused():
    assert _check(r2=[_b("Ran Examplelang3 for 99 users", "EB1")]) == [
        "INVENTED METRIC ['99'] not in ['EB1']: Ran Examplelang3 for 99 users"]


def test_span_removal_is_licensed_by_the_cited_entries_only():
    # EA1 declares no tools and does not carry the figure 3, so only a removal licensed by
    # ANOTHER entry's declared tools could hide the 3 inside the name, which must not happen.
    facts = _facts(EA1=EntryFacts(frozenset({"8"}), (), ("Grew the team.",), "role",
                                  ("Example Alpha",)))
    assert _check(r1=[_b("Ran Examplelang3 for the team", "EA1")], facts=facts) == [
        "INVENTED METRIC ['3'] not in ['EA1']: Ran Examplelang3 for the team",
        "MISATTRIBUTED TOOL 'Examplelang3' not in ['EA1']: Ran Examplelang3 for the team"]


def test_a_profile_figure_must_be_in_some_entry():
    assert _check(profile="I grew teams from 3 to 12.") == []
    assert _check(profile="I grew teams to 40.") == [
        "INVENTED PROFILE METRIC 40 not in your evidence: I grew teams to 40."]


def test_only_tools_never_other_names_strip_a_profile_digit():
    assert _check(profile="Skilled in Examplequery2.") == [
        "INVENTED PROFILE METRIC 2 not in your evidence: Skilled in Examplequery2."]


def test_a_declared_tools_digit_is_not_a_profile_metric():
    # The other half of the row above: a DECLARED tool's digit is blanked out of the profile,
    # since a tool name is not a metric (#165). EA1 is narrowed so the 3 is in no entry's
    # figures, so only the removal can make this clean.
    facts = _facts(EA1=EntryFacts(frozenset({"8"}), (), ("Grew the team.",), "role",
                                  ("Example Alpha",)))
    assert _check(profile="Skilled in Examplelang3.", facts=facts) == []


def test_a_lowercase_word_never_triggers_a_capitalised_tool():
    # The `go-live` / `Go` shape: EB1 declares the capitalised Exampleco, and EA1 cites
    # nothing that licenses it. The lowercase word is not the tool, so it is no finding,
    # while the capitalised spelling still is.
    assert _check(r1=[_b("Ran the exampleco-live cutover", "EA1")]) == []
    assert _check(r1=[_b("Ran the Exampleco cutover", "EA1")]) == [
        "MISATTRIBUTED TOOL 'Exampleco' not in ['EA1']: Ran the Exampleco cutover"]


def test_a_tool_declared_by_the_cited_entry_is_licensed():
    assert _check(r2=[_b("Built it in Exampleco", "EB1")]) == []


def test_a_tool_another_entry_declares_is_misattributed():
    assert _check(r1=[_b("Built it in Exampleco", "EA1")]) == [
        "MISATTRIBUTED TOOL 'Exampleco' not in ['EA1']: Built it in Exampleco"]


def test_a_cited_entrys_own_text_licenses_a_tool_by_the_same_case_rule():
    licensed = _facts(EA1=EntryFacts(frozenset(), (), ("", "Moved the platform to Exampleco."),
                                     "role", ("Example Alpha",)))
    lowercase_word = _facts(EA1=EntryFacts(frozenset(), (), ("", "Ran the exampleco-live cutover."),
                                           "role", ("Example Alpha",)))
    assert _check(r1=[_b("Built it in Exampleco", "EA1")], facts=licensed) == []
    assert _check(r1=[_b("Built it in Exampleco", "EA1")], facts=lowercase_word) == [
        "MISATTRIBUTED TOOL 'Exampleco' not in ['EA1']: Built it in Exampleco"]


def test_a_tool_spanning_the_title_and_the_body_is_not_licensed_by_them():
    # Title and body are two strings the user wrote apart: the title ending in one word and
    # the body opening with the next never wrote the two-word tool, so it licenses nothing.
    other = EntryFacts(frozenset(), tools=("Example Zephyr",), texts=(),
                       placement="role", role_headings=("Example Beta",))
    split = _facts(EA1=EntryFacts(frozenset(), (), ("Lead at Example", "Zephyr rollout."),
                                  "role", ("Example Alpha",)), EB1=other)
    whole = _facts(EA1=EntryFacts(frozenset(), (), ("Lead", "Ran Example Zephyr."),
                                  "role", ("Example Alpha",)), EB1=other)
    assert _check(r1=[_b("Ran Example Zephyr", "EA1")], facts=split) == [
        "MISATTRIBUTED TOOL 'Example Zephyr' not in ['EA1']: Ran Example Zephyr"]
    assert _check(r1=[_b("Ran Example Zephyr", "EA1")], facts=whole) == []


def test_a_lowercase_declared_tool_is_licensed_by_a_capitalised_mention():
    facts = _facts(EA1=EntryFacts(frozenset(), (), ("Examplecoach sessions ran weekly.", ""),
                                  "role", ("Example Alpha",)))
    assert _check(r1=[_b("Ran examplecoach sessions", "EA1")], facts=facts) == []


def test_a_longer_token_in_the_cited_text_never_licenses_the_shorter_tool():
    facts = _facts(EA1=EntryFacts(frozenset(), (), ("", "Wrote Examplelangscript daily."),
                                  "role", ("Example Alpha",)),
                   EB1=EntryFacts(frozenset(), ("Examplelang",), (), "role",
                                  ("Example Beta",)))
    assert _check(r1=[_b("Wrote Examplelang daily", "EA1")], facts=facts) == [
        "MISATTRIBUTED TOOL 'Examplelang' not in ['EA1']: Wrote Examplelang daily"]


def test_a_lowercase_listed_item_is_policed_like_any_other():
    assert _check(r1=[_b("Ran examplecoach sessions", "EA1")]) == [
        "MISATTRIBUTED TOOL 'examplecoach' not in ['EA1']: Ran examplecoach sessions"]


def test_with_no_tools_declared_anywhere_the_attribution_check_does_not_run():
    facts = {k: EntryFacts(f.figures, (), f.texts, f.placement, f.role_headings)
             for k, f in _facts().items()}
    assert _check(r1=[_b("Built it in Exampleco", "EA1")], facts=facts) == []


def test_a_decoy_is_refused_as_a_whole_term_only():
    assert _check(r1=[_b("Shipped Examplelang tooling", "EA1")], decoys=("examplelang",)) == [
        "FABRICATED: contains 'examplelang'"]
    assert _check(r1=[_b("Shipped Examplelangscript tooling", "EA1")],
                  decoys=("examplelang",)) == []


def test_a_multi_word_decoy_matches_as_a_phrase_but_not_across_a_sentence_break():
    assert _check(profile="I ran Example Zephyr work.", decoys=("Example Zephyr",)) == [
        "FABRICATED: contains 'Example Zephyr'"]
    assert _check(profile="I ran Example. Zephyr checks ran.", decoys=("Example Zephyr",)) == []



def test_entry_facts_reads_figures_tools_text_and_placement_from_the_bundle():
    from sluice.cv.bundle import build_bundle
    entries = [{"title": "Grew the team", "company": "Example Alpha", "best_for": "",
                "category": "", "metrics": "3 ８", "body": "Grew 3 to 8.",
                "fields": {"Tools": "Exampleco"}}]
    bundle = build_bundle(entries, [], [], {"Example Alpha": "EA"})
    layout = CvLayout(roles=(ALPHA,))
    facts = entry_facts(bundle, layout)
    assert facts == {"EA1": EntryFacts(frozenset({"3", "8"}), ("Exampleco",),
                                       ("Grew the team", "Grew 3 to 8."), "role",
                                       ("Example Alpha",))}


def test_a_punctuation_split_figure_is_one_figure_not_two_licensed_ones():
    assert _check(r1=[_b("Grew revenue 8.3x", "EA1")]) == [
        "INVENTED METRIC ['8.3'] not in ['EA1']: Grew revenue 8.3x"]
    assert _check(r1=[_b("Grew to 3,8 million", "EA1")]) == [
        "INVENTED METRIC ['38'] not in ['EA1']: Grew to 3,8 million"]


def test_a_grouped_figure_is_licensed_by_its_ungrouped_spelling_and_the_reverse():
    for entry_figure in ("3,000", "3000"):
        facts = _facts(EA1=EntryFacts(frozenset({entry_figure.replace(",", "")}), (), (),
                                      "role", ("Example Alpha",)))
        assert _check(r1=[_b("Served 3,000 users", "EA1")], facts=facts) == []
        assert _check(r1=[_b("Served 3000 users", "EA1")], facts=facts) == []


def test_a_decimal_figure_is_licensed_only_by_the_same_decimal():
    facts = _facts(EA1=EntryFacts(frozenset({"2.5"}), (), (), "role", ("Example Alpha",)))
    assert _check(r1=[_b("A 2.5 ratio", "EA1")], facts=facts) == []
    assert _check(r1=[_b("A 2 ratio", "EA1")], facts=facts) != []
    assert _check(r1=[_b("A 25 ratio", "EA1")], facts=facts) != []


def test_an_entry_side_grouped_figure_is_normalised_through_entry_facts():
    from sluice.cv import bundle as B
    b = B.build_bundle([{"title": "Served users", "company": "Example Alpha", "metrics": "",
                         "body": "Served 3,000 users."}], [], [], {"Example Alpha": "EA"})
    assert entry_facts(b, CvLayout(roles=(ALPHA,)))["EA1"].figures == frozenset({"3000"})


def test_a_grouped_figure_is_licensed_by_any_spelling_of_the_group():
    facts = _facts(EA1=EntryFacts(frozenset({"2000"}), (), (), "role", ("Example Alpha",)))
    # Not "2 000": an ASCII-space group is ambiguous, cv/reply.py refuses it in the model's
    # text, and core/tokens.py::figures reads it both ways (tests below).
    for shown in ("2,000", "2\u00a0000", "2000"):
        assert _check(r1=[_b(f"Served {shown} users", "EA1")], facts=facts) == []


def test_a_grouped_figure_is_refused_against_its_parts():
    facts = _facts(EA1=EntryFacts(frozenset({"2", "000"}), (), (), "role", ("Example Alpha",)))
    assert _check(r1=[_b("Served 2 000 users", "EA1")], facts=facts) == [
        "INVENTED METRIC ['2000'] not in ['EA1']: Served 2 000 users"]


def test_a_quarter_and_a_year_are_two_figures():
    facts = _facts(EA1=EntryFacts(frozenset({"3", "2023"}), (), (), "role", ("Example Alpha",)))
    assert _check(r1=[_b("Shipped in Q3 2023", "EA1")], facts=facts) == []


def _entry(body):
    from sluice.cv import bundle as B
    b = B.build_bundle([{"title": "Example work", "company": "Example Alpha", "metrics": "",
                         "body": body}], [], [], {"Example Alpha": "EA"})
    return _facts(EA1=entry_facts(b, CvLayout(roles=(ALPHA,)))["EA1"])


def test_an_ascii_space_group_in_an_entry_licenses_its_joined_reading():
    assert _check(r1=[_b("Served 10,000 users", "EA1")],
                  facts=_entry("Served 10 000 users.")) == []


def test_an_ascii_space_group_in_an_entry_licenses_its_separate_reading():
    facts = _entry("Led 3 100-person teams.")
    assert _check(r1=[_b("Led 3 teams", "EA1")], facts=facts) == []
    assert _check(r1=[_b("Ran a 100-person team", "EA1")], facts=facts) == []


def test_a_digit_glued_to_a_letter_never_heads_a_group_in_an_entry():
    assert _check(r1=[_b("Signed 120 customers", "EA1")],
                  facts=_entry("In Q3 120 customers signed.")) == []


def test_a_currency_code_does_not_exempt_a_grouped_number_in_an_entry():
    assert _check(r1=[_b("Saved 3,100 a month", "EA1")],
                  facts=_entry("Saved USD3 100 a month.")) == []


def test_a_lone_letter_label_reads_two_figures_whatever_the_space():
    facts = _entry("In Q3\u00a0120 customers signed.")
    assert _check(r1=[_b("Signed 120 customers", "EA1")], facts=facts) == []
    assert _check(r1=[_b("Signed 3,120 customers", "EA1")], facts=facts) != []


def _facts_from(entries, negatives=()):
    from sluice.cv import bundle as B
    b = B.build_bundle(entries, list(negatives), [], {"Example Alpha": "EA"})
    return entry_facts(b, CvLayout(roles=(ALPHA,)))


_SCALED = [{"title": "Scaled the platform", "company": "Example Alpha", "metrics": "40",
            "body": "Scaled to 40 nodes."}]


def test_negatives_block_does_not_widen_the_last_entrys_allowlist():
    facts = _facts_from(_SCALED, negatives=["never claim 500 users"])
    assert _check(r1=[_b("Scaled the platform to 500 users", "EA1")], facts=facts) == [
        "INVENTED METRIC ['500'] not in ['EA1']: Scaled the platform to 500 users"]


def test_profile_number_from_negatives_is_flagged():
    facts = _facts_from(_SCALED, negatives=["never claim 500 users"])
    assert _check(profile="I scaled to 500 users.", facts=facts) == [
        "INVENTED PROFILE METRIC 500 not in your evidence: I scaled to 500 users."]


def test_at_zero_entries_the_negatives_no_longer_reach_the_profile_pool():
    facts = _facts_from([], negatives=["never claim 500 users"])
    assert facts == {}
    selection = Selection("I scaled to 500 users.", {"R1": (), "R2": ()}, ())
    assert check_selection(selection, SLOTS, facts) == [
        "INVENTED PROFILE METRIC 500 not in your evidence: I scaled to 500 users."]


def test_the_vocabulary_cannot_widen_the_hard_gate():
    """SEPARATION (#364 spec §8): the term vocabulary is a STYLE-only pool and carries an
    inventory-only name; the hard gate must still refuse that name's digit, asserted on the
    discriminating message, so a gate that started reading the vocabulary goes red here."""
    from sluice.cv import bundle as B
    layout = CvLayout(roles=(ALPHA,))
    b = B.build_bundle([{"title": "Ran things", "company": "Example Alpha", "metrics": "",
                         "body": "Ran things."}], [], [], {"Example Alpha": "EA"},
                       skills=[{"title": "Examplelang9", "fields": {}, "body": ""}])
    assert "examplelang9" in B.term_vocabulary(b, layout), "premise: the pool carries it"
    selection = Selection("I build.", {"R1": (_b("Ran Examplelang9 for the team", "EA1"),)},
                          ())
    assert check_selection(selection, (Slot("R1", ALPHA, ("EA1",), None),),
                           entry_facts(b, layout)) == [
        "INVENTED METRIC ['9'] not in ['EA1']: Ran Examplelang9 for the team"]


# --- #174, retargeted from the text gate (spec §12.2) ---------------------------------
# An entry's figures are derived from that entry's OWN lines, never by re-reading rendered
# text, so no line of user free text can mint, rebind or strand an id's allowlist.

_TWO = [{"title": "Held uptime", "company": "Example Alpha", "metrics": "90", "body": ""},
        {"title": "Cut latency", "company": "Example Alpha", "metrics": "", "body": ""}]


def test_a_figure_from_either_of_two_cited_entries_is_licensed():
    # Through entry_facts, so each entry's own figures are what is unioned: 90 is EA1's
    # and 99 is EA2's, and neither entry carries the other's.
    facts = _facts_from([dict(_TWO[0]), dict(_TWO[1], metrics="99")])
    slots = (Slot("R1", ALPHA, ("EA1", "EA2"), None),)

    def run(text):
        return check_selection(Selection("I build.", {"R1": (_b(text, "EA1", "EA2"),)}, ()),
                               slots, facts)

    assert run("Lifted uptime 90 to 99") == []
    assert run("Lifted uptime 90 to 98") == [
        "INVENTED METRIC ['98'] not in ['EA1', 'EA2']: Lifted uptime 90 to 98"]


def test_an_entrys_own_id_digit_is_not_one_of_its_figures():
    # `1` appears ONLY inside the id EA1; if the id token were scanned too, the code's own
    # digit would silently become a permitted figure.
    facts = _facts_from([{"title": "Owned direction", "company": "Example Alpha",
                          "metrics": "", "body": ""}])
    assert facts["EA1"].figures == frozenset()
    assert _check(r1=[_b("Owned 1 direction", "EA1")], facts=facts) == [
        "INVENTED METRIC ['1'] not in ['EA1']: Owned 1 direction"]


def test_an_id_shaped_line_in_another_entrys_body_rebinds_nothing():
    # A body line shaped like an EARLIER, REAL code used to overwrite that entry's
    # allowlist: the fabricated figure passed and the genuine one was reported invented.
    entries = [_TWO[0], dict(_TWO[1], body="[EA1] fabricated 500 users")]
    facts = _facts_from(entries)
    assert "500" not in facts["EA1"].figures and "90" in facts["EA1"].figures
    assert _check(r1=[_b("Scaled to 500 users", "EA1")], facts=facts) == [
        "INVENTED METRIC ['500'] not in ['EA1']: Scaled to 500 users"]
    assert _check(r1=[_b("Held 90 uptime", "EA1")], facts=facts) == []


def test_an_id_shaped_line_in_an_entrys_own_body_still_sources_that_entrys_figures():
    # The opposite direction: a body that cross-references an earlier code in prose is
    # still that entry's own evidence.
    facts = _facts_from([dict(_TWO[1], body="[EA1] as above, cut latency to 250 ms")])
    assert "250" in facts["EA1"].figures
    assert _check(r1=[_b("Cut latency to 250 ms", "EA1")], facts=facts) == []


def test_a_section_shaped_body_line_does_not_strand_the_entrys_figures():
    facts = _facts_from([dict(_TWO[1], body="Highlights\n=== Detail ===\nCut latency to 250 ms")])
    assert "250" in facts["EA1"].figures
