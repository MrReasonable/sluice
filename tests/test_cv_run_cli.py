"""`sluice cv run` at the CLI layer (#167 Task 16, extended by #258): `CvResult.slop`
had NO reader since it was added, and `CvResult.voice_flags` was a brand-new field
threaded through cv/engine.py's retry loop by Task 14 -- neither a style phrase match
nor a model-judged voice finding reached the user unless `cmd_cv_run` printed them.
#258 is the same defect on the remaining two fields: `violations` (the HARD gate's own
findings, the ones that actually BIN the lead) and `audit_flags`, both of which reached
the operator as a bare COUNT at every log level while the MCP server's `cv_run` had
been returning them in full the whole time.

Mirrors this repo's `test_<command>_cli.py` convention (`test_apply_record_cli.py`,
`test_triage_run_cli.py`, `test_health_cli.py`): `Sluice.compose_cv` is monkeypatched to
a canned result so this file tests `cmd_cv_run`'s OWN printing, not cv/engine.py's retry
machinery -- that is already pinned directly against `run_one` in tests/test_cv_engine.py.
"""
from sluice.cli import _build_parser, cmd_cv_run
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.cv.engine import CvResult


def _args():
    return _build_parser().parse_args(["cv", "run", "--lead", "example-foundry-analyst"])


def _detail_lines(err):
    """The indented per-finding lines `cmd_cv_run` prints under each summary line.

    ONE expression, used by both the clean-run test (which asserts it is empty) and the
    order test (which asserts it is all four, in order). That sharing is the point: the
    clean-run assertion is a NEGATIVE guard -- finding nothing IS the success case -- so it
    passes just as happily on a predicate that can never match anything. Measured by a
    reviewer while this was two copies: narrowing only the clean test's predicate to
    `startswith("\t\t\t\t")` left the WHOLE SUITE green. With one helper, that same
    narrowing reddens the populated row instead, which is what makes the negative guard's
    scope a check rather than a comment asking a future editor to keep two spellings in step.
    """
    return [ln for ln in err.splitlines() if ln.startswith("  ")]


def test_cmd_cv_run_prints_the_style_and_voice_findings(monkeypatch, tmp_path, capsys):
    # The populated case, not just a field that EXISTS or is empty on a clean run --
    # either of those is indistinguishable from a broken reader (see this task's own
    # brief). One genuinely style-dirty finding and one genuinely voice-dirty finding,
    # both already SLOP/flag-prefixed exactly as cv/engine.py's retained-draft path
    # (Task 16) hands them back.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult(
        "Job Applications/Job Leads/Example Foundry - Analyst.md", "rendered",
        served="Example_CV_deadbeef.pdf",
        slop=["SLOP leverage: I leverage strong delivery patterns."],
        voice_flags=["flag\tThis reads like a press release."])
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])

    assert cmd_cv_run(_args(), Config()) == 0
    err = capsys.readouterr().err
    assert "SLOP leverage: I leverage strong delivery patterns." in err
    assert "flag\tThis reads like a press release." in err
    # The LABEL, not just the text. `slop` entries arrive already prefixed by
    # cv/engine.py; `voice_flags` do not (cv/voice.py hands back the raw "flag\t..."
    # line), so cmd_cv_run adds "VOICE: " to make the two read alike. Measured:
    # dropping that prefix left the whole suite green, so the two kinds of finding
    # became indistinguishable in the output with nothing to catch it.
    assert "VOICE: flag\tThis reads like a press release." in err


def test_cmd_cv_run_prints_nothing_extra_when_every_finding_list_is_empty(
        monkeypatch, tmp_path, capsys):
    # The other half of the populated-case discipline: a genuinely clean run must not
    # grow a spurious "SLOP"/"VOICE" line just because the fields now have a reader.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult(
        "Job Applications/Job Leads/Example Foundry - Analyst.md", "rendered",
        served="Example_CV_deadbeef.pdf")
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])

    assert cmd_cv_run(_args(), Config()) == 0
    err = capsys.readouterr().err
    assert "SLOP" not in err
    assert "VOICE" not in err
    assert "AUDIT" not in err
    assert "slop=0" in err and "voice_flags=0" in err
    assert "violations=0" in err and "audit_flags=0" in err
    # The general form of the three label assertions above, which #258 is the reason to
    # add: `violations` entries carry no single label to name (every producer prefixes
    # its own category -- see the skipped-gate test), so an absence check keyed on
    # labels cannot cover them, and a hand-list of the gate's category words would go
    # stale the next time validate.py grows one. Every detail line this command emits is
    # indented by exactly the two spaces the loop writes, so sweeping for ANY indented
    # line covers all four kinds at once -- and a fifth added later, which is the case a
    # label list structurally cannot reach.
    #
    # This is a NEGATIVE guard -- finding nothing IS the success case -- so it passes just
    # as happily on a sweep that can never see anything. Its SCOPE is pinned by sharing
    # `_detail_lines` with the order test at the bottom of this file, which asserts the same
    # helper returns all four lines against a populated result. Shared, not merely identical:
    # as two copies of one expression, narrowing THIS one alone left the whole suite green.
    assert _detail_lines(err) == []


def test_cmd_cv_run_prints_the_gate_violations_on_skipped_gate(monkeypatch, tmp_path, capsys):
    """#258: `skipped-gate` means this lead produced NO CV, and the count was the whole
    diagnostic surface -- there is no verbose flag that reaches these (`--verbose` is a
    `doctor`-only option, and nothing logs them at any level, so `SLUICE_LOG_LEVEL`
    cannot help either). Diagnosing one real case required monkeypatching
    `cv.engine._validate` from a driver script.

    The populated case, on the status that actually produces it: one gate violation
    (cv/validate.py's own wording) and one renderer `precheck` violation
    (renderers/template.py's `FORMAT:` prefix), since the field carries both and a
    fixture holding only cv/validate.py's shape would not notice a reader that dropped
    the folded-in renderer half.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult(
        "Job Applications/Job Leads/Example Foundry - Analyst.md", "skipped-gate",
        violations=["UNSOURCED SKILL 'Widget, Gadget': not in the bundle",
                    "FORMAT: meta line 1 has 4 fields, expected 3"])
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])

    assert cmd_cv_run(_args(), Config()) == 0
    err = capsys.readouterr().err
    assert "violations=2" in err
    # The CONTENT, on its own indented line under the summary -- the whole point of the
    # issue. Asserted with the leading indent, not as a bare substring: the summary line
    # would satisfy a bare `in err` for neither of these, but a future reader that
    # inlined them into the summary would still be the count-only defect wearing a
    # longer line, and this is what distinguishes the two.
    assert "\n  UNSOURCED SKILL 'Widget, Gadget': not in the bundle\n" in err
    assert "\n  FORMAT: meta line 1 has 4 fields, expected 3\n" in err
    # No added label, unlike VOICE below: every producer of a `violations` entry already
    # prefixes its own ALL-CAPS category (cv/validate.py's UNSOURCED SKILL / INVENTED
    # METRIC / ..., cv/engine.py's STRUCTURAL, and renderers/template.py's FORMAT --
    # whose docstring states it chose that prefix to "match the shape the engine's other
    # gate messages take"). Pinned so a well-meant "GATE: " prefix does not silently
    # double-label them.
    assert "GATE:" not in err


def test_cmd_cv_run_prints_the_violations_whatever_the_status_is(monkeypatch, tmp_path, capsys):
    """The print is NOT gated on the status, and this is the row that falsifies it.

    #258 suggested printing them "on a non-clean status". `skipped-gate` is the only
    CvResult constructor in cv/engine.py that passes `violations=` today, so a status
    condition would be equivalent -- and would silently re-introduce the exact defect
    the moment a status that DOES populate the field is added, with nothing red. The
    unconditional form cannot: it prints what the field holds. A canned `rendered`
    carrying violations is unreachable from the engine on purpose -- this pins
    `cmd_cv_run`'s own contract (print what you were handed), not the engine's.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult(
        "Job Applications/Job Leads/Example Foundry - Analyst.md", "rendered",
        served="Example_CV_deadbeef.pdf",
        violations=["INVENTED METRIC ['500'] not in ['BO1']: Scaled the platform"])
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])

    assert cmd_cv_run(_args(), Config()) == 0
    err = capsys.readouterr().err
    assert "\n  INVENTED METRIC ['500'] not in ['BO1']: Scaled the platform\n" in err


def test_cmd_cv_run_prints_the_audit_flags_with_their_own_label(monkeypatch, tmp_path, capsys):
    """#258's second half: `audit_flags` is the advisory model-judged FABRICATION
    verdict (cv/audit.py) and had the same count-only reader as `violations`. Unlike
    `violations` it survives a RENDERED run, where the count is the only thing standing
    between the operator and a `paraphrase`/`unsupported` claim in a CV about to be sent
    under their own name.

    The LABEL is asserted, not just the text: `run_audit` hands back the raw
    "<verdict>\\t<claim>\\t<cited-id>" line unprefixed, exactly like `voice_flags`, so
    without "AUDIT: " an audit verdict and a gate violation read as the same kind of
    thing in one indented block.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult(
        "Job Applications/Job Leads/Example Foundry - Analyst.md", "needs-signoff",
        served="Example_CV_deadbeef.pdf",
        audit_flags=["unsupported\tLed a team of nine\tBO1",
                     "paraphrase\tScaled the platform\tBO2"])
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])

    assert cmd_cv_run(_args(), Config()) == 0
    err = capsys.readouterr().err
    assert "audit_flags=2" in err
    assert "\n  AUDIT: unsupported\tLed a team of nine\tBO1\n" in err
    assert "\n  AUDIT: paraphrase\tScaled the platform\tBO2\n" in err


def test_cmd_cv_run_prints_the_four_finding_kinds_in_the_summary_lines_own_order(
        monkeypatch, tmp_path, capsys):
    """One result carrying all four, to pin the ORDER as the summary line's own field
    order (violations, audit_flags, slop, voice_flags).

    Not decoration: the summary line is what the operator reads first, and a detail
    block ordered differently from the counts it expands makes the reader match blocks
    to counts by guessing. Nothing else in this file can see the order -- each of the
    other tests holds exactly one kind.

    It carries a second job since a reviewer measured the first version of it: sharing
    `_detail_lines` with the clean-run test above is what stops that test's negative
    assertion from going quietly vacuous. See the helper.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    result = CvResult(
        "Job Applications/Job Leads/Example Foundry - Analyst.md", "skipped-gate",
        violations=["UNCITED BULLET: - Delivered the widget pipeline"],
        slop=["SLOP leverage: I leverage strong delivery patterns."],
        audit_flags=["unsupported\tLed a team of nine\tBO1"],
        voice_flags=["flag\tThis reads like a press release."])
    monkeypatch.setattr(Sluice, "compose_cv", lambda self, **kw: [result])

    assert cmd_cv_run(_args(), Config()) == 0
    assert _detail_lines(capsys.readouterr().err) == [
        "  UNCITED BULLET: - Delivered the widget pipeline",
        "  AUDIT: unsupported\tLed a team of nine\tBO1",
        "  SLOP leverage: I leverage strong delivery patterns.",
        "  VOICE: flag\tThis reads like a press release.",
    ]
