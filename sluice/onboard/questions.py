"""The question catalogue: pure data plus the parsers that validate an answer.

A BLANK answer is a SKIP, and a skipped question writes nothing -- the empty-config-abstains
invariant expressed at the wizard. A wizard that fills a gate because someone fumbled a prompt bins
a stranger's job hunt exactly as `672ad2a` did. The vault is the sole exception, because the
profile has to be written somewhere and "skip" is not an answer to that.

Nothing here proposes a taxonomy of good jobs. Every prior neutrality risk in this repo was a
shipped VALUE; a wizard adds a new surface, the shipped QUESTION, and "startup or enterprise?"
ships an opinion just as surely as a default would.
"""
import os
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass


class BadAnswer(ValueError):
    """An answer that cannot be used. On a TTY the asker re-asks; unreachable under `--no-input`,
    which supplies no answers at all."""


# PyYAML resolves all of these to a bool, and `bool` subclasses `int`, so an int-typed key answered
# `yes` would load as 1 with nothing raising. `lead_ttl_days: yes` is the natural way to think you
# are turning staleness ON, and it would instead declare every lead stale after one day (#75).
_BOOL_WORDS = {"y", "n", "yes", "no", "on", "off", "true", "false"}

# A SMOKE TEST's vocabulary, and named as one. It cannot be complete -- an exemplar this list does
# not contain sails past -- so it must never be treated as the neutrality guarantee, and no real
# check may be deleted on its strength. What it does catch is the cheap regression: someone
# reaching for a concrete example while writing a prompt. Held HERE, in one place, so the unit and
# e2e tiers sweep the same vocabulary rather than two copies that drift.
NO_TAXONOMY_WORDS = ("startup", "scaleup", "enterprise", "fintech", "platform", "infrastructure",
                     "senior", "junior", "staff", "principal", "manager", "engineer", "developer",
                     "remote-first", "hybrid")


def expresses_a_preference(text: str) -> list:
    """Which taxonomy words `text` names, matched on WORD BOUNDARIES.

    Boundaries, not substrings: `senior` inside `seniority` is not an exemplar, and a bare `in`
    check fails on the scaffold's own prose. Deleting the word to make that pass would shrink the
    guard rather than the problem.
    """
    low = (text or "").lower()
    return [w for w in NO_TAXONOMY_WORDS if re.search(rf"\b{re.escape(w)}\b", low)]


def parse_text(raw: str) -> str:
    return raw.strip()


def parse_csv(raw: str) -> list:
    return [s.strip() for s in raw.split(",") if s.strip()]


def parse_int(raw: str) -> int:
    text = raw.strip()
    if text.lower() in _BOOL_WORDS:
        raise BadAnswer(f"{text!r} is a yes/no word and this key takes a number. YAML loads it as "
                        f"true, which counts as 1 -- give a number, or leave it blank to skip.")
    try:
        value = int(text)
    except ValueError:
        raise BadAnswer(f"{text!r} is not a whole number.") from None
    if value < 0:
        raise BadAnswer("must not be negative (0 means the gate is off).")
    return value


def parse_path(raw: str) -> str:
    """Absolute, always. A relative `vault_dir` follows the working directory, which is how a user
    ends up with a second empty vault beside the one they meant -- and the wizard would be the
    thing that wrote it."""
    return os.path.abspath(os.path.expanduser(raw.strip()))


def parse_choice(*allowed: str) -> Callable:
    """A closure that also EXPOSES its allowed set as `.allowed`, so a test can compare it against
    the live registry. Hand-listing a name-keyed registry is a second copy of it; the exposure is
    what lets a completeness test redden when a provider is added."""
    def _parse(raw: str) -> str:
        text = raw.strip()
        if text not in allowed:
            raise BadAnswer(f"{text!r} is not one of: {', '.join(sorted(allowed))}")
        return text
    _parse.allowed = tuple(allowed)
    return _parse


def parse_url(raw: str) -> str:
    """Scheme and shape only. Deliberately NOT `core/urlguard.py`, which resolves DNS: that guard
    stops the dossier fetcher reaching a private address, and using it here would make `init`
    non-hermetic and could hang behind a slow resolver while someone is setting up."""
    text = raw.strip()
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise BadAnswer(f"{text!r} is not an http(s) URL.")
    return text


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    parse: Callable
    writes_to: tuple = ()
    section: str = ""
    hint: str = ""
    # None means "blank skips me". Only the vault question sets it.
    default: object = None
    # Printed by the post-write report when this key ends up set, so the user learns what their
    # config will DO rather than only what was written.
    consequence: str = ""


def catalogue(*, default_vault: str = "") -> tuple:
    """Every question, in ask order.

    `default_vault` is a PARAMETER: a pure catalogue must not import from `core/vault.py`, the
    concrete store, or the wizard's one non-skipping default would be pinned to the vault
    implementation even when `store:` names something else. `cli.py` supplies it. It defaults to
    `""` so callers that only need the question TUPLE -- `plan`'s renderers, which never read
    `q.default` -- do not have to thread a value they cannot observe.

    Ask order matters in one place: the coarse ingest gate is asked AFTER every gate whose
    consequence it amplifies (the provider questions come later still, but they gate nothing). It
    is the most dangerous key in the file -- a keep-list discards every title that does not match,
    before dedup and before any LLM call -- so it is asked once the user has seen what the
    downstream gates do, and its prompt states the consequence outright.
    """
    from sluice.core.app import Sluice
    backends = tuple(sorted(Sluice.available("backend")))
    renderers = tuple(sorted(Sluice.available("renderer")))
    return (
        Question("vault_dir", "Where is your Obsidian vault?", parse_path, ("vault_dir",),
                 "Vault", default=default_vault,
                 hint="Where sluice reads your judging criteria and writes lead notes.",
                 consequence="vault: {value}"),

        Question("cv_name", "What name should appear on a tailored CV?", parse_text,
                 ("cv.name",), "You"),
        Question("cv_contact", "Contact block for the CV (email, phone, links)?", parse_text,
                 ("cv.contact",), "You", hint="One line; edit the config for a multi-line block."),
        # The hint states the mechanism the code ACTUALLY implements. It previously said this
        # checked that a CV "only cites places you worked" -- a soundness check -- while
        # `cv/validate.py` runs the opposite: a case-sensitive COMPLETENESS check that every name
        # listed here appears VERBATIM in each tailored CV. Measured, `example alpha ltd` against a
        # CV saying `Example Alpha Ltd` yields MISSING EMPLOYER, which the hard gate blocks and the
        # engine retries once then skips -- so typing your employers in lower case here turns the
        # whole cv sub-app off, silently, forever. A wizard hint that inverts its key's meaning is
        # worse than no hint.
        Question("cv_employers", "Places you have worked, comma-separated?", parse_csv,
                 ("cv.employers",), "You",
                 hint="Every name here must appear VERBATIM in each tailored CV or the fabrication "
                      "gate blocks it. Match your baseline CV's spelling and case exactly, or "
                      "leave blank.",
                 consequence="require every tailored CV to name, verbatim: {value}"),

        Question("accept_titles", "Which job titles do you want, comma-separated?", parse_csv,
                 ("triage.accept_titles",), "Want",
                 hint="Blank leaves the gate open: every lead reaches the judge.",
                 consequence="accept only titles matching: {value}"),
        Question("reject_titles", "Which titles disqualify a role, comma-separated?", parse_csv,
                 ("triage.reject_titles",), "Want",
                 consequence="reject titles matching: {value}"),
        Question("target_locations", "Where are you willing to work, comma-separated?", parse_csv,
                 ("triage.target_locations",), "Want",
                 hint="Blank means no geographic gate at all.",
                 consequence="require a location matching: {value}"),
        Question("reject_companies", "Any companies to skip, comma-separated?", parse_csv,
                 ("triage.reject_companies",), "Want", consequence="always skip: {value}"),
        Question("contract_floor", "Minimum day rate for contract work?", parse_int,
                 ("triage.contract_floor_gbp_day",), "Want",
                 hint="0 or blank means no floor. The pay-floor config keys are named in GBP "
                      "(contract_floor_gbp_day), so give the number in whatever currency your "
                      "leads quote -- sluice compares numbers, not currencies.",
                 consequence="drop contract roles under {value}/day"),
        Question("perm_floor", "Minimum salary for permanent work?", parse_int,
                 ("triage.perm_floor_gbp",), "Want", hint="0 or blank means no floor.",
                 consequence="drop permanent roles under {value}"),
        Question("lead_ttl_days", "Drop leads not seen in a scrape for how many days?", parse_int,
                 ("lead_ttl_days",), "Want",
                 hint="0 or blank turns staleness off, which is the shipped default.",
                 consequence="treat leads unseen for {value} days as stale"),

        Question("relevance_keep", "Keep only titles containing these words, comma-separated?",
                 parse_csv, ("relevance_keep",), "Cost",
                 hint="A cheap filter at scrape time, BEFORE anything else runs. Anything not "
                      "matching is discarded and never judged. Leave blank unless you want that.",
                 consequence="keep ONLY titles containing: {value} "
                             "(everything else dropped before triage)"),
        Question("relevance_drop", "Discard titles containing these words?", parse_csv,
                 ("relevance_drop",), "Cost", consequence="discard titles containing: {value}"),

        Question("primary_backend", f"Primary LLM backend -- {', '.join(backends)}?",
                 parse_choice(*backends),
                 ("triage.primary_backend", "cv.primary_backend", "track.primary_backend"),
                 "Providers", hint="Set once; written into all three blocks that take one."),
        Question("fallback_backend", f"Fallback LLM backend -- {', '.join(backends)}?",
                 parse_choice(*backends),
                 ("triage.fallback_backend", "cv.fallback_backend", "track.fallback_backend"),
                 "Providers"),
        Question("renderer", f"CV renderer -- {', '.join(renderers)}?", parse_choice(*renderers),
                 ("cv.renderer",), "Providers",
                 hint="template fills a Jinja2 template. It uses the packaged default "
                      "unless cv.template names your own template file. Install support "
                      "with: pip install 'sluice[render]'."),
    )
