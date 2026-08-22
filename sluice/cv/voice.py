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
"""


def build_voice_prompt(cv_text: str) -> str:
    return (
        "You are judging the VOICE of a CV, not its accuracy. Flag lines that read as "
        "machine-generated: inflated register, empty intensifiers, corporate cliche, "
        "hollow abstraction, or a claim shaped like a slogan.\n"
        "Judge the writing ONLY. Do not comment on whether a claim is true, and do not "
        "suggest new content.\n"
        "Output one line per finding: flag\\t<the offending phrase>\\t<why, in under 12 "
        "words>. Output nothing at all if the writing is clean.\n\n"
        "=== CV ===\n" + cv_text + "\n"
    )


def run_voice(backend, cv_text: str):
    """(raw report, flagged lines). Pure over the backend's reply -- fails open
    (a backend error or timeout) is the CALLER's job, exactly as it is for run_audit:
    this function makes no attempt to swallow anything itself, so a caller that forgets
    to wrap it finds out immediately rather than shipping a silent no-op."""
    report = backend.complete(build_voice_prompt(cv_text))
    flagged = [line for line in report.splitlines()
               if line.strip().lower().startswith("flag")]
    return report, flagged
