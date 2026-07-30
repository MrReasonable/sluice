"""Issue #8's acceptance criterion, in TWO ARMS -- a single arm passes even if the profile is
ignored entirely. Same attribution shape as S1 in #58."""
import re

from sluice.core.protocols import CRITERIA_RELPATH
from sluice.core.vault import Vault
from sluice.onboard.questions import expresses_a_preference
from sluice.triage.prompt import build_system_prompt_from

FILLED = """\
## Who this candidate is

An example practitioner of the example trade, with example standing.

### Target and wrong shape

Target: an example-shaped role. Wrong: anything at example-lead scope.

## Win patterns and anti-patterns

Attracts: 'example win phrase'. Repels: 'example anti phrase'.
"""


def test_a_scaffolded_profile_still_tells_the_judge_to_abstain(run_init, tmp_path):
    """Arm 1. v1 asserted the OPPOSITE of this and called it the acceptance criterion."""
    vault = tmp_path / "notes"
    assert run_init(["init", "--vault", str(vault), "--no-input"])[0] == 0
    prompt = build_system_prompt_from(Vault(str(vault)).read_criteria())
    assert "No judging criteria have been supplied yet" in prompt
    assert "prefer `research`" in prompt


def test_an_install_with_no_profile_at_all_abstains_identically(tmp_path):
    """Arm 2, the attribution half: scaffolding must not CHANGE the judge's behaviour until a
    human writes something. If arm 1 passed while this failed, arm 1 would prove nothing."""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "No judging criteria have been supplied yet" in \
        build_system_prompt_from(Vault(str(empty)).read_criteria())


def test_a_filled_profile_reaches_the_judge_verbatim(run_init, tmp_path):
    vault = tmp_path / "notes"
    run_init(["init", "--vault", str(vault), "--no-input"])
    (vault / CRITERIA_RELPATH).write_text(FILLED, encoding="utf-8")
    prompt = build_system_prompt_from(Vault(str(vault)).read_criteria())
    assert "example win phrase" in prompt and "example anti phrase" in prompt
    assert "No judging criteria have been supplied yet" not in prompt


def test_the_scaffold_smuggles_no_exemplar_into_the_judge_prompt(run_init, tmp_path):
    """Same shared vocabulary as the unit tier, imported not re-listed."""
    vault = tmp_path / "notes"
    run_init(["init", "--vault", str(vault), "--no-input"])
    criteria = Vault(str(vault)).read_criteria()
    assert criteria.strip()                                  # SCOPE
    for prompt in re.findall(r"<!--(.*?)-->", criteria, re.S):
        assert not expresses_a_preference(prompt)
