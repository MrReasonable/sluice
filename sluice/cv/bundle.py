# sluice/cv/bundle.py
"""Closed, verified-only CV source bundle. The composer, the checks over a selection, and
the strip step all share the short company-prefixed [id] codes assigned here. The FULL
verified set is emitted (JD keywords order/emphasise, never exclude), so every entry the
CV Layout places is available to be cited."""
import re

from sluice.core.layout import layout_text
from sluice.core.stem import stem_all as _stem_all

# The one tokeniser lives in core/tokens.py, where core/doctor.py shares it; the name stays
# importable from here for cv/terms.py.
from sluice.core.tokens import WORD_RE as _WORD_RE
from sluice.core.tokens import tool_items


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


def build_bundle(entries, negatives, jd_keywords, prefix_map, skills=()) -> dict:
    """Assemble the evidence bundle: entries ranked by the JD keywords and given citable
    codes, the negative constraints, and the skills framing. Skills are ranked but never
    code-assigned, since an `[id]` is what makes a thing citable."""
    # No `Tools:` validation here: `missing_prerequisites` refuses a malformed item before
    # any spend, and `entry_facts` raises on one regardless of the caller.
    ranked = rank(entries, jd_keywords)
    return {"entries": assign_codes(ranked, prefix_map),
            "negatives": list(negatives),
            # Ranked by the same JD keywords so the most relevant framing leads -- but NOT
            # code-assigned: an [id] is what makes a thing citable, and the whole point of
            # this section is that it is not (#165). Defaults to () so every existing
            # caller and test constructs a bundle unchanged.
            "skills": rank(list(skills), jd_keywords)}


def _entry_block(entry: dict) -> list[str]:
    """The lines ONE entry contributes to the bundle.

    The single definition of what an entry is made of, shared by the composer's entries
    section and the auditor's (`_entries_section`) and by `cv/validate.py::entry_facts`,
    which harvests this entry's permitted figures from them. Sharing it is what makes the
    prompt and the allowlist unable to disagree -- see #174.

    THE RULE, and it is narrower than it looks: every line this function returns is a
    SOURCE for that entry, and nothing else is. Not "whatever the model was shown" -- the
    guidance section is shown to the model and is deliberately not citable (#31), and an
    entry's `Tools:` line (`_tools_line`) is a separate emitter for the same reason. So a
    line added here becomes citable by that entry -- witnessed: appending a per-entry "do
    NOT claim N" caution here widens every entry's figures, and it is caught:
    `test_the_allowlist_still_matches_the_frozen_prompt` (tests/test_cv_bundle.py) goes red,
    because the caution line lands in the entry's figures but the frozen reference does not
    carry it. Presentation that must not become a source belongs in `_entries_section` or
    the renderers, not here.

    That enforcement is a RATCHET, not an impossibility, and the honest limit is this: it
    catches a widening only against the FROZEN literal. Re-capture `FROZEN_BUNDLE_TEXT`
    after widening this function -- which its own comment invites a maintainer to do --
    and the comparison moves with the mutant and stays green. The guard that does not
    compare against that literal is
    `test_entry_facts_sentinels_hold_independent_of_the_frozen_literal`, which names the
    figures an entry must and must not carry. Nothing here can tell a deliberate prompt
    change from a silent allowlist widening; a human reading the freeze diff is what still
    has to. Same shape as this repo's fixture-digest ratchet
    (`tests/test_fixture_name_neutrality.py`): a value pinned by a literal certifies
    against that literal, never against the world.

    Excludes the inter-entry blank line for the same reason: it is presentation, carries
    no digits, and `_entries_section` owns it.
    """
    lines = [f"[{entry['id']}] ({entry.get('company','')}) {entry.get('title','')} "
             f"| metrics={entry.get('metrics','')}"]
    if entry.get("body"):
        lines.append(entry["body"])
    return lines


def _framing_lines(skill: dict) -> list[str]:
    """The lines ONE skills entry contributes to the COMPOSER's prompt (#165).

    Deliberately NOT named `_skills_block`. `_entry_block` carries a stated contract --
    every line returned is a SOURCE the fabrication gate may license -- and these lines are
    the opposite of that. Nothing that LICENSES reads these lines: `entry_facts` walks
    `bundle["entries"]` and never touches `bundle["skills"]`, which is what makes a skills
    figure licensed nowhere. `term_vocabulary` (#194) does read them, to RECOGNISE a
    declared skill as not invented -- a STYLE-tier question, kept off the hard gate's facts
    so it cannot become a licence. Folding these into `_entry_block`, or teaching
    `entry_facts` to read them, licenses every skills digit at once;
    `test_a_skills_digit_is_licensed_in_neither_pool` catches that.

    Reads `fields` by the kind's own frontmatter names rather than the floor keys:
    `EVIDENCE_KINDS["skills"]` maps only `best_for <- Domain`, so Proficiency, Evidence
    and Signal Value have no floor analogue and are reachable only here.
    """
    f = skill.get("fields") or {}
    # Headed by the CV name (Label when set, else the title): the composer should see the
    # spelling the SKILLS pool offers, and the filename is a slug that destroys `C#`.
    head = f"- {f.get('Label') or skill.get('title','')}"
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


# The structured composer's claim-source constraint (#364/#365/#368). Names the entries
# alone: under #364 D2 the baseline CV is not read when composing, so naming it would point
# the model at a source it cannot see.
_TOOLS_SOURCE_PROMPT = ("in the profile and the bullets, claim no technology, language, "
                        "framework or tool that is not named in the VERIFIED EXPERIENCE "
                        "ENTRIES above")

# The section headers the composer and the auditor read. Named *PROMPT* so
# tests/test_prompt_neutrality.py sweeps them with every other shipped prompt text.
_ENTRIES_HEADER_PROMPT = ("=== VERIFIED EXPERIENCE ENTRIES (the ONLY source for the profile "
                          "and bullets; cite by id) ===")
_INVENTORY_HEADER_PROMPT = ("=== SKILLS INVENTORY (framing for the profile and bullets; a "
                            "source only for the skills list) ===")
_GUIDANCE_HEADER_PROMPT = "=== THE CANDIDATE'S GUIDANCE (follow it; it is not a source) ==="
_AUDIT_ENTRIES_HEADER_PROMPT = ("=== VERIFIED EXPERIENCE ENTRIES (the ONLY truth; cited by "
                                "id) ===")


def _tools_line(entry: dict) -> list[str]:
    """An entry's Tools:, shown beside its block. A SEPARATE emitter from `_entry_block`
    on purpose: `entry_facts` harvests figures from `_entry_block` alone, so a digit inside
    a tool name (`Examplelang9`) can never license a figure."""
    items = tool_items(entry)
    return [f"tools={', '.join(items)}"] if items else []


def _defang(lines: list[str]) -> list[str]:
    """Vault text (an entry body, an inventory field, a guidance line) is rendered
    verbatim, so a line of it beginning `===` would read as one of this prompt's own
    section headers -- a forged boundary. Prefix such a line with a quote marker. The ONE
    helper for both renderings. Presentation only: the gate reads structured entries
    (#174), never this text, so nothing it licenses changes."""
    out: list[str] = []
    for line in lines:
        for part in str(line).split("\n"):
            out.append("> " + part if part.lstrip().startswith("===") else part)
    return out


def _guidance_section(bundle: dict) -> list[str]:
    """`cv.negatives`, shown as what it now is (#364 D7): the user's free-text guidance to
    the composer. No check reads it and nothing in it is a source. Empty when there is
    none, so no bare header is emitted."""
    if not bundle["negatives"]:
        return []
    return [_GUIDANCE_HEADER_PROMPT] + _defang([f"- {n}" for n in bundle["negatives"]])


def _entries_section(bundle: dict, heading: str) -> list[str]:
    lines = [heading]
    for e in bundle["entries"]:
        lines += _defang(_entry_block(e) + _tools_line(e))
        lines.append("")
    return lines


def render_structured_bundle(bundle: dict) -> str:
    """The composer's source text: entries with their tools, the Skills Inventory as
    framing, sluice's own tools rule, the guidance. No baseline (#364 D2)."""
    lines = _entries_section(bundle, _ENTRIES_HEADER_PROMPT)
    if bundle.get("skills"):
        lines.append(_INVENTORY_HEADER_PROMPT)
        for sk in bundle["skills"]:
            lines += _defang(_framing_lines(sk))
        lines.append("")
    # Sluice's own rule, outside the guidance section: guidance is the user's and "not a
    # source", while this is a constraint the engine enforces.
    if bundle.get("skills") or any(_tools_line(e) for e in bundle["entries"]):
        lines += [_TOOLS_SOURCE_PROMPT, ""]
    return "\n".join(lines + _guidance_section(bundle))


def render_audit_bundle(bundle: dict) -> str:
    """The advisory auditor's truth: the entries WITH their tools -- the hard gate licenses
    a tool through Tools:, so the auditor must see the same evidence or every tool-naming
    bullet would read unsupported and be held (#364 spec §6.4) -- and the guidance. No baseline,
    no inventory."""
    lines = _entries_section(bundle, _AUDIT_ENTRIES_HEADER_PROMPT)
    return "\n".join(lines + _guidance_section(bundle))


def term_vocabulary(bundle: dict, layout) -> frozenset[str]:
    """What the unbundled-term check (cv/terms.py, #194) recognises: every word of the
    entries, their tools, the Skills Inventory framing and the CV Layout. It subtracts
    NOTHING -- #368: reading prose negatives as bans stripped terms the user's own evidence
    carries. A real "never claim X" belongs in cv.fabrication_decoys, a hard check."""
    lines: list[str] = []
    for e in bundle["entries"]:
        lines += _entry_block(e) + _tools_line(e)
    for s in bundle.get("skills", ()):
        lines += _framing_lines(s)
    lines.append(layout_text(layout))
    return frozenset(t.casefold() for line in lines for t in _WORD_RE.findall(line or ""))
