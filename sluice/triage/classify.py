"""Deterministic pre-gate: keep | reject | needs_review, with a plain-language
reason. Runs before any dossier build or LLM call, so obvious cases cost nothing.
The gate stays conservative: it rejects only high-confidence disqualifiers from the
user's own configured lists and hands anything ambiguous to the LLM, because
false-negatives are what the audit catches. It ships with no lists of its own, so an
unconfigured gate abstains rather than applying somebody else's idea of a good role.
"""
import re

from sluice.core.leads import is_placeholder_company
from sluice.core.roletype import (
    CONTRACT,
    PERMANENT,
    boundaried,
    normalise_role_type,
    trusted_provenance,
)

# Boards advertise pay in wildly inconsistent shapes: "£60k", "£30,000-£40,000",
# "up to £75k", "£500/day", "£50,000 + 10% bonus". The old parser stripped every
# non-digit and concatenated whatever was left, which broke the floors in BOTH
# directions:
#   "£30,000-£40,000"  -> 3000040000, sailing over any floor (the gate silently did
#                         nothing, so a configured floor still showed you £30k jobs)
#   "£1.5k/day"        -> 15, binned by any floor (the gate silently rejected a lead
#                         the user wanted -- the failure that actually costs)
# Parse the money tokens properly instead, and abstain when nothing credible parses.
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")

# A bare digit run is NOT money. A salary field reading "Ref 50000" or "postcode 1234"
# would otherwise parse as an advertised salary and be REJECTED by a floor -- binning a
# lead whose pay was never stated at all. That is the fails-closed direction, i.e. exactly
# the bug class this module exists to remove, coming back in through the parser.
#
# So a number only counts as money when it carries MONEY CONTEXT: a currency symbol, or a
# k suffix (which is itself unambiguous). Anything else is ignored, and a string with no
# money in it yields no opinion -- and no opinion never rejects.
_MONEY_RE = re.compile(
    r"[£$€]\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?\b"   # £60k, £30,000, $120,000
    r"|(\d[\d,]*(?:\.\d+)?)\s*([kK])\b"           # 60k -- the suffix IS the context
)

# Boards routinely write a range with ONE symbol: "£30,000-40,000". The upper bound then
# carries no money context of its own, so _MONEY_RE alone reads the ceiling as 30,000 and a
# £35k floor REJECTS a role paying up to £40k -- fails-closed, the expensive direction. A
# bare number is money when it is the tail of a range whose head was money.
_RANGE_TAIL_RE = re.compile(
    r"\s*(?:-|–|—|to)\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?\b", re.I)

# Below these, a parse is not a real offer -- it is a mis-parse. Abstain rather than
# reject: a wrong reject bins a lead the user never sees, the expensive direction.
#
# Parsing facts, not preferences, and the property that makes that claim checkable is
# that they are MONOTONE: each appears only inside its branch's reject CONJUNCTION, so it
# can turn a reject into an abstain and never the reverse. A number that cannot
# manufacture a reject cannot encode an opinion about which jobs are good.
_MIN_CREDIBLE_HOURLY_RATE = 5
_MIN_CREDIBLE_DAY_RATE = 50
_MIN_CREDIBLE_WEEKLY_RATE = 100
_MIN_CREDIBLE_SALARY = 1000

# How boards SPELL a pay basis (#223 §2.3). Parsing facts, not preferences: they encode
# how a rate is written, never which rate is desirable, so there is no `*Config` field
# and no `sluice.yaml.example` entry for either. ENGLISH/UK-BOARD idiom -- a board in
# another market spells its bases differently and falls through to step 4, which is a
# visible gap rather than a silent misread.
#
# Matched via `roletype.boundaried`, never plain containment: bare `pd` and `pa` inside
# an ordinary word is #128's bug class through a new door. That helper asserts a word
# boundary only on an edge the pattern HAS one for, which is what lets `/day` and `p.a.`
# sit in the same tuple as `pd` -- `_word_match` above cannot, because it asserts both
# edges unconditionally and "£500/day" then fails the assertion on the `0`.
#
# Compiled once at import, like `roletype`'s own JD sets: the gate runs every one of them
# over every lead of every run. No COUNT here on purpose -- this comment said "all
# fourteen of these" and the very next commit doubled the table, which is the third stale
# count in this branch's own prose. The number is the drift surface; `_BASES` is the
# answer, and it is two lines down.
_HOUR_MARKERS = ("/hour", "/hr", "per hour", "hourly", "an hour", "a hour", "p/h", "ph")
_DAY_MARKERS = ("/day", "per day", "day rate", "a day", "daily", "per diem", "p/d", "pd")
_WEEK_MARKERS = ("/week", "/wk", "per week", "weekly", "a week", "p/w", "pw")
# `a year` carries the `a <unit>` spelling the other three rows all have, and its absence
# was not symmetry for its own sake: it is the exact spelling of the only real annual
# salary in the golden fixtures (`tests/fixtures/indeed/raw.json`, "£60,000 - £70,000 a
# year"). Without it that string reached NO basis, so it fell to the annual branch by
# default -- right by accident -- and flipped to the DAY branch under a trusted
# `role_type: contract`, which is the dependency §2.3 exists to remove.
_ANNUAL_MARKERS = ("per annum", "p.a.", "pa", "/year", "per year", "a year", "annually")

# basis -> (its markers, its credibility floor, the config key holding its floor, the
# noun the reject message uses). ONE table, so a basis cannot exist with no floor to be
# judged against, and cannot be judged against another basis's floor -- which is exactly
# the harm an earlier draft of §2.3 declined hourly/weekly support to avoid. That draft
# read the harm as "do not parse these"; it was really "do not reuse the DAY floor".
_BASES = {
    "hour": (_HOUR_MARKERS, _MIN_CREDIBLE_HOURLY_RATE, "contract_floor_gbp_hour",
             "Hourly rate"),
    "day": (_DAY_MARKERS, _MIN_CREDIBLE_DAY_RATE, "contract_floor_gbp_day", "Day rate"),
    "week": (_WEEK_MARKERS, _MIN_CREDIBLE_WEEKLY_RATE, "contract_floor_gbp_week",
             "Weekly rate"),
    "annual": (_ANNUAL_MARKERS, _MIN_CREDIBLE_SALARY, "perm_floor_gbp", "Salary"),
}
_BASIS_RE = {name: tuple(boundaried(m) for m in markers)
             for name, (markers, _f, _k, _n) in _BASES.items()}


def _pay_basis(salary: str, role_type: str, source: str) -> str | None:
    """A key of `_BASES`, `"ambiguous"`, or None for "the lead does not say" (#223 §2.3).

    The order is the whole design. The SALARY's own markers are read first, so the
    posting's own words beat everything; only an UNMARKED salary consults `role_type`, and
    only when the note records the value as observed on the posting or declared by the
    user. An `assumed` one is the tool's guess about which search ran, which is the defect
    #223 exists to close.

    **An advert naming TWO bases abstains**, the same rule `observe_role_type` follows for
    a JD carrying evidence both ways. First-match-wins would be an arbitrary precedence
    between two things the advert actually said, and the cost is not symmetric: the day
    branch's reject window sits exactly where an hourly figure lands, so a wrong pick
    manufactures a reject.

    None falls through to the caller's EXISTING annual branch, byte-for-byte today's
    behaviour for a bare amount. Deliberately not improved by a guess.

    **There is no magnitude step, and that omission is measured rather than stylistic.**
    An earlier draft selected the basis from low/high thresholds, by analogy with
    `_MIN_CREDIBLE_DAY_RATE`. The analogy is false: those constants appear only inside the
    reject CONJUNCTION, so they are monotone -- they can only turn a reject into an
    abstain. A step that selects the BRANCH is bidirectional, and the day branch's reject
    window `[_MIN_CREDIBLE_DAY_RATE, contract_floor_gbp_day)` is exactly where small
    unmarked numbers land, so a low threshold MANUFACTURES rejects: £300 and £450 both
    keep on the annual branch and reject on the day one.
    """
    text = (salary or "").lower()
    named = [name for name, patterns in _BASIS_RE.items()
             if any(r.search(text) for r in patterns)]
    if len(named) > 1:
        return "ambiguous"
    if named:
        return named[0]
    if trusted_provenance(source):
        if role_type == CONTRACT:
            return "day"
        if role_type == PERMANENT:
            return "annual"
    return None


def _salary_amounts(s: str) -> list[int]:
    """Every money amount in `s`, with k-notation expanded ("60k" -> 60000).

    Percentages are stripped first, so "£50,000 + 10% bonus" does not contribute a
    spurious 10.
    """
    if not s:
        return []
    def _to_int(raw: str, k: str) -> int | None:
        try:
            value = float(raw.replace(",", ""))
        except ValueError:  # pragma: no cover - regex only yields parseable numbers
            return None
        # round(), not int(): int() truncates a float product toward zero, and 2.01 is not
        # exactly representable, so int(2.01 * 1000) is 2009. A rate sitting on a floor
        # would flip keep->reject on representation error, not on the pay.
        return round(value * 1000) if k else int(value)

    out: list[int] = []
    text = _PERCENT_RE.sub(" ", s)
    for m in _MONEY_RE.finditer(text):
        cur_raw, cur_k, k_raw, k_suffix = m.groups()
        v = _to_int(cur_raw or k_raw, cur_k or k_suffix)
        if v is not None:
            out.append(v)
        # ...and the tail of a single-symbol range ("£30,000-40,000") is money too.
        tail = _RANGE_TAIL_RE.match(text, m.end())
        if tail:
            tv = _to_int(tail.group(1), tail.group(2))
            if tv is not None:
                out.append(tv)
    return out


def _salary_ceiling(s: str) -> int | None:
    """The TOP of the advertised pay, or None when nothing parses.

    The top, not the bottom, because a floor check must fail OPEN. Rejecting an
    "£80,000-£120,000" lead against a £90k floor on the strength of its lower bound
    would bin a job the user wants; only when even the best case is under the floor is
    the reject safe. `None` means "no opinion" and never rejects.
    """
    amounts = _salary_amounts(s)
    return max(amounts) if amounts else None


# #128: a WORD-BOUNDARY match, not `pat in text`. Plain substring containment treats
# "engineer" as present inside "engineering" -- a strict character-for-character
# prefix -- so every reject pattern ending in "engineer" (machine learning engineer,
# security engineer, mobile engineer, staff engineer, solutions engineer, sales
# engineer, network engineer, project engineer, ...) silently matched inside a role
# that was actually titled "... Engineering Manager" or "... Engineering Lead": a
# different word, wearing the first 8 letters of "engineer" as a coincidence of
# English spelling, not a real match. The accept-list override did not save these
# either -- it only excuses a reject pattern that is ITSELF a substring of the
# matched accept phrase, and "security engineer" is not a substring of "engineering
# manager", so a role carrying BOTH an accepted title and this collision still got
# killed. Real leads lost to this before ever reaching the LLM judge: "Ads Conversion
# Modeling, Machine Learning Engineering Manager", "Software Security Engineering
# Manager, Secure Frameworks", "mobile engineering manager", "Staff Engineering Lead".
# Same shape independently affects the bare `vp` pattern, which currently matches
# inside "svp" for the identical reason.
#
# `\b` requires a transition between a \w character and a non-\w one (or a string
# edge), so `\bengineer\b` fails to match inside "engineering": the "r" ending
# "engineer" and the "i" starting "ing" are both word characters, with no boundary
# between them. A genuine standalone "Senior Software Engineer" is unaffected --
# "engineer" there is followed by a space or end-of-string, both real boundaries.
#
# `\b` itself breaks for a PATTERN that starts or ends in a non-word character --
# "c++", "c#", "sr.", ".net" -- because `\b` requires a WORD-to-non-word transition,
# and a pattern edge that is already non-word (the second "+" in "c++", the "."
# in "sr.") can never be one side of that transition. `\bc\+\+\b` verified live to
# fail against "C++ Developer": the boundary before "c" holds (space -> word), but
# there is no boundary after the second "+" (non-word "+" next to non-word " ").
# Lookaround assertions check ABSENCE of an adjacent word character on each side
# instead of a transition, so they hold regardless of which side of the pattern is
# itself non-word -- verified live to match all four cases above, and confirmed to
# still refuse "engineer" inside "engineering" and "vp" inside "svp" identically to
# the `\b` form (there the pattern's own edges are word characters, so both forms
# agree).
def _word_match(pat: str, text: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(pat) + r"(?!\w)", text) is not None


def _legacy_pay_basis(lead: dict) -> str:
    """The branch the PRE-#223 gate would have selected, spelled out verbatim.

    Kept -- and kept EXACT rather than approximated -- for one caller: `reverdict_notice`
    below. Every approximation tried during implementation under-reported in the
    direction of the harm. A probe that simply forces this lead's `role_type` to be
    TRUSTED misses two populations: `Contract-to-perm`, which the old SUBSTRING test sent
    down the day branch and the closed set now folds to blank, and an annual-marked
    salary on a contract-labelled lead, because §2.3's marker step runs before the
    provenance one. Both flip verdict silently, which is precisely what the notice
    exists to stop.

    Delete this, and `reverdict_notice`, once no vault predating #223 is plausible.
    """
    role_type = (lead.get("role_type") or "").lower()
    salary = (lead.get("salary") or "").lower()
    if "contract" in role_type or "/day" in salary or "per day" in salary:
        return "day"
    return "annual"


def _pay_reject(salary: str, basis: str, cfg) -> tuple[str, str] | None:
    """The floors' verdict for a STATED basis, or None for no opinion.

    Both branches abstain on an implausible parse rather than trusting it. The perm
    branch always had its credibility guard; the contract branch did not, which is why a
    "£1.5k/day" lead was silently rejected while the identical mistake on the perm side
    was harmless.

    Split out from `classify` so the re-verdict notice can ask the same floors the same
    question under the OTHER basis, rather than re-implementing them and drifting.

    A basis outside `_BASES` -- `"ambiguous"`, or a `None` the caller did not default --
    yields NO OPINION rather than falling back to a floor. Defaulting an unknown basis to
    `annual` here is how `£2,000 per week` came to be judged as a sub-90,000 salary; the
    caller defaults an UNMARKED salary deliberately and visibly, and nothing else should.
    """
    amount = _salary_ceiling(salary)
    if amount is None or basis not in _BASES:
        return None
    _markers, credible, key, noun = _BASES[basis]
    floor = getattr(cfg, key, 0)
    if amount >= credible and amount < floor:
        return "reject", f"{noun} below floor: {amount} < {floor}"
    return None


def reverdict_notice(lead: dict, cfg) -> str | None:
    """What #223 changes for THIS lead's pay verdict, or None when it changes nothing.

    A note written before #223 carries no `role_type_source` key and reads as `assumed`,
    so the gate stops consulting its `role_type` (§2.1). On an accumulated vault that is
    a BATCH of leads changing verdict at once, on the first run after an upgrade -- and
    `dismiss` is not in `DEFAULT_TRIAGE_STATUSES`, so a lead dismissed that way is never
    re-selected and the user never sees it again.

    Compares the PAY GATE's own verdict under each basis, which is the thing that
    actually moves. A lead an earlier gate already rejects is not affected: `classify`
    returns the FIRST reject it finds, so a current reject whose reason is not the
    current pay reject means the pay branch never decided this lead at all.
    """
    salary = lead.get("salary") or ""
    was = _legacy_pay_basis(lead)
    now = _pay_basis(salary, normalise_role_type(lead.get("role_type")),
                     lead.get("role_type_source") or "") or "annual"
    if was == now:
        return None
    before, after = _pay_reject(salary, was, cfg), _pay_reject(salary, now, cfg)
    if before == after:
        return None
    verdict, why = classify(lead, cfg)
    if verdict == "reject" and (after is None or why != after[1]):
        return None
    return (f"pay was judged as {was}, now judged as {now}: "
            f"{'reject' if before else 'keep'} -> {'reject' if after else 'keep'}"
            f"{' (' + after[1] + ')' if after else ''}")


def classify(lead: dict, cfg) -> tuple[str, str]:
    role = (lead.get("role") or "").lower()
    company = (lead.get("company") or "").strip()
    location = (lead.get("location") or "").lower()
    salary = (lead.get("salary") or "")
    # Folded on READ as well as on write (#223 §2.2). A note's frontmatter is a file a
    # human edits in Obsidian, so `Contract` typed by hand must reach the same branch
    # `contract` does -- and `Contract-to-perm`, which the old substring test sent down
    # the contract branch on its first eight characters, must reach neither.
    role_type = normalise_role_type(lead.get("role_type"))
    # The PERSISTED key, never re-derived from config (§2.5). Asking whether the source's
    # search is configured TODAY would consult a legacy value written before #223 ran and
    # recreate the false KEEP this whole issue is about.
    role_type_source = lead.get("role_type_source") or ""

    # The accept list exists to stop a BROAD reject pattern from killing a good
    # title: a bare "manager" reject must not disqualify an accepted "<x> manager".
    # So a reject pattern is ignored only when it is part of the accepted phrase.
    #
    # It must NOT go further and wave the role through wholesale. A title can carry
    # an accept token AND an unrelated disqualifier ("<accepted role> / <rejected
    # role>"), and those mixed titles are exactly what the gate exists to catch.
    # A blanket accept-wins rule let every one of them through.
    matched_accepts = [t for t in cfg.accept_titles if _word_match(t, role)]

    for pat in cfg.reject_titles:
        if _word_match(pat, role) and not any(_word_match(pat, acc) for acc in matched_accepts):
            return "reject", f"Role not a fit: {pat}"

    if any(c in company.lower() for c in cfg.reject_companies):
        return "reject", f"Company skipped: {company}"

    if any(b in location for b in cfg.reject_locations):
        return "reject", "Location outside target geography"

    # Guarded on target_locations being set. Without the guard an empty list makes
    # `not any(...)` true for every located lead, so an unconfigured install would
    # reject EVERY job that names a location -- the opposite of abstaining.
    if cfg.target_locations and location and not any(
            t in location for t in cfg.target_locations):
        return "reject", "Location outside target geography"

    # Pay floors. `None` from `_pay_basis` means the lead states no basis, and falls
    # through to the annual branch -- byte-for-byte the pre-#223 behaviour for a bare
    # unmarked amount.
    pay = _pay_reject(
        salary, _pay_basis(salary, role_type, role_type_source) or "annual", cfg)
    if pay:
        return pay

    # `is_placeholder_company` catches blank AND every honest non-answer a board or
    # a legacy/foreign note carries ("Unknown", "Confidential", "N/A", ...) --
    # not just the bare "unknown" sentinel this used to check by itself. Kept LAST,
    # after every reject check above: a placeholder company must not short-circuit
    # a role/location/pay reject the lead would otherwise earn.
    if is_placeholder_company(company):
        return "needs_review", "Blank/placeholder company; visit URL to identify"

    return "keep", ""
