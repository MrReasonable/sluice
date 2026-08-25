# sluice/cv/bundle.py
"""Closed, verified-only CV source bundle. The composer, the validate gate, and the
strip step all share the short company-prefixed [id] codes assigned here. The FULL
verified set is emitted (JD keywords order/emphasise, never exclude) so the
employer-completeness gate is always satisfiable from cited entries."""
import re
from typing import NamedTuple

from sluice.core.stem import stem_all as _stem_all


def _prefix(company: str, prefix_map: dict) -> str:
    """Two-uppercase-letter company prefix. Coerces ANY source (a prefix_map
    override or the derived fallback) to exactly two A-Z letters so the citation
    code always matches the strip regex and can never leak into the rendered PDF."""
    raw = prefix_map.get(company) or company
    letters = re.sub(r"[^A-Za-z]", "", raw).upper()
    return (letters[:2] or "XX").ljust(2, "X")


def assign_codes(entries: list[dict], prefix_map: dict) -> list[dict]:
    """Attach a stable [id] (e.g. NC1, SF2) to each entry, sequenced per company prefix."""
    seq: dict[str, int] = {}
    out = []
    for e in entries:
        p = _prefix(e.get("company", ""), prefix_map)
        seq[p] = seq.get(p, 0) + 1
        out.append({**e, "id": f"{p}{seq[p]}"})
    return out


def rank(entries: list[dict], jd_keywords: list[str]) -> list[dict]:
    """Order entries by how many JD keywords their classification fields answer.

    Matching is on STEMS, both sides (#165), so "documenting", "documentation" and
    "documented" all rank the same entry. Before this it was raw substring containment,
    which missed every inflection -- measured on a real posting, an entry that directly
    evidenced the ad's most-emphasised requirement scored ZERO and ranked below dozens of
    unrelated ones, because the ad said "documenting" and the entry said "documentation".
    It also related words it should not: `"java" in "javascript"` is True.

    Orders, never excludes. The FULL verified set is emitted either way, so a ranking
    change can never lose evidence -- only move it. It DOES change which `[id]` an entry
    receives, since `assign_codes` runs after this.

    The haystack stays `best_for`/`category`/`title` and deliberately excludes `body`:
    matching into free prose lets a long entry out-score a precise one on volume alone.

    BOTH sides go through `_stem_all`, which tokenises before stemming. Stemming each
    keyword WHOLE (`_stem(k)`) is not the same operation: `_stem("machine learning")` is
    `"machine learn"`, a single string that no tokenised haystack can ever contain, so a
    multi-word keyword scored ZERO -- measured, the entry that answered it ranked last of
    seven while entries matching an unrelated keyword scored 1. That is the SAME
    two-vocabularies-nobody-normalised defect this function was rewritten to fix, one
    level down. Today's only production caller (`cv/engine.py:_jd_keywords`) yields single
    `[a-z]{4,}` words, for which the two spellings are provably identical, so this is
    reachability-hardening rather than a live bug fix -- but `rank` is reachable with any
    keyword list, and the two sides agreeing BY CONSTRUCTION is the property worth having.
    """
    wanted = _stem_all(" ".join(jd_keywords))

    def score(e):
        hay = f"{e.get('best_for','')} {e.get('category','')} {e.get('title','')}"
        return len(wanted & _stem_all(hay))

    return sorted(entries, key=score, reverse=True)


# The skills-shaped negative, DERIVED rather than hand-typed (#165). `cv.negatives` is a
# prose shadow of the Skills Inventory and drifts from it; this line names no skill, so it
# cannot go stale. It does NOT, on its own, stop a stale CONFIGURED negative disagreeing
# with the inventory -- `core/doctor.py`'s classify_negatives_vs_skills is what makes that
# disagreement visible.
#
# It names the TWO CLAIM sources and deliberately NOT the SKILLS INVENTORY, which is the
# whole point of the section being framing. An earlier revision listed all three, on the
# reasoning that a source omitted from the most strongly worded block of the prompt reads
# to the composer as a source it must not use. That reasoning is right for a source and
# wrong here: naming a technology IS a claim, so permitting one that appears only in the
# framing section is exactly what `compose._RULES` forbids two lines above ("never
# introduce a claim that rests on it alone: every fact in the CV must still come from the
# BASELINE CV or a VERIFIED EXPERIENCE ENTRY"). The two must agree, and
# `test_the_derived_constraint_names_the_same_claim_sources_as_the_prompt_rule` is what
# holds them together -- it reads the real `_RULES` rather than restating it.
#
# Named `_PROMPT` so tests/test_prompt_neutrality.py's discovery reaches it: that sweep
# finds `*build*prompt*` functions and PROMPT-named constants, and this is shipped,
# model-facing text. It is also listed in that file's `_KNOWN_PROMPTS`, so a rename cannot
# silently drop it from the sweep -- discovery alone has no falsifier.
#
# It is NOT stored on the bundle, and `extra` is not a convenience. `bundle["negatives"]`
# is read by BOTH renderers, and this constraint is about the COMPOSER's task; the auditor
# is not composing. It used to matter more literally still: while this string named the
# SKILLS INVENTORY, storing it on the bundle handed the ADVISORY auditor a sentence naming
# a source it cannot see -- the D11 widening arriving as prose rather than as a section,
# measured before it was fixed.
_DERIVED_NEGATIVE_PROMPT = ("claim no technology, language, framework or tool that is not "
                            "named in the BASELINE CV or the VERIFIED EXPERIENCE ENTRIES "
                            "above")


def build_bundle(entries, baseline, negatives, jd_keywords, prefix_map,
                 skills=()) -> dict:
    ranked = rank(entries, jd_keywords)
    return {"baseline": baseline, "entries": assign_codes(ranked, prefix_map),
            "negatives": list(negatives),
            # Ranked by the same JD keywords so the most relevant framing leads -- but NOT
            # code-assigned: an [id] is what makes a thing citable, and the whole point of
            # this section is that it is not (#165). Defaults to () so every existing
            # caller and test constructs a bundle unchanged.
            "skills": rank(list(skills), jd_keywords)}


def _entry_block(entry: dict) -> list[str]:
    """The lines ONE entry contributes to the rendered bundle.

    The single definition of what an entry is made of, shared by `render_bundle` (which
    joins these into the prompt) and `bundle_sources` (which harvests this entry's
    permitted numbers from them). Sharing it is what makes the prompt and the allowlist
    unable to disagree -- see #174.

    THE RULE, and it is narrower than it looks: every line this function returns is a
    SOURCE for that entry, and nothing else is. Not "whatever the model was shown" -- the
    NEGATIVE CONSTRAINTS block is shown to the model and is deliberately not citable
    (#31). So a line added here becomes citable by that entry -- witnessed: appending a
    per-entry "do NOT claim N" caution here widens every entry's allowlist, and it is
    caught: `test_the_rendered_prompt_has_not_drifted` and
    `test_the_allowlist_still_matches_the_frozen_prompt` (tests/test_cv_bundle.py) both go
    red, because the caution line lands in `FROZEN_BUNDLE_TEXT`'s co-variant comparison
    but the frozen reference does not carry it. Presentation that must not become a
    source belongs in `_source_section`/`render_composer_bundle`, not here -- and note it
    must go in the one the intended AUDIENCE reads: `render_bundle` is the auditor's.

    That enforcement is a RATCHET, not an impossibility, and the honest limit is this: it
    catches a widening only against the FROZEN literal. Re-capture `FROZEN_BUNDLE_TEXT`
    after widening this function -- which its own comment invites a maintainer to do --
    and both tests move with the mutant and stay green. Nothing here can tell a
    deliberate prompt change from a silent allowlist widening; a human reading the freeze
    diff is what still has to. Same shape as this repo's fixture-digest ratchet
    (`tests/test_fixture_name_neutrality.py`): a value pinned by a literal certifies
    against that literal, never against the world.

    Excludes the inter-entry blank line for the same reason: it is presentation, carries
    no digits, and `_source_section` owns it.
    """
    lines = [f"[{entry['id']}] ({entry.get('company','')}) {entry.get('title','')} "
             f"| metrics={entry.get('metrics','')}"]
    if entry.get("body"):
        lines.append(entry["body"])
    return lines


def _baseline_block(bundle: dict) -> list[str]:
    """The baseline CV's SOURCE lines -- no header, no blank, no slice.

    Sibling of `_entry_block`, same rule: every line returned is a source, this time for
    the PROFILE-only pool. It holds no header deliberately. An earlier draft returned the
    header too and had `bundle_sources` drop it with `block[1:]`, which has two live
    mutants: keep a second header and its future digits become citable in the one region
    with no BAD-CITATION backstop behind it (`validate.py`'s profile sweep); drop the
    header and `[1:]` eats the real baseline instead, so every baseline-sourced profile
    figure is reported INVENTED and the lead is skipped. Owning no presentation removes
    both.
    """
    return [bundle["baseline"]]


def _framing_lines(skill: dict) -> list[str]:
    """The lines ONE skills entry contributes to the COMPOSER's prompt (#165).

    Deliberately NOT named `_skills_block`. In this module `_entry_block` and
    `_baseline_block` carry a stated contract -- every line returned is a SOURCE the
    fabrication gate may license -- and these lines are the opposite of that. Nothing
    harvests from here: `bundle_sources` walks `bundle["entries"]` and never touches
    `bundle["skills"]`, which is what makes a skills figure licensed nowhere. Folding
    these into `_entry_block`, or teaching `bundle_sources` to read them, licenses every
    skills digit at once; `test_a_skills_digit_is_licensed_in_neither_pool` catches that.

    Reads `fields` by the kind's own frontmatter names rather than the floor keys:
    `EVIDENCE_KINDS["skills"]` maps only `best_for <- Domain`, so Proficiency, Evidence
    and Signal Value have no floor analogue and are reachable only here.
    """
    f = skill.get("fields") or {}
    head = f"- {skill.get('title','')}"
    for label, key in (("proficiency", "Proficiency"), ("domain", "Domain"),
                       ("signal", "Signal Value")):
        if f.get(key):
            head += f" | {label}={f[key]}"
    lines = [head]
    if f.get("Evidence"):
        lines.append(f"  {f['Evidence']}")
    if skill.get("body"):
        lines.append(f"  {skill['body']}")
    return lines


def _source_section(bundle: dict) -> list[str]:
    """Everything up to and including the last entry: the lines BOTH audiences see."""
    lines = ["=== BASELINE CV (authoritative for dates/employers/certs) ==="]
    lines += _baseline_block(bundle)
    lines += ["",
              "=== VERIFIED EXPERIENCE ENTRIES (the ONLY permitted source; cite by [id]) ==="]
    for e in bundle["entries"]:
        lines += _entry_block(e)
        lines.append("")
    return lines


def _negatives_section(bundle: dict, extra: tuple = ()) -> list[str]:
    """The NEGATIVE CONSTRAINTS block.

    `extra` is prepended and is NOT part of `bundle["negatives"]`, so a constraint meant
    for one audience cannot reach the other by riding shared state. That is not
    hypothetical: the derived skills constraint (#165) NAMES the SKILLS INVENTORY
    section, and stored on the bundle it was rendered to the advisory auditor too --
    handing it a sentence naming a source it cannot see.
    """
    return (["=== NEGATIVE CONSTRAINTS (must NOT appear) ==="]
            + [f"- {n}" for n in list(extra) + list(bundle["negatives"])])


def render_bundle(bundle: dict) -> str:
    """Render the SOURCE bundle: the prompt text the ADVISORY audit sees.

    The `[id]` codes and the `=== SECTION ===` headers used to be a parsing contract with
    `cv/validate.py`, which recovered the citable ids from this text. It no longer does
    (#174): ids and entry boundaries come from `build_bundle`'s structure via
    `bundle_sources`, so no line of user free text can mint or rebind one. The headers
    are now presentation only, and the section builders own ALL of them -- `_entry_block`
    and `_baseline_block` own only source lines.

    This function does NOT emit `bundle["skills"]`, and that omission is load-bearing
    rather than incidental -- see `render_composer_bundle` (#165, D11).

    `tests/test_cv_bundle.py::test_the_rendered_prompt_has_not_drifted` pins this
    function's exact output, because it is the prompt a live LLM call receives.
    """
    return "\n".join(_source_section(bundle) + _negatives_section(bundle))


def render_composer_bundle(bundle: dict) -> str:
    """`render_bundle` plus the framing the COMPOSER gets and the auditor must not see.

    A separate function rather than a flag on `render_bundle`. There are two consumers of
    a rendered bundle and they want opposite things: `cv/engine.py`'s compose call, and
    the #60 ADVISORY audit (via `cv/audit.py`), whose prompt opens "SOURCE BUNDLE is the
    ONLY truth". Showing the auditor the framing section would make a CV claim resting on
    a skills line alone read as SUPPORTED -- where today it is `unsupported` and, at the
    shipped `cv.require_signoff: true`, withholds the send-ready pointer until a human
    signs off. #165's D3 calls such a claim illegitimate, so widening the auditor's source
    set disarms the one layer that catches it.

    A keyword flag was the first design and was rejected twice over: its default widened
    (a caller who forgets it gets the framing), and it did not even work, because the
    derived negative NAMES the section and rode `bundle["negatives"]` into both spellings.
    A second function has no default to get wrong, and leaves the audit call site
    unedited, which is the strongest available form of "the auditor sees what it sees
    today".

    Framing goes AFTER the entries it frames and BEFORE the hard "must NOT appear" list.
    Placement is measured, not stylistic: emitted BEFORE the entries, the pre-#174 oracle
    in tests/test_cv_bundle.py folds these digits into `baseline` and disagrees with
    `bundle_sources`. Omitted ENTIRELY when the inventory is empty -- an empty header
    would assert to the model that the candidate holds no skills, a negative claim it may
    act on.
    """
    if not bundle.get("skills"):
        return render_bundle(bundle)
    framing = ["=== SKILLS INVENTORY (framing only; NOT citable, introduces no facts) ==="]
    for sk in bundle["skills"]:
        framing += _framing_lines(sk)
    framing.append("")
    return "\n".join(_source_section(bundle) + framing
                     + _negatives_section(bundle, extra=(_DERIVED_NEGATIVE_PROMPT,)))


class BundleSources(NamedTuple):
    """What the fabrication gate is allowed to treat as a source, keyed by entry id.

    Handed to `cv/validate.py` instead of the rendered bundle TEXT (#174). The gate used
    to recover this by re-parsing that text, which meant any line of user free text could
    decide what an id was: a body line shaped like an existing code REBOUND that entry's
    permitted numbers, so a fabricated figure passed AND the entry's real metric was
    reported INVENTED. Passing the derived value removes the gate's capability to be
    fooled, rather than narrowing the ways in.

    `ids` is a derived property rather than a third field. Carrying it as data would
    re-create, one level up, the exact redundancy this fixes -- two structures that can
    disagree about what an id is.
    """
    nums: dict[str, frozenset[str]]
    baseline: frozenset[str]

    @property
    def ids(self):
        return self.nums.keys()


def bundle_sources(bundle: dict) -> BundleSources:
    """Derive the citable ids and their permitted numbers from the bundle's STRUCTURE.

    Ids and entry boundaries come from `build_bundle`; the numbers come from exactly the
    lines that entry contributed to the prompt, via the shared `_entry_block`. Nothing
    here parses the rendered text, so nothing here can invent an id.

    `bundle["negatives"]` is read by NOTHING HERE -- `_negatives_section` renders it into
    both prompts, but no digit of it reaches this derivation. #31 established that exclusion by where the
    negatives happened to land in the text, which failed at zero entries -- with no ids
    the negatives fell through into the baseline pool and a do-not-say figure was
    profile-permitted (measured). It is now a property of the derivation.

    The `[{id}] ` token is sliced by LENGTH from the known id, never matched out of the
    text: `_entry_block` puts it first on line 0, and that offset-0 contract is what
    `test_the_allowlist_still_matches_the_frozen_prompt` pins.
    """
    nums: dict[str, frozenset[str]] = {}
    for e in bundle["entries"]:
        eid = e["id"]
        if eid in nums:
            # Fail loudly at construction. Naming the id and NOT the entry is deliberate:
            # the entry holds the user's own CV prose, and this message reaches a log.
            raise ValueError(f"duplicate bundle entry id {eid!r}: ids must be unique, "
                             "since each one keys its own allowlist")
        block = _entry_block(e)
        block[0] = block[0][len(eid) + 2:]   # drop the leading `[{eid}]`
        nums[eid] = frozenset(re.findall(r"\d+", "\n".join(block)))
    baseline = frozenset(re.findall(r"\d+", "\n".join(_baseline_block(bundle))))
    return BundleSources(nums, baseline)
