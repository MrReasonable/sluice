"""Runnable derivation for the Evidence table in 2026-07-16-location-identity-normalizer-design.md.

    .venv/bin/python docs/superpowers/specs/2026-07-16-location-identity-evidence.py

This is EVIDENCE, not shipped code, and nothing here is imported by `sluice/` or `tests/`.
The ground-truth grouping still cannot be derived by the rule under test without begging the
question, so a human assigns it -- but since #27 the values it groups are SYNTHETIC, scrubbed in
step with the fixtures, so this table is no longer geography and no longer the reason the file
sits outside `tests/`. It sits here because it is evidence for a spec, not a shipped test.

The first draft of the spec quoted counts (31 / 158 / 21 / 179) that no reader could reproduce,
because the grouping silently dropped two cities. This file exists so that never recurs: the universe
and the inclusion rule are explicit, and `check_universe` raises in BOTH directions.

Both directions are load-bearing, and the first draft of THIS FILE only asserted one. It checked
`set(CITY) - found` -- table against fixtures -- while its docstring claimed the reverse. A new
fixture city therefore passed silently and vanished from the universe: exactly the bug the file was
added to prevent, with the comment claiming otherwise sitting directly above it. Three reviewers
caught it independently. `assert found` matters too: the glob below is relative, so a run from the
wrong cwd finds nothing, and a naive one-direction flip would report a green 0-value universe. Its
message names BOTH causes deliberately -- it used to name only the cwd, which is a cwd diagnosis for
what #27 made the likelier cause by far: a corpus scrub that moved the values out from under it.
"""
import glob
import itertools
import re
from collections import Counter

from sluice.core.leads import DIFFERENT, _compare_locations, _norm_location

# SAME and UNKNOWN are the other two members of the shipped verdict vocabulary (also importable
# from sluice.core.leads) but this script never compares against them directly -- DIFFERENT is the
# only verdict #5 acts on (see _compare_locations's docstring), so it is the only one this script
# needs to test against. The tri-state print below reports the raw dict Counter builds from
# `_compare_locations`'s actual return values, which is why it shows the shipped lowercase strings.

# Ground truth, assigned BY HAND. Every distinct non-empty `location` in tests/fixtures/*/raw.json
# that names a city. Country-only ('ASR', 'Vesperia', 'Karnovia') and arrangement-only ('Remote') values
# are EXCLUDED from the universe: they denote no city, so "same city" is undefined for them.
CITY = {
    'Brackenburgh - Bantria': 'brackenburgh',
    'Clarkefurt - Allied Sundic Reaches (ASR)': 'clarkefurt',
    'Clarkefurt - Allied Sundic Reaches': 'clarkefurt',
    'Clarkefurt': 'clarkefurt',
    'Ellery Kestrelburgh , Quillon Denfurt - Allied Sundic Reaches (ASR)': 'ellerykestrelburgh',
    'Ellery Kestrelburgh - Allied Sundic Reaches (ASR)': 'ellerykestrelburgh',
    'Ellery Kestrelburgh - Allied Sundic Reaches': 'ellerykestrelburgh',
    'Ellery Kestrelburgh': 'ellerykestrelburgh',
    'Fennimoreburgh': 'fennimoreburgh',
    # Added 2026-08-28 with wttj's recapture for its list view. A three-city comma list,
    # classified on its FIRST city exactly as the two-city
    # 'Ellery Kestrelburgh , Quillon Denfurt - ...' entry above already is.
    'Palmerburgh, Potterburgh, Clarkefurt': 'palmerburgh',
    'Hensleyfurt - Halvenia': 'hensleyfurt',
    'Hensleyfurt': 'hensleyfurt',
    'Hybrid work in Palmerburgh': 'palmerburgh',
    'Marshburgh - Norvane Thessary': 'marshburgh',
    'Marshburgh': 'marshburgh',
    'Palmerburgh Area, Allied Brennmark (Hybrid)': 'palmerburgh',
    'Palmerburgh ZZ9Z': 'palmerburgh',
    'Palmerburgh': 'palmerburgh',
    'Palmerburgh, Wexmoor, Allied Brennmark (Hybrid)': 'palmerburgh',
    'Palmerburgh, Wexmoor, Allied Brennmark (Remote)': 'palmerburgh',
    'Palmerburgh\xa0∙ Choose area': 'palmerburgh',
    'Potterburgh - Allied Sundic Reaches (ASR)': 'potterburgh',
    'Potterburgh': 'potterburgh',
    'Tolliverfurt': 'tolliverfurt',
    'Whitlockfurt': 'whitlockfurt',
    'Wrenfieldburgh - Norvane Thessary': 'wrenfieldburgh',
}

# Every distinct non-empty fixture value that names NO city. Deliberately excluded from the pair
# space -- "same city" is undefined for them. Naming them explicitly is what lets check_universe
# distinguish a deliberate exclusion from a forgotten one; without this set the fixtures-to-table
# direction cannot be asserted at all.
NOT_A_CITY = {
    'Sedgewickfurt - Sedgewickfurt', 'Vesperia', 'Remote', 'Norvane Thessary - Norvane Thessary', 'ASR', 'VSA', 'Karnovia',
    'Allied Sundic Reaches - Allied Sundic Reaches', 'Allied Brennmark (Remote)',
}

# A sample `location_noise_words`. NOT a shipped default -- this is what a user with this geography
# would configure. It ships nowhere; see the spec's Neutrality section. Every token here is read from
# a fixture value above EXCEPT 'abm', which is hand-added to cover the abbreviation the boards in this
# corpus happen not to use -- so this set is very slightly more than the corpus discloses.
GEO_NOISE = frozenset({
    'allied', 'brennmark', 'abm', 'wexmoor', 'sundic', 'reaches', 'asr', 'area', 'choose',
    'bantria', 'halvenia', 'norvane', 'thessary', 'hybrid', 'remote', 'work', 'in',
    'quillon', 'denfurt',
})


def rule2_as_written(a, b):
    """#5's rule 2 BEFORE this spec: 'both locations non-empty and normalized-EQUAL -> SAME'.

    This models a rule that does NOT exist in the shipped code -- #5's old rule, which this spec
    replaces -- so it stays a local definition rather than an import. `_norm_location` IS the
    shipped one (imported above): only the equality comparison it feeds is the rejected rule."""
    na, nb = _norm_location(a), _norm_location(b)
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
         zero-value universe. A SCRUBBED corpus trips this same assertion, which is why the message
         names that cause too rather than sending the reader to check their working directory.
    """
    assert found, (
        'no fixture location values matched. Either the glob found no files (it is relative -- run '
        'this from the repo root), or the fixtures no longer carry the values this table names '
        '(they were scrubbed; re-derive the table from the corpus alongside the scrub).')
    both = set(CITY) & NOT_A_CITY
    # Without this, a double-listed value passes BOTH checks below (one subtracts the tables, the
    # other unions them) while still entering the pair space via sorted(CITY).
    assert not both, f'values in both CITY and NOT_A_CITY: {sorted(both)}'
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
    # Token SUBSET, the rejected alternative to overlap. Derived rather than quoted, because
    # there are TWO populations and quoting either without naming it invites exactly the
    # confusion this line replaces. `_compare_locations`'s docstring counts the 21 pairs ONE
    # city's seven renderings make (pinned in tests/test_leads_location.py); this script counts
    # all 33 same-city pairs in the corpus. Both split 15 -- the SAME 15, because the other 12
    # pairs are subsets and split 0 -- so the two figures agree and only the denominators differ.
    # Neither is stale: the universe has been 25 CITY values since this file's first commit.
    print(f'token-SUBSET (the rejected alternative) splits '
          f'{sum(not (set(_norm_location(a).split()) <= set(_norm_location(b).split())
                      or set(_norm_location(b).split()) <= set(_norm_location(a).split()))
                 for a, b in same)}/{len(same)} same-city pairs')
    print(f'tri-state _compare_locations  returns '
          f'{dict(Counter(_compare_locations(a, b) for a, b in same))} on the same pairs\n')

    for label, noise in [('empty (the default)', frozenset()), ('geography configured', GEO_NOISE)]:
        splits = sum(_compare_locations(a, b, noise) == DIFFERENT for a, b in same)
        ok = sum(_compare_locations(a, b, noise) == DIFFERENT for a, b in diff)
        merged = [(a, b) for a, b in diff if _compare_locations(a, b, noise) != DIFFERENT]
        print(f'{label}:')
        print(f'  same-city {len(same):4} pairs: {len(same) - splits:3} SAME (correct) / '
              f'{splits:3} DIFFERENT (REGRESSION)')
        print(f'  diff-city {len(diff):4} pairs: {ok:3} DIFFERENT (correct) / '
              f'{len(merged):3} merged (missed split)')
        if merged:
            shared = Counter(
                frozenset((set(_norm_location(a).split()) & set(_norm_location(b).split())) - noise)
                for a, b in merged)
            for toks, n in shared.most_common():
                print(f'      {n:3} merged on {sorted(toks)}')
        print()


if __name__ == '__main__':
    main()
