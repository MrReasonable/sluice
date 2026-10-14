"""Run the #196 live eval against the CONFIGURED triage backend. Spends real calls
(2 x --repeats judge calls), so it refuses without --yes; the first run's cost is
reported to the owner before any further run (README)."""
import argparse
import json
import sys
from pathlib import Path

from scripts.triage_evidence_eval import fixtures, score

FIX = Path(__file__).with_name("fixtures.json")


def _judge_all(lead_list, system_prompt, backend):
    """One judge call over every lead: lead id -> its NORMALISED verdict, or None.

    Every raw verdict goes through `sluice.triage.apply.normalise_verdict`, the engine's own
    gate, so the eval scores exactly what a real run would apply. A verdict that gate
    rejects (not an object, no usable `lead_id`, no usable verdict) counts as MISSING for
    its lead rather than raising: one malformed item must not lose a whole paid run, and
    `score.lead_result` already treats a missing run as INVALID or FAIL."""
    from sluice.triage.apply import normalise_verdict
    from sluice.triage.judge import judge
    dossiers = [{"lead_id": lead.id, "company": lead.company, "position": lead.title,
                 "location": lead.location, "jd": {"markdown": lead.jd}}
                for lead in lead_list]
    got = {}
    for raw in judge(dossiers, backend, batch_size=len(dossiers),
                     system_prompt=system_prompt):
        verdict, _why = normalise_verdict(raw)
        if verdict is not None:
            got[verdict["lead_id"]] = verdict
    return {lead.id: got.get(lead.id) for lead in lead_list}


def _odd(text):
    """`--repeats` must be odd: the median of an even count of ranks can be a half
    (1.5 between `research` and `shortlist`), which equals no expected band and would read
    every such lead as INVALID for a reason that is the harness's, not the judge's."""
    n = int(text)
    if n < 1 or n % 2 == 0:
        raise argparse.ArgumentTypeError(f"--repeats must be a positive odd number, got {n}")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scripts.triage_evidence_eval.run")
    ap.add_argument("--repeats", type=_odd, default=5)
    ap.add_argument("--yes", action="store_true", help="confirm: this spends real backend calls")
    ap.add_argument("--out", help="write per-run verdicts as JSON here")
    args = ap.parse_args(argv)
    if not args.yes:
        print("refusing: this spends real backend calls. Re-run with --yes after the "
              "owner's go-ahead.", file=sys.stderr)
        raise SystemExit(2)
    from sluice.core.app import Sluice
    from sluice.core.protocols import EVIDENCE_KINDS
    from sluice.triage.config import load_triage_config
    from sluice.triage.prompt import build_system_prompt_from
    f = fixtures.load(FIX)
    tcfg = load_triage_config()
    backend = Sluice.from_config_file().backend(
        provider=tcfg.backend, model=tcfg.model, effort=tcfg.claude_max_effort,
        host=tcfg.claude_max_host, claude_path=tcfg.claude_max_path)
    # Derived the way the engine derives it (non-empty judge_fields), never hand-listed.
    record = [(spec, f.record.get(kind, [])) for kind, spec in EVIDENCE_KINDS.items()
              if spec.judge_fields]
    prompts = {"baseline": build_system_prompt_from(f.profile),
               "with_record": build_system_prompt_from(f.profile, record)}
    # A plain loop rather than a comprehension: a transient outage raises `JudgeAborted`
    # out of `judge()`, and inside a comprehension that discards every repeat already
    # paid for. Here the partial `runs` survives to be written, and the run stops unscored
    # -- a short condition would be scored on fewer repeats than the other, which the
    # median comparison does not account for.
    from sluice.triage.judge import JudgeAborted
    runs = {cond: [] for cond in prompts}
    try:
        for cond, p in prompts.items():
            for _ in range(args.repeats):
                runs[cond].append(_judge_all(f.leads, p, backend))
    except JudgeAborted as e:
        done = sum(len(r) for r in runs.values())
        if args.out:
            Path(args.out).write_text(json.dumps({"runs": runs, "aborted": str(e.cause)},
                                                 indent=2))
        print(f"stopped on a backend outage after {done} of {2 * args.repeats} repeats, "
              f"unscored: {e.cause}", file=sys.stderr)
        return 3
    # Written BEFORE scoring: the calls are the paid part, so a scorer that raises must not
    # take the collected verdicts with it. Rewritten below with the results added.
    if args.out:
        Path(args.out).write_text(json.dumps({"runs": runs}, indent=2))
    results = {lead.id: score.lead_result([r[lead.id] for r in runs["baseline"]],
                                          [r[lead.id] for r in runs["with_record"]], lead)
               for lead in f.leads}
    for lid, res in results.items():
        print(f"{lid}: {res}")
    verdict = score.overall(results, f.leads)
    print(f"OVERALL: {verdict}")
    if args.out:
        Path(args.out).write_text(json.dumps({"runs": runs, "results": results,
                                              "overall": verdict}, indent=2))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
