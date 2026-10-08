"""Synthetic users for the coach evals: a seeded name, a fictional location from
tests/conftest.py::LOCATIONS, and a hand-written situation (reviewed like shipped prose).

A persona may also name a SCENARIO: a scripted turn of events the harness plays into the run
(run.py) and the rubric scores (rubric.scenario_checks). Each scenario's scripted text lives
HERE, once, because the harness that plays it and the check that looks for it must agree on it
to the byte."""
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
    return [Persona(**json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(Path(directory).glob("*.json"))]


def persona_name(p) -> str:
    from faker import Faker
    f = Faker()
    f.seed_instance(p.name_seed)
    return f.name()
