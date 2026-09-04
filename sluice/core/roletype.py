"""The `role_type` closed set, its provenance ladder, and observing one from a JD (#223).

`role_type` used to record which SEARCH found a lead, and the relevance gate consumed
it as though it were a fact about the posting. Three things live here to close that:

- **A closed set.** `normalise_role_type` folds every spelling a real vault holds to
  `contract | permanent | ""`. Unrecognised input folds to `""` AND WARNS. Passing an
  unrecognised value through -- which is what `core/status.py:normalize` deliberately
  does for statuses -- would be wrong here, because the gate's contract branch is a
  SUBSTRING test: `"contract" in "contract-to-perm"` is True, so a value whose whole
  meaning is "both" takes the contract branch on the strength of its first eight
  characters. A status is a state a human chose and a new one is real; a role_type is
  a two-valued fact about pay basis and a third value is drift.

- **A provenance ladder**, `observed > declared > assumed > ""`. `observed` is the
  posting's own words, `declared` the user's assertion (a search they configured, or a
  lead they typed), `assumed` the tool's guess (a shipped example search, a source's
  `extra`). Only the top two are trusted by the gate -- see `trusted_provenance`.

- **`observe_role_type`**, which reads the basis off the JD text.

**The vocabulary is deliberately high-precision and low-recall, and the asymmetry is the
whole design.** An observation outranks every other origin, so a WRONG one overwrites the
user's own declaration with a guess; a BLANK one merely leaves today's behaviour in place.
So a marker earns its seat only if its presence in a job advert is near-conclusive. That
is why "full-time" is absent despite being the obvious candidate: it describes HOURS, not
basis, and contract adverts carry it routinely, so it would mint wrong `permanent`
observations across a large population. Negation ("not a permanent role") is not parsed
either -- the both-sides abstention below catches the common phrasings by accident rather
than by design, and a claim to handle negation would be a claim about natural language
this module cannot keep.

These are ENGLISH/UK-BOARD idioms. IR35 and "day rate" are UK contracting vocabulary; a
board in another market spells its bases differently and will simply abstain here, which
is a visible gap rather than a silent misread.
"""
import re

from sluice.core.log import get_logger

_log = get_logger("core.roletype")

CONTRACT = "contract"
PERMANENT = "permanent"

# Provenance values, weakest to strongest. `""` is the absence of a role_type at all;
# `assumed` is what a note with NO `role_type_source` key reads as (#223 §2.1 backfills
# nothing, so every note predating this feature lands there -- fail toward not trusting).
OBSERVED = "observed"
DECLARED = "declared"
ASSUMED = "assumed"
_PROVENANCE_LADDER = ("", ASSUMED, DECLARED, OBSERVED)
# What the relevance gate is allowed to consult. An `assumed` value is the tool's own
# guess about which search found the lead, which is precisely what #223 says is not a
# fact about the posting.
_TRUSTED = frozenset({OBSERVED, DECLARED})

# Stored spellings -> the closed set. Sourced from what a real accumulated vault holds
# (#223 lists contract/Contract/perm/Perm/permanent/Permanent, quoted and unquoted) plus
# the unambiguous near-synonyms a source's `extra` or a user's config might carry.
# Everything else warns rather than being stored.
_ALIASES = {
    "contract": CONTRACT,
    "contractor": CONTRACT,
    "contracting": CONTRACT,
    "contracts": CONTRACT,
    "fixed term": CONTRACT,
    "interim": CONTRACT,
    "temporary": CONTRACT,
    "temp": CONTRACT,
    "freelance": CONTRACT,
    "day rate": CONTRACT,
    "permanent": PERMANENT,
    "perm": PERMANENT,
    "fte": PERMANENT,
}

# What a HUMAN may type for a pay basis, derived from the alias table rather than
# hand-listed beside it: `job-sluice leads add --role-type` (#241) needs the accepted
# set while argparse BUILDS the parser, so it can refuse a typo by listing the real
# names. Deriving it is the whole point -- a hand-listed copy would drift the moment an
# alias is added, and the CLI would then reject a spelling `normalise_role_type` maps
# perfectly well. Sorted so the error argparse prints is stable rather than dict-ordered.
ACCEPTED_ROLE_TYPES = tuple(sorted(_ALIASES))


def fold_role_type(value) -> str:
    """The alias-lookup fold, exposed for `leads add --role-type`'s argparse `type=`.

    argparse tests `choices` against the RAW argument, while `normalise_role_type`
    folds first -- so `choices=ACCEPTED_ROLE_TYPES` alone refuses `Contract`, `PERM`
    and `fixed-term`, every one of which the facade maps correctly and every one of
    which #223 measured in real vault contents. Folding at `type=` (argparse runs it
    BEFORE the choices test) is what makes the CLI and the MCP tool, which share this
    facade, accept the same set. Deriving the CLI's accepted list from `_ALIASES`
    closed the alias half of that gap; this closes the casing/punctuation half.
    """
    return _fold(value)

# JD markers. See the module docstring for why the bar is "near-conclusive" rather than
# "suggestive", and why `full time` is not here.
#
# **A BASIS WORD ALONE IS NOT EVIDENCE**, and an earlier version of this vocabulary
# believed otherwise. Measured on it: "In the interim you will report to...", "the
# interim reporting pack", "We do not offer temporary or agency placements" and "our
# Contractors and Suppliers governance team" ALL observed `contract`, one-sided, so the
# both-sides abstention never engaged. So a qualifier counts only in a BASIS-BEARING
# COLLOCATION -- immediately followed by a noun naming the thing being advertised.
#
# The cost is real and accepted: an advert saying only "we are looking for a contractor"
# now abstains. Recall is the cheap side of this trade. An `observed` value outranks the
# user's own `declared` one, is written to the vault, and is STICKY -- the next run sees
# `observed == previous` and skips, so a hand correction is overwritten again.
# `post` is NOT here, and the reason is the same failure one level in. `observe_role_type`
# folds hyphens to spaces, so `post` as a basis noun matched every `post-<word>` compound
# that happened to follow a qualifier: "you will own contract post-award administration"
# and "temporary post-launch cover is provided by the vendor" both observed `contract`,
# on permanent adverts. The derived set is not automatically safer than the hand-list --
# it just makes each member's shape uniform.
_BASIS_NOUNS = ("role", "position", "contract", "appointment", "assignment",
                "engagement", "basis", "placement", "vacancy", "opportunity")
_CONTRACT_QUALIFIERS = ("fixed term", "interim", "temporary", "temp", "freelance",
                        "contract")
_PERMANENT_QUALIFIERS = ("permanent", "perm")

# Conclusive on their own. This tuple is SHORT because the collocation rule was first
# applied to the qualifiers and not to it, and every one of the five members removed
# since was measured firing on ordinary prose:
#
#   per day          "ships several releases per day", "two million events per day"
#   /day             "we handle 2TB/day of telemetry"
#   per diem         "a per diem is paid for travel to the London office"
#   month contract   "most customers sign a 12 month contract with us"
#
# `per day` and `/day` remain DAY MARKERS in `triage/classify.py`, and that is not an
# inconsistency: a salary field is a short controlled string where they can only mean
# pay, while a JD is prose where they mean throughput. The same split is already
# documented there for bare `pa`.
#
# The recall cost is real and worth naming rather than minimising: "A 6-month contract
# with a strong chance of extension" now ABSTAINS, and that is among the commonest ways
# a contract advert states its nature. It is also lexically indistinguishable from
# "customers sign a 12 month contract". Abstention is the safe side, and a genuine
# contract advert almost always also says IR35, "day rate" or "contract role".
_CONTRACT_STANDALONE = (
    # `inside`/`outside`, never bare `ir35`, and never `umbrella company`. Both were
    # here on the claim that they "appear in no permanent advert", which is false of the
    # jobs whose SUBJECT is contracting: "Permanent Compliance Manager. You will own our
    # IR35 determinations" and "Permanent Payroll Lead. Experience of umbrella company
    # models required" both observed `contract`. The determination phrases are what a
    # role states about ITSELF.
    "inside ir35", "outside ir35",
    # Pay-basis terms of art. A permanent advert can still quote a client-facing day
    # rate (a consultancy describing its own commercial model), so these are the
    # weakest members here rather than the exceptions to the rule above.
    "day rate", "daily rate",
)
# Empty, and that is the honest answer rather than an oversight: no single word is
# conclusive evidence of PERMANENCE. `per annum`, `annual salary` and `fte` were all
# here before review and all three are wrong -- fixed-term and interim staff are quoted
# annual salaries too, pro rata, and "2 FTE" is a headcount rather than a basis.
# Measured on the old set: "12 month FTC. Salary £90,000 per annum" observed `permanent`.
_PERMANENT_STANDALONE = ()

# Phrases naming a role that is BOTH, in sequence. Checked FIRST and abstaining, because
# the collocation rule alone resolves them the wrong way: "contract-to-perm position"
# folds to "contract to perm position", where `perm position` is a permanent collocation
# and no contract one matches. `normalise_role_type` already refuses the STORED form of
# this idea, so an observer that resolved it would contradict its own module.
#
# The tail is unbounded, because this is a question about natural language rather than
# about a vocabulary. That is tolerable HERE and nowhere else: everything this misses
# which carries both a contract and a permanent collocation already abstains through the
# both-sides rule below, and abstaining is the safe direction.
_JD_AMBIGUOUS_MARKERS = (
    "contract to perm", "contract to permanent",
    "temp to perm", "temp to permanent",
    "temporary to perm", "temporary to permanent",
    "contract to hire", "temp to hire",
    "with a view to permanent", "with a view to a permanent",
)
# Deliberately NOT a JD marker, though it IS an annual marker for a SALARY string
# (`triage/classify.py`): bare `pa`. A salary field is a short controlled string where
# `pa` can only mean per annum; a JD is prose where "PA to the CEO" is a job title.


def _collocations(qualifiers, nouns) -> tuple:
    """Every SINGULAR `<qualifier> <noun>` pair, derived rather than hand-listed.

    **Singular only, and that is the third correction this vocabulary has taken.** A
    round of review added plurals, because `boundaried`'s trailing `(?!\\w)` makes every
    phrase exact and "two contract roles" was abstaining. The next round measured what
    the 53 added markers cost: on adverts from the populations where those phrases are
    ordinary SUBJECT MATTER -- recruitment, bid and business development, consultancy,
    procurement, HR, payroll -- 27 of 32 observed `contract`, against 18 before. Every
    one traced to an added plural:

        "You will source candidates for contract opportunities."
        "The team advertises contract vacancies on behalf of our clients."
        "Reporting on temporary placements is part of the role."

    A plural is far likelier to name a CLASS of roles the advert discusses than the one
    it is advertising, and the both-sides rule does not save it: "we advertise both
    permanent and contract positions" fires on the contract side alone, because
    `permanent` there is followed by `and`.

    The self-pair (`contract contract`) is dropped, and NOT as tidiness -- an earlier
    version of this docstring claimed that and was wrong. `boundaried("contract
    contract")` matches the hyphen-and-newline-folded form of "Employment Contract /
    Contract Type: Permanent", which is a permanent advert that would then observe
    `contract`. The filter is load-bearing.
    """
    return tuple(f"{q} {n}" for q in qualifiers for n in nouns if q != n)


def boundaried(pat: str) -> re.Pattern:
    """`pat` with a word boundary on each side that HAS one to assert.

    `(?<!\\w)x(?!\\w)` -- the idiom `triage/classify.py:_word_match` already uses -- is
    correct only while the pattern's own edges are word characters. `/day` starts with a
    slash, so a leading `(?<!\\w)` asserts about the character before the slash and
    "£500/day" fails it on the `0`: the marker would never match the spelling it exists
    for. So each edge is asserted only where the pattern's own character at that edge is
    a word character, which is what makes `pd`/`pa`-shaped markers safe to word-bound
    without breaking `/day`- and `p.a.`-shaped ones.

    PUBLIC because `triage/classify.py`'s pay-basis marker sets carry the same
    `/day`- and `p.a.`-shaped members and precompile them the same way. Returning the
    compiled pattern rather than doing the match keeps that precompilation possible: the
    gate runs these over every lead of every run, and a match helper would rebuild the
    pattern string on each one.
    """
    lead = r"(?<!\w)" if pat[:1].isalnum() or pat[:1] == "_" else ""
    tail = r"(?!\w)" if pat[-1:].isalnum() or pat[-1:] == "_" else ""
    return re.compile(lead + re.escape(pat) + tail)


_JD_CONTRACT_MARKERS = _CONTRACT_STANDALONE + _collocations(
    _CONTRACT_QUALIFIERS, _BASIS_NOUNS)
_JD_PERMANENT_MARKERS = _PERMANENT_STANDALONE + _collocations(
    _PERMANENT_QUALIFIERS, _BASIS_NOUNS)

_JD_CONTRACT_RE = tuple(boundaried(p) for p in _JD_CONTRACT_MARKERS)
_JD_PERMANENT_RE = tuple(boundaried(p) for p in _JD_PERMANENT_MARKERS)
_JD_AMBIGUOUS_RE = tuple(boundaried(p) for p in _JD_AMBIGUOUS_MARKERS)


def _fold(value) -> str:
    """Lowercase, unquote, and collapse every hyphen/underscore/whitespace run to one
    space, so `Fixed-Term`, `fixed_term` and `  FIXED   TERM ` are one lookup key.

    Returns "" for anything that is not a string -- this runs inside the ingest sink
    loop and in `classify`'s read path, where raising on one malformed row would abort
    a whole run (the discipline `Lead.__post_init__` and `Vault._safe_or_blank` state).
    """
    if not isinstance(value, str):
        return ""
    return re.sub(r"[-_\s]+", " ", value.strip().strip('"').strip("'")).strip().lower()


def normalise_role_type(value) -> str:
    """Fold a stored or incoming role_type to `contract | permanent | ""`.

    Warns on an unrecognised non-blank value and returns "". Never raises. Blank is a
    real answer (most leads have no basis recorded at all), so it is silent."""
    folded = _fold(value)
    if not folded:
        return ""
    canonical = _ALIASES.get(folded)
    if canonical is None:
        _log.warning("role_type %r is not one of contract/permanent; read as blank",
                     value)
        return ""
    return canonical


def observe_role_type(jd_text) -> str:
    """The pay basis the POSTING itself states, or "" if it does not state one.

    Abstains when the text carries evidence for both -- an observation outranks the
    user's own declaration (§2.5), so answering a coin-flip here is worse than saying
    nothing and leaving the declaration in force."""
    if not isinstance(jd_text, str) or not jd_text:
        return ""
    # Hyphens fold to spaces so `fixed-term` and `6-month contract` reach the same
    # markers as their spaced spellings; `/` is left alone because `/day` IS a marker.
    text = re.sub(r"[-\s]+", " ", jd_text).lower()
    if any(r.search(text) for r in _JD_AMBIGUOUS_RE):
        return ""
    is_contract = any(r.search(text) for r in _JD_CONTRACT_RE)
    is_permanent = any(r.search(text) for r in _JD_PERMANENT_RE)
    if is_contract and not is_permanent:
        return CONTRACT
    if is_permanent and not is_contract:
        return PERMANENT
    return ""


def provenance_rank(source) -> int:
    """Where `source` sits on `observed > declared > assumed > ""`. An unknown value
    ranks at the BOTTOM, with `""` -- the same fail-toward-not-trusting direction §2.1
    gives a note with no `role_type_source` key at all."""
    if not isinstance(source, str):
        return 0
    try:
        return _PROVENANCE_LADDER.index(source.strip().lower())
    except ValueError:
        return 0


def trusted_provenance(source) -> bool:
    """May the relevance gate consult a role_type carrying this provenance? Only an
    `observed` or `declared` one -- #223's whole complaint is that the gate trusts a
    value that records which search ran."""
    return isinstance(source, str) and source.strip().lower() in _TRUSTED
