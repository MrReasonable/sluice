import os

import pytest

from sluice.core.leads import Lead
from sluice.core.vault import Vault, _clamp_bytes


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


def test_clamp_bytes_keeps_string_within_budget_unchanged():
    assert _clamp_bytes("hello", 100) == "hello"


def test_clamp_bytes_truncates_ascii_to_byte_budget():
    assert _clamp_bytes("hello", 3) == "hel"


def test_clamp_bytes_never_splits_a_multibyte_codepoint():
    # "測" encodes to 3 UTF-8 bytes. A 4-byte budget must keep exactly one whole
    # char, never one-and-a-fraction — the guarantee _path_for relies on.
    out = _clamp_bytes("測測", 4)
    assert out == "測"
    assert len(out.encode("utf-8")) <= 4
    out.encode("utf-8").decode("utf-8")  # must be valid UTF-8 (no exception)


def test_clamp_bytes_boundary_exact_and_too_small():
    assert _clamp_bytes("測", 3) == "測"   # exact fit
    assert _clamp_bytes("測", 2) == ""     # cannot fit even one whole char


def test_clamp_bytes_non_positive_budget_yields_empty():
    # A non-positive byte budget holds nothing. Without the guard the negative slice
    # keeps all-but-the-last-few bytes (s.encode()[:-1]), silently defeating the cap;
    # "" is the only correct answer. _name_max keeps production budgets positive, so
    # this pins the pure helper's totality as defence-in-depth.
    assert _clamp_bytes("hello", 0) == ""
    assert _clamp_bytes("hello", -1) == ""
    assert _clamp_bytes("hello", -4) == ""


def test_long_non_ascii_name_fits_the_byte_budget(tmp_path):
    # A 120-CHARACTER CJK company is ~360 bytes — over NAME_MAX. Inject a small
    # budget so the assertion is filesystem-independent, then verify the written
    # filename fits it and decodes cleanly (no split codepoint).
    v = Vault(str(tmp_path))
    v._name_max_cache = 64
    v.upsert(_lead(company="測" * 200, title="X"))
    files = list(_leads_dir(tmp_path).glob("*.md"))
    assert len(files) == 1
    name = files[0].name
    assert len(name.encode("utf-8")) <= 64        # whole filename within budget
    name.encode("utf-8").decode("utf-8")          # valid UTF-8, no partial char


def test_byte_clamp_is_a_noop_for_a_name_that_fits(tmp_path):
    # never-clobber guard: a name already within the byte budget MUST keep the exact
    # char-capped path it has today, or a re-scrape would create a duplicate note.
    # Inject the budget (255) explicitly so the assertion does NOT silently ride the
    # pathconf-failure fallback: _path_for is called directly here, so leads_dir does
    # not exist yet and a real os.pathconf would raise.
    v = Vault(str(tmp_path))
    v._name_max_cache = 255
    lead = _lead(company="X" * 200, title="Y")     # f-string -> 120 'X' after [:120]
    expected = _leads_dir(tmp_path) / ("X" * 120 + ".md")
    assert v._path_for(lead) == str(expected)


def test_name_max_reads_pathconf(tmp_path, monkeypatch):
    # The SUCCESS branch: _name_max returns the filesystem's real PC_NAME_MAX, not the
    # 255 fallback. Mock pathconf to a non-255 value so a hardcoded-255 mutant reddens
    # even on a 255-limit filesystem where the fallback would otherwise mask it.
    v = Vault(str(tmp_path))
    os.makedirs(v.leads_dir, exist_ok=True)        # pathconf needs an existing path
    monkeypatch.setattr(os, "pathconf", lambda *a: 143)
    assert v._name_max() == 143


def test_name_max_falls_back_when_pathconf_unsupported(tmp_path, monkeypatch):
    # The FALLBACK branch: pathconf unsupported (some network/FUSE mounts) -> 255.
    v = Vault(str(tmp_path))
    def _boom(*a):
        raise OSError("PC_NAME_MAX unsupported")
    monkeypatch.setattr(os, "pathconf", _boom)
    assert v._name_max() == 255


def test_name_max_falls_back_when_pathconf_returns_negative_one(tmp_path, monkeypatch):
    # POSIX pathconf RETURNS -1 (a value, not an exception) when NAME_MAX is
    # indeterminate. Uncaught, that -1 caches and drives _path_for's byte budget to -4,
    # so _clamp_bytes negative-slices EVERY name -> a vault-wide rename/duplicate. A
    # non-positive limit must take the 255 fallback, same as the exception path.
    v = Vault(str(tmp_path))
    monkeypatch.setattr(os, "pathconf", lambda *a: -1)
    assert v._name_max() == 255


def test_upsert_removes_partial_note_when_create_write_fails(tmp_path, monkeypatch):
    # A create whose write fails mid-way must not leave a partial file: open("w")
    # truncates/creates at open time, and a lingering 0-byte note would be treated as
    # real on the next re-scrape (exists -> "updated" -> last_seen bumped on garbage).
    import sluice.core.vault as vault_mod
    v = Vault(str(tmp_path))

    def failing_write(p, text):
        with open(p, "w", encoding="utf-8"):   # leave a 0-byte file, as open("w") does
            pass
        raise OSError("disk full mid-write")

    monkeypatch.setattr(vault_mod, "_write", failing_write)
    with pytest.raises(OSError):
        v.upsert(_lead())
    assert list(_leads_dir(tmp_path).glob("*.md")) == []   # partial artifact removed


def test_note_name_candidate1_matches_path_for(tmp_path):
    v = Vault(str(tmp_path))
    v._name_max_cache = 255
    assert v._note_name("Acme - Analyst") == "Acme - Analyst"


def test_note_name_suffix_appends_sanitized_location(tmp_path):
    v = Vault(str(tmp_path))
    v._name_max_cache = 255
    assert v._note_name("Acme - Analyst", "aaa/bbb") == "Acme - Analyst - aaa-bbb"


def test_note_name_bounds_suffix_so_stem_budget_never_negative(tmp_path):
    v = Vault(str(tmp_path))
    v._name_max_cache = 255
    out = v._note_name("C" * 200, "L" * 200)       # a 200-char location is clamped to _SUFFIX_MAX(40)
    stem, _, suffix = out.rpartition(" - ")
    assert len(suffix) == 40 and len(stem) == 120 - len(" - ") - 40
