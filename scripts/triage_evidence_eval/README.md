# Triage candidate-record eval (#196)

A dev-only harness for the live eval in the #196 spec (section 7). It judges a small set of
synthetic leads twice, once with the baseline Judging Profile and once with the verified
candidate record in the prompt, `--repeats` times each (default 5; it must be odd, so the median verdict is a whole band), and scores whether the
record lowered any band or score, and whether the judge named the requirement the
record does not cover. All inputs are in `fixtures.json` (synthetic; a digest in
`tests/test_triage_evidence_eval.py` summons a human reader when it changes). The tests cover
the loader, the fixtures' neutrality and the scorer only; **CI never runs the eval.**

## Running

**No run happens without the owner's explicit go-ahead.** It makes `2 x --repeats` real judge
calls on the CONFIGURED triage backend, and the script refuses without `--yes`. The first run
is reported to the owner (calls, duration) before any further run. `--out` is written as
soon as the calls finish, before scoring, so the paid verdicts survive a scoring error. A
verdict the engine's own `normalise_verdict` rejects counts as missing for its lead.
A backend outage stops the run unscored with exit 3, after writing the repeats collected
so far to `--out`.

```bash
python -m scripts.triage_evidence_eval.run --yes --out "$(mktemp -d)/eval.json"
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | `OVERALL: PASS` |
| 1 | `OVERALL: FAIL` or `OVERALL: INCONCLUSIVE` |
| 2 | bad arguments (argparse's own exit, e.g. an even `--repeats`), or `--yes` missing; no backend call was made |
| 3 | stopped by a backend outage, unscored; the repeats collected so far are written to `--out` when it is given |

## Reading the result

Per lead: `PASS`, `FAIL`, or `INVALID` (the no-record baseline did not reach the expected
verdict, so that lead measures nothing). `OVERALL` is `PASS`, `FAIL` or `INCONCLUSIVE`. Only
`OVERALL: PASS` lets `docs/CONFIGURATION.md` recommend `triage.evidence`. A pass never changes
a default: the key stays off.
