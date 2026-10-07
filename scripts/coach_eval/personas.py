"""Synthetic users for the coach evals: a seeded name, a fictional location from
tests/conftest.py::LOCATIONS, and a hand-written situation (reviewed like shipped prose)."""
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Persona:
    id: str
    name_seed: int
    location: str
    situation: str
    focus: str = ""
    vault_env: bool = False
    max_turns: int = 12   # owner's budget ruling: a good interview reaches the form well before


def load_personas(directory) -> list:
    return [Persona(**json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(Path(directory).glob("*.json"))]


def persona_name(p) -> str:
    from faker import Faker
    f = Faker()
    f.seed_instance(p.name_seed)
    return f.name()
