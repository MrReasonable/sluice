"""#112: `sluice triage run` at the CLI layer -- cmd_triage_run must surface the actual
triage.engine failure MESSAGES on stderr, not just their count. `report.failures` already
carries actionable strings (dossier fetch errors, judge/lead_id mismatches, and
company-resolve conflicts); a bare `failures=N` gives a user no way to act on them short of
re-running under a debugger."""
from sluice import cli
from sluice.cli import _build_parser, cmd_triage_run
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.triage.engine import TriageReport


def test_cmd_triage_run_prints_each_failure_message(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    report = TriageReport(counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 0,
                                  "needs_review": 0, "skipped": 0},
                          judged=0, backend=None,
                          failures=["dossier Example Co - Analyst.md: connection refused",
                                    "judge 'ghost-lead': no note matches this lead_id "
                                    "(the model likely paraphrased the echoed slug)"])
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: report)

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    err = capsys.readouterr().err
    assert "dossier Example Co - Analyst.md: connection refused" in err
    assert ("judge 'ghost-lead': no note matches this lead_id "
            "(the model likely paraphrased the echoed slug)") in err


def test_cmd_triage_run_prints_nothing_extra_when_no_failures(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    report = TriageReport(counts={"keep": 1, "shortlist": 0, "research": 0, "dismiss": 0,
                                  "needs_review": 0, "skipped": 0},
                          judged=0, backend=None, failures=[])
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: report)

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    assert "failures=0" in capsys.readouterr().err


def test_the_cli_reports_a_triage_subapp_config_error_instead_of_crashing(
        tmp_path, monkeypatch, capsys):
    """load_triage_config() runs LAZILY inside Sluice.triage()/Sluice.doctor(), not
    inside main()'s own load_config() -- so a malformed triage: block previously
    reached the user as a raw traceback instead of the SAME "usage error, not a
    crash" shape a malformed ROOT config key already gets. #120's own
    company_resolve_llm cross-field check is what a real install is most likely to
    trip (turning tier 3 on and forgetting company_resolve_fetch), so this proves
    the general fix -- widening main()'s dispatch wrap, not a triage-specific
    patch -- with that concrete case."""
    from sluice.cli import main
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text("triage:\n  company_resolve_llm: true\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))

    rc = main(["triage", "run", "--no-llm"])
    err = capsys.readouterr().err

    assert rc == 2, "a malformed sub-app config key is a usage error, not a crash"
    assert "Traceback" not in err
    assert "company_resolve_llm" in err and "company_resolve_fetch" in err


def test_the_cli_reports_the_same_triage_config_error_via_doctor(tmp_path, monkeypatch, capsys):
    """doctor is the command whose whole job is diagnosing exactly this -- it must
    get the same clean message, not a traceback, from the SAME widened catch."""
    from sluice.cli import main
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text("triage:\n  company_resolve_llm: true\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfgp))

    rc = main(["doctor", "--offline"])
    err = capsys.readouterr().err

    assert rc == 2
    assert "Traceback" not in err
    assert "company_resolve_llm" in err


def test_cmd_triage_run_prints_the_resolved_by_tier_counts_and_the_llm_call_count(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    report = TriageReport(counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 0,
                                  "needs_review": 0, "skipped": 0},
                          judged=0, backend=None, failures=[],
                          resolved={"tier1": 0, "tier2": 1, "tier3": 3}, llm_calls=9)
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: report)

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    err = capsys.readouterr().err
    assert "resolved={'tier1': 0, 'tier2': 1, 'tier3': 3}" in err
    assert "llm_calls=9" in err


def test_the_selection_default_reaches_the_engine_from_BOTH_of_its_spellings(
        monkeypatch, tmp_path):
    """`cmd_triage_run` resolves the selection twice, and only one of them was covered.

    `tests/test_status.py::test_the_selection_default_has_ONE_home_and_the_parser_uses_it`
    walks the PARSER, so it never reaches the `args.status or ...` fallback beside it --
    replacing that fallback with a stale literal left the whole suite green. `--status ""`
    is what reaches it: argparse's default only applies when the flag is ABSENT, so an
    explicitly empty value falls through to the fallback instead.

    Both spellings must hand the engine the same tuple, and it must be the shipped
    constant rather than anything transcribed from it.
    """
    from sluice.core import status as _status

    seen = {}

    def _capture(self, **kw):
        seen["statuses"] = kw["statuses"]
        return TriageReport(counts={}, judged=0, backend=None, failures=[])

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(Sluice, "triage", _capture)
    expected = tuple(_status.DEFAULT_TRIAGE_STATUSES)

    # Flag absent: argparse's default supplies it.
    cmd_triage_run(_build_parser().parse_args(["triage", "run", "--no-llm"]), Config())
    assert seen["statuses"] == expected

    # Flag present but empty: the in-function fallback supplies it.
    seen.clear()
    cmd_triage_run(
        _build_parser().parse_args(["triage", "run", "--no-llm", "--status", ""]), Config())
    assert seen["statuses"] == expected, (
        "the --status '' fallback in cmd_triage_run disagrees with the parser default")


# ── #223 §2.5: a disagreement is surfaced, not silently overridden ────────────
def _report(**kw):
    base = dict(counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 0,
                        "needs_review": 0, "skipped": 0},
                judged=0, backend=None, failures=[])
    base.update(kw)
    return TriageReport(**base)


def test_cmd_triage_run_prints_each_role_type_conflict(monkeypatch, tmp_path, capsys):
    said = ("role-type Example Co - Analyst.md: the posting says 'contract', but this "
            "lead carried 'permanent' as 'declared' -- the search's premise does not "
            "hold for this posting")
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(Sluice, "triage",
                        lambda self, **kw: _report(role_type_conflicts=[said]))

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    err = capsys.readouterr().err
    assert said in err
    # NOT counted as a failure. Nothing failed -- the posting simply contradicted a
    # role type the user declared on a search -- and a user scanning `failures=N` for
    # something to fix would be misled about both numbers.
    assert "failures=0" in err


def test_cmd_triage_run_reports_how_many_role_types_the_postings_settled(
        monkeypatch, tmp_path, capsys):
    # The aggregate half. `corrected` is the tool overwriting its OWN guess, which on
    # the population #223 describes is most leads -- counted rather than announced, so
    # the summary stays readable while the volume is still visible.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report(
        observed_role_types={"filled": 3, "corrected": 12, "conflicted": 1}))

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    assert "observed_role_types={'filled': 3, 'corrected': 12, 'conflicted': 1}" in \
        capsys.readouterr().err


def test_cmd_triage_run_prints_the_reverdict_notice_and_says_the_run_wrote_nothing(
        monkeypatch, tmp_path, capsys):
    # #223 §2.1. The notice is the entire point of skipping the run, so it must not be
    # one line among a summary that otherwise reads like an ordinary quiet run: a user
    # who does not understand why nothing happened will simply run it again, which is
    # the acknowledgement, and the batch dismissal lands unread.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report(
        reverdict_pending=["acme: pay was judged as day, now judged as annual: "
                           "keep -> reject (Salary below floor: 45000 < 90000)"],
        reverdict_deferred=True))

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    err = capsys.readouterr().err
    assert "acme: pay was judged as day" in err
    # Case-folded: what has to hold is that the user is told the run wrote nothing and
    # what to do next, not how the notice is capitalised.
    assert "wrote nothing" in err.lower()
    assert "run it again" in err.lower()
    # ...and the ordinary summary is NOT printed underneath it. A row of zeroes below
    # the notice reads as a quiet run rather than a suppressed one.
    assert "judged=" not in err


def test_cmd_triage_run_does_not_claim_it_wrote_nothing_when_it_wrote(
        monkeypatch, tmp_path, capsys):
    """The round-1 fix's own defect, found by a reviewer and while reading the CLI back.

    `run()` PROCEEDS when the acknowledgement could not be recorded -- repeating the
    notice forever would mean never triaging again. `reverdict_pending` is non-empty on
    BOTH arms, so branching on it alone printed "WROTE NOTHING" over a run that had just
    dismissed every lead it named, pushed the same claim to the notification channel, and
    returned before the summary and the failures line explaining why.
    """
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report(
        reverdict_pending=["acme: pay was judged as day, now judged as annual: "
                           "keep -> reject"],
        reverdict_deferred=False,
        counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 1,
                "needs_review": 0, "skipped": 0},
        failures=["role-type re-verdict: the notice above could not be recorded"]))

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    err = capsys.readouterr().err
    assert "wrote nothing" not in err.lower()
    assert "acme: pay was judged as day" in err          # still named
    assert "judged=" in err                              # ...and the summary survives
    assert "could not be recorded" in err                # ...and so does the reason


def test_cmd_triage_run_still_holds_when_the_marker_landed(monkeypatch, tmp_path, capsys):
    # The other arm, unchanged: the acknowledgement recorded, so the run really did stop.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report(
        reverdict_pending=["acme: pay was judged as day, now judged as annual"],
        reverdict_deferred=True))

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    err = capsys.readouterr().err
    assert "wrote nothing" in err.lower()
    assert "judged=" not in err


def test_the_reverdict_notice_reaches_the_push_channel(monkeypatch, tmp_path, capsys):
    """Round 1 added this and shipped it untested; deleting the call passed the suite.

    stderr is read by nobody on an unattended install -- a cron entry, a container --
    and this is the one run where the tool has something urgent to say and then stops.
    The push channel is the only surface a human sees, so its absence is invisible
    exactly where the re-verdict is most dangerous.
    """
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting",
                        lambda msg, **kw: sent.append((msg, kw.get("label"))))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report(
        reverdict_pending=["acme: pay was judged as day, now judged as annual"],
        reverdict_deferred=True))

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    assert len(sent) == 1, "the held run notified nobody"
    msg, label = sent[0]
    assert "1 lead" in msg and "#223" in msg
    assert label == "triage-summary"


def test_the_APPLIED_arm_pushes_the_re_verdict_too(monkeypatch, tmp_path, capsys):
    """Round 3's High, and it was exactly backwards.

    The HELD arm writes NOTHING and pushed an urgent "#223, run it again". The APPLIED
    arm has just dismissed leads irreversibly -- `dismiss` is not re-selected -- and sent
    a summary indistinguishable from an ordinary run. On the cron or container install
    the push exists for, that put the alert on the recoverable arm and left the
    unrecoverable one silent.
    """
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting",
                        lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report(
        reverdict_pending=["acme: pay was judged as day, now judged as annual"],
        reverdict_deferred=False,
        counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 1,
                "needs_review": 0, "skipped": 0}))

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    assert len(sent) == 1
    assert "#223" in sent[0] and "1 lead" in sent[0]
    assert "'dismiss': 1" in sent[0]        # ...and still carries the counts


def test_an_ordinary_run_pushes_no_re_verdict_wording(monkeypatch, tmp_path, capsys):
    # The other half: a run with nothing to announce must not gain the prefix, or the
    # alert stops meaning anything.
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report())

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    assert "#223" not in sent[0]


def test_a_dry_run_is_told_to_re_run_without_dry_run(monkeypatch, tmp_path, capsys):
    # A dry run never spends the marker, so "run it again to apply them" is false for it
    # -- executed dry, dry, real, real, that sentence printed three times before
    # anything applied.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report(
        reverdict_pending=["acme: pay was judged as day, now judged as annual"],
        reverdict_deferred=True))

    args = _build_parser().parse_args(["triage", "run", "--no-llm", "--dry-run"])
    assert cmd_triage_run(args, Config()) == 0
    err = capsys.readouterr().err
    assert "without --dry-run" in err.lower()
    assert "run it again to apply them" not in err.lower()
