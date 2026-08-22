"""The pure status -> folder verdict, and the guard that keeps the Archive set DERIVED.

Decision 3 (#1): Archive is `dismiss` plus every terminal, READ from core/status.py. A hand-listed
set silently keeps a newly-added terminal in Active, which is the quiet-wrong-default this codebase
engineers out. The enumeration guard below is what makes that unfakeable.
"""
import pytest

from sluice.core import status as _status
from sluice.core.leads import (
    ACTIVE_SUBDIR,
    ARCHIVE_SUBDIR,
    LEAD_LAYOUTS,
    layout_subfolder,
)


def test_is_terminal_answers_every_terminal_and_nothing_else():
    """SCOPE first: the predicate must agree with status.py's own ladder, BOTH ways. Asserting
    only the True half would pass for `lambda s: True`."""
    for s in _status._TERMINAL:
        assert _status.is_terminal(s), s
    for s in set(_status.CANONICAL) - set(_status._TERMINAL):
        assert not _status.is_terminal(s), s


def test_is_terminal_normalizes_before_deciding():
    """`normalize` folds quoting and drift; a predicate that skipped it would answer False for
    the value a real note carries (the vault's own frontmatter quotes statuses)."""
    assert _status.is_terminal(' "rejected" ')
    assert not _status.is_terminal(' "shortlist" ')


@pytest.mark.parametrize("status", ["new", "shortlist", "research", "needs_review",
                                    "applied", "phone_screen", "interview", "offer"])
def test_a_live_status_is_active(status):
    assert layout_subfolder(status, "active_archive") == ACTIVE_SUBDIR


@pytest.mark.parametrize("status", ["dismiss", "rejected", "accepted", "withdrawn"])
def test_dismiss_and_every_terminal_are_archive(status):
    assert layout_subfolder(status, "active_archive") == ARCHIVE_SUBDIR


def test_the_archive_set_is_derived_from_status_not_hand_listed():
    """THE decision-3 guard. Every canonical status is classified, and the Archive set is
    computed here the way the spec states it -- `dismiss` plus status.py's own `_TERMINAL` --
    then compared with what the shipped map answers. A hand-listed literal inside
    `layout_subfolder` passes today and diverges the moment `_TERMINAL` grows; this comparison
    cannot.

    It asserts on SCOPE too (the count of CANONICAL): a sweep over an empty vocabulary would
    satisfy every membership check below, and for a guard whose success case is 'nothing was
    mis-filed' that is indistinguishable from working.
    """
    assert len(_status.CANONICAL) == 13, sorted(_status.CANONICAL)
    expected_archive = {"dismiss"} | set(_status._TERMINAL)
    got_archive = {s for s in _status.CANONICAL
                   if layout_subfolder(s, "active_archive") == ARCHIVE_SUBDIR}
    got_active = {s for s in _status.CANONICAL
                  if layout_subfolder(s, "active_archive") == ACTIVE_SUBDIR}
    assert got_archive == expected_archive
    assert got_active == set(_status.CANONICAL) - expected_archive
    assert got_active | got_archive == set(_status.CANONICAL), "a canonical status is unclassified"


def test_a_non_canonical_status_is_never_moved():
    """never-regress: an unrecognized status is passed through untouched everywhere else, so the
    layout must not decide a folder for one either. None means 'leave it where it is'."""
    assert layout_subfolder("some_future_state", "active_archive") is None
    assert layout_subfolder("", "active_archive") is None


def test_the_flat_layout_puts_every_status_at_the_root():
    for s in _status.CANONICAL:
        assert layout_subfolder(s, "") == ""


def test_the_flat_layout_still_declines_a_non_canonical_status():
    """Even flat must answer None rather than "": "" means 'the root is where this belongs',
    which would make reconcile MOVE an unknown-status note out of a subfolder a human chose.
    None is the only answer that means 'do not touch this note'."""
    assert layout_subfolder("some_future_state", "") is None


def test_an_unknown_layout_raises_and_lists_the_valid_names():
    """Fail loudly at construction. A typo'd layout must not fall through to flat -- see
    `_select_backend`. Matched on the MESSAGE, not just the type: ValueError is what several
    other things in this module raise, so asserting the type alone would pass with the guard
    deleted."""
    with pytest.raises(ValueError, match="active_archive"):
        layout_subfolder("new", "activearchive")


def test_lead_layouts_names_the_flat_default_first():
    assert LEAD_LAYOUTS[0] == ""
    assert "active_archive" in LEAD_LAYOUTS
