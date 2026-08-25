"""sluice has no OAuth consent flow, and no shipped text may say it has (#104 PR 7).

WHY THIS EXISTS. `docs/INSTALL.md`'s Google section documents obtaining `google_token.json` as a
manual, one-time step you perform yourself, because that is what the code does: `GoogleClient._creds`
calls `Credentials.from_authorized_user_file` and refreshes what it finds, and nothing anywhere
mints a credential. Three shipped surfaces nonetheless told users a sluice command would walk them
through consent -- `README.md`, `docs/TROUBLESHOOTING.md` and `sluice/core/doctor.py`'s own DEGRADED
message, the last of which prints on a real install. Measured on a packaged install: `doctor` said
"the first `track run` will need an interactive OAuth consent" while a token-less `track run` in
fact records a failure row and prompts for nothing. The claim was never true, in any release.

TWO ASSERTIONS, and the first is what licenses the second:

1. The mechanism is absent -- no `google-auth-oauthlib` (the package supplying `InstalledAppFlow`)
   in any dependency list, and no flow entry point in `sluice/`.
2. Given 1, no shipped doc and no `sluice/` string asserts an interactive consent.

So this guard LIFTS ITSELF the day someone implements the flow: assertion 1 fails first, and its
message names the four places whose prose then has to change. A guard that fought a legitimate
feature would just be deleted by whoever added it, taking the doc sync with it.

FOUR THINGS THIS GUARD GOT WRONG FIRST, each found by review and each fixed by measurement rather
than by argument -- recorded because every one of them failed GREEN:

- **A Python string literal can be re-wrapped, and the phrase then spans a quote boundary.**
  `doctor.py`'s message is three adjacent literals; only where the wrap happened to fall kept the
  first version of this pattern honest. Measured, same false sentence, wrap moved one word left:
  `"... an interactive " "OAuth consent")` did NOT match, while the identical text on one line did.
  A `ruff` reflow is routine, so the guard would have gone blind to the exact string it was written
  for.
- **The first fix for that was a regex, and it was wrong twice over.** Joining on a
  quote-whitespace-quote boundary misses a PREFIXED continuation (`"an interactive " f"consent"` -- a shape that ships live in this
  repo today), and it FUSES two adjacent triple-quoted strings into text nobody wrote, which can
  only invent a violation. Both measured. So this parses instead of patching: `ast.parse` hands back
  each string constant with implicit concatenation already resolved by Python's own tokenizer, which
  is correct for every prefix, every quote style and every escape by construction, and cannot fuse
  two separate literals at all. That is this repo's standing rule -- when a narrowing needs a third
  patch, stop patching and parse -- applied on the second.
- **A consent flow written to THIS repo's own stdlib-only rule trips neither licence check.**
  `urllib` + `http.server` + `webbrowser` imports no `InstalledAppFlow` and adds no dependency, so
  the guard would have kept forbidding the prose after the prose became true -- blocking an honest
  doc, which is the failure direction that gets a guard deleted. Google's OAuth endpoint hosts are
  checked too: no flow can avoid talking to them.
- **CHANGELOG.md and `.rulesync/` are deliberately OUT of scope.** A changelog RECORDS what changed,
  and a hand-edited release entry saying which false sentence was removed is doing its job; the same
  goes for `.rulesync/rules/CLAUDE.md`, which is where this repo writes incidents down verbatim so
  the next author does not repeat them. A forbidden-string guard cannot tell "instructs X" from
  "records that X was removed" -- the #170 failure exactly. Neither file is user-facing prose, so
  excluding them costs nothing the guard exists to protect. (An earlier version of this comment
  justified the CHANGELOG exclusion by claiming release-please copies commit BODIES into it. It does
  not -- it renders SUBJECTS and footers, verified against this repo's own generated 2.0.1 section.
  The exclusion is right; that reason for it was not.)

KNOWN GAP, stated rather than disguised. Assertion 2 sweeps the spelling that actually shipped
three times (`interactive ... consent`), not every way English can assert a flow. A sentence
claiming one without that word is not caught. The alternative considered -- flagging every
"consent flow" mention unless a negation appears within N characters -- was dropped after measuring
it: the doctor string's own "**no** token file exists yet" sits 60 characters ahead of its false
claim, so the negation window that admits this file's correct replacements ALSO admits the exact
sentence the guard exists to catch, at any N large enough to be useful. A tuned distance is not a
rule, and this repo has been bitten before by narrowings that each admitted one more construct.
"""
import ast
import glob
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent

# The literal assertive spelling, tolerant of what sat between the two words in the three real
# instances ("interactive consent flow", "interactive OAuth consent"). Bounded to stay inside one
# phrase rather than spanning a paragraph.
_CONSENT_CLAIM = re.compile(r"interactive(?:[\w ]|\n(?!\s*\n)){0,12}consent", re.I)

# No consent flow can avoid Google's own OAuth endpoints, whatever library (or none) reaches them.
_OAUTH_ENDPOINT = re.compile(r"accounts\.google\.com|oauth2\.googleapis\.com")

# Flow entry points from the usual libraries. Kept alongside the endpoint check rather than
# replaced by it: a vendored or mocked helper may name the class without the host, and vice versa.
_FLOW_ENTRY = re.compile(r"InstalledAppFlow|run_local_server|run_console|from_client_secrets_file")

# What a user reads, plus the modules whose runtime strings are what they see on a real install.
# CHANGELOG.md and .rulesync/ are excluded ON PURPOSE -- see this module's docstring.
_PROSE = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "sluice.yaml.example"]
_PROSE += sorted(glob.glob("docs/*.md", root_dir=ROOT))
_CODE = sorted(glob.glob("sluice/**/*.py", root_dir=ROOT, recursive=True))
_SHIPPED = _PROSE + _CODE


def _identifiers(rel: str, text: str) -> set[str]:
    """Every NAME a Python module binds or references, plus its string constants.

    Deliberately not the raw source: comments and docstrings are prose, and prose that DENIES the
    flow ("no `run_local_server` anywhere in this codebase") is exactly what a repo with this
    comment convention would write. Reading it as evidence of a mechanism makes the guard fail
    the build for a correct explanation.
    """
    names: set[str] = set()
    tree = ast.parse(text)
    # DOCSTRINGS are prose, exactly like comments, and must not count as evidence either -- a
    # module docstring saying "this module deliberately has no InstalledAppFlow" is the same
    # denial in a different node type. Identified by POSITION (the first statement of a module,
    # class or function), which is what makes a docstring a docstring; a string constant
    # anywhere else is a value the code actually uses.
    docstrings = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(n, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and id(n) in docstrings:
            continue
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
        elif isinstance(n, ast.alias):
            names.add(n.name)
            if n.asname:
                names.add(n.asname)
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            names.add(n.value)
    return names


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


def test_sluice_ships_no_oauth_consent_flow():
    """The fact `docs/INSTALL.md`'s Google section is written from.

    If you are here because you ADDED a consent flow: good -- delete this test, and update
    `docs/INSTALL.md`'s "Google access for `track`" section, `README.md`'s requirements list,
    `docs/TROUBLESHOOTING.md`'s reauth section, and `classify_track_google`'s DEGRADED message in
    `sluice/core/doctor.py`. All four currently tell the user the acquisition step is theirs.
    """
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "google-auth-oauthlib" not in pyproject and "google_auth_oauthlib" not in pyproject, (
        "google-auth-oauthlib is declared -- if sluice now runs the OAuth consent flow, the four "
        "doc sites named in this test's docstring describe the old manual procedure and are wrong")
    offenders = []
    for rel in _CODE:
        p = ROOT / rel
        if not p.exists():
            continue
        idents = _identifiers(rel, p.read_text(encoding="utf-8"))
        if any(_FLOW_ENTRY.search(i) or _OAUTH_ENDPOINT.search(i) for i in idents):
            offenders.append(rel)
    assert not offenders, (
        f"a consent-flow entry point or a Google OAuth endpoint appeared in {offenders} -- see "
        "this test's docstring for the four doc sites that then need updating")


def test_no_shipped_text_claims_an_interactive_consent():
    """The prose half. Licensed by the test above: while no flow exists, this claim cannot be true.

    Every legitimate mention in this repo denies the flow ("never runs the consent flow itself",
    "There is no consent flow in the codebase"), and none reaches for the word `interactive` to do
    it -- which is why this needs no negation handling and so cannot be defeated by phrasing that
    happens to put a `no` somewhere earlier in the sentence.
    """
    offenders = [(rel, m.group(0))
                 for rel, texts in _shipped_texts()
                 for m in (next((x for x in map(_CONSENT_CLAIM.search, texts) if x), None),)
                 if m]
    assert not offenders, (
        f"shipped text asserts an interactive OAuth consent sluice does not perform: {offenders}. "
        "sluice reads and refreshes an existing token; producing it is the user's step -- see "
        "docs/INSTALL.md's 'Google access for track'.")


def test_the_claim_pattern_catches_the_three_sentences_that_actually_shipped():
    """The falsify partner, against SYNTHETIC input.

    Pinned here rather than against the live tree because the live tree is CLEAN now: an assertion
    made against the real files passes today and would start failing for the wrong reason the
    moment the shape reappears. The three strings below are the ones this PR removed; the three
    after them are their replacements, which must NOT trip the pattern.
    """
    shipped_and_false = [
        "obtained on first `track run` via an interactive consent flow",
        "`track` will walk you through the interactive consent flow again",
        "google libs are importable but no token file exists yet -- the first `track run` "
        "will need an interactive OAuth consent",
    ]
    for sentence in shipped_and_false:
        assert _CONSENT_CLAIM.search(sentence), f"pattern missed a real instance: {sentence!r}"

    corrected = [
        "sluice reads and refreshes that credential but never runs the consent flow itself",
        "sluice reads and refreshes the token but never runs the OAuth consent flow itself",
        "sluice does not run the OAuth consent flow itself; see docs/INSTALL.md",
    ]
    for sentence in corrected:
        assert not _CONSENT_CLAIM.search(sentence), f"pattern flags correct prose: {sentence!r}"


def test_a_re_wrapped_python_literal_cannot_hide_the_claim():
    """The wrap-position hole, pinned as real Python SOURCE rather than as a pre-joined string.

    `doctor.py`'s message spans three adjacent literals. With the wrap one word to the left the
    phrase straddles a quote boundary and a search over the raw file misses it entirely -- which is
    how this guard would have gone blind to the exact sentence it was written for, silently, on a
    routine reflow. The raw miss is asserted too, so this row cannot quietly become a no-op if the
    pattern is ever widened to cross quotes on its own.

    The f-prefixed row is the one a REGEX fix got wrong: a prefix on the continuation is not a
    quote-to-quote boundary, and that shape ships live in this repo today, so the earlier
    quote-whitespace-quote join left the hole open for it. Parsing has no such case to remember.
    """
    for label, src in (
        ("plain continuation",
         'MSG = ("no token file exists yet -- the "\n'
         '       "first `track run` will need an interactive "\n'
         '       "OAuth consent")\n'),
        ("f-prefixed continuation",
         'MSG = ("no token file exists yet -- the "\n'
         '       f"first `track run` will need an interactive "\n'
         '       "OAuth consent")\n'),
        ("implicitly joined on one line",
         'MSG = ("will need an interactive " "OAuth consent")\n'),
    ):
        assert not _CONSENT_CLAIM.search(src), (
            f"[{label}] the raw pattern is expected to miss this -- if it now matches, parsing is "
            "no longer what closes the wrap hole and this row proves nothing")
        assert any(_CONSENT_CLAIM.search(t) for t in _searchable("sluice/core/doctor.py", src)), (
            f"[{label}] a re-wrapped literal hid the claim from the sweep")


def test_parsing_never_fuses_two_separate_literals():
    """The failure direction that INVENTS a violation, which is worse than missing one.

    A regex join across `"` … `"` fuses two adjacent triple-quoted strings into text nobody wrote,
    and would fail the build on prose that does not exist in the file. `ast` cannot do this: each
    constant is a separate node, and only genuine implicit concatenation is ever one node.
    """
    src = ('A = """ends in interactive"""\n'
           'B = """consent begins"""\n'
           'C = ("interactive", "consent")\n'
           'D = {"interactive": "consent"}\n'
           'E = "interactive" + "consent"\n')
    assert not any(_CONSENT_CLAIM.search(t) for t in _searchable("x.py", src)), (
        "separate literals were fused into a phrase that is not in the source")
    joined = _searchable("x.py", 'M = ("inter" "active consent")\n')
    assert any("interactive consent" == t for t in joined), (
        "genuine implicit concatenation was not resolved")


def test_a_python_file_that_cannot_be_parsed_is_loud_rather_than_skipped():
    """A NEGATIVE sweep that silently drops a file reports success for the file it never read."""
    import pytest
    with pytest.raises(AssertionError, match="does not parse"):
        _searchable("sluice/broken.py", "def (:\n")


def test_a_comment_denying_the_flow_does_not_read_as_evidence_of_one():
    """The FALSE-POSITIVE direction, which is the worse one for a guard like this.

    The mechanism check first searched raw source, so a comment written to DENY the flow tripped
    it -- measured, `# no run_local_server anywhere in this codebase` failed the build with a
    message asserting a flow had been added. In a repo whose convention is dense explanatory
    comments, the only actionable reading of that failure is "delete the explanation", so the
    guard would have destroyed the very prose it exists to keep honest.

    An import, a call and an attribute are what a real flow needs. A comment cannot authorise one.
    """
    for denial in ("# we never contact accounts.google.com ourselves\nx = 1\n",
                   "# no run_local_server anywhere in this codebase\nx = 1\n",
                   '"""This module deliberately has no InstalledAppFlow."""\nx = 1\n'):
        idents = _identifiers("sluice/x.py", denial)
        assert not any(_FLOW_ENTRY.search(i) or _OAUTH_ENDPOINT.search(i) for i in idents), denial


def test_a_real_mechanism_is_still_caught_however_it_is_spelled():
    """The partner to the row above: narrowing to identifiers must not narrow past the target.

    Four spellings, each a different AST node -- an import alias, a method call, an attribute, and
    a bare endpoint literal (the stdlib-only flow this repo's own rules would push someone toward,
    which imports nothing recognisable and so is caught by the host instead).
    """
    for label, src in (("import", "from google_auth_oauthlib.flow import InstalledAppFlow\n"),
                       ("aliased", "from x import InstalledAppFlow as _f\n"),
                       ("call", "flow.run_local_server(port=0)\n"),
                       ("endpoint", 'AUTH = "https://accounts.google.com/o/oauth2/v2/auth"\n')):
        idents = _identifiers("sluice/x.py", src)
        assert any(_FLOW_ENTRY.search(i) or _OAUTH_ENDPOINT.search(i) for i in idents), label


def test_the_claim_pattern_does_not_span_a_paragraph_break():
    """`[\\w\\s]` includes a newline, so two unrelated sentences could match across a blank line.

    A SINGLE newline must still match -- markdown reflow legitimately puts one between the two
    words, and that is the whole reason the gap is permissive at all.
    """
    assert not _CONSENT_CLAIM.search("...was interactive.\n\nConsent is yours to obtain.")
    assert _CONSENT_CLAIM.search("will need an interactive OAuth\nconsent")
