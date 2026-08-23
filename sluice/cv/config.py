"""CV configuration: code defaults overlaid by the `cv:` block of sluice.yaml.
Every field has a sane default so cv runs with no config file. Secrets via env."""
import os
from dataclasses import dataclass, field

from sluice.core.backends import DEFAULT_TIMEOUT
from sluice.core.config import (refuse_retired_dossier_dir,
                                refuse_wrong_container, sub_app_block)
from sluice.core.paths import config_file
from sluice.cv.slop import _PHRASES

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# Employer-code and negative-fact-check lists are entirely personal to whoever
# runs sluice, so the code ships with no entries; supply your own via the `cv:`
# block of sluice.yaml (see sluice.yaml.example).
_PREFIX_MAP: dict = {}
_NEGATIVES: list = []


@dataclass
class CvConfig:
    # NB no `name`/`contact` here (#107): both moved to the vault's Candidate
    # Profile note. load_cv_config RAISES on either rather than letting `hasattr`
    # drop it in silence -- see the guard below, same shape as baseline_rel's.
    # Employers the composer must cite and the validate() gate must see present.
    # Empty by default: with no list configured, compose.py asks the model to
    # include every employer present in the source bundle instead of a fixed
    # list, and validate.py skips the per-employer completeness check.
    employers: list = field(default_factory=list)
    # Strings the validate() gate treats as known-hallucination decoys (a HARD
    # FAIL if any appear in the composed CV). Empty by default; supply your own
    # via the `cv:` block of sluice.yaml.
    fabrication_decoys: list = field(default_factory=list)
    # Prefix used for the served/staged PDF filename: "{served_prefix}_<sha1>.pdf".
    # Must match apply.config.ApplyConfig.served_prefix so apply/cvfile.py's
    # artifact regex recognizes files this module serves.
    served_prefix: str = "CV"
    prefix_map: dict = field(default_factory=lambda: dict(_PREFIX_MAP))
    negatives: list = field(default_factory=lambda: list(_NEGATIVES))
    ttl_days: int = 7
    # Whether an `unsupported` profile audit flag WITHHOLDS the send-ready pointer until a
    # human signs off (`sluice cv signoff`), rather than auto-serving a possibly-fabricated
    # CV (#60). A safety valve, not a job preference, so it ships LIVE (True); set False to
    # restore the old auto-serve. The hard validate gate is unaffected either way.
    require_signoff: bool = True
    # Whether the model-judged VOICE check runs at all (#167). OFF by default: an
    # unconfigured install must never start spending LLM calls the moment it upgrades --
    # the company_resolve_llm precedent (sluice/triage/config.py). This does NOT make
    # #167's fix inert: the deterministic phrase matches still reach the composer's
    # retry either way, which is the issue's actual complaint.
    voice_check: bool = False
    # Whether a STYLE finding that survives the retry WITHHOLDS the send-ready pointer
    # (#167). OFF by default, and deliberately NOT riding `require_signoff`, whose True
    # default was chosen for FABRICATION. Riding it would mean a hard-clean CV containing
    # any of ~40 case-insensitive stems in PROFILE prose or a WORK bullet has
    # `tailored_cv` withheld at shipped defaults -- and a rendered CV with no pointer is
    # inert to apply/select, so "the CV still renders" understates the cost. Via the
    # source-material vector (the composer is told to reuse the bundle's wording), one
    # phrase in an Experience Library entry would hold EVERY lead composed from it.
    style_hold: bool = False
    # Phrases from slop._PHRASES this candidate legitimately uses in their own voice.
    # NB this is NOT abstain-shaped: it SUBTRACTS from a hardcoded list, so empty means
    # FULL enforcement -- the dossier_allow_hosts polarity. What makes the shipped
    # default safe is `style_hold` being off, not this list being empty.
    slop_allow: list = field(default_factory=list)
    # NB no `dossier_dir` here: #80 retired it in favour of one root `dossier_dir`,
    # because triage and cv share this cache and two keys could split it.
    # load_cv_config RAISES on it rather than letting `hasattr` drop it in silence.
    # Which renderer fills the seam. "template" is the default: it fills the user's own
    # Jinja2 template (or the packaged one) with the parsed CV and writes the PDF via
    # WeasyPrint (pip install 'job-sluice[render]'). "script" is the external shell-out to a
    # user-supplied WeasyPrint script, for full control over rendering. There is no
    # "weasyprint" renderer any more -- selecting it raises naming `template` as the
    # replacement (sluice/renderers/template.py's retired-name registration), because it
    # was a <pre>-dumping renderer that ignored the CV's structure and `template`
    # strictly supersedes it. "script"'s default render_script has never existed in this
    # repository, so no operator can be relying on it as the default -- that is why
    # `template`, not `script`, gets to be the default here.
    renderer: str = "template"
    # Not routed through paths.resolve(): like render_script below, this names a
    # workspace artefact the user is standing in (one of the deliberate cwd-relative
    # exceptions), not per-system state. Blank is load-bearing, the same shape as
    # paths.py's blank-means-derive -- a non-empty default would be truthy, short-circuit
    # TemplateRenderer's own "unset -> packaged default" check, and make the packaged
    # template unreachable while nothing goes red. A `str`, so it is invisible to
    # tests/test_sluice_neutral_defaults.py's list-keyed sweep and needs its own named
    # guard (tests/test_cv_template_config.py), exactly as `lead_layout` does for the
    # same reason.
    template: str = ""
    render_script: str = "./scripts/cv_render_v2.py"
    render_python: str = "/usr/bin/python3"
    render_home: str = "./cv-home"
    output_dir: str = "./cv-output"
    served_dir: str = "./cv-served"
    vault_cv_dir: str = "My CV/tailored"
    neutral_filename: str = "CV.pdf"
    primary_backend: str = "claude-max"
    fallback_backend: str = "deepseek"
    compose_model: str = "claude-sonnet-4-5"  # proven on the configured claude-max host's CLI (2.1.202); claude-sonnet-5 is NOT accepted there
    compose_effort: str = "max"
    cheap_model: str = "deepseek-v4-flash"
    audit_model: str = "claude-sonnet-4-5"
    # Host + claude binary path for the ClaudeMaxBackend this sub-app builds.
    # Empty host runs claude_path locally; set a host to shell out over ssh.
    compose_host: str = ""
    compose_claude_path: str = "claude"
    # Seconds one backend invocation may take before it gives up (#28). Defaults to the
    # value that was hardcoded, so making it reachable retunes nobody's runtime.
    #
    # PER INVOCATION PER LEG, and both multipliers are real. The engine composes up to
    # twice (the one gate-failure retry) and then runs the audit through the SAME backend
    # -- three invocations. Under the default `auto` role that backend is a
    # FallbackBackend, whose `complete` tries the primary and THEN the fallback when the
    # primary raises, and a timeout raises. So a lead's worst case is six times this, not
    # three: an earlier version of this comment said three and was wrong, having counted
    # the invocations but not the legs.
    #
    # It reaches both legs. Threading it into the primary alone (the first shape of this
    # knob) left the fallback pinned at the shipped default and made `--backend fallback`
    # ignore the knob entirely, with nothing logged to say so.
    #
    # Raise it if compositions degrade to the fallback mid-run -- an agent shelling over
    # ssh at `--effort max` against a large bundle is the slow case, and the swap is
    # logged at WARNING but easy to miss.
    compose_timeout: int = DEFAULT_TIMEOUT


def load_cv_config(path: str | None = None) -> CvConfig:
    cfg = CvConfig()
    path = path or config_file()
    if not (path and os.path.exists(path) and yaml is not None):
        return cfg
    with open(path, encoding="utf-8") as f:
        data = sub_app_block("cv", (yaml.safe_load(f) or {}).get("cv"))

    # baseline_rel MOVED to the root config (only the store can honour it). This loader
    # filters unknown keys with `hasattr`, so an existing `cv.baseline_rel` would be
    # dropped in silence -- and it was LIVE before this move, so a user with a curated
    # baseline would quietly get a CV composed from a stale `My CV/CV.md` instead, with
    # the fabrication gate green (the gate checks bullets against cited entries; it does
    # not check the baseline's dates and employers). Fail loudly at construction, which is
    # this codebase's rule precisely because a quiet wrong default is the bug class it
    # most consistently engineers out.
    refuse_retired_dossier_dir("cv", data)

    if "baseline_rel" in data:
        raise ValueError(
            "cv.baseline_rel has moved to the top level of sluice.yaml (it is a STORE "
            "location, and only the store can honour it). Move it out of the `cv:` block:\n"
            "    baseline_rel: " + str(data["baseline_rel"])
        )

    # cv.name/cv.contact MOVED to the vault (#107): the candidate's identity is now
    # read from Job Applications/Candidate Profile.md, once per lead, so it can be
    # edited without a config change. Keyed on `in data`, NOT on the value being
    # truthy -- a `cv.name: ""` left behind by a half-finished migration must be as
    # loud as a populated one, and a truthy check would silently accept the empty
    # spelling while still dropping it (the setattr loop below is hasattr-filtered
    # and has no field left to catch it on).
    #
    # DELIBERATELY the opposite spelling of the render_script/renderer guard just
    # below, which checks `.get(...) is not None` rather than `in data` -- do not
    # "fix" the two into matching each other. That guard wants a VALUELESS key to
    # read as ABSENT (an ordinary half-edited line the guard exists to wave
    # through); this one wants a valueless `cv.name:` (PyYAML resolves it to None)
    # to read as PRESENT, because it is exactly the shape a half-finished migration
    # leaves behind -- the value deleted, the key not. `in data` is True on `{"name":
    # None}` the same as on `{"name": "Ada"}`, which is what makes this raise on
    # both; see test_a_valueless_legacy_cv_name_still_raises.
    #
    # Both keys collected before raising, not "raise on the first hit": a config
    # carrying BOTH cv.name and cv.contact would otherwise name only cv.name, the
    # operator fixes that one, reruns, and hits cv.contact next -- two loud raises
    # instead of one. See test_both_legacy_keys_together_are_named_in_one_message.
    moved_present = [k for k in ("name", "contact") if k in data]
    if moved_present:
        keys = ", ".join(f"cv.{k}" for k in moved_present)
        # The path comes from the constant, not a literal: this message is the entire
        # migration instruction a user gets, so a hardcoded copy left behind by a move
        # would send them to a file that does not exist, with nothing red. Imported
        # here rather than at module scope to keep the config loader's import surface
        # as it is -- `protocols` is pure, but this is the only site in the file that
        # needs it. (Same fix as `cli.py`'s `skipped-config` message; CodeRabbit found
        # this sibling after that one, which is the reminder that closing a class for
        # one instance does not close it for the others.)
        from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH

        raise ValueError(
            f"{keys} {'has' if len(moved_present) == 1 else 'have'} moved to the "
            f"vault. sluice now reads your identity from '{CANDIDATE_PROFILE_RELPATH}' "
            f"(frontmatter keys: forenames, surname, email, "
            f"mobile, linkedin). Remove {keys} from the `cv:` block and put the "
            f"value{'s' if len(moved_present) > 1 else ''} in that note."
        )

    # A user who set render_script and NOTHING else was relying on the `script` default,
    # which this release changes to `template`. That is the one case where the new
    # default could silently change an operator's output, so refuse rather than guess --
    # inferring `renderer: script` from the presence of render_script would be an
    # implicit coupling between two keys, which is its own quiet wrong default.
    # `.get(...) is not None`, NOT `in data`, on BOTH sides -- and the two sides fail in
    # OPPOSITE directions if only one is fixed. `render_script:` with nothing after it
    # parses as None, and a half-edited or commented-out value is an ordinary thing to
    # leave in a config file; membership alone raised on a file that sets NOTHING, which
    # is the state this guard exists to wave through. But `"renderer" not in data` has
    # the MIRROR bug: `renderer:` (present, YAML null) makes the key membership check
    # False, so a user who wrote `render_script: mine.py` alongside a blanked-out
    # `renderer:` slipped PAST this guard entirely -- measured 2026-08-06, `load_cv_config`
    # returned `renderer="template"` with their `render_script` silently unused, the exact
    # quiet-renderer-switch this guard exists to refuse. `data.get("renderer") is None`
    # closes it: a null value is now treated the same as an absent key, on both operands.
    # The setattr loop below skips None the same way, so a valueless key loads exactly
    # like an absent one either side of this check. Same reasoning, and the same
    # spelling, as the `compose_timeout` validator below; the loader's contract is pinned
    # by test_a_valueless_key_never_becomes_None.
    if data.get("render_script") is not None and data.get("renderer") is None:
        raise ValueError(
            "cv.render_script is set but cv.renderer is not, and the default renderer is "
            "now `template` (it was `script`). Add `cv.renderer: script` to keep using "
            "your render script, or drop cv.render_script to use a Jinja2 template.")

    # bool BEFORE int, and separately: `bool` subclasses `int`, and PyYAML resolves
    # yes/on/true to True. `compose_timeout: yes` would otherwise pass an isinstance(int)
    # check as the value 1, giving every composition a ONE SECOND budget -- every lead
    # times out, silently degrades to the fallback, and nothing names the cause. Same
    # trap as `lead_ttl_days` at the root, and the same ordering closes it.
    #
    # No abstain value here, unlike lead_ttl_days: 0 does not mean "off", it means every
    # call dies instantly, so non-positive is refused rather than treated as a feature
    # switch. Validated ahead of the setattr loop because that loop is `hasattr`-filtered
    # and must not be taught to name its own fields.
    # `is not None`, NOT `in data`: `compose_timeout:` with nothing after it parses as
    # None, and a half-edited or commented-out value is an ordinary thing to leave in a
    # config file. The setattr loop below already skips None, so validating membership
    # instead would REJECT a file the loader is contractually required to accept --
    # caught by test_a_valueless_key_never_becomes_None, which writes every field
    # valueless and asserts the loader still returns defaults.
    if data.get("compose_timeout") is not None:
        raw = data["compose_timeout"]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            raise ValueError(
                f"cv.compose_timeout must be a positive integer (seconds), got {raw!r}")

    # cv.slop_allow SUBTRACTS from slop._PHRASES (#167) -- unlike every other
    # list-typed key in this loader, an entry that names no real phrase is not merely
    # inert, it is SILENTLY inert: the style hold it was meant to suppress just recurs
    # forever, with no error anywhere pointing at the typo. _PHRASES holds STEMS
    # ("leverage", "spearhead"), and a candidate naturally writes the INFLECTION they
    # actually typed ("leveraged", "spearheaded") -- exactly the entry most likely to
    # slip past a silent check. Fail loudly at construction and name the valid stems,
    # this repo's rule 8 for an unknown name. Every offender collected before raising,
    # not "raise on the first hit", same discipline as the cv.name/cv.contact guard
    # above.
    if data.get("slop_allow") is not None:
        # The TYPE first, because this guard's whole posture is fail-loudly-with-a-name
        # and both non-list spellings defeated it. `slop_allow: 5` raised a bare
        # `TypeError: 'int' object is not iterable` from inside the comprehension --
        # no key named, nothing pointing at the config file. Worse, `slop_allow:
        # leverage`, the natural scalar spelling of a VALID stem, iterated the string
        # PER CHARACTER and raised naming 'l', 'e', 'v', ... which reads as a bug in
        # sluice rather than a YAML mistake. Both are fixed by refusing the shape before
        # iterating it. (The loader's generic list handling has the same hole for every
        # other list key; that is #176, deliberately not half-fixed here -- this guard is
        # patched because it is THIS field's own fail-loudly contract that was broken.)
        raw = data["slop_allow"]
        if not isinstance(raw, list) or any(not isinstance(p, str) for p in raw):
            raise ValueError(
                f"cv.slop_allow must be a YAML list of strings, but got a "
                f"{type(raw).__name__}. Write it as `slop_allow: [leverage]`, not "
                "`slop_allow: leverage`.")
        # Case-INSENSITIVE membership. Every stem in _PHRASES is lower-case, and a user
        # writing `slop_allow: [Leverage]` means the stem, not a different one -- raising
        # "not in slop._PHRASES" over a capital letter is a papercut with no upside.
        # It also makes true a claim `check_phrases` already relies on: its docstring says
        # the config entry's casing and the text's casing are independent, which is why it
        # lower-cases both sides of the `allow` comparison. Under a case-SENSITIVE check
        # here that was false -- no mixed-case entry could reach it, so the defensive
        # normalisation was unreachable and the stated reason for it was wrong.
        unknown = [p for p in raw if p.lower() not in _PHRASES]
        if unknown:
            raise ValueError(
                f"cv.slop_allow names {', '.join(repr(p) for p in unknown)}, not in "
                "slop._PHRASES. slop_allow holds STEMS, not inflections -- e.g. "
                "'leverage', not 'leveraged'. Valid stems: "
                + ", ".join(repr(p) for p in _PHRASES))

    for k, v in data.items():
        if not hasattr(cfg, k) or v is None:
            continue
        # A field whose CODE DEFAULT is a bool must be given a real YAML boolean.
        # Ported verbatim from triage/config.py's identical guard (#167 review): this
        # loader had never had it for ANY bool field, not even the long-standing
        # require_signoff, and the hazard is the same one that check exists for --
        # a QUOTED `voice_check: "false"` is not a YAML boolean at all, it stays the
        # STRING "false", which this loop would setattr verbatim and every consumer
        # reads in a boolean context, where a non-empty string is TRUE. So the one
        # spelling a user reaches for to keep a knob OFF is the spelling that
        # silently switches it ON, with nothing anywhere going red. Fail loudly at
        # construction instead, this file's house style.
        #
        # Keyed on the default's type rather than on a hardcoded field list
        # (`voice_check`, `style_hold`, `require_signoff` today) so a bool knob added
        # later cannot quietly opt out of the check. `getattr(cfg, k)` is still the
        # code default here: a YAML mapping yields each key once, so no earlier
        # iteration of this loop has replaced it.
        if isinstance(getattr(cfg, k), bool) and not isinstance(v, bool):
            raise ValueError(
                f"cv.{k} must be a YAML boolean (true/false), got {v!r}. Quoted, it "
                f'is a STRING -- and "false" is truthy in Python, so the knob would '
                f"be switched ON by the value meant to switch it off.")
        # #176, the container sibling of the bool guard above and keyed the same way.
        # `employers`, `fabrication_decoys` and `negatives` feed the FABRICATION GATE,
        # which iterates them: measured before this existed, `fabrication_decoys: Acme`
        # made `validate()` return `FABRICATED: contains 'A'`, `'c'`, `'m'` and
        # hard-block every CV, accusing the model and naming neither the config key nor
        # the word "list". `slop_allow` keeps its own bespoke check above, which is
        # narrower than this one (it also validates MEMBERSHIP against the stem list).
        refuse_wrong_container("cv", k, v, getattr(cfg, k))
        setattr(cfg, k, v)
    return cfg
