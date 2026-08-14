"""Vault._render_new's frontmatter-injection guard (#131 decision 7): the 5
non-identity interpolated fields (location, salary, role_type, url, source) abstain
-and-blank on an unsafe value, never raise -- one bad scraped field must not sink the
whole create. This is a live gap #131 closes independent of create_lead: ingest/base.py's
`.strip()` leaves an embedded newline intact, so a hostile scraped field can already
forge a frontmatter key today. company/role are tested separately in
tests/test_vault.py, since they're the vault's IDENTITY key and get a narrower,
refuse-the-whole-create treatment instead (decision 7's round-3 correction)."""
from sluice.core.leads import Lead
from sluice.core.vault import Vault


def _lead(**kw):
    defaults = dict(source="s", search="q", title="Example Role", company="Example Ltd",
                    url="https://example.invalid/1")
    defaults.update(kw)
    return Lead(**defaults)


def test_an_embedded_newline_in_location_does_not_forge_a_frontmatter_key(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(location='Remote\nstatus: applied')) == "created"
    note = v.read_leads()[0]
    assert note.fm.get("location", "") == ""       # whole unsafe value refused, not truncated
    assert note.status == "new"                    # NOT forged to "applied"


def test_a_safe_location_survives_render_new_unchanged(tmp_path):
    # The companion positive case (round-2 test-engineer finding): without this, an
    # over-broad "abstain everything unconditionally" mutant would also pass.
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(location="Remote, UK")) == "created"
    note = v.read_leads()[0]
    assert note.fm["location"] == "Remote, UK"


def test_an_embedded_quote_in_url_abstains_with_a_warning_not_a_raise(tmp_path, caplog):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(url='https://x/"; status: applied')) == "created"
    note = v.read_leads()[0]
    assert note.fm.get("url", "") == ""
    assert any("not frontmatter-safe" in r.message for r in caplog.records)


def test_salary_role_type_source_are_each_independently_guarded(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(salary="80k\nstatus: applied", job_type='perm"',
                          source="scrape\n")) == "created"
    note = v.read_leads()[0]
    assert note.fm.get("salary", "") == ""
    assert note.fm.get("role_type", "") == ""
    assert note.fm.get("source", "") == ""
    assert note.status == "new"
