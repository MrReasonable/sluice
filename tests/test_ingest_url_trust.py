"""Both halves of the "an extractor trusted a URL it never checked" class (#160, #153).

Two DIFFERENT failure modes that a single audit surfaced, kept in one file because the
sweep that finds them is shared:

  #160 CONTENT.   A field built from a URL segment reaches the vault without being
                  percent-decoded. cord derived `company` from the path slug and decoded
                  only `title`, so `Which?` -- which cord serves as `which%3F` -- landed in
                  two production notes as `which%3F`. Not cosmetic: `track` matches mail
                  against the stored company name, so three already-resolved interview
                  emails re-surfaced as unmatched on every pass.

  #153 ADMISSION. A row is taken without checking where its link points. reed interleaves
                  sponsored COURSE cards into the jobsearch results page and the
                  extractor's link cascade ends in "any anchor in the card", so courses
                  became leads that park in `needs_review` forever.

The asymmetry between them is why the fixes differ. #160 corrupts a field and can lose
nothing; #153's fix is a FILTER, and a filter is what silently bins real leads -- so its
default abstains and its rejections are counted rather than dropped.
"""
import re

import pytest

from sluice.ingest import sources as S

# ── #160: the decode-symmetry sweep ───────────────────────────────────────────

# A capture group that cannot carry a percent-encoding needs no decode. Digits are the
# only such shape in the registry today (`eighty_k` captures `jobId=(\d+)`), and it is
# listed as a PATTERN rather than a source name so a second numeric-capture source is
# exempt automatically instead of needing a roster entry.
_UNENCODABLE_CAPTURE = re.compile(r"^\(\\d\+\)$|^\(\[0-9\]\+\)$")
# The match BINDING, so the variable name is derived rather than assumed. Keying on `m`
# was measured blind to `wttj` (binds `sm`) and `workinstartups` (binds `salMatch`).
_BINDING_RE = re.compile(
    r"(?:const|let|var)\s+(\w+)\s*=\s*[^;]*?\.match\(/(.+?)/[gimsuy]*\)")
_CAPTURES = re.compile(r"\((?!\?:)[^)]*\)")


def _js_blobs(src):
    """Every JS attribute a source carries, found by SUFFIX rather than by name.

    `CarouselSource` calls its blob `read_js`, not `extractor_js` -- keying on the latter
    made the first version of this sweep skip wttj entirely and report it clean, which is
    the "a search that finds nothing proves nothing" failure this file exists to prevent.
    """
    return {n: getattr(src, n) for n in dir(src)
            if n.endswith("_js") and isinstance(getattr(src, n, None), str)
            and getattr(src, n).strip()}


def _url_derived_fields(js):
    r"""`[(capture_pattern, statement, is_decoded)]` for each `<var>[N]` read out of a match.

    Three things here were wrong in the first version, each MEASURED against a real
    offender rather than reasoned about, and each failing in the silent direction:

    STATEMENTS, not lines. Splitting on newlines meant the identical #160 bug joined onto
    one line reported `offenders == []`. That is not hypothetical minification: `eighty_k`
    already packs three statements per line today.

    The match VARIABLE derived from its own binding, not assumed to be `m`. `wttj` binds
    `sm` and `workinstartups` binds `salMatch`, so both were invisible to the sweep that
    claimed to cover every source.

    Groups tracked PER BINDING, not flattened across the blob. Two `.match()` calls in one
    source desynchronised the flat list, so an undecoded company inherited the numeric
    capture `(\d+)` from an unrelated match and was FALSELY EXEMPTED -- a guard actively
    certifying the bug it exists to find.

    `is_decoded` is decided at the USE SITE (`decodeURIComponent(<var>[N]`), not by asking
    whether the statement mentions a decode ANYWHERE: `const a=decodeURIComponent(m[2])+m[1]`
    decodes one capture and not the other. Requiring the call to wrap this specific use is
    strict, and deliberately so -- an over-strict check reports a clean field as an offender,
    which is LOUD and gets fixed, while a lax one certifies a real one, which is how #160
    shipped.
    """
    groups_by_var = {var: _CAPTURES.findall(pattern)
                     for var, pattern in _BINDING_RE.findall(js)}
    out = []
    for stmt in js.split(";"):
        for var, idx in re.findall(r"\b(\w+)\[(\d+)\]", stmt):
            if var not in groups_by_var:
                continue          # an array index into something that is not a match result
            n = int(idx)
            if n == 0:
                continue          # [0] is the whole match, a dedup key here
            groups = groups_by_var[var]
            pattern = groups[n - 1] if n - 1 < len(groups) else "<unknown>"
            decoded = re.search(
                r"decodeURIComponent\(\s*" + re.escape(var) + r"\[" + str(n) + r"\]", stmt)
            out.append((pattern, stmt.strip(), bool(decoded)))
    return out


def test_every_url_derived_field_is_percent_decoded():
    """The #160 class, swept across every registered source.

    This is a SOURCE-TEXT check, and that is not a shortcut -- it is the only executable
    option. The golden fixtures under `tests/fixtures/` are the extractor's OUTPUT, captured
    after the JS ran, so no offline test can exercise the JS itself; running it would need a
    browser, which the suite deliberately never touches. Asserting the symmetry in the source
    catches exactly the defect that shipped: cord decoded `title` and not `company`, and the
    two sat on adjacent lines for months.
    """
    srcs = S.all_sources()
    assert srcs, "registry empty -- this sweep would certify nothing"

    examined, with_captures, offenders = 0, 0, []
    for src in sorted(srcs, key=lambda s: s.id):
        blobs = _js_blobs(src)
        assert blobs, f"{src.id} carries no JS at all -- inspect it by hand rather than skipping"
        examined += 1
        for name, js in blobs.items():
            uses = _url_derived_fields(js)
            if uses:
                with_captures += 1
            for pattern, stmt, decoded in uses:
                if _UNENCODABLE_CAPTURE.match(pattern):
                    continue
                if not decoded:
                    offenders.append(f"{src.id}.{name}: capture {pattern} used undecoded -> {stmt}")

    # SCOPE, asserted before the verdict: `all([])` is `True`, so an offender list that is
    # empty because nothing was examined reads identically to one that is empty because
    # everything is clean.
    assert examined == len(srcs), f"examined {examined} of {len(srcs)} sources"
    assert with_captures >= 2, (
        f"only {with_captures} JS blob(s) derive a field from a URL capture -- cord and "
        "eighty_k both do, so the extraction has drifted and this sweep is now vacuous")
    assert not offenders, (
        "a field built from a URL segment reaches the vault without percent-decoding. "
        "cord shipped this for months (`company` undecoded beside a decoded `title`) and "
        "it corrupted a real company name in a production vault:\n  " + "\n  ".join(offenders))


def test_the_decode_sweep_catches_the_bug_it_was_written_for():
    """POSITIVE CONTROL. Without it the sweep above passes just as happily on a registry
    whose JS it can no longer parse -- and this specific sweep has already been wrong once,
    silently skipping the one source whose blob is named `read_js`."""
    broken = "const m=h.match(/\\/u\\/([^\\/]+)\\/jobs\\/(\\d+)/);const company=m[1].replace(/-/g,' ');"
    uses = _url_derived_fields(broken)
    assert uses, "the parser found no URL-derived field in text that plainly has one"
    pattern, _stmt, decoded = uses[0]
    assert not _UNENCODABLE_CAPTURE.match(pattern), f"{pattern} wrongly treated as unencodable"
    assert not decoded, "the control sample must be an OFFENDER"


_UNDECODED = "const company=%s[1].replace(/-/g,' ');"
_MATCH = "const %s=h.match(/\\/u\\/([^\\/]+)\\/jobs\\/([^?]+)/)"


@pytest.mark.parametrize("label,js", [
    # The shape the sweep was originally written against -- the control for the rest.
    ("multi-line", f"{_MATCH % 'm'};\n{_UNDECODED % 'm'}"),
    # MINIFIED. The first sweep split on newlines, so joining the identical bug onto one
    # line reported zero offenders. `eighty_k` already packs three statements per line, so
    # this style is present in the registry today rather than hypothetical.
    ("minified onto one line", f"{_MATCH % 'm'};{_UNDECODED % 'm'}"),
    # A match bound to something other than `m`. `wttj` binds `sm`, `workinstartups` binds
    # `salMatch`; a sweep keyed on the name `m` is blind to both while reporting them clean.
    ("bound to a variable that is not `m`", f"{_MATCH % 'sm'};{_UNDECODED % 'sm'}"),
    # TWO matches in one blob. The first sweep flattened every capture into one list, so
    # this undecoded company inherited the numeric `(\d+)` from the unrelated match above
    # it and was FALSELY EXEMPTED -- the sweep certifying the exact bug it exists to catch.
    ("two .match() calls desyncing the groups",
     f"const idm=h.match(/jobId=(\\d+)/);const id=idm[1];{_MATCH % 'm'};{_UNDECODED % 'm'}"),
    # A decode present in the statement but wrapping the OTHER capture. Asking whether the
    # statement mentions `decodeURIComponent` anywhere passes this; asking whether it wraps
    # THIS use does not.
    ("decode on one capture but not the other",
     f"{_MATCH % 'm'};const a=decodeURIComponent(m[2])+m[1];"),
], ids=lambda v: v if isinstance(v, str) and " " in v else "")
def test_the_decode_sweep_catches_every_offender_shape_in_this_registry(label, js):
    """REGRESSION ROWS for a guard that failed OPEN on all four of the last shapes.

    Every one of these carries the same defect -- a capture used to build a field with no
    decode -- and every one was measured returning `offenders == []` before this rewrite.
    A guard that finds nothing reads exactly like a codebase that is clean, which is why
    these are pinned individually rather than covered by one happy-path row.
    """
    offenders = [(pattern, stmt) for pattern, stmt, decoded in _url_derived_fields(js)
                 if not _UNENCODABLE_CAPTURE.match(pattern) and not decoded]
    assert offenders, f"{label}: an undecoded URL-derived field was reported as clean"


def test_a_numeric_capture_is_exempt_without_needing_a_roster_entry():
    """`eighty_k` captures `jobId=(\\d+)`. Digits cannot carry a percent-encoding, so
    requiring a decode there would be noise -- and the exemption is keyed on the capture
    PATTERN, so a second numeric-capture source is covered without anyone adding it to a
    list that would then need maintaining."""
    numeric = "const m=h.match(/jobId=(\\d+)/);const id=m[1];"
    (pattern, _stmt, _decoded), = _url_derived_fields(numeric)
    assert _UNENCODABLE_CAPTURE.match(pattern), f"{pattern} should be exempt"
    assert not _decoded, "premise: the exempt use is genuinely undecoded"
