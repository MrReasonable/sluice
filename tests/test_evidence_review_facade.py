"""The two facade methods the MCP verify tool reaches the store through. They exist so
mcpserver.py never names a Store member (tests/test_mcpserver.py's isolation sweep matches a
call by attribute name), and so the CLI's per-entry review loop is left untouched."""
import pytest

from sluice.core.app import Sluice, verify_outcome_text
from sluice.core.config import Config

_FIELDS = {"Company": "Example Ltd", "Best For": "platform"}


def _app(tmp_path):
    return Sluice(Config(vault_dir=str(tmp_path / "vault")), today=lambda: "2026-01-01")


def _propose(app, name, body="Did a thing."):
    app.add_evidence(kind="experience", name=name, fields=_FIELDS, body=body)


def test_pending_for_review_returns_every_pending_entry_with_its_exact_text(tmp_path):
    app = _app(tmp_path)
    _propose(app, "Example alpha")
    _propose(app, "Example beta")
    out = app.pending_evidence_for_review(kind="experience")
    titles = [t for t, _ in out["entries"]]
    assert sorted(titles) == ["example-alpha", "example-beta"]
    for title, text in out["entries"]:
        assert text == app.store().read_pending_evidence_text("experience", title)
    assert out["failed"] == [] and out["not_found"] == []


def test_pending_for_review_names_filters_dedupes_and_reports_unknowns(tmp_path):
    app = _app(tmp_path)
    _propose(app, "Example alpha")
    _propose(app, "Example beta")
    out = app.pending_evidence_for_review(
        kind="experience", names=["Example alpha", "example-alpha", "No such entry"])
    assert [t for t, _ in out["entries"]] == ["example-alpha"]
    assert out["not_found"] == ["No such entry"]


def test_promote_reviewed_promotes_matching_text_and_reports_changed(tmp_path):
    app = _app(tmp_path)
    _propose(app, "Example alpha")
    _propose(app, "Example beta")
    entries = dict(app.pending_evidence_for_review(kind="experience")["entries"])
    out = app.promote_reviewed_evidence(kind="experience", approved=[
        ("example-alpha", entries["example-alpha"]),
        ("example-beta", entries["example-beta"] + "\nedited after review"),
    ])
    assert out == {"promoted": ["example-alpha"], "changed": ["example-beta"], "failed": []}
    citable = [e["title"] for e in app.list_evidence(kind="experience")]
    assert citable == ["example-alpha"]


def test_promote_reviewed_isolates_one_failure(tmp_path):
    app = _app(tmp_path)
    _propose(app, "Example alpha")
    entries = dict(app.pending_evidence_for_review(kind="experience")["entries"])
    out = app.promote_reviewed_evidence(kind="experience", approved=[
        ("example-gone", "whatever"),
        ("example-alpha", entries["example-alpha"]),
    ])
    assert out["promoted"] == ["example-alpha"]
    assert [t for t, _ in out["failed"]] == ["example-gone"]


def _sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_promote_shown_promotes_only_text_matching_what_was_shown(tmp_path):
    """The check that an entry was not edited after the human saw it lives HERE, in the
    facade both routes share -- not in a front-end -- so it holds without mcp."""
    app = _app(tmp_path)
    _propose(app, "Example alpha")
    _propose(app, "Example beta")
    texts = dict(app.pending_evidence_for_review(kind="experience")["entries"])
    out = app.promote_shown_evidence(kind="experience", shown=[
        ("example-alpha", _sha(texts["example-alpha"])),
        ("example-beta", _sha(texts["example-beta"] + "edited")),
        ("example-gone", _sha("whatever")),
    ])
    assert out["promoted"] == ["example-alpha"] and out["changed"] == ["example-beta"]
    assert out["no_longer_pending"] == ["example-gone"] and out["failed"] == []
    assert [e["title"] for e in app.list_evidence(kind="experience")] == ["example-alpha"]


def test_promote_shown_reads_by_exact_title_not_by_slug(tmp_path):
    """A ticked title must not drag in a DIFFERENT pending entry its slug matches."""
    app = _app(tmp_path)
    _propose(app, "Example alpha")
    out = app.promote_shown_evidence(kind="experience", shown=[("Example alpha", _sha("x"))])
    assert out["no_longer_pending"] == ["Example alpha"] and out["promoted"] == []


@pytest.mark.parametrize("kind,expected", [
    ("experience", "make it citable"), ("skills", "make it available to a CV's skills list"),
    ("stories", "mark it reviewed")])
def test_verify_outcome_text_is_keyed_on_the_kinds_flags(kind, expected):
    assert verify_outcome_text(kind) == expected


def test_verify_outcome_text_rejects_unknown_kind():
    with pytest.raises(ValueError, match="experience"):
        verify_outcome_text("nope")
