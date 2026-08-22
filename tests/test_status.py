import argparse

import pytest
from sluice.core import status


def _walk_actions(parser):
    """Every argparse action reachable from `parser`, recursing into every subparser.

    Modeled on tests/test_docs_claims.py's `_command_tree` -- the same private-API shape
    (`_SubParsersAction`/`.choices`) `argcomplete` itself relies on to introspect an
    arbitrary argparse tree, so this cannot drift from what `--help` shows. Flat rather
    than nested like `_command_tree`: a caller filtering for `--status` doesn't care which
    subcommand owns it, only whether the option exists anywhere in the tree.
    """
    actions = list(parser._actions)
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                actions.extend(_walk_actions(sub))
    return actions


@pytest.mark.parametrize("raw,expected", [
    ('"new"', "new"), ("new", "new"), ("New", "new"),
    ("dismissed", "dismiss"), ('"dismiss"', "dismiss"),
    ("Researching", "research"), ("shortlisted", "shortlist"),
    ("needs_review", "needs_review"), ("phone screen", "phone_screen"),
    ("  applied  ", "applied"), ("weird_unknown", "weird_unknown"),
])
def test_normalize_maps_drift_and_passes_unknown(raw, expected):
    assert status.normalize(raw) == expected


def test_ownership_partitions():
    assert status.is_application_owned("applied") is True
    assert status.is_application_owned("dismissed") is False   # triage-owned
    assert status.is_application_owned("new") is False
    assert status.is_canonical("dismiss") is True
    assert status.is_canonical("weird_unknown") is False
    # the two sets are disjoint and both live in CANONICAL
    assert not (set(status.TRIAGE_OWNED) & set(status.APPLICATION_OWNED))
    assert set(status.TRIAGE_OWNED) | set(status.APPLICATION_OWNED) == status.CANONICAL


def test_unjudgeable_is_triage_owned_and_not_application_owned():
    assert "unjudgeable" in status.TRIAGE_OWNED
    assert not status.is_application_owned("unjudgeable")
    assert not status.is_terminal("unjudgeable")


def test_the_common_misspelling_normalises():
    assert status.normalize("unjudgable") == "unjudgeable"


def test_the_selection_default_has_ONE_home_and_the_parser_uses_it():
    # The value lived in FOUR places before this (cli.py's `cmd_triage_run` fallback and
    # its `--status` argparse default, `Sluice.triage`'s `statuses=` default in
    # core/app.py, and `triage/engine.py`'s `run`) and only the last was ever changed by
    # an earlier draft -- which would have written `unjudgeable` and then never re-read
    # it. Symbols, not line numbers: the numbers this comment first carried were accurate
    # in the pre-change tree and point somewhere else entirely in this one.
    from sluice.cli import _build_parser
    actions = _walk_actions(_build_parser())
    status_defaults = [a.default for a in actions if "--status" in (a.option_strings or [])]
    assert status_defaults, "the walk found no --status option: the sweep is vacuous"
    expected = ",".join(status.DEFAULT_TRIAGE_STATUSES)
    assert set(status_defaults) == {expected}, status_defaults


def test_the_selection_default_is_not_derivable_from_the_vocabulary():
    # It is a hand-picked RETRY subset, not a computed one. An implementer who derives it
    # from TRIAGE_OWNED would silently re-judge shortlisted and dismissed leads every run.
    assert set(status.DEFAULT_TRIAGE_STATUSES) < set(status.TRIAGE_OWNED)
    assert "shortlist" not in status.DEFAULT_TRIAGE_STATUSES
    assert "dismiss" not in status.DEFAULT_TRIAGE_STATUSES
