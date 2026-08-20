"""Vault.read_candidate_profile(): the frontmatter read, and the parser's limits."""
import os

from sluice.core.candidate import full_name
from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH, CandidateProfile
from sluice.core.vault import Vault, parse_frontmatter


def _write_note(tmp_path, body):
    dest = os.path.join(str(tmp_path), CANDIDATE_PROFILE_RELPATH)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(body)
    return Vault(str(tmp_path))


def test_a_missing_note_returns_an_all_blank_profile_rather_than_raising(tmp_path):
    # Same "unset means empty string, caller falls back" shape read_criteria has.
    v = Vault(str(tmp_path))
    assert v.read_candidate_profile() == CandidateProfile()


def test_a_missing_note_creates_nothing(tmp_path):
    # #81's rule applied to a NEW store method: a read must not create the file, its
    # parent directory, or any marker -- sqlite3.connect creating a 0-byte file merely
    # by opening one is how a relocation refusal was once silently disarmed for good.
    # Both checks use os.path.exists, not os.path.isdir for the parent -- isdir would
    # wrongly pass if some stray non-directory artefact ever sat at that exact path.
    v = Vault(str(tmp_path))
    v.read_candidate_profile()
    dest = os.path.join(str(tmp_path), CANDIDATE_PROFILE_RELPATH)
    assert not os.path.exists(dest)
    assert not os.path.exists(os.path.dirname(dest))


def test_a_note_with_no_frontmatter_fence_returns_an_all_blank_profile(tmp_path):
    # The plan names three abstain shapes: a missing note (above), a note with NO
    # --- fence at all (here), and a note declaring only some keys (below).
    # _split_frontmatter already returns None for a non-fenced note and
    # _fm_dict(None) already returns {}, so this shape works for free -- pinned
    # with its own test rather than left to ride along as an accident of the
    # other two, since "works for free" is exactly the kind of claim a later
    # refactor can break without anything here noticing.
    v = _write_note(tmp_path, "just prose, no frontmatter fence at all\n")
    assert v.read_candidate_profile() == CandidateProfile()


def test_only_the_declared_keys_come_back_declared(tmp_path):
    v = _write_note(tmp_path, "---\nforenames: Ada\nsurname: Example\n---\n\nbody prose\n")
    p = v.read_candidate_profile()
    assert p.forenames == "Ada"
    assert p.surname == "Example"
    assert p.email == ""
    assert full_name(p) == "Ada Example"


def test_unknown_frontmatter_keys_are_ignored(tmp_path):
    # `hasattr(p, "age_range")` (the ORIGINAL form of this test) can NEVER be True
    # regardless of whether the known-keys filter works: CandidateProfile is a
    # plain dataclass with no `**kwargs` path, so an unfiltered
    # `CandidateProfile(**fm)` raises TypeError rather than setting a stray
    # attribute -- that assertion was dead on arrival, witnessed only by accident
    # (as an error, not a failed assertion) if the filter were removed.
    #
    # Prove the note genuinely DECLARES the unknown keys, through the filter-free
    # raw parse, then prove the read succeeds anyway with the one known field
    # correctly populated -- that combination is what "ignored" actually means.
    note = "---\nforenames: Ada\nage_range: 35-44\nnonsense: x\n---\n"
    raw = parse_frontmatter(note)
    assert raw["age_range"] == "35-44"
    assert raw["nonsense"] == "x"
    v = _write_note(tmp_path, note)
    assert v.read_candidate_profile().forenames == "Ada"


def test_a_key_outside_fm_dicts_character_class_is_dropped(tmp_path):
    # _fm_dict's key regex is [A-Za-z0-9_]+, so a key containing any other
    # character is silently invisible, not loud. Checked here on the RAW parsed
    # dict (parse_frontmatter), which has no known-keys filter of its own --
    # a single `read_candidate_profile().forenames == "Cy"` assertion (the
    # ORIGINAL form of this test) cannot tell that mechanism apart from the
    # known-keys filter dropping `fore-names` for being an unrecognised FIELD
    # regardless of whether the regex matched it. Measured: widening the class to
    # [A-Za-z0-9_-]+ leaves that single assertion green, because `fore-names` is
    # not a CandidateProfile field either way -- only the assertion below reddens.
    assert parse_frontmatter("---\nfore-names: Bea\n---\n") == {}

    # Last-key-wins is a real, separate property of the known-keys filter, worth
    # keeping alongside the above: "Forenames" (capital F) is a different dict
    # key from "forenames" and is dropped as unrecognised, while the lowercase
    # spelling -- written last -- wins.
    v = _write_note(tmp_path, "---\nForenames: Ada\nfore-names: Bea\nforenames: Cy\n---\n")
    assert v.read_candidate_profile().forenames == "Cy"


def test_the_body_is_never_read_as_data(tmp_path):
    v = _write_note(tmp_path, "---\nforenames: Ada\n---\n\nsurname: NotAField\n")
    assert v.read_candidate_profile().surname == ""


def test_parse_frontmatter_is_public_and_matches_the_reader(tmp_path):
    # onboard/plan.py's `_render_candidate` verifies its own render through THIS function,
    # so it must be the SAME parser the vault reads with -- not a second
    # implementation that could silently drift. Asserting only that
    # parse_frontmatter parses correctly (the ORIGINAL form of this test) says
    # nothing about whether read_candidate_profile actually uses it: swapping the
    # reader to _parse_fm_spaced would leave that assertion green.
    #
    # A single-quoted value is where the two parsers actually diverge --
    # _fm_dict strips BOTH quote styles, _parse_fm_spaced only strips "" -- so
    # parsing the SAME note text through both and tying the results together
    # here is what actually catches that swap.
    note = "---\nforenames: 'Ada'\n---\n"
    assert parse_frontmatter(note) == {"forenames": "Ada"}
    v = _write_note(tmp_path, note)
    assert v.read_candidate_profile().forenames == parse_frontmatter(note)["forenames"] == "Ada"
