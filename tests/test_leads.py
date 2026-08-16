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
                url="https://example.invalid/jobs/view/44268/#applied")
    assert lead.dedup_key == "https://example.invalid/jobs/view/44268/"


def test_dedup_key_falls_back_to_title_company_when_no_url():
    a = Lead(source="s", search="x", title="Eng Mgr", company="Acme")
    b = Lead(source="s2", search="y", title="Eng Mgr", company="Acme")
    assert a.dedup_key.startswith("h:")
    assert a.dedup_key == b.dedup_key


def test_dedup_key_splits_urlless_leads_by_location():
    # #23: a url-less lead's read-key MUST include location, or the engine collapses two
    # jobs at DIFFERENT locations before the store's #5 split can run -- defeating the split
    # end-to-end. Same title+company, different city -> different key. (Synthetic cities per
    # the tests/test_leads_location.py convention -- real place names belong in fixtures, not
    # in tests/.)
    a = Lead(source="s", search="x", title="Eng Mgr", company="Acme", location="Palmerburgh")
    b = Lead(source="s", search="x", title="Eng Mgr", company="Acme", location="Clarkefurt")
    assert a.dedup_key != b.dedup_key


def test_dedup_key_urlless_same_location_is_stable():
    # The accepted merge is preserved: same title+company+location (two teams, one city, no
    # url) still shares a key, so the engine still collapses genuine in-run duplicates.
    a = Lead(source="s", search="x", title="Eng Mgr", company="Acme", location="Palmerburgh")
    b = Lead(source="s2", search="y", title="Eng Mgr", company="Acme", location="Palmerburgh")
    assert a.dedup_key == b.dedup_key


def test_dedup_key_urlless_location_is_nfkd_normalized():
    # Location is folded the same way the store compares it, so an accented spelling of one
    # city (NFKD: ä -> a) still collapses rather than minting a duplicate read-key.
    a = Lead(source="s", search="x", title="Eng Mgr", company="Acme", location="Pälmerburgh")
    b = Lead(source="s", search="x", title="Eng Mgr", company="Acme", location="Palmerburgh")
    assert a.dedup_key == b.dedup_key


def test_dedup_key_urlless_location_is_case_folded():
    # ...and a pure case variant of one city collapses too (casefold, a distinct facet from
    # the NFKD fold above).
    a = Lead(source="s", search="x", title="Eng Mgr", company="Acme", location="Palmerburgh")
    b = Lead(source="s", search="x", title="Eng Mgr", company="Acme", location="PALMERBURGH")
    assert a.dedup_key == b.dedup_key


def test_dedup_key_urlless_field_join_is_unambiguous():
    # A delimiter char INSIDE a field must not forge a collision: title="a", company="b|c"
    # and title="a|b", company="c" would both flatten to the same "a|b|c" under a naive
    # join -- one genuinely different lead then silently dropped as "already seen".
    a = Lead(source="s", search="x", title="a", company="b|c", location="Palmerburgh")
    b = Lead(source="s", search="x", title="a|b", company="c", location="Palmerburgh")
    assert a.dedup_key != b.dedup_key


def test_dedup_key_url_present_ignores_location():
    # The URL branch is the identity when a url exists; location must NOT enter the key there
    # (guards against a future "fold location everywhere" regression).
    a = Lead(source="s", search="x", title="Eng Mgr", company="Acme",
             url="https://a/1", location="Palmerburgh")
    b = Lead(source="s", search="x", title="Eng Mgr", company="Acme",
             url="https://a/1", location="Clarkefurt")
    assert a.dedup_key == b.dedup_key == "https://a/1"


def test_slug_is_filesystem_safe():
    lead = Lead(source="s", search="x", title="Analyst/Lead", company="A&B")
    assert "/" not in lead.slug
    assert lead.slug == "a-b-analyst-lead"


def test_slug_never_empty():
    assert Lead(source="s", search="x", title="", company="").slug == "lead"
