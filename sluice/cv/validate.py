# sluice/cv/validate.py
"""Deterministic citation/fabrication gate. Pure: validate(cv_text, sources) ->
[violations], where `sources` is a `cv.bundle.BundleSources` (#174) rather than the
rendered bundle text. A non-empty list HARD-blocks rendering. Every WORK bullet must
cite a real bundle [id]; every number in a bullet must appear in a cited entry.

`employers` and `fabrication_decoys` are supplied by the caller (from CvConfig, see
cv/config.py) rather than hardcoded here: an empty `employers` list skips the
per-employer completeness check (so the gate works for anyone, not just a caller
with a fixed employer roster), and an empty `fabrication_decoys` list skips the
known-hallucination-string check. Configure both via the `cv:` block of
sluice.yaml for the same behavior a fixed list gave before."""
import re

from sluice.cv.bundle import _WORD_RE, BundleSources

# The PROFILE is prose, not bullets, so its citation strip must match what the
# RENDERER delivers, not the WORK-bullet strip. render.strip_citations removes only
# id-shaped [XX9] codes (render._CITE_RE), so a NON-id bracket like [500] SURVIVES
# into the PDF and the profile check must see and check it. This pattern is
# byte-identical to render._CITE_RE (render.py:10); test_profile_strip_matches_render_
# citation_shape pins that equality, because a comment cannot enforce it and a drift
# silently reopens a fabricated-number-ships fail-open.
#
# This is now the ONLY regex in this module that touches a citation code. Until #174
# there was a second one, `_ID_RE`, which parsed the bundle text to decide which ids
# existed -- and could therefore be fooled by a line of user free text. Ids now arrive
# structurally in `BundleSources`. This one is unrelated to that: it mirrors render's
# LENIENT strip of whatever the MODEL emitted ([A-Za-z]), not any generated code. (#30)
_CITE_RE = re.compile(r"\s*\[[A-Za-z]{2}[0-9]+\]")


# The WORK set. Must stay EXACTLY equal to cv/parse.py's `_BULLET_MARKERS` -- too narrow
# refuses a gate-clean CV, too wide is a citation-check bypass. Named, not inlined, so
# `tests/test_cv_parse.py`'s AST guard can recover it BY NAME: with a second marker tuple
# now alive in this module (`_SKILLS_MARKERS` below, not equal to this one), the guard
# used to recover "the" literal tuple passed to `.startswith(...)` and index `[0]` --
# with two candidates, selecting the WORK one by VALUE or by position would make its own
# equality assertion a tautology, certifying nothing while reading as a real check.
_WORK_BULLET_MARKERS = ("-", "•", "*")

# The SKILLS region's markers (#168). Pre-shaped to equal cv/parse.py's own
# `_TRAILING_MARKERS` -- the WORK set plus the en and em dash -- rather than the
# narrower WORK tuple, and derived from `_WORK_BULLET_MARKERS` the same way
# `_TRAILING_MARKERS` is derived from `_BULLET_MARKERS` there, so the two en/em-dash
# characters are typed in exactly one place in each module. `cv/parse.py` now accepts a
# SKILLS section of its own (#168 Task 7), so this tuple must equal whatever THAT reader
# accepts, for the identical reason `_WORK_BULLET_MARKERS` must equal `_BULLET_MARKERS`:
# a marker the parser accepts and this function does not is a gate BYPASS -- the line
# parses, renders into the PDF, and is never containment-checked here.
# `test_the_skills_region_markers_equal_what_the_gate_collects`
# (`tests/test_cv_parse.py`) derives both tuples and asserts equality, so a change to
# either side reds. Wider than the WORK tuple is safe for the same reason
# `_TRAILING_MARKERS` is: a SKILLS line is not number- or citation-checked (Task 3's
# region split carries no such check, and Task 7's parser doesn't add one either), only
# CONTAINMENT-checked (`UNSOURCED SKILL`, Task 4) -- and that check strips the
# identical marker set before comparing (`.lstrip("-•*–— ")`), so a marker this tuple
# accepts is one that check also strips. (The misattribution check, Task 5, reads
# `work_by_line` instead -- it never reaches this tuple or `skills_lines` at all; see
# the docstring below.)
_SKILLS_MARKERS = _WORK_BULLET_MARKERS + ("–", "—")


def section_spans(cv_text):
    """The PROFILE prose lines, the WORK bullet lines, and the SKILLS bullet lines of
    `cv_text`, 1-indexed.

    Returns `(profile_lines, work_bullet_lines, skills_lines)`, each a list of
    `(lineno, line)` with the RAW line: all three checks below consume it unstripped
    (`_CITE_RE.sub("", line)`, `line.lstrip()`, and the SKILLS check's own
    `.lstrip("-•*–— ")`), so handing back a stripped line would change what they see.
    `skills_lines` is CHECKED now, by the UNSOURCED SKILL containment check (#168 Task
    4). At Task 3, when this region split was first extracted, it was collected but not
    yet read by anything -- that gap is what Task 4 closed, and this sentence is what
    stops the docstring from still claiming it. (The misattribution check, Task 5,
    reads `work_bullet_lines` instead -- it scans WORK-bullet prose against the SKILLS
    vocabulary, never `skills_lines` itself.)

    Extracted from `validate`'s own loop so a later scope-limited check can reason about
    the exact lines the gate reasons about, rather than a second copy of the split that
    would drift from it. `validate` consumes this too -- one state machine, three
    consumers now. `tests/test_cv_validate.py`'s equivalence pin transcribes the loop as
    it stood BEFORE the extraction (later hand-extended to model the SKILLS run too --
    see that file's own comment) and asserts the profile/work lists against it.

    The terminator set for PROFILE/WORK is `CERTIFICATES`/`EDUCATION` and NOTHING else,
    deliberately. The obvious generalisation -- "any all-caps line ends the section" --
    is a WEAKENING of the fabrication gate, not a tidy-up: bullets under a header this
    module does not model (PUBLICATIONS, PROJECTS, AWARDS) are citation-checked today,
    and a generic splitter would silently stop checking them, rendering an uncited claim
    into the PDF. That is the arm a naive extraction drops, so it is pinned by name.

    SKILLS is a DIFFERENT kind of region: a BULLET RUN, not a span held open by a
    start/end header pair. It ends at `CERTIFICATES`/`EDUCATION`, or at a non-blank
    non-bullet line that is either ALL-CAPS (a section header) or reached while `in_work`
    is live -- and NOT at a non-blank non-bullet line that is neither, which is read as a
    GROUP HEADING (`Languages`, `Frameworks`) and keeps the run alive. See the branch's
    own comments for why a blank must not end the run, why the two arms that do end it
    are exactly those two, and why the grouped case had to be closed HERE rather than in
    a renderer: it was a row 2 bypass, measured, on the section order the shipped format
    contract itself asks for. Only a bullet line is materialised into `skills_lines`; a
    blank line, or a group heading, that merely keeps the run alive is not itself a skill
    and is dropped, the same way `work_bullet_lines` already drops every non-bullet line
    inside WORK.

    That means gate and parser no longer stop at the same line in every case: `cv/parse.py`
    refuses at a group heading this run reads past. The direction is deliberate and is the
    safe one -- against the `template` renderer the gate now inspects a SUPERSET of what
    the parser accepts, so nothing that renderer lays out is uninspected -- and it is the
    whole point, since the `script` renderer parses nothing at all.

    That superset argument buys NOTHING under `script`, and this docstring used to
    overreach into "no line can reach a PDF uninspected", which the residual forty lines
    down falsifies: no parser runs there, so a bullet under a SHOUTED group heading
    (`LANGUAGES`, which this run reads as a section header and stops at) reaches a
    script-rendered PDF with neither check behind it. The change strictly REDUCES that set
    -- every ordinary Title-Case grouping is now covered where none was -- but it does not
    empty it.

    Note that no header CLEARS another's flag on the way IN, with one exception:
    `PROFILE` sets `in_profile` without touching `in_work`, so a CV that repeats
    `PROFILE` after `WORK EXPERIENCE` puts the same line in BOTH lists (`validate`
    reports in line order across the two for that reason, and that order is pinned too --
    the violation list is fed back to the composer verbatim on its single retry).
    `SKILLS` is the same shape: entering it does NOT clear `in_work`, which is what lets
    a `PUBLICATIONS` section emitted after a SKILLS run stay citation-checked (see the
    branch's own comment). The one asymmetry is `PROFILE` and `SKILLS` DO clear each
    other -- a CV cannot be inside both at once, since neither is prose a skill bullet
    could sensibly belong to.

    The WORK marker tuple (`_WORK_BULLET_MARKERS`) is the gate's own citation-check
    surface and must stay the only NAME `tests/test_cv_parse.py`'s AST guard resolves for
    that purpose -- see `test_the_work_bullet_markers_are_exactly_what_the_gate_citation_
    checks`, which asserts EQUALITY with `cv/parse.py`'s `_BULLET_MARKERS`, because a WORK
    marker the parser accepts and the gate does not reaches the PDF with the citation
    check never having looked at it. `_SKILLS_MARKERS` is a SECOND, wider tuple used only
    to shape the SKILLS run (see its own comment above) -- it is NOT `cv/parse.py`'s
    `_TRAILING_MARKERS`, which stays scoped to CERTIFICATES/EDUCATION, a pair of sections
    this helper collects nothing inside at all: their terminator clears every flag and
    only a later PROFILE/WORK EXPERIENCE/SKILLS header can set one again. CLAUDE.md is
    explicit these tuples stay separate rather than being widened into one.
    """
    profile, work, skills = [], [], []
    in_work = False
    in_profile = False
    in_skills = False
    for i, line in enumerate(cv_text.splitlines(), 1):
        u = line.strip().upper()
        if u == "PROFILE":
            in_profile, in_skills = True, False
            continue
        if u == "WORK EXPERIENCE":
            in_work, in_profile, in_skills = True, False, False
            continue
        if u == "SKILLS":
            # A CONTIGUOUS BULLET RUN that deliberately does NOT clear `in_work`.
            # Clearing it would swallow a PUBLICATIONS section emitted afterwards, whose
            # bullets ARE citation-checked today -- measured -- and a fabricated figure
            # there would ship unchecked. Reverting to WORK on any unmodelled header
            # instead regresses the mirror case (a PUBLICATIONS bullet after
            # CERTIFICATES, uncited-clean today). A run that ends at the first non-bullet
            # line satisfies both, without the "any all-caps line ends the section"
            # generalisation this function's docstring names as a gate WEAKENING.
            in_skills, in_profile = True, False
            continue
        if u in ("CERTIFICATES", "EDUCATION"):
            in_work, in_profile, in_skills = False, False, False
        is_bullet = line.lstrip().startswith(_SKILLS_MARKERS)
        # A BLANK line does NOT end the run, and that is the whole correctness of this
        # branch. `cv/parse.py` skips blank runs in three places -- its own comment calls
        # that spacing "the LIKELY case, not an exotic one" -- so ending at the first
        # non-bullet line diverges from the section the parser actually reads. Measured, a
        # blank under the header put the skills lines in NO region while `parse_cv` still
        # returned them and the template rendered them: containment-checked by nothing.
        #
        # A non-blank non-bullet line inside the run is one of TWO different things, and
        # ending the run unconditionally at both was a row 2 BYPASS. Measured on the
        # shipped format contract's own section order (`compose._RULES` puts SKILLS LAST,
        # after EDUCATION, and EDUCATION clears `in_work`):
        #
        #     SKILLS
        #     - Example Query
        #     Languages
        #     - Totally Invented Skill
        #
        # `- Totally Invented Skill` landed in NO region at all, `validate()` returned
        # `[]`, and only `cv/parse.py` refused it -- so under `cv.renderer: script`, which
        # implements no `precheck`, a fabricated skill rendered ungated. A grouped SKILLS
        # section is an ordinary CV convention and the format contract does not forbid
        # one, so this had to be closed in the HARD gate rather than left to one
        # renderer's grammar (CLAUDE.md: the engine may guard what the prompt required;
        # only a renderer may guard what its own LAYOUT needs).
        #
        # The discriminator is ALL-CAPS, and it is the conservative direction on purpose.
        # Every header in the format contract is all-caps, and so are the unmodelled ones
        # this module's docstring names (PUBLICATIONS, PROJECTS, AWARDS); a GROUP HEADING
        # inside a skills list is not (`Languages`, `Frameworks`, `Tools`). Getting it
        # wrong the other way has a real cost: a Title-Case unmodelled section after
        # SKILLS IS swallowed. Measured -- `SKILLS / - Example Query / Publications /
        # - Wrote a paper that cut cost by 92%` returned `[]` before and now returns
        # `UNSOURCED SKILL 'Wrote a paper that cut cost by 92%'`.
        #
        # BOTH residuals are stated, because each is the price of the other and a comment
        # that names only one reads as if the rule were free:
        #
        #   over-checking  a Title-Case SECTION heading is read as a group heading, and
        #                  its bullets are containment-checked as skills. Answerable
        #                  WITHOUT inventing or deleting anything -- shout the heading,
        #                  which is what the format contract asks for anyway -- and the
        #                  message names the CONTENT of the bullet, not the heading, so
        #                  the retry has to be told where to look. That is the weaker half
        #                  of the trade and is why the message is worth reading twice.
        #   under-checking an ALL-CAPS group heading (`LANGUAGES`) ends the run, so its
        #                  bullets are checked by nothing at all under `script`.
        #
        # The direction is deliberate: over-checking costs a retry on a refusal a human
        # can answer, under-checking ships an ungated line into a PDF. For a containment
        # gate that is the right way round. What must NOT be read into this comment is
        # that the over-checking case does not exist -- an earlier revision stated only
        # the shouted-heading half, on the side it itself called "what must not happen".
        #
        # `in_work` ends the run too, and that is what keeps this from WEAKENING anything.
        # Entering SKILLS deliberately does not clear `in_work`, so while WORK is live the
        # following bullets are already claimed by the stricter citation check -- measured
        # on the same fixture with WORK still open, `- Totally Invented Skill` is reported
        # as `UNCITED BULLET`. The bypass exists only where NO region claims the line, so
        # extending the run only there can add checking and can never move a line out of
        # one. Without this clause a company line after a SKILLS section (`Example Beta`,
        # not all-caps) would read as a group heading and swallow that role's bullets out
        # of the citation check entirely.
        #
        # This SPLITS the gate/parser correspondence that used to hold here, and the
        # docstring above says so. Before, both stopped at the same line: `cv/parse.py`'s
        # trailing-section reader refuses at the first non-marker line, and Task 7 was what
        # made that agreement about WHERE rather than merely THAT (before it, `parse_cv`
        # rejected every SKILLS section at the HEADER). The gate now reads PAST a group
        # heading the parser still refuses at. That direction is the safe one -- the gate
        # inspects MORE than the parser accepts, never less -- and it changes nothing for
        # the `template` renderer, whose parse already refused this document; it changes
        # `script`, where the line went unchecked.
        #
        # WHAT PINS THE TWO ARMS, named after measuring rather than assumed. An earlier
        # revision of this comment cited `test_a_skills_terminator_line_ends_both_the_
        # gate_region_and_the_parse` (`tests/test_cv_parse.py`). It does NOT pin them:
        # deleting the ALL-CAPS arm, the `in_work` arm, or this whole block each leaves
        # that entire file green (280 passed, measured three times). Its "HALF ONE" is
        # vacuous under this rule -- it asserts the terminator line is absent from
        # `skills_lines`, and a non-bullet line is never materialised there whatever this
        # block decides, with nothing following the terminator in its fixture to tell the
        # two apart. The arms are pinned by three rows in
        # `tests/test_cv_skills_containment.py` --
        # `test_a_bullet_under_a_group_heading_is_still_row_2_checked` (this block
        # deleted), `test_an_all_caps_line_still_ends_the_run_so_a_real_section_is_not_
        # swallowed` (the ALL-CAPS arm deleted) and
        # `test_a_group_heading_while_work_is_live_still_ends_the_run` (the `in_work` arm
        # deleted) -- and, for the `in_work` arm a second time, by
        # `tests/test_cv_validate.py`'s random-document equivalence sweep, which is what
        # makes that arm's agreement with the pre-extraction loop a property rather than
        # a claim.
        if in_skills and line.strip() and not is_bullet:
            if line.strip().isupper() or in_work:
                in_skills = False
        if in_skills:
            # Only a BULLET line is materialised here -- a blank kept the run alive
            # (the check above) but is not itself a skill. The first shipped version of
            # this branch appended every in-region line unconditionally, so a blank
            # right after the `SKILLS` header showed up as a stray `(i, "")` entry in
            # the returned list: harmless to the gate AT THE TIME (Task 3 shipped before
            # anything read `skills_lines` -- that changed at Task 4) but a real defect
            # in the region's own returned contents, caught by the blank-tolerance test
            # this branch exists to satisfy. `work_bullet_lines` already drops
            # non-bullet lines the identical way, via its own marker check below.
            if is_bullet:
                skills.append((i, line))
            continue                       # a skills-region line is NOT also a work bullet
        if in_profile:
            profile.append((i, line))
        if in_work and line.lstrip().startswith(_WORK_BULLET_MARKERS):
            work.append((i, line))
    return profile, work, skills


# The ONE tokeniser and the ONE subsequence primitive both containment rows use (#168).
# Row 2 (`_in_source`, below) is the only consumer today; row 1 (`_names_skill`, Task 5)
# reuses these two rather than redefining them -- two copies would let the vocabulary the
# gate BUILDS drift from the one it SEARCHES with.
def _tokens(text):
    """Case-PRESERVING runs of letters, digits, `#`, `+` and the dots INSIDE a name.

    Not "alphanumeric runs" -- that description was wrong in the one way that mattered.
    `_WORD_RE` (cv/bundle.py, the ONE definition, imported rather than copied) also admits
    `#` and `+` so `C#` and `C++` survive as single tokens, and a dot BETWEEN alphanumerics
    or LEADING one so `Node.js`, `ASP.NET` and `.NET` do. A TRAILING dot is not part of the
    token: while it was, a sentence-final period silently made `Examplestore3.` a different
    token from the declared `Examplestore3`, which produced a false `INVENTED METRIC` in a
    bullet, a false `INVENTED PROFILE METRIC` in prose, and a false `UNSOURCED SKILL`
    against a skill the entry body really carried -- see `_WORD_RE`'s own comment for the
    measured cases.

    Deliberately not core/stem.py: stemming answers a RELEVANCE question (right for
    rank()), and this is an IDENTITY question -- a licensed `Widget` would license an
    emitted `Widgeting`. `stem.tokens` is also alphabetic-only, so it destroys the
    digit-bearing names span removal exists to protect."""
    return _WORD_RE.findall(text)


def _subseq(hay, needle):
    """True when `needle` appears as a CONTIGUOUS subsequence of `hay`.

    The ONE matching primitive both rows use -- row 1 case-sensitively over a bullet's
    prose, row 2 case-insensitively over each source block. Never substring containment:
    `"java" in "javascript"` is the bug rank() was rewritten to remove. Two copies of this
    would let the vocabulary the gate BUILDS drift from the one it SEARCHES with.
    """
    if not needle:
        return False
    return any(list(hay[i:i + len(needle)]) == list(needle)
               for i in range(len(hay) - len(needle) + 1))


def _in_source(blocks, item):
    """True when `item`'s token sequence appears contiguously in ANY source block.

    Case-INSENSITIVE, unlike row 1: this matches a whole emitted item against a corpus,
    with no sentence to collide with, and a case-sensitive rule would refuse a skill whose
    note is filed lowercase. Per-BLOCK rather than over a flattened corpus, so a two-word
    skill cannot match an adjacency invented at a block seam.
    """
    needle = [t.casefold() for t in _tokens(item)]
    return bool(needle) and any(
        _subseq([t.casefold() for t in block], needle) for block in blocks)


def _names_skill(text, skill):
    """Row 1: `skill`'s token sequence, CASE-SENSITIVELY, in `text`."""
    return _subseq(_tokens(text), _tokens(skill))


def _strip_skill_spans(text, skills):
    """Remove each licensed skill's own span before `\\d+` extraction.

    The same technique this module already applies to citations, and for the same reason:
    a digit that is part of a NAME is not a metric. Without it `Example Widget3` reads as the
    number 3 and the only actionable answer is to delete a true skill name.

    Matches COMPLETE TOKEN SEQUENCES, never substrings. A substring removal
    (`re.sub(re.escape(skill), ...)`) is a hole in the numeric gate, not a fix to it:
    with `Skills: Example Widget3`, a bullet reading `Widget30` has `Widget3` struck out and
    leaves `0` behind, so
    a fabricated `30` passes whenever `0` is licensed by a cited entry. It is also the
    substring-containment SC9 forbids by name -- `"java" in "javascript"` is the bug rank()
    was rewritten to remove, and this function must not reintroduce it one layer down.

    CASE-INSENSITIVE, and decided WITHOUT reference to row 1's verdict. Row 1 answers a
    different question (misattribution) under a case-SENSITIVE rule that deliberately
    under-fires; gating removal on it converted every one of those under-fires into a
    hard INVENTED METRIC. `cv/bundle.py`'s PER-TOKEN letter-leading rule is what stops
    this subtracting a real figure.
    """
    hay = _tokens(text)
    needles = sorted((_tokens(s) for s in skills), key=len, reverse=True)
    kept, i = [], 0
    while i < len(hay):
        for n in needles:
            if n and [t.casefold() for t in hay[i:i + len(n)]] == [t.casefold() for t in n]:
                i += len(n)          # drop the whole matched token run
                break
        else:
            kept.append(hay[i])
            i += 1
    return " ".join(kept)


def validate(cv_text, sources, employers=None, fabrication_decoys=None):
    if not isinstance(sources, BundleSources):
        # Fail loudly at construction. The old second parameter was the rendered bundle
        # TEXT; the position is unchanged, so a stale caller would otherwise reach
        # `sources.nums` and raise AttributeError from inside the gate, which reads as a
        # gate bug rather than a call-site one.
        #
        # The type ONLY, never the value: the stale argument is the user's whole CV source
        # corpus and cv/engine.py:795 logs this exception with %s.
        raise TypeError(
            f"validate() takes a BundleSources, not {type(sources).__name__} -- build it "
            "with cv.bundle.bundle_sources(bundle)")
    # Row 2's vocabulary is NESTED: one token SEQUENCE per source block. A flat
    # `tuple[str, ...]` is valid Python and `_in_source` iterates each member, so a flat
    # value makes every block a STRING that iterates as CHARACTERS -- no multi-token
    # needle can match, every emitted skill reads UNSOURCED, and the lead goes
    # `skipped-gate` after burning its one retry. Measured on
    # `source_tokens=("Example", "Query")` against a bundle that really declared
    # `Example Query`: exactly that, silently, on a value that looks right.
    #
    # Here rather than at construction, for the same reason as the check above: the one
    # producer, `bundle_sources`, cannot get it wrong, so a wrong value can only arrive
    # from a hand-built caller -- and this is where such a value arrives. Unlike the
    # `entries`/`nums` key sets, which are equal by construction (`nums` is derived), the
    # nesting is NOT structural, so nothing else holds it.
    #
    # Checked in FULL rather than on the first member: a partial check would pass a
    # correctly-shaped head with a flat tail, which fails in exactly the same silent way.
    if not all(isinstance(b, (tuple, list)) and all(isinstance(t, str) for t in b)
               for b in sources.source_tokens):
        raise TypeError(
            "validate() takes a BundleSources whose source_tokens is a sequence of "
            "TOKEN SEQUENCES, one per source block -- a flat sequence of strings would "
            "silently report every emitted skill as UNSOURCED; build it with "
            "cv.bundle.bundle_sources(bundle)")
    v = []
    ids, nums, baseline = sources.ids, sources.nums, sources.baseline
    for decoy in (fabrication_decoys or []):
        if decoy.lower() in cv_text.lower():
            v.append(f"FABRICATED: contains '{decoy}'")
    for e in (employers or []):
        if e not in cv_text:
            v.append(f"MISSING EMPLOYER: {e}")
    years = [int(y) for y in re.findall(r"\d{2}/(\d{4})\s*[–-]", cv_text.split("WORK EXPERIENCE")[-1])]
    if years != sorted(years, reverse=True):
        v.append(f"NOT REVERSE-CHRONOLOGICAL: start years {years}")
    # Permitted numbers for PROFILE prose: any figure the bundle actually contains as
    # a source -- every entry's allowlist plus the baseline block -- but NOT the
    # negatives (excluded by the parse). Broader than a WORK bullet, which is tied to
    # its cited entry, because a profile is an aggregate summary. (#30)
    profile_permitted = baseline.union(*nums.values())
    # `skills_lines` (#168, Task 4) is now checked below (row 2) alongside the two
    # checks that existed before it. Row 1 (Task 5, attribution) and digit handling
    # (Task 6) still have no branch here -- this call site does not change again to
    # gain those, only the loop body does.
    profile_lines, work_bullets, skills_lines = section_spans(cv_text)
    # The three line SETS come from `section_spans` so a later scope-limited check
    # (#167) can share this split instead of keeping a second copy that would drift
    # from it. Merged back into ONE line-ordered pass, not separate sequential passes,
    # because more than one region can be live at once (`PROFILE` after `WORK
    # EXPERIENCE`, or `SKILLS` alongside either -- see `section_spans`) and the
    # violation list's order is output: it is fed back to the composer verbatim on the
    # single retry.
    profile_by_line = dict(profile_lines)
    work_by_line = dict(work_bullets)
    skills_by_line = dict(skills_lines)
    for i in sorted(profile_by_line | work_by_line | skills_by_line):
        if i in profile_by_line:
            line = profile_by_line[i]
            # Prose, NOT a bullet: no citation required or expected (requiring [id]
            # on prose invites a fake-citation launder). Strip citations with render's
            # EXACT shape (_CITE_RE) so the check sees what the reader sees -- narrower
            # than the WORK strip on purpose: a non-id bracket like [500] survives to
            # the PDF and MUST be checked, and the profile has no BAD-CITATION backstop
            # behind the strip. (#30)
            prose = _CITE_RE.sub("", line)
            # Digit handling (#168, Task 6): the union of entry `Skills:`, NEVER
            # `sources.source_tokens`. PROFILE has no citation to scope by, but licensing
            # removal from row 2's (SC4) wide vocabulary -- the baseline's and bodies'
            # WORDS -- would let any ordinary word in the user's prose blank an adjacent
            # digit. That is a hole in the numeric gate rather than a fix to it.
            #
            # Extraction only, same shape as the WORK-bullet branch just below: `prose`
            # itself stays UNSTRIPPED of skill spans, because it also feeds the violation
            # MESSAGE (`prose.strip()[:50]`) a few lines down, and `_strip_skill_spans`
            # reconstructs its output by re-tokenising and rejoining with single spaces --
            # it drops everything `_WORD_RE` does not match (a leading bullet marker,
            # original punctuation and spacing) even when nothing is actually stripped.
            # Feeding that reconstruction to the MESSAGE, not just the digit scan, was
            # measured to desync `tests/test_cv_validate.py`'s line-order pin from the raw
            # line it was written against for no gain in what the check detects.
            all_skills = (set().union(*(e.skills for e in sources.entries.values()))
                          if sources.entries else set())
            for n in re.findall(r"\d+", _strip_skill_spans(prose, all_skills)):
                if n not in profile_permitted:
                    v.append(f"INVENTED PROFILE METRIC {n} not in bundle: {prose.strip()[:50]}")
        # The bullet-marker test that selects these lines lives in `section_spans` and
        # must match cv_render_v2.py's bullet markers exactly -- the renderer treats
        # '-', '•', and '*' all as bullets, so a WORK bullet composed with '•' or '*'
        # is delivered in the rendered PDF and MUST be citation-checked here too.
        if i in work_by_line:
            line = work_by_line[i]
            cites = re.findall(r"\[([^\]]+)\]", line)
            if not cites:
                v.append(f"UNCITED BULLET: {line.strip()[:60]}")
                continue
            bad = [c for c in cites if c not in ids]
            if bad:
                v.append(f"BAD CITATION {bad}: not bundle entries - {line.strip()[:50]}")
                continue
            prose = re.sub(r"\[[^\]]+\]", "", line)
            # Digit handling (#168, Task 6): strip each CITED entry's own skill spans
            # before `\d+` extraction, so a digit-bearing skill name (`Example Widget3`)
            # does not read as a fabricated metric. Licensed by the entries THIS BULLET
            # CITES (mirrors `union` two lines down), and decided WITHOUT reference to
            # row 1's verdict just below -- see `_strip_skill_spans`' own docstring for
            # why gating removal on row 1 passing converted every one of its deliberate
            # under-fires into a hard INVENTED METRIC on a skill the user really declared.
            #
            # Indexed WITHOUT a membership guard, deliberately. Every `c` here is already
            # known to be a key of `sources.entries`: the `bad`/BAD CITATION arm five
            # lines up `continue`s on any cite outside `ids`, and `ids` IS
            # `sources.entries.keys()`. `cites` is non-empty for the same reason (the
            # UNCITED BULLET arm). Two `if c in sources.entries` filters used to sit here
            # and on row 1's abstain below; they were unreachable, and worse than merely
            # redundant -- had a `c` ever been missing, filtering it out would have made
            # row 1's `all(...)` vacuously True and RUN the check instead of abstaining,
            # then raised KeyError one line later on `licensed`, which carries no such
            # filter. So the filters could not have protected anything; they only hid
            # that the invariant is upheld by the two `continue`s above.
            cited_skills = set().union(*(sources.entries[c].skills for c in cites))
            bullet_nums = set(re.findall(r"\d+", _strip_skill_spans(prose, cited_skills)))
            union = set().union(*(nums[c] for c in cites))
            invented = bullet_nums - union
            if invented:
                v.append(f"INVENTED METRIC {sorted(invented)} not in {cites}: {prose.strip()[:50]}")
            # ROW 1 (SC2): is this attributed to the right role? Licensed by the entries
            # THIS BULLET CITES -- the identical shape as the numeric rule two lines up,
            # which permits a figure only if a cited entry carries it.
            #
            # ABSTAINS PER-ENTRY (SC5): if ANY cited entry declares no non-empty
            # `Skills:`, this bullet is not checked at all. Measured otherwise: on a
            # partially annotated vault a bullet citing an un-annotated entry, naming a
            # skill from that entry's own body, was a hard violation.
            #
            # CASE-SENSITIVE (SC9): this scans free prose. A candidate whose inventory
            # lists a short common-word skill must not be blocked for using that word in
            # its ordinary sense. Every failure mode is an under-fire, which is the
            # direction a hard gate must err.
            # No membership guard on `cites`, for the reason given at `cited_skills`
            # above -- and it matters most HERE, since filtering would turn an abstain
            # into a run.
            if all(sources.entries[c].skills for c in cites):
                licensed = set().union(*(sources.entries[c].skills for c in cites))
                vocabulary = set().union(*(e.skills for e in sources.entries.values()))
                for item in sorted(vocabulary - licensed):
                    if _names_skill(prose, item):
                        v.append(f"MISATTRIBUTED SKILL {item!r} not in {cites}: "
                                 f"{prose.strip()[:50]}")
        if i in skills_by_line:
            # ROW 2 (SC4): did you invent this? Licensed by the bundle's SOURCE TEXT --
            # entry `Skills:` + entry bodies + the baseline -- because `compose._RULES`
            # and `_DERIVED_NEGATIVE_PROMPT` license exactly those, and #168 asks that an
            # emitted skill "appear in the source bundle", not in `Skills:` alone.
            #
            # ALWAYS RUNS. Never conditional on a non-empty vocabulary: `section_spans`
            # is pure over text, so a section emitted on an un-annotated vault would
            # otherwise be checked by nothing at all. Fail closed.
            #
            # Normalised comparison, unlike row 1's case-sensitive in-prose scan: this
            # compares a whole emitted line against the vocabulary, with no sentence to
            # collide with, and a case-sensitive rule here would refuse every skill whose
            # note is filed lowercase.
            item = _CITE_RE.sub("", skills_by_line[i]).lstrip("-•*–— ").strip()
            if item and not _in_source(sources.source_tokens, item):
                v.append(f"UNSOURCED SKILL {item!r}: not in the bundle")
    return v
