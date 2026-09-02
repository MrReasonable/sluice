"""Recovering a JD from the page's own JSON-LD when the rendered body is chrome (#228).

MEASURED ON LIVE POSTINGS, which is what settled the design -- every number here was read off
the real dossier path, not reasoned about.

  vendor    settled body                      JSON-LD JobPosting.description
  Ashby     20121 chars (the real posting)    25696 chars (the same posting, as HTML)
  Workday   152 chars ("Skip to main          4022 chars (the real posting)
            content ... Sign In Home ...")

Workday is why this exists, and it is a case #228's settle made WORSE rather than better. With
no settle the body came back EMPTY, which `jd_arrived` refuses as a fact and `cv run` reports
honestly as `dossier_failed`. With the settle it comes back as 152 characters of NAVIGATION
CHROME -- non-empty, so accepted at the shipped `min_jd_chars` of 0, and therefore silently
treated as a job description that arrived. That is the exact silent case #228 was filed about,
reintroduced by the fix for it.

THE RULE IS "prefer whichever source yields more text", and it is deliberately not a threshold,
a ratio, or a list of client-rendered hosts. All three would be judgements about what a real
posting looks like -- the judgement `min_jd_chars` exists to NOT ship uninvited (its default is
0 for that reason). Length is not such a judgement: both candidates are the same page's own
description of itself, so taking the longer one needs no opinion about jobs, and no host list to
go stale as vendors change their rendering.
"""
import json

import pytest

from sluice.core.dossier import jd_from_structured_data as _jd_from_structured_data


def _ld(*nodes):
    return json.dumps(list(nodes))


def test_a_jobposting_description_is_recovered_as_text():
    raw = _ld({"@type": "JobPosting", "title": "Engineer",
               "description": "<p>We are looking for an engineer.</p>"})
    assert _jd_from_structured_data(raw) == "We are looking for an engineer."


def test_html_entities_and_tags_are_reduced_to_readable_text():
    """The description is HTML on at least one live vendor, so it reaches the judge as text.

    Handing the model raw markup would spend tokens on tags and put angle brackets through a
    prompt; the JD is prose everywhere else in the pipeline and must stay prose here.
    """
    raw = _ld({"@type": "JobPosting",
               "description": "<p>Build&nbsp;things &amp; ship them.</p><ul><li>Python</li></ul>"})
    out = _jd_from_structured_data(raw)
    assert "<" not in out and "&nbsp;" not in out and "&amp;" not in out
    assert "Build things & ship them." in out
    assert "Python" in out


def test_a_bare_object_and_a_graph_are_both_searched():
    """Real pages ship a single object, a list, or a @graph -- all three are the same claim."""
    assert "Solo" in _jd_from_structured_data(
        json.dumps({"@type": "JobPosting", "description": "Solo node."}))
    assert "Graphed" in _jd_from_structured_data(
        json.dumps({"@graph": [{"@type": "Organization"},
                               {"@type": "JobPosting", "description": "Graphed node."}]}))
    assert "Nested" in _jd_from_structured_data(
        json.dumps([[{"@type": "JobPosting", "description": "Nested node."}]]))


def test_a_type_list_still_matches():
    """`@type` is legally a LIST, and a string-equality check would walk past it."""
    raw = _ld({"@type": ["JobPosting", "Thing"], "description": "Typed as a list."})
    assert "Typed as a list." in _jd_from_structured_data(raw)


def test_anything_unusable_degrades_to_empty_rather_than_raising():
    """Best-effort, exactly like the probe that captured it.

    This runs inside the dossier fetch, and raising here would discard a JD that HAS already
    been read from the page -- losing a good dossier over a malformed optional field.
    """
    for raw in ("", "not json at all", "null", "[]", "{}", '{"@type": "Organization"}',
                json.dumps({"@type": "JobPosting"}),
                json.dumps({"@type": "JobPosting", "description": 42}),
                json.dumps({"@type": "JobPosting", "description": "   "})):
        assert _jd_from_structured_data(raw) == "", f"{raw!r} did not degrade to empty"


def test_a_non_string_input_degrades_to_empty():
    """`structured_data` is "" when its probe failed, and a caller could pass None."""
    assert _jd_from_structured_data(None) == ""
    assert _jd_from_structured_data(42) == ""


# --- end to end, through the real dossier fetch -----------------------------------------

from sluice.core.app import Sluice, _LD_JSON_JS  # noqa: E402
from sluice.core.config import Config  # noqa: E402
from tests.harness.config import FIXTURE_ADDR as GLOBAL_ADDR  # noqa: E402


class _Page:
    """A tab serving a body and a JSON-LD blob, the two JD candidates."""

    def __init__(self, body, ld_json):
        self.body, self.ld_json = body, ld_json

    def create_tab(self, url):
        return "tab-1"

    def evaluate(self, tid, js):
        if js == "location.href":
            return {"result": "https://jobs.invalid/x"}
        if js == "document.body.innerText":
            return {"result": self.body}
        if js == _LD_JSON_JS:
            return {"result": self.ld_json}
        return {"result": ""}

    def scroll(self, tid, amount):
        pass

    def close_tab(self, tid):
        pass


@pytest.fixture
def role(titles):
    """Synthetic, from the seeded pool -- see tests/conftest.py."""
    return titles[0][0]


def _fetch_and_cache(tmp_path, page, role):
    """The dossier AND the cache that judged it.

    `_jd` alone bypasses `jd_arrived`, which is where `min_jd_chars` lives -- so a claim
    about ACCEPTANCE asserted through `_jd` is unasserted. Proved: the residual row below
    passed with `jd_arrived` stubbed to raise.
    """
    app = Sluice(Config(), fetcher=page, sleep=lambda _s: None,
                 resolve_host=lambda h: [GLOBAL_ADDR])
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    dossier = cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co",
                             "role": role})
    return dossier, cache


def _jd(tmp_path, page, role):
    app = Sluice(Config(), fetcher=page, sleep=lambda _s: None,
                 resolve_host=lambda h: [GLOBAL_ADDR])
    cache = app.dossier_cache(str(tmp_path), ttl_days=7, min_jd_chars=0)
    return cache.fetcher({"url": "https://jobs.invalid/x", "company": "Example Co",
                          "role": role})["jd"]["markdown"]


def test_navigation_chrome_loses_to_the_pages_own_jobposting(tmp_path, role):
    """The Workday shape, and the regression the settle would otherwise have shipped.

    152 characters of chrome is NON-EMPTY, so `jd_arrived` accepts it at the shipped floor of 0
    and `cv run` reports no failure while composing against navigation text. The recovered
    posting must win.
    """
    chrome = "Skip to main content CAREERS Sign In Home Search for Jobs Locations Apply"
    posting = "We are looking for a Senior Engineer. " * 40
    got = _jd(tmp_path, _Page(chrome, _ld({"@type": "JobPosting", "description": posting})), role)
    assert got.startswith("We are looking for a Senior Engineer.")
    assert "Skip to main content" not in got


def test_a_real_settled_body_is_kept_over_a_shorter_jobposting(tmp_path, role):
    """The Ashby shape: the body IS the posting, so the fallback must not displace it.

    A fallback that always won would replace clean rendered text with whatever markup a vendor
    happens to put in its metadata, on every board that carries both.
    """
    body = "The full rendered posting, every word of it. " * 40
    got = _jd(tmp_path, _Page(body, _ld({"@type": "JobPosting", "description": "<p>Short.</p>"})), role)
    assert got.startswith("The full rendered posting")


@pytest.mark.parametrize("ld", ["", "not json", '[{"@type": "Organization"}]'])
def test_a_page_without_usable_jobposting_keeps_whatever_the_body_gave(tmp_path, role, ld):
    """No JSON-LD is not an error: most boards render server-side and need none of this."""
    assert _jd(tmp_path, _Page("A perfectly good posting body.", ld), role) == \
        "A perfectly good posting body."


def test_an_empty_body_is_still_recovered_when_the_page_publishes_one(tmp_path, role):
    """The fallback also covers the pre-settle EMPTY case, at any budget including 0 --
    the JSON-LD is in the served HTML rather than painted by the app."""
    posting = "Recovered from metadata alone. " * 20
    got = _jd(tmp_path, _Page("", _ld({"@type": "JobPosting", "description": posting})), role)
    assert got.startswith("Recovered from metadata alone.")


def test_a_deeply_nested_document_degrades_instead_of_raising():
    """An uncapped recursive WALK raises on documents `json.loads` itself survives.

    Measured: `json.loads` parses 20000 levels cleanly on CPython 3.14, whose C scanner is
    iterative -- so the parse is not where this breaks. The walk over the parsed structure is,
    and it broke on input `json.loads` had already accepted. `triage/resolve.py` walks this
    same `structured_data` field behind its own depth cap, whose docstring names this input
    class; the guard existed and this function shipped without it.
    """
    assert _jd_from_structured_data("[" * 20000 + "]" * 20000) == ""
    # Built as a STRING rather than with `json.dumps`. The ENCODER recurses on CPython 3.12 and
    # 3.13, so constructing the input that way raised inside the test itself and reported as a
    # failure of the code under test -- green on 3.14, red on the two older interpreters CI
    # also runs. Which end raises is exactly the interpreter detail this function refuses to
    # depend on: on 3.12/3.13 `json.loads` raises here and the catch-all degrades it, on 3.14
    # the parse succeeds and the walk's depth cap does. The assertion is the same either way,
    # which is the point.
    deep = '{"@graph": [' * 5000 + '{"@type": "JobPosting", "description": "x"}' + "]}" * 5000
    assert _jd_from_structured_data(deep) == ""


def test_a_parse_that_raises_anything_still_degrades(monkeypatch):
    """The CONTRACT, forced rather than inferred from one interpreter's behaviour.

    This function is called OUTSIDE the fetch's `finally`, so an escaping exception discards a
    JD already read from the page. Whether any particular CPython build raises on deep nesting
    is not the point and would be a brittle thing to assert -- what must hold is that nothing
    the parser can throw reaches the caller. `RecursionError` is used because it is the one
    that a `(ValueError, TypeError)` catch would let past.
    """
    import sluice.core.app as app

    def boom(_raw):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(app.json, "loads", boom)
    assert _jd_from_structured_data('{"@type": "JobPosting", "description": "x"}') == ""


def test_a_script_element_is_removed_even_with_a_spaced_closing_tag():
    """`</script >` and `</script\n>` are legal HTML, and a pattern demanding the exact
    `</script>` left the element's CODE in the text handed to the judge."""
    for close in ("</script>", "</script >", "</script\n>"):
        raw = _ld({"@type": "JobPosting",
                   "description": f"Real posting.<script>var x = 1;{close}More posting."})
        out = _jd_from_structured_data(raw)
        assert "var x" not in out, f"script body survived with {close!r}: {out!r}"
        assert "Real posting." in out and "More posting." in out


def test_prose_angle_brackets_are_not_eaten_as_markup():
    """A bare `<[^>]*>` strip deletes from any `<` to the next `>`, so ordinary prose loses
    real content -- silently, which is the part that matters for a JD."""
    raw = _ld({"@type": "JobPosting",
               "description": "<p>Teams of < 10 people, > 2 years experience.</p>"})
    out = _jd_from_structured_data(raw)
    assert "10 people" in out and "2 years experience" in out


def test_a_type_merely_ending_in_jobposting_is_rejected():
    """`endswith` was wrong in the direction that matters: an arbitrary node's description
    became the JD. The namespaced spelling must still be accepted."""
    assert _jd_from_structured_data(
        _ld({"@type": "NotAJobPosting", "description": "Not a job."})) == ""
    assert _jd_from_structured_data(
        _ld({"@type": "FakeJobPosting", "description": "Also not a job."})) == ""
    # ALL THREE legal spellings, because narrowing to fix the hostile case broke a real one:
    # the CURIE form is legal against a prefixed `@context` and `endswith` had accepted it,
    # so an exact-match fix silently stopped recovering JDs on pages that use it.
    for spelling in ("JobPosting", "https://schema.org/JobPosting", "schema:JobPosting"):
        assert "Genuine." in _jd_from_structured_data(
            _ld({"@type": spelling, "description": "Genuine."})), f"rejected {spelling!r}"


def test_two_distinct_jobpostings_abstain_rather_than_guess():
    """A posting page may carry more than one JobPosting -- a related-roles rail, a board
    widget. Nothing ties a node to the lead being fetched, so picking one would silently
    tailor a CV to a DIFFERENT JOB. One candidate is an answer; two is a question.

    Identical duplicates are not ambiguity: the same description repeated in two blocks is
    still one answer, so it must NOT abstain.
    """
    assert _jd_from_structured_data(
        _ld({"@type": "JobPosting", "description": "Role one, the lead's."},
            {"@type": "JobPosting", "description": "Role two, a neighbour's."})) == ""
    assert "Same role." in _jd_from_structured_data(
        _ld({"@type": "JobPosting", "description": "Same role."},
            {"@type": "JobPosting", "description": "Same role."}))


def test_the_residual_chrome_without_json_ld_is_still_accepted(tmp_path, role):
    """PINNED AS A KNOWN GAP, deliberately not closed here.

    Where a client-rendered page settles to navigation chrome AND publishes no JobPosting, the
    settle has moved that failure from LOUD to QUIET: before #228 the body was empty, which
    `jd_arrived` refuses as a fact and `cv run` reports as `dossier_failed`; now it is a short
    NON-EMPTY string that is accepted at the shipped `min_jd_chars` of 0.

    Closing it would need a judgement about what a real posting looks like -- exactly what
    `min_jd_chars` exists to NOT ship uninvited -- or per-host knowledge that goes stale. This
    row exists so the gap is a recorded decision rather than an assumption, and so that any
    future fix has a red test to turn green. Both measured vendors publish JSON-LD and are
    therefore recovered; this is the case neither of them is.
    """
    chrome = "Skip to main content CAREERS Sign In Home Search for Jobs Locations"
    dossier, cache = _fetch_and_cache(tmp_path, _Page(chrome, ""), role)
    assert dossier["jd"]["markdown"] == chrome
    # THE HALF THAT MATTERS, asserted at the layer the claim is about. `min_jd_chars` is 0,
    # so chrome counts as arrived -- that is the silence. A future fix landing here (the
    # natural place, since the floor lives here) reddens this row instead of leaving it
    # green while the recorded gap quietly stops describing reality.
    assert cache.jd_arrived(dossier) is True, (
        "the residual is that chrome is ACCEPTED; if this now abstains, the gap is closed "
        "and this row should be rewritten rather than deleted")


def test_a_node_naming_a_different_posting_is_dropped():
    """The wrong-job hazard is reachable at ONE node, not only at two.

    A posting page routinely carries a neighbour's JobPosting — a related-roles rail, a board
    widget. Measured before this: a page whose body settled to chrome, carrying a SINGLE
    JobPosting whose own `url` named a different job, returned the neighbour's description as
    this lead's JD. The abstain-on-ambiguity rule never fired, because there was no ambiguity
    to see — one candidate, confidently wrong. The node carries the evidence that disqualifies
    it; nothing was looking at it.
    """
    other = _ld({"@type": "JobPosting", "url": "https://boards.invalid/jobs/SOME-OTHER-JOB",
                 "description": "A completely different role at the same company."})
    assert _jd_from_structured_data(other, landed_url="https://boards.invalid/jobs/OURS") == ""
    # The lead's own node still resolves, and a differing query string or fragment is not a
    # different posting.
    ours = _ld({"@type": "JobPosting", "url": "https://boards.invalid/jobs/OURS?src=rss#top",
                "description": "The role we actually fetched."})
    assert "The role we actually fetched." in _jd_from_structured_data(
        ours, landed_url="https://boards.invalid/jobs/OURS")


def test_a_url_less_node_stays_eligible():
    """Absence is not evidence: most real postings omit `url`, and dropping them would
    disable the recovery on the majority of pages that need it."""
    node = _ld({"@type": "JobPosting", "description": "No url on this node."})
    assert "No url on this node." in _jd_from_structured_data(
        node, landed_url="https://boards.invalid/jobs/OURS")


def test_the_lead_node_wins_over_a_neighbour_instead_of_abstaining():
    """Url-tying is what makes the common two-node page RECOVERABLE rather than ambiguous.

    Without it this is the abstain case, and the abstain falls back to the body — which on a
    client-rendered page is the chrome this whole feature exists to replace. So the tie is not
    only a safety rule; it is what keeps the recovery working on real pages.
    """
    both = _ld({"@type": "JobPosting", "url": "https://boards.invalid/jobs/OURS",
                "description": "The lead's own posting."},
               {"@type": "JobPosting", "url": "https://boards.invalid/jobs/NEIGHBOUR",
                "description": "A neighbouring role, much longer. " * 20})
    assert "The lead's own posting." in _jd_from_structured_data(
        both, landed_url="https://boards.invalid/jobs/OURS")


def test_a_description_less_lead_node_beside_a_neighbour_abstains(caplog):
    """tst-009's narrowest case, pinned as a DECISION rather than left to drift.

    The lead's node may carry no `description` while a neighbour's does. Counting descriptions
    rather than nodes, that is one candidate and no ambiguity — and the neighbour's text would
    become this lead's JD. With url-tying the neighbour is dropped on its own url and the
    result is an abstain, which is the correct outcome: no JD is better than another job's.
    """
    payload = _ld({"@type": "JobPosting", "url": "https://boards.invalid/jobs/OURS",
                   "title": "Ours, but no description"},
                  {"@type": "JobPosting", "url": "https://boards.invalid/jobs/NEIGHBOUR",
                   "description": "The neighbour's description."})
    assert _jd_from_structured_data(payload, landed_url="https://boards.invalid/jobs/OURS") == ""


def test_an_ambiguous_page_says_so_rather_than_abstaining_silently(caplog):
    """Every other degrade on this fetch logs; both abstain paths were silent.

    The ambiguity case is the one worth hearing about: the page DID publish postings and the
    code declined to choose, which a human could resolve and would otherwise never learn.
    """
    import logging
    payload = _ld({"@type": "JobPosting", "description": "Role one."},
                  {"@type": "JobPosting", "description": "Role two."})
    log = logging.getLogger("sluice.test.ld")
    with caplog.at_level("WARNING"):
        assert _jd_from_structured_data(payload, log=log) == ""
    assert any("distinct JobPosting" in r.getMessage() for r in caplog.records), \
        f"the abstain was silent: {[r.getMessage() for r in caplog.records]}"


def test_the_graph_shape_the_real_probe_actually_emits_is_walked():
    """`_LD_JSON_JS` always hands over an ARRAY of blocks, so a real `@graph` page arrives
    array-wrapped and sits one level deeper than a bare object.

    The only `@graph` coverage fed a BARE object — a shape the probe cannot produce — so the
    depth cap's lower bound was unpinned: dropping it from 6 to 2 left every test green while
    the real array-wrapped shape stopped resolving.
    """
    array_wrapped = json.dumps([{"@graph": [{"@type": "Organization"},
                                            {"@type": "JobPosting",
                                             "description": "Array-wrapped graph."}]}])
    assert "Array-wrapped graph." in _jd_from_structured_data(array_wrapped)


def test_entities_are_unescaped_after_tags_are_stripped_not_before():
    """Order is the whole claim, and it was pinned by nothing.

    Running `html.unescape` FIRST turns `&lt;canvas&gt;` into markup that the tag strip then
    deletes — silently removing a word the posting actually contains. The existing entity test
    (`&nbsp;`, `&amp;`) is order-insensitive and cannot see it.
    """
    raw = _ld({"@type": "JobPosting",
               "description": "<p>You will write &lt;canvas&gt; renderers.</p>"})
    assert "<canvas>" in _jd_from_structured_data(raw)


def test_an_xml_processing_instruction_does_not_leak_into_the_jd():
    """Tag-shaped means tag-shaped: a `<?xml ... ?>` prologue is markup, not prose."""
    raw = _ld({"@type": "JobPosting", "description": "<?xml version='1.0'?><p>The posting.</p>"})
    out = _jd_from_structured_data(raw)
    assert "xml version" not in out and "The posting." in out
