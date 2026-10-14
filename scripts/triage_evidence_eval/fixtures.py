"""Synthetic inputs for the #196 live eval. The eval's Judging Profile states preferences
ON PURPOSE (no lead could be expected to shortlist otherwise); it is a dev-only fixture,
never shipped or defaulted. Neutrality here means no REAL identity -- checked on the
loaded values by tests/test_triage_evidence_eval.py."""
import json
from dataclasses import dataclass
from pathlib import Path

ROLES = ("gap", "borderline", "no_support")


@dataclass(frozen=True)
class Lead:
    id: str
    company: str
    title: str
    location: str
    url: str
    jd: str
    expected: str
    gap_term: str
    role: str

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f"lead {self.id}: unknown role {self.role!r}; valid: {ROLES}")


@dataclass(frozen=True)
class Fixtures:
    profile: str
    record: dict
    leads: list


def load(path) -> Fixtures:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return Fixtures(profile=raw["profile"], record=raw["record"],
                    leads=[Lead(**lead) for lead in raw["leads"]])
