"""No shipped text may repeat a consent claim that was measured false (#104 PR 7, #201).

WHY THIS EXISTS. Three shipped surfaces once told users a sluice command would walk them through
Google's OAuth consent while nothing in the tree could open a browser: `README.md`,
`docs/TROUBLESHOOTING.md`, and `sluice/core/doctor.py::classify_track_google`'s own message, the
last of which prints on a real install. Measured on a packaged install, `doctor` said "the first
`track run` will need an interactive OAuth consent" while a token-less `track run` in fact recorded
a failure row and prompted for nothing. That measurement is scoped to the DOCTOR string alone --
the other two sentences were read off the docs, not executed -- and an earlier draft of this
paragraph over-attributed it to all three.

WHAT #201 CHANGED, and why the guard this file replaces could not survive it. The old guard rested
on two assertions, the first licensing the second:

1. The mechanism is absent -- no `google-auth-oauthlib` in any dependency list, no flow entry point
   and no Google OAuth host anywhere in `sluice/`.
2. Given 1, no shipped doc and no `sluice/` string asserts an interactive consent, because while no
   flow exists the claim cannot be true.

#201 implements the flow (`sluice/track/auth.py::run_consent_flow`, reached through
`sluice/core/app.py::track_auth` by `job-sluice track auth`), so assertion 1 is false BY DESIGN
and every test
that rested on it dies with it. The old docstring said this guard would lift itself the day someone
implemented the flow; this is that day. `git log -- tests/test_no_false_consent_flow_claim.py` has
the mechanism machinery -- `_identifiers`, the flow-entry-point and OAuth-host patterns, and the
rows pinning that a COMMENT denying the flow must not read as evidence of one -- if a future change
ever needs to assert absence again.

THE PROPERTY THAT SURVIVES is narrower and still true. The mint lives in its own module behind its
own command: `RealGoogleClient._creds` still only READS and refreshes a credential it finds, and
gained no path that can produce one -- #201's only edit to it is the renamed writer it calls. So a
token-less `track run` still records a failure row and exits without prompting anyone, which
`test_track_engine.py::test_a_missing_google_token_is_a_failure_row_not_a_reauth_and_not_a_prompt`
executes rather than asserts from the code. What must never reappear is a sentence attributing
consent to `track run` -- or to bare `track`.

WHY THERE IS NO LONGER A PATTERN. Attribution is not mechanically expressible as one, and three
measurements say so rather than three arguments:

- **The old `interactive ... consent` pattern matches the HONEST new prose.** It has to: what
  `track auth` now does IS an interactive consent, so every true sentence about it reaches for the
  same two words. Measured against honest sentences about what the command does ("`job-sluice
  track auth` runs the interactive consent flow in your browser"), the pattern matches all of
  them. Armed
  unchanged it fails the build on true text, which the old docstring itself names as the failure
  direction that gets a guard deleted.
- **Keying on the command does not separate them either, in either direction.** One of the three
  sentences that actually shipped attributes consent to bare `track` ("`track` will walk you
  through the interactive consent flow again") and contains no `track run` at all -- measured, so a
  check keyed on `track run` drops it silently. Keyed on bare `track` instead, the check fails every
  honest new sentence, because all of them carry that token in `track auth`. No proximity or
  negation window separates the two: the old docstring already records that approach being tried
  and rejected on the doctor string, whose own "no token file exists yet" sits some sixty characters
  ahead of its false claim, so any window wide enough to admit the corrections also admits the
  sentence the guard exists to catch.
- **The old `corrected` roster cannot serve as a must-keep-passing set.** None of those three
  sentences contains the word `interactive`, so they pass the old pattern today and cannot
  distinguish a narrowed pattern from an unnarrowed one -- and they are denials near-verbatim the
  live prose #201 deletes.

SO THIS IS A RATCHET, which is what this repo does when nothing local can classify --
`tests/test_fixture_name_neutrality.py`'s reviewed-identity roster is the same shape.
`_SHIPPED_FALSE` holds the sentences that ACTUALLY shipped false, as data, compared as
normalised exact strings. A ratchet over known values cannot be defeated by phrasing, because it
makes no claim about phrasing.

TWO MECHANISMS STAY, and both are still load-bearing under exact-string comparison:

- **`_searchable` PARSES rather than greps the code half.** A Python string literal can be
  re-wrapped and the phrase then straddles a quote boundary: measured on `doctor.py`'s three-literal
  message, the same false sentence with the wrap moved one word left did NOT match a raw search
  while the identical text on one line did. A `ruff` reflow is routine, so a raw search would go
  blind to the exact string it was written for. The first fix for that was a regex join across a
  quote-whitespace-quote boundary, and it was wrong in BOTH directions, each measured: it MISSES a
  prefixed continuation (`"an interactive " f"consent"`, a shape that ships live in this repo
  today), and it FUSES two adjacent triple-quoted strings into text nobody wrote, which can only
  invent a violation. `tests/test_doc_links_from_code.py` cites this file as the record of that
  cost, so both halves are kept here rather than summarised. `ast.parse` hands back each constant
  with implicit concatenation already resolved by Python's own tokenizer -- correct for every
  prefix, quote style and escape by construction -- and cannot fuse two separate literals at all.
  That is this repo's standing rule: when a narrowing needs a third patch, stop patching and
  parse.
- **Whitespace is normalised on BOTH sides.** The code half arrives pre-joined, but the prose half
  arrives as raw markdown, and two of these sentences shipped in README and TROUBLESHOOTING where a
  reflow moves the line breaks. Normalising only the needle would silently stop matching. TWO
  things together make that falsifiable, and neither is sufficient alone. The comparison
  lives in ONE function, `_catches`, called by the ratchet and by
  `test_the_comparator_catches_a_planted_sentence` alike, so a deletion lands in code the
  partner executes -- measured while the two compared inline, deleting the haystack half from
  the ratchet's own loop left the partner green and blinded the ratchet to reflowed prose. And
  that partner plants its markdown copy REFLOWED across a line break, asserting the raw text
  misses it, because through a shared comparator a ONE-LINE fixture would still match with the
  haystack half deleted.

SCOPE. `CHANGELOG.md` and `.rulesync/` are deliberately OUT of it. A changelog RECORDS what changed,
and a hand-edited release entry naming which false sentence was removed is doing its job; the same
goes for `.rulesync/rules/CLAUDE.md`, which is where this repo writes incidents down verbatim so the
next author does not repeat them. A forbidden-string check cannot tell "instructs X" from "records
that X was removed" -- the #170 failure exactly. Neither is user-facing prose, so excluding them
costs nothing this guard exists to protect. The same latitude is NOT extended to `sluice/` source
comments, which are swept as raw text: a false sentence quoted verbatim in a comment trips the
ratchet, and the place to record one is the changelog or `.rulesync/`.

RESIDUAL, stated rather than disguised. A NEW false attribution, worded differently from the three
below, is not caught here. That is a real loss against the old guard's AMBITION and not against its
reach, since the old pattern could not distinguish attribution either -- the three measurements
above are why. The doctor row is the one narrow inverse cover: whatever else that message says, the
command it names must be one the real parser accepts, which is what a partial doc edit leaving an
inverse claim printing on every fresh install would break.
"""
import ast
import glob
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

# The sentences that ACTUALLY SHIPPED false, kept as DATA. A ratchet over known values, not a
# classifier: nothing local can decide whether a NEW sentence misattributes consent, and this
# module's docstring records the three measurements that killed every attempt to pattern-match it.
_SHIPPED_FALSE = (
    "obtained on first `track run` via an interactive consent flow",
    "`track` will walk you through the interactive consent flow again",
    "google libs are importable but no token file exists yet -- the first `track run` "
    "will need an interactive OAuth consent",
)

# What a user reads, plus the modules whose runtime strings are what they see on a real install.
# CHANGELOG.md and .rulesync/ are excluded ON PURPOSE -- see this module's docstring.
_PROSE = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "sluice.yaml.example"]
_PROSE += sorted(glob.glob("docs/*.md", root_dir=ROOT))
_CODE = sorted(glob.glob("sluice/**/*.py", root_dir=ROOT, recursive=True))
_SHIPPED = _PROSE + _CODE


def _norm(text: str) -> str:
    """Collapse whitespace, applied to the HAYSTACK as well as the needle.

    The code half arrives pre-joined by `ast`, but the prose half is raw markdown and two of these
    sentences shipped in README/TROUBLESHOOTING, where a reflow moves the line breaks. Comparing
    raw would silently stop matching the exact text this guard exists to hold down.
    """
    return " ".join(text.split())


def _catches(texts, sentence: str) -> bool:
    """THE comparator. One function, called by the ratchet and by its anti-vacuity partner alike.

    It is a function rather than two inline comprehensions because the partner can only falsify a
    line it actually EXECUTES. Measured while both sites compared inline: deleting the haystack
    half of the normalisation from the ratchet's own loop (`[_norm(t) for t in texts]` ->
    `list(texts)`) left the partner GREEN while blinding the ratchet to reflowed prose -- which is
    how two of the three `_SHIPPED_FALSE` sentences actually shipped. The mutant was not
    equivalent; against the sentence planted across a line break it went True -> False with the
    whole suite passing.

    So the rule this file already applies to fixtures applies to comparators too: a guard's
    falsify-partner must exercise the SAME code the verdict is read from, not a copy of it.
    """
    needle = _norm(sentence)
    # Normalised per TEXT and never concatenated into one haystack. Joining a file's string
    # constants would fuse two separate literals into a phrase nobody wrote, which can only INVENT
    # a violation -- the failure direction that is worse than missing one, and the measured reason
    # `_searchable` parses instead of grepping. `test_two_separate_literals_are_never_fused`
    # is what holds this, and it reddens if the join is ever reintroduced here.
    return any(needle in _norm(t) for t in texts)


def _searchable(rel: str, text: str) -> list[str]:
    """The texts to search for `rel`: the raw file, and for Python every string CONSTANT.

    The second element is what closes the re-wrap hole. Python's own parser joins adjacent
    literals into one `ast.Constant` before this code ever sees them, so a message split across
    three lines -- with any prefix, any quote style, any escape -- arrives here as the single
    string a user will actually read. Searching the raw text as well keeps comments and anything
    outside a literal in scope.

    A `.py` that does not parse raises rather than being skipped. Skipping would drop a file out
    of a NEGATIVE sweep silently, which is this guard's whole failure mode.
    """
    if not rel.endswith(".py"):
        return [text]
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise AssertionError(f"{rel} does not parse, so it cannot be swept: {e}") from e
    return [text] + [n.value for n in ast.walk(tree)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _shipped_texts():
    """(path, [texts to search]) for every swept file that exists.

    Returned as a list so a caller can assert on the SCOPE. A sweep that silently reads nothing
    satisfies every assertion made over it -- `all([])` is `True` -- and for a negative guard like
    this one, finding no violations IS the success case, so the empty-set failure is invisible.
    """
    out = []
    for rel in _SHIPPED:
        p = ROOT / rel
        if p.exists():
            out.append((rel, _searchable(rel, p.read_text(encoding="utf-8"))))
    return out


def test_the_sweep_reads_the_files_it_means_to():
    """SCOPE, asserted before any verdict is read off the sweep below.

    PROSE and CODE are counted SEPARATELY, and that separation is the point. `sluice/**/*.py` is
    over a hundred files, so a single combined threshold stayed satisfied with every hand-listed
    prose file deleted -- the widened scope was pinned by nothing at all, and would have silently
    narrowed back to what it was before. Each hand-listed name is asserted individually for the
    same reason; `docs/*.md` is DERIVED from disk, so a doc added later cannot quietly sit outside.
    """
    texts = _shipped_texts()
    names = {rel for rel, _ in texts}
    prose = {n for n in names if not n.endswith(".py")}
    code = {n for n in names if n.endswith(".py")}

    assert len(code) > 50, f"the code sweep found only {len(code)} modules"
    for required in ("README.md", "CONTRIBUTING.md", "SECURITY.md", "sluice.yaml.example",
                     "docs/INSTALL.md", "docs/TROUBLESHOOTING.md"):
        assert required in prose, f"{required} fell out of the prose sweep"
    assert "sluice/core/doctor.py" in code, "doctor.py fell out of the code sweep"

    on_disk_docs = set(glob.glob("docs/*.md", root_dir=ROOT))
    assert on_disk_docs, "no docs/*.md matched at all -- the glob is broken, not the docs tree"
    assert on_disk_docs <= prose, f"docs fell out of the sweep: {sorted(on_disk_docs - prose)}"

    assert "CHANGELOG.md" not in names, (
        "CHANGELOG.md must stay out of scope -- a release entry recording which false sentence "
        "was removed is the changelog doing its job, and a forbidden-string guard cannot tell "
        "that from an instruction (#170)")
    assert not any(n.startswith(".rulesync/") for n in names), (
        ".rulesync/ must stay out of scope for the same reason -- it is where this repo records "
        "incidents verbatim, and it is not user-facing prose")


def test_no_shipped_text_repeats_a_sentence_that_was_false():
    """THE RATCHET. Exact normalised strings, no pattern -- this module's docstring says why.

    Read the SCOPE assertion above before this verdict: finding nothing is the success case here,
    so a sweep that read nothing would satisfy this test in full.
    """
    offenders = [(rel, s) for rel, texts in _shipped_texts()
                 for s in _SHIPPED_FALSE if _catches(texts, s)]
    assert not offenders, (
        f"shipped text repeats a sentence that was measured false: {offenders}. "
        "`job-sluice track auth` mints the credential; `track run` still never prompts, and "
        "neither does bare `track`.")


@pytest.mark.parametrize("sentence", _SHIPPED_FALSE)
def test_the_comparator_catches_a_planted_sentence(sentence):
    """ANTI-VACUITY, and the scope test above cannot stand in for it.

    That test asserts what is READ; this one asserts what is FOUND. A comparator broken in any way
    -- a normalisation dropped, the `ast` half of `_searchable` deleted, a needle mistyped -- finds
    nothing, and finding nothing is exactly what success looks like for a negative guard. This
    repo has shipped that shape before.

    Both fixtures are deliberately hostile, and each carries a partner assertion that the RAW
    text misses the sentence. Without those partners a fixture can rot into a plain one-line
    copy that matches whatever the comparator does, and the row goes on passing while proving
    nothing. The two partners compare DIFFERENTLY on purpose, because they pin different
    mechanisms: the markdown one is checked against UNNORMALISED text, since normalising the
    haystack is exactly what must rescue it, while the Python one is checked against
    NORMALISED text, since no amount of whitespace collapsing can cross a quote boundary and
    only `ast` can.
    """
    # PROSE half, REFLOWED across a line break on purpose: two of these sentences shipped in
    # README/TROUBLESHOOTING, where a reflow is routine. A one-line fixture would still match with
    # the haystack half of `_norm` deleted, leaving that half unfalsifiable.
    cut = sentence.rfind(" ", 0, len(sentence) // 2)
    assert cut > 0, f"fixture needs a word boundary to wrap at: {sentence!r}"
    md = f"Some prose.\n{sentence[:cut]}\n{sentence[cut + 1:]}\nMore prose.\n"
    assert _norm(sentence) not in md, (
        "the markdown fixture is not actually reflowed, so it would match without normalising the "
        "haystack and proves nothing about it")
    assert _catches(_searchable("x.md", md), sentence), (
        "a reflowed markdown sentence hid from the comparator")

    # CODE half, split MID-SENTENCE so the phrase straddles a quote boundary -- the shape that
    # measurably hid `doctor.py`'s message from a raw search when a wrap moved one word left.
    # Each half is rendered with `%r` rather than interpolated raw, so a ratchet entry added later
    # carrying a quote or a backslash yields a real literal instead of a parse error.
    half = len(sentence) // 2
    src = "MSG = (\n    %r\n    %r\n)\n" % (sentence[:half], sentence[half:])
    assert _norm(sentence) not in _norm(src), (
        "the python fixture is not re-wrapped, so it would match on raw text and proves nothing "
        "about `ast` resolving implicit concatenation")
    assert _catches(_searchable("x.py", src), sentence), (
        "a re-wrapped python literal hid the sentence from the comparator")


@pytest.mark.parametrize("sentence", _SHIPPED_FALSE)
def test_two_separate_literals_are_never_fused(sentence):
    """The failure direction that is WORSE than missing one: inventing a violation.

    A comparator that joins a file's texts into one haystack reports prose nobody wrote -- it would
    fail the build on a sentence that exists in no file, and the only actionable reading of that
    failure is to delete text that is already correct. Two adjacent triple-quoted literals are the
    shape that does it: separate `ast.Constant` nodes, never implicitly concatenated, sitting next
    to each other in the source. This was a test of the old pattern and is re-expressed here
    without one -- the hazard belongs to the COMPARATOR, not to how the needle is spelled.

    Measured: with `_catches` mutated to `needle in _norm(" ".join(texts))`, every row here goes
    red, which is what makes this a guard rather than a comment.
    """
    assert '"' not in sentence, (
        f"fixture needs a quote-free sentence to sit in a triple-quoted literal: {sentence!r}")
    cut = sentence.rfind(" ", 0, len(sentence) // 2)
    assert cut > 0, f"fixture needs a word boundary to split at: {sentence!r}"
    src = 'A = """%s"""\nB = """%s"""\n' % (sentence[:cut], sentence[cut + 1:])
    assert not _catches(_searchable("x.py", src), sentence), (
        "two separate literals were fused into a phrase that is in no file, which would fail the "
        "build on prose nobody wrote")


def test_a_python_file_that_cannot_be_parsed_is_loud_rather_than_skipped():
    """A NEGATIVE sweep that silently drops a file reports success for the file it never read."""
    with pytest.raises(AssertionError, match="does not parse"):
        _searchable("sluice/broken.py", "def (:\n")


def test_doctors_missing_token_remedy_is_an_invocation_the_real_parser_accepts():
    """The one runtime string the original incident was measured on, and the one #201 hand-edits.

    The inverse cover for the ratchet's residual: the ratchet holds down the sentence that shipped,
    while this holds down the sentence that replaced it. A partial doc edit that left the old
    attribution in place, or renamed the command out from under it, prints on every fresh install
    where no doc sweep would ever see it -- so the remedy is fed to the REAL parser rather than
    compared against a name written down twice.

    It checks the WHOLE invocation, flags included, not just the group and subcommand -- an earlier
    cut asserted only the command while hardcoding `--client-secrets`, so a renamed flag failed a
    test whose name claimed to check something else. The flags are taken from the message itself,
    so nothing here is a second place to keep that spelling in step. Angle-bracketed placeholders
    are the message's own convention for "your value here" and stand in for one argument each.

    `allow_abbrev` is left ON, so an unambiguous PREFIX of a real flag passes here. That is right
    rather than a gap: the question is whether the invocation the message prints works when a user
    types it, and an abbreviation does. A genuine rename does not -- measured, `--secrets-file`
    exits 2 on the required argument it no longer supplies.
    """
    from sluice.cli import _build_parser
    from sluice.core.doctor import classify_track_google
    detail = classify_track_google(available=True, import_error=None,
                                   token_present=False, token_path="/x/t.json").detail
    m = re.search(r"`job-sluice ([^`]+)`", detail)
    assert m, f"doctor's remedy names no backticked job-sluice invocation: {detail!r}"
    argv = re.sub(r"<[^>]*>", "PLACEHOLDER", m.group(1)).split()
    assert len(argv) >= 2, f"the remedy names no group and subcommand: {m.group(1)!r}"
    _build_parser().parse_args(argv)
