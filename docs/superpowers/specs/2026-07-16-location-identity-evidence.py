"""Runnable derivation for the Evidence table in 2026-07-16-location-identity-normalizer-design.md.

    .venv/bin/python docs/superpowers/specs/2026-07-16-location-identity-evidence.py

This is EVIDENCE, not shipped code. It lives in docs/ because it names real cities: the ground-truth
grouping cannot be derived by the rule under test without begging the question, so a human assigned
it, and that table is geography. Nothing here is imported by `sluice/` or `tests/`.

The first draft of the spec quoted counts (31 / 158 / 21 / 179) that no reader could reproduce,
because the grouping silently dropped two cities. This file exists so that never recurs: the universe
and the inclusion rule are explicit, and `check_universe` raises in BOTH directions.

Both directions are load-bearing, and the first draft of THIS FILE only asserted one. It checked
`set(CITY) - found` -- table against fixtures -- while its docstring claimed the reverse. A new
fixture city therefore passed silently and vanished from the universe: exactly the bug the file was
added to prevent, with the comment claiming otherwise sitting directly above it. Three reviewers
caught it independently. `assert found` matters too: the glob below is relative, so a run from the
wrong cwd finds nothing, and a naive one-direction flip would report a green 0-value universe.
"""
import glob
import itertools
import re
import unicodedata
from collections import Counter

# Ground truth, assigned BY HAND. Every distinct non-empty `location` in tests/fixtures/*/raw.json
# that names a city. Country-only ('UAE', 'India', 'Ukraine') and arrangement-only ('Remote') values
# are EXCLUDED from the universe: they denote no city, so "same city" is undefined for them.
CITY = {
    'Abu Dhabi': 'abudhabi',
    'Abu Dhabi - United Arab Emirates': 'abudhabi',
    'Abu Dhabi - United Arab Emirates (UAE)': 'abudhabi',
    'Abu Dhabi , Al Ain - United Arab Emirates (UAE)': 'abudhabi',
    'Beirut': 'beirut',
    'Bengaluru': 'bengaluru',
    'Cairo - Egypt': 'cairo',
    'Dammam': 'dammam',
    'Doha': 'doha',
    'Doha - Qatar': 'doha',
    'Dubai': 'dubai',
    'Dubai - United Arab Emirates': 'dubai',
    'Dubai - United Arab Emirates (UAE)': 'dubai',
    'Jeddah - Saudi Arabia': 'jeddah',
    'Hybrid work in London': 'london',
    'London': 'london',
    'London EC4Y': 'london',
    'London Area, United Kingdom (Hybrid)': 'london',
    'London, England, United Kingdom (Hybrid)': 'london',
    'London, England, United Kingdom (Remote)': 'london',
    'London\xa0∙ Choose area': 'london',
    'Riyadh': 'riyadh',
    'Riyadh - Saudi Arabia': 'riyadh',
    'Sharjah': 'sharjah',
    'Sharjah - United Arab Emirates (UAE)': 'sharjah',
}

# Every distinct non-empty fixture value that names NO city. Deliberately excluded from the pair
# space -- "same city" is undefined for them. Naming them explicitly is what lets check_universe
# distinguish a deliberate exclusion from a forgotten one; without this set the fixtures-to-table
# direction cannot be asserted at all.
NOT_A_CITY = {
    'Bahrain - Bahrain', 'India', 'Remote', 'Saudi Arabia - Saudi Arabia', 'UAE', 'USA', 'Ukraine',
    'United Arab Emirates - United Arab Emirates', 'United Kingdom (Remote)',
}

# A sample `location_noise_words`. NOT a shipped default -- this is what a user with this geography
# would configure. It ships nowhere; see the spec's Neutrality section. Every token here is read from
# a fixture value above EXCEPT 'uk', which is hand-added to cover the abbreviation the boards in this
# corpus happen not to use -- so this set is very slightly more than the corpus discloses.
GEO_NOISE = frozenset({
    'united', 'kingdom', 'uk', 'england', 'arab', 'emirates', 'uae', 'area', 'choose',
    'egypt', 'qatar', 'saudi', 'arabia', 'hybrid', 'remote', 'work', 'in', 'al', 'ain',
})

SAME, DIFFERENT, UNKNOWN = 'SAME', 'DIFFERENT', 'UNKNOWN'


def norm(s):
    """The spec's `_norm_location`."""
    s = unicodedata.normalize('NFKD', s.casefold())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\W+', ' ', s).strip()


def compare(a, b, noise=frozenset()):
    """The spec's `_compare_locations`. The noise set is normalized and tokenized through `norm`,
    not subtracted raw: raw subtraction is the inert-knob bug the spec mandates three tests for, and
    a model of the function that does not model that is not evidence for it."""
    if isinstance(noise, str):
        raise TypeError(f'noise must be a set of words, not a str: {noise!r}')
    n = {tok for w in noise for tok in norm(w).split()}
    ta, tb = set(norm(a).split()) - n, set(norm(b).split()) - n
    if not ta or not tb:
        return UNKNOWN
    return SAME if ta & tb else DIFFERENT


def rule2_as_written(a, b):
    """#5's rule 2 BEFORE this spec: 'both locations non-empty and normalized-EQUAL -> SAME'."""
    na, nb = norm(a), norm(b)
    return bool(na) and bool(nb) and na == nb


def fixture_values():
    found = set()
    for path in glob.glob('tests/fixtures/*/raw.json'):
        with open(path) as fh:
            for m in re.finditer(r'"location"\s*:\s*"([^"]*)"', fh.read()):
                if m.group(1).strip():
                    found.add(m.group(1))
    return found


def check_universe(found):
    """Fail loudly rather than silently measuring a stale universe -- the bug this file exists for.

    THREE assertions, because each catches a different way of going stale, and the first draft had
    only the second one (while its docstring promised the first):
      1. fixtures -> tables: a NEW fixture value must be classified, not silently dropped.
      2. tables -> fixtures: a value that no longer exists must not pad the universe.
      3. the corpus is non-empty: the glob is relative, so a wrong-cwd run must not report a green
         zero-value universe.
    """
    assert found, 'no fixture locations found -- run this from the repo root; the glob is relative'
    unclassified = found - set(CITY) - NOT_A_CITY
    assert not unclassified, (
        'fixture values are in neither CITY nor NOT_A_CITY, so the universe silently shrank: '
        f'{sorted(unclassified)}')
    stale = (set(CITY) | NOT_A_CITY) - found
    assert not stale, f'the tables name values absent from the fixtures: {sorted(stale)}'


def main():
    found = fixture_values()
    check_universe(found)
    vals = sorted(CITY)
    print(f'universe: {len(found)} distinct non-empty values; {len(vals)} name a city\n')

    same = [(a, b) for a, b in itertools.combinations(vals, 2) if CITY[a] == CITY[b]]
    diff = [(a, b) for a, b in itertools.combinations(vals, 2) if CITY[a] != CITY[b]]

    print(f'#5 rule 2 as written (normalized-EQUAL) fires {sum(rule2_as_written(a, b) for a, b in same)}'
          f'/{len(same)} same-city pairs  <- the defect this spec fixes')
    print(f'tri-state _compare_locations  returns {dict(Counter(compare(a, b) for a, b in same))}'
          f' on the same pairs\n')

    for label, noise in [('empty (the default)', frozenset()), ('geography configured', GEO_NOISE)]:
        splits = sum(compare(a, b, noise) == DIFFERENT for a, b in same)
        ok = sum(compare(a, b, noise) == DIFFERENT for a, b in diff)
        merged = [(a, b) for a, b in diff if compare(a, b, noise) != DIFFERENT]
        print(f'{label}:')
        print(f'  same-city {len(same):4} pairs: {len(same) - splits:3} SAME (correct) / '
              f'{splits:3} DIFFERENT (REGRESSION)')
        print(f'  diff-city {len(diff):4} pairs: {ok:3} DIFFERENT (correct) / '
              f'{len(merged):3} merged (missed split)')
        if merged:
            shared = Counter(frozenset((set(norm(a).split()) & set(norm(b).split())) - noise)
                             for a, b in merged)
            for toks, n in shared.most_common():
                print(f'      {n:3} merged on {sorted(toks)}')
        print()


if __name__ == '__main__':
    main()
