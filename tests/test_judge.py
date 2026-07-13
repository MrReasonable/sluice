from sluice.triage.judge import judge, parse_verdicts


def test_parse_clean_array():
    out = '[{"lead_id":"a","verdict":"shortlist","relevance_score":80}]'
    assert parse_verdicts(out)[0]["verdict"] == "shortlist"


def test_parse_with_surrounding_prose():
    out = 'Here you go:\n[{"lead_id":"a","verdict":"dismiss","relevance_score":10}]\nDone.'
    assert parse_verdicts(out)[0]["verdict"] == "dismiss"


def test_parse_garbage_returns_none():
    assert parse_verdicts("no json here") is None


class _Backend:
    def __init__(self, per_batch):
        self.per_batch, self.prompts = per_batch, []
    def complete(self, prompt):
        self.prompts.append(prompt)
        return self.per_batch.pop(0)


def test_judge_batches_and_collects():
    dossiers = [{"lead_id": str(i), "company": "C", "jd": {"markdown": "j"}}
                for i in range(3)]
    be = _Backend(per_batch=[
        '[{"lead_id":"0","verdict":"shortlist","relevance_score":80},'
        '{"lead_id":"1","verdict":"research","relevance_score":60}]',
        '[{"lead_id":"2","verdict":"dismiss","relevance_score":20}]',
    ])
    verdicts = judge(dossiers, be, batch_size=2)
    assert len(verdicts) == 3
    assert len(be.prompts) == 2                 # two batches
    assert {v["lead_id"] for v in verdicts} == {"0", "1", "2"}


def test_judge_skips_unparseable_batch():
    dossiers = [{"lead_id": "0", "jd": {"markdown": "j"}}]
    be = _Backend(per_batch=["garbage", "garbage"])  # first + one retry
    assert judge(dossiers, be, batch_size=1) == []
