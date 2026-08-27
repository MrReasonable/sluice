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
    # Fail loudly at construction (#168, this module's house rule -- see SKILL_TOKEN_RE's
    # own comment). `_skill_items` is otherwise only reached lazily, from `bundle_sources`,
    # which most callers invoke well after `build_bundle` -- an entry with a malformed
    # `Skills:` value would then surface far from the note that caused it, at gate time
    # instead of at load time. Called for its validation side effect only: the returned
    # items are discarded here and re-derived (identically) by `bundle_sources` later.
    for e in entries:
        _skill_items(e)
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


# EVERY TOKEN of a `Skills:` item must begin with a letter. Span removal (cv/validate.py)
# makes this the first field that SUBTRACTS from the hard numeric gate, so an unconstrained
# value is a laundering path.
#
# PER TOKEN, and that is the whole guard: an ITEM-level check (`^[A-Za-z]` against the
# comma-separated item) accepts `Result 92`, because the item begins with `R` -- and removal
# then blanks `92` from every bullet citing the entry, which is the exact path this rule
# exists to close. A per-token rule refuses `Result 92`, `92x`, `120ms` and a bare `92`
# alike, while accepting `Example Widget3`, where the digit is INSIDE a letter-led token.
#
# It does NOT close a letter-led metric shorthand -- `p99` still licenses removing `99` for
# its own entry. That is a stated residual (spec section 14): tightening further (two
# leading alphabetic characters) would kill legitimate short names.
SKILL_TOKEN_RE = re.compile(r"^[A-Za-z]")

# The ONE tokeniser. `cv/validate.py` imports this rather than redefining it -- two copies
# let the vocabulary the gate BUILDS drift from the one it SEARCHES with.
_WORD_RE = re.compile(r"[A-Za-z0-9#+.]+")


def _skill_items(entry: dict) -> list[str]:
    """The `Skills:` items for one entry, blank-safe.

    Accepts the comma spelling AND a YAML block list: `_parse_fm_spaced` joins a block
    list to the identical comma string, so both arrive here the same way -- which is why
    a collector written for one shape alone sweeps clean over the other.

    A BLANK value yields [], and that is load-bearing: `_evidence_entries` materialises
    every declared field via `fm.get(k, "")`, so every existing note carries
    `Skills == ""` the day #168 lands. Blank is absent (SC5).
    """
    raw = (entry.get("fields") or {}).get("Skills", "")
    items = [t.strip() for t in raw.split(",") if t.strip()]
    for item in items:
        for token in _WORD_RE.findall(item):
            if not SKILL_TOKEN_RE.match(token):
                raise ValueError(
                    f"skill {item!r} is invalid: every token must begin with a letter "
                    f"({token!r} does not), or the numeric gate's span removal would "
                    "blank a real figure")
    return items


def _entry_skills_line(entry: dict) -> list[str]:
    """The lines ONE entry contributes as SKILL sources.

    Sibling of `_entry_block` and `_baseline_block`, carrying the INVERTED contract:
    every token here is a SKILL source for this entry, and NO DIGIT of it is a numeric
    source. That is why it is a separate function -- `_entry_block`'s stated rule is that
    every line it returns is harvested by `bundle_sources` into `nums`, so folding these
    in would license every digit inside every skill name at once (`Example Widget3` -> `3`).

    Deliberately not named `_skills_block`, matching `_framing_lines`' precedent for the
    same reason: the `_*_block` names in this module mean "numeric source".

    NOT part of `_entry_block`'s own emission, and not reached by `render_bundle`'s call
    to `_source_section` -- that call passes no `entry_lines` override, so it gets
    `_entry_block` alone. `render_bundle` is the #60 ADVISORY audit's corpus, and a skill
    claim resting on `Skills:` alone must read `unsupported` to the auditor -- exactly the
    D11 guarantee `render_composer_bundle` already holds for the framing section. Only
    `render_composer_bundle` folds this function's output in, by passing `_source_section`
    an `entry_lines` override that appends it to `_entry_block`'s own lines -- see
    `_source_section`'s docstring for why that is a safe default to invert.
    """
    items = _skill_items(entry)
    return [f"skills={', '.join(items)}"] if items else []


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


def _source_section(bundle: dict, entry_lines=_entry_block) -> list[str]:
    """Everything up to and including the last entry: the lines BOTH audiences see --
    unless `entry_lines` overrides what one entry contributes.

    `entry_lines` is a per-entry LINE EMITTER, defaulting to `_entry_block` -- the narrow,
    auditor-safe shape. `render_bundle` (the #60 ADVISORY audit's corpus) calls this with
    no argument, so a caller who forgets one gets the SAFE default. That is the OPPOSITE
    hazard from the one `render_composer_bundle`'s own docstring cites for rejecting a
    keyword flag on `render_bundle` itself: there, a forgetful default WIDENED what the
    auditor could see (it would get the framing section too). Here, a forgetful default
    only NARROWS what the composer would have seen, and can never leak a skill source to
    the auditor -- so a flag is safe at THIS seam even though it was rejected one level up.
    `render_composer_bundle` passes `lambda e: _entry_block(e) + _entry_skills_line(e)` to
    add its own per-entry skill line, through the ONE loop both callers already share.

    A second, near-duplicate loop was the first design here and was rejected: a line added
    to this loop later would need remembering to add to a second copy too, and nothing
    would go red if a maintainer forgot -- the exact drift class a shared loop closes.
    """
    lines = ["=== BASELINE CV (authoritative for dates/employers/certs) ==="]
    lines += _baseline_block(bundle)
    lines += ["",
              "=== VERIFIED EXPERIENCE ENTRIES (the ONLY permitted source; cite by [id]) ==="]
    for e in bundle["entries"]:
        lines += entry_lines(e)
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

    Passes its own `entry_lines` override to `_source_section` (#168) -- never calls
    `render_bundle` or duplicates its loop -- so every entry's own `_entry_skills_line`
    belongs in the composer's prompt ALONE. That inclusion is, unlike the SKILLS INVENTORY
    framing above, NOT conditional on `bundle["skills"]`: a `Skills:` field declared on an
    entry is a source in its own right, independent of whether a separate Skills Inventory
    note exists at all. Gating it on the inventory's presence would silently drop a
    candidate's own declared entry skills from the one prompt that is supposed to see
    them, whenever they had not also filed a Skills Inventory note.
    """
    lines = _source_section(bundle, entry_lines=lambda e: _entry_block(e) + _entry_skills_line(e))
    if bundle.get("skills"):
        lines += ["=== SKILLS INVENTORY (framing only; NOT citable, introduces no facts) ==="]
        for sk in bundle["skills"]:
            lines += _framing_lines(sk)
        lines.append("")
        lines += _negatives_section(bundle, extra=(_DERIVED_NEGATIVE_PROMPT,))
    else:
        lines += _negatives_section(bundle)
    return "\n".join(lines)


class EntrySources(NamedTuple):
    """What ONE bundle entry licenses. Numbers and skills travel together because they are
    keyed by the same id: two separate id-keyed dicts could disagree about what an id is,
    which is what `BundleSources.ids`' docstring argues against. Collapsed, key equality is
    structural even for the hand-built values the suite constructs -- so no `validate()`
    guard is needed, and none is added: a guard there would NARROW THE WAYS IN rather than
    remove the capability, the distinction #174's own docstring draws."""
    nums: frozenset[str]
    skills: frozenset[str]


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
    # Row 1's per-entry vocabulary. ONE id-keyed structure carrying both the numeric and the
    # skill allowlist, so the two cannot disagree about what an id is -- see EntrySources.
    entries: dict[str, EntrySources]
    baseline: frozenset[str]
    # Row 2's vocabulary: the WORDS of the bundle's source text, as one token SEQUENCE per
    # source block. A different question (did you invent this) at a different granularity
    # (bundle-wide), which `baseline` and the entries' digit sets cannot answer. Sequences
    # rather than a set, because a skill can be two words and no single token is one.
    #
    # Its NESTING needs a construction-time shape check: a flat `tuple[str, ...]` is valid
    # Python and iterates as CHARACTERS in row 2's matcher, so every skill would read
    # UNSOURCED and every lead would go `skipped-gate` -- silently, on a value that looks
    # right. Reject a member that is not a tuple/list of `str`. (Task 4 adds that guard to
    # `validate()`, the one caller that reaches row 2 -- see `EntrySources`' docstring for
    # why no guard lives on construction here either.)
    source_tokens: tuple[tuple[str, ...], ...]

    @property
    def ids(self):
        return self.entries.keys()

    @property
    def nums(self):
        """The per-entry numeric allowlists, DERIVED -- same reason `ids` is derived: a
        stored second view could disagree with `entries` about what an id licenses.
        Keeps every existing `validate()` consumer (`sluice/cv/validate.py`) working
        unchanged across #168's rename of the stored field from `nums` to `entries`, so
        plumbing the bundle stays genuinely INERT for the gate -- Task 2 changes no
        production file outside this one."""
        return {eid: es.nums for eid, es in self.entries.items()}


def bundle_sources(bundle: dict) -> BundleSources:
    """Derive the citable ids and their permitted numbers, skills and source vocabulary
    from the bundle's STRUCTURE.

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

    `source_tokens` (#168, SC4) is built in this SAME pass, from the SAME per-entry
    `items`/`body` values that fill `EntrySources.skills` -- one entry's skill vocabulary
    and its row-2 source text can never disagree about what that entry declared, because
    both come from one read of it. Kept as one token SEQUENCE per source block (entry
    skills, entry body, baseline), never flattened into a single list: row 2 searches for
    a skill's token SUBSEQUENCE, and a flat list would invent adjacencies across block
    seams that exist nowhere in the user's prose (see
    `test_source_tokens_are_per_block_so_a_two_word_skill_cannot_match_across_a_seam`).
    """
    entries: dict[str, EntrySources] = {}
    blocks: list[tuple] = []
    for e in bundle["entries"]:
        eid = e["id"]
        if eid in entries:
            # Fail loudly at construction. Naming the id and NOT the entry is deliberate:
            # the entry holds the user's own CV prose, and this message reaches a log.
            raise ValueError(f"duplicate bundle entry id {eid!r}: ids must be unique, "
                             "since each one keys its own allowlist")
        block = _entry_block(e)
        block[0] = block[0][len(eid) + 2:]   # drop the leading `[{eid}]`
        entry_nums = frozenset(re.findall(r"\d+", "\n".join(block)))
        items = _skill_items(e)
        # ONE id-keyed structure: `nums` and `skills` cannot disagree about what an id is,
        # so the key equality holds for hand-built values too and needs no guard.
        entries[eid] = EntrySources(entry_nums, frozenset(items))
        # Row 2's vocabulary, SC4: entry `Skills:` + the entry's BODY + the baseline.
        # Enumerated, never "everything _source_section contributes" -- that larger set
        # also carries the presentation headers and `_entry_block`'s head line, under
        # which an emitted `- Example Alpha` would be a licensed skill token.
        #
        # Kept as one token SEQUENCE PER BLOCK, never flattened: row 2 searches for a
        # skill's token subsequence, and a flat list would invent adjacencies across
        # block seams that exist nowhere in the user's prose.
        # TOKENISED, not stored whole: row 2 searches for a skill's token SEQUENCE, so a
        # block holding the item `"Example Query"` as ONE element can never match the needle
        # ["Example", "Query"] -- a multi-word skill declared only in `Skills:` would be
        # refused as unsourced, which is the opposite of what declaring it means.
        blocks.append(tuple(t for item in items for t in _WORD_RE.findall(item)))
        blocks.append(tuple(_WORD_RE.findall(e.get("body", ""))))
    baseline_block = _baseline_block(bundle)
    baseline = frozenset(re.findall(r"\d+", "\n".join(baseline_block)))
    blocks.append(tuple(_WORD_RE.findall("\n".join(baseline_block))))
    # `skills` and `nums` are keyed in ONE pass, so their key sets are equal by
    # construction rather than by assertion -- for values built HERE. That is not the whole
    # contract: `BundleSources` is a directly-constructible NamedTuple and the suite builds
    # it by hand, so `validate()` re-checks the key sets on entry (see Task 4). The failure
    # mode is why it is worth a guard at all: row 1 reads a missing `skills` key as an
    # abstain, so a mismatched value skips attribution checking SILENTLY.
    return BundleSources(entries, baseline, tuple(b for b in blocks if b))
