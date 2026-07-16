# Location identity — a comparison #5 can key a split on

- **Date**: 2026-07-16
- **Status**: Design approved, not yet planned. Unblocks #5.
- **Origin**: split out of #6 on 2026-07-16 after the code contradicted #6 as filed (see Background).
  #6 keeps the demashing half and is now blocked on capturing a real payload; this spec is the half
  #5 actually waits on.
- **Consumer**: #5 (`docs/superpowers/specs/2026-07-16-lead-identity-write-path-design.md`), whose
  `same_opportunity` rule 3 points at `locations_differ` when this lands. Nothing else calls it.

## Goal

Give #5 a location comparison it can key a note split on, at the bar #5 needs: **confidently
different, or abstain.** Not "same or different".

#5 splits only on `DIFFERENT`, so the two error directions are not symmetric:

- A wrong `DIFFERENT` **manufactures a duplicate note** for an ordinary cross-board re-post. That is
  a **regression** on today, where the same lead collides at `_path_for` and reports `updated`.
- A wrong "not different" **merges** two leads into one note. That is exactly what happens today, it
  needs company *and* title to also match, and it is recoverable.

So abstaining is always safe and guessing `DIFFERENT` is not. Every choice below falls out of that
asymmetry.

## Background — what the code actually does

**#6 as filed is misdiagnosed, and this was verified before any design work.** #6 says
`_demash_company` "misses the case where the location string itself is a phrase rather than a bare
place — e.g. `AcmeRemote in <city>` with location `Remote in <city>`". It does not:

```
_demash_company('AcmeRemote in London', 'Remote in London')  ->  'Acme'
```

`endswith` never cared whether the suffix was one word or five, and `tests/test_demash.py:9,13` have
pinned that exact shape since the initial public release. Location *phrases* are not the failure.

What is actually brittle is that the match must be **byte-exact and case-exact**:

```
'Acmeremote in london' + 'Remote in London'      -> unchanged   (case differs)
'AcmeRemote in London' + 'Remote in London, UK'  -> unchanged   (location richer than the jam)
```

The second is the plausible real-world bite — the location cell carrying more detail than what got
jammed into the company cell — but that is a **guess**, there is no reproducer, and #6 itself
concedes the fixtures were never captured. Loosening the match without a fixture is precisely how
`Stark Capital UK` gets truncated (`tests/test_demash.py:18` guards that today). So demashing is
speculative and risky; it stays in #6, blocked on a real capture via `ingest test-source --raw`.

**The location half is neither speculative nor optional.** `core/leads.py` has exactly one
normalizer, `_norm_url`. `_path_for` (`core/vault.py:86`) keys on `f"{company} - {title}"` with
location nowhere in it. So today every rendering of a location collides into one note, and #5 — which
introduces the first location comparison in the codebase — decides every split with it.

This is the same shape as #5 itself, whose spec opens: "issue #5, rescoped after the code
contradicted the issue as filed." Two for two. The issues are written from prose; the tree disagrees.

## The governing rule

> `locations_differ(a, b)` returns `True` **only** on positive evidence of difference. Equal,
> overlapping, or either side empty — all return `False`. `False` means "same **or unsure**", never
> "same".

## Design

Two pure functions in `core/leads.py`, beside `_norm_url` and matching its shape — deterministic,
offline, standard-library only.

### 1. `_norm_location(s: str) -> str` — new, private

NFKD-normalize, drop combining marks, casefold, replace runs of non-word characters with a single
space, strip. Unicode-aware `\W`, **not** `[^a-z0-9]`.

That last point is load-bearing and was chosen against evidence, not taste:

```
'Zürich'   --[^a-z0-9]-->  'z rich'     # shredded into two junk tokens
'Zürich'   --NFKD + \W -->  'zurich'    # one whole token; compares equal to 'Zurich'
```

Under `[^a-z0-9]`, `Zürich` vs `Zurich` shares no token, returns `DIFFERENT`, and **splits** — the
regression direction, on a case that needs no board quirk to occur.

Blank-ish input normalizes to `''`. This is what disarms `bool("   ") is True`: whitespace dirt
cannot manufacture a difference, because an empty side abstains.

### 2. `locations_differ(a, b, noise=frozenset()) -> bool` — new, public

Normalize both, tokenize on whitespace, subtract `noise`. Return `True` **only** when both token sets
are non-empty and their intersection is empty. Everything else returns `False`.

`noise` is a set of words to ignore when comparing — work-arrangement and administrative-geography
vocabulary that decorates a location without locating it. It defaults to empty and **no config key
ships in this issue**; see "Config-first", below.

**`noise` is fed through `_norm_location` and tokenized, not used raw.** This is not a detail; taking
it raw yields a knob that silently does nothing, in two ways that were both verified:

```
noise={'UK'}              -> never matches the token 'uk'          (case)
noise={'United Kingdom'}  -> normalizes to the STRING 'united kingdom',
                             which never equals a single token      (arity)
```

Both fail toward merge — the safe direction — but they fail **silently**, which is the failure class
this codebase most consistently engineers out. So the implementation must build the noise token set
as `{tok for w in noise for tok in _norm_location(w).split()}`: the same normalizer, then split. A
multi-word entry contributes each of its tokens. The user writes `location_noise_words:
[United Kingdom, Remote]` in the natural way and it works.

## Evidence — why token-overlap, and why not the alternatives

Three candidate rules were evaluated against the **real location values in `tests/fixtures/*/raw.json`**
(the captured board payloads), not against invented examples. Method: extract every non-empty
`location`, group the renderings that denote the same city, and count same-city pairs the rule would
split (regressions) and different-city pairs it would merge (missed splits).

**The alternatives each fail on a case that matters, and on different ones:**

| rule | fails on | outcome |
|---|---|---|
| substring containment | `London` / `Londonderry` | merges two genuinely different places |
| token-disjointness | `London, UK` / `Manchester, UK` | shares the `uk` token → abstains → defeats #5 on any board that suffixes a country |
| **token-subset** | **15 of 21 same-city pairs** | **splits real re-posts — rejected** |
| Jaccard threshold | no threshold exists | `Hybrid work in <city>`/`<city>` scores 0.25, below `London, UK`/`Manchester, UK` at 0.33 — the orders cross |

**Token-subset was recommended and then retracted.** It survived a hand-built table
(`London` vs `London, UK`) because those examples are subset-shaped *by construction*. Real board
renderings are not: they are each richly and differently decorated —

```
'London'                              'London EC4Y'
'Hybrid work in London'               'London ∙ Choose area'
'London Area, United Kingdom (Hybrid)'
'London, England, United Kingdom (Hybrid)'
'London, England, United Kingdom (Remote)'
```

— and neither side of most pairs is a subset of the other. Token-subset splits 15 of those 21 pairs.
This is the repo's own named pattern, one level down: **the table certified the rule because the
table's author chose the cases.** The corpus is the tree; the table was the document.

**Token-overlap holds unconditionally.** Every rendering of a city shares the *city token*; what
varies is decoration. Overlap keys on the signal, and the decoration that defeated subset is
precisely what it ignores:

| noise list | same-city (regressions) | different-city (missed splits) |
|---|---|---|
| **empty (the default)** | 31 abstain / **0 split** | 158 split / 21 merge |
| geography configured | 31 abstain / **0 split** | 179 split / **0 merge** |

**0 regressions in every configuration.** The noise list is a pure **precision** knob: it never
converts an abstain into a split on this corpus; it only recovers missed splits. The 21 misses at
default are `Abu Dhabi` vs `Dubai` — both carry "United Arab Emirates", so they share country tokens
and abstain. Those are mis-*merges*: today's behaviour, the acceptable direction.

The error asymmetry runs the right way by construction. Overlap needs only **one** shared token to
abstain, so an *incomplete* noise list leaves more shared tokens, which yields more abstains, which
yields more merges — the safe direction. A noise list can only be dangerous by stripping a genuine
city token, which is why no gazetteer ships.

## The accepted cost — stated honestly

All verified, none hypothetical. The first three point the safe way; the last two do not.

| case | verdict | direction |
|---|---|---|
| `York` / `New York` | abstain → merge | mis-merge. Acceptable: matches today, needs company *and* title to also match, recoverable. |
| `Abu Dhabi` / `Dubai` | abstain → merge | mis-merge until noise is configured. Matches today. |
| `Cambridge, MA` / `Cambridge, UK` | abstain → merge | mis-merge. Acceptable. Note token-subset got this one *right* — the trade was made deliberately, and the London corpus is worth more than this case. |
| `Remote` / `London` | **DIFFERENT → split** | **mis-split — regression direction.** Ships as the documented default; fixed by adding `remote` to the noise list. On the record per the user decision of 2026-07-16. |
| `København` / `Kobenhavn` | **DIFFERENT → split** | **mis-split — regression direction.** `ø` is a distinct letter, not an accented `o`, so NFKD cannot fold it. Rare, unattested in the corpus. Cheap remedy if it ever bites: a ~6-entry transliteration map (`ø→o, æ→ae, ß→ss, ð→d, þ→th, ł→l`). Not built — YAGNI. |

`Remote` / `London` is the one that will be argued in review, so the reasoning is recorded here
rather than left to be re-derived. remoteok and weworkremotely **ship as sources**, so remote-vs-city
is a shipped configuration, and splitting it by default manufactures a duplicate out of the box. It
is accepted anyway because the alternative — a non-empty code default — is worse on two counts: it is
non-monotone (stripping `remote` turns `Remote, US` vs `Remote, UK` from abstain into a **split**,
a code default that *causes* the bad direction), and it contradicts `sluice.yaml.example:14`, where
`locations: [Remote]` treats `Remote` as a real location value. The knob is the honest fix.

## Config-first: no knob ships in this issue

The user chose "one knob, `location_noise_words`, code default empty, commented-out example in
`sluice.yaml.example`" — following the `locations:` precedent verbatim.

**That knob does not ship here, because nothing would read it.** #5 is the only consumer, and a
config key parsed but ignored is exactly what #7 forbids. So:

- the empty default is encoded now as the **function parameter default** (`noise=frozenset()`);
- **#5 adds `location_noise_words`** to config and threads it into `locations_differ` when it wires
  rule 3 — the knob lands with its consumer, in the same change that gives it a reader.

The decision is recorded; only the wiring is deferred. When #5 adds it, `sluice.yaml.example` follows
`sluice.yaml.example:11-14` exactly: commented out, no active value, because the file is copied.

## Neutrality

No place names ship in `sluice/`: no gazetteer, no country list, no transliteration table. The rule
is vocabulary-free — it keys on token overlap, not on knowing what a city is. The user's geography
reaches the code only through `sluice.local.yaml`, exactly like `locations:`.

`tests/fixtures/*/raw.json` already contains real location values as captured board payloads; this
spec neither adds to them nor builds a city-grouping table on top of them (see Testing). Whether
golden fixtures are themselves in tension with the neutrality rule is a **pre-existing question this
spec deliberately does not open** — it is noted in Risks.

## Testing

Unit tests, offline, using **synthetic** place names (`Palmerburgh`, `Clarkefurt` — the existing
`tests/test_demash.py` convention), in the **shapes** the real corpus revealed:

- bare city; city + postcode; city + region + country + `(Hybrid)`; `Hybrid work in <city>`;
  `<city> ∙ Choose area` (including the real `\xa0` and `∙`).

The corpus evaluation is **evidence in this spec, not a fixture**. A test that groups fixture values
by city would have to name the cities, encoding the hunt geography in `tests/` — the thing neutrality
forbids. The shapes carry the regression risk; the specific cities do not.

`_norm_location`:

- casefolds; collapses whitespace runs; strips; `'   '` → `''`.
- `\xa0` and `∙` are separators, not characters.
- **`Zürich` → `zurich`, one token** — asserted directly, because `[^a-z0-9]` yields `'z rich'` and
  this test is the only thing standing between the implementation and that regression.
- `København` → `københavn`, one whole token (pins that non-ASCII letters are not shredded, and
  documents the known limitation rather than hiding it).

`locations_differ`:

- **Same-shape pairs abstain** — every pair drawn from the corpus shapes above, with synthetic names.
  This is the test that token-subset fails 15 times and is the reason the rule is what it is.
- **Genuinely different cities return `True`** — `<city A>` vs `<city B>` sharing no token.
- `differ(a, b) == differ(b, a)` — symmetry.
- `differ(a, a)` is `False` — reflexivity.
- `differ(a, '')`, `differ(a, '   ')`, `differ('', '')` are all `False` — absence never splits.
- **Noise list**: `<city A>, <region>` vs `<city B>, <region>` returns `False` with an empty noise set
  and `True` with `{region}` — pinning that the knob buys precision and that its default is the
  conservative one.
- **Noise is normalized and tokenized**: the same pair returns `True` with `{'<REGION>'}` (wrong case)
  and with `{'<Region Of Two Words>'}` (multi-word). Both fail on a raw-`noise` implementation, and
  both fail *silently* — so without these two assertions the knob can ship inert and the suite stays
  green.
- `differ('Remote', '<city>')` is `True` with an empty noise set and `False` with `{'remote'}` —
  the accepted cost, pinned as deliberate so it cannot be "fixed" by accident.

## Non-goals

- **Demashing.** Stays in #6, blocked on a real captured payload.
- **A gazetteer, geocoding, or place-name translation.** `München`/`Munich` is out of scope forever
  at this layer.
- **Wiring the comparison into `Vault.upsert`.** That is #5, and it is where the config key lands.
- **`existing_keys()` / the read path.** #23.
- **Deciding whether work arrangement deserves its own field.** `Remote`/`Hybrid` arriving inside
  `location` is arguably mashing of a second kind, related to #6 in spirit. Noted, not solved.

## Definition of done

Each item below fails if the mechanism is absent. None can pass by accident — the 2026-07-16 lesson
is that *a DoD item that can pass without fixing anything certifies the bug.*

1. `_norm_location` and `locations_differ` exist in `core/leads.py`, are pure, and import nothing
   outside the standard library.
2. `python -m pytest` passes; the suite stays offline and under ~2s.
3. `ruff check sluice tests` passes.
4. **A test asserts `_norm_location('Zürich')` has exactly one token.** Deleting the NFKD fold, or
   swapping `\W` for `[^a-z0-9]`, must turn this test red.
5. **A test asserts every corpus *shape* pair abstains.** Reverting the rule to token-subset must
   turn this test red. (This is the item that would have caught the retracted design.)
6. **A test asserts `differ('Remote', '<city>')` is `True` with empty noise and `False` with
   `{'remote'}`.** The accepted cost is pinned in both directions, so a future change that silently
   flips it fails the build.
7. **A test asserts the noise set works with wrong case and with a multi-word entry.** Implementing
   `noise` as a raw set must turn this test red — otherwise the knob ships inert and silent.
8. No place name, country, or region appears in `sluice/`. No new config key ships.
9. `locations_differ`'s docstring states that `False` means "same **or unsure**" and that `True` is
   the only verdict #5 acts on.

## Risks and notes

- **The corpus is small and is one user's.** 31 same-city and 179 different-city pairs from the
  shipped fixtures. It is real data, which beats invented data, but it is not a guarantee about
  boards not yet captured. The mitigation is structural rather than statistical: overlap's error
  direction is merge, and merge is today's behaviour.
- **This ships with no caller.** Two pure functions and their tests, consumed by #5. That is
  deliberate — it is the whole point of splitting #6 — but it means the functions are not exercised
  end-to-end until #5 lands. The conformance suite in #5 is where that happens.
- **`tests/fixtures/` holds real location values.** Pre-existing; golden parser fixtures are captured
  payloads. This spec does not add to them and does not resolve whether they sit oddly beside "no
  locations in `tests/`". Worth its own issue if anyone cares to open it.
- **#6 needs re-titling and re-diagnosing** when this is filed, or the next reader re-derives the
  misdiagnosis. Its Problem section names a mechanism that is not the one failing.

## Process

1. File the issue for this work; retitle and re-diagnose #6 to the exact-match mechanism, and mark it
   blocked on a real captured payload.
2. Plan via `writing-plans`, then implement.
3. Review with **both** `/review-pr` and CodeRabbit (per the standing cadence). Read the CodeRabbit
   rate-limit comment **before** triggering.
4. On merge, unblock #5: point `same_opportunity` rule 3 at `locations_differ`, add
   `location_noise_words`, and restart #5's review from the "Blocked on #6" question being closed.
