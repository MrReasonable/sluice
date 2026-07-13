from sluice.core.leads import Lead
from sluice.core.vault import Vault


def _lead(**kw):
    base = dict(
        source="cord", search="Analyst", title="Analyst", company="Acme",
        url="https://a/1", location="London", salary="£100k",
        job_type="permanent", first_seen="2026-07-07", last_seen="2026-07-07",
    )
    base.update(kw)
    return Lead(**base)


def _leads_dir(tmp_path):
    return tmp_path / "Job Applications" / "Job Leads"


def test_create_writes_lead_note_in_vault_schema(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()) == "created"
    f = _leads_dir(tmp_path) / "Acme - Analyst.md"
    assert f.exists()
    txt = f.read_text()
    assert 'company: "Acme"' in txt
    assert 'role: "Analyst"' in txt            # title maps to the vault's `role`
    assert 'role_type: "permanent"' in txt
    assert "status: new" in txt
    assert 'url: "https://a/1"' in txt
    assert "first_seen: 2026-07-07" in txt


def test_update_preserves_status_and_enrichment_and_body_bumps_last_seen(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()) == "created"
    f = _leads_dir(tmp_path) / "Acme - Analyst.md"
    # An agent later triages: sets status + score + notes, adds a body note.
    f.write_text(
        f.read_text()
        .replace("status: new", "status: shortlisted")
        .replace("score: 0", "score: 87")
        .replace('relevance_notes: ""', 'relevance_notes: "great fit"')
        + "\nAgent added this body note.\n"
    )
    # A later scan re-surfaces the same lead with a newer date.
    assert v.upsert(_lead(last_seen="2026-07-09")) == "updated"
    txt = f.read_text()
    assert "status: shortlisted" in txt           # NOT clobbered
    assert "status: new" not in txt
    assert "score: 87" in txt                      # enrichment preserved
    assert 'relevance_notes: "great fit"' in txt   # preserved
    assert "Agent added this body note." in txt    # body preserved
    assert "last_seen: 2026-07-09" in txt           # bumped


def test_update_adds_last_seen_when_missing(tmp_path):
    # A pre-existing note from the OLD pipeline has no last_seen field.
    d = _leads_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "Acme - Analyst.md").write_text(
        '---\ncompany: "Acme"\nrole: "Analyst"\nstatus: research\n'
        'url: "https://a/1"\n---\n\n# body kept\n'
    )
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(last_seen="2026-07-09")) == "updated"
    txt = (d / "Acme - Analyst.md").read_text()
    assert "status: research" in txt
    assert "last_seen: 2026-07-09" in txt
    assert "# body kept" in txt


def test_existing_keys_returns_dedup_keys(tmp_path):
    v = Vault(str(tmp_path))
    v.upsert(_lead(url="https://a/1?ref=x"))
    # full link kept (only #fragment dropped), matching Lead.dedup_key
    assert "https://a/1?ref=x" in v.existing_keys()


def test_existing_keys_empty_when_no_vault(tmp_path):
    assert Vault(str(tmp_path)).existing_keys() == set()


def test_filename_sanitizes_slashes_and_colons(tmp_path):
    v = Vault(str(tmp_path))
    v.upsert(_lead(company="A/B", title="Lead: Analyst"))
    assert (_leads_dir(tmp_path) / "A-B - Lead- Analyst.md").exists()


def test_ensure_stfolder(tmp_path):
    v = Vault(str(tmp_path))
    v.ensure_stfolder()
    assert (tmp_path / ".stfolder").is_dir()
