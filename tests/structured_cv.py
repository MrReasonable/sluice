"""Fakes for the structured CV loop (#364/#365/#368).

A vault that holds a CV Layout and evidence by kind, a backend that answers each compose
with the next scripted reply and records what the composer, the auditor and the voice
judge were each shown, a renderer that records the DOCUMENT it is handed, and a reply
builder. Imported by the structured-engine tests; never collected itself (no `test_`
prefix), like tests/template_content.py.
"""
import dataclasses
import json
import os

from sluice.core.backends import Completion
from sluice.core.protocols import CandidateProfile, CvLayout, LayoutRole

# The first line of each prompt the loop sends: the backend routes on it, and an
# unrecognised prompt raises rather than getting a default answer.
COMPOSE_FIRST_LINE = "Compose a tailored CV for"            # cv/compose.py::build_structured_prompt
AUDIT_FIRST_LINE = "You are auditing a CV for fabrication."  # cv/audit.py::build_audit_prompt
VOICE_FIRST_LINE = "You are judging the VOICE of a CV"       # cv/voice.py::build_voice_prompt

CANDIDATE = CandidateProfile(forenames="Jane", surname="Roe", mobile="+1 555 0100")

LAYOUT = CvLayout(
    roles=(LayoutRole("Example Alpha", "02/2023", "present", location="Example Location A",
                      title="SYNTHETIC-TITLE-1"),
           LayoutRole("Example Beta", "06/2020", "01/2023", location="Example Location B",
                      title="SYNTHETIC-TITLE-2")),
    certificates=("Example Scrum Master",), education=("Example University, BSc Example",))


def layout_with(*, role0=None, **top):
    """LAYOUT with its first role's fields, and any top-level fields, replaced."""
    roles = list(LAYOUT.roles)
    if role0:
        roles[0] = dataclasses.replace(roles[0], **role0)
    return dataclasses.replace(LAYOUT, roles=tuple(roles), **top)


# One verified entry per role, each with figures of its own. EA1 declares `Tools:`, so the
# misattributed-tool check is ON and only EA1 licenses Examplelang. `CI` in EA1's body
# keeps a bullet naming it free of an unbundled-term finding.
ENTRIES = [
    {"title": "Grew the team", "company": "Example Alpha", "best_for": "delivery",
     "category": "people", "metrics": "3 8", "body": "Grew 3 to 8 with CI.",
     "fields": {"Tools": "Examplelang"}},
    {"title": "Cut the build time", "company": "Example Beta", "best_for": "platform",
     "category": "engineering", "metrics": "40", "body": "Cut builds by 40 percent.",
     "fields": {}},
]
SKILLS = [{"title": "Example Query", "best_for": "data", "category": "", "metrics": "",
           "body": "", "fields": {"Domain": "data"}}]
PREFIX_MAP = {"Example Alpha": "EA", "Example Beta": "EB"}

# One clean, cited bullet per role: every figure licensed by the cited entry, the one tool
# declared by it, no slop stem, no unbundled term.
GOOD_R1 = {"text": "Grew the team from 3 to 8 on Examplelang", "cites": ["EA1"]}
GOOD_R2 = {"text": "Cut build time by 40%", "cites": ["EB1"]}


def reply(profile="I build reliable systems.", roles=None, skills=("Examplelang",),
          **extra):
    """A reply's JSON text; `roles` defaults to GOOD_R1 under R1 and GOOD_R2 under R2."""
    obj = {"profile": profile,
           "roles": {"R1": [GOOD_R1], "R2": [GOOD_R2]} if roles is None else roles,
           "skills": list(skills), **extra}
    return json.dumps(obj)


def cfg(**over):
    """A CvConfig writing only inside the per-test sandbox (HOME is a fresh tmp_path)."""
    from sluice.cv.config import CvConfig
    c = CvConfig()
    c.output_dir = os.path.join(os.environ["HOME"], "cvout")
    c.served_dir = os.path.join(os.environ["HOME"], "cvserved")
    c.prefix_map = dict(PREFIX_MAP)
    for key, value in over.items():
        setattr(c, key, value)
    return c


class Note:
    def __init__(self, fm=None,
                 path="Job Applications/Job Leads/Example Alpha - SYNTHETIC-ROLE.md"):
        self.fm = {"status": "shortlist", "company": "Example Alpha",
                   "role": "SYNTHETIC-ROLE", **(fm or {})}
        self.ref, self.slug = path, path.split("/")[-1][:-3]


class FakeVault:
    """The Store members the structured CV path drives, and no others. Each signature is
    pinned to the real Vault's by test_cv_structured_engine.py's conformance row."""

    def __init__(self, *, layout=LAYOUT, experience=None, skills=None,
                 candidate=CANDIDATE, notes=(), skills_error=None):
        self._layout = layout
        self._evidence = {"experience": list(ENTRIES if experience is None else experience),
                          "skills": list(SKILLS if skills is None else skills)}
        self._candidate, self._notes = candidate, list(notes)
        self._skills_error = skills_error
        self.tailored, self.holds = {}, {}

    def read_cv_layout(self):
        return self._layout

    def read_candidate_profile(self):
        return self._candidate

    def read_evidence(self, kind, verified_only=True):
        if kind == "skills" and self._skills_error is not None:
            raise self._skills_error
        return [dict(e) for e in self._evidence.get(kind, [])]

    def read_leads(self, statuses=None):
        return list(self._notes)

    def set_tailored_cv(self, ref, value, *, only_if_absent=False):
        if only_if_absent and ref in self.tailored:
            return False
        self.tailored[ref] = value
        return True

    def hold_for_signoff(self, ref, *, pending, claims):
        if ref in self.tailored:
            return False
        self.holds[ref] = (pending, json.loads(claims))
        return True


class Cache:
    """A dossier cache that counts fetches: the only witness that a refusal spent nothing."""

    def __init__(self):
        self.calls = 0

    def get_or_build(self, fm):
        self.calls += 1
        return {"jd": {"markdown": "we value delivery"}}

    def jd_arrived(self, dossier):
        return True


class RecordingRenderer:
    """Records the DOCUMENT and directory it was handed, and writes a stand-in PDF there so
    the real `serve` runs."""

    def __init__(self):
        self.rendered = []

    def render(self, document, out_dir, *, neutral_name="CV.pdf"):
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, neutral_name)
        with open(path, "wb") as fh:
            fh.write(b"%PDF-1.4 stand-in")
        self.rendered.append((document, out_dir))
        return path


class ReplyBackend:
    """Answers the Nth compose with the Nth scripted reply, and each audit and voice check
    with `audit_out` / `voice_out` -- one answer for every call, or a list giving the Nth
    call the Nth answer. Any answer that is an exception instance is RAISED instead, which is
    how a test scripts a backend failure. Records each prompt by kind."""
    label = "scripted"

    def __init__(self, replies, *, audit_out="supported\tclaim\tEA1", voice_out=""):
        self.replies = list(replies)
        self.audit_out, self.voice_out = audit_out, voice_out
        self.compose_prompts, self.audit_prompts, self.voice_prompts = [], [], []

    @staticmethod
    def _answer(answers, calls, kind):
        if isinstance(answers, list):
            assert len(calls) <= len(answers), (
                f"{kind} call {len(calls)} but only {len(answers)} answers scripted")
            answers = answers[len(calls) - 1]
        if isinstance(answers, BaseException):
            raise answers
        return Completion(answers)

    def complete(self, prompt):
        first = prompt.splitlines()[0] if prompt else ""
        if first.startswith(COMPOSE_FIRST_LINE):
            self.compose_prompts.append(prompt)
            assert len(self.compose_prompts) <= len(self.replies), (
                f"compose call {len(self.compose_prompts)} but only {len(self.replies)} "
                "replies scripted -- the retry budget is one")
            return self._answer(self.replies, self.compose_prompts, "compose")
        if first.startswith(AUDIT_FIRST_LINE):
            self.audit_prompts.append(prompt)
            return self._answer(self.audit_out, self.audit_prompts, "audit")
        if first.startswith(VOICE_FIRST_LINE):
            self.voice_prompts.append(prompt)
            return self._answer(self.voice_out, self.voice_prompts, "voice")
        raise AssertionError(f"ReplyBackend: unrecognised prompt {first!r}")

    def audited(self):
        """The text each audit was asked about: cv/audit.py puts it after `=== CV ===` and
        appends one newline."""
        out = []
        for prompt in self.audit_prompts:
            body = prompt.partition("=== CV ===\n")[2]
            assert body, "cv/audit.py no longer carries the audited text under '=== CV ==='"
            out.append(body[:-1])
        return out

    def assert_consumed(self):
        assert len(self.compose_prompts) == len(self.replies), (
            f"{len(self.replies)} replies scripted, {len(self.compose_prompts)} composed")
