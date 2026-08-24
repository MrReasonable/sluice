"""`job-sluice init`'s evidence capture steps.

The asker is INJECTED here, never imported from sluice/onboard/: this module itself
imports nothing from onboard. `commands.py`, its sibling in this package, is NOT held to
the same rule -- its `verify` handler imports `sluice.onboard.ask` directly, lazily, for
its interactive review prompt (see `sluice/evidence/__init__.py` for why that is a
deliberate cross-import, not a boundary violation).

Everything captured here lands in `_inbox/`, unverified. The wizard gets no special
power -- a fresh install's corpus is inert until the user runs `verify`, and the copy
below says so, because an inert corpus that looks captured is the failure mode this
design accepts in exchange for a single trust root.

Prompt copy states no preference and offers no exemplar: naming a technology, a
seniority or a proficiency scale here would ship an opinion about what a good
candidate looks like.

Every prompt shown to a user is a MODULE-LEVEL constant, not an in-body f-string
(Task 8 review, FIX 3). `tests/onboard_prose.py`'s own docstring records the shape of
the bug an in-body literal creates: a taxonomy word planted inside a function body
was invisible to that sweep and shipped green for three review rounds before three
separate reviewers caught it by hand. Task 11 widens that sweep's module discovery to
reach `sluice/evidence/`; hoisting every string here now is what makes this module
actually covered by it rather than only appearing to be -- hoisting `_INTRO` alone would
have left every other prompt below invisible to it. (No count here: this used to say "the
other five strings" and there were more than five, the same stale-number-in-prose class
#164's review found three further instances of. `tests/onboard_prose.py` enumerates them,
which is where a total belongs -- and it derives its own, so it cannot go stale.)

`commands.py`'s own user-facing messages stay in-body f-strings and are swept a different
way -- WHERE THEY RUN, by `test_no_command_message_names_a_taxonomy_word`, since CLI status
output interpolates values no roster of constants could see.
"""
from sluice.core.protocols import EVIDENCE_KINDS

# "unless", not "until": "until" states that review is SUFFICIENT to make an entry
# usable by the gate, and for `skills`/`stories` it is not -- `cv/engine.py` reads
# `experience` alone until #165 (#164 review, M2, the same over-claim as doctor's and
# `add`'s). "unless" states the necessary condition, which is true of every kind and
# stays true when #165 lands.
_INTRO = ("These are long-tail corpora meant to grow -- capture a handful now and add "
          "more any time with `job-sluice {kind} add`. Nothing here is used by the CV "
          "gate unless you review it with `job-sluice {kind} verify`.")

_CAPTURE_PROMPT = "Capture some {kind} entries now? [y/N] "
_NAME_PROMPT = "{kind} entry name (blank to stop): "
_FIELD_PROMPT = "  {field}: "

# One body prompt per kind is looked up here, never branched on in the loop (Task 8
# review, FIX 4): every kind gets an optional free-text body (blank = none, `add_evidence`'s
# own default), because the registry's OWN comment on `stories` (core/protocols.py) says
# Situation/Task/Action/Result live in the body -- and a wizard that captured no body at
# all would let a user create, and then `verify` as citable, a STAR story containing no
# story. `stories` gets a prompt naming those four headings because that is the schema the
# registry already documents; nothing here prescribes content beyond the headings or
# offers an example, for the same no-exemplar reason `_INTRO` states above.
_BODY_PROMPT_DEFAULT = "  body -- free text, blank to skip: "
_BODY_PROMPT_BY_KIND = {
    "stories": "  body -- Situation, Task, Action, Result, as free text, blank to skip: ",
}

_ADD_ANOTHER_PROMPT = "Add another? [y/N] "
_NOT_CAPTURED_PROMPT = "  not captured ({error}); press enter to continue: "


def collect_evidence(asker, sluice) -> dict:
    """Offer a short capture loop per kind. Returns kind -> proposed names.

    Gated on `asker.interactive` -- the class attribute, never `sys.stdin.isatty()` (the
    reason is the same one `sluice/onboard/ask.py` already records: deriving it
    independently makes the interactive half unreachable under pytest, where isatty()
    is always False). `--no-input`'s `NoInputAsker.interactive` is False, so this
    returns {} before asking or writing anything -- a flag-only run must not be able to
    seed the corpus any more than it can promote an entry.
    """
    if not getattr(asker, "interactive", False):
        return {}
    collected = {}
    for kind, spec in EVIDENCE_KINDS.items():
        # The intro rides on the prompt rather than reaching for the asker's private
        # _say: `confirm` and `ask_text_plain` are the whole injected interface, and a
        # hasattr probe for a private method would make the fake askers in tests
        # silently take a different path from the real ones.
        if not asker.confirm(f"{_INTRO.format(kind=kind)}\n{_CAPTURE_PROMPT.format(kind=kind)}"):
            continue
        names = []
        while True:
            name = (asker.ask_text_plain(_NAME_PROMPT.format(kind=kind)) or "").strip()
            if not name:
                break
            fields = {f: (asker.ask_text_plain(_FIELD_PROMPT.format(field=f)) or "").strip()
                      for f in spec.fields}
            # SAME call site for every kind -- only the looked-up PROMPT TEXT varies
            # (see `_BODY_PROMPT_BY_KIND` above). A blank answer means no body at all.
            body = (asker.ask_text_plain(_BODY_PROMPT_BY_KIND.get(kind, _BODY_PROMPT_DEFAULT))
                    or "").strip()
            try:
                sluice.add_evidence(kind=kind, name=name, fields=fields, body=body)
            except (ValueError, OSError, FileExistsError) as e:
                # Per-item isolation: one bad entry must not abort the interview. The
                # reason is PRINTED, never swallowed -- a counting-only except (increment
                # a counter, log nothing) is how a permanently-failing write stays
                # invisible: every later attempt fails the identical way and nobody
                # sees why.
                asker.ask_text_plain(_NOT_CAPTURED_PROMPT.format(error=e))
                continue
            names.append(name)
            if not asker.confirm(_ADD_ANOTHER_PROMPT):
                break
        if names:
            collected[kind] = names
    return collected
