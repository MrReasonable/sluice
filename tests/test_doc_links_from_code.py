"""Every doc link `sluice/` prints to a user must resolve -- file AND anchor (#104 PR 7).

WHY. Three runtime strings point a stuck user at documentation: the renderer's
"could not load its rendering backend" error (`sluice/renderers/template.py`), `doctor`'s
equivalent for the same failure, and `doctor`'s missing-google-token row (`sluice/core/doctor.py`).
These are printed to a TERMINAL, not to a contributor reading the repo, so they name absolute URLs
rather than repo-relative paths -- a `.deb`, an `.rpm`, the container image and a brew venv all ship
`sluice/` with no docs tree, and until this branch two of these strings said "README.md" to users
who had no README.

An absolute URL fixes reachability and introduces a new silent failure in its place: nothing
notices when the heading it anchors to is renamed. `docs/INSTALL.md` is over three hundred lines,
so a stale `#anchor` does not 404 -- it drops the reader at the top of a long document with no sign
anything went wrong, which is the same "quietly unhelpful" outcome the URLs were meant to end.

WHY THE STRINGS ARE READ WITH `ast` RATHER THAN GREPPED. A URL long enough to carry a path and an
anchor is longer than the line width, so it is written as adjacent literals and no single line
contains it. A regex over the raw source finds the halves and never the whole; Python's own parser
resolves implicit concatenation before this code sees it. The sibling guard in
`tests/test_no_false_consent_flow_claim.py` records the measured cost of trying to do that join by
hand instead.

TWO SWEEPS, and the second was added a round later. Links emitted from CODE are the reason this
file exists. Links from one DOC to another carry the identical anchor hazard -- GitHub resolves the
file and validates no `#fragment`, so a renamed heading drops the reader at the top of a long page
with nothing to say it happened -- and nothing else in the suite looks at them.

SCOPE, and its edges, stated rather than left to be discovered. `_DOC_URL` is anchored on the
literal `blob/main/`, so a `/tree/` link, a tag-pinned one, or one carrying a query string is
invisible to this sweep -- none is used today, and widening on speculation is how a pattern starts
matching prose. `_string_constants` reads `ast.Constant`, so an f-string carrying an INTERPOLATION
arrives as its separate literal pieces rather than one value; a url split across `{}` would be
missed. Adjacent f-strings without interpolation do fold to one constant, which is the case that
actually occurs.

Both sweeps read only what a reader can actually FOLLOW: `_outside_fences` is applied to the
document on the target side (so a `#` inside a shell snippet is not a heading) and on the source
side (so a link inside a fence or an HTML comment is not a shipped link). Getting that asymmetric
in either direction fails in a different way -- a dead anchor reads live, or a documented example
fails the build.

Links emitted FROM code, not links between documents. A doc-to-doc link is
followed by a reader who can see the file tree and by GitHub's own renderer; a link in an error
message is followed by someone whose install is already broken.
"""
import ast
import glob
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent

_BLOB = "https://github.com/MrReasonable/sluice/blob/main/"
# The path, then an optional #anchor. Applied to fully-resolved string constants, never raw source.
_DOC_URL = re.compile(re.escape(_BLOB) + r"(?P<path>[\w./-]+\.md)(?:#(?P<anchor>[\w-]+))?")

# GitHub's heading-slug rules: case-folded, punctuation dropped, then EACH remaining space becomes
# one hyphen -- runs are NOT collapsed, which is why `## deb / rpm` slugs to `deb--rpm` and not
# `deb-rpm`: dropping the slash leaves two spaces behind. Written as a collapse first; the falsify
# partner below caught it against that very heading, which is in this repo's own INSTALL.md.
# Backticks matter too -- `## Google access for `track`` slugs to `google-access-for-track`.
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(?P<title>.+?)\s*#*\s*$", re.M)

# A fence opener, and the rule for what closes it. Three or more backticks or tildes, indented no
# more than three spaces (four makes it an indented code block, not a fence).
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(?P<rest>.*)$")

# `<!-- ... -->`, including across lines. Applied BEFORE fence stripping, so a comment wrapping a
# fence cannot leave a dangling opener behind.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _outside_fences(text: str) -> str:
    """`text` with HTML comments and fenced code blocks removed -- neither is document structure.

    Measured on this repo's own `docs/INSTALL.md`: without this, `# Debian, Ubuntu` and `# Fedora`
    -- shell comments inside a bash fence in the deb/rpm section -- became the slugs
    `debian-ubuntu` and `fedora`. An anchor pointing at either passed the check below while GitHub
    resolved nothing, which is exactly the silent drop-at-the-top-of-a-long-file this file exists
    to end. README.md and TROUBLESHOOTING.md contribute more of them.

    A fence is closed only by the SAME character, at least as long, with nothing but whitespace
    after it. That is CommonMark, and this repo already learned it the hard way somewhere else:
    `tests/test_docs_claims.py`'s `_without_breaking_blocks` carries the same three conditions,
    and its own comment records the incident -- a `### BREAKING CHANGES` line quoted inside a
    fence exempted everything after it from the retired-key sweep. Two implementations rather
    than one shared helper, deliberately, matching this suite's file-scoped-helper convention;
    if you change the rule here, check that one too.

    HTML COMMENTS go first, and that ordering matters twice over. A `## Heading` inside
    `<!-- ... -->` produced a live slug, so an anchor pointing at commented-out content passed
    while GitHub resolved nothing -- the same fail-open as the fenced case, one layer along. And
    stripping them BEFORE fences means a comment that wraps a fence cannot leave a dangling
    opener behind, which would have swallowed the rest of the file.
    """
    out, fence = [], None
    for line in _HTML_COMMENT.sub("", text).splitlines():
        m = _FENCE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
            else:
                out.append(line)
        elif (m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence)
              and not m.group("rest").strip()):
            # Same character, at least as long, and NOTHING but whitespace after it. That last
            # clause is not pedantry: an info string (```python) opens a fence and can never
            # close one, so without it a fenced block containing another fence's opener ends
            # early and the code after it is read as document text.
            fence = None
    return "\n".join(out)


_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _slug(title: str) -> str:
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", _MD_LINK.sub(r"\1", title).strip().lower()))


def _string_constants(path: Path) -> list[str]:
    """Every string constant in `path`, implicit concatenation already resolved by the parser."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _doc_links_in_code():
    """(module, url, path, anchor) for every doc URL a `sluice/` module can print."""
    found = []
    for rel in sorted(glob.glob("sluice/**/*.py", root_dir=ROOT, recursive=True)):
        for text in _string_constants(ROOT / rel):
            for m in _DOC_URL.finditer(text):
                found.append((rel, m.group(0), m.group("path"), m.group("anchor")))
    return found


def test_the_sweep_finds_the_links_it_means_to():
    """SCOPE. A sweep that matches nothing passes every assertion made over it.

    This one is a POSITIVE sweep -- the links exist and are meant to -- so a non-empty result is
    the honest non-vacuity check, unlike the negative guards elsewhere in this suite. Both modules
    that carry such a link are named, so deleting the ast reader or narrowing the pattern until it
    matches only one of them fails here rather than passing quietly.
    """
    links = _doc_links_in_code()
    assert links, (
        "no doc URLs found in sluice/ at all -- either every runtime string stopped naming one, "
        "or this sweep stopped reading them (it needs the parser: these URLs span line breaks)")
    modules = {rel for rel, _, _, _ in links}
    for expected in ("sluice/core/doctor.py", "sluice/renderers/template.py"):
        assert expected in modules, f"{expected} no longer contributes a doc link to this sweep"
    assert any(anchor for _, _, _, anchor in links), (
        "no ANCHORED link found -- the anchor check below would then be vacuous")


def test_every_doc_link_printed_to_a_user_resolves():
    """The file exists and, where an anchor is given, a heading in it slugs to that anchor."""
    for rel, url, path, anchor in _doc_links_in_code():
        target = ROOT / path
        assert target.exists(), f"{rel} prints {url}, but {path} does not exist in this repo"
        if anchor is None:
            continue
        slugs = {_slug(m.group("title")) for m in _HEADING.finditer(
            _outside_fences(target.read_text(encoding="utf-8")))}
        assert anchor in slugs, (
            f"{rel} prints {url}, but {path} has no heading slugging to '{anchor}'. A renamed "
            f"heading does not 404 -- it silently drops the reader at the top of the file. "
            f"Nearest available: {sorted(s for s in slugs if s.split('-')[0] == anchor.split('-')[0])}")


def test_the_slug_rules_match_githubs_for_the_headings_this_repo_actually_uses():
    """The falsify partner for `_slug`, against the shapes present in these docs.

    Chosen because each exercises a different rule rather than because each passes: backticks and
    a slash are dropped, a hyphen survives, case folds, and EACH space becomes its own hyphen.
    """
    assert _slug("System libraries for PDF rendering") == "system-libraries-for-pdf-rendering"
    assert _slug("Google access for `track`") == "google-access-for-track"
    assert _slug("deb / rpm") == "deb--rpm"
    assert _slug("Pinning an older version") == "pinning-an-older-version"
    assert _slug("  Trailing and   inner   space ") == "trailing-and---inner---space"


def test_a_hash_comment_inside_a_fenced_block_is_not_a_heading():
    """The fence hole, pinned against the real `docs/INSTALL.md` AND a synthetic fixture.

    The real-file half asserts its own SCOPE FIRST, and that is not ceremony. Checked only for the
    absence of the phantom slugs, this test disarms itself the day someone edits those two lines
    out of INSTALL.md: nothing would be left for the stripper to remove, and it would pass while
    guarding nothing. So it pins that the raw file still contains the shell comments and that
    `_HEADING` really does match them BEFORE stripping -- the input has to be present for the
    absence of the output to mean anything.

    Both directions of the transform are asserted too. A stripper that removes too much -- or the
    whole file -- would make the phantom slugs disappear and pass a violations-only check, so a
    real heading must still be there afterwards.
    """
    raw = (ROOT / "docs/INSTALL.md").read_text(encoding="utf-8")
    assert "# Debian, Ubuntu" in raw and "# Fedora" in raw, (
        "INSTALL.md no longer contains the fenced shell comments this test is built on -- it is "
        "now guarding nothing. Point it at another fenced `#` line or delete it.")
    unstripped = {_slug(m.group("title")) for m in _HEADING.finditer(raw)}
    assert {"fedora", "debian-ubuntu"} <= unstripped, (
        "those lines are no longer matched as headings even before stripping, so their absence "
        "afterwards proves nothing about the stripper")

    slugs = {_slug(m.group("title")) for m in _HEADING.finditer(_outside_fences(raw))}
    assert "fedora" not in slugs and "debian-ubuntu" not in slugs, (
        "a shell comment inside a bash fence is still being read as a heading")
    assert "system-libraries-for-pdf-rendering" in slugs, (
        "the real headings went with the fences -- the stripper is eating document text")


def test_only_a_bare_matching_marker_closes_a_fence():
    """The three CommonMark conditions, one synthetic fixture per condition.

    Each row exists because getting it wrong ends the fence EARLY, which puts real code back into
    the heading scan -- the exact failure this file exists to prevent, arriving by a different
    route than the one that prompted it.
    """
    def slugs(md):
        return {_slug(m.group("title")) for m in _HEADING.finditer(_outside_fences(md))}

    # A different marker character must not close a backtick fence.
    assert slugs("## A\n```sh\n# no\n~~~\n# no\n```\n## B\n") == {"a", "b"}
    # A shorter run must not close a longer opener.
    assert slugs("## A\n````sh\n# no\n```\n# no\n````\n## B\n") == {"a", "b"}
    # An INFO STRING opens a fence and can never close one.
    assert slugs("## A\n```\n# no\n```python\n# no\n```\n## B\n") == {"a", "b"}
    # A heading indented up to three spaces is still a heading to GitHub.
    assert slugs("   ### Indented\n") == {"indented"}


# An anchored markdown link between shipped docs, in EITHER form this repo uses: relative
# (`[text](TARGET.md#anchor)`) or absolute into its own blob (`](https://github.com/.../x.md#a)`).
# Both spellings are live -- README.md links to docs/ absolutely because it is also the PyPI
# description, where a relative path resolves to nothing, while the docs/ pages link to each
# other relatively. A relative-only pattern silently skipped the absolute half; matching one
# form and not the other is how a sweep looks complete while covering part of the set.
# Absolute links to any OTHER host stay out: an external anchor is not this repo's to keep valid.
_DOC_TO_DOC = re.compile(
    r"\]\((?:" + re.escape(_BLOB) + r"|(?!\w+:))(?P<path>[\w./-]*\.md)#(?P<anchor>[\w-]+)\)")

_SHIPPED_DOCS = ["README.md", "CONTRIBUTING.md", "SECURITY.md"]
_SHIPPED_DOCS += sorted(glob.glob("docs/*.md", root_dir=ROOT))


def _doc_to_doc_links():
    """(source doc, target path, anchor) for every anchored relative link between shipped docs."""
    found = []
    for rel in _SHIPPED_DOCS:
        p = ROOT / rel
        if not p.exists():
            continue
        # `_outside_fences` on the SOURCE side too, not just when collecting headings. The
        # asymmetry was a real defect: a link inside a fenced block or an HTML comment is not a
        # link GitHub renders, so checking it fails the build for a documented EXAMPLE -- and an
        # example anchor is exactly the kind a doc writes deliberately wrong. Measured: a fenced
        # `[example](README.md#does-not-exist)` and a commented-out `[old](README.md#gone)` were
        # both collected and would both have gone red.
        for m in _DOC_TO_DOC.finditer(_outside_fences(p.read_text(encoding="utf-8"))):
            # An absolute _BLOB link's path is repo-root-relative; a plain one is relative to the
            # linking file. Resolving both against the wrong base is how a live link reads dead.
            base = ROOT if m.group(0).startswith("](" + _BLOB) else p.parent
            found.append((rel, (base / m.group("path")).resolve(), m.group("anchor")))
    return found


def test_the_doc_to_doc_sweep_finds_the_links_it_means_to():
    """SCOPE. Positive sweep, so a non-empty result is the honest non-vacuity check."""
    links = _doc_to_doc_links()
    assert links, "no anchored doc-to-doc links found at all -- the pattern stopped matching"
    sources = {rel for rel, _, _ in links}
    assert len(sources) >= 2, f"only {sources} contribute -- the sweep has narrowed"
    # BOTH spellings must be represented, or the pattern has quietly lost one form. README uses
    # the absolute one (it doubles as the PyPI description); docs/ pages use the relative one.
    assert "README.md" in sources, "the absolute _BLOB form fell out of the doc-to-doc sweep"
    assert any(r.startswith("docs/") for r in sources), "the relative form fell out"


def test_every_anchored_link_between_shipped_docs_resolves():
    """The file exists, and a heading in it slugs to the anchor.

    Fenced blocks are stripped first, for the reason `_outside_fences` records: this repo's docs
    are full of shell snippets whose `#` comments would otherwise read as headings and make a
    dead anchor look live.
    """
    for rel, target, anchor in _doc_to_doc_links():
        assert target.exists(), f"{rel} links to {target.name}#{anchor}, which does not exist"
        slugs = {_slug(m.group("title")) for m in _HEADING.finditer(
            _outside_fences(target.read_text(encoding="utf-8")))}
        assert anchor in slugs, (
            f"{rel} links to {target.name}#{anchor}, but no heading there slugs to it. GitHub "
            f"resolves the FILE and ignores a stale fragment, so this fails silently for a reader.")


def test_a_heading_inside_an_html_comment_is_not_a_heading():
    """Commented-out structure is not structure. A fail-open, found by the fenced case's logic.

    Both directions again: the ghost must go AND the real headings either side must survive, so a
    stripper that eats the document cannot pass by making the violation disappear.
    """
    got = {_slug(m.group("title")) for m in _HEADING.finditer(
        _outside_fences("## Real\n<!--\n## Ghost\n-->\n## Second Real\n"))}
    assert got == {"real", "second-real"}, got

    # A comment wrapping a FENCE must not leave the fence's opener behind -- if it did, everything
    # after would read as fenced and every later heading would vanish.
    wrapped = "## Before\n<!--\n```sh\n# not a heading\n-->\n## After\n"
    got = {_slug(m.group("title")) for m in _HEADING.finditer(_outside_fences(wrapped))}
    assert got == {"before", "after"}, got


def test_a_markdown_link_in_a_heading_slugs_to_its_TEXT():
    """GitHub keeps the link text and drops the target; a naive strip concatenates both.

    No heading in this repo carries a link today. That is why this is here now: the failure is a
    build break blaming an anchor that is perfectly correct, which is the noisy direction, but the
    noise points the next author at deleting a good link.
    """
    assert _slug("See the [guide](docs/INSTALL.md)") == "see-the-guide"
    assert _slug("[Install](#install) and [Naming](#naming)") == "install-and-naming"
    # A bare bracket that is not a link is still just punctuation.
    assert _slug("Arrays [] and things") == "arrays--and-things"


def test_a_link_that_github_never_renders_is_not_checked(monkeypatch, tmp_path):
    """The SOURCE side of the fence rule, driven through `_doc_to_doc_links` itself.

    Headings were collected outside fences from the start; links were collected from raw text. So
    a deliberately-wrong anchor in a documented EXAMPLE, or a commented-out link left for context,
    would fail the build -- and the actionable reading of that failure is "delete the example",
    the same fail-toward-deleting-explanation this suite already fixed once for comments.

    THE FIRST VERSION OF THIS TEST DID NOT PIN THE FIX, and its docstring claimed it did. It
    composed `_DOC_TO_DOC.finditer(_outside_fences(...))` by hand, which is the transform, not the
    wiring: measured, deleting `_outside_fences` from `_doc_to_doc_links` left it green. That is
    this repo's recorded "testing the helper reproduces the defect one level up" failure (#170),
    committed in the very test written to close a fail-open -- so the reader is monkeypatched onto
    a synthetic tree and CALLED, and the assertion reads its return value.
    """
    doc = tmp_path / "sample.md"
    doc.write_text("# T\n\nSee [real](README.md#install).\n\n"
                   "```md\n[example](README.md#deliberately-not-a-heading)\n```\n\n"
                   "<!-- [old](README.md#removed-long-ago) -->\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("## Install\n", encoding="utf-8")

    monkeypatch.setattr("tests.test_doc_links_from_code.ROOT", tmp_path)
    monkeypatch.setattr("tests.test_doc_links_from_code._SHIPPED_DOCS", ["sample.md"])

    anchors = {anchor for _rel, _target, anchor in _doc_to_doc_links()}
    assert anchors == {"install"}, anchors
