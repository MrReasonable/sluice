# sluice/cv/voice.py
"""Opt-in, model-judged VOICE check (#167). Flags AI-tell phrasing a fixed phrase
blocklist cannot catch -- a novel clause that reads as machine-generated was never
going to be in cv/slop.py's `_PHRASES`, which is the gap the issue argues a blocklist
alone cannot close.

Deliberately a SEPARATE module from cv/audit.py, not a second prompt folded into it:
the two checks have opposite consequences, and folding them together would put both
behaviours behind one name and one config gate (`cv.voice_check` here vs.
`cv.require_signoff` there), making either impossible to turn off independently of
the other.

cv/audit.py's fabrication audit NEVER blocks (see its own docstring): it judges
whether a CLAIM is TRUE, and a wrong verdict there is either an unwinnable ask (the
candidate cannot manufacture evidence for a real fact the bundle happens not to
state) or exactly the "paraphrase" case audit.py already argues would train
rubber-stamping if it blocked. A voice finding is different in kind: it judges HOW a
true claim is WORDED, and the fix costs nothing but phrasing -- the retry can always
satisfy it without inventing, omitting, or misrepresenting a fact. That is what makes
it defensible for a voice finding that survives the retry to hold the send-ready
pointer once `cv.style_hold` is on (Task 15), where the same consequence on the
fabrication audit would not be.

Opt-in (`cv.voice_check`, default False): an unconfigured install must not start
spending an extra LLM call the moment it upgrades. The deterministic phrase tier
(cv/slop.py, wired into the retry by cv/engine.py) runs regardless of this flag, so
#167's original complaint -- that the linter's matches were computed and discarded --
is already closed whether or not this module ever runs.

The model call goes through core/backends (the `backend` argument below is whatever
cv/engine.py was constructed with), never a hardcoded host path -- this module has no
network code of its own.

SCOPING is the CALLER's, exactly as it is for this check's deterministic sibling
(cv/slop.py's `check_phrases`, which takes lines it has no opinion about). This module is
never handed the document -- what goes in the `excerpt` is cv/engine.py's call, and it
sends the PROFILE prose and WORK bullets `validate.section_spans` yields, because a voice
complaint about an EMPLOYER or CERTIFICATE line is answerable only by renaming the
employer or the certificate -- a style rule turned into fabrication pressure. That policy
lives with the tier, in cv/engine.py, and this module keeps its zero imports rather than
reaching for cv/validate.py to reproduce the split.
"""


def build_voice_prompt(excerpt: str) -> str:
    # The EXCERPT sentence is load-bearing, not politeness. The caller passes a SUBSET
    # of the document (see the module docstring), so the text below genuinely is missing
    # headings, employer names and contact details -- and a model told it is looking at
    # a whole CV can answer with an ABSENCE ("no contact block", "no employer named").
    # Every finding rides cv/engine.py's retry into cv/compose.py under "Fix these and
    # re-emit the FULL CV", so an absence complaint is an instruction to ADD material --
    # the fabrication pressure the scoping exists to remove, re-entering through the
    # prompt. The parameter name says this to a READER of the code; the sentence below
    # says it to the MODEL. The delimiter has to agree with both -- it is the LAST
    # framing before the content, so one still calling this a whole CV would re-assert
    # exactly what that sentence has just denied. All three stay free of the PROFILE/WORK
    # policy: which lines make up the excerpt is the caller's.
    return (
        "You are judging the VOICE of a CV, not its accuracy. Flag lines that read as "
        "machine-generated: inflated register, empty intensifiers, corporate cliche, "
        "hollow abstraction, or a claim shaped like a slogan.\n"
        "You are shown an EXCERPT: the candidate's own prose lines, and nothing else. "
        "Judge only the lines you are given -- a heading, fact, or section that is "
        "absent from the excerpt is not a finding.\n"
        "Judge the writing ONLY. Do not comment on whether a claim is true, and do not "
        "suggest new content.\n"
        "Output one line per finding: flag\\t<the offending phrase>\\t<why, in under 12 "
        "words>. Output nothing at all if the writing is clean.\n\n"
        "=== EXCERPT ===\n" + excerpt + "\n"
    )


def run_voice(backend, excerpt: str):
    """(raw report, flagged lines). Pure over the backend's reply -- fails open
    (a backend error or timeout) is the CALLER's job, exactly as it is for run_audit:
    this function makes no attempt to swallow anything itself, so a caller that forgets
    to wrap it finds out immediately rather than shipping a silent no-op."""
    report = backend.complete(build_voice_prompt(excerpt))
    # Matches the FLAG token exactly -- the first tab-delimited field of the
    # `flag\t<phrase>\t<why>` line the prompt asks for -- not a prefix, the same
    # discipline `cv/audit.py`'s `unsupported_claims` states for the same reason one
    # module over.
    #
    # A prefix match here is worse than it looks, because the false positive lands on
    # the CLEAN case. The prompt says "output nothing at all if the writing is clean",
    # and an agentic backend routinely answers that in a sentence instead -- measured,
    # "Flagged nothing: the writing is clean." returned ONE finding. That burns the
    # single retry on every such run and, under `cv.style_hold`, withholds the
    # send-ready `tailored_cv` pointer from a CV with nothing wrong with it. An
    # ordinary "Flagship product ..." line does the same. `_unwrap_agent_envelope`
    # exists in compose.py precisely because these backends do not honour "output
    # nothing"; this is the same lesson at the parse instead of the prompt.
    flagged = [line for line in report.splitlines()
               if line.partition("\t")[0].strip().lower() == "flag"]
    return report, flagged
