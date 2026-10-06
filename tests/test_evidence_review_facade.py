"""The two facade methods the MCP verify tool reaches the store through. They exist so
mcpserver.py never names a Store member (tests/test_mcpserver.py's isolation sweep matches a
call by attribute name), and so the CLI's per-entry review loop is left untouched."""
import pytest

from sluice.core.app import Sluice
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


@pytest.mark.parametrize("kind,expected", [
    ("experience", "make it citable"), ("skills", "make it available to a CV's skills list"),
    ("stories", "mark it reviewed")])
def test_evidence_verify_outcome_is_keyed_on_cited_by_gate(tmp_path, kind, expected):
    assert _app(tmp_path).evidence_verify_outcome(kind) == expected


def test_evidence_verify_outcome_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError, match="experience"):
        _app(tmp_path).evidence_verify_outcome("nope")
