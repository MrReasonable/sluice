"""Guards the FIXTURE-NAME half of the neutrality property.

`test_sluice_neutral_defaults.py` enumerates `sluice/**/config.py` and `sluice.yaml.example`.
Nothing walked `tests/` — so a real employer name typed into a test fixture shipped in a
public repo, and a partial manual rename of it (8 occurrences in one file, 12 left across
four others) read as complete. Caught by CodeRabbit's WEB pass, not by anything local. (#135)

These are RATCHETS, not classifiers. Nothing running locally can tell whether a name belongs
to a real firm — that needs a human or a web lookup. So `_REVIEWED_FIXTURE_IDENTITIES` is a
list somebody has LOOKED AT, and the test fails when a fixture introduces a value that is not
on it. The point is to force that judgement to happen once, when the name is added, instead
of never.

Deliberately NOT a "must match `Example <Word>`" rule. That is not the convention this repo
actually has: `Acme` (17 uses), `A`/`B`/`C`, `Beta`/`Gamma`/`Delta`, `Human Typed Co` and the
deliberately-malformed `Foo\\Bar Ltd` injection fixtures are all legitimate and all fail such
a rule. A guard that fires on ~40 good fixtures gets suppressed, and a suppressed guard
guards nothing.

SCOPE, stated honestly. This sweeps four ENUMERATED positions that carry a lead identity:
frontmatter `company:`, lead-note filenames, `lead_slug=` kwargs, and the first positional
argument of the `_note`/`_lead`/`_vault_with`/`_shortlist_with` helpers. That last position
holds a company in some modules and a slug in others — the same helper name has different
signatures per file — which is why the roster is named for IDENTITIES rather than companies:
a leaked employer name could land in either shape, so both are swept and neither is filtered
out. A name written into some other shape — prose in a comment, a docstring, an unusual
helper — is NOT covered; the email-domain guard below is the broader net.
"""
import re
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_SELF = Path(__file__).name

# Reviewed 2026-08-15. Every entry has been eyeballed as synthetic: placeholder words (Acme,
# Foo, Widget, Alpha/Beta/Gamma), single letters and their phonetics (A/B/C/D, Aye/Bee),
# invented compounds (Beavni), descriptive labels (Human Typed Co, Conflicted,
# blank), cluster-test slugs (a1/b2), a job-board name (indeed), the deliberately-malformed
# `Foo\Bar` injection fixtures, and the `Example …` family.
#
# Adding a name here is a DECISION that it names no real firm. Make it deliberately — a local
# check cannot establish it — and prefer `Example <Word>` for anything new.
#
# `indeed` is the one entry allowed on a different ground: it is a shipped source identifier
# (`sluice/ingest/sources/indeed.py`), so it is public integration surface rather than anything
# out of a private job hunt. `.coderabbit.yaml`'s `tests/**` instruction carries that exception
# in full. It is narrow in both directions — the exception covers the identifiers of adapters
# this repo ships, and it does NOT extend to using one as a lead or employer identity, which is
# what this roster governs. `indeed` is here only because a fixture already sits in an identity
# position; a new one should still be `Example <Word>`.
#
# `Unknown` is on the SAME different ground as `indeed`, reviewed 2026-08-17 (#151): it is a
# shipped SENTINEL, one of `core/leads.py`'s `NON_ANSWER_COMPANIES` (the board's own honest
# non-answer, not a name anyone typed hoping it was real). It had already appeared as a bare
# frontmatter value in several other test files without tripping this sweep -- what changed is
# `tests/test_leads_rename.py` (#151's rename pass) being the first to put it in one of the
# FOUR positions this sweep actually watches (a quoted `"Unknown - <role>.md"` filename), so it
# is reviewed here rather than silently matching the roster by accident.
#
# `N-A` is the SAME ground as `Unknown`, reviewed 2026-08-17 (#151, CodeRabbit finding 3): it
# is `_sanitize`'s length-preserving rendering of the sentinel "N/A" (`/` maps to `-` because
# `/` is a filename-illegal path separator), not a name anyone typed hoping it was real --
# `NON_ANSWER_COMPANIES` in core/leads.py lists "n/a" itself as one of its members.
# `tests/test_leads_rename.py`'s sanitize-aware placeholder-head test is what put it in the
# filename position this sweep watches.
_REVIEWED_FIXTURE_IDENTITIES = frozenset({
    "A", "A-B", "Acme", "Alpha", "Aye", "B", "Beavni", "Bee", "Beta", "C", "Conflicted",
    "D", "Delta", "Epsilon", "Example", "Example Analytics", "Example Co", "Example Foundry",
    "Example Ltd", "Example Meridian", "Example MeridianRemote", "Example Northgate",
    "Example Systems", "Example Telemetry", "Example Tidal", "Foo", "Gamma",
    "Human Typed Co", "N-A", "Unknown", "Widget", "X",
    "a", "a1", "a2", "b", "b1", "b2", "blank", "c", "d", "example-lead",
    # Escaping/injection fixtures — the backslashes are the point of the test.
    "Foo\\Bar Ltd", "Foo\\\\Bar Ltd", "Foo\\\\g<0>Bar", "Foo\\\\nBar",
})


def _test_sources():
    """Every test module's text, EXCEPT this one.

    Self-exclusion is load-bearing: this file contains the collector regexes and the roster as
    string literals, so scanning it makes the sweep match its own source and report nonsense
    like `'([^'` as an unreviewed employer name.
    """
    return [p.read_text(encoding="utf-8")
            for p in sorted(_TESTS_DIR.rglob("*.py")) if p.name != _SELF]


def _identity_of(raw):
    """The lead identity in a fixture value that may be a path, a `.md` name, or a full
    `Company - Role` slug. Order matters: strip the extension, then the directory, then the
    role — a path segment can itself contain ` - `."""
    return raw.removesuffix(".md").split("/")[-1].split(" - ")[0].strip()


_COLLECTORS = (
    ("frontmatter company:", re.compile(r'company:\s*"([^"]*)"')),
    ("lead-note filename", re.compile(r'"([A-Za-z][^"\n]*? - [^"\n]*?\.md)"')),
    ("lead_slug= kwarg", re.compile(r'lead_slug="([^"\n]*)"')),
    # `(?<![A-Za-z0-9])` so the helper NAME must start here. Without it this matched the tail
    # of `_row_to_lead("indeed", ...)` -- whose first argument is a SOURCE ID, not a lead
    # identity -- and the roster carried `indeed` purely to silence that false positive. A
    # sweep that has to be appeased with entries for things it misread is a sweep nobody can
    # read the roster of.
    ("identity-first helper",
     re.compile(r'(?<![A-Za-z0-9])_(?:note|lead|vault_with|shortlist_with)\("([^"\n]*)"')),
)


def _collect(pattern):
    found = set()
    for text in _test_sources():
        for raw in pattern.findall(text):
            name = _identity_of(raw)
            # Blank/whitespace fixtures and `{template}` placeholders carry no identity.
            if not name or name.startswith("{"):
                continue
            found.add(name)
    return found


def _all_fixture_identities():
    names = set()
    for _label, pattern in _COLLECTORS:
        names |= _collect(pattern)
    return names


@pytest.mark.parametrize("label,pattern", _COLLECTORS, ids=[c[0] for c in _COLLECTORS])
def test_every_collector_actually_finds_fixtures(label, pattern):
    """A collector that matches nothing makes the roster check VACUOUSLY green.

    This is the failure mode that matters. A regex silently stops matching (a helper is
    renamed, a quoting style changes), the sweep finds an empty set, `set() <= roster` passes,
    and the guard reports success while covering nothing — "a search that finds nothing proves
    nothing." The floor is deliberately low: it pins that the position is still LIVE, not how
    many fixtures the repo happens to have.
    """
    assert len(_collect(pattern)) >= 2, (
        f"the {label!r} collector matched fewer than 2 fixtures across tests/ — its regex has "
        f"probably drifted from the code, which would make the roster check vacuous")


def test_no_unreviewed_employer_name_in_test_fixtures():
    unreviewed = sorted(_all_fixture_identities() - _REVIEWED_FIXTURE_IDENTITIES)
    assert not unreviewed, (
        "test fixtures introduce lead-identity values that nobody has reviewed:\n  "
        + "\n  ".join(unreviewed)
        + "\n\nThis repo is PUBLIC and these fixtures ship in it. Confirm each names no real "
          "firm — a local check CANNOT establish this, it needs a human or a web lookup — then "
          "add it to _REVIEWED_FIXTURE_IDENTITIES. Prefer `Example <Word>`.")


def test_the_reviewed_roster_carries_no_identity_the_fixtures_stopped_using():
    """A roster entry outliving its fixture is a name recorded for no reason.

    Completeness in the other direction, matching the `_SWEPT_CONFIGS` pattern in
    test_sluice_neutral_defaults.py. Without it the roster only ever grows, and a name deleted
    from the fixtures for a NEUTRALITY reason stays written down here — the same "the
    remediation records the value" trap the deletion was meant to close.
    """
    stale = sorted(_REVIEWED_FIXTURE_IDENTITIES - _all_fixture_identities())
    assert not stale, (
        "these values are on the reviewed roster but no fixture uses them any more:\n  "
        + "\n  ".join(stale)
        + "\n\nDelete them from _REVIEWED_FIXTURE_IDENTITIES.")


# RFC 2606 / RFC 6761 reserve these for documentation and testing; they can never resolve to a
# real host, so an address built from one cannot name a real employer's mail domain.
#
# Split in two because the matching rules differ, and conflating them punched a hole in this
# guard: a plain `endswith("example.com")` also accepts `notexample.com`, which is an ordinary
# registrable domain that could belong to an employer. The reserved TLDs are safe under
# endswith because their leading dot forces a real label boundary; the reserved DOMAINS must
# match exactly or as a dot-delimited subdomain.
_RESERVED_TLDS = (".invalid", ".example", ".test", ".localhost")
_RESERVED_DOMAINS = ("example.com", "example.org", "example.net")


def _is_reserved(domain: str) -> bool:
    if domain.endswith(_RESERVED_TLDS):
        return True
    return any(domain == d or domain.endswith("." + d) for d in _RESERVED_DOMAINS)

# Non-employer real domains. Each needs a reason, not just an entry.
_DOMAIN_ALLOWLIST = {
    # The repo's own commit-trailer identity, asserted on by tests/test_packaging.py. A GitHub
    # noreply address, not an employer.
    "users.noreply.github.com",
}

_EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})')


def test_every_email_domain_in_test_fixtures_is_reserved_or_allowlisted():
    """The broader net: a mail domain is the shape an employer identity most often hides in.

    `Acme` reads as obviously fake. `jobs@<something>.com` does not, and a plausible-looking
    placeholder domain is usually registered by somebody — this guard's first run found
    `jobs@company.com` sitting in a track fixture.
    """
    offenders = set()
    for text in _test_sources():
        for domain in _EMAIL.findall(text):
            d = domain.lower().rstrip(".")
            if d in _DOMAIN_ALLOWLIST or _is_reserved(d):
                continue
            offenders.add(d)
    assert not offenders, (
        "test fixtures use mail domains that are not reserved for documentation:\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\nUse an RFC 2606 reserved domain (`example.com`, or anything under `.invalid` / "
          "`.example`) so the address cannot name a real host.")


def test_a_lookalike_domain_is_not_mistaken_for_a_reserved_one():
    """`notexample.com` is registrable and could belong to an employer.

    The first cut of this guard tested `d.endswith("example.com")`, which accepts it. A
    neutrality check that silently passes an employer domain is worse than none, because it
    reads as coverage. The reserved TLDs are still endswith-matched: their leading dot forces
    a real label boundary, so `.invalid` cannot be spoofed the same way.
    """
    assert _is_reserved("example.com")
    assert _is_reserved("careers.example.com")
    assert _is_reserved("anything.invalid")
    assert _is_reserved("evil.example")
    assert not _is_reserved("notexample.com"), "lookalike accepted as reserved"
    assert not _is_reserved("example.com.evil.co.uk"), "suffix-spoofed domain accepted"
    assert not _is_reserved("myexample.org")
    assert not _is_reserved("indeed.com")


def test_the_email_sweep_actually_reads_fixtures():
    """Same vacuity guard as above: an `_EMAIL` regex that matches nothing passes silently."""
    found = {m.lower() for text in _test_sources() for m in _EMAIL.findall(text)}
    assert len(found) >= 10, (
        f"the email sweep found only {len(found)} domains across tests/ — the regex has "
        f"probably drifted, which would make the reserved-domain check vacuous")
