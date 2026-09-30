"""Where a frontmatter key is found, and what counts as its value (#329).

Every read and write in `core/vault.py` finds a key through `_key_lines`: a line at the
frontmatter's BASE indent (`_base_indent`: where PyYAML finds the root mapping's first key, or the
shallowest key-shaped line when PyYAML refuses the note), whose value is what follows the colon on
THAT line only. Before this, each site had
its own pattern, and they disagreed in two ways that each had a live corruption behind it:

- a key nested under another mapping (`hand_notes:\\n  status: rejected`) was matched as if it
  were the top-level key, so a write could land on the nested line and lift it out of its
  parent, and a read could report the nested value;
- a blank key (`pending_cv:`) read the NEXT line as its value, so `cv signoff` promoted
  `needs_signoff: [...]` as the send-ready CV pointer and `leads normalize` wrote
  `status: location: ...`.

Reading only the key's own line makes a blank key over a block list read blank, which the old
misread had been refusing by accident. Each guard that relied on that refusal now asks
`_holds_multiline_value` instead, and each has a row below on a blank key over a block list
(nothing written) PAIRED with a blank key over the next key (written), so the row is red only
for its guard.
"""
import logging
import os

import pytest

from sluice.core.protocols import MalformedNoteField
from sluice.core.vault import (
    Vault, _fm_dict, _fm_value, _key_lines, _parse_fm_spaced, _set_fm,
)

_REQUIRED = ['company: "Example Foundry"', 'role: "Analyst"']


def _seed(tmp_path, fm_lines, name="Example Foundry - Analyst.md"):
    v = Vault(str(tmp_path))
    leads = os.path.join(v.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    path = os.path.join(leads, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("---\n" + "\n".join(fm_lines) + "\n---\n# body\n")
    return v, path


def _bytes(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _fm_lines(path):
    return _bytes(path).split("---\n")[1].splitlines()


# A blank key over a hand-typed block list, and the same blank key over the next key.
def _block(key):
    return [f"{key}:", "  - HAND-ONE", "  - HAND-TWO"]


def _blank(key):
    return [f"{key}:", 'next_key: "x"']


# ── 1. the locator ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("inner,expected", [
    pytest.param("k: top\nhand:\n  k: nested", [(0, "top")], id="nested-after-top"),
    pytest.param("hand:\n  k: nested\nk: top", [(2, "top")], id="nested-before-top"),
    pytest.param("hand:\n  k: nested", [], id="nested-only"),
    pytest.param("  a: 1\n  k: top\n  hand:\n    k: nested", [(1, "top")], id="all-indented"),
    pytest.param("a:\n- k: item\nk: top", [(2, "top")], id="list-item-at-base-indent"),
    # A comment, even one indented deeper, does not set the base indent.
    pytest.param("  # deeper note\n# note\n\nk: top", [(3, "top")], id="leading-comments"),
])
def test_the_locator_finds_a_key_only_at_the_base_indent(inner, expected):
    assert [(i, value) for i, value, _ in _key_lines(inner, "k")] == expected


# ── 2. the writer ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("inner", [
    pytest.param("status: new\nhand_notes:\n  culture_flags: nested\nculture_flags: top",
                 id="nested-before"),
    pytest.param("status: new\nhand_notes:\n  culture_flags: nested", id="nested-only"),
])
def test_set_fm_never_touches_a_nested_line(inner):
    out = _set_fm(inner, "culture_flags", '"w"').split("\n")
    assert out[1:3] == ["hand_notes:", "  culture_flags: nested"]
    assert out.count('culture_flags: "w"') == 1


def test_set_fm_appends_at_the_base_indent_of_an_all_indented_block():
    assert _set_fm("  status: new", "culture_flags", '"w"') == \
        '  status: new\n  culture_flags: "w"'
    assert _set_fm("  status: new\n  culture_flags: old", "culture_flags", '"w"') == \
        '  status: new\n  culture_flags: "w"'


# ── 3-6. nested keys through the public methods ─────────────────────────────

def test_sign_off_does_not_promote_or_delete_a_nested_pending_cv(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", "hand:",
                               "  pending_cv: my.pdf", "  needs_signoff: keep"])
    before = _bytes(path)
    assert v.sign_off(path, accept=True) == "nothing"
    assert _bytes(path) == before


def test_sign_off_clears_only_the_top_level_markers(tmp_path):
    nested = ["hand:", "  pending_cv: mine.pdf", "  needs_signoff: keep"]
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", 'pending_cv: "p.pdf"',
                               "needs_signoff: []", *nested])
    assert v.sign_off(path, accept=True) == "promoted"
    lines = _fm_lines(path)
    start = lines.index("hand:")
    assert lines[start:start + len(nested)] == nested
    assert "tailored_cv: p.pdf" in lines


def test_normalize_leaves_a_nested_status_line(tmp_path):
    nested = ["hand:", "  status: shortlist"]
    v, path = _seed(tmp_path, [*_REQUIRED, "status: Shortlist", *nested])
    v.normalize_all_statuses()
    lines = _fm_lines(path)
    assert "status: shortlist" in lines
    start = lines.index("hand:")
    assert lines[start:start + 2] == nested


@pytest.mark.parametrize("fm,top_at", [
    pytest.param(["last_seen: 2020-01-01", "hand:", "  last_seen: mine"], 0, id="nested-after"),
    pytest.param(["hand:", "  last_seen: mine", "last_seen: 2020-01-01"], 2, id="nested-before"),
])
def test_a_rescrape_advances_only_the_top_level_last_seen(tmp_path, fm, top_at):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: new", *fm])
    v._bump_last_seen(path, "2026-09-30")
    lines = _fm_lines(path)[len(_REQUIRED) + 1:]
    assert lines[top_at] == "last_seen: 2026-09-30"
    assert "  last_seen: mine" in lines
    assert lines.count("hand:") == 1


def test_read_leads_reports_the_top_level_status_not_a_nested_one(tmp_path):
    v, _ = _seed(tmp_path, [*_REQUIRED, "status: shortlist", "hand:", "  status: rejected"])
    assert v.read_leads()[0].status == "shortlist"
    assert _fm_dict("status: shortlist\nhand:\n  status: rejected")["status"] == "shortlist"


# ── 7. a blank key's value is its own line ──────────────────────────────────

@pytest.mark.parametrize("after", [
    pytest.param(["  - Foo", "  - Bar"], id="block-list"),
    pytest.param(["- Foo"], id="block-list-column-0"),
    pytest.param(["bio: |", "  text"], id="next-key-holding-a-block-scalar"),
    pytest.param(["  Acme Ltd"], id="indented-continuation"),
    pytest.param(["# note"], id="column-0-comment"),
    pytest.param(["role: Eng"], id="next-key"),
    pytest.param(["", "", "role: Eng"], id="blank-lines-then-key"),
    pytest.param([], id="last-line"),
])
def test_a_blank_key_reads_blank_whatever_follows_it(after):
    inner = "\n".join(["status: new", "pending_cv:", *after])
    assert _fm_value(inner, "pending_cv") == ""
    assert _fm_dict(inner)["pending_cv"] == ""


# ── 8. every guard the old misread satisfied by accident, with its control ──

def test_g1_require_blank_refuses_a_blank_key_over_a_block_list(tmp_path):
    v, path = _seed(tmp_path, ['role: "Analyst"', "status: new", *_block("company")])
    before = _bytes(path)
    assert v.update_fields(path, {"company": '"Filled"'}, require_blank=frozenset({"company"})) \
        is False
    assert _bytes(path) == before


def test_g1_control_require_blank_fills_a_blank_key_over_the_next_key(tmp_path):
    v, path = _seed(tmp_path, ['role: "Analyst"', "status: new", *_blank("company")])
    assert v.update_fields(path, {"company": '"Filled"'}, require_blank=frozenset({"company"}))
    assert v.read_leads()[0].fm["company"] == "Filled"


def test_g2_require_unchanged_refuses_a_block_list_that_reads_blank(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: new", *_block("role_type")])
    before = _bytes(path)
    assert v.read_leads()[0].fm["role_type"] == ""
    assert v.update_fields(path, {"role_type": '"contract"'},
                           require_unchanged={"role_type": ""}) is False
    assert _bytes(path) == before


def test_g2_control_require_unchanged_writes_over_a_blank_key(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: new", *_blank("role_type")])
    assert v.update_fields(path, {"role_type": '"contract"'}, require_unchanged={"role_type": ""})
    assert v.read_leads()[0].fm["role_type"] == "contract"


def test_g3_set_tailored_cv_counts_a_block_list_as_present(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", *_block("tailored_cv")])
    before = _bytes(path)
    assert v.set_tailored_cv(path, "cv.pdf", only_if_absent=True) is False
    assert _bytes(path) == before


def test_g3_control_set_tailored_cv_fills_a_blank_key(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", *_blank("tailored_cv")])
    assert v.set_tailored_cv(path, "cv.pdf", only_if_absent=True)
    assert v.read_leads()[0].fm["tailored_cv"] == "cv.pdf"


def test_g3_hold_for_signoff_counts_a_block_list_as_present(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", *_block("tailored_cv")])
    before = _bytes(path)
    assert v.hold_for_signoff(path, pending="p.pdf", claims="[]") is False
    assert _bytes(path) == before


def test_g3_control_hold_for_signoff_holds_over_a_blank_key(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", *_blank("tailored_cv")])
    assert v.hold_for_signoff(path, pending="p.pdf", claims="[]")
    assert v.read_leads()[0].fm["pending_cv"] == "p.pdf"


def test_g4_sign_off_reports_a_collision_over_a_block_list_tailored_cv(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", 'pending_cv: "p.pdf"',
                               "needs_signoff: []", *_block("tailored_cv")])
    assert v.sign_off(path, accept=True) == "collision"
    lines = _fm_lines(path)
    start = lines.index("tailored_cv:")
    assert lines[start:start + 3] == _block("tailored_cv")


def test_g4_control_sign_off_promotes_over_a_blank_tailored_cv(tmp_path):
    # Also the second live corruption this closes: the misread saw `next_key: "x"` as a CV
    # already present, reported a collision and threw the signed-off CV away.
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", 'pending_cv: "p.pdf"',
                               "needs_signoff: []", *_blank("tailored_cv")])
    assert v.sign_off(path, accept=True) == "promoted"
    fm = v.read_leads()[0].fm
    assert fm["tailored_cv"] == "p.pdf" and fm["next_key"] == "x"


def test_g5_sign_off_leaves_a_block_list_needs_signoff_alone(tmp_path, caplog):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", 'pending_cv: "p.pdf"',
                               *_block("needs_signoff")])
    before = _bytes(path)
    with caplog.at_level(logging.WARNING):
        assert v.sign_off(path, accept=True) == "nothing"
    assert _bytes(path) == before
    assert "needs_signoff" in caplog.text


def test_g5_sign_off_leaves_a_pending_cv_continued_on_a_later_line_alone(tmp_path, caplog):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", 'pending_cv: "p.pdf',
                               '  continued"', "needs_signoff: []"])
    before = _bytes(path)
    with caplog.at_level(logging.WARNING):
        assert v.sign_off(path, accept=True) == "nothing"
    assert _bytes(path) == before
    assert any("pending_cv" in r.getMessage() and "spread over several lines" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


def test_g5_control_sign_off_clears_a_one_line_needs_signoff(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", 'pending_cv: "p.pdf"',
                               "needs_signoff: []"])
    assert v.sign_off(path, accept=True) == "promoted"
    fm = v.read_leads()[0].fm
    assert "needs_signoff" not in fm and "pending_cv" not in fm


def _merge(tmp_path, fm, **kw):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: new", *fm])
    args = {"alt_urls": ["https://example.invalid/2"], "first_seen": "", "last_seen": ""}
    args.update(kw)
    v.merge_cluster(path, [], **args)
    return v, path


def test_g6_merge_refuses_a_block_list_alt_urls(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: new", *_block("alt_urls")])
    before = _bytes(path)
    with pytest.raises(MalformedNoteField):
        v.merge_cluster(path, [], alt_urls=["https://example.invalid/2"],
                        first_seen="", last_seen="")
    assert _bytes(path) == before


def test_g6_control_merge_fills_a_blank_alt_urls(tmp_path):
    v, _ = _merge(tmp_path, _blank("alt_urls"))
    assert v.read_leads()[0].fm["alt_urls"] == '["https://example.invalid/2"]'


@pytest.mark.parametrize("key,stamp", [("first_seen", "2020-01-01"), ("last_seen", "2030-01-01")])
def test_g7_merge_skips_a_block_list_timestamp(tmp_path, key, stamp):
    _, path = _merge(tmp_path, _block(key), **{key: stamp})
    lines = _fm_lines(path)
    start = lines.index(f"{key}:")
    assert lines[start:start + 3] == _block(key)


@pytest.mark.parametrize("key,stamp", [("first_seen", "2020-01-01"), ("last_seen", "2030-01-01")])
def test_g7_control_merge_fills_a_blank_timestamp(tmp_path, key, stamp):
    v, _ = _merge(tmp_path, _blank(key), **{key: stamp})
    assert v.read_leads()[0].fm[key] == stamp


def test_g8_a_rescrape_skips_and_logs_a_block_list_last_seen(tmp_path, caplog):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: new", *_block("last_seen")])
    before = _bytes(path)
    with caplog.at_level(logging.WARNING):
        v._bump_last_seen(path, "2026-09-30")
    assert _bytes(path) == before
    assert "last_seen" in caplog.text


def test_g8_control_a_rescrape_stamps_a_blank_last_seen(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: new", *_blank("last_seen")])
    v._bump_last_seen(path, "2026-09-30")
    assert v.read_leads()[0].fm["last_seen"] == "2026-09-30"


@pytest.mark.parametrize("status", [
    pytest.param(_block("status"), id="block-list"),
    pytest.param(['status: "Shortlist', '  continued"'], id="quote-continued"),
    # Two agreeing status lines, the SECOND spread over several lines: every status line is
    # asked, since the collapse removes all of them.
    pytest.param(["status: shortlist", 'status: "shortlist', '  continued"'],
                 id="duplicate-second-spread"),
])
def test_g9_normalize_skips_and_reports_a_status_spread_over_several_lines(tmp_path, status):
    v, path = _seed(tmp_path, [*_REQUIRED, *status])
    before = _bytes(path)
    summary = v.normalize_all_statuses()
    assert _bytes(path) == before
    assert [name for name, _ in summary["conflicts"]] == [os.path.basename(path)]


def test_g9_normalize_skips_a_status_spread_over_several_lines_in_the_transform(tmp_path):
    # The scan and the CAS transform each decide; this row reaches the transform alone.
    from sluice.core.vault import _normalize_status_transform
    text = "---\n" + "\n".join([*_REQUIRED, 'status: "Shortlist', '  continued"']) + "\n---\nb\n"
    assert _normalize_status_transform(text) == text


def test_g9_control_normalize_rewrites_a_one_line_status(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, 'status: "Shortlist"', 'next_key: "x"'])
    assert v.normalize_all_statuses()["changed"] == 1
    assert "status: shortlist" in _fm_lines(path)


# ── 9-10. the live corruptions the misread caused ───────────────────────────

def test_sign_off_does_not_promote_the_next_line_of_a_blank_pending_cv(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, "status: shortlist", "pending_cv:",
                               'needs_signoff: ["x"]'])
    before = _bytes(path)
    assert v.sign_off(path, accept=True) == "nothing"
    assert _bytes(path) == before


def test_normalize_does_not_write_the_next_line_into_a_blank_status(tmp_path):
    # The scan reads the blank status as blank, so it reports nothing to change and writes
    # nothing -- not even `status: ` with a trailing space, which a comparison of the stored line
    # against the unstripped canonical form would rewrite on every run.
    v, path = _seed(tmp_path, [*_REQUIRED, "status:", "location: Example Place"])
    before = _bytes(path)
    summary = v.normalize_all_statuses()
    assert summary["changed"] == 0 and summary["unchanged"] == 1
    assert _bytes(path) == before


def test_the_normalize_transform_does_not_write_the_next_line_into_a_blank_status():
    from sluice.core.vault import _normalize_status_transform
    text = "---\n" + "\n".join([*_REQUIRED, "status:", "location: Example Place"]) + "\n---\nb\n"
    assert _normalize_status_transform(text) == text


def test_the_archived_name_is_read_from_its_own_top_level_line():
    from sluice.core.vault import _archived_from
    assert _archived_from('archived_from_note: "Example Foundry - Analyst"') == \
        "Example Foundry - Analyst"
    assert _archived_from('hand:\n  archived_from_note: "Example Foundry - Analyst"') is None


# ── 11. citability ──────────────────────────────────────────────────────────

def test_a_nested_verified_key_does_not_make_an_evidence_entry_citable():
    assert "verified" not in _parse_fm_spaced("Company: Alpha\nhand:\n  verified: 2026-01-01")
    assert _parse_fm_spaced("Company: Alpha\nverified: 2026-01-01")["verified"] == "2026-01-01"


def test_a_list_under_a_nested_key_does_not_join_the_top_level_key():
    fm = _parse_fm_spaced("Category:\n  - Process\nhand:\n  inner:\n    - Leak")
    assert fm["Category"] == "Process"
    assert fm["hand"] == ""


# --- A stray indent on the FIRST line, and the citability read failing closed (#329) -------
#
# The base indent was taken from the first line that is neither blank nor a comment, so one
# accidental leading space on it -- a slip in Obsidian's source view -- made every real key read as
# nested: the lead dropped out of `read_leads` with nothing logged, and each re-scrape appended a
# second `last_seen` at the stray indent. PyYAML refuses such a note, so the fallback decides it:
# the shallowest key-shaped line, which the stray first line does not undercut.

@pytest.mark.parametrize("stray", ["  ", "\t"])
def test_a_stray_indent_on_the_first_line_does_not_hide_the_lead(tmp_path, stray):
    v, path = _seed(tmp_path, [f"{stray}aliases: x", *_REQUIRED, "status: applied",
                               "last_seen: 2026-01-01"])

    assert [(n.fm.get("company"), n.status) for n in v.read_leads()] == [("Example Foundry", "applied")]

    v._bump_last_seen(path, "2026-02-01")

    lines = _fm_lines(path)
    assert "last_seen: 2026-02-01" in lines
    assert [ln for ln in lines if "last_seen" in ln] == ["last_seen: 2026-02-01"], lines


def test_a_stray_first_line_does_not_let_a_guard_write_over_a_present_key(tmp_path):
    v, path = _seed(tmp_path, ["  aliases: x", *_REQUIRED, 'tailored_cv: "cv/real.pdf"'])

    before = _bytes(path)

    v.set_tailored_cv(path, '"cv/other.pdf"', only_if_absent=True)

    assert _bytes(path) == before


def test_a_quoted_value_continued_at_a_shallower_indent_does_not_move_the_base():
    """PyYAML reads this note, so it decides the base: the continuation line is part of a value,
    whatever its indent. `test_the_fallback_counts_only_key_shaped_lines` covers the line rule."""
    inner = '  a: "one\ntwo"\n  b: 1'
    assert _fm_value(inner, "b") == "1"


# Citability may only NARROW. The old reader took the LAST `verified:` at any indent, so a nested
# blank `verified:` after a top-level stamp un-cited the entry; reading top-level only would make it
# citable again, licensing its figures in the gate. A nested `verified:` line therefore makes the
# entry's `verified` read absent.

@pytest.mark.parametrize("nested", ["  verified:", "  verified: 2026-02-02"])
def test_a_nested_verified_line_withdraws_citability(nested):
    fm = _parse_fm_spaced(f"verified: 2026-01-01\nMetrics: cut cost 40%\nnotes:\n{nested}")
    assert "verified" not in fm


def test_a_nested_verified_line_withdraws_citability_through_read_evidence(tmp_path):
    v = Vault(str(tmp_path))
    kind = "experience"
    d = v._evidence_dir(kind)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "Example entry.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nverified: 2026-01-01\nMetrics: cut cost 40%\nnotes:\n  verified:\n---\nbody\n")

    assert v.read_evidence(kind) == []


# The base indent is PyYAML's when PyYAML reads the frontmatter: the column of the root mapping's
# first key. The shallowest key-shaped line was a hand-written approximation, and it lost to a
# quoted or flow value continued at column 0 in an all-indented note: the continuation carries a
# `: `, reads as a key line, drags the base to column 0, and every real key reads as nested -- the
# lead vanished and a re-scrape wrote a column-0 `last_seen` that broke the note. The shallowest-key
# rule stays as the fallback for a note PyYAML refuses (a stray indent on the first line is one).

@pytest.mark.parametrize("value, cont", [
    ('"Example Foundry', 'foo: bar"'),
    ('[Example Foundry,', 'foo: bar]'),
    ('{name: Example Foundry,', 'foo: bar}'),
])
def test_a_continuation_at_column_zero_does_not_move_the_base(tmp_path, value, cont):
    import yaml
    v, path = _seed(tmp_path, ['  company: "Example Foundry"', '  role: "Analyst"',
                               f"  aliases: {value}", cont, "  status: applied",
                               "  last_seen: 2026-01-01"])
    assert yaml.safe_load(_bytes(path).split("---\n")[1]), "the fixture must be YAML PyYAML reads"

    assert [(n.fm.get("company"), n.status) for n in v.read_leads()] == [("Example Foundry", "applied")]

    v._bump_last_seen(path, "2026-02-01")

    fm = yaml.safe_load(_bytes(path).split("---\n")[1])
    assert str(fm["last_seen"]) == "2026-02-01", fm


# Citability may only narrow, for every field and not only `verified`: the old reader took the LAST
# line of a key at any indent, so a nested blank `Metrics:` hid the top-level figure from the gate.
# A nested line named like a top-level key therefore makes that key read absent.

def test_a_nested_line_named_like_a_field_withdraws_that_field():
    fm = _parse_fm_spaced("verified: 2026-01-01\nMetrics: cut cost 40%\nnotes:\n  Metrics:")
    assert "Metrics" not in fm
    assert fm["verified"] == "2026-01-01"


def test_a_flow_mapping_root_defers_to_the_line_rule_rather_than_reading_its_brace_as_an_indent():
    """PyYAML reads `{company: x}` as a mapping whose first key sits after `{`. That prefix is not
    an indent, so PyYAML's answer is refused and the line rule decides."""
    from sluice.core.vault import _base_indent, _yaml_base_indent
    assert _yaml_base_indent("{company: x}") is None
    assert _yaml_base_indent("  a: 1\n  b: 2") == "  "
    assert _base_indent(["{company: x}", "  role: y"]) == ""


def test_an_anchor_on_the_first_key_leaves_the_base_at_the_mapping():
    """Measured: PyYAML marks an anchored first key at the anchor's column, so the base stays at
    the mapping's own indent and the next key is found."""
    assert _fm_value("&x company: Example Foundry\nrole: Analyst", "role") == "Analyst"


# PyYAML counts U+2028, U+2029 and U+0085 as line breaks too, so its mark's LINE number does not
# index a `split("\n")` list: a comment carrying one before the first key moved the indent read to
# the wrong line (the lead vanished) or past the end of the list (`IndexError`, which stopped every
# command that reads leads). The indent is taken from the mark's character offset instead.

@pytest.mark.parametrize("brk", ["\u2028", "\u2029", "\u0085"])
def test_a_unicode_line_break_in_a_comment_does_not_move_or_crash_the_base(tmp_path, brk):
    from sluice.core.vault import _base_indent
    assert _base_indent(f"#a{brk}#b\n  company: x".split("\n")) == "  "
    v, path = _seed(tmp_path, [f"#a{brk}#b", '  company: "Example Foundry"', "", '  role: "Analyst"',
                               "  status: applied"])

    assert [(n.fm.get("company"), n.status) for n in v.read_leads()] == [("Example Foundry", "applied")]


def test_a_frontmatter_that_is_not_a_mapping_falls_back_without_crashing():
    from sluice.core.vault import _base_indent, _yaml_base_indent
    assert _yaml_base_indent("- a\n- b") is None
    assert _yaml_base_indent("just a scalar") is None
    assert _base_indent(["- a", "- b"]) == ""


def test_the_fallback_counts_only_key_shaped_lines():
    """PyYAML refuses this note (the tab line), so the line rule decides; a quoted value continued at
    column 0 is not a key line and must not drag the base to column 0."""
    inner = '  aliases: "one\ntwo three"\n  role: Engineer\n\tjunk\n  status: applied'
    assert _fm_value(inner, "status") == "applied"


def test_a_note_whose_first_key_sits_at_column_zero_is_not_parsed(monkeypatch):
    """Nearly every note opens with a column-0 key, and for it both PyYAML and the fallback answer
    column 0, so PyYAML is not asked: parsing every note on every read made a whole-vault read
    many times slower."""
    import yaml
    from sluice.core.vault import _base_indent, _yaml_base_indent
    _yaml_base_indent.cache_clear()
    calls = []
    real = yaml.compose
    monkeypatch.setattr(yaml, "compose", lambda *a, **k: calls.append(1) or real(*a, **k))

    assert _base_indent(["company: x", "role: y"]) == ""
    assert calls == []
    assert _base_indent(["  company: x", "  role: y"]) == "  "
    assert calls == [1]


@pytest.mark.parametrize("opener", ["%FOO bar: baz", "!!map # a: b", "&a # note: x", "*a # note: x"])
def test_a_directive_or_tag_line_does_not_take_the_fast_path(opener):
    """A column-0 `%`, `!`, `&` or `*` line can carry a `: ` without being a key, so the fast path
    must not read it as the root mapping's first key."""
    from sluice.core.vault import _base_indent
    assert _base_indent([opener, "  company: x", "  status: applied"]) == "  "


def test_sign_off_names_a_blank_pending_cv_that_holds_a_list(tmp_path, caplog):
    """A blank `pending_cv:` over a list reads blank on its own line; the spread check runs first,
    so the refusal is logged by name rather than reported as a plain `nothing`."""
    v, path = _seed(tmp_path, [*_REQUIRED, *_block("pending_cv")])
    before = _bytes(path)

    with caplog.at_level(logging.WARNING):
        assert v.sign_off(path) == "nothing"

    assert _bytes(path) == before
    assert any("pending_cv" in r.getMessage() and "spread over several lines" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


# The sign-off latch in `cv/engine.py` reads a parsed snapshot, which cannot tell a hand-typed list
# under a blank `pending_cv:` from a blank value. The writes re-read the FRESH note inside their
# transform, so they are where a spread value is refused -- loudly, with the store's
# `MalformedNoteField`, as `merge_cluster` refuses a block-list `alt_urls`. Returning False instead
# would let `run_one` report `rendered` for a pointer it never wrote.

@pytest.mark.parametrize("key", ["pending_cv", "needs_signoff"])
def test_a_hold_is_refused_over_a_spread_marker(tmp_path, key):
    v, path = _seed(tmp_path, [*_REQUIRED, *_block(key)])
    before = _bytes(path)

    with pytest.raises(MalformedNoteField, match=key):
        v.hold_for_signoff(path, pending='"cv/new.pdf"', claims='["x"]')

    assert _bytes(path) == before


def test_an_unguarded_pointer_write_is_refused_over_a_spread_tailored_cv(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, *_block("tailored_cv")])
    before = _bytes(path)

    with pytest.raises(MalformedNoteField, match="tailored_cv"):
        v.set_tailored_cv(path, '"cv/new.pdf"', only_if_absent=False)

    assert _bytes(path) == before


def test_an_unguarded_pointer_write_still_fills_a_blank_tailored_cv(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, *_blank("tailored_cv")])

    assert v.set_tailored_cv(path, '"cv/new.pdf"', only_if_absent=False) is True

    assert 'tailored_cv: "cv/new.pdf"' in _fm_lines(path)


def test_a_sign_off_bound_to_a_blank_snapshot_is_stale_when_a_hold_has_appeared(tmp_path):
    """`Sluice.sign_off_cv` hands a blank snapshot to the store with `require_pending=""`: a hold
    placed after the snapshot must not be promoted without the confirmation the caller skipped."""
    v, path = _seed(tmp_path, [*_REQUIRED, 'pending_cv: "cv/new.pdf"', 'needs_signoff: ["x"]'])
    before = _bytes(path)

    assert v.sign_off(path, accept=True, require_pending="") == "stale"

    assert _bytes(path) == before


def test_a_normalize_dry_run_agrees_with_the_real_run_on_a_note_with_no_status(tmp_path):
    v, path = _seed(tmp_path, [*_REQUIRED, 'url: "https://example.invalid/1"'])

    dry = v.normalize_all_statuses(dry_run=True)
    real = v.normalize_all_statuses()

    assert (dry["changed"], dry["unchanged"]) == (real["changed"], real["unchanged"]) == (0, 1)
