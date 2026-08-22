"""One neutrality sweep over EVERY shipped prompt, rendered rather than read as source.

The per-prompt guards this joins are static by necessity and were going stale one at a
time:

  * `tests/test_cv_compose.py::test_cv_prompt_expresses_no_role_or_culture_preference`
    reads `compose._RULES`, which used to spell out seven banned inflections. #167 moved
    the ban list into a `{banned_phrases}` placeholder rendered from `slop._PHRASES`, so
    that guard's coverage of the list went from seven terms to ZERO while the list itself
    grew roughly six-fold. `_PHRASES` had no neutrality guard of its own: a hand-edited
    entry that happened to name a role or a culture would have rendered into a shipped
    prompt with the whole suite green.
  * `sluice/cv/voice.py` had no neutrality guard at all.

So this sweeps the RENDERED text, and it sweeps every prompt builder rather than the one
that prompted the finding -- a fourth prompt is covered the day it is added, with no
roster to remember to update. Caller data cannot leak in and trip it: every builder is
called with FIXED synthetic arguments (see `_SYNTHETIC` / `_SYNTHETIC_ARGS`), never a
real JD, bundle, company or role. That is the same reason the CV guard gives for staying
static, honoured here by construction instead -- a real JD may legitimately say
"startup"; a synthetic one never does.

This is a RATCHET, not a classifier. Nothing local can decide whether a new phrase is a
preference; a term landing in this list forces a human to look. It went green on the day
it was written with no change to any prompt or to `_PHRASES`.
"""
import ast
import importlib
import inspect
import re
from pathlib import Path

from sluice.cv.slop import _PHRASES

_SLUICE = Path(__file__).resolve().parent.parent / "sluice"

# The shipped vocabulary a prompt must not name. Union of the two per-prompt guards'
# lists (tests/test_prompt.py's judge guard and tests/test_cv_compose.py's CV guard).
#
# Nothing is subtracted from this tuple. An earlier revision dropped "remote-first"
# globally so that triage/prompt.py's few-shot would pass, which is the wrong shape twice
# over: it disabled the term for EVERY prompt including the CV builders that have nothing
# to do with the exemption, and it buried the carve-out in a comment where no test could
# see it go stale. Exemptions are per (prompt, term) in `_EXEMPT` below, and each one is
# asserted to still be NECESSARY, so an exemption that stops being needed reddens the
# build instead of quietly widening the blind spot forever.
_FORBIDDEN = (
    # company type / industry
    "startup", "enterprise", "faang", "unicorn", "well-funded",
    # work style / location
    "fast-paced", "onsite", "relocation", "remote-first",
    # compensation, including the CONTRACT-BASIS and RATE-SHAPED wording the earlier
    # list missed entirely: it named "salary"/"equity"/"compensation"/"six-figure" and
    # nothing else, so a prompt could state a day rate and a contract basis -- the two
    # figures a contractor's preferences actually turn on -- and sweep clean.
    "salary", "equity", "compensation", "six-figure",
    "per day", "day rate", "per annum", "outside ir35", "inside ir35",
    # role shapes (target and anti-target)
    "engineering manager", "software engineering manager", "development manager",
    "team lead", "tech lead", "technical lead", "scrum master", "agile coach",
    "head of engineering", "vp engineering", "manager-of-managers",
    # a specific culture rubric, and hype
    "transformation-shaped", "dora", "kanban", "wip limits", "retros are sacred",
    "rockstar", "ninja",
)

# A bare figure attached to rate wording, which no substring in `_FORBIDDEN` can express.
_RATE_FIGURE = re.compile(r"\d[\d,.]*\s*(?:per day|/\s*day|a day|per annum|pa\b)", re.I)

# (prompt qualname, term) pairs this sweep does not apply, each with the reason.
#
# All of these are one prompt: triage/prompt.py's `_SCAFFOLD_TAIL` few-shot block, which
# reaches the model through both `SYSTEM_PROMPT` and the builder that renders it. The
# block quotes a recruiter-shaped JD as EXAMPLE INPUT for the judge to reason ABOUT, and
# its paired example output turns on the recruiter hiding the client while explicitly
# deferring the money to the user's own profile ("workable against the profile") rather
# than asserting any figure is good. That is the distinction this map encodes: a term
# inside quoted example INPUT is not the repo holding an opinion.
#
# The exemption is per-term and per-prompt on purpose. Every one of these terms stays
# live for all nine other shipped prompts, which is exactly the coverage the old global
# subtraction threw away.
_EXEMPT = {
    ("sluice.triage.prompt.SYSTEM_PROMPT", t): "few-shot quotes a JD-shaped line"
    for t in ("remote-first", "per day", "outside ir35", "rate-figure")
}
_EXEMPT.update({
    ("sluice.triage.prompt.build_system_prompt_from", t): "same few-shot, via the builder"
    for t in ("remote-first", "per day", "outside ir35", "rate-figure")
})

# What every REQUIRED parameter of a discovered builder is called with. Fixed and
# synthetic, so no caller data is in scope for the sweep.
_SYNTHETIC = "SYNTHETIC {}"

# Builders whose required parameters are not plain text. Everything else takes strings
# and needs no entry here -- which is what keeps a fourth string-taking prompt (the
# shape all four of cv/ and triage/'s builders share) covered with no edit to this file.
# A builder that takes something else fails LOUDLY in `_render` rather than silently
# dropping out of the sweep.
_SYNTHETIC_ARGS = {
    "sluice.track.classify.build_prompt": {
        "msg": {"headers": {"from": "sender@example.invalid",
                            "subject": "SYNTHETIC subject"},
                "attachments": [], "body_text": "SYNTHETIC body"},
        "leads": (),
        "cfg": None,
    },
    # Both of these reach the model and neither was swept before the discovery predicate
    # stopped keying on a bare "build" prefix.
    "sluice.triage.judge._build_prompt": {
        "batch": [{"lead_id": "SYNTHETIC", "company": "SYNTHETIC", "role": "SYNTHETIC"}],
        "system_prompt": "SYNTHETIC system_prompt",
    },
    # Every evidence field blank makes this return None by design ("tier 3 must never
    # spend a backend call reasoning over nothing"), and a None would sweep as no text at
    # all -- the silent-empty shape this file exists to close. One field is populated so
    # the real prompt body is what gets swept.
    "sluice.triage.resolve._build_resolve_prompt": {
        "dossier": {"page_title": "SYNTHETIC page_title",
                    "structured_data": "",
                    "jd": {"markdown": "SYNTHETIC jd"}},
    },
}

# A floor, not a roster: every prompt known when this was written must still be swept, so
# deleting or renaming one reddens here rather than silently shrinking the sweep. A
# SUBSET check on purpose -- a NEW prompt is picked up and swept automatically, and must
# not have to be listed here first.
_KNOWN_PROMPTS = frozenset({
    "sluice.cv.compose.build_prompt",
    "sluice.cv.audit.build_audit_prompt",
    "sluice.cv.voice.build_voice_prompt",
    "sluice.triage.prompt.build_system_prompt_from",
    "sluice.triage.prompt.SYSTEM_PROMPT",
    "sluice.triage.judge._build_prompt",
    "sluice.triage.resolve._build_resolve_prompt",
    "sluice.triage.resolve._RESOLVE_PROMPT_HEAD",
    "sluice.triage.resolve._RESOLVE_PROMPT_TAIL",
    "sluice.track.classify.build_prompt",
    "sluice.core.doctor.PROBE_PROMPT",
    "sluice.onboard.ask._CANDIDATE_PROMPTS",
    "sluice.onboard.plan._PROFILE_PROMPTS",
})


def _prompt_symbols_in(path):
    """(builder names, constant names) declared at MODULE level in one source file.

    AST, and NOT `pkgutil.walk_packages`, for the reason
    tests/test_sluice_neutral_defaults.py's own discovery states: walking the package
    would import the ingest source modules that drive Camofox. Reading the source first
    means only the handful of modules that actually declare a prompt are ever imported.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    funcs, consts = [], []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            # `lstrip("_")`: an underscore prefix is a visibility convention, not a
            # statement about whether the text reaches a model. Keying on a bare
            # `startswith("build")` silently dropped two shipped builders --
            # `triage/judge.py::_build_prompt` and `triage/resolve.py::_build_resolve_prompt`
            # -- each contributing literal prompt text no other guard covers, while this
            # file's docstring claimed it swept EVERY shipped prompt. `_KNOWN_PROMPTS`
            # could not catch that: it is a floor over names discovery already finds, so
            # a builder discovery never reaches is invisible to it too.
            if node.name.lstrip("_").startswith("build") and "prompt" in node.name:
                funcs.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            consts += [t.id for t in targets
                       if isinstance(t, ast.Name) and "PROMPT" in t.id]
    return funcs, consts


def _strings_in(value):
    """Every string reachable in a prompt constant, flattening the containers the
    onboarding prompts use (a tuple of (key, question) pairs; a dict of heading ->
    (key, question)). Without this a container-valued constant would be swept as
    nothing at all -- silently, which is the shape this whole file exists to close."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings_in(v)]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [s for v in value for s in _strings_in(v)]
    return []


def _render(func, qualname):
    """`func` called with a fixed synthetic value for each REQUIRED parameter; optional
    ones keep their shipped defaults (which is what renders the FULL ban list, since
    compose's `slop_allow` defaults to None)."""
    overrides = _SYNTHETIC_ARGS.get(qualname, {})
    args, kwargs = [], {}
    for name, p in inspect.signature(func).parameters.items():
        if p.default is not inspect.Parameter.empty or p.kind in (
                p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        value = overrides.get(name, _SYNTHETIC.format(name))
        if p.kind is p.POSITIONAL_ONLY:
            args.append(value)
        else:
            kwargs[name] = value
    try:
        return func(*args, **kwargs)
    except Exception as exc:                                  # pragma: no cover - guard
        raise AssertionError(
            f"{qualname} could not be rendered with synthetic arguments ({exc!r}). It is "
            "a shipped prompt, so it must be swept: give it an _SYNTHETIC_ARGS entry "
            "rather than letting it drop out of the sweep.") from exc


def _discover_prompts():
    """{qualified name: the text that reaches the model (or the user)} for every prompt
    builder and prompt constant declared under sluice/."""
    found = {}
    for path in sorted(_SLUICE.rglob("*.py")):
        funcs, consts = _prompt_symbols_in(path)
        if not (funcs or consts):
            continue
        parts = path.relative_to(_SLUICE.parent).with_suffix("").parts
        dotted = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        module = importlib.import_module(dotted)
        for name in funcs:
            found[f"{dotted}.{name}"] = _render(getattr(module, name), f"{dotted}.{name}")
        for name in consts:
            found[f"{dotted}.{name}"] = "\n".join(_strings_in(getattr(module, name)))
    return found


def test_no_shipped_prompt_names_a_job_or_culture_preference():
    """The privacy invariant, over EVERY prompt this repo ships, rendered.

    A candidate's target roles, anti-targets, pay floor and culture preferences belong in
    the vault Judging Profile, never in code. This fails the moment one is baked into a
    prompt -- including through `slop._PHRASES`, which reaches the CV prompt only by
    interpolation and which no static source read can see.
    """
    leaked = {}
    for name, text in _discover_prompts().items():
        hits = [t for t in _FORBIDDEN
                if t in text.lower() and (name, t) not in _EXEMPT]
        if _RATE_FIGURE.search(text) and (name, "rate-figure") not in _EXEMPT:
            hits.append("rate-figure")
        if hits:
            leaked[name] = hits
    assert leaked == {}, (
        "a shipped prompt names a job or culture preference: "
        f"{leaked}. Those are personal and belong in the vault Judging Profile. "
        "If it is quoted EXAMPLE INPUT rather than the repo's own opinion, add the "
        "(prompt, term) pair to _EXEMPT with its reason -- never widen _FORBIDDEN, "
        "which would drop the term for every other prompt too.")


def test_every_exemption_is_still_necessary():
    """`_EXEMPT` is a ratchet, so a stale entry must fail rather than accumulate.

    An exemption that no longer corresponds to real text is a hole held open for a term
    that could return tomorrow with nothing to catch it -- the same failure as the global
    subtraction this map replaced, just smaller and harder to see.
    """
    found = _discover_prompts()
    stale = []
    for (name, term), _reason in _EXEMPT.items():
        text = found.get(name)
        if text is None:
            stale.append((name, term, "prompt no longer exists"))
            continue
        present = (_RATE_FIGURE.search(text) if term == "rate-figure"
                   else term in text.lower())
        if not present:
            stale.append((name, term, "term no longer present"))
    assert stale == [], (
        f"these _EXEMPT entries are no longer needed and must be deleted: {stale}")


def test_the_sweep_reaches_every_prompt_it_was_written_against():
    # A sweep that discovers nothing passes exactly like a sweep that finds nothing
    # wrong. Pin the floor: every prompt present when this was written is still found,
    # and none of them rendered empty.
    found = _discover_prompts()
    assert _KNOWN_PROMPTS <= set(found), (
        "the prompt sweep stopped reaching: "
        f"{sorted(_KNOWN_PROMPTS - set(found))}")
    assert [n for n, text in found.items() if not text.strip()] == []


def test_the_swept_cv_prompt_carries_the_whole_enforced_ban_list():
    # The coverage claim, made executable. `_PHRASES` reaches the CV prompt ONLY through
    # `{banned_phrases}`, so if that interpolation ever broke, the sweep above would
    # still pass -- over text that no longer contains the ~40 stems it is there to cover.
    # Checked against the imported list, never a hand-copied one.
    rendered = _discover_prompts()["sluice.cv.compose.build_prompt"]
    missing = [p for p in _PHRASES if p not in rendered]
    assert missing == [], (
        "these enforced phrases never reach the rendered CV prompt, so the neutrality "
        f"sweep above does not actually cover them: {missing}")
