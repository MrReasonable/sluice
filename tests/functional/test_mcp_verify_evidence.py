"""verify_evidence end to end through the real SDK, in memory. `mcp.Client`'s default
`auto` mode speaks the 2026-07-28 protocol and drives the SEP-2322 input-required loop
with `elicitation_callback` standing in for the human -- the same loop Claude Code runs.
Promotions are read back from the store, never taken from the tool's own report.

No `async def test_...`, as in test_mcp_contract.py: each test wraps its async body in
`asyncio.run(...)`, since the repo carries no pytest-asyncio."""
import asyncio
import json
import pathlib

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.mcpserver import build_server

_FIELDS = {"Company": "Example Ltd", "Best For": "platform"}


def _seed(tmp_path, *names, body="Did a thing."):
    cfg = Config(vault_dir=str(tmp_path / "vault"))
    app = Sluice(cfg)
    for n in names:
        app.add_evidence(kind="experience", name=n, fields=_FIELDS, body=body)
    return cfg, app


def _citable(app):
    return sorted(e["title"] for e in app.list_evidence(kind="experience"))


def _call(cfg, answer, args=None, mode="auto", seen=None, with_callback=True):
    from mcp import Client, types

    async def cb(context, params):
        if seen is not None:
            seen.append(params)
        action, content = answer(params)
        return types.ElicitResult(action=action, content=content)

    async def _run():
        kw = {"elicitation_callback": cb} if with_callback else {}
        async with Client(build_server(cfg, write=True), mode=mode, **kw) as client:
            r = await client.call_tool("verify_evidence", args or {"kind": "experience"})
            return json.loads(r.content[0].text)

    return asyncio.run(_run())


def _all(value):
    return lambda p: ("accept", {k: value for k in p.requested_schema["properties"]})


def test_accept_all_promotes_every_entry(tmp_path):
    cfg, app = _seed(tmp_path, "Example alpha", "Example beta")
    out = _call(cfg, _all(True))
    assert out["outcome"] == "completed"
    assert _citable(app) == ["example-alpha", "example-beta"]


def test_unticked_entry_is_not_promoted(tmp_path):
    cfg, app = _seed(tmp_path, "Example alpha", "Example beta")
    out = _call(cfg, lambda p: ("accept", {"entry_1": True, "entry_2": False}))
    assert len(out["promoted"]) == 1 and len(out["skipped"]) == 1
    assert len(_citable(app)) == 1


def test_empty_answer_decline_and_cancel_promote_nothing(tmp_path):
    answers = (lambda p: ("accept", {}), lambda p: ("decline", None),
               lambda p: ("cancel", None))
    # One vault for all three: tests/conftest.py's autouse sandbox pins the vault location,
    # so a per-iteration tmp_path subdirectory would NOT isolate them. Distinct names do,
    # and each answer is checked against only the entry it was shown.
    cfg, app = _seed(tmp_path)
    for i, answer in enumerate(answers):
        app.add_evidence(kind="experience", name=f"Example entry {i}", fields=_FIELDS,
                         body="Did a thing.")
        _call(cfg, answer, args={"kind": "experience", "names": [f"Example entry {i}"]})
        assert _citable(app) == []


def test_entry_edited_between_legs_is_reported_changed_not_promoted(tmp_path):
    cfg, app = _seed(tmp_path, "Example alpha")

    def edit_then_accept(params):
        inbox = next(pathlib.Path(tmp_path / "vault").rglob("_inbox/*.md"))
        inbox.write_text(inbox.read_text() + "\nedited after review\n")
        return "accept", {"entry_1": True}

    out = _call(cfg, edit_then_accept)
    assert out["changed"] == ["example-alpha"] and out["promoted"] == []
    assert _citable(app) == []


def test_form_shows_every_entry_in_full(tmp_path):
    body = "Shipped ``` a fence and <!-- 40% --> literally."
    cfg, app = _seed(tmp_path, "Example alpha", body=body)
    seen = []
    _call(cfg, lambda p: ("cancel", None), seen=seen)
    assert len(seen) == 1
    text = app.store().read_pending_evidence_text("experience", "example-alpha")
    assert text in seen[0].message
    assert "make them citable" in seen[0].message


def test_long_corpus_reports_remaining_and_a_second_call_shows_the_rest(tmp_path):
    names = [f"Example entry {i}" for i in range(12)]
    cfg, app = _seed(tmp_path, *names, body="x" * 1500)
    first = _call(cfg, _all(True))
    assert first["remaining"] > 0 and first["promoted"]
    second = _call(cfg, _all(True))
    assert len(_citable(app)) == len(first["promoted"]) + len(second["promoted"])


def test_unticking_everything_still_lets_the_rest_of_the_queue_be_reached(tmp_path):
    """Unticked entries stay pending, so a bare second call would rebuild the SAME form.
    The report names what was never shown, and calling with those names reaches it."""
    names = [f"Example entry {i}" for i in range(12)]
    cfg, app = _seed(tmp_path, *names, body="x" * 1500)
    seen = []
    first = _call(cfg, _all(False), seen=seen)
    shown_first = {p["description"] for p in seen[0].requested_schema["properties"].values()}
    assert first["remaining"] == len(first["remaining_titles"]) > 0
    assert "names=" in first["detail"]
    seen.clear()
    _call(cfg, _all(False), seen=seen,
          args={"kind": "experience", "names": first["remaining_titles"]})
    shown_second = {p["description"] for p in seen[0].requested_schema["properties"].values()}
    assert shown_second and not (shown_first & shown_second)
    assert _citable(app) == []


def test_entry_deleted_between_legs_is_reported_no_longer_pending(tmp_path):
    cfg, app = _seed(tmp_path, "Example alpha")

    def delete_then_accept(params):
        next(pathlib.Path(tmp_path / "vault").rglob("_inbox/*.md")).unlink()
        return "accept", {"entry_1": True}

    out = _call(cfg, delete_then_accept)
    assert out["no_longer_pending"] == ["example-alpha"]
    assert out["changed"] == [] and out["promoted"] == []
    assert _citable(app) == []


def test_entry_verified_elsewhere_between_legs_is_not_reported_as_edited(tmp_path):
    """Verified through the CLI while the form was open: the entry is fine, it just is
    not pending any more. Calling that "changed since review" would send the user
    looking for an edit that never happened."""
    cfg, app = _seed(tmp_path, "Example alpha")

    def verify_elsewhere_then_accept(params):
        entries = app.pending_evidence_for_review(kind="experience")["entries"]
        app.promote_reviewed_evidence(kind="experience", approved=entries)
        return "accept", {"entry_1": True}

    out = _call(cfg, verify_elsewhere_then_accept)
    assert out["no_longer_pending"] == ["example-alpha"] and out["changed"] == []
    assert _citable(app) == ["example-alpha"]


def test_first_leg_not_found_reaches_the_final_report(tmp_path):
    """The model only ever sees the SECOND leg's report, so whatever the first leg could
    not show must ride through to it."""
    cfg, _ = _seed(tmp_path, "Example alpha")
    out = _call(cfg, _all(True),
                args={"kind": "experience", "names": ["Example alpha", "No such entry"]})
    assert out["promoted"] == ["example-alpha"] and out["not_found"] == ["No such entry"]


def test_unreadable_only_entry_is_nothing_shown_not_nothing_pending(tmp_path, monkeypatch):
    from sluice.core.vault import Vault

    cfg, _ = _seed(tmp_path, "Example alpha")

    def unreadable(self, kind, title):
        raise OSError("simulated unreadable entry")

    monkeypatch.setattr(Vault, "read_pending_evidence_text", unreadable)
    seen = []
    out = _call(cfg, _all(True), seen=seen)
    assert seen == [] and out["outcome"] == "nothing_shown"
    assert [t for t, _ in out["failed"]] == ["example-alpha"]


def test_decline_with_ticked_content_promotes_nothing(tmp_path):
    """A decline must win even if the client still sends the ticked boxes along."""
    cfg, app = _seed(tmp_path, "Example alpha")
    out = _call(cfg, lambda p: ("decline", {"entry_1": True}))
    assert out["outcome"] == "declined" and _citable(app) == []


def test_pending_entries_that_cannot_be_shown_are_not_reported_as_nothing_pending(tmp_path):
    cfg, _ = _seed(tmp_path, "Example alpha")
    out = _call(cfg, _all(True), args={"kind": "experience", "names": ["No such entry"]})
    assert out["outcome"] == "nothing_shown" and out["not_found"] == ["No such entry"]
    assert "no pending entries" not in out["detail"]


def test_nothing_pending_shows_no_form(tmp_path):
    cfg, _ = _seed(tmp_path)
    seen = []
    out = _call(cfg, _all(True), seen=seen)
    assert out["outcome"] == "nothing_pending" and seen == []


def test_unknown_names_are_reported(tmp_path):
    cfg, _ = _seed(tmp_path, "Example alpha")
    out = _call(cfg, _all(True), args={"kind": "experience", "names": ["No such entry"]})
    assert out["not_found"] == ["No such entry"]


def test_legacy_client_and_client_without_elicitation_get_unsupported(tmp_path):
    cfg, app = _seed(tmp_path, "Example alpha")
    assert _call(cfg, _all(True), mode="legacy")["outcome"] == "unsupported_client"
    assert _call(cfg, _all(True), with_callback=False)["outcome"] == "unsupported_client"
    assert _citable(app) == []


def test_unknown_kind_is_a_tool_error_and_writes_nothing(tmp_path):
    from mcp import Client

    cfg, app = _seed(tmp_path, "Example alpha")

    async def _run():
        async with Client(build_server(cfg, write=True)) as client:
            return await client.call_tool("verify_evidence", {"kind": "nope"})

    assert asyncio.run(_run()).is_error is True
    assert _citable(app) == []
