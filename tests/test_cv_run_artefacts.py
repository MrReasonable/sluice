"""`cv run` keeps what a badly tailored CV needs in order to be diagnosed afterwards.

Before this, a run kept the PDF and nothing else. The prompt the composer was sent (which
already carries the bundle, the job description and the rules), each attempt's composed
text, and the gate and audit findings were all discarded once the result line printed. So a
CV that came out poorly tailored could not be explained after the fact -- which entries the
bundle held, what the model returned on each attempt, what the audit flagged -- and a run
that rendered NOTHING (a gate failure) left nothing on disk at all, although that is the run
most in need of a diagnosis.

These drive the real `run_one`/`run_batch` over `tests/test_cv_engine.py`'s own fakes, with
`output_dir` inside `tmp_path`, and read the files back. The file NAMES are asserted as
literals rather than imported from the module that writes them: they are the contract
`docs/USAGE.md` documents, so a rename must turn this file red rather than follow along.
"""
import json
import os
from datetime import datetime

from sluice.core.backends import BackendError, Completion
from sluice.cv.engine import run_batch, run_one
from tests.test_cv_engine import (
    CLEAN_CV, ENTRIES, HARD_DIRTY_CV, STYLE_DIRTY_CV, FakeCache, FakeRenderer, FakeVault,
    Note, _cfg)

_LEAD_FM = {"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}
# `cv/engine.py::_slug` of the company and role above: the per-lead working directory the
# renderer already writes its PDF into. A literal, for the same reason as the file names.
_LEAD_DIR = "example-foundry-analyst"
# Fails the citation gate on every attempt, so the run ends `skipped-gate` after a retry.
_UNCITED_CV = CLEAN_CV.replace("- Grew team from 3 to 8 [EF1]", "- Grew team from 3 to 8")
_UNSUPPORTED = "unsupported\tMotivated by placeholder\tNONE"


def _cfg_at(tmp_path, *, serve=False):
    cfg = _cfg()
    cfg.output_dir = str(tmp_path / "cv-output")
    # "" is the engine's own no-serve idiom (see test_no_serve_renders_but_does_not_mark_lead).
    cfg.served_dir = str(tmp_path / "cv-served") if serve else ""
    return cfg


def _lead_dir(tmp_path):
    return tmp_path / "cv-output" / _LEAD_DIR


def _text(path):
    # Bytes then decode, never `read_text`: that translates newlines, and every comparison
    # below is about the exact text the engine had in hand.
    return path.read_bytes().decode("utf-8")


def _run_record(tmp_path):
    return json.loads(_text(_lead_dir(tmp_path) / "run.json"))


class _ScriptedBackend:
    """One scripted reply per COMPOSE call (a draft, or an exception to raise), and every
    compose prompt recorded exactly as the backend received it -- which is what the prompt
    artefact is compared against. Routing mirrors test_cv_engine.py's FakeBackend: a compose
    prompt carries "SOURCE BUNDLE" and not "auditing"; an audit prompt carries both. Running
    past the end of the script raises IndexError, so a compose that must not happen is loud.
    """

    def __init__(self, replies, audit_out="supported\tx\tEF1"):
        self.replies = list(replies)
        self.audit_out = audit_out
        self.last_backend = "primary"
        self.compose_prompts = []

    def complete(self, prompt):
        if "SOURCE BUNDLE" in prompt and "auditing" not in prompt:
            self.compose_prompts.append(prompt)
            reply = self.replies[len(self.compose_prompts) - 1]
            if isinstance(reply, Exception):
                raise reply
            return Completion(reply)
        return Completion(self.audit_out)


class _PdfRenderer(FakeRenderer):
    """FakeRenderer that also WRITES a PDF where the `template` renderer does, so the real
    `render.serve` has a file to copy and `served_dir` can be inspected afterwards."""

    def render(self, cv_text, out_dir, *, neutral_name="CV.pdf"):
        super().render(cv_text, out_dir, neutral_name=neutral_name)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, neutral_name)
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4\n%%EOF\n")
        return path


class _BrokenRenderer(FakeRenderer):
    """Raises after recording, the way a WeasyPrint failure crosses the seam."""

    def render(self, cv_text, out_dir, *, neutral_name="CV.pdf"):
        super().render(cv_text, out_dir, neutral_name=neutral_name)
        raise RuntimeError("renderer boom")


def _run(tmp_path, replies, *, cfg=None, renderer=None, note=None, **kw):
    note = note or Note(dict(_LEAD_FM))
    backend = replies if isinstance(replies, _ScriptedBackend) else _ScriptedBackend(replies)
    renderer = renderer or FakeRenderer()
    result = run_one(note, FakeVault(ENTRIES, notes=[note]), cfg or _cfg_at(tmp_path),
                     backend, FakeCache(), renderer=renderer, **kw)
    return result, backend, renderer


def test_the_shared_engine_config_writes_inside_the_test_sandbox(tmp_path):
    """`tests/test_cv_engine.py::_cfg` feeds hundreds of engine runs across several test
    files, and every one of them that reaches composition now writes under its
    `output_dir`. That was a fixed `/tmp/cvout`: outside the per-test sandbox, and shared by
    every test in the session, so one test's leftovers were another's input."""
    out, box = os.path.realpath(_cfg().output_dir), os.path.realpath(tmp_path)
    assert os.path.commonpath([out, box]) == box, out


def test_a_rendered_run_keeps_the_prompt_the_draft_and_a_run_record_beside_the_pdf(tmp_path):
    cfg = _cfg_at(tmp_path, serve=True)
    r, be, rend = _run(tmp_path, [CLEAN_CV], cfg=cfg, renderer=_PdfRenderer())
    assert r.status == "rendered"
    assert r.artefacts_failed is False

    lead = _lead_dir(tmp_path)
    # The EXACT prompt the backend received, not a rebuild of it: a second build with its
    # own argument list could drift from the one actually sent, and a diagnostic that
    # differs from what the model saw is worse than none.
    assert _text(lead / "prompt.attempt-1.txt") == be.compose_prompts[0]
    assert _text(lead / "cv.attempt-1.md") == CLEAN_CV
    assert _text(lead / "cv.rendered.md") == rend.rendered[0]

    run = _run_record(tmp_path)
    assert run["status"] == "rendered"
    assert run["dry_run"] is False
    assert run["lead"] == "Acme - Analyst"          # the store-issued slug, never the ref
    assert run["attempt_count"] == 1
    assert run["retained_attempt"] == 1
    assert run["attempts"] == [{"attempt": 1, "compose_error": None}]
    assert run["backend"] == "primary"
    assert run["dossier_failed"] is False
    assert run["skills_unreadable"] is False
    assert run["bundle_entry_ids"] == ["EF1"]
    assert run["violations"] == r.violations == []
    assert run["audit_flags"] == r.audit_flags
    assert run["slop"] == r.slop
    assert run["voice_flags"] == r.voice_flags
    assert r.served and run["served"] == r.served
    assert os.path.basename(run["rendered_pdf"]) == "CV.pdf"
    assert run["error"] is None
    assert run["artefact_errors"] == []
    assert sorted(run["files"]) == ["cv.attempt-1.md", "cv.rendered.md",
                                    "prompt.attempt-1.txt"]
    assert (datetime.fromisoformat(run["started_at"])
            <= datetime.fromisoformat(run["finished_at"]))
    assert run["run_id"]

    # Never published: served_dir holds the served copy of the PDF and nothing else. The
    # prompt carries the whole bundle and the contact block, which is not something to
    # put wherever the served PDFs are exposed from.
    assert os.listdir(cfg.served_dir) == [r.served]


def test_a_gate_failure_that_renders_nothing_still_leaves_both_attempts_to_read(tmp_path):
    r, be, rend = _run(tmp_path, [_UNCITED_CV, _UNCITED_CV])
    assert r.status == "skipped-gate"
    assert rend.rendered == []

    lead = _lead_dir(tmp_path)
    assert _text(lead / "prompt.attempt-1.txt") == be.compose_prompts[0]
    assert _text(lead / "prompt.attempt-2.txt") == be.compose_prompts[1]
    # Attempt 2's prompt is the RETRY prompt carrying attempt 1's findings -- the only place
    # those survive, since the result line prints the last attempt's findings alone.
    assert "YOUR PREVIOUS DRAFT FAILED THE GATE" in _text(lead / "prompt.attempt-2.txt")
    assert _text(lead / "cv.attempt-1.md") == _UNCITED_CV
    assert _text(lead / "cv.attempt-2.md") == _UNCITED_CV
    assert not (lead / "cv.rendered.md").exists()

    run = _run_record(tmp_path)
    assert run["status"] == "skipped-gate"
    assert run["attempt_count"] == 2
    assert run["retained_attempt"] is None
    assert run["violations"] == r.violations
    assert any("UNCITED" in v for v in run["violations"])
    assert run["served"] is None
    assert run["rendered_pdf"] is None


def test_a_held_run_records_the_audit_verdict_that_held_it(tmp_path):
    r, _be, _rend = _run(tmp_path, _ScriptedBackend([CLEAN_CV], audit_out=_UNSUPPORTED),
                         cfg=_cfg_at(tmp_path, serve=True), renderer=_PdfRenderer())
    assert r.status == "needs-signoff"

    run = _run_record(tmp_path)
    assert run["status"] == "needs-signoff"
    assert run["audit_flags"] == r.audit_flags
    assert any(f.startswith("unsupported\t") for f in run["audit_flags"])
    assert r.served and run["served"] == r.served
    assert _text(_lead_dir(tmp_path) / "cv.rendered.md") == CLEAN_CV


def test_the_rendered_text_is_the_retained_draft_even_when_a_later_attempt_was_worse(
        tmp_path):
    """Attempt 1 clears the HARD gate carrying a style finding, so a retry runs; attempt 2
    comes back HARD-dirty, so the engine renders attempt 1. A reader comparing the PDF with
    the LAST attempt's text would be diagnosing a draft that never shipped."""
    r, _be, rend = _run(tmp_path, [STYLE_DIRTY_CV, HARD_DIRTY_CV])
    assert r.status == "rendered"
    assert rend.rendered == [STYLE_DIRTY_CV]

    lead = _lead_dir(tmp_path)
    assert _text(lead / "cv.attempt-2.md") == HARD_DIRTY_CV
    assert _text(lead / "cv.rendered.md") == STYLE_DIRTY_CV
    run = _run_record(tmp_path)
    assert run["attempt_count"] == 2
    assert run["retained_attempt"] == 1


def test_a_retry_whose_compose_raised_is_recorded_against_that_attempt(tmp_path):
    timeout = BackendError("compose timeout: every backend leg is down")
    r, be, _rend = _run(tmp_path, [STYLE_DIRTY_CV, timeout])
    assert r.status == "rendered"
    assert len(be.compose_prompts) == 2, "the retry never happened, so nothing raised"

    lead = _lead_dir(tmp_path)
    # Written BEFORE the backend call, so a compose that hangs or raises still leaves the
    # prompt that caused it on disk.
    assert _text(lead / "prompt.attempt-2.txt") == be.compose_prompts[1]
    assert not (lead / "cv.attempt-2.md").exists()
    run = _run_record(tmp_path)
    assert run["attempt_count"] == 2
    assert run["retained_attempt"] == 1
    assert run["attempts"][0]["compose_error"] is None
    assert "compose timeout" in run["attempts"][1]["compose_error"]


def test_a_later_run_clears_every_artefact_an_earlier_run_left_that_it_does_not_rewrite(
        tmp_path):
    """First run: two attempts, attempt 1 rendered -- so it leaves attempt-2 files AND
    `cv.rendered.md`. Second run: a dry run with one attempt, which writes neither. Every
    one of those three must be gone, or a stale file sits beside the new run.json reading
    as current; the second run also carries a DIFFERENT status, so a stale run.json that
    survived would be caught by the status assertion."""
    first, _be, _rend = _run(tmp_path, [STYLE_DIRTY_CV, HARD_DIRTY_CV])
    assert first.status == "rendered"
    lead = _lead_dir(tmp_path)
    for stale in ("prompt.attempt-2.txt", "cv.attempt-2.md", "cv.rendered.md"):
        assert (lead / stale).exists(), f"the first run never wrote {stale}: nothing to clear"
    # An attempt number the engine does not reach today is still this module's name, so a
    # larger retry budget later cannot leave one behind.
    (lead / "prompt.attempt-3.txt").write_text("stale", encoding="utf-8")
    # Files the artefact writer does not own must survive the clear: the renderer's PDF
    # shares this directory, and so may anything the user put there -- including a name
    # that merely STARTS like an artefact's.
    (lead / "CV.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (lead / "notes.txt").write_text("kept", encoding="utf-8")
    (lead / "cv.attempt-1.md.orig").write_text("kept", encoding="utf-8")

    second, _be, _rend = _run(tmp_path, [CLEAN_CV], dry_run=True)
    assert second.status == "dry-run"
    for stale in ("prompt.attempt-2.txt", "cv.attempt-2.md", "cv.rendered.md",
                  "prompt.attempt-3.txt"):
        assert not (lead / stale).exists(), f"{stale} from the earlier run survived"
    run = _run_record(tmp_path)
    assert run["status"] == "dry-run"
    assert sorted(run["files"]) == ["cv.attempt-1.md", "prompt.attempt-1.txt"]
    for kept in ("CV.pdf", "notes.txt", "cv.attempt-1.md.orig"):
        assert (lead / kept).exists(), f"{kept} is not an artefact, and was deleted"


def test_a_dry_run_writes_the_artefacts_and_nothing_else(tmp_path):
    """Decided rather than inherited: a dry run DOES write artefacts. It already spends a
    composition and an audit call per lead, and the composed text is the part of that spend
    worth keeping -- the result line shows findings but never the draft they were found in.
    `output_dir` is a scratch workspace nothing downstream reads, so this changes no
    pipeline state. Everything a dry run never wrote, it still does not."""
    cfg = _cfg_at(tmp_path, serve=True)
    note = Note(dict(_LEAD_FM))
    v, rend = FakeVault(ENTRIES, notes=[note]), FakeRenderer()
    r = run_one(note, v, cfg, _ScriptedBackend([CLEAN_CV]), FakeCache(), renderer=rend,
                dry_run=True)
    assert r.status == "dry-run"

    run = _run_record(tmp_path)
    assert run["status"] == "dry-run"
    assert run["dry_run"] is True
    assert run["retained_attempt"] == 1
    assert _text(_lead_dir(tmp_path) / "cv.attempt-1.md") == CLEAN_CV
    assert not (_lead_dir(tmp_path) / "cv.rendered.md").exists()
    assert rend.rendered == []
    assert v.written == {} and v.fields == {}
    assert not os.path.exists(cfg.served_dir)


def test_rerunning_a_held_lead_leaves_the_artefacts_that_explain_the_hold(tmp_path):
    """`cv signoff` never touches `output_dir`, and the #60 latch refuses a held lead before
    composition -- so the held run's artefacts stand untouched for exactly as long as a
    human has the hold to review. A re-run must not clear them on its way to refusing."""
    cfg = _cfg_at(tmp_path, serve=True)
    note = Note(dict(_LEAD_FM))
    v = FakeVault(ENTRIES, notes=[note])
    held = run_one(note, v, cfg, _ScriptedBackend([CLEAN_CV], audit_out=_UNSUPPORTED),
                   FakeCache(), renderer=_PdfRenderer())
    assert held.status == "needs-signoff"
    lead = _lead_dir(tmp_path)
    before = {p.name: p.read_bytes() for p in lead.iterdir()}
    assert "run.json" in before and "prompt.attempt-1.txt" in before

    again = run_one(note, v, cfg, _ScriptedBackend([]), FakeCache(), renderer=_PdfRenderer())
    assert again.status == "skipped-needs-signoff"
    assert {p.name: p.read_bytes() for p in lead.iterdir()} == before


def test_a_lead_refused_before_composition_gets_no_working_directory(tmp_path):
    r, _be, _rend = _run(tmp_path, [], note=Note({**_LEAD_FM, "status": "new"}))
    assert r.status == "skipped-selection"
    assert r.artefacts_failed is False
    assert not (tmp_path / "cv-output").exists()


def test_an_unwritable_working_directory_warns_and_flags_but_still_ships_the_cv(
        tmp_path, caplog):
    blocker = tmp_path / "cv-output"
    blocker.write_text("a file where the directory should be", encoding="utf-8")
    with caplog.at_level("WARNING"):
        r, _be, rend = _run(tmp_path, [CLEAN_CV])
    # The CV itself is untouched by the failure...
    assert r.status == "rendered"
    assert rend.rendered == [CLEAN_CV]
    # ...and the run does not carry on as if the artefacts existed.
    assert r.artefacts_failed is True
    said = [rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"]
    about_it = [m for m in said if str(blocker / _LEAD_DIR) in m]
    # ONCE, for the directory. Every file below it fails for that same cause, and a WARNING
    # per file would bury the one line that says what is actually wrong.
    assert len(about_it) == 1, said


def test_text_that_cannot_be_encoded_flags_that_file_and_keeps_the_rest(tmp_path, caplog):
    """The ValueError arm, which an OSError-only handler would let escape: a lone surrogate
    (a JSON-decoded backend reply can carry one) has no UTF-8 encoding, and the refusal is
    UnicodeEncodeError, a ValueError. One unwritable file must not cost the others."""
    draft = CLEAN_CV.replace("- Shipped [EF1]", "- Shipped\ud800 [EF1]")
    with caplog.at_level("WARNING"):
        r, _be, _rend = _run(tmp_path, [draft])
    assert r.status == "rendered"
    assert r.artefacts_failed is True

    lead = _lead_dir(tmp_path)
    # Encoded before the file is opened, so the refusal leaves no truncated file behind.
    assert not (lead / "cv.attempt-1.md").exists()
    run = _run_record(tmp_path)
    assert "prompt.attempt-1.txt" in run["files"]
    assert "cv.attempt-1.md" not in run["files"]
    assert any("cv.attempt-1.md" in e for e in run["artefact_errors"])
    assert any(str(lead / "cv.attempt-1.md") in rec.getMessage() for rec in caplog.records)


def test_a_stale_artefact_that_cannot_be_cleared_is_reported_not_ignored(tmp_path, caplog):
    """A stale file that survives the clear is exactly the one that could be mistaken for
    this run's, so failing to remove it counts as an artefact failure like any write. A
    DIRECTORY with an artefact's name is the portable way to make removal fail: unlink
    refuses a directory on every platform and for every user, where a permission bit does
    not stop root."""
    lead = _lead_dir(tmp_path)
    (lead / "cv.attempt-9.md").mkdir(parents=True)
    with caplog.at_level("WARNING"):
        r, _be, _rend = _run(tmp_path, [CLEAN_CV])
    assert r.status == "rendered"
    assert r.artefacts_failed is True
    run = _run_record(tmp_path)
    assert any("cv.attempt-9.md" in e for e in run["artefact_errors"]), run["artefact_errors"]
    assert any(str(lead / "cv.attempt-9.md") in rec.getMessage() for rec in caplog.records)


class _UnreadableCorpusVault(FakeVault):
    """Raises reading the citable corpus, which happens BEFORE any compose."""

    def read_evidence(self, kind, verified_only=True):
        raise OSError("experience corpus unreadable")


def test_a_run_that_fails_before_composing_leaves_the_last_diagnosis_alone(tmp_path):
    """The artefacts begin where composition begins, not where the run does. A run that
    dies earlier has composed nothing to diagnose, and clearing the previous run's set on
    its way to failing would destroy the last diagnosis this lead had for nothing."""
    first, _be, _rend = _run(tmp_path, [_UNCITED_CV, _UNCITED_CV])
    assert first.status == "skipped-gate"
    lead = _lead_dir(tmp_path)
    before = {p.name: p.read_bytes() for p in lead.iterdir()}
    assert "run.json" in before and "cv.attempt-2.md" in before

    note = Note(dict(_LEAD_FM))
    [r] = run_batch(_UnreadableCorpusVault(ENTRIES, notes=[note]), _cfg_at(tmp_path),
                    _ScriptedBackend([]), FakeCache(), renderer=FakeRenderer())
    assert r.status == "error"
    assert {p.name: p.read_bytes() for p in lead.iterdir()} == before


def test_the_run_record_survives_quoting_text_utf8_cannot_encode(tmp_path):
    """run.json quotes backend errors and findings verbatim, and it is the one file that
    says what else went wrong -- so it must not be the file a lone surrogate costs. It is
    escaped rather than written raw, which this row falsifies: a record serialised with
    `ensure_ascii=False` fails to encode and never reaches disk."""
    odd = BackendError("compose failed near \ud800")
    r, _be, _rend = _run(tmp_path, [STYLE_DIRTY_CV, odd])
    assert r.status == "rendered"
    assert r.artefacts_failed is False
    run = _run_record(tmp_path)
    assert "\ud800" in run["attempts"][1]["compose_error"]


class _NonStringPathRenderer(FakeRenderer):
    """Returns something other than the `str` path the Renderer protocol declares. Nothing
    checks an injected renderer's return at runtime, and run.json records it verbatim."""

    def render(self, cv_text, out_dir, *, neutral_name="CV.pdf"):
        super().render(cv_text, out_dir, neutral_name=neutral_name)
        return object()


def test_a_run_record_that_cannot_be_serialised_flags_rather_than_costing_the_cv(
        tmp_path, caplog):
    """Serialising run.json is part of writing it, so it fails the way a write does: a
    WARNING naming the path, `artefacts_failed`, and a CV that still ships. Serialised
    ahead of the guarded write, a TypeError escaped `run_one` and turned a rendered CV into
    an `error`."""
    with caplog.at_level("WARNING"):
        r, _be, rend = _run(tmp_path, [CLEAN_CV], renderer=_NonStringPathRenderer())
    assert r.status == "rendered"
    assert rend.rendered == [CLEAN_CV]
    assert r.artefacts_failed is True
    lead = _lead_dir(tmp_path)
    assert not (lead / "run.json").exists()
    # The files written before the record are untouched by its failure.
    assert _text(lead / "cv.rendered.md") == CLEAN_CV
    assert any(str(lead / "run.json") in rec.getMessage() for rec in caplog.records)


def test_a_run_that_raises_after_composing_records_the_error(tmp_path):
    note = Note(dict(_LEAD_FM))
    [r] = run_batch(FakeVault(ENTRIES, notes=[note]), _cfg_at(tmp_path),
                    _ScriptedBackend([CLEAN_CV]), FakeCache(), renderer=_BrokenRenderer())
    assert r.status == "error"
    assert r.artefacts_failed is False

    run = _run_record(tmp_path)
    assert run["status"] == "error"
    assert "renderer boom" in run["error"]
    # What the renderer was handed, which is the first thing to look at when it fails.
    assert _text(_lead_dir(tmp_path) / "cv.rendered.md") == CLEAN_CV


def test_the_artefacts_flag_survives_a_run_that_raises(tmp_path):
    """run_batch builds an `error` result from the exception alone, so the flag has to ride
    on it the way `dossier_failed` already does."""
    (tmp_path / "cv-output").write_text("not a directory", encoding="utf-8")
    note = Note(dict(_LEAD_FM))
    [r] = run_batch(FakeVault(ENTRIES, notes=[note]), _cfg_at(tmp_path),
                    _ScriptedBackend([CLEAN_CV]), FakeCache(), renderer=_BrokenRenderer())
    assert r.status == "error"
    assert r.artefacts_failed is True
