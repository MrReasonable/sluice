"""The repo's one NAME fold: whether two names are one identity up to case and canonical
equivalence (#205, #299).

Its own module so that every consumer imports it from a neutral home: core/vault.py (lead
note names, archive filenames) and core/layout.py (employer matching, #364) both depend on
it, and core/vault.py also imports core/layout.py, so the fold living in either one made the
other reach for it lazily or form a cycle. Pure and stdlib only."""
import unicodedata


def fold_note_name(name: str) -> str:
    """The identity fold for a note NAME: two names that fold equal name one lead (#205).

    Every path that resolves a lead by NAME goes through it, and that is the obligation
    rather than a list: `_locate` (so a re-scrape under a different company casing resolves
    to the note already on disk instead of minting a sibling), `_archived_match` (so the same
    re-scrape cannot walk past a merged-away loser and RESURRECT it), `read_leads`' collision
    report (so what the read path calls a collision is what the write path calls one
    identity), and `reconcile_names` (so the rename pass neither MINTS a pair nor reports a
    note as its own blocker). Deliberately not stated as a count: it shipped saying THREE and
    was stale within the same branch, which is this repo's most-repeated finding applied to
    its own docstring. `tests/test_vault_case_identity.py` sweeps the roster instead.

    A second copy of this rule kept in step by a comment is the #30 failure mode; here it
    would be worse than usual, because the consumers disagree SILENTLY -- a `_locate` that
    folds against an `_archived_match` that does not is measurably a resurrection, and a
    `reconcile_names` that does not is measurably a newly-minted pair. Both were live on this
    branch before review.

    CASE AND CANONICAL EQUIVALENCE, and no further -- that is the line not to blur.
    `_norm_location` folds case AND applies NFKD AND drops combining marks AND collapses
    non-word runs, because it compares two values for whether they describe the same PLACE.
    This compares two FILENAMES for whether they are the same note, and every widening past
    CANONICAL equivalence is a claim that two differently-SPELLED names are one job -- which,
    applied to a name, silently merges two real postings and is unrecoverable in the
    direction that matters. Canonical equivalence is not such a widening: it says the two
    strings ARE the same text, which is why #299 folded it in and why compatibility
    (NFKD/NFKC, which merges a superscript with its digit) stays out. Do not carry
    `_norm_location`'s NFKD across on the strength of the shared word "normalize".

    `casefold`, not `lower`: `lower` is a per-character map that leaves the German sharp s
    alone, so a company written "STRASSE" and one written "Straße" would answer as two
    identities under `lower` and one under `casefold`. Matching `_norm_location`'s choice
    also means the two folds cannot disagree on a value they both see.

    NORMALIZATION is folded too (#299), and the shape is UAX #15's CANONICAL CASELESS MATCH
    (definition D145), `NFD(toCasefold(NFD(x)))` -- not the `NFC(casefold(x))` most reach
    for. The two are not interchangeable: on some inputs the naive form is NARROWER, so it
    would seat two notes for one employer -- the defect this fold exists to close, surviving
    in a corner. Deliberately NO COUNT of such inputs here. A draft of this paragraph gave
    one, and it was not reproducible: four readers sweeping "every assigned code point"
    arrived at different numbers because the phrase does not say what each point is compared
    AGAINST, and every one of those numbers was consistent with its own sweep. The witness
    is executable instead --
    `tests/test_vault_case_identity.py::test_the_fold_is_the_DEFINED_caseless_match_not_the_naive_composition`
    pins one pair that the pre-#299 fold and the naive form each classify as two identities
    and this one classifies as one, so the row reddens if either is restored. The load-bearing
    half is normalizing to NFD BEFORE casefolding; the TRAILING NFD is a no-op on every
    single code point (that much did reproduce, on two UCD versions) and is kept because the
    definition specifies it and single code points say nothing about multi-character
    sequences, where casefolding can expand a character into marks needing reordering.
    `casefold` alone is a case mapping and normalizes nothing, so the composed and decomposed
    spellings of one accented employer folded APART and each seated its own note -- measured
    on Linux against shipped code as `created, created`, and a two-accent name seated FOUR.
    The harm is the paragraph above, unchanged, plus the replication half: a
    normalization-INSENSITIVE filesystem (macOS APFS, in both its case-sensitive and
    case-insensitive variants -- measured) cannot hold the pair at all.

    No compatibility NORMALIZATION -- that is the line, and it is NOT the same line as "no
    compatibility equivalence", which is where a draft of this paragraph drew it and was
    wrong. `casefold` is FULL Unicode case folding, and its own mappings already decompose the
    fi/ff/ffi ligatures: measured, `fold_note_name` calls U+FB01 + "le" and "file" one
    identity, with no NFKD involved. So a ligature merging with its letters is on the
    PERMITTED side, as a property of case folding this function cannot decline without
    hand-rolling a fold. What must stay out is NFKD/NFKC, which would additionally merge a
    superscript with its digit and a full-width letter with its ASCII form -- measured, both
    of those are two identities today and must remain so, because merging them claims two
    differently-SPELLED names are one job. `_norm_location` does reach for NFKD -- do not
    carry that across: it compares token SETS for a human-gated report, not filenames for a
    write decision.

    A SECOND kind of consumer since #298, and the reason this function's roster is a lower
    bound rather than "every path that resolves a lead by NAME":
    `_folded_archive_names`/`_archive_name_candidates` fold to choose an archive FILENAME --
    asking what a REPLICA would conflate, not what is one lead. The two questions agree today
    and share this one fold deliberately, because a second copy is the #30 hazard. They can
    diverge, and the pressures at the compatibility boundary are OPPOSITE: widening is cheap
    for the filename question (it costs a numeric suffix, and on a stamp-failed archive a
    re-created duplicate -- see `_reserve_and_move`) and forbidden for the identity one
    (it merges two jobs). If a replica filesystem is ever found that conflates something
    identity must not, split them then -- not before.

    Stdlib only, deliberately: `unicodedata` ships the UCD and `casefold` is genuine Unicode
    full case folding from `CaseFolding.txt`. PyICU and `precis-i18n` were considered and
    rejected -- both are third-party, against this package's stdlib-only rule, and
    `precis-i18n`'s `NFKC_Casefold` is the compatibility fold this paragraph just refused."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", name).casefold())
