"""CV configuration: code defaults overlaid by the `cv:` block of sluice.yaml.
Every field has a sane default so cv runs with no config file. Secrets via env."""
import os
from dataclasses import dataclass, field

from sluice.core.backends import DEFAULT_TIMEOUT
from sluice.core.config import refuse_retired_dossier_dir
from sluice.core.paths import config_file

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
    name: str = "Your Name"
    # Contact block inserted verbatim into the composed CV (phone/email/web lines,
    # whatever format you want the renderer to see). Entirely personal, so the
    # code ships with no contact info; supply your own via the `cv:` block of
    # sluice.yaml (see sluice.yaml.example).
    contact: str = ""
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
    # NB no `dossier_dir` here: #80 retired it in favour of one root `dossier_dir`,
    # because triage and cv share this cache and two keys could split it.
    # load_cv_config RAISES on it rather than letting `hasattr` drop it in silence.
    # Which renderer fills the seam. "template" is the default: it fills the user's own
    # Jinja2 template (or the packaged one) with the parsed CV and writes the PDF via
    # WeasyPrint (pip install 'sluice[render]'). "script" is the external shell-out to a
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
        data = (yaml.safe_load(f) or {}).get("cv") or {}

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

    for k, v in data.items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    return cfg
