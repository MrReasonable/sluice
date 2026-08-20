"""The application packet: what the browser-assisted step needs to fill a form.
Text is slop-clean (no em dashes). The listing host is best-effort and usually the
board, not the company ATS, which is found during the browser step."""
import dataclasses
import json
import os
from urllib.parse import urlparse

from sluice.core.candidate import age_from_dob
from sluice.core.protocols import CandidateProfile

_HOST_LABELS = [
    ("linkedin.", "linkedin"), ("indeed.", "indeed"),
    ("greenhouse.io", "greenhouse"), ("ashbyhq.com", "ashby"),
    ("lever.co", "lever"), ("workable.com", "workable"),
    ("icims.com", "icims"), ("teamtailor.com", "teamtailor"),
]
_SKILL = "job-application-workflow"

# `CandidateProfile` fields that never reach this dict directly -- each has its own
# reason, grouped below rather than one flat exclusion list so the reason for each
# member stays attached to it.

_IDENTITY_KEYS = frozenset({"forenames", "surname", "email", "mobile", "linkedin"})
# Feeds cv (full_name/contact_block, core/candidate.py), never apply's packet:
# render_text's own RULES block says "Use first names only. No real full names in
# third-party forms" -- the CV upload IS the name/contact channel a third-party ATS
# already gets, and a second, uncontrolled copy of the same data in the packet would
# undercut that rule rather than support it.

_HOW_HEARD_KEYS = frozenset({"how_heard_default", "how_heard_detail_from_lead_source"})
# Resolved into ONE derived `how_heard` packet key via resolve_how_heard() below --
# never passed through raw, because the resolution also depends on the listing host,
# which these two fields alone cannot express.

_DOB_KEY = frozenset({"date_of_birth"})
# Resolved into a derived `age` packet key via age_from_dob() below -- the raw DOB is
# never a packet key.

_EXCLUDED_KEYS = _IDENTITY_KEYS | _HOW_HEARD_KEYS | _DOB_KEY

_PASSTHROUGH_KEYS = tuple(
    f.name for f in dataclasses.fields(CandidateProfile) if f.name not in _EXCLUDED_KEYS
)
# DERIVED from the CandidateProfile roster, not hand-listed -- the same reasoning as
# core/vault.py's read_candidate_profile, which builds its `known` set from
# dataclasses.fields() rather than keeping a second, hand-maintained copy of the field
# list that could quietly drift out of sync. A field later added to CandidateProfile
# needs exactly one decision here (join one of the three excluded sets above, or fall
# through as plain passthrough) rather than an easy-to-forget SECOND edit to a tuple.

_DETAIL_KEYS = (
    "address_line1", "address_line2", "town", "county", "postcode", "country",
    "requires_uk_work_permit", "right_to_work_uk", "currently_employed_by_them",
    "previously_employed_by_them", "referred_by_current_employee",
    "honorific", "first_language", "served_armed_forces", "caring_responsibility",
    "worked_in_construction",
)
# Hand-listed as the SAFE set, deliberately: the derivation used
# to run the other way (_DETAIL_KEYS = _PASSTHROUGH_KEYS minus a hand-listed warned
# set), which meant a CandidateProfile field added later and forgotten in the
# hand-list fell silently into the LESS-warned section. Hand-listing the safe set and
# deriving the warned one as the remainder (below) makes an unclassified field
# over-warn instead of under-warn -- the direction an omission should cost for
# equality/protected-characteristic data. `served_armed_forces`,
# `caring_responsibility` and `first_language` were reviewed and kept here, not
# warned.

_WARNED_KEYS = tuple(k for k in _PASSTHROUGH_KEYS if k not in _DETAIL_KEYS)
# DERIVED, not hand-listed -- see _DETAIL_KEYS' comment for why this direction.
# Currently resolves to gender_identity, identifies_as_trans, ethnicity, religion,
# sexual_orientation, preferred_pronouns, disability, neurodivergent,
# open_about_orientation_at_work, marital_status, nationality, dual_nationality.
# The last three joined the original nine EO-monitoring fields alongside the
# safe/warned split introduced above: marital_status is the Equality Act 2010
# "marriage and civil partnership" characteristic, and nationality/dual_nationality
# both map onto "race" (which covers national origins). `age` is a FOURTH field
# that same change moved into the warned SECTION of render_text's output, but it
# is not, and cannot be, a member of
# THIS tuple: it is not a CandidateProfile field at all (it is derived from
# date_of_birth) -- render_text below adds it to that same rendered section by
# hand instead.

_WARNED_HEADING = "  EQUAL OPPORTUNITIES MONITORING (optional on most forms):"
# Deliberately not "special-category" -- that is a UK GDPR Article 9 term covering
# racial/ethnic origin, religion, health, sex life and sexual orientation, and does
# NOT cover age or marital status, both of which are also in _WARNED_KEYS.
# "Equal opportunities monitoring" is the accurate umbrella: real UK job application
# forms commonly bundle age and marital status into the same EO-monitoring section
# as ethnicity/religion/disability, so labelling them together here matches the form
# the answers are destined for.


def listing_host(url):
    netloc = urlparse(url or "").netloc.lower()
    for needle, label in _HOST_LABELS:
        if needle in netloc:
            return label
    return "other"


def resolve_how_heard(profile, host):
    """Prefer the computed lead source over the stored default when the caller asked
    for that AND the host actually resolved to something specific -- "" or "other"
    means sluice could not identify the board, so there is nothing more specific to
    offer than the user's own default. None means "nothing to say": the caller OMITS
    the `how_heard` key rather than writing a null, the same shape `age` uses below.

    Parameter named `host`, not `listing_host`: the latter shadowed
    the module-level `listing_host` function for the whole body of this function,
    harmlessly today but a `'str' object is not callable` trap for the next edit that
    tries to call it from in here."""
    prefer_lead = profile.how_heard_detail_from_lead_source.strip().lower() == "true"
    if prefer_lead and host not in ("", "other"):
        return host
    return profile.how_heard_default.strip() or None


def build_packet(note, cfg, *, profile, today, cv_staged):
    """`profile` and `today` are KEYWORD-ONLY, joining the `*` this function already
    used. Two new required POSITIONAL parameters would silently transpose at any call
    site that still passes positionally, and there were three call sites in sluice/
    plus several in tests/ when this parameter pair was added.

    `today` is an ISO 8601 string, not a date -- see core/candidate.py's
    age_from_dob for why. `Sluice._today` is a zero-arg CALLABLE or `None`, never a
    string itself, so a caller reaching this function through Sluice must resolve it
    to a string first. `Sluice.prep` (the sole production caller, core/app.py) does
    that ONCE per invocation, inside `self.staleness()`, and reads the resolved date
    back off the frozen `StalenessPolicy` that call returns (`policy.today`) rather
    than resolving `self._today` a second time beside it -- two independent
    resolutions in one `prep()` call could straddle midnight and hand this function
    two different dates for what should be one run. Passing `self._today` unresolved
    (the bare callable) would hand this function a callable where it expects a
    string.

    Every profile-derived key is included ONLY when declared. An undeclared field is
    absent from the dict entirely, never present as "", so the form-filling step can
    tell "sluice has nothing to offer" from "sluice knows this is blank"."""
    fm = note.fm
    url = (fm.get("url") or "").strip().strip('"')
    host = listing_host(url)
    pkt = {
        "company": fm.get("company", ""),
        "role": fm.get("role", ""),
        "location": fm.get("location", ""),
        "salary": fm.get("salary", ""),
        "url": url,
        "listing_host": host,
        "cv_path": os.path.join(cfg.camofox_cv_dir, cfg.neutral_name) if cv_staged else None,
        "skill": _SKILL,
    }
    for key in _PASSTHROUGH_KEYS:
        value = getattr(profile, key).strip()
        if value:
            pkt[key] = value
    age = age_from_dob(profile.date_of_birth, today)
    if age is not None:
        pkt["age"] = age
    how_heard = resolve_how_heard(profile, host)
    if how_heard is not None:
        pkt["how_heard"] = how_heard
    return pkt


def render_text(p):
    lines = [
        f"APPLICATION PACKET: {p['company']} - {p['role']}",
        f"  location: {p['location'] or 'n/a'}   salary: {p['salary'] or 'n/a'}",
        f"  job url: {p['url'] or 'n/a'}",
        f"  listing host: {p['listing_host']} (best-effort; the real ATS is on the company careers page)",
    ]
    if p["cv_path"]:
        lines.append(f"  CV staged for upload: {p['cv_path']}")
    else:
        lines.append("  CV not staged (preview mode). Stage this lead's CV with apply prep before applying.")
    # `_DETAIL_KEYS` is the hand-listed SAFE set (see its own comment above);
    # `_WARNED_KEYS` is everything else in `_PASSTHROUGH_KEYS`, so a field NOT YET
    # classified into `_DETAIL_KEYS` renders under the warned heading below by
    # default, never silently here. What this loop does NOT
    # guarantee on its own is that every name in `_DETAIL_KEYS` truly belongs there;
    # that is a human judgement each new hand-list entry makes.
    # `test_every_passthrough_field_reaches_both_the_packet_and_render_text`'s count
    # pin over `_PASSTHROUGH_KEYS` is what forces that judgement: a CandidateProfile
    # field that is neither excluded above nor added to `_DETAIL_KEYS` still changes
    # the derived tuples' combined length, and the pin reddens before either loop
    # here runs.
    details = [(k, p[k]) for k in _DETAIL_KEYS if k in p]
    if "how_heard" in p:
        details.append(("how_heard", p["how_heard"]))
    if details:
        lines.append("  DETAILS:")
        lines += [f"    {k}: {v}" for k, v in details]
    warned = [(k, p[k]) for k in _WARNED_KEYS if k in p]
    if "age" in p:
        warned.append(("age", p["age"]))
    if warned:
        # Printed by DEFAULT, not withheld behind a flag: the user asked sluice to
        # fill these forms, and withholding the answers leaves them retyping the
        # exact fields #133 is about. The heading is the mitigation -- what this
        # data is, stated where it appears. `apply prep --json` exists for anyone
        # piping the packet somewhere it will not be read on a terminal at all --
        # render_json emits the identical values with no heading of its own, so
        # `--json` is not itself a mitigation for retention.
        lines.append(_WARNED_HEADING)
        lines += [f"    {k}: {v}" for k, v in warned]
    lines += [
        "  RULES:",
        "    - Never use one-click apply. Go to the company's own ATS.",
        "    - Use first names only. No real full names in third-party forms.",
        "    - Never auto-submit. You review on VNC and click submit.",
        "    - Never guess a value for a field that is not in this packet. If an ATS",
        "      asks something with no matching field here, leave it for the human.",
        # Unconditional, not folded behind `if warned:`: the
        # guidance is for the case this packet has NO value, so gating it on the
        # opposite condition (something IS declared) suppressed it exactly when it
        # would matter, and showed it, vacuously, only when it was not needed. Worded
        # to avoid `_WARNED_HEADING`'s own text so the two remain independently
        # detectable in rendered output. Hedged as "usually optional" to match the
        # heading's own "optional on most forms" rather than overclaiming "every".
        "    - Equality-related form questions (age, ethnicity, disability, and",
        "      similar) are usually optional. With no value here, choose 'prefer",
        "      not to say'.",
    ]
    lines.append(f"  Form-fill technique: skill '{p['skill']}'.")
    return "\n".join(lines)


def render_json(p):
    return json.dumps(p, ensure_ascii=False)
