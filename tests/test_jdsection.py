"""core/jdsection.py: the ONE grammar for a lead note's `## Job description` section (#369).

The reader (where reading stops) and the writer (what writing must demote) share
`breaks_section`, so a drift between them cannot silently truncate a posting."""
import pytest

from sluice.core import jdsection
from sluice.core.jdsection import HEADING, TAG, breaks_section, open_fence, read, render

_JD = "We are hiring an engineer.\nYou will build placeholder systems."


@pytest.mark.parametrize("line", ["#", "##", "# X", "## X", "#\tX", "  ## X", "   # X",
                                  "##   "])
def test_level_one_and_two_atx_headings_break(line):
    assert breaks_section(line)


@pytest.mark.parametrize("line", ["### X", "#tag", "##tag", "    ## X", "", "text ## X",
                                  "#### X"])
def test_other_lines_do_not_break(line):
    assert not breaks_section(line)


def test_no_section_reads_empty_and_zero():
    assert read("# Title\n\nbody text\n") == ("", 0)


def test_an_empty_section_reads_empty_and_one():
    assert read(f"{HEADING}\n\n## Next\nmore\n") == ("", 1)


@pytest.mark.parametrize("heading", [
    HEADING, "## job description", "## JOB DESCRIPTION", f"{HEADING}   ",
    f"{HEADING} {TAG}", f"## Job Description {TAG}  "])
def test_heading_spellings_that_read(heading):
    assert read(f"intro\n{heading}\n{_JD}\n") == (_JD, 1)


def test_a_heading_with_trailing_punctuation_is_not_the_section():
    # Review Focus 1: the grammar is exact (case-insensitive). docs/USAGE.md says so.
    assert read(f"## Job Description:\n{_JD}\n") == ("", 0)


@pytest.mark.parametrize("ender", ["# X", "## X", "#\tX", "  ## X", "##"])
def test_the_section_ends_at_a_level_one_or_two_heading(ender):
    assert read(f"{HEADING}\n{_JD}\n{ender}\nnot part of it\n") == (_JD, 1)


@pytest.mark.parametrize("inner", ["### Requirements", "#hashtag", "    ## indented code"])
def test_the_section_does_not_end_at_other_lines(inner):
    text, count = read(f"{HEADING}\n{_JD}\n{inner}\ntail line\n")
    assert text == f"{_JD}\n{inner}\ntail line" and count == 1


@pytest.mark.parametrize("eol", ["\r\n", "\r"])
def test_crlf_and_lone_cr_bodies_read_like_lf(eol):
    body = f"intro{eol}{HEADING}{eol}{_JD.replace(chr(10), eol)}{eol}## Next{eol}x{eol}"
    assert read(body) == (_JD, 1)


def test_the_section_runs_to_end_of_body():
    assert read(f"{HEADING}\n{_JD}") == (_JD, 1)


def test_two_sections_return_the_first_text_and_count_two():
    body = f"{HEADING}\nfirst one\n## Other\n{HEADING}\nsecond one\n"
    assert read(body) == ("first one", 2)


def test_render_shape():
    assert render(_JD) == f"{HEADING} {TAG}\n\n{_JD}"


# Every spelling `read` breaks on, plus the heading itself. A `render` that demoted only
# `startswith(("# ", "## "))` lines would leave `#\tX`, a bare `##` and `  ## X` live, and
# `read` would then stop at them -- silently truncating the posting.
_EDGE = "\n".join(["Intro line.", "# Top", "## Mid", "#\tTabbed", "##", "  ## Indented",
                   HEADING, "Closing line."])


def test_render_then_read_round_trips_every_breaking_spelling():
    text, count = read(render(_EDGE))
    assert count == 1
    assert text == "\n".join(["Intro line.", "### Top", "### Mid", "###\tTabbed", "###",
                              "  ### Indented", "### Job description", "Closing line."])


def test_render_leaves_non_breaking_lines_alone():
    t = "### Already three\n#hashtag\n    ## code"
    assert render(t) == f"{HEADING} {TAG}\n\n{t}"


def test_render_normalises_line_endings():
    assert render("a\r\nb\rc") == f"{HEADING} {TAG}\n\na\nb\nc"


def test_module_is_pure():
    # No I/O imports: the module is shared by core, triage and cv.
    src = open(jdsection.__file__, encoding="utf-8").read()
    for banned in ("import os", "open(", "import sys"):
        assert banned not in src


def test_the_short_section_reason_is_one_phrase():
    # arc-001: cv records this in needs_signoff and triage in its unjudgeable reason; one
    # home, so a reword cannot split two explanations of the same fact.
    assert jdsection.short_section_reason(500) == (
        "the job description in the note is shorter than min_jd_chars (500)")


# A `## Job description` line inside fenced code is code, not the section.
# Obsidian renders it as code, so reading it as the posting would let a note with no job
# description skip the blind hold. Both fence characters, a longer closing run, and up to three
# spaces of indent are CommonMark fences.
@pytest.mark.parametrize("fence,close", [("```", "```"), ("~~~", "~~~"), ("````md", "`````"),
                                         ("   ```", "```")])
def test_a_heading_inside_a_fence_is_not_the_section(fence, close):
    body = f"intro\n{fence}\n{HEADING}\n{_JD}\n{close}\nafter the example\n"
    assert read(body) == ("", 0)


def test_an_unclosed_fence_runs_to_the_end_of_the_body():
    # CommonMark: an unclosed fence is code to end of document, so the heading under it is too.
    assert read(f"```\n{HEADING}\n{_JD}\n") == ("", 0)


def test_a_fence_of_the_other_character_does_not_close_it():
    assert read(f"```\n~~~\n{HEADING}\n{_JD}\n") == ("", 0)


def test_a_real_section_after_a_fenced_example_is_read():
    body = f"```\n{HEADING}\nexample text\n```\n{HEADING}\n{_JD}\n"
    assert read(body) == (_JD, 1)


def test_a_heading_inside_a_fence_in_the_section_does_not_end_it():
    inner = "```\n## Not a heading\n```"
    assert read(f"{HEADING}\n{_JD}\n{inner}\ntail\n") == (f"{_JD}\n{inner}\ntail", 1)


@pytest.mark.parametrize("line", ["    ```", "``", "```x`y"])
def test_lines_that_are_not_fences_do_not_open_one(line):
    # Four spaces is indented code, two backticks is inline code, and a backtick fence's info
    # string may not contain a backtick.
    assert read(f"{line}\n{HEADING}\n{_JD}\n") == (_JD, 1)


@pytest.mark.parametrize("not_a_close", ["```", "```` text after"])
def test_a_fence_closes_only_on_a_long_enough_bare_run(not_a_close):
    # A closing run shorter than the opener, or one with text after it, is a line of code.
    assert read(f"````\n{not_a_close}\n{HEADING}\n{_JD}\n") == ("", 0)


def test_sections_returns_every_section_in_order():
    body = "## Notes\nx\n## Other\nfirst\n## Other\nsecond\n# End\n"
    assert jdsection.sections(body, "## Other") == ["first", "second"]


def test_sections_default_empty_tag_still_matches_a_bare_heading():
    # `bare[:-len("")]` is `bare[:0]`, the empty string: an unguarded strip would match nothing.
    assert jdsection.sections("## Other\nkept\n", "## Other") == ["kept"]


def test_sections_honours_fences():
    body = "```\n## Other\nfenced\n```\n## Other\nreal\n"
    assert jdsection.sections(body, "## Other") == ["real"]


def test_sections_a_heading_with_other_trailing_text_is_not_a_section():
    assert jdsection.sections("## Other:\nx\n", "## Other") == []


def test_read_is_sections_reduced_to_the_first_and_the_count():
    body = f"{HEADING} {TAG}\nfirst\n{HEADING}\nsecond\n"
    assert read(body) == ("first", 2)
    assert jdsection.sections(body, HEADING, TAG) == ["first", "second"]


def test_demote_rewrites_only_breaking_lines():
    assert jdsection.demote("a\n## b\n  # c\n### d") == "a\n### b\n  ### c\n### d"


def test_neutralise_closes_a_fence_the_text_leaves_open():
    # inv-002: an unclosed fence runs to the end of the body, so every LATER section heading
    # would read as fenced code and be skipped. Witness: return `demote(text)` unclosed.
    body = "## Other\n" + jdsection.neutralise("x\n~~~~\ny") + "\n## Other\nlater\n"
    assert jdsection.sections(body, "## Other") == ["x\n~~~~\ny\n~~~~", "later"]


def test_neutralise_leaves_a_closed_fence_alone():
    assert jdsection.neutralise("```\n## a\n```") == "```\n### a\n```"


def test_a_job_description_left_open_no_longer_hides_a_later_section():
    body = render("posting\n```") + "\n\n## Other\nlater\n"
    assert jdsection.sections(body, "## Other") == ["later"]


@pytest.mark.parametrize("body", ["x\n```\ncode", "x\n~~~\ncode", "x\n````py\n```\nstill code"])
def test_open_fence_is_true_for_a_body_ending_inside_a_fence(body):
    # #195: `leads prep --write` refuses a note whose body ends inside a fence, since the
    # brief appended after it would be swallowed into the fence. The last row: a shorter
    # run of the same character does not close a longer opener.
    assert open_fence(body)


@pytest.mark.parametrize("body", ["x\n```\ncode\n```\n", "x\n~~~\ncode\n~~~", "plain", ""])
def test_open_fence_is_false_for_a_closed_fence_and_for_no_fence(body):
    assert not open_fence(body)


def test_lines_is_the_public_splitter_and_normalises_line_endings():
    # #195: core/prepbrief.py splits with the SAME rule the scanner reads, through a public
    # name rather than the private `_lines`.
    assert jdsection.lines("a\r\nb\rc\n") == ["a", "b", "c", ""]
