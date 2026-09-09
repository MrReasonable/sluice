"""#112: `sluice triage run` at the CLI layer -- cmd_triage_run must surface the actual
triage.engine failure MESSAGES on stderr, not just their count. `report.failures` already
carries actionable strings (dossier fetch errors, judge/lead_id mismatches, and
company-resolve conflicts); a bare `failures=N` gives a user no way to act on them short of
re-running under a debugger."""
from sluice import cli
from sluice.cli import _build_parser, cmd_triage_run
from sluice.core import status as _status
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
    # ...and still carries the counts. Re-spelled when the body stopped being `counts`'
    # repr: what has to hold is that the alert does not REPLACE the run's numbers, which
    # is the same guarantee this line always made.
    assert "1 dismissed" in sent[0]


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


def test_a_dry_run_push_does_not_promise_that_re_running_applies(monkeypatch, tmp_path):
    # The stderr wording already split for `--dry-run`; the push body did not, so a dry
    # run pushed "Run it again to apply" on every run and nothing ever applied. That is
    # the same defect `nudge` exists to fix, on the one channel an unattended install
    # actually reads.
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report(
        reverdict_pending=["acme: pay was judged as day, now judged as annual"],
        reverdict_deferred=True))

    args = _build_parser().parse_args(["triage", "run", "--no-llm", "--dry-run"])
    assert cmd_triage_run(args, Config()) == 0
    assert len(sent) == 1
    assert "without --dry-run" in sent[0].lower()
    assert "run it again to apply" not in sent[0].lower()


# ── the push body is read on a phone: prose, not a dict repr ─────────────────
def _push(monkeypatch, tmp_path, **kw):
    """Run `triage run` over a canned report and return the single push body."""
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kwargs: sent.append(msg))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kwargs: _report(**kw))
    assert cmd_triage_run(
        _build_parser().parse_args(["triage", "run", "--no-llm"]), Config()) == 0
    assert len(sent) == 1, "the run notified nobody"
    return sent[0]


def test_the_push_names_the_surfaced_leads(monkeypatch, tmp_path):
    """"1 shortlist" sends the reader to the machine to find out what it was.

    The names are the whole reason to read the message on a phone, so they are the one
    thing the body must carry. Only the surfaced verdicts: a dismissed lead is one the
    reader is deliberately not being asked to look at.
    """
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 2, "shortlist": 1, "research": 1, "dismiss": 3,
                         "needs_review": 0, "skipped": 0},
                 judged=2, backend="deepseek-v4-flash",
                 surfaced=[("shortlist", "Acme", "Engineering Manager"),
                           ("research", "Beta", "Delivery Lead")])

    assert body.startswith("job-sluice triage: 2 to look at"), (
        "the headline VALUE is the phone's preview line and nothing asserted it")
    assert "Acme, Engineering Manager" in body
    assert "Beta, Delivery Lead" in body
    # `_status.SURFACED`'s order is load-bearing, not cosmetic: the first group spends the
    # shared budget first, so a reversed tuple gives the WEAKER call the names and the
    # top of the message. (It no longer truncates the stronger call out entirely -- the
    # one-name-per-group guarantee prevents that -- but it does demote it.) The order was
    # claimed in a comment and held by nothing.
    assert body.index("Acme") < body.index("Beta"), "shortlist must lead research"
    # ...and the raw dict is gone. This is the defect the whole change exists to fix, so
    # it is asserted directly rather than inferred from the prose above.
    assert "'shortlist':" not in body and "{" not in body


def test_the_push_caps_the_named_leads_and_counts_the_rest(monkeypatch, tmp_path):
    # A big run can surface twenty. Naming all of them turns the notification back into
    # a wall of text, and the tail is the least interesting part of it.
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 7, "shortlist": 0, "research": 7, "dismiss": 0,
                         "needs_review": 0, "skipped": 0},
                 judged=7, backend="deepseek-v4-flash",
                 surfaced=[("research", f"Company {n}", "Delivery Lead")
                           for n in range(7)])

    assert "Company 4, Delivery Lead" in body     # the fifth is named
    assert "Company 5" not in body                # the sixth is not
    assert "2 more" in body                       # ...but it is counted
    # A verdict with no leads gets no heading. Dropping the empty-group filter renders a
    # bare "Shortlist (0):" AND silently rebalances the budget, because an empty group
    # takes nothing and then refunds the point reserved for it.
    assert "Shortlist" not in body


def test_the_quiet_push_claims_only_what_the_run_knows(monkeypatch, tmp_path):
    """The quiet run still pushes -- silence cannot be told apart from a dead cron -- so
    it needs a sentence of its own rather than zeroes to decode.

    That sentence must be about the RUN, not the world. `surfaced` records writes that
    LANDED, so a re-judge of `research` leads to `research` writes nothing (`unchanged`)
    and empties it while the leads sit in the vault; "nothing surfaced this pass" asserted
    the opposite. `research` is in DEFAULT_TRIAGE_STATUSES, so that is the ordinary
    second-run-of-the-day cron path.
    """
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 4,
                         "needs_review": 0, "skipped": 0})

    assert "nothing new" in body.lower()
    assert "nothing surfaced" not in body.lower(), (
        "a run cannot establish that nothing surfaced, only that nothing new did")
    assert "0" not in body.split("\n")[0], "the headline should not render a zero count"


def test_the_push_never_presents_keep_as_a_verdict(monkeypatch, tmp_path):
    """`counts["keep"]` is incremented at the pre-gate and NEVER decremented, so a lead
    counted `keep` is counted again under its judged verdict. The rows do not partition
    the leads, and rendering `keep` beside `dismiss` invites exactly the reading that
    they do -- which is how a push saying "keep 55, dismiss 29" came to be unreadable.

    So `keep` is described as what it is: leads that passed the pre-gate and went to the
    judge.
    """
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 55, "shortlist": 1, "research": 0, "dismiss": 29,
                         "needs_review": 6, "skipped": 0},
                 judged=1, backend="deepseek-v4-flash",
                 surfaced=[("shortlist", "Acme", "Engineering Manager")])

    assert "pre-gate" in body, "keep has to be named as a stage, not a verdict"
    # The specific misreading: "55" must never sit in the same list as the outcomes.
    assert "55 dismissed" not in body and "55 kept" not in body


def test_the_push_says_no_judge_ran_rather_than_backend_none(monkeypatch, tmp_path):
    """A null `backend` has THREE causes and this arm is only the first: the judge was
    never called (`--no-llm`, a classify-only pass, nothing to judge), where the actionable
    fact is how many leads are still waiting.

    The second is a judge that ran perfectly well on a backend with no fallback leg to name
    (`test_the_push_does_not_name_a_backend_called_None`); gating this wording on `backend`
    rather than on `judged` would put that run here and claim no judge ran. The third is a
    real outage, which `judge()` swallows into an empty verdict list
    (`test_a_judge_outage_does_not_read_as_a_quiet_run`) -- told apart by `sent_to_judge`,
    which is 0 here because nothing was ever handed over."""
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 55, "shortlist": 0, "research": 0, "dismiss": 29,
                         "needs_review": 6, "skipped": 0},
                 judged=0, backend=None)

    assert "backend None" not in body and "None" not in body
    assert "no judge ran" in body.lower()
    assert "55" in body, "the leads still waiting on a judge are the actionable part"


def test_the_push_names_the_backend_that_judged(monkeypatch, tmp_path):
    # The other arm: when there IS a name, it is printed. Note what this does NOT prove --
    # `FallbackBackend.last_backend` is overwritten on every call, so it reports the leg
    # that served the LAST batch, not whether the primary failed earlier in the run. It is
    # an identifier, not a health signal.
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 3, "shortlist": 1, "research": 0, "dismiss": 2,
                         "needs_review": 0, "skipped": 0},
                 judged=3, backend="deepseek-v4-flash",
                 surfaced=[("shortlist", "Acme", "Engineering Manager")])

    assert "deepseek-v4-flash" in body
    assert "3" in body


def test_the_push_loses_no_non_zero_count(monkeypatch, tmp_path):
    """Prettifying must not silently drop data the dict repr carried.

    Every row is given a DISTINCT value, so a formatter that renders one row's number in
    another row's place cannot pass by coincidence.
    """
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 11, "shortlist": 2, "research": 3, "dismiss": 4,
                         "needs_review": 5, "skipped": 6, "unjudgeable": 7},
                 judged=9, backend="deepseek-v4-flash",
                 # AGREES with the counts rows above, as only the engine's own append site
                 # can produce. A fixture where they disagreed rendered "Shortlist (1)"
                 # under a headline of 5 and made this test's own name false.
                 surfaced=[("shortlist", "Acme", "Engineering Manager"),
                           ("shortlist", "Alpha", "Engineering Manager"),
                           ("research", "Beta", "Delivery Lead"),
                           ("research", "Delta", "Delivery Lead"),
                           ("research", "Epsilon", "Delivery Lead")])

    # The two SURFACED rows are rendered as a per-heading count, not as words.
    assert "Shortlist (2):" in body and "Research (3):" in body
    assert "5 need review" in body
    assert "4 dismissed" in body
    assert "6 skipped" in body
    # #300: the phrase names the OUTCOME, not one cause of it. `unjudgeable` now has two
    # producers -- the `jd_arrived` pre-gate (nothing came back) and a judge verdict (a
    # page came back and was not a posting) -- and "no JD fetched" is false for the second.
    assert "7 with no usable job description" in body
    # Joined, like every assertion above it: `"7" in body` holds only because 7 happens
    # to be unique in this fixture, which is the coincidence the distinct values were
    # chosen to rule out.
    assert "Judged 9 of the 11 that passed the pre-gate" in body


def test_the_push_splits_unjudgeable_by_which_producer_made_it(monkeypatch, tmp_path):
    # #300 review: one aggregate hides two different operational problems. A JD that never
    # arrived points at the scraper or the network; a page that arrived and was not a
    # posting points at bot-blocking. They are fixed in different places, so a digest that
    # sums them tells the reader to go and look at the machine, which is the failure this
    # whole formatter exists to stop. Distinct values, per this file's own convention, so a
    # formatter that renders one number in the other's place cannot pass by coincidence.
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 0,
                         "needs_review": 0, "skipped": 0, "unjudgeable": 7},
                 unjudgeable_by={"pre_gate": 3, "judge": 4})

    assert "7 with no usable job description" in body
    assert "3 not fetched" in body
    assert "4 not a posting" in body


def test_the_push_names_only_the_unjudgeable_producer_that_fired(monkeypatch, tmp_path):
    # A zero term is the noise the zero-row rule already removes one level up; naming a
    # producer that contributed nothing invites the reader to go looking for it.
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 0,
                         "needs_review": 0, "skipped": 0, "unjudgeable": 5},
                 unjudgeable_by={"pre_gate": 0, "judge": 5})

    assert "5 not a posting" in body
    assert "not fetched" not in body


def test_the_push_omits_the_rows_that_are_zero(monkeypatch, tmp_path):
    # The reason the dict was unreadable was mostly the zeroes: five of the seven rows
    # say nothing on a typical run.
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 4,
                         "needs_review": 0, "skipped": 0, "unjudgeable": 0})

    assert "4 dismissed" in body
    assert "skipped" not in body and "need review" not in body and "no JD" not in body


def test_the_push_reports_failures(monkeypatch, tmp_path):
    # NEW to the push, which never carried a failure count -- `failures=N` is on the
    # stderr line, which nobody reads on an unattended install. It is the one number in
    # the digest that means "go and look".
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 1, "shortlist": 0, "research": 0, "dismiss": 0,
                         "needs_review": 0, "skipped": 0},
                 failures=["dossier Acme - Analyst.md: connection refused",
                           "judge 'ghost': no note matches this lead_id"])

    assert "2 failures, check the run log." in body


def test_a_clean_push_does_not_mention_failures(monkeypatch, tmp_path):
    # ...and says nothing when there are none, or the word stops meaning anything.
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 1, "shortlist": 0, "research": 0, "dismiss": 0,
                         "needs_review": 0, "skipped": 0})

    # Pinned against the FORMATTED body, not the old dict repr: asserting only the
    # absence of a word passes against a body that never had it, which proves nothing
    # about the formatter this test exists to constrain.
    assert "pre-gate" in body
    assert "failure" not in body.lower()


def test_the_held_push_does_not_promise_a_list_it_does_not_carry(monkeypatch, tmp_path):
    """The #223 hold pushed "Review these, then run it again to apply them:" -- a colon
    introducing a list that only ever went to stderr. On the unattended install the push
    exists for, that is a message ending mid-sentence."""
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report(
        reverdict_pending=["acme: pay was judged as day, now judged as annual"],
        reverdict_deferred=True))

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    assert not sent[0].rstrip().endswith(":"), "the push promises a list it never carries"
    assert "#223" in sent[0]


def test_the_applied_alert_does_not_repeat_the_product_name(monkeypatch, tmp_path):
    """The alert line and the digest headline each opened with "job-sluice triage", so
    the push said its own name twice in two lines.

    The FIRST line is what a phone renders as the preview, so that is the one that has to
    carry the name -- and on this arm it has to be the alert, because the run has just
    dismissed leads irreversibly.
    """
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report(
        reverdict_pending=["acme: pay was judged as day, now judged as annual"],
        reverdict_deferred=False,
        counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 1,
                "needs_review": 0, "skipped": 0}))

    args = _build_parser().parse_args(["triage", "run", "--no-llm"])
    assert cmd_triage_run(args, Config()) == 0
    assert sent[0].count("job-sluice") == 1
    assert sent[0].startswith("job-sluice triage APPLIED"), "the alert must lead"
    assert "1 dismissed" in sent[0], "...and the digest must survive underneath it"


def test_the_push_does_not_say_N_of_the_N(monkeypatch, tmp_path):
    """"Judged 18 of the 18 that passed the pre-gate" is the common case and reads as a
    template that forgot to collapse.

    The pre-gate total earns its place only when it DIFFERS from the judged count, which
    is the case where some of those leads did not reach the judge.
    """
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 18, "shortlist": 1, "research": 0, "dismiss": 0,
                         "needs_review": 0, "skipped": 0},
                 judged=18, backend="deepseek-v4-flash",
                 surfaced=[("shortlist", "Acme", "Engineering Manager")])

    assert "18 of the 18" not in body
    assert "Judged 18 via deepseek-v4-flash" in body


def test_the_push_still_shows_the_pre_gate_total_when_it_differs(monkeypatch, tmp_path):
    # The other arm: leads that passed the pre-gate and never reached the judge are the
    # reason to print both numbers at all.
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 18, "shortlist": 1, "research": 0, "dismiss": 0,
                         "needs_review": 0, "skipped": 0, "unjudgeable": 9},
                 judged=9, backend="deepseek-v4-flash",
                 surfaced=[("shortlist", "Acme", "Engineering Manager")])

    assert "9 of the 18 that passed the pre-gate" in body


def test_the_push_does_not_name_a_backend_called_None(monkeypatch, tmp_path):
    """`report.backend` is `getattr(backend, "last_backend", None)`, and `last_backend` is
    defined by ONE class -- `FallbackBackend`. `Sluice.backend()` returns a bare provider
    for `--backend primary`, for `--backend fallback`, and for `auto` when no fallback is
    configured, which `_make_fallback`'s own docstring calls legitimate and supported.

    So a judge run that made 12 real calls reports `backend=None`, and gating the wording
    on `judged` rather than on `backend` rendered "Judged 12 via None." -- a sentence
    asserting a backend of that name, which is worse than the bare null it replaced.
    """
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 12, "shortlist": 1, "research": 0, "dismiss": 5,
                         "needs_review": 0, "skipped": 0},
                 judged=12, backend=None,
                 surfaced=[("shortlist", "Acme", "Engineering Manager")])

    assert "None" not in body
    assert "Judged 12" in body, "the judged count is still the fact worth reporting"


def test_a_dry_run_push_says_it_wrote_nothing(monkeypatch, tmp_path):
    """A preview forces `outcome = "skipped"` for every lead, so `counts["shortlist"]`
    stays 0 and `surfaced` stays empty however good the run was.

    Rendered without a dry-run clause that reads "nothing surfaced this pass", which is a
    claim about the WORLD rather than about the run -- on a preview that may have judged
    18 leads and would have shortlisted five. The old dict at least showed `'skipped': 18`
    next to the zero.
    """
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: _report(
        counts={"keep": 18, "shortlist": 0, "research": 0, "dismiss": 0,
                "needs_review": 0, "skipped": 18},
        judged=18, backend="deepseek-v4-flash"))

    args = _build_parser().parse_args(["triage", "run", "--no-llm", "--dry-run"])
    assert cmd_triage_run(args, Config()) == 0
    assert "dry run" in sent[0].lower()
    assert "nothing surfaced this pass" not in sent[0], (
        "a preview has not established that nothing surfaced, only that nothing was written")


def test_the_push_never_starves_a_verdict_group_of_its_heading(monkeypatch, tmp_path):
    """One shared budget plus shortlist-first ordering erased `Research:` entirely on a
    run with six shortlists: the heading never rendered, so the reader could not tell that
    any research leads existed, let alone how many.

    Every non-empty group gets at least one name.
    """
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 8, "shortlist": 6, "research": 2, "dismiss": 0,
                         "needs_review": 0, "skipped": 0},
                 judged=8, backend="deepseek-v4-flash",
                 surfaced=[("shortlist", f"Shortlisted {n}", "EM") for n in range(6)] +
                          [("research", f"Researched {n}", "EM") for n in range(2)])

    assert "Research" in body, "the starved group lost its heading entirely"
    assert "- Researched 0" in body, "...and every one of its names"
    # The budget is SHARED, and the only capping test seats every lead in one group, where
    # a shared budget is indistinguishable from a per-group one. Pinned as an identity
    # over the whole message rather than as a literal, so it holds at any cap.
    named = body.count("\n- ")
    assert named == cli._PUSH_NAME_CAP, (
        f"the cap does not hold across groups: {named} names")
    assert "(3 more not named)" in body, "the tail must count only what was withheld"


def test_the_push_makes_each_verdict_count_recoverable(monkeypatch, tmp_path):
    """The headline sums shortlist and research, and the cap can hide names, so neither
    number was recoverable from the message. The heading carries its own count."""
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 8, "shortlist": 6, "research": 2, "dismiss": 0,
                         "needs_review": 0, "skipped": 0},
                 judged=8, backend="deepseek-v4-flash",
                 surfaced=[("shortlist", f"Shortlisted {n}", "EM") for n in range(6)] +
                          [("research", f"Researched {n}", "EM") for n in range(2)])

    assert "Shortlist (6):" in body
    assert "Research (2):" in body


def test_the_more_tail_counts_what_was_actually_named(monkeypatch, tmp_path):
    """The tail was `len(surfaced) - _PUSH_NAME_CAP`, which is not what the loop named:
    the loop only renders entries whose verdict is in `_status.SURFACED`, so a verdict
    outside it made the message contradict itself ("4 to look at", four names, "and 2
    more"). Derived from the loop, the arithmetic is right whatever reaches the list.
    """
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 7, "shortlist": 0, "research": 7, "dismiss": 0,
                         "needs_review": 0, "skipped": 0},
                 judged=7, backend="deepseek-v4-flash",
                 surfaced=[("research", f"Company {n}", "Delivery Lead")
                           for n in range(7)] +
                          [("dismiss", "Never Named", "Should Not Appear")])

    assert "Never Named" not in body
    assert "2 more" in body, "the tail must count unnamed SURFACED leads, not list length"
    # ...and it is NOT a bullet. Rendered as one it sits under the last heading and reads
    # as that group's remainder, when it is the total across every group.
    assert not any(line.startswith("- (") for line in body.splitlines()), (
        "the tail is rendered as a bullet under the last heading")


def test_lead_label_degrades_when_the_posting_named_no_company(monkeypatch, tmp_path):
    # `_lead_label`'s whole job is this edge case and nothing exercised it: a dangling
    # ", Engineering Manager" reads as a formatting bug rather than as missing data.
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 2, "shortlist": 2, "research": 0, "dismiss": 0,
                         "needs_review": 0, "skipped": 0},
                 judged=2, backend="deepseek-v4-flash",
                 surfaced=[("shortlist", "", "Engineering Manager"),
                           ("shortlist", "", "")])

    assert "- Engineering Manager" in body
    assert ", Engineering Manager" not in body
    assert "(unnamed lead)" in body


def test_every_counts_row_the_digest_can_receive_is_rendered_somewhere():
    """`_format_triage_digest`'s docstring promises that every NON-ZERO row survives. That
    was prose, and prose is the drift surface this repo keeps engineering out -- the first
    version of the promise was already false, because `shortlist` and `research` were
    summed into the headline and never named.

    Derived from BOTH producers rather than hand-listed:

    - `TriageReport().counts`' own keys, which is what the engine initialises; and
    - every verdict `clamp_verdict` can return, which is what the judge can ADD to that
      dict at runtime via `counts.get(key, 0) + 1`. A fourth entry in `_JUDGE_VERDICTS`
      would be counted, clamped and written while the digest silently never mentioned it,
      and the initialised-keys check alone cannot see that -- the new key is not in the
      default dict.
    """
    from sluice.triage.apply import _JUDGE_VERDICTS, clamp_verdict
    from sluice.triage.engine import TriageReport

    filtered_keys = {key for key, _ in cli._TRIAGE_FILTERED_WORDS}
    rendered = {"keep"} | set(_status.SURFACED) | filtered_keys
    # `clamp_verdict`'s fallback, derived rather than written as `{"needs_review"}` -- a
    # hand-listed literal in the one test whose whole argument is that both sides are
    # derived. Change that fallback and this follows it.
    reachable = (set(TriageReport().counts) | set(_JUDGE_VERDICTS)
                 | {clamp_verdict("__not_a_verdict__")})

    assert not (reachable - rendered), (
        f"the digest never mentions {sorted(reachable - rendered)}, so a run carrying it "
        "reports a number the reader cannot see")
    assert not (rendered - reachable), (
        f"the digest spells {sorted(rendered - reachable)}, which nothing can produce")
    # EXACTLY one place, which the union above cannot see: a verdict in both collapses to
    # one member and both differences stay empty, while the digest would name it as a
    # heading AND spell it as a filtered count in the same message.
    assert not (set(_status.SURFACED) & filtered_keys), (
        f"{sorted(set(_status.SURFACED) & filtered_keys)} is both named as a heading and "
        "counted as a word, so the same leads are reported twice")


def test_a_judge_outage_does_not_read_as_a_quiet_run(monkeypatch, tmp_path):
    """`triage/judge.py` swallows every backend error and parse failure -- twice per batch,
    on a bare `except Exception` -- and holds no reference to the report. So both legs
    down, a revoked key, an exhausted quota, or a TypeError in our own prompt building all
    returned an empty verdict list with `failures` untouched.

    The digest then said "no judge ran", which is the opposite of what happened, on the one
    channel an unattended install reads. Distinguishable now, and asserted as a DIFFERENCE
    rather than a phrase, so it cannot pass by both arms happening to say the same thing.
    """
    outage = _push(monkeypatch, tmp_path,
                   counts={"keep": 18, "shortlist": 0, "research": 0, "dismiss": 0,
                           "needs_review": 0, "skipped": 0},
                   judged=0, sent_to_judge=18, backend=None,
                   failures=["judge: 18 of 18 dossier(s) came back with no verdict"])
    no_llm = _push(monkeypatch, tmp_path,
                   counts={"keep": 18, "shortlist": 0, "research": 0, "dismiss": 0,
                           "needs_review": 0, "skipped": 0},
                   judged=0, sent_to_judge=0, backend=None)

    assert outage != no_llm, "an outage and a healthy --no-llm run read identically"
    assert "no judge ran" in no_llm, "the judge really was never called on this arm"
    assert "no judge ran" not in outage, "the judge ran; saying otherwise is false"
    assert "returned NOTHING" in outage and "failed" in outage


def test_one_lead_needing_review_is_not_pluralised(monkeypatch, tmp_path):
    # "1 need review" in a message whose entire purpose is being readable, ten lines above
    # a failure count that pluralises correctly.
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 0, "shortlist": 0, "research": 0, "dismiss": 1,
                         "needs_review": 1, "skipped": 0})

    assert "1 needs review" in body
    assert "1 need review" not in body


def test_the_headline_sums_both_surfaced_verdicts(monkeypatch, tmp_path):
    """Asymmetric on purpose: a headline built from one term alone still matches a
    symmetric fixture. Pins that BOTH verdicts reach the number."""
    research_only = _push(monkeypatch, tmp_path,
                          counts={"keep": 3, "shortlist": 0, "research": 3, "dismiss": 0,
                                  "needs_review": 0, "skipped": 0},
                          judged=3, sent_to_judge=3, backend="deepseek-v4-flash",
                          surfaced=[("research", f"Co {n}", "Delivery Lead")
                                    for n in range(3)])
    shortlist_only = _push(monkeypatch, tmp_path,
                           counts={"keep": 2, "shortlist": 2, "research": 0, "dismiss": 0,
                                   "needs_review": 0, "skipped": 0},
                           judged=2, sent_to_judge=2, backend="deepseek-v4-flash",
                           surfaced=[("shortlist", f"Co {n}", "EM") for n in range(2)])

    assert research_only.startswith("job-sluice triage: 3 to look at")
    assert shortlist_only.startswith("job-sluice triage: 2 to look at")


def test_the_name_budget_holds_with_the_small_group_first(monkeypatch, tmp_path):
    """The cap tests both seat the LARGE group first, where the first group exhausts the
    budget and an off-by-one in `take` cannot show. Two shortlist then six research is the
    commoner real shape, and there `1 + min(len(entries), spare)` under-fills the cap by
    one while over-stating the withheld count.
    """
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 8, "shortlist": 2, "research": 6, "dismiss": 0,
                         "needs_review": 0, "skipped": 0},
                 judged=8, sent_to_judge=8, backend="deepseek-v4-flash",
                 surfaced=[("shortlist", f"S{n}", "EM") for n in range(2)] +
                          [("research", f"R{n}", "DL") for n in range(6)])

    assert body.count("\n- ") == cli._PUSH_NAME_CAP
    assert "(3 more not named)" in body


def test_one_failure_is_not_pluralised(monkeypatch, tmp_path):
    # "2 failure" is a prefix of "2 failures", so the conditional was unpinned in both
    # directions and no case ever exercised a single failure.
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 1, "shortlist": 0, "research": 0, "dismiss": 0,
                         "needs_review": 0, "skipped": 0},
                 failures=["dossier Acme - Analyst.md: connection refused"])

    assert "1 failure, check the run log." in body
    assert "1 failures" not in body


def test_a_whitespace_only_company_is_treated_as_absent(monkeypatch, tmp_path):
    # `_lead_label` exists to stop a dangling ", Engineering Manager"; frontmatter
    # `company: " "` reaches it from a scraper, and only `""` was ever tested.
    body = _push(monkeypatch, tmp_path,
                 counts={"keep": 1, "shortlist": 1, "research": 0, "dismiss": 0,
                         "needs_review": 0, "skipped": 0},
                 judged=1, sent_to_judge=1, backend="deepseek-v4-flash",
                 surfaced=[("shortlist", "   ", "Engineering Manager")])

    assert "- Engineering Manager" in body
    assert ", Engineering Manager" not in body
