"""Synthetic users for the coach evals: a seeded name, a fictional location from
tests/conftest.py::LOCATIONS, and a hand-written situation (reviewed like shipped prose).

A persona may also name a SCENARIO: a scripted turn of events the harness plays into the run
(run.py) and the rubric scores (rubric.scenario_checks). Each scenario's scripted text lives
HERE, once, because the harness that plays it and the check that looks for it must agree on it
to the byte.

The prep runner (prep.py) has its own persona type, `PrepPersona`, loaded from `personas/prep/`:
the interview's employer, a seeded role, the lead's status, the server's level, a JD template and
the user's own stories. `Persona` never receives its keys, since `load_personas` globs only the
top directory."""
import json
from dataclasses import dataclass
from pathlib import Path

# The simulated user's exact reply the first time the coach asks whether to save.
HESITANT_LINE = "Maybe. I'm not sure yet."
# The line the harness appends to the Judging Profile behind the coach's back.
EDIT_MARKER = "Edited by hand during the session."
EDIT_NOTE = ("Job Applications", "Judging Profile.md")
SCENARIOS = ("hesitant", "edited_mid_session", "retired_board")


@dataclass(frozen=True)
class Persona:
    id: str
    name_seed: int
    location: str
    situation: str
    focus: str = ""
    vault_env: bool = False
    max_turns: int = 12   # owner's budget ruling: a good interview reaches the form well before
    scenario: str | None = None

    def __post_init__(self):
        # Loudly: a misspelt scenario would otherwise run as a plain persona, and every
        # scenario check would read "not exercised" -- indistinguishable from a run whose
        # scripted event simply never came up.
        if self.scenario is not None and self.scenario not in SCENARIOS:
            raise ValueError(f"persona {self.id}: unknown scenario {self.scenario!r}; "
                             f"valid: {', '.join(SCENARIOS)}")


def load_personas(directory) -> list:
    # A flat glob, so the prep runner's personas in `prep/` below are never read as setup's.
    return [Persona(**json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(Path(directory).glob("*.json"))]


def persona_name(p) -> str:
    from faker import Faker
    f = Faker()
    f.seed_instance(p.name_seed)
    return f.name()


# ── the prep runner's personas (prep.py): a user preparing for one interview ──
# The only placeholders a prep JD template may carry. The role arrives only through `{role}`,
# a seeded faker title, so no persona file restates one: hand-written prose naming a role trips
# the taxonomy sweep (about one faker title in five does, measured).
JD_PLACEHOLDERS = ("company", "location", "role")
# Every free-text field of a story. None may carry a placeholder: a story happened at its OWN
# past employer, so filling in the interview's company would make every story claim past work
# there, and the grader's ground truth would say so too.
STORY_TEXT_FIELDS = ("title", "company", "best_for", "body")
# What a story cannot do without: the title names its note, and the brief shows the body and the
# past employer.
STORY_REQUIRED = ("title", "company", "body")


def template_fields(template: str) -> list:
    """The replacement fields `str.format` would fill in `template` (`{{` escapes excluded)."""
    import string
    return [f for _, f, _, _ in string.Formatter().parse(template) if f is not None]


@dataclass(frozen=True)
class PrepPersona:
    id: str
    name_seed: int
    location: str
    company: str          # the employer the interview is with
    role_seed: int        # a faker seed: the role is generated, never written here
    status: str           # one canonical status, set on the seeded lead
    write: bool           # the server's level: --write, else read-only
    jd_template: str
    stories: list         # [{title, company (the PAST employer), best_for, body, verified}]
    max_turns: int = 12   # the owner's setup budget; no prep run has been measured yet

    def __post_init__(self):
        # Loudly, at load: each of these would otherwise seed a vault the persona did not mean,
        # and the run would grade a coach against it.
        from sluice.core.status import CANONICAL
        if self.status not in CANONICAL:
            raise ValueError(f"prep persona {self.id}: unknown status {self.status!r}; "
                             f"valid: {', '.join(sorted(CANONICAL))}")
        # `type(...) is bool`, never truthiness: a hand-written "false" (or 0) would otherwise
        # read as the opposite of what the file says -- `write` omitting `--read-only` and running
        # the read-only persona at the write level, or a story seeded `verified:` that the
        # persona marked unverified, so the grader scores a vault the persona never defined.
        if type(self.write) is not bool:
            raise ValueError(f"prep persona {self.id}: write must be true or false, not "
                             f"{self.write!r}")
        extra = sorted(set(template_fields(self.jd_template)) - set(JD_PLACEHOLDERS))
        if extra:
            raise ValueError(f"prep persona {self.id}: the JD template carries a placeholder "
                             f"outside {', '.join(JD_PLACEHOLDERS)}: {extra}")
        for i, story in enumerate(self.stories):
            for key in STORY_REQUIRED:
                if not str(story.get(key) or "").strip():
                    raise ValueError(f"prep persona {self.id}: story {i} has no {key}")
            if type(story.get("verified")) is not bool:
                raise ValueError(f"prep persona {self.id}: story {i}'s verified must be true or "
                                 f"false, not {story.get('verified')!r}")
            for key in STORY_TEXT_FIELDS:
                # Any brace, not only a well-formed field: nothing formats a story, so a brace
                # there can only be a template that slipped in.
                if any(c in str(story.get(key) or "") for c in "{}"):
                    raise ValueError(f"prep persona {self.id}: story {i}'s {key} carries a "
                                     f"placeholder")
            # The title NAMES the story's note (prep.py::seed_vault writes `<title>.md` into the
            # stories folder), so it must be one plain file name: a separator or a `..` would
            # write outside that folder, and a leading dot names a hidden file.
            if not is_plain_file_name(str(story["title"])):
                raise ValueError(f"prep persona {self.id}: story {i}'s title "
                                 f"{story['title']!r} is not a plain file name")
        # Two stories sharing a title would seed ONE note, the second overwriting the first,
        # while the simulated user is still handed both bodies as its true record: the grader
        # would then score fidelity against a vault the persona never defined. Casefolded, since
        # a case-insensitive filesystem holds the two spellings as one file.
        folded = [str(s["title"]).casefold() for s in self.stories]
        shared = sorted({t for t in folded if folded.count(t) > 1})
        if shared:
            raise ValueError(f"prep persona {self.id}: two stories share the title {shared}")


def is_plain_file_name(name: str) -> bool:
    """`name` is one path component that names an ordinary file: no separator of either
    platform, no NUL, not `.` or `..`, and no leading dot."""
    return bool(name) and not any(c in name for c in "/\\\0") and not name.startswith(".")


def load_prep_personas(directory) -> list:
    return [PrepPersona(**json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(Path(directory).glob("*.json"))]


def role_name(p) -> str:
    """The prep persona's role: a faker title from its seed, in the locale and shape of
    tests/conftest.py::_title_pool."""
    from faker import Faker
    f = Faker("en_GB")
    f.seed_instance(p.role_seed)
    return f.job()
