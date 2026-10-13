"""Advisory LLM fabrication audit. Classifies each CV claim supported | paraphrase |
deduced | unsupported against the bundle and returns the flagged lines for the
candidate's sign-off. It NEVER blocks (advisory only). The model call goes through
core/backends, not a hardcoded host path."""
import re

# #380. A profile line sums up several entries, so asking for ONE cited id made the auditor
# answer NONE -- `unsupported`, a hold -- for a summary the bundle bears out. The rule now:
# a claim may COMBINE facts from different entries, and is honest when
# every part of it is evidenced and the dates let the parts meet. Coaching teams in a
# technology follows from leadership plus that technology before or during the leadership;
# coaching in a technology the candidate never touched is a fabrication. `deduced` is shown
# to the user (which entries were combined) and holds only when it cites nothing or an id
# no entry carries (`unsupported_claims`); `unsupported` still holds.
DEDUCED_RULE = (
    "'deduced' = no single entry states the claim, but every particular it names (each "
    "activity, skill, domain, tool, figure and scope) is evidenced somewhere in the bundle, "
    "and the timeline lets those particulars combine: work the claim draws on happened "
    "before or during the role it sits under (for the profile, anywhere in the career). "
    "Cite every id the deduction uses, comma-separated."
)
UNSUPPORTED_RULE = (
    "'unsupported' = some particular of the claim has no evidence anywhere in the bundle, "
    "or the timeline rules the combination out (the work it draws on began only after the "
    "role it sits under ended)."
)


def build_audit_prompt(cv_text, bundle_text):
    return (
        "You are auditing a CV for fabrication. SOURCE BUNDLE is the ONLY truth.\n"
        "For each factual claim in the CV, output one line: <verdict>\t<claim>\t<cited-ids-or-NONE>\n"
        "verdict in {supported, paraphrase, deduced, unsupported}. 'supported' = one entry "
        "states it. 'paraphrase' = one entry states the same fact, reworded. "
        + DEDUCED_RULE + " " + UNSUPPORTED_RULE + " A bullet is read against the role it "
        "sits under in the CV; the profile against the whole career. Date entries by the "
        "CAREER TIMELINE when the bundle has one. Do not invent support.\n\n"
        "=== SOURCE BUNDLE ===\n" + bundle_text + "\n\n=== CV ===\n" + cv_text + "\n"
    )


def run_audit(backend, cv_text, bundle_text):
    report = backend.complete(build_audit_prompt(cv_text, bundle_text)).text
    flagged = [line for line in report.splitlines()
               if line.strip().lower().startswith(("unsupported", "paraphrase", "deduced"))]
    return report, flagged


def _shows_evidence(field, known):
    """Whether a `deduced` line's cite field names real entries: at least one of `known`,
    and no other id-shaped token (one carrying a digit). Read as word tokens, so `EA1,EA2`,
    `EA1; EA2`, `[EA1]` and `EA1 and EA2` all read the same, and only an id is judged --
    a filler word between two real ids is not an unknown id, and `NONE` names nothing."""
    tokens = [t.casefold() for t in re.findall(r"\w+", field)]
    named = [t for t in tokens if t in known]
    stray = [t for t in tokens if t not in known and any(c.isdigit() for c in t)]
    return bool(named) and not stray


def unsupported_claims(flagged, ids):
    """The flagged lines that HOLD under the #60 sign-off gate: every `unsupported`, and
    every `deduced` that shows no evidence -- no readable cite field, no id from `ids` (the
    bundle's own entry ids) in it, or an id-shaped token that is not one of them (#380). A deduction is "every part evidenced
    somewhere", so a `deduced\t<claim>\tNONE` is an unsupported claim under another name,
    and letting it through would release a CV the pre-#380 verdict set held. `paraphrase`
    (same fact, reworded) is legitimate tailoring: blocking on it would fire on nearly every
    CV and train rubber-stamping, so it stays advisory, as does a `deduced` line whose every
    id is real. Pure over run_audit's output; CvResult.audit_flags keeps every flagged line.

    Matches the VERDICT word exactly (the line's first run of letters, whatever separates
    it from the claim), not a prefix -- so a malformed 'unsupportedness' verdict is not
    mistaken for 'unsupported' and does not block."""
    known = {i.casefold() for i in ids}
    held = []
    for line in flagged:
        # The verdict is the line's first WORD, not its first tab field: a model writing
        # `unsupported claim NONE` or `deduced: claim` has still said `unsupported` or
        # `deduced`, and reading the whole run up to a tab as the verdict released those.
        word = re.match(r"\s*([a-z]+)", line.lower())
        verdict = word.group(1) if word else ""
        fields = line.split("\t")
        if verdict == "unsupported":
            held.append(line)
        elif verdict == "deduced" and not (len(fields) > 2
                                           and _shows_evidence(fields[2], known)):
            # No cite FIELD fails closed: a deduction whose evidence cannot be read has
            # shown none.
            held.append(line)
    return held
