from sluice.core.leads import Lead


def test_dedup_key_preserves_query_job_id():
    # eighty_k (?jobId=) and indeed (?jk=) encode the job id in the query, so
    # distinct query params MUST yield distinct keys - stripping the query
    # collapsed 24 distinct 80k jobs into 1 (the bug this fixes).
    a = Lead(source="eighty_k", search="x", title="Analyst",
             url="https://jobs.80000hours.org/?jobId=1")
    b = Lead(source="eighty_k", search="x", title="Analyst",
             url="https://jobs.80000hours.org/?jobId=2")
    assert a.dedup_key != b.dedup_key


def test_dedup_key_is_full_link_minus_fragment():
    # dedup_key keeps the query, case, and trailing slash (only #fragment is
    # dropped) so it matches the full-link format already stored in the legacy
    # seen.db - a clean cutover with no re-surfacing.
    lead = Lead(source="li", search="x", title="Analyst",
                url="https://www.linkedin.com/jobs/view/44268/#applied")
    assert lead.dedup_key == "https://www.linkedin.com/jobs/view/44268/"


def test_dedup_key_falls_back_to_title_company_when_no_url():
    a = Lead(source="s", search="x", title="Eng Mgr", company="Acme")
    b = Lead(source="s2", search="y", title="Eng Mgr", company="Acme")
    assert a.dedup_key.startswith("h:")
    assert a.dedup_key == b.dedup_key


def test_slug_is_filesystem_safe():
    lead = Lead(source="s", search="x", title="Analyst/Lead", company="A&B")
    assert "/" not in lead.slug
    assert lead.slug == "a-b-analyst-lead"


def test_slug_never_empty():
    assert Lead(source="s", search="x", title="", company="").slug == "lead"
