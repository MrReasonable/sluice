"""Runnable derivation for the Evidence table in 2026-07-16-location-identity-normalizer-design.md.

    .venv/bin/python docs/superpowers/specs/2026-07-16-location-identity-evidence.py

This is EVIDENCE, not shipped code. It lives in docs/ because it names real cities: the ground-truth
grouping cannot be derived by the rule under test without begging the question, so a human assigned
it, and that table is geography. Nothing here is imported by `sluice/` or `tests/`.

The first draft of the spec quoted counts (31 / 158 / 21 / 179) that no reader could reproduce,
because the grouping silently dropped two cities. This file exists so that never recurs: the universe
and the inclusion rule are explicit, and a value present in the fixtures but absent from CITY raises.
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

# A sample `location_noise_words`. NOT a shipped default -- this is what a user with this geography
# would configure. It ships nowhere; see the spec's Neutrality section.
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
    """The spec's `_compare_locations`."""
    ta, tb = set(norm(a).split()) - noise, set(norm(b).split()) - noise
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


def main():
    found = fixture_values()
    missing = set(CITY) - found
    # Fail loudly rather than silently measuring a stale universe -- the bug this file exists for.
    assert not missing, f'CITY names values absent from the fixtures: {sorted(missing)}'
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
