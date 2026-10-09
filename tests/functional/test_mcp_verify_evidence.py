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


def test_form_shows_every_entry_in_full_under_its_checkbox(tmp_path):
    body = "Shipped ``` a fence and <!-- 40% --> literally."
    cfg, app = _seed(tmp_path, "Example alpha", body=body)
    seen = []
    _call(cfg, lambda p: ("cancel", None), seen=seen)
    assert len(seen) == 1
    text = app.store().read_pending_evidence_text("experience", "example-alpha")
    desc = seen[0].requested_schema["properties"]["entry_1"]["description"]
    assert text in desc
    assert "\n" not in seen[0].message and "make them citable" in seen[0].message


def test_long_corpus_reports_not_shown_and_a_second_call_shows_the_rest(tmp_path):
    names = [f"Example entry {i}" for i in range(12)]
    cfg, app = _seed(tmp_path, *names, body="x" * 900)
    first = _call(cfg, _all(True))
    assert first["not_shown"] > 0 and first["promoted"]
    second = _call(cfg, _all(True))
    assert len(_citable(app)) == len(first["promoted"]) + len(second["promoted"])


def test_unticking_everything_still_lets_the_rest_of_the_queue_be_reached(tmp_path):
    """Unticked entries stay pending, so a bare second call would rebuild the SAME form.
    The report names what was never shown, and calling with those names reaches it."""
    names = [f"Example entry {i}" for i in range(12)]
    cfg, app = _seed(tmp_path, *names, body="x" * 900)
    seen = []
    first = _call(cfg, _all(False), seen=seen)
    shown_first = {p["description"].split("\n")[0]
                   for p in seen[0].requested_schema["properties"].values()}
    assert first["not_shown"] == len(first["not_shown_titles"]) > 0
    assert "names=" in first["detail"]
    seen.clear()
    _call(cfg, _all(False), seen=seen,
          args={"kind": "experience", "names": first["not_shown_titles"]})
    shown_second = {p["description"].split("\n")[0]
                    for p in seen[0].requested_schema["properties"].values()}
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


def test_first_leg_set_aside_reaches_the_final_report(tmp_path):
    """An entry too long for any form must still be named in the ONLY report the model
    reads, the second leg's, with its reason."""
    cfg, app = _seed(tmp_path, "Example short")
    app.add_evidence(kind="experience", name="Example huge", fields=_FIELDS, body="z" * 3000)
    out = _call(cfg, _all(True))
    assert out["promoted"] == ["example-short"]
    assert [t for t, _ in out["failed"]] == ["example-huge"]
    assert _citable(app) == ["example-short"]


def test_ticked_entry_unreadable_between_legs_is_reported_failed(tmp_path, monkeypatch):
    from sluice.core.vault import Vault

    cfg, app = _seed(tmp_path, "Example alpha")

    def break_reads_then_accept(params):
        def unreadable(self, kind, title):
            raise OSError("simulated unreadable entry")
        monkeypatch.setattr(Vault, "read_pending_evidence_text", unreadable)
        return "accept", {"entry_1": True}

    out = _call(cfg, break_reads_then_accept)
    assert [t for t, _ in out["failed"]] == ["example-alpha"]
    assert out["promoted"] == out["changed"] == out["no_longer_pending"] == []
    monkeypatch.undo()
    assert _citable(app) == []


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


def test_client_without_elicitation_gets_unsupported(tmp_path):
    cfg, app = _seed(tmp_path, "Example alpha")
    for mode in ("auto", "legacy"):
        out = _call(cfg, _all(True), mode=mode, with_callback=False)
        assert out["outcome"] == "unsupported_client", mode
    assert _citable(app) == []


# The PUSHED route: a pre-2026-07-28 client that declares form elicitation (Cursor, Codex,
# opencode, measured 2026-10-09) is sent the same form as an elicitation/create request
# inside the tool call. `mode="legacy"` is the initialize handshake such a client speaks.
# Every human-in-front property of the input-required route must hold here too.

def test_pushed_form_accept_all_promotes_every_entry(tmp_path):
    cfg, app = _seed(tmp_path, "Example alpha", "Example beta")
    out = _call(cfg, _all(True), mode="legacy")
    assert out["outcome"] == "completed"
    assert _citable(app) == ["example-alpha", "example-beta"]


def test_pushed_form_promotes_only_the_ticked_entry(tmp_path):
    cfg, app = _seed(tmp_path, "Example alpha", "Example beta")
    out = _call(cfg, lambda p: ("accept", {"entry_1": True, "entry_2": False}),
                mode="legacy")
    assert len(out["promoted"]) == 1 and len(out["skipped"]) == 1
    assert len(_citable(app)) == 1


def test_pushed_form_decline_and_cancel_promote_nothing(tmp_path):
    cfg, app = _seed(tmp_path)
    for i, answer in enumerate((lambda p: ("decline", None), lambda p: ("cancel", None),
                                lambda p: ("accept", {}))):
        app.add_evidence(kind="experience", name=f"Example entry {i}", fields=_FIELDS,
                         body="Did a thing.")
        _call(cfg, answer, args={"kind": "experience", "names": [f"Example entry {i}"]},
              mode="legacy")
        assert _citable(app) == []


def test_pushed_form_entry_edited_while_shown_is_changed_not_promoted(tmp_path):
    cfg, app = _seed(tmp_path, "Example alpha")

    def edit_then_accept(params):
        inbox = next(pathlib.Path(tmp_path / "vault").rglob("_inbox/*.md"))
        inbox.write_text(inbox.read_text() + "\nedited after review\n")
        return "accept", {"entry_1": True}

    out = _call(cfg, edit_then_accept, mode="legacy")
    assert out["changed"] == ["example-alpha"] and out["promoted"] == []
    assert _citable(app) == []


def test_pushed_form_shows_every_entry_in_full_under_its_checkbox(tmp_path):
    cfg, app = _seed(tmp_path, "Example alpha", body="Shipped <!-- 40% --> literally.")
    seen = []
    _call(cfg, lambda p: ("cancel", None), seen=seen, mode="legacy")
    assert len(seen) == 1
    text = app.store().read_pending_evidence_text("experience", "example-alpha")
    assert text in seen[0].requested_schema["properties"]["entry_1"]["description"]


def test_pushed_form_the_client_fails_to_show_promotes_nothing(tmp_path):
    """A client that declares form support and then errors on the request (Gemini's
    reported "Method not found" shape) gets unsupported_client, not a tool crash."""
    cfg, app = _seed(tmp_path, "Example alpha")

    def broken(params):
        raise RuntimeError("cannot draw forms after all")

    out = _call(cfg, broken, mode="legacy")
    assert out["outcome"] == "unsupported_client"
    assert _citable(app) == []


def test_unknown_kind_is_a_tool_error_and_writes_nothing(tmp_path):
    from mcp import Client

    cfg, app = _seed(tmp_path, "Example alpha")

    async def _run():
        async with Client(build_server(cfg, write=True)) as client:
            return await client.call_tool("verify_evidence", {"kind": "nope"})

    assert asyncio.run(_run()).is_error is True
    assert _citable(app) == []
