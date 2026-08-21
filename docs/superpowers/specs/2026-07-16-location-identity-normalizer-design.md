# Location identity — a comparison #5 can key a split on

- **Date**: 2026-07-16
- **Status**: **IMPLEMENTED.** Plan-reviewed three times by a five-specialist roster — 17 findings
  (0 Critical / 6 High) → 13 (0 Critical / 9 High) → 13 (1 Critical / 4 High) — all folded, plus a
  final whole-branch review (1 Important, fixed). Round 3 is the informative one: the rule, the
  normalizer, the tri-state and every mechanism item came back clean from all five, and **every**
  finding landed in the two cross-issue handoffs added a round earlier. Those were then cut (#26) and
  scoped. Unblocks #5.

> **SUPERSEDED IN PART, 2026-08-11 (#119)** — the `Remote`/`<city>` decision below (`DIFFERENT` by
> default, `UNKNOWN` only when `remote` is configured as noise) was reversed after the failure mode
> was reported for real: a role advertised remote-friendly and cross-posted with the employer's HQ
> city never clustered as a duplicate, no matter how many times dedupe ran. `_compare_locations` now
> treats a side whose token set is EXACTLY `{"remote"}` as `UNKNOWN` against a disjoint other side
> unconditionally, not only when configured as noise — see `tests/test_leads_location.py`. The
> reasoning below documents WHY the original call was made and is left as the historical record; it
> is no longer what the code does.
- **Issue**: **#25**. Split out of #6 on 2026-07-16 after the code contradicted #6 as filed (see
  Background). #6 keeps the demashing half, is re-diagnosed, and is blocked on capturing a real
  payload; this spec is the half #5 actually waits on.
- **Filed alongside**: **#26** (close the unguarded-preference class — cut from this spec, see
  "Config-first"), **#27** (golden fixtures carry real locations; pre-existing).
- **Consumer**: #5, whose `same_opportunity` consumes this in **two** of its four rules — not one.
  See "The contract #5 needs". #5's design doc is **parked and unmerged** (branch
  `fix/lead-identity-write-path`), so it is deliberately not in this change; the `#5:<line>` citations
  below refer to it there. Nothing on `main` calls these functions yet — see Risks.
- **Evidence**: every count below is produced by
  `docs/superpowers/specs/2026-07-16-location-identity-evidence.py`. Run it; do not trust the tables.

## Goal

Give #5 a location comparison it can key a note split on, at the bar #5 needs: **confidently
different, or abstain.** Not "same or different".

#5 splits only on `DIFFERENT`, so the two error directions are not symmetric:

- A wrong `DIFFERENT` **manufactures a duplicate note** for an ordinary cross-board re-post. That is
  a **regression** on today, where the same lead collides at `_path_for` and reports `updated`.
- A wrong `SAME` **merges** two leads into one note. That is exactly what happens today, it needs
  company *and* title to also match, and it is recoverable.

So abstaining is always safe and guessing `DIFFERENT` is not. Every choice below falls out of that
asymmetry.

## Background — what the code actually does

**#6 as filed is misdiagnosed, and this was verified before any design work.** #6 says
`_demash_company` "misses the case where the location string itself is a phrase rather than a bare
place — e.g. `AcmeRemote in <city>` with location `Remote in <city>`". It does not:

```
_demash_company('AcmeRemote in Palmerburgh', 'Remote in Palmerburgh')  ->  'Acme'
```

`endswith` never cared whether the suffix was one word or five, and `tests/test_demash.py:9,13` have
pinned that exact shape since the initial public release. Location *phrases* are not the failure.

What is actually brittle is that the match must be **byte-exact and case-exact**:

```
'Acmeremote in palmerburgh' + 'Remote in Palmerburgh'      -> unchanged   (case differs)
'AcmeRemote in Palmerburgh' + 'Remote in Palmerburgh, UK'  -> unchanged   (location richer than the jam)
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

> `_compare_locations(a, b)` returns `DIFFERENT` **only** on positive evidence of difference.
> Overlapping evidence returns `SAME`; absent evidence returns `UNKNOWN`. Neither ever splits.

## The contract #5 needs

**#5 consumes this in two rules, not one — which is why it returns a tri-state rather than a bool.**
#5's `same_opportunity` is:

```
both urls non-empty and normalized-equal      -> SAME       # proof
both locations non-empty and normalized-equal -> SAME       # rule 2: inference, re-post/cross-board
both locations non-empty and differ           -> DIFFERENT  # rule 3: the only split
otherwise                                     -> UNKNOWN    # never splits
```

Rule 2 is keyed on **normalized equality**, and measured against the real corpus it **fires 0 of 33
same-city re-post pairs** — because real re-posts *overlap* but are never *equal* (`Palmerburgh` vs
`Palmerburgh ZZ9Z`). Every one of them falls through to `UNKNOWN`. #5 calls `merged` "the one place the
design knowingly loses data" and says its printed count "is its only signal"; routing 100% of
ordinary re-posts into that counter destroys the signal. A bool expressing only rule 3 would have
left rule 2 defeated and the defect invisible.

So the seam is the trichotomy itself. `_compare_locations` returns #5's exact three verdicts, and
#5's rules 2–4 collapse into one call:

```
both urls non-empty and normalized-equal -> SAME
otherwise                                -> _compare_locations(note_location, lead_location, noise)
```

Measured: rule 2 goes from **0/33 to 33/33**, and `merged` returns to meaning what #5 says it means.

## Design

Two pure functions and three constants in `core/leads.py`, beside `_norm_url` and matching its shape
— deterministic, offline, standard-library only (`re`, `unicodedata`).

### 1. `_norm_location(s: str) -> str` — new, private

NFKD-normalize, drop combining marks, casefold, replace runs of non-word characters with a single
space, strip. Unicode-aware `\W`, **not** `[^a-z0-9]`.

Both halves are load-bearing, and **they are load-bearing for different reasons**. Conflating them is
what makes a guard test inert, so each gets its own witness:

```
NFKD fold        'Zürich'     -> 'zurich'      vs 'zürich' without it   (compares equal to 'Zurich')
\W not [^a-z0-9] 'København'  -> 'københavn'   vs 'k benhavn' under it  (one token, not shredded)
```

`Zürich` cannot witness the character class: NFKD has already folded `ü` to `u`, so `[^a-z0-9]` has
nothing left to shred and yields `zurich` either way. **`København` is the only witness for the
class**, because `ø` has no NFKD decomposition — it is a distinct letter, not an accented `o`, so the
class is the only live variable. Each half needs its own witness; see DoD 4a/4b.

Deleting the NFKD fold is not cosmetic: it flips `_compare_locations('Zürich', 'Zurich')` to
`DIFFERENT` and **splits**.

Blank-ish input normalizes to `''`. This is what disarms `bool("   ") is True`: whitespace dirt
cannot manufacture a difference, because an empty side returns `UNKNOWN`.

### 2. `_compare_locations(a, b, noise=frozenset()) -> str` — new, private

```python
SAME = "same"           # module constants; status.py's convention is strings, not an enum
DIFFERENT = "different"
UNKNOWN = "unknown"
```

The constants are **public** (`core/vault.py` reads the verdict #5's `same_opportunity` returns); the
function is **private**, because its only consumer, `same_opportunity`, lives in this module. That
split matches the file's own precedent: `_norm_url` is private and in-module, `slug_matches` public
and cross-module. Promote the function if #23 ever needs it out of module.

**Why the constants ship here but the config knob does not** — the two rules look contradictory a
paragraph apart, so: a dead **config key** is *silent*. Nothing reads it, nothing fails, and the user
edits a knob that does nothing. A **return vocabulary** its own tests read is not dead: every test
below asserts one of these three values, so a wrong or missing constant is loud immediately. The test
for "ship it now" is not "does a cross-module consumer exist yet" but "does anything execute it".

Normalize both, tokenize on whitespace, subtract `noise`, then:

| condition | verdict |
|---|---|
| either token set empty | `UNKNOWN` — absence of evidence never splits |
| token sets intersect | `SAME` — a shared token is the city; the rest is decoration |
| token sets disjoint | `DIFFERENT` — the only verdict #5 splits on |

`noise` is a set of words to ignore when comparing — work-arrangement and administrative-geography
vocabulary that decorates a location without locating it. It defaults to empty; no config key ships
in this issue (see "Config-first").

**`noise` is fed through `_norm_location` and tokenized, not used raw**, and **a bare `str` raises.**
Three ways a naive implementation yields a knob that silently does nothing, all verified:

```
noise={'UK'}              -> never matches the token 'uk'                       (case)
noise={'Allied Brennmark'}  -> the STRING 'allied brennmark' equals no single token  (arity)
noise='Remote'            -> iterates CHARACTERS: {'r','e','m','o','t'}          (shape)
```

The first two fail toward merge — safe, but **silently**, which is the failure class this codebase
most consistently engineers out. So build the noise token set as
`{tok for w in noise for tok in _norm_location(w).split()}`; a multi-word entry contributes each of
its tokens, and `location_noise_words: [Allied Brennmark, Remote]` works as written.

The third is a shape error, not a content error: `location_noise_words: Remote` (a YAML scalar
instead of a list) is an ordinary user mistake, and iterating it yields single-letter tokens that
strip nothing. **Raise on a bare `str`, naming the expected shape** — the repo's fail-loudly-at-
construction discipline, one line here instead of a config-coercion special case in #5. (`None`
already raises `TypeError`, which is loud, so it needs nothing.)

## Evidence — why token-overlap, and why not the alternatives

Produced by `2026-07-16-location-identity-evidence.py`. **Universe**: the 34 distinct non-empty
`location` values in `tests/fixtures/*/raw.json`; the **25 that name a city** form the pair space.
Country-only (`ASR`, `Vesperia`, `Karnovia`) and arrangement-only (`Remote`) values are excluded — they
denote no city, so "same city" is undefined for them. The city grouping is **assigned by hand** in
that script: ground truth cannot be derived by the rule under test without begging the question. The
script raises if a fixture value is missing from its table, so the universe cannot silently drift.

**The alternatives each fail on a case that matters, and on different ones:**

| rule | fails on | outcome |
|---|---|---|
| substring containment | `Palmerburgh` / `Palmerburghton` | merges two genuinely different places |
| token-disjointness | `Palmerburgh, UK` / `Manchester, UK` | shares the `uk` token → abstains → defeats #5 on any board that suffixes a country |
| **token-subset** | **15 of 21 same-city Palmerburgh pairs** | **splits real re-posts — rejected** |
| Jaccard threshold | no threshold exists | `Hybrid work in <city>`/`<city>` scores 0.25, below `Palmerburgh, UK`/`Manchester, UK` at 0.3333 — the orders cross |

**Token-subset was recommended and then retracted.** It survived a hand-built table
(`Palmerburgh` vs `Palmerburgh, UK`) because those examples are subset-shaped *by construction*. Real board
renderings are not: they are each richly and differently decorated —

```text
'Palmerburgh'                              'Palmerburgh ZZ9Z'
'Hybrid work in Palmerburgh'               'Palmerburgh ∙ Choose area'
'Palmerburgh Area, Allied Brennmark (Hybrid)'
'Palmerburgh, Wexmoor, Allied Brennmark (Hybrid)'
'Palmerburgh, Wexmoor, Allied Brennmark (Remote)'
```

— and neither side of most pairs is a subset of the other. Token-subset splits 15 of those 21 pairs.
This is the repo's own named pattern, one level down: **the table certified the rule because the
table's author chose the cases.** The corpus is the tree; the table was the document.

**Token-overlap holds unconditionally.** Every rendering of a city shares the *city token*; what
varies is decoration. Overlap keys on the signal and ignores what defeated subset:

| noise list | same-city (33 pairs) | different-city (267 pairs) |
|---|---|---|
| **empty (the default)** | 33 `SAME` / **0 split** | 237 split / 30 merged |
| geography configured | 33 `SAME` / **0 split** | 267 split / **0 merged** |

**0 regressions in every configuration.** The noise list is a pure **precision** knob: on this corpus
it never converts a `SAME` into a split, it only recovers missed splits.

**The 30 misses are mostly one structural class.** `Ellery Kestrelburgh` vs `Clarkefurt` is only 6 of 30. The
largest is **18 of 30, another city against Palmerburgh, merging on the single shared token `allied`** —
*Allied Sundic Reaches* against *Allied Brennmark*:

```text
 18 merged on ['allied']                       <- other city vs Palmerburgh
  6 merged on ['sundic', 'reaches', 'allied']   <- the "Ellery Kestrelburgh vs Clarkefurt" class
  5 merged on ['sundic', 'reaches', 'asr', 'allied']
  1 merged on ['thessary', 'norvane']
```

This is structural across any two multi-word country names sharing a leading token (ASR / ABM / VSA),
not a quirk of one pair. It is a **merge**, so nothing is threatened — but it is the cost, and the
cost table is the one place that must state it.

The error asymmetry runs the right way by construction. Overlap needs only **one** shared token to
return `SAME`, so an *incomplete* noise list leaves more shared tokens, yielding more merges — the
safe direction. A noise list can only be dangerous by stripping a genuine city token, which is why no
gazetteer ships.

## The accepted cost — stated honestly

All verified, none hypothetical. The first four point the safe way; the last three do not.

| case | verdict | direction |
|---|---|---|
| `York` / `New York` | `SAME` → merge | mis-merge. Acceptable: matches today, needs company *and* title to also match, recoverable. |
| **`<city A> - Allied Sundic Reaches` / `<city B>, Allied Brennmark`** | **`SAME` → merge** | **mis-merge on the bare token `allied` — 18 of the 30 misses, the largest class.** Structural for any two multi-word country names sharing a token. Recovered by configuring noise. |
| `Ellery Kestrelburgh` / `Clarkefurt` | `SAME` → merge | mis-merge on the shared country tokens; 6 of 30. Matches today. |
| `Cambridge, MA` / `Cambridge, UK` | `SAME` → merge | mis-merge. Acceptable. Token-subset got this one *right* — the trade was deliberate; the Palmerburgh corpus is worth more than this case. |
| `Remote` / `Palmerburgh` | **`DIFFERENT` → split** | **mis-split — regression direction.** Ships as the documented default. Configuring `remote` as noise makes it `UNKNOWN` — an **abstain, not a merge**: subtraction empties one side, and an empty side is `UNKNOWN` by the design table's first row. On the record per the user decision of 2026-07-16. |
| `København` / `Kobenhavn` | **`DIFFERENT` → split** | **mis-split — regression direction.** `ø` is a distinct letter, not an accented `o`, so NFKD cannot fold it. Rare, unattested in the corpus. Cheap remedy if it ever bites: a ~6-entry transliteration map (`ø→o, æ→ae, ß→ss, ð→d, þ→th, ł→l`). Not built — YAGNI. |
| `ASR` / `Allied Sundic Reaches - Allied Sundic Reaches` | **`DIFFERENT` → split** | **mis-split — regression direction.** Both strings are attested. An abbreviation and its expansion share no token, so one country splits against itself. **This row is outside the measured pair space**: both values are `NOT_A_CITY` in the evidence script — correct for measuring *city* pairs, but the shipped function has no `NOT_A_CITY` concept and compares whatever a board hands it, so the 30-miss count never saw this class. Recovered like `Remote`: configuring either form as noise empties a side → `UNKNOWN`, an abstain rather than a merge. |

`Remote` / `Palmerburgh` is the one that will be argued in review, so the reasoning is recorded here rather
than left to be re-derived. remoteok and weworkremotely **ship as sources**, so remote-vs-city is a
shipped configuration, and splitting it by default manufactures a duplicate out of the box.

**The config fix produces an abstain, not a merge** — `UNKNOWN` says "these may be the same job and I
have no evidence either way", which is exactly true of a remote posting and a city posting. The
distinction is load-bearing twice: it is the row this argument rests on, and the `UNKNOWN` path it
names — *noise subtraction emptying a side*, as opposed to an empty input string — is the one path
the design has no other witness for. See DoD 6.

**The refusal to ship a `{remote, hybrid, onsite}` code default rests on non-monotonicity, not on
neutrality.** This distinction matters: neutrality *would permit* work-arrangement vocabulary in
code, because it encodes no opinion about which jobs are good. Recording the wrong reason here would
entrench a rule that could later block a legitimate fix. The real reason is that stripping `remote`
turns `Remote, US` vs `Remote, UK` from `SAME` into `DIFFERENT` — **a code default that causes the
bad direction**, verified. The knob is the honest fix.

## Config-first: no knob ships in this issue

The user chose "one knob, `location_noise_words`, code default empty, commented-out example in
`sluice.yaml.example`" — following the `locations:` precedent.

**That knob does not ship here, because nothing would read it.** #5 is the only consumer, and a
config key nothing reads is a dead knob — the same bug class as #7 ("no CLI flag may be parsed but
ignored"), though #7 as filed is scoped to walking the argparse tree and does not literally cover
config keys. CLAUDE.md's fail-loudly / no-quiet-default discipline covers it directly. So:

- the empty default is encoded now as the **function parameter default** (`noise=frozenset()`);
- **#5 adds `location_noise_words`** when it wires the consumer — the knob lands with its reader.

**It belongs on the root `Config`, and the reason is mechanical, not stylistic.** `Sluice.store()`
resolves the store from `self.config` (`app.py:169`), so a key the store must honour cannot live in a
sub-app block. `config.py:43-50` records this lesson already paid for, in `baseline_rel`'s comment:
*"once the store is resolved from the root Config, a `cv.baseline_rel` could not reach the store that
has to honour it."* Citing the `locations:` precedent alone lands correctly for an unrelated reason.

**The guard assertion must travel with the key, in the same change** — so it is written into #5's
"Blocked on #6" section, beside the key, rather than mandated from here.
`tests/test_sluice_neutral_defaults.py` is an *enumeration*: it ships green on keys nobody named, and
its own comments record that escape **twice** (`:43-46` `locations` shipping `["Remote"]`, `:51-54`
`baseline_rel` losing its assertion in a refactor) — both "caught by review", neither by the suite.
`location_noise_words` would be a geography key with no guard, so #5 now carries
`assert c.location_noise_words == []` and the commented-out `sluice.yaml.example` line as part of the
change that adds it.

**Closing the whole class — sweeping the config dataclasses so the *next* unguarded key cannot ship
green — is a separate issue, filed, and deliberately not attempted here.** It is a repo-wide test
hardening unrelated to location identity, and this spec tried it once and got it wrong three ways at
once: blind to `TriageConfig` where 672ad2a actually happened, blind to the `list[str]` annotation
`location_noise_words` will most likely carry, and — read as a replacement rather than an addition —
silently dropping `baseline_rel`'s absolute-path assertion, which is the very escape it cited. Those
mistakes are worth a diff and a review of its own; they are not worth risking a guard test whose own
comments record two escapes, in a spec about two pure functions.

## Neutrality

No place names ship in `sluice/`: no gazetteer, no country list, no transliteration table. The rule
is vocabulary-free — it keys on token overlap, not on knowing what a city is. The user's geography
reaches the code only through `sluice.local.yaml`, exactly like `locations:`.

`tests/fixtures/*/raw.json` carries location values in captured board payloads. When this spec was
written those were REAL captured place names; #27 replaced every one of them with a SYNTHETIC
stand-in, preserving token structure so the analysis below still holds. The names quoted throughout
this document (`Palmerburgh ZZ9Z`, `Ellery Kestrelburgh`, `Clarkefurt`, `Marshburgh`,
`Hensleyfurt`) are those synthetic replacements, not the captured originals. This spec adds none, and deliberately refuses to
propagate them into `tests/` via a city-grouping table — that grouping lives in the evidence
script under `docs/`, alongside this prose.

The deferral stands — fixing it means re-capturing every golden fixture and would swamp two pure
functions — but this spec is the first document to *attribute* that geography to a person ("the
corpus is one user's"), which raises the salience of an exposure it does not resolve. So it gets a
filed issue, not a shrug in Risks; see Process step 1.

## Testing

Unit tests, offline, using **synthetic** place names (`Palmerburgh`, `Clarkefurt` — the existing
`tests/test_demash.py` convention), in the **shapes** the real corpus revealed:

- bare city; city + postcode; city + region + country + `(Hybrid)`; `Hybrid work in <city>`;
  `<city> ∙ Choose area` (including the real `\xa0` and `∙`).

The corpus evaluation is **evidence, not a fixture**. A test that groups fixture values by city would
have to name the cities, encoding the hunt geography in `tests/`. The shapes carry the regression
risk; the specific cities do not — verified: seven synthetic shapes reproduce the 15-of-21 split
exactly.

`_norm_location`:

- casefolds; collapses whitespace runs; strips; `'   '` → `''`.
- `\xa0` and `∙` are separators, not characters.
- `_norm_location('Zürich') == 'zurich'` — **the exact string, not the token count.** Token count is
  green under both single mutations and catches neither; see DoD 4a.
- `_norm_location('København')` is exactly one token — the guard for `\W` vs `[^a-z0-9]`, and the
  only test that catches it. Not a footnote; see DoD 4b.

`_compare_locations`:

- **Same-shape pairs return `SAME`** — every pair drawn from the corpus shapes above, with synthetic
  names. This is the test token-subset fails 15 times, and the reason the rule is what it is.
- **Genuinely different cities return `DIFFERENT`** — `<city A>` vs `<city B>` sharing no token.
- **Two multi-word country strings sharing a leading token** — `<city A> - North Clarke Republic` vs
  `<city B>, North Clarke Kingdom` returns `SAME` at default (the same SHAPE as the `allied`
  collision, 18 of 30 -- here the shared leading tokens are `north clarke`)
  and `DIFFERENT` once the shared tokens are configured as noise.
- `compare(a, b) == compare(b, a)` — symmetry.
- `compare(a, a)` is `SAME` **for any `a` with a surviving token** — reflexivity. The qualifier is
  required, not pedantry: `compare('', '')` and `compare('Remote', 'Remote', {'remote'})` are both
  `UNKNOWN`, so an unqualified claim is false in two ways.
- `compare(a, '')`, `compare(a, '   ')`, `compare('', '')` are all `UNKNOWN` — absence never splits.
  **These reach `UNKNOWN` via an empty input string.** The other route — noise subtraction emptying a
  side — is a different code path and is covered only by DoD 6.
- **Noise list, with a TWO-word region** — `'Palmerburgh, North Clarke'` vs `'Clarkefurt, North
  Clarke'` returns `SAME` with empty noise and `DIFFERENT` with `noise={'North Clarke'}`. The region
  must be two words or the arity assertion below cannot pass against correct code.
- **Noise is normalized and tokenized** — the same pair returns `DIFFERENT` with `{'NORTH CLARKE'}`
  (pins case) and with `{'North Clarke'}` (pins arity). Both are green on a raw-`noise`
  implementation and both go red on a correct one *if the region is one word* — which is why the
  region is two.
- **A bare `str` noise raises** — `compare(a, b, noise='Remote')` raises rather than silently
  iterating characters.
- `compare('Remote', '<city>')` is `DIFFERENT` with empty noise and **`UNKNOWN`** with `{'remote'}` —
  the accepted cost, pinned in both directions so it cannot be "fixed" by accident. `UNKNOWN`, not
  `SAME`: subtraction empties one side. This bullet is also the **only** witness for the
  empty-check-hoist mutant; see DoD 6.
- `compare('Remote', 'Remote', {'remote'})` is `UNKNOWN` — two *identical* locations, both emptied by
  noise. Under the hoist mutant this returns `DIFFERENT` and **splits**, which is the worst verdict
  the design can produce and the reason this one-line test exists.

## Non-goals

- **Demashing.** Stays in #6, blocked on a real captured payload.
- **A gazetteer, geocoding, or place-name translation.** `München`/`Munich` is out of scope forever
  at this layer.
- **Wiring the comparison into `Vault.upsert`.** That is #5, and it is where the config key lands.
- **`existing_keys()` / the read path.** #23.
- **Re-capturing the golden fixtures** to remove real locations from `tests/`. Filed, not fixed here.
- **Deciding whether work arrangement deserves its own field.** `Remote`/`Hybrid` arriving inside
  `location` is arguably mashing of a second kind, related to #6 in spirit. Noted, not solved.

## Definition of done

**Items 1–3 are gates** (they pass on an empty diff — that is what gates are for). **4a–8 are
mechanisms**, each verified red on the specific mutation it names. **9 is a handoff; 10–11 are
contract.** The distinction is stated because "none can pass by accident" was claimed here once, of a
list that included three CI gates, two prose items, and one inert guard.

Gates (they pass on an empty diff; that is what they are for):

1. `python -m pytest` passes; the suite stays offline and under ~2s.
2. `ruff check sluice tests` passes.
3. `.venv/bin/python docs/superpowers/specs/2026-07-16-location-identity-evidence.py` runs and
   reproduces every count in Evidence. **A one-time derivation check, not an automated gate**: CI
   runs `ruff check sluice tests` and pytest, and neither reaches `docs/`. Automating it is the wrong
   fix — a pytest wrapper would import the hand-assigned city table into `tests/`, which Neutrality
   refuses. Its own `check_universe` is what keeps it honest between runs.

Mechanisms (each names the mutation that must turn it red — every one verified against a real mutant,
in isolation, because the failure this spec is named after is a mutation list nobody ran):

4. **(a)** `_norm_location('Zürich') == 'zurich'` — the exact **string**. Deleting the NFKD fold
   turns this red. *A token-count assertion does not: it is green under both single mutations.*
   **(b)** `_norm_location('København')` is exactly one token. Swapping `\W` for `[^a-z0-9]` turns
   this red, and **nothing else does** — `ø` has no NFKD decomposition, so the class is the only live
   variable.
5. Every corpus **shape** pair returns `SAME`. Reverting the rule to token-subset turns this red.
   (Verified achievable: synthetic shapes reproduce the 15-of-21 split exactly.)
6. `compare('Remote', '<city>')` is `DIFFERENT` with empty noise and **`UNKNOWN`** with `{'remote'}`,
   and `compare('Remote', 'Remote', {'remote'})` is `UNKNOWN`. A code-default noise list turns the
   first half red. **Moving the empty check before noise subtraction turns the second half red** —
   the mutant returns `DIFFERENT` and splits two identical locations. This item carries two mutants
   because it is the only test that exercises `UNKNOWN` reached by *subtraction* rather than by an
   empty input; the first draft asserted `SAME` here, which is unreachable, and pinned the hoist to
   item 8, which cannot see it.
7. The noise set works with wrong case **and** with a multi-word entry, against a **two-word** region.
   A raw-`noise` implementation turns this red. A bare-`str` noise raises.
8. The `allied`-collision shape returns `SAME` at default and `DIFFERENT` once configured. Dropping
   the overlap rule turns this red. *(It does **not** witness the empty-check hoist — both sides keep
   a city token after subtraction, so correct and mutant agree. That claim was in the first draft and
   was false; the witness is item 6.)*

Handoff — **a mechanism, not prose.** This reaches across to #5, and a sentence in a document #5's
implementer will never execute is this spec's own thesis one level up. So it is a DoD item with a
check. *(A second handoff — sweeping the config dataclasses to close the unguarded-key class — was
attempted here and cut; it is its own issue. See "Config-first" and Risks.)*

9. **#5's resumption instruction is corrected — on the branch where #5's spec lives.** #5's design
    doc is **not on `main`**: it is parked, unmerged, on `fix/lead-identity-write-path`. This PR
    therefore cannot carry the correction, and an item claiming it did would be false the moment it
    was written. The correction *is* made and *is* committed — it travels with #5's spec, in the same
    tree, and lands whenever that spec does.

    **What was wrong, recorded here because this spec is what makes it wrong:** #5 instructed "point
    rule 3 at #6's normalizer", and its rule table keyed rule 2 on normalized *equality* — measured
    firing **0 of 33** real same-city re-post pairs. Executed as written, #5 reintroduces the defect
    this spec exists to remove. Its "Blocked on #6" section now carries the correction and its rule
    table is annotated superseded (left in place, so the fix is legible rather than silently applied).

    **This item does NOT claim "#5 no longer contradicts this spec", and the earlier draft's claim to
    that effect was false.** Collapsing rules 2–4 changes what returns `DIFFERENT`, and the
    consequences run past the rule table: `_compare_locations('X/Y', 'X:Y')` is `SAME` (`\W+` maps
    both `/` and `:` to a space), so #5's REFUSE trigger recipe (`#5:501-504`, whose "`X/Y` and `X:Y`
    both → `X-Y`, verified" is the exact pair) is unsatisfiable, and its control yields `updated`, not
    `created`. REFUSE stays *reachable* (a ≥40-char non-word run still collides), so this is a stale
    test recipe, not a design break. Re-deriving
    #5's §2, Testing and DoD against the tri-state is **#5's resumption work** — it restarts review
    anyway (Process step 4) — and pretending otherwise is how a DoD item passes while the thing it
    names stays broken. `#5:60`'s "the block is the only open item" is therefore no longer true, and
    #5 says so.

Contract:

10. `SAME`/`DIFFERENT`/`UNKNOWN` are the three verdicts, matching #5's `same_opportunity` vocabulary
    exactly, and `_compare_locations`'s docstring states that `DIFFERENT` is the only verdict #5 acts
    on.
11. **No place vocabulary ships as DATA or LOGIC in `sluice/`**: no gazetteer, country list, or
    transliteration table; neither function's behaviour depends on knowing what a city is; the rule
    stays vocabulary-free. No new config key ships. The user's geography reaches the code only
    through `sluice.local.yaml`.

    **Illustrative place names in a docstring are permitted — user decision, 2026-07-16.** The
    `_compare_locations` docstring cites `'Palmerburgh'`/`'Palmerburgh ZZ9Z'`/`'UK'`/`'Allied Brennmark'` to show
    *why* the rule is overlap and *why* raw noise is inert. The reasoning: `sluice/` already ships
    `location=London` in its neutral example searches (each source ships one, and #27 deliberately
    left them alone: a single ordinary city in an illustrative URL is not the captured SET), so marginal
    disclosure is ~zero; and a comment describing how boards render a city is prose about board
    behaviour, not an expressed preference about which jobs are good — which is the property
    neutrality actually protects.

    **The dissent, recorded because this widens a hard rule and the next reader deserves both
    sides:** `CLAUDE.md:97` says "locations" without qualification, and this item was deliberately
    scoped by plan review to "the diff **adds** no place name", precisely so that pre-existing
    example searches stayed out of scope while new geography stayed out of the tree. This diff adds
    five names to the identity layer itself. Synthetic vocabulary (`Palmerburgh`, `North Clarke`)
    was available at zero cost — the docstring's argument is identical either way. If a neutrality
    reviewer wants them synthetic, that is a one-line comment edit with no behavioural risk.

    *(Pre-existing neutral example searches in `ingest/sources/` — `hackajob.py:16`, `cord.py:25`,
    `remoteok.py:12` — are out of scope. `Zürich`/`København` in `_norm_location`'s docstring are
    Unicode fixtures, not geography: `ü` folds under NFKD and `ø` does not, which is the entire
    reason the character class matters.)*

## Risks and notes

- **The corpus is small and is one user's.** 33 same-city and 267 different-city pairs from the
  shipped fixtures. Real data beats invented data, but it is not a guarantee about boards not yet
  captured. The mitigation is structural, not statistical: overlap's error direction is merge, and
  merge is today's behaviour.
- **This ships with no caller.** Two pure functions, three constants, and their tests, consumed by
  #5. Deliberate — it is the point of splitting #6 — and architecturally sound: the functions are
  pure and fully tested, so the tests *are* the caller and nothing defers to integration.
- **The `allied` collision is structural.** Any two multi-word country names sharing a token merge at
  default. Safe direction, recovered by config, but it will recur on any new board that renders
  full country names.
- **Everything this spec got wrong, it got wrong reaching outside its own two functions.** Round 3
  found the rule, the normalizer, the tri-state and every mechanism item clean across five reviewers
  — and found a Critical plus three Highs in the two cross-issue handoffs added a round earlier. The
  config-guard sweep is now its own issue and the #5 reconciliation is scoped to the one instruction
  it can actually correct. The remaining scope is two pure functions and their tests, and that is the
  whole point of the boundary.
- **This spec cannot finish #5's reconciliation, and should not try.** DoD 9 corrects #5's resumption
  instruction; it does not re-derive #5's §2, Testing or DoD against the tri-state. Two attempts to
  reach across that boundary from here produced a false claim each time.
- **#6 needs re-titling and re-diagnosing** when this is filed, or the next reader re-derives the
  misdiagnosis. Its Problem section names a mechanism that is not the one failing.

## Process

1. ~~File the issues.~~ **Done 2026-07-16**: #25 (this work); #26 (close the unguarded-preference
   class — cut from here, with the three verified mistakes recorded so the next attempt starts from
   them); #27 (fixtures carry real locations; pre-existing). #6 re-titled and re-diagnosed to the
   exact-match mechanism and blocked on a real captured payload; #5 told what its resumption actually
   owes.
2. Plan via `writing-plans`, then implement.
3. Review with **both** `/review-pr` and CodeRabbit (per the standing cadence). Read the CodeRabbit
   rate-limit comment **before** triggering.
4. On merge, unblock #5. Its resumption is **not** "point rule 3 at the new function": rules 2–4
   collapse into `_compare_locations`, so `same_opportunity` becomes "urls decide, else compare
   locations". Add `location_noise_words` to the **root** `Config` with
   `assert c.location_noise_words == []` in `test_ingest_defaults_carry_no_preference` in the same
   change. Then restart #5's review from the "Blocked on #6" question being closed.
