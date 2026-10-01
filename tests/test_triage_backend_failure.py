"""#333: a triage run whose backend is down -- after `RetryingBackend` has already retried
it -- stops calling it, says so, and exits non-zero.

Before #333 `judge()` swallowed every backend error, retried each batch once more on top of
whatever the backend did, and moved on to the next batch: an outage cost two calls per batch
and surfaced only as "returned NOTHING" in the digest, with exit 0.
"""
from sluice import cli
from sluice.cli import _build_parser, cmd_triage_run
from sluice.core.app import Sluice
from sluice.core.backends import BackendError, Completion
from sluice.core.config import Config
from sluice.core.vault import Vault
from sluice.triage.audit import AuditLog
from sluice.triage.config import TriageConfig
from sluice.triage.engine import TriageReport, run
from sluice.triage.judge import JudgeAborted, judge
from tests.test_triage_engine import (_LLM_DOSSIER, _RecordingCache, _ResolveBackend,
                                      _blank_fields, _cache, _fields, _note)

_DOWN = "deepseek m: HTTP 503 from https://api.example.invalid after 3 attempts"


class _Down:
    def __init__(self):
        self.calls = 0

    def complete(self, prompt):
        self.calls += 1
        raise BackendError(_DOWN)


def test_a_backend_error_aborts_the_remaining_batches_and_keeps_what_completed():
    verdict = ('[{"lead_id":"l0","verdict":"dismiss","relevance_score":1,'
               '"fit_reasoning":"","concerns":[],"culture_flags":[],'
               '"recommended_next_action":""}]')

    class _FirstThenDown:
        calls = 0

        def complete(self, prompt):
            _FirstThenDown.calls += 1
            if _FirstThenDown.calls == 1:
                return Completion(verdict)
            raise BackendError(_DOWN)

    ds = [{"lead_id": f"l{i}"} for i in range(15)]
    try:
        judge(ds, _FirstThenDown(), batch_size=5)
    except JudgeAborted as e:
        assert [v["lead_id"] for v in e.verdicts] == ["l0"]
        assert str(e.cause) == _DOWN
    else:
        raise AssertionError("expected JudgeAborted")
    # One call for the batch that succeeded, ONE for the batch that failed -- no per-batch
    # retry on top of the backend's own, and no third batch attempted at all.
    assert _FirstThenDown.calls == 2


def test_a_parse_failure_still_gets_its_one_retry_and_skips_only_its_batch():
    replies = iter(["nonsense", "nonsense",
                    '[{"lead_id":"l5","verdict":"dismiss","relevance_score":1,'
                    '"fit_reasoning":"","concerns":[],"culture_flags":[],'
                    '"recommended_next_action":""}]'])

    class _B:
        def complete(self, prompt):
            return Completion(next(replies))

    out = judge([{"lead_id": f"l{i}"} for i in range(6)], _B(), batch_size=5)
    assert [v["lead_id"] for v in out] == ["l5"]


def test_the_engine_records_a_judge_backend_outage(tmp_path, titles):
    accept, _reject = titles
    v = Vault(str(tmp_path / "vault"))
    _note(v, "acme.md", _fields("Acme", accept[0].title()))
    cfg = TriageConfig()
    cfg.accept_titles = list(accept)
    backend = _Down()

    report = run(v, cfg, backend, _cache(tmp_path), AuditLog(str(tmp_path / "a.jsonl")),
                 statuses=("new",))

    assert report.backend_error == _DOWN
    assert backend.calls == 1
    assert report.judged == 0
    assert v.read_leads()[0].status == "new", "an unjudged lead keeps its status"


def test_a_tripped_tier3_breaker_is_a_backend_outage_too(tmp_path, titles):
    accept, _reject = titles
    v = Vault(str(tmp_path / "vault"))
    for i in range(4):
        _note(v, f"blank{i}.md", _blank_fields(accept[0].title(), source="ex-board",
                                                url=f"https://x/{i}"))
    cfg = TriageConfig()
    cfg.company_resolve_fetch = True
    cfg.company_resolve_llm = True
    report = run(v, cfg, None, _RecordingCache(dossier=_LLM_DOSSIER),
                 AuditLog(str(tmp_path / "a.jsonl")), statuses=("new",),
                 get_source=None, resolve_backend=_ResolveBackend([BackendError("down")] * 3))
    assert "tier 3" in report.backend_error


def _run_cli(monkeypatch, tmp_path, report):
    sent = []
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_notify_reporting", lambda msg, **kw: sent.append(msg))
    monkeypatch.setattr(Sluice, "triage", lambda self, **kw: report)
    code = cmd_triage_run(_build_parser().parse_args(["triage", "run"]), Config())
    return code, sent


def test_the_cli_exits_non_zero_and_says_so_on_every_channel(monkeypatch, tmp_path, capsys):
    report = TriageReport(sent_to_judge=3, judged=0, backend="deepseek m",
                          backend_error=_DOWN)
    code, sent = _run_cli(monkeypatch, tmp_path, report)
    assert code == 1
    err = capsys.readouterr().err
    assert "backend unavailable" in err and _DOWN in err
    # The push is what an unattended install reads; stderr under cron goes nowhere.
    assert len(sent) == 1 and "backend unavailable" in sent[0].lower()


def test_a_healthy_run_still_exits_zero(monkeypatch, tmp_path):
    code, _sent = _run_cli(monkeypatch, tmp_path, TriageReport())
    assert code == 0


def test_a_non_transient_failure_skips_only_its_own_batch():
    """A truncated or rejected batch is a property of THAT batch's prompt; aborting the judge
    on it would fail every run on the same leads for good. Skipped like a parse failure --
    and not re-sent, since the identical prompt fails identically."""
    verdict = ('[{"lead_id":"l5","verdict":"dismiss","relevance_score":1,'
               '"fit_reasoning":"","concerns":[],"culture_flags":[],'
               '"recommended_next_action":""}]')

    class _FirstBatchTooLong:
        calls = 0

        def complete(self, prompt):
            _FirstBatchTooLong.calls += 1
            if _FirstBatchTooLong.calls == 1:
                raise BackendError("response incomplete (finish_reason=length)",
                                   transient=False)
            return Completion(verdict)

    out = judge([{"lead_id": f"l{i}"} for i in range(6)], _FirstBatchTooLong(), batch_size=5)
    assert [v["lead_id"] for v in out] == ["l5"]
    assert _FirstBatchTooLong.calls == 2
