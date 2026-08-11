"""A backend that answers by prompt KIND.

The four LLM call sites an e2e run touches -- triage judge, cv compose, cv audit,
track classify -- each have a stable first line. `ScriptedBackend` dispatches on
a PREFIX of the prompt's first line and RAISES on anything it does not recognise.

Two design points, both load-bearing:

- **Never registered in the `backend` seam.** `test_backend_registry.py` asserts
  the registry equals exactly `{claude-max, anthropic, deepseek, openai}` at
  collection time, so a registered fake would break the suite. It is passed via
  `Sluice(backend=...)`, the per-instance override PR 0 added for this.
- **Prefix keying, and raise-on-unknown.** `cv/compose.py`'s first line is
  `Compose a tailored CV for {name} applying for {role} at {company}.`, fully
  interpolated -- an exact-match table would raise on every CV call. And an
  unrecognised prompt raises rather than returning a default, because a silent
  default would let a mis-wired call pass as success (the quiet-wrong-default
  class this codebase engineers out).
"""
import json
import re

# Stable first-line prefixes of the four call sites. cv-compose is a prefix
# because its first line is fully interpolated (see module docstring).
_TRIAGE = "You are the batched judgment stage"           # triage/prompt.py:_SCAFFOLD_INTRO
_CV = "Compose a tailored CV for"                        # cv/compose.py:build_prompt
_AUDIT = "You are auditing a CV for fabrication."        # cv/audit.py:build_audit_prompt
_TRACK = "You track a job seeker's live applications."   # track/classify.py:build_prompt

# The id is the REST OF THE LINE, not the first whitespace-free run. `lead_id` is the
# note's store-issued slug, and a real slug is a note FILENAME -- `Example Ltd - Example
# Role`, spaces and all. `(\S+)` captured `Example` and every verdict then failed to match
# the dossier it came from, which the engine drops SILENTLY (an unmatched lead_id is a
# `continue`), so the run simply judged nothing. The vault's `_sanitize` maps the C0
# controls out of any name it issues, so one LINE is always the whole id -- spelled
# `[^\n]` rather than `.` so it stays one line under a DOTALL flag too.
_DOSSIER_ID_RE = re.compile(r"Dossier \d+ lead_id: ([^\n]+)")
# The company is the tail of the interpolated first line; `.+ at` is greedy so
# it binds the LAST " at ", not one inside a role title.
_CV_COMPANY_RE = re.compile(r"^Compose a tailored CV for .+ at (.+)\.\s*$")


class ScriptedBackend:
    last_backend = "primary"

    def __init__(self, *, cv_by_company=None, triage_verdicts=None,
                 default_verdict="shortlist", track_response=None):
        # {company: cv_text} -- keyed by COMPANY (parsed from the compose first
        # line), required for any company the CV hop composes for; a missing key
        # RAISES (a silent default CV would mask a mis-wired call). Keying by
        # company, not lead, means two DIFFERENT CVs at one company is not
        # expressible today -- a limitation to lift if PR 2/PR 3 ever needs it.
        self.cv_by_company = dict(cv_by_company or {})
        # {lead_id: "shortlist"|"research"|"dismiss"}; leads not named get default.
        self.triage_verdicts = dict(triage_verdicts or {})
        self.default_verdict = default_verdict
        # The track classify JSON: a dict, or [(marker_substring, dict), ...]
        # matched against the prompt (first hit wins) when one run sees several
        # emails. None -> a not_job answer, which reconcile skips.
        self.track_response = track_response
        self.prompts: list[str] = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        first = prompt.splitlines()[0] if prompt else ""
        if first.startswith(_TRIAGE):
            return self._triage(prompt)
        if first.startswith(_CV):
            return self._cv(first)
        if first.startswith(_AUDIT):
            return self._audit()
        if first.startswith(_TRACK):
            return self._track(prompt)
        raise AssertionError(
            f"ScriptedBackend: unrecognised prompt (first line {first!r}). "
            "Add a handler rather than returning a silent default.")

    def _triage(self, prompt):
        # Echo the batch's real lead_ids back as verdicts, exactly as a live judge
        # would key its output -- so the engine can map each verdict to its note.
        ids = _DOSSIER_ID_RE.findall(prompt)
        return json.dumps([
            {"lead_id": i,
             "verdict": self.triage_verdicts.get(i, self.default_verdict),
             "relevance_score": 80}
            for i in ids])

    def _cv(self, first):
        m = _CV_COMPANY_RE.match(first)
        company = m.group(1) if m else ""
        if company not in self.cv_by_company:
            raise AssertionError(
                f"ScriptedBackend: no scripted CV for company {company!r} "
                f"(have {sorted(self.cv_by_company)})")
        return self.cv_by_company[company]

    def _audit(self):
        # The audit is advisory and NEVER blocks (cv/audit.py). A clean
        # "supported" line leaves no flags; the id token is cosmetic.
        return "supported\ta claim\tEF1\n"

    def _track(self, prompt):
        resp = self.track_response
        if isinstance(resp, list):
            for marker, data in resp:
                if marker in prompt:
                    return json.dumps(data)
            resp = None
        if isinstance(resp, dict):
            return json.dumps(resp)
        return json.dumps({"lead": None, "type": "not_job", "confidence": 0.0})
