"""Derivations over a CandidateProfile. Mirrors core/criteria.py on the split that
matters -- the contract TYPE lives in core/protocols.py (interface only, no logic);
anything with a body lives here -- though unlike that module, this one is not
wholly pure: `age_from_dob` emits log warnings (never the raw value, only the field
name) on an unparseable or impossible date, in the shape `core/leads.py`'s
`ambiguous_slug_warnings` uses ONE module over (return messages, let the caller log)
rather than mirrors. Kept as a direct emit here, deliberately: threading the warning
back through `apply/packet.py`'s sole call site would change `age_from_dob`'s return
shape from `int | None` to something carrying both, for one low-traffic warning path
with four tests already pinned against its caplog behaviour -- judged not worth the
ripple for this PR. No filesystem, network, store or config access; every function
takes what it needs as an argument.
"""
import dataclasses
from datetime import date

from sluice.core.log import get_logger
from sluice.core.protocols import CandidateProfile

_log = get_logger("core.candidate")


def full_name(profile: CandidateProfile) -> str:
    """The CV header's name line. Joins whichever parts are declared, collapsing
    any internal whitespace RUN to one space -- not just stripping the ends.

    cv/engine.py's #99/#100 STRUCTURAL guard case-fold-matches the composed
    header's last line against this value (Task 3) -- it no longer compares
    against `cvcfg.name`. The normalization is FOR that guard: a composer that
    collapses a whitespace run (models routinely do) would otherwise fail the
    anchor check and the lead would be binned after its one retry with an
    otherwise gate-clean CV. `.split()` with no argument already splits on any
    run of whitespace and drops empty tokens, so
    `" ".join(a.split() + b.split())` is lossless for every sane input and
    removes the class outright rather than patching one run width.
    """
    return " ".join(profile.forenames.split() + profile.surname.split())


def contact_block(profile: CandidateProfile) -> str:
    """The CV header's contact block: the BARE declared value, one per line, in
    mobile/email/linkedin order, undeclared lines omitted rather than emitted empty.

    Bare, not labelled. The retired `cv.contact` config key (#133/#107) used to
    illustrate labels ("Phone number: ..."), but those were one user's formatting
    choice living in a value they could edit. Moving them here would make them a
    shipped constant with no override, which is a formatting preference in code.
    A user who wants a label puts it in the field value -- the field is free text.

    cv/engine.py's #99/#100 STRUCTURAL guard compares the composed CV's header
    block against this value (Task 3) -- it no longer compares against
    `cvcfg.contact`/`cvcfg.name`. Whatever this returns is what the composer is
    told to emit and what the guard expects back.
    """
    lines = [v for v in (profile.mobile.strip(), profile.email.strip(),
                         profile.linkedin.strip()) if v]
    return "\n".join(lines)


def has_any_declared(profile: CandidateProfile) -> bool:
    """True when ANY of the 36 fields is declared.

    `cmd_init` (cli.py) gates BOTH its candidate-profile write and its existence probe on this
    predicate, and that sameness is the point: if the write happened, the probe returns True, so
    the interview gate always closes. A `full_name`-based probe would re-ask forever for a user who
    answered only `email` -- the note would exist and be useful but `full_name` would stay blank.
    """
    return any(getattr(profile, f.name).strip() for f in dataclasses.fields(profile))


def age_from_dob(dob: str, today: str) -> int | None:
    """Whole years between two ISO 8601 (YYYY-MM-DD) dates, or None.

    `today` is a `str`, not a `date`, deliberately matching `Sluice.staleness`'s
    existing pattern (core/app.py): `self._today` is a zero-arg CALLABLE returning
    a string, never a string itself, and `Sluice.staleness`'s own docstring already
    records the trap of binding the unresolved callable into a typed value. Cited
    by SYMBOL rather than a line range -- this repo has twice been bitten by a
    line count in prose going stale silently.

    A non-`str` `today` RAISES `TypeError` naming `today`, checked BEFORE any parse
    is attempted (rev5-002). This is what the precedent this parameter's type
    was chosen from actually does: `StalenessPolicy` refuses a non-`str` at
    construction so the mistake cannot reach a gate silently. It must NOT share
    `dob`'s abstain path below: the warning there names `date_of_birth`, so letting
    an unresolved-callable `today` fall through to it would point the operator at
    the user's vault note while the bug sits in sluice's own caller, on every
    lead, with the age silently absent from every packet.

    This separation is TYPE-only, not exhaustive: a `today` that IS a `str`
    but unparseable -- an injected clock returning garbage, say -- still reaches
    the `except` below and logs "date_of_birth is not ISO 8601", pointing the
    operator at the vault note for what is actually a caller-side fault. That
    behaviour is deliberately left alone: the non-`str` case is the one the
    clock trap above actually produces, and parse-checking `today` separately
    here would duplicate the parse below for a fault nobody has hit.

    A blank `date_of_birth` abstains SILENTLY (rev5-001) -- `""` is the
    designed default of an optional field, so warning on it would warn on every
    lead of every run for a user who simply declined to declare one, which is how
    a codebase teaches its users to ignore warnings. Only a NON-blank `dob` that
    fails to parse warns.

    For `dob`, catches `ValueError` and `TypeError` only. `ValueError` is a
    malformed or out-of-range date string; `TypeError` is a non-`str` reaching the
    field (e.g. `None`) -- `dob` comes from a user's vault note and must not crash
    packet-building even when it is not a plain str. The narrow tuple is NOT what
    surfaces the clock trap -- it would swallow that `TypeError` exactly as a bare
    `except Exception` would, which is why the guard on `today` above exists as a
    separate, earlier check. It stays narrow so an unforeseen exception class
    propagates instead of being silently converted into "this user declared no
    DOB".

    A `dob` later than `today` abstains AND WARNS (rev5-003): the function
    already declines to guess at an unparseable date, and an impossible one is
    the same case -- a DECLARED value that cannot be used, not an undeclared
    one. The line is declared-versus-undeclared, not
    abstain-versus-not: a blank `date_of_birth` is a user declining to answer
    (silent, above); a future one is a declared value the same way an
    unparseable one is, so it gets the same remedy -- a warning naming the
    field, distinct from the not-ISO-8601 message. This also makes a transposed
    `age_from_dob(today, dob)` -- two `str`s, so no type guard can see it --
    abstain-and-warn rather than silently report a large negative "age".

    The warning names the FIELD, never the raw value -- a log is a plausible place
    for a sensitive value to leak into a bug report.
    """
    if not isinstance(today, str):
        raise TypeError(
            f"age_from_dob: today must be an ISO 8601 str, got {type(today).__name__} "
            "-- did you pass Sluice._today unresolved instead of calling it?"
        )
    if isinstance(dob, str) and not dob.strip():
        return None
    try:
        born = date.fromisoformat(dob)
        now = date.fromisoformat(today)
    except (ValueError, TypeError):
        _log.warning("candidate: date_of_birth is not ISO 8601 (YYYY-MM-DD); "
                     "age omitted from the application packet")
        return None
    if born > now:
        # rev5-003: this is a DECLARED value that cannot be used, the same
        # category as the unparseable-string case above -- not rev5-001's
        # silent blank. Silence here would mean a user who typed 2062 in place
        # of 1962 loses `age` from every packet of every run with nothing said
        # anywhere, which is the quiet-wrong-default failure this codebase
        # most consistently engineers out. Own message, distinct from the
        # not-ISO-8601 one above, so a reader (or a caplog assertion) can tell
        # which branch fired; still names only the FIELD, never either raw
        # value.
        _log.warning("candidate: date_of_birth is later than today; "
                     "age omitted from the application packet")
        return None
    return now.year - born.year - ((now.month, now.day) < (born.month, born.day))
