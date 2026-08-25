# sluice/cv/bundle.py
"""Closed, verified-only CV source bundle. The composer, the validate gate, and the
strip step all share the short company-prefixed [id] codes assigned here. The FULL
verified set is emitted (JD keywords order/emphasise, never exclude) so the
employer-completeness gate is always satisfiable from cited entries."""
import re
from typing import NamedTuple

from sluice.core.stem import stem as _stem
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
    """
    wanted = {_stem(k) for k in jd_keywords}

    def score(e):
        hay = f"{e.get('best_for','')} {e.get('category','')} {e.get('title','')}"
        return len(wanted & _stem_all(hay))

    return sorted(entries, key=score, reverse=True)


def build_bundle(entries, baseline, negatives, jd_keywords, prefix_map) -> dict:
    ranked = rank(entries, jd_keywords)
    return {"baseline": baseline, "entries": assign_codes(ranked, prefix_map),
            "negatives": list(negatives)}


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
    source belongs in `render_bundle`, not here.

    That enforcement is a RATCHET, not an impossibility, and the honest limit is this: it
    catches a widening only against the FROZEN literal. Re-capture `FROZEN_BUNDLE_TEXT`
    after widening this function -- which its own comment invites a maintainer to do --
    and both tests move with the mutant and stay green. Nothing here can tell a
    deliberate prompt change from a silent allowlist widening; a human reading the freeze
    diff is what still has to. Same shape as this repo's fixture-digest ratchet
    (`tests/test_fixture_name_neutrality.py`): a value pinned by a literal certifies
    against that literal, never against the world.

    Excludes the inter-entry blank line for the same reason: it is presentation, carries
    no digits, and `render_bundle` owns it.
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


def render_bundle(bundle: dict) -> str:
    """Render the bundle as the prompt text the model actually sees.

    The `[id]` codes and the `=== SECTION ===` headers used to be a parsing contract with
    `cv/validate.py`, which recovered the citable ids from this text. It no longer does
    (#174): ids and entry boundaries come from `build_bundle`'s structure via
    `bundle_sources`, so no line of user free text can mint or rebind one. The headers
    are now presentation only, and this function owns ALL of them -- the two builders
    above own only source lines.

    `tests/test_cv_bundle.py::test_the_rendered_prompt_has_not_drifted` pins this
    function's exact output, because it is the prompt two live LLM calls receive.
    """
    lines = ["=== BASELINE CV (authoritative for dates/employers/certs) ==="]
    lines += _baseline_block(bundle)
    lines += ["",
              "=== VERIFIED EXPERIENCE ENTRIES (the ONLY permitted source; cite by [id]) ==="]
    for e in bundle["entries"]:
        lines += _entry_block(e)
        lines.append("")
    lines += ["=== NEGATIVE CONSTRAINTS (must NOT appear) ==="]
    lines += [f"- {n}" for n in bundle["negatives"]]
    return "\n".join(lines)


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

    `bundle["negatives"]` is read by NOTHING. #31 established that exclusion by where the
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
