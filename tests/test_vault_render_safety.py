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
    assert v.upsert(_lead(location='Remote\nstatus: applied')).outcome == "created"
    note = v.read_leads()[0]
    assert note.fm.get("location", "") == ""       # whole unsafe value refused, not truncated
    assert note.status == "new"                    # NOT forged to "applied"
    # The body's own "**Location:**" line must use the SAME blanked value as frontmatter --
    # not the raw lead.location -- or the unsafe text still reaches disk verbatim, one block
    # lower (round-2 review finding: _render_new sanitised location/salary/url for
    # frontmatter but interpolated the raw Lead fields into the body regardless).
    assert "status: applied" not in note.body


def test_a_safe_location_survives_render_new_unchanged(tmp_path):
    # The companion positive case (round-2 test-engineer finding): without this, an
    # over-broad "abstain everything unconditionally" mutant would also pass.
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(location="Remote, UK")).outcome == "created"
    note = v.read_leads()[0]
    assert note.fm["location"] == "Remote, UK"
    assert "Remote, UK" in note.body


def test_an_embedded_quote_in_url_abstains_with_a_warning_not_a_raise(tmp_path, caplog):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(url='https://x/"; status: applied')).outcome == "created"
    note = v.read_leads()[0]
    assert note.fm.get("url", "") == ""
    assert any("not frontmatter-safe" in r.message for r in caplog.records)
    assert "status: applied" not in note.body


def test_an_unsafe_salary_blanks_only_salary_leaving_role_type_and_source_intact(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(salary="80k\nstatus: applied", job_type="perm",
                          source="scrape")).outcome == "created"
    note = v.read_leads()[0]
    assert note.fm.get("salary", "") == ""
    assert note.fm.get("role_type", "") == "perm"
    assert note.fm.get("source", "") == "scrape"
    assert note.status == "new"
    assert "status: applied" not in note.body       # body's Salary line must also be blanked


def test_an_unsafe_role_type_blanks_only_role_type_leaving_salary_and_source_intact(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(salary="80k", job_type='perm"',
                          source="scrape")).outcome == "created"
    note = v.read_leads()[0]
    assert note.fm.get("salary", "") == "80k"
    assert note.fm.get("role_type", "") == ""
    assert note.fm.get("source", "") == "scrape"
    assert note.status == "new"


def test_an_unsafe_source_blanks_only_source_leaving_salary_and_role_type_intact(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(salary="80k", job_type="perm",
                          source="scrape\n")).outcome == "created"
    note = v.read_leads()[0]
    assert note.fm.get("salary", "") == "80k"
    assert note.fm.get("role_type", "") == "perm"
    assert note.fm.get("source", "") == ""
    assert note.status == "new"
