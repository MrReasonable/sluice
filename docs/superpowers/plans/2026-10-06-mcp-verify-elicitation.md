# MCP Evidence Verify via Elicitation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `verify_evidence` MCP tool, under `--write` only. It shows pending evidence entries
in one Claude Code dialog and promotes only the entries the human ticks.

**Architecture:** The tool uses SEP-2322 input-required elicitation:
- **First leg.** The tool returns an `InputRequiredResult` carrying a form and a plain-JSON
  `request_state`.
- **Second leg.** Claude Code retries the call with the user's answer. The tool re-reads each
  approved entry, checks its hash against the text that was shown, and promotes it through a
  new facade method that wraps `Store.verify_evidence`.

The decision logic lives in pure module-level helpers in `sluice/mcpserver.py`. Two facade
methods in `sluice/core/app.py` do the store reads and writes. The CLI's verify path is not
touched.

**Tech Stack:** Python 3.12+, `mcp>=2.0.0,<3` (`[test]` pins 2.2.0), `mcp_types`, pytest. Async
tests use `mcp.Client` in memory, wrapped in `asyncio.run` (no pytest-asyncio).

**Spec:** `docs/superpowers/specs/2026-10-06-mcp-verify-elicitation-design.md`

## Global Constraints

- **Threat model is ACCIDENTAL model error.** The model must not be able to say yes on the
  user's behalf, and the user must see each entry's full text. Do NOT add hardening against a
  hostile client: no signed state, no tamper tests.
- **Tool registration.** The tool is registered only when `write=True`. Its input schema is
  exactly `{kind, names}`.
- **Approval.** Only an explicit `True` in the client's answer approves an entry. A missing key,
  an empty answer, a decline or a cancel promotes nothing.
- **Promotion path.** Promotion goes only through `Store.verify_evidence(kind, title, today=...,
  reviewed=<text>)`, reached via a facade method whose name differs from the Store member (the
  isolation sweep matches calls by attribute name).
- **Imports.** `mcp` and `mcp_types` are imported only inside `build_server()`. `sluice/` stays
  stdlib-only otherwise.
- **Protocol.** Only clients on protocol `2026-07-28` or later that declared form elicitation get
  a form. Every other client gets `outcome: "unsupported_client"`, and nothing is written.
- **Form size.** `_VERIFY_FORM_BUDGET = 8000` characters per form, as a module constant, not
  config.
- **Comments.** Comments explain *why*, at the density of the surrounding code. Never cite a line
  number: cite `file.py::symbol`.
- **Commits.** Conventional commits. Every commit message ends with the line
  `MrReasonable <4990954+MrReasonable@users.noreply.github.com>`.
- **Verification commands:**
  - `.venv/bin/python -m pytest`
  - `.venv/bin/ruff check sluice tests scripts` (if ruff is missing: `.venv/bin/pip install ruff==0.15.21`)
  - Once before any mutation check: `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts`

## Review Focus

1. **A pending entry is renamed or deleted between the two legs.** Expect it reported under
   `changed`, not promoted, and no crash. Covered in Task 3.
2. **The same name passed twice in `names`, or a title and its slug together.** Expect one
   checkbox, not two. Covered in Task 1.
3. **A body containing ```` ``` ```` or HTML comments.** Expect it shown literally inside a
   longer fence. Covered in Task 2.
4. **No pending entries at all.** Expect `outcome: "nothing_pending"` and no form shown. Covered
   in Task 3.
5. **An unknown `kind`.** Expect an SDK tool error, the same as `list_evidence` today. Nothing is
   written and no form is shown. Covered in Task 3.

---

## File Structure

- **Modify `sluice/core/protocols.py`.** Gains `verify_outcome(spec, subject="it")`, moved
  verbatim from `sluice/evidence/commands.py`.
- **Modify `sluice/evidence/commands.py`.** Re-imports `verify_outcome` from protocols, so
  `cli.py`'s existing import keeps working.
- **Modify `sluice/core/app.py`.** Three new `Sluice` methods: `pending_evidence_for_review`,
  `promote_reviewed_evidence` and `evidence_verify_outcome`.
- **Modify `sluice/mcpserver.py`.** Gains the pure helpers, the module-level
  `verify_evidence_step(...)` logic, and the registered tool inside `build_server`.
- **Create `tests/test_evidence_review_facade.py`.** Tests for the facade methods.
- **Create `tests/test_mcp_verify_helpers.py`.** Tests for the pure helpers, with no mcp needed.
- **Create `tests/functional/test_mcp_verify_evidence.py`.** End-to-end tests through `mcp.Client`.
- **Modify the guard tests:**
  - `tests/functional/test_mcp_contract.py`
  - `tests/test_mcpserver.py`
  - `tests/test_ai_setup_contract.py`
- **Modify the prose:**
  - `.rulesync/rules/CLAUDE.md`
  - `docs/AI-SETUP.md`
  - `docs/ARCHITECTURE.md`
  - `docs/USAGE.md`
  - the `sluice/cli.py` help text
  - the `sluice/mcpserver.py` docstrings

---

### Task 1: Facade methods and the shared wording

**Files:**
- Modify: `sluice/core/protocols.py`. Add `verify_outcome` after `EVIDENCE_KINDS`.
- Modify: `sluice/evidence/commands.py`. Replace the `verify_outcome` definition with an import.
- Modify: `sluice/core/app.py`. Add three methods right after `Sluice.list_evidence`.
- Test: `tests/test_evidence_review_facade.py`

**Interfaces:**
- Produces:
  - `Sluice.pending_evidence_for_review(*, kind: str, names: list[str] | None = None) -> dict`
    with keys `entries: list[tuple[str, str]]` (title, exact text), `failed: list[tuple[str, str]]`
    and `not_found: list[str]`.
  - `Sluice.promote_reviewed_evidence(*, kind: str, approved: list[tuple[str, str]], today: str | None = None) -> dict`
    with keys `promoted: list[str]`, `changed: list[str]` and `failed: list[tuple[str, str]]`.
  - `Sluice.evidence_verify_outcome(kind: str, subject: str = "it") -> str`
  - `sluice.core.protocols.verify_outcome(spec, subject="it") -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evidence_review_facade.py
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
    ("experience", "make it citable"), ("skills", "mark it reviewed"),
    ("stories", "mark it reviewed")])
def test_evidence_verify_outcome_is_keyed_on_cited_by_gate(tmp_path, kind, expected):
    assert _app(tmp_path).evidence_verify_outcome(kind) == expected


def test_evidence_verify_outcome_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError, match="experience"):
        _app(tmp_path).evidence_verify_outcome("nope")
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_evidence_review_facade.py -v`

Expected: FAIL with `AttributeError: 'Sluice' object has no attribute 'pending_evidence_for_review'`.

If `_propose` fails first, check that the `Sluice(..., today=...)` keyword and the slug
reduction (`"Example alpha"` → `"example-alpha"`) match `sluice/core/app.py::Sluice.__init__` and
`sluice/core/vault.py::evidence_slug`, and adjust the test.

- [ ] **Step 3: Move `verify_outcome` and add the facade methods**

In `sluice/core/protocols.py`, directly after the `EVIDENCE_KINDS` literal, paste the
`verify_outcome` function exactly as it currently appears in `sluice/evidence/commands.py`
(docstring included). Add one sentence at the end of its docstring:

```
    Lives here, beside `EvidenceKind`, so both the CLI and the MCP verify tool (through
    `Sluice.evidence_verify_outcome`) reach the one wording.
```

In `sluice/evidence/commands.py`, delete the `def verify_outcome` block and change the import
line:

```python
from sluice.core.protocols import EVIDENCE_KINDS, verify_outcome  # noqa: F401 -- re-exported for cli.py
```

In `sluice/core/app.py`, insert these three methods directly after `def list_evidence(...)`:

```python
    def pending_evidence_for_review(self, *, kind: str, names=None) -> dict:
        """The pending entries a reviewer should be shown, each with its EXACT stored text.

        Serves the MCP verify tool (#mcp-verify). The CLI keeps its own per-entry loop in
        verify_evidence_interactive, deliberately untouched: that loop reads each entry just
        before asking about it, while a form shows a batch at once.

        `names` only NARROWS the set, never approves. Each name matches a title verbatim or
        through `evidence_slug`, the same two arms verify_evidence_interactive's `only` uses,
        and a title reached twice (a name and its slug) is offered once. Unmatched names are
        reported in `not_found` rather than absorbed, so "you named nothing pending" stays
        distinguishable from "nothing is pending". One unreadable entry is isolated into
        `failed` rather than sinking the batch, as in the CLI loop."""
        from sluice.core.vault import evidence_slug

        store = self.store()
        pending = [e["title"] for e in store.read_pending_evidence(kind)]
        not_found: list = []
        if names:
            wanted: list = []
            for name in names:
                try:
                    reduced = evidence_slug(name)
                except ValueError:
                    reduced = None  # cannot reduce at all -- only the verbatim arm applies
                hits = [t for t in pending if t == name or t == reduced]
                if not hits:
                    not_found.append(name)
                wanted.extend(hits)
            titles = list(dict.fromkeys(wanted))
        else:
            titles = pending
        entries, failed = [], []
        for title in titles:
            try:
                entries.append((title, store.read_pending_evidence_text(kind, title)))
            except (OSError, ValueError) as e:
                failed.append((title, _evidence_failure_reason(e)))
        return {"entries": entries, "failed": failed, "not_found": not_found}

    def promote_reviewed_evidence(self, *, kind: str, approved, today: str | None = None) -> dict:
        """Promote each (title, text-the-human-was-shown) pair through Store.verify_evidence.

        Named apart from the Store member for the isolation-sweep reason add_evidence gives.
        `reviewed` is the exact text the caller showed, so the store's own compare-and-set
        refuses an entry edited after review; that is reported as `changed`, never as
        promoted. Failures are isolated per entry, as in verify_evidence_interactive."""
        store = self.store()
        clock = self._today or _today
        today = today or clock()
        out = {"promoted": [], "changed": [], "failed": []}
        for title, reviewed in approved:
            try:
                ok = store.verify_evidence(kind, title, today=today, reviewed=reviewed)
            except (OSError, ValueError) as e:
                out["failed"].append((title, _evidence_failure_reason(e)))
                continue
            (out["promoted"] if ok else out["changed"]).append(title)
        return out

    def evidence_verify_outcome(self, kind: str, subject: str = "it") -> str:
        """What verifying buys for `kind`, worded by the one keyed helper
        (core/protocols.py::verify_outcome). A facade method because mcpserver.py may not
        import sluice.core.protocols (the isolation allow-list)."""
        from sluice.core.protocols import EVIDENCE_KINDS, verify_outcome

        if kind not in EVIDENCE_KINDS:
            raise ValueError(
                f"unknown evidence kind {kind!r}; expected one of {sorted(EVIDENCE_KINDS)}")
        return verify_outcome(EVIDENCE_KINDS[kind], subject=subject)
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_evidence_review_facade.py tests/test_evidence_cli.py -v`

Expected: PASS.

If `test_promote_reviewed_isolates_one_failure` shows `example-gone` under `changed` instead of
`failed`, that is the store's real behaviour for an absent entry. Keep the store as it is,
change the assertion to match, and add a one-line comment naming what the store does.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest -q`

Expected: all pass. In particular, every importer of `sluice.evidence.commands.verify_outcome`
still resolves it.

```bash
git add sluice/core/protocols.py sluice/evidence/commands.py sluice/core/app.py tests/test_evidence_review_facade.py
git commit -m "feat(core): facade methods to review and promote a batch of evidence

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 2: Pure helpers for the form, the state and the approval rule

**Files:**
- Modify: `sluice/mcpserver.py`. Add the helpers after `_PROPOSE_EVIDENCE_PENDING_DETAIL`.
- Test: `tests/test_mcp_verify_helpers.py`

**Interfaces:**
- Produces:
  - `_VERIFY_FORM_BUDGET: int = 8000`
  - `_MIN_PROTOCOL = "2026-07-28"`
  - `_can_elicit(protocol_version: str | None, elicitation) -> bool`. Here `elicitation` is the
    client's declared `ElicitationCapability` or `None`.
  - `_fence(body: str) -> str`. Returns a backtick run longer than any run in `body`, at least 3
    long.
  - `_pack_form(entries: list[tuple[str, str]], budget: int) -> tuple[list[tuple[str, str]], int, list[str]]`.
    Returns (entries shown, count remaining, titles too long for any form).
  - `_render_form(shown: list[tuple[str, str]], outcome_phrase: str) -> str`
  - `_form_schema(shown: list[tuple[str, str]]) -> dict`
  - `_encode_state(kind: str, shown: list[tuple[str, str]], remaining: int = 0) -> str`
  - `_decode_state(state: str | None) -> dict | None`. Returns `{"kind": str, "remaining": int, "entries": [[key, title, sha256hex], ...]}`,
    or `None` if the state is missing or malformed.
  - `_approved_keys(content) -> set[str]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_verify_helpers.py
"""Pure helpers behind the MCP verify tool. No mcp import: these run in a bare install."""
import hashlib
import types

from sluice import mcpserver as m


def _cap(form=None, url=None):
    return types.SimpleNamespace(form=form, url=url)


def test_can_elicit_needs_the_modern_protocol_and_form_elicitation():
    assert m._can_elicit("2026-07-28", _cap(form={})) is True
    assert m._can_elicit("2026-07-28", _cap()) is True        # a bare {} counts as form
    assert m._can_elicit("2026-07-28", _cap(url={})) is False  # url-only
    assert m._can_elicit("2026-07-28", None) is False          # declared no elicitation
    assert m._can_elicit("2025-06-18", _cap(form={})) is False  # legacy protocol
    assert m._can_elicit(None, _cap(form={})) is False


def test_fence_is_longer_than_any_backtick_run_in_the_body():
    assert m._fence("plain") == "```"
    assert m._fence("has ``` inside") == "````"
    assert m._fence("has ````` inside") == "``````"


def test_render_form_shows_every_body_in_full_inside_its_own_fence():
    shown = [("example-alpha", "Line with ``` and <!-- 40% --> and [a](b)"),
             ("example-beta", "Second body")]
    msg = m._render_form(shown, "make them citable")
    for title, body in shown:
        assert title in msg
        fence = m._fence(body)
        assert f"{fence}\n{body}\n{fence}" in msg
    assert "make them citable" in msg


def test_form_schema_uses_positional_keys_ticked_by_default():
    schema = m._form_schema([("a title with spaces", "x"), ("entry_9", "y")])
    assert list(schema["properties"]) == ["entry_1", "entry_2"]
    for prop in schema["properties"].values():
        assert prop["type"] == "boolean" and prop["default"] is True
    assert schema["properties"]["entry_1"]["description"] == "a title with spaces"


def test_pack_form_keeps_order_reports_remaining_and_oversize():
    small = [(f"t{i}", "x" * 100) for i in range(5)]
    shown, remaining, oversize = m._pack_form(small, budget=350)
    assert [t for t, _ in shown] == ["t0", "t1"]
    assert remaining == 3 and oversize == []
    shown, remaining, oversize = m._pack_form([("big", "x" * 500), ("ok", "y")], budget=350)
    assert [t for t, _ in shown] == ["ok"] and oversize == ["big"] and remaining == 0


def test_state_round_trips_and_binds_each_key_to_the_shown_text_hash():
    shown = [("example-alpha", "body a"), ("example-beta", "body b")]
    state = m._decode_state(m._encode_state("experience", shown, remaining=3))
    assert state["kind"] == "experience" and state["remaining"] == 3
    assert state["entries"] == [
        ["entry_1", "example-alpha", hashlib.sha256(b"body a").hexdigest()],
        ["entry_2", "example-beta", hashlib.sha256(b"body b").hexdigest()]]


def test_decode_state_returns_none_for_missing_or_malformed():
    for bad in (None, "", "not json", '{"kind": 1}', '{"kind": "x", "entries": "no"}'):
        assert m._decode_state(bad) is None


def test_only_an_explicit_true_approves():
    assert m._approved_keys({"entry_1": True, "entry_2": False}) == {"entry_1"}
    assert m._approved_keys({}) == set()
    assert m._approved_keys(None) == set()
    assert m._approved_keys({"entry_1": 1, "entry_2": "true"}) == set()
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_mcp_verify_helpers.py -v`

Expected: FAIL with `AttributeError: module 'sluice.mcpserver' has no attribute '_can_elicit'`.

- [ ] **Step 3: Implement the helpers**

Add `import re` to `sluice/mcpserver.py`'s stdlib imports (`hashlib` and `json` are already
there). Then add:

```python
# ── verify_evidence helpers ─────────────────────────────────────────────────
# The verify step exists so the MODEL cannot accidentally make its own claims citable:
# a human sees each entry's full text, and only the human's tick approves it. These
# helpers are pure so tests drive them without mcp; the tool in build_server only
# wires them to the protocol.

# One form's message, in characters. Bounds what a client dialog can usefully show;
# not a user preference, so a constant rather than config. The pre-merge live check
# (see the spec) is what confirms Claude Code shows this much without cutting it.
_VERIFY_FORM_BUDGET = 8000

# SEP-2322 input-required results exist from this protocol on. Claude Code 2.1.291
# negotiates it, and cannot take a server-PUSHED elicitation at all (NoBackChannelError,
# measured 2026-10-06), so this is the only mechanism that reaches the user there. An
# older client cannot even parse an InputRequiredResult, so it must never be sent one.
_MIN_PROTOCOL = "2026-07-28"


def _can_elicit(protocol_version, elicitation) -> bool:
    """True when this client can show an input-required form. A bare `elicitation: {}`
    counts as form support (the library's own rule, mcp/server/mcpserver/resolve.py);
    a declaration naming only `url` does not. ISO dates compare correctly as strings."""
    if not protocol_version or protocol_version < _MIN_PROTOCOL or elicitation is None:
        return False
    form = getattr(elicitation, "form", None)
    url = getattr(elicitation, "url", None)
    return form is not None or url is None


def _fence(body: str) -> str:
    """A backtick fence one longer than any run inside `body` (CommonMark), so nothing
    in the body can close it and markdown inside shows literally."""
    longest = max((len(r) for r in re.findall(r"`+", body)), default=0)
    return "`" * max(3, longest + 1)


def _entry_block(index: int, title: str, body: str) -> str:
    fence = _fence(body)
    return f"entry_{index}: {title}\n{fence}\n{body}\n{fence}\n"


def _pack_form(entries, budget: int):
    """Take entries in order while the rendered form stays within `budget`. An entry
    too big for a form on its own is never truncated -- truncating would show the human
    less than they approve -- it is reported for the CLI instead."""
    shown, oversize, used = [], [], 0
    remaining = 0
    for i, (title, body) in enumerate(entries):
        size = len(_entry_block(len(shown) + 1, title, body))
        if size > budget:
            oversize.append(title)
            continue
        if used + size > budget:
            remaining = sum(1 for t, b in entries[i:]
                            if len(_entry_block(1, t, b)) <= budget)
            break
        shown.append((title, body))
        used += size
    return shown, remaining, oversize


def _render_form(shown, outcome_phrase: str) -> str:
    head = (f"Review these {len(shown)} evidence entries. Ticked entries are verified, "
            f"which will {outcome_phrase}. Untick anything that is wrong or that you "
            f"did not actually do.\n\n")
    return head + "\n".join(_entry_block(i, t, b) for i, (t, b) in enumerate(shown, 1))


def _form_schema(shown) -> dict:
    """Positional keys: a title is free text and does not belong in a schema key."""
    return {"type": "object", "properties": {
        f"entry_{i}": {"type": "boolean", "default": True, "description": title}
        for i, (title, _) in enumerate(shown, 1)}}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _encode_state(kind: str, shown, remaining: int = 0) -> str:
    """Plain JSON, deliberately unsigned: the threat is the model accidentally approving,
    not a client forging protocol state (see the spec's threat model)."""
    return json.dumps({"kind": kind, "remaining": remaining, "entries": [
        [f"entry_{i}", title, _sha(body)] for i, (title, body) in enumerate(shown, 1)]})


def _decode_state(state):
    try:
        data = json.loads(state) if state else None
    except ValueError:
        return None
    if (not isinstance(data, dict) or not isinstance(data.get("kind"), str)
            or not isinstance(data.get("entries"), list)
            or not all(isinstance(e, list) and len(e) == 3 for e in data["entries"])):
        return None
    return data


def _approved_keys(content) -> set:
    """Only an explicit True approves. A client that omits an unticked key, or answers
    with an empty form, must approve nothing -- never fall back to the schema default."""
    if not isinstance(content, dict):
        return set()
    return {k for k, v in content.items() if v is True}
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_mcp_verify_helpers.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sluice/mcpserver.py tests/test_mcp_verify_helpers.py
git commit -m "feat(mcp): pure helpers for the evidence verify form

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 3: The `verify_evidence` tool, end to end

**Files:**
- Modify: `sluice/mcpserver.py`. Add `verify_evidence_step(...)` after `propose_evidence`, and
  register the tool inside `build_server` under `if write:`.
- Test: `tests/functional/test_mcp_verify_evidence.py`

**Interfaces:**
- Consumes: Task 1's three facade methods and Task 2's helpers.
- Produces:
  - `verify_evidence_step(sluice, *, kind, names, protocol_version, elicitation, responses, state) -> dict`.
    It returns one of two shapes:
    - `{"ask": {"message": str, "schema": dict, "state": str}}`
    - a report: `{"outcome", "promoted", "changed", "skipped", "failed", "remaining", "not_found", "detail"}`
  - A registered tool named `verify_evidence`, whose input schema is exactly `{kind, names}`.

- [ ] **Step 1: Write the failing end-to-end tests**

```python
# tests/functional/test_mcp_verify_evidence.py
"""verify_evidence end to end through the real SDK, in memory. `mcp.Client`'s default
`auto` mode speaks the 2026-07-28 protocol and drives the SEP-2322 input-required loop
with `elicitation_callback` standing in for the human -- the same loop Claude Code runs.
Promotions are read back from the store, never taken from the tool's own report."""
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
    for i, answer in enumerate(answers):
        cfg, app = _seed(tmp_path / str(i), "Example alpha")
        _call(cfg, answer)
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
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `.venv/bin/python -m pytest tests/functional/test_mcp_verify_evidence.py -v`

Expected: FAIL. The tool `verify_evidence` is unknown, so the SDK raises or returns an
is_error result.

- [ ] **Step 3: Implement `verify_evidence_step`**

Add this after `propose_evidence(...)` in `sluice/mcpserver.py`:

```python
def verify_evidence_step(sluice: Sluice, *, kind: str, names, protocol_version,
                         elicitation, responses, state) -> dict:
    """One leg of the verify loop, with the protocol stripped off so tests reach it
    without mcp. `responses` is None on the first leg; on the retry it is the
    client's answer to the one form, keyed "verify" (see build_server).

    First leg: read the pending entries and return {"ask": ...} carrying the form and
    a state that binds each checkbox to the hash of the exact text shown. Second
    leg: promote each entry the human ticked whose CURRENT text still hashes to what
    they saw; anything edited since is reported `changed`, never promoted."""
    report = {"outcome": "", "promoted": [], "changed": [], "skipped": [], "failed": [],
              "remaining": 0, "not_found": [], "detail": ""}
    # Raises ValueError for an unknown kind before anything is read or shown -- the
    # same SDK tool error list_evidence gives for one.
    phrase = sluice.evidence_verify_outcome(kind, subject="them")
    if not _can_elicit(protocol_version, elicitation):
        report["outcome"] = "unsupported_client"
        report["detail"] = (f"this client cannot show a review form -- run "
                            f"`job-sluice {kind} verify` in a terminal instead")
        return report

    if responses is None:
        found = sluice.pending_evidence_for_review(kind=kind, names=names)
        report["not_found"], report["failed"] = found["not_found"], found["failed"]
        shown, remaining, oversize = _pack_form(found["entries"], _VERIFY_FORM_BUDGET)
        report["failed"] += [(t, f"too long for a review form -- run `job-sluice {kind} "
                                 f"verify` for this one") for t in oversize]
        if not shown:
            report["outcome"] = "nothing_pending"
            report["detail"] = "no pending entries to review"
            return report
        return {"ask": {"message": _render_form(shown, phrase),
                        "schema": _form_schema(shown),
                        "state": _encode_state(kind, shown, remaining)}}

    decoded = _decode_state(state)
    action = getattr(responses, "action", None)
    if decoded is None or decoded["kind"] != kind:
        report["outcome"] = "invalid_state"
        report["detail"] = "the review form's state did not come back intact; nothing was verified"
        return report
    titles = [title for _, title, _ in decoded["entries"]]
    report["remaining"] = int(decoded.get("remaining", 0))
    if action != "accept":
        report["outcome"] = "declined" if action == "decline" else "cancelled"
        report["skipped"] = titles
        report["detail"] = "nothing was verified"
        return report
    ticked = _approved_keys(getattr(responses, "content", None))
    approved_titles = {title: sha for key, title, sha in decoded["entries"] if key in ticked}
    report["skipped"] = [t for t in titles if t not in approved_titles]
    current = dict(sluice.pending_evidence_for_review(
        kind=kind, names=list(approved_titles))["entries"]) if approved_titles else {}
    approved = []
    for title, sha in approved_titles.items():
        text = current.get(title)
        if text is None or _sha(text) != sha:
            report["changed"].append(title)
        else:
            approved.append((title, text))
    result = sluice.promote_reviewed_evidence(kind=kind, approved=approved)
    report["promoted"] = result["promoted"]
    report["changed"] += result["changed"]
    report["failed"] += result["failed"]
    report["outcome"] = "completed"
    report["detail"] = (f"verified {len(report['promoted'])}, left "
                        f"{len(report['skipped'])} unticked, {len(report['changed'])} changed "
                        f"since review, {len(report['failed'])} failed")
    return report
```

- [ ] **Step 4: Register the tool**

Inside `build_server`, change the import block to:

```python
    try:
        from mcp.server.mcpserver import Context, MCPServer
        from mcp_types import (
            CallToolResult, ElicitRequest, ElicitRequestFormParams, InputRequiredResult,
            TextContent)
    except ImportError as e:
```

Then add, at the end of the `if write:` block:

```python
        # The one tool that returns an InputRequiredResult (SEP-2322). Still a SYNC def,
        # dispatched to a worker thread like every other tool here; the protocol's retry
        # carries the human's answer back in as ctx.input_responses.
        @mcp_server.tool(name="verify_evidence")
        def verify_evidence_tool(kind: str, names: list[str] | None = None,
                                 ctx: Context = None) -> CallToolResult | InputRequiredResult:
            """Show pending evidence entries ('experience', 'skills', 'stories') to the
            human in one review form and verify only the ones they tick. `names`
            narrows which pending entries are offered; it never approves anything.
            There is no argument that approves on the human's behalf. Clients that
            cannot show a form get outcome="unsupported_client"."""
            responses = ctx.input_responses
            caps = ctx.session.client_capabilities
            out = verify_evidence_step(
                sluice, kind=kind, names=names, protocol_version=ctx.protocol_version,
                elicitation=getattr(caps, "elicitation", None),
                responses=None if responses is None else responses.get("verify"),
                state=ctx.request_state)
            if "ask" in out:
                ask = out["ask"]
                return InputRequiredResult(
                    input_requests={"verify": ElicitRequest(params=ElicitRequestFormParams(
                        mode="form", message=ask["message"],
                        requested_schema=ask["schema"]))},
                    request_state=ask["state"])
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(out))])
```

**`ctx.protocol_version`.** If it does not exist on the pinned `mcp==2.2.0`, use
  `ctx.session.protocol_version`. Check with
  `.venv/bin/python -c "from mcp.server.mcpserver import Context; print(hasattr(Context,'protocol_version'))"`.

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `.venv/bin/python -m pytest tests/functional/test_mcp_verify_evidence.py tests/test_mcp_verify_helpers.py -v`

Expected: PASS.

If `Context = None` makes `ctx` appear in the tool's input schema, or the SDK rejects the
`None` default, drop the default (`ctx: Context`) and re-run. Task 4's schema pin catches
either way.

If a sync tool cannot return `InputRequiredResult`, make only this tool `async def` and
leave the body unchanged. Every call it makes is in-process and short, apart from the vault
reads; add a comment saying so.

- [ ] **Step 6: Run the mutation check on the two load-bearing lines**

```bash
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
git stash list >/dev/null  # tree must be committed or stashed before mutating
```

First mutation:
1. Delete `if v is True` from `_approved_keys`, so it reads `return {k for k, v in content.items()}`.
2. Run `.venv/bin/python -m pytest tests/test_mcp_verify_helpers.py::test_only_an_explicit_true_approves tests/functional/test_mcp_verify_evidence.py::test_unticked_entry_is_not_promoted -q`.
   Expected: FAIL.
3. Restore with `git checkout sluice/mcpserver.py`.

Second mutation:
1. Delete `or _sha(text) != sha` from the second leg.
2. Run `.venv/bin/python -m pytest tests/functional/test_mcp_verify_evidence.py::test_entry_edited_between_legs_is_reported_changed_not_promoted -q`.
3. If it still PASSES, the store's own compare-and-set is catching the edit (the edited entry
   lands in `changed` through `promote_reviewed_evidence`). Record that in a comment on the
   hash check: it is defence in depth, and the store's compare-and-set is the primary guard.
4. Restore with `git checkout sluice/mcpserver.py`.

- [ ] **Step 7: Commit**

```bash
git add sluice/mcpserver.py tests/functional/test_mcp_verify_evidence.py tests/test_mcp_verify_helpers.py
git commit -m "feat(mcp): verify_evidence shows pending entries in one review form

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 4: Update the roster and import guards

**Files:**
- Modify: `tests/functional/test_mcp_contract.py`. Specifically
  `test_tools_list_under_write_true_returns_every_tool_with_exact_schemas`.
- Modify: `tests/test_mcpserver.py`. Specifically `_EXPECTED_EVIDENCE_WRITE_TOOLS`, the
  docstring of `test_only_propose_evidence_and_only_at_write_true_is_registered`, and
  `test_mcp_imported_nowhere_outside_build_server`.
- Modify: `tests/test_ai_setup_contract.py`. Specifically
  `test_no_mcp_tool_can_verify_evidence_at_any_write_level`.

- [ ] **Step 1: Run the suite to see which guards now fail**

Run: `.venv/bin/python -m pytest tests/functional/test_mcp_contract.py tests/test_mcpserver.py tests/test_ai_setup_contract.py -q`

Expected: FAIL in those three guards, each reporting `verify_evidence` at write=True.

- [ ] **Step 2: Update `test_mcp_contract.py`**

In `test_tools_list_under_write_true_returns_every_tool_with_exact_schemas`:
- Replace the `assert not [n for n in by_name if "verify" in n]` clause and its comment with:

```python
    # Exactly ONE promotion tool, and only at --write. It promotes only what the human
    # ticks in a client-shown form (see sluice/mcpserver.py::verify_evidence_step); the
    # exact-set `==` below is what catches a second one under any other name.
    assert [n for n in by_name if "verify" in n] == ["verify_evidence"]
```

- Add `"verify_evidence"` to the exact set.
- Add after the other schema pins:

```python
    assert set(by_name["verify_evidence"].input_schema["properties"]) == {"kind", "names"}, (
        "verify_evidence must take no argument that could approve on the human's behalf")
```

Leave the write=False exact-set test unchanged: `verify_evidence` must stay absent there.

- [ ] **Step 3: Update `test_mcpserver.py`**

Set `_EXPECTED_EVIDENCE_WRITE_TOOLS = {False: set(), True: {"propose_evidence", "verify_evidence"}}`.

Rewrite the sweep's docstring paragraph that says "A VERIFY tool must never exist at either
level" so that it says this instead:
- `verify_evidence` exists only at `--write`;
- it promotes only entries the human ticks in a client-shown form;
- any other verify-shaped name at either level is still a failure.

In `test_mcp_imported_nowhere_outside_build_server`, widen the mcp-name matcher so that
`mcp_types` counts as mcp-named. Find the predicate that tests a module name against
`"mcp"` / `"mcp."` and add `or name == "mcp_types" or name.startswith("mcp_types.")`. Then
add a synthetic row next to its existing synthetic rows, asserting that a module-scope
`from mcp_types import TextContent` is reported.

- [ ] **Step 4: Update `test_ai_setup_contract.py`**

Rename `test_no_mcp_tool_can_verify_evidence_at_any_write_level` to
`test_only_verify_evidence_can_verify_and_only_at_write`. Then:

- Change the body to assert that, at `write=False`, `offenders == set()`.
- At `write=True`, assert `offenders == {"verify_evidence"}`.
- Move the anti-vacuity plant to `"bulk_verify"`, and assert it is caught.
- Rewrite the docstring to match the new AI-SETUP rule 2 (Task 5).

- [ ] **Step 5: Run the suite to confirm it passes**

Run: `.venv/bin/python -m pytest -q`

Expected: all pass, except `tests/test_ai_setup_contract.py` rows that pin AI-SETUP's wording.
Task 5 fixes those.

- [ ] **Step 6: Commit**

```bash
git add tests/functional/test_mcp_contract.py tests/test_mcpserver.py tests/test_ai_setup_contract.py
git commit -m "test(mcp): pin verify_evidence as the one promotion tool, write-only

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 5: Prose that the new tool makes false

**Files:** whatever this grep returns, plus the files listed under it.

- [ ] **Step 1: Enumerate the claims**

```bash
git grep -n -i -E "no tool .*(verif|promot)|nothing that VERIFIES|verify (tool|counterpart)|promotes one|not possible through MCP|isolation sweep forbids|no write or verify|interactive-only|stays a human action at a prompt" -- sluice docs .rulesync README.md tests
```

Work through every hit. These hits are already known:

- **`sluice/mcpserver.py`:**
  - The module docstring's tool list gains `verify_evidence`.
  - `list_evidence`'s docstring paragraph ("still has no VERIFY counterpart at any privilege
    level ...") now says the counterpart is `verify_evidence`, which promotes only what a
    human ticks in a client-shown form.
  - The registered `list_evidence_tool` description ("There is deliberately no tool here that
    VERIFIES one") now names `verify_evidence` instead.
  - `propose_evidence`'s docstring ("There is deliberately no companion VERIFY tool").
  - The registered `propose_evidence_tool` description ("there is deliberately no tool here
    that promotes one").
  - `_PROPOSE_EVIDENCE_PENDING_DETAIL` becomes: `"proposed only -- this entry is NOT citable by the CV fabrication gate and is not visible to list_evidence's default view. A human reviews it with verify_evidence (a form they tick) or `job-sluice {kind} verify`."`
  - The comment above that string: `verify_outcome` now lives in `core/protocols.py`,
    reachable through `Sluice.evidence_verify_outcome`.
  - `build_server`'s docstring gains `verify_evidence` in its write-tool list.
- **`sluice/cli.py`:** the `mcp serve --write` help text, if it lists the write tools.
- **`.rulesync/rules/CLAUDE.md`:** the "Citability has ONE writer" section. Rewrite these
  sentences:
  - "The MCP server exposes `list_evidence` at every level and ... nothing that VERIFIES, at
    any level."
  - "do not read this one as licence to add the verify tool beside it."
  - "a second PROMOTION path — a bulk verifier, an MCP verify tool, a `--yes` — is not a
    convenience but a new trust root".

  Replace them with this paragraph, which asserts only what Tasks 1–4 built:

  > Since the MCP verify tool, there are TWO ways to reach `Store.verify_evidence`, and both go
  > through a human: the CLI's `[y/N]` loop, and `verify_evidence` under `mcp serve --write`. The
  > second shows every pending entry in full in a client-rendered form (SEP-2322 input-required
  > elicitation) and promotes only entries the human ticked with an explicit `true`, whose current
  > text still hashes to what was shown. The tool takes no argument that could approve on the
  > human's behalf (its input is exactly `{kind, names}`, pinned in
  > `tests/functional/test_mcp_contract.py`). What this guards against is the MODEL accidentally
  > making its own claims citable; it is not hardened against a client or hook configured to
  > answer the form for the user, which is the user's own tooling acting for them. A bulk flag
  > or a `--yes` on the CLI is still a promotion path with no human in it, and still must not ship.

  Then run `npm ci --ignore-scripts && npm run rulesync`.
- **`docs/AI-SETUP.md`:**
  - Rule 2's sentence "the MCP server exposes no tool that verifies at any `--write` level"
    becomes: "under `mcp serve --write` the `verify_evidence` tool shows the user a review form
    in their client; only entries they tick are verified, and you cannot answer that form for
    them."
  - "`job-sluice experience verify` is theirs to run" becomes "verification is theirs: call
    `verify_evidence` and let them tick the form, or have them run `job-sluice experience
    verify`".
  - The division-of-labour row "Verify evidence | never | only they can" stays. Add "(through
    the `verify_evidence` form, or the CLI)" to the human column.
- **`docs/ARCHITECTURE.md` and `docs/USAGE.md`:** update every MCP tool roster, the "standing
  property" sentence, and "not possible through MCP" so they name `verify_evidence` and its
  human-ticks-the-form rule.

- [ ] **Step 2: Run the full suite and lint**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check sluice tests scripts
```

Expected: all pass and ruff is clean. If `tests/test_docs_claims.py` or
`tests/test_ai_setup_contract.py` pin a phrase you changed, update the pinned phrase to the new
true wording. Never delete a pin to make it pass.

- [ ] **Step 3: Re-run the claim grep**

Run the grep from Step 1 again. Expected: every remaining hit is either still true (for example,
CLI-only statements about `--yes`) or historical (`docs/superpowers/`).

- [ ] **Step 4: Commit**

```bash
git add -A sluice docs .rulesync tests
git commit -m "docs(mcp): describe verify_evidence wherever MCP was said to have no verifier

MrReasonable <4990954+MrReasonable@users.noreply.github.com>"
```

---

### Task 6: Live check in Claude Code (human-run, before merge)

The automated tests prove our side of the protocol. Only a person can confirm what Claude Code
renders.

- [ ] **Step 1: Make a scratch install**

```bash
export SLUICE_CONFIG="$(mktemp -d)/config.yaml"
SCRATCH_VAULT="$(mktemp -d)/vault"
.venv/bin/job-sluice init --no-input --vault "$SCRATCH_VAULT"
claude mcp add sluice-verify-check -- "$PWD/.venv/bin/job-sluice" mcp serve --write
```

`claude mcp add` must be run with `SLUICE_CONFIG` exported. If the server does not inherit it,
add `-e SLUICE_CONFIG="$SLUICE_CONFIG"`.

- [ ] **Step 2: In an interactive Claude Code session, propose three entries and verify**

Ask Claude to use `propose_evidence` for three synthetic `experience` entries. One should have a
body containing `<!-- 40% -->`, a markdown link, and a ```` ``` ```` line. Then ask it to call
`verify_evidence`. In the dialog:

- confirm every body is visible in full, and that the comment, the link and the fence show
  literally;
- confirm the boxes start ticked;
- untick one, then Accept.

Then run `job-sluice experience list`. Expected: exactly the two ticked entries are listed.

- [ ] **Step 3: Clean up**

```bash
claude mcp remove sluice-verify-check
```

If the dialog renders markup, truncates a body, or shows no checkboxes, stop and bring it back
to the spec. Do not merge.

---

## Self-review

- **Spec coverage:**
  - The tool, its schema and `--write` only: Tasks 3 and 4.
  - The unsupported client: Task 3.
  - Fenced full text and ticked by default: Tasks 2 and 3.
  - Explicit-true approval: Tasks 2 and 3.
  - The hash-checked re-read: Task 3.
  - The `remaining` overflow and an entry too long for any form: Tasks 2 and 3.
  - `cited_by_gate` wording: Task 1.
  - The guards: Task 4. The prose: Task 5. The live check: Task 6.
  - The mutation checks: Task 3, Step 6.
- **Type consistency:** `pending_evidence_for_review` returns `entries` as a list of tuples. It
  is used as `dict(...)` in Task 3, which is valid for 2-tuples. Task 1 defines
  `promote_reviewed_evidence` with a `changed` key, and Task 3 merges that same key.
- **Known judgement points left to the implementer, each with its fallback stated:**
  - whether `ctx.protocol_version` exists at the pin;
  - the sync vs. async tool;
  - how the `Context` default renders in the schema;
  - whether the hash-check mutation is caught by the store.
