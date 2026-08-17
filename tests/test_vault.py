import os

import pytest

from sluice.core.leads import Lead
from sluice.core.vault import _CREATE_RACE_RETRIES, Vault, _clamp_bytes, _sanitize, _title_digest
from tests.conftest import LOCATIONS


def _lead(**kw):
    base = dict(
        source="cord", search="Analyst", title="Analyst", company="Acme",
        url="https://a/1", location=LOCATIONS[0], salary="£100k",
        job_type="permanent", first_seen="2026-07-07", last_seen="2026-07-07",
    )
    base.update(kw)
    return Lead(**base)


def _leads_dir(tmp_path):
    return tmp_path / "Job Applications" / "Job Leads"


def test_create_writes_lead_note_in_vault_schema(tmp_path):
    v = Vault(str(tmp_path))
    assert v.upsert(_lead()).outcome == "created"
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
    assert v.upsert(_lead()).outcome == "created"
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
    assert v.upsert(_lead(last_seen="2026-07-09")).outcome == "updated"
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
    assert v.upsert(_lead(last_seen="2026-07-09")).outcome == "updated"
    txt = (d / "Acme - Analyst.md").read_text()
    assert "status: research" in txt
    assert "last_seen: 2026-07-09" in txt
    assert "# body kept" in txt


def test_upsert_does_not_regress_last_seen_on_older_rescrape(tmp_path):
    # last_seen must be MONOTONIC. A board re-lists a role carrying a STALE date (older
    # than the note's stored last_seen); the re-scrape must NOT drag the marker into the
    # past -- the note WAS seen on the newer date. The upsert still reports "updated";
    # only the write is suppressed.
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(last_seen="2026-07-14")).outcome == "created"
    assert v.upsert(_lead(last_seen="2026-07-09")).outcome == "updated"   # older re-scrape
    txt = (_leads_dir(tmp_path) / "Acme - Analyst.md").read_text()
    assert "last_seen: 2026-07-14" in txt          # newer stored value KEPT
    assert "last_seen: 2026-07-09" not in txt      # older stamp did not overwrite


def test_upsert_merge_does_not_regress_last_seen_on_older_rescrape(tmp_path):
    # The MERGE branch (UNKNOWN verdict) also routes through _bump_last_seen, so it must
    # honour the same monotonic guard. A url-less lead against a note that has a location
    # merges; a newer merge, then an older re-scrape must keep the newer stamp.
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location=LOCATIONS[0], url="")   # note has a location; leads below do not
    same = dict(company="X", title="Y", location="", url="")
    assert v.upsert(_lead(**same, last_seen="2026-07-14")).outcome == "merged"
    assert v.upsert(_lead(**same, last_seen="2026-07-09")).outcome == "merged"   # older re-scrape
    txt = (_leads_dir(tmp_path) / "X - Y.md").read_text()
    assert "last_seen: 2026-07-14" in txt
    assert "last_seen: 2026-07-09" not in txt


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
    # char, never one-and-a-fraction — the guarantee _note_name relies on.
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
    # char-capped name it has today, or a re-scrape would create a duplicate note.
    # Inject the budget (255) explicitly so the assertion does NOT silently ride the
    # pathconf-failure fallback: _candidate_names is called directly here, so leads_dir
    # does not exist yet and a real os.pathconf would raise.
    v = Vault(str(tmp_path))
    v._name_max_cache = 255
    lead = _lead(company="X" * 200, title="Y")     # f-string -> 120 'X' after [:120]
    names, _capped = v._candidate_names(lead.company, lead.title, lead.location)
    assert names[0] == "X" * 120


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
    # indeterminate. Uncaught, that -1 caches and drives _note_name's byte budget to -4,
    # so _clamp_bytes negative-slices EVERY name -> a vault-wide rename/duplicate. A
    # non-positive limit must take the 255 fallback, same as the exception path.
    v = Vault(str(tmp_path))
    monkeypatch.setattr(os, "pathconf", lambda *a: -1)
    assert v._name_max() == 255


class _WriteFails:
    """A file wrapper whose .write() raises but .close() works -- simulates an open that
    SUCCEEDED (a real file was created) and a write that then failed mid-way."""
    def __init__(self, f): self._f = f
    def write(self, *_): raise OSError("disk full mid-write")
    def close(self): self._f.close()


def test_upsert_create_write_failure_leaves_no_partial(tmp_path, monkeypatch):
    # #24: a create whose WRITE fails mid-way (the exclusive open succeeded, so a 0-byte file
    # exists) must leave no partial -- a lingering 0-byte note would be adopted as real on the
    # next re-scrape. _write removes its own partial; upsert propagates the OSError (skipped).
    import sluice.core.vault as vault_mod
    v = Vault(str(tmp_path))
    real_open = open

    def fake_open(p, mode="r", **kw):
        if mode == "x":                                   # the exclusive create opens...
            return _WriteFails(real_open(p, mode, **kw))  # ...succeeds, but the write below fails
        return real_open(p, mode, **kw)

    monkeypatch.setattr(vault_mod, "open", fake_open, raising=False)
    with pytest.raises(OSError):
        v.upsert(_lead())
    monkeypatch.undo()
    assert list(_leads_dir(tmp_path).glob("*.md")) == []   # our partial removed


def test_upsert_create_open_failure_does_not_clobber_a_racer_note(tmp_path, monkeypatch):
    # The clobber this closes (CodeRabbit): if the create's exclusive OPEN fails (EACCES/ENOSPC,
    # NOT FileExistsError) while a concurrent writer lands a note at the path in the same window,
    # nothing must delete that note. The old os.path.exists-based cleanup would have -- it cannot
    # tell a racer's note from our own partial. The cleanup now lives in _write and fires ONLY
    # when OUR exclusive open returned a handle, so a failed open unlinks nothing.
    import sluice.core.vault as vault_mod
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    os.makedirs(v.leads_dir, exist_ok=True)
    path = os.path.join(v.leads_dir, "X - Y.md")
    real_open = open

    def fake_open(p, mode="r", **kw):
        if mode == "x" and p == path:
            with real_open(path, "w", encoding="utf-8") as rf:   # a racer lands a note...
                rf.write("RACER'S NOTE")
            raise OSError("EACCES: exclusive open failed")        # ...and OUR open fails, creating nothing
        return real_open(p, mode, **kw)

    monkeypatch.setattr(vault_mod, "open", fake_open, raising=False)
    with pytest.raises(OSError):
        v.upsert(_lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/1"))
    monkeypatch.undo()
    assert (_leads_dir(tmp_path) / "X - Y.md").read_text() == "RACER'S NOTE", \
        "the create's failed open clobbered a concurrent writer's note"


def test_upsert_create_race_does_not_clobber_a_concurrently_created_note(tmp_path, monkeypatch):
    # #16 TOCTOU: a concurrent writer (another `ingest run`, or a human in Obsidian) creates
    # the note in the window between _resolve_path's existence check and the create write.
    # The create must NOT truncate it -- that is never-clobber, now under concurrency. It
    # must re-reconcile against the note that now exists.
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    os.makedirs(v.leads_dir, exist_ok=True)
    racer_path = os.path.join(v.leads_dir, "X - Y.md")           # candidate 1 for the lead
    lead = _lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/1")

    # _render_new runs AFTER _resolve_path and BEFORE the write, so a side effect here
    # occupies the TOCTOU window exactly: the racer lands an ENRICHED note mid-upsert.
    real_render = v._render_new

    def racing_render(incoming):
        text = real_render(incoming)
        with open(racer_path, "w", encoding="utf-8") as f:
            f.write('---\ncompany: "X"\nrole: "Y"\nstatus: applied\n'
                    'url: "https://a/1"\nlast_seen: 2026-07-01\n---\n\nenriched body\n')
        return text

    monkeypatch.setattr(v, "_render_new", racing_render)
    outcome = v.upsert(lead).outcome

    txt = (_leads_dir(tmp_path) / "X - Y.md").read_text()
    assert "status: applied" in txt              # the racer's enrichment survived
    assert "enriched body" in txt                # ...body too
    assert outcome in ("updated", "merged")      # re-reconciled, not "created" over the top


def test_upsert_refuses_when_the_create_races_repeatedly(tmp_path, monkeypatch):
    # #16 exhaustion tail: if EVERY create attempt loses the race (sustained create/delete
    # flapping -- the note keeps being created under us and vanishing before we re-resolve),
    # upsert must give up bounded, with "refused", writing NOTHING. It must NOT spin (the
    # loop is bounded) and must NOT falsely report "created" -- a never-written lead recorded
    # into seen.db would be silently lost.
    import sluice.core.vault as vault_mod
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    os.makedirs(v.leads_dir, exist_ok=True)
    attempts = 0

    def always_taken(p, text, *, exclusive=False):
        nonlocal attempts
        attempts += 1
        raise FileExistsError(p)   # the exclusive create always finds the path occupied

    monkeypatch.setattr(vault_mod, "_write", always_taken)
    outcome = v.upsert(_lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/1")).outcome
    assert outcome == "refused"
    assert attempts == _CREATE_RACE_RETRIES == 3, "the create must be bounded to a fixed retry count"
    assert list(_leads_dir(tmp_path).glob("*.md")) == []   # nothing written


def test_candidate1_is_the_clean_company_title_name(tmp_path):
    # The drift-pin the old `_path_for` used to hold: candidate 1 is exactly
    # `Company - Title`, and a blank location adds no second candidate. Expressed as a
    # LITERAL, not re-derived from _note_name, so the two cannot agree by construction.
    v = Vault(str(tmp_path))
    v._name_max_cache = 255
    assert v._note_name("Acme - Analyst") == "Acme - Analyst"
    names, capped = v._candidate_names("Acme", "Analyst", "")
    assert names == ["Acme - Analyst"] and not capped


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


def test_make_threads_noise_words_from_config(tmp_path, monkeypatch):
    import sluice.stores.vault as store_mod
    from sluice.core.config import Config
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    v = store_mod._make(Config(location_noise_words=["remote"]))
    assert v._noise == frozenset({"remote"})


def _seed_note(tmp_path, name, location="", url="", role="Y"):
    from sluice.core.vault import _LEADS_SUBDIR
    d = tmp_path / _LEADS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f'---\ncompany: "X"\nrole: "{role}"\nlocation: "{location}"\nurl: "{url}"\n---\n\nbody\n')


def test_capped_gate_on_title_lost_is_load_bearing(tmp_path):
    """`title_lost` is gated on `capped`; without that gate a SHORT-title lead whose stored
    `role` was hand-corrected in Obsidian (#16's threat model) advances instead of merging,
    so `last_seen` stops advancing and #9's staleness sweep can expire a live posting.
    Deleting `capped and` from _reconcile leaves the rest of the suite green -- this is the
    only test that reddens.

    The note sits at `X - Y` but carries role "Z": url-less and location-less, so the
    verdict is UNKNOWN, and the title is short, so `capped` is False and title_lost MUST
    stay dormant. Under the mutant title_lost fires, the walk advances past the only
    candidate, and upsert refuses instead of merging."""
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location="", url="", role="Z")
    assert v.upsert(_lead(company="X", title="Y", location="", url="")).outcome == "merged"


def test_resolve_path_free_candidate1_creates(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    path, action = v._resolve_path(_lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/1"))
    assert action == "create" and path.endswith("X - Y.md")


def test_resolve_path_same_url_updates(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location=LOCATIONS[1], url="https://a/1")
    _, action = v._resolve_path(_lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/1"))
    assert action == "update"


def test_resolve_path_different_location_advances_to_candidate2_create(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location=LOCATIONS[0], url="https://a/1")
    lead = _lead(company="X", title="Y", location=LOCATIONS[1], url="https://a/2")
    path, action = v._resolve_path(lead)
    expected2 = os.path.join(v.leads_dir, v._note_name("X - Y", LOCATIONS[1]) + ".md")
    assert action == "create" and path == expected2


def test_resolve_path_absent_location_merges_at_candidate1(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location=LOCATIONS[0], url="")   # note has a location; lead does not
    _, action = v._resolve_path(_lead(company="X", title="Y", location="", url=""))
    assert action == "merge"


def test_resolve_path_refuses_when_frontmatter_contradicts_filename(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location=LOCATIONS[0], url="")   # candidate 1: DIFFERENT from LOCATIONS[1]
    c2 = v._note_name("X - Y", LOCATIONS[1])                        # candidate 2 filename for LOCATIONS[1]
    _seed_note(tmp_path, c2, location=LOCATIONS[2], url="")         # fm location contradicts the filename
    path, action = v._resolve_path(_lead(company="X", title="Y", location=LOCATIONS[1], url=""))
    assert action == "refuse" and path is None


def test_upsert_splits_two_cities_into_two_notes(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    assert v.upsert(_lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/1")).outcome == "created"
    assert v.upsert(_lead(company="X", title="Y", location=LOCATIONS[1], url="https://a/2")).outcome == "created"
    names = {p.name for p in _leads_dir(tmp_path).glob("*.md")}
    assert len(names) == 2
    assert "X - Y.md" in names                             # candidate 1: the first-seen clean name
    assert any(n.startswith("X - Y - ") for n in names)    # candidate 2: the split


def test_upsert_merge_bumps_only_last_seen(tmp_path):
    import re
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location=LOCATIONS[0], url="")
    f = _leads_dir(tmp_path) / "X - Y.md"
    before = f.read_text()
    assert v.upsert(_lead(company="X", title="Y", location="", url="", last_seen="2026-07-19")).outcome == "merged"
    after = f.read_text()
    assert "last_seen: 2026-07-19" in after
    strip = lambda t: re.sub(r"(?m)^\s*last_seen:.*$\n?", "", t)
    assert strip(after) == strip(before), "merge changed a field other than last_seen"


def test_upsert_refuses_and_writes_nothing(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    _seed_note(tmp_path, "X - Y", location=LOCATIONS[0], url="")
    c2 = v._note_name("X - Y", LOCATIONS[1])
    _seed_note(tmp_path, c2, location=LOCATIONS[2], url="")     # fm contradicts filename -> both DIFFERENT

    def _tree(root):   # snapshot every path, not just *.md, so a stray .stfolder is caught
        return {str(p.relative_to(root)): (p.read_text() if p.is_file() else "<dir>")
                for p in sorted(root.rglob("*"))}

    before = _tree(tmp_path)
    assert v.upsert(_lead(company="X", title="Y", location=LOCATIONS[1], url="")).outcome == "refused"
    assert _tree(tmp_path) == before, "refuse mutated the filesystem"
    assert not (tmp_path / ".stfolder").exists(), "refuse created the Syncthing marker"


def test_upsert_refuses_a_lead_with_neither_company_nor_title(tmp_path, caplog):
    """A lead carrying neither has no identity to be seated at. Before this it was CREATED:
    every name candidate collapsed to the bare separator, the note went in as ` - .md`,
    and `read_leads` then skipped it -- `_is_lead_note` is exactly `company or role` -- so
    the note existed, `created` was reported, the ingest sink wrote the lead into `seen.db`
    (which has no removal path), and no read in the tool could ever surface it again.

    Refusing writes nothing and keeps it out of `seen.db`, so a source that starts emitting
    these re-reports every run rather than filling the vault with unreadable stubs. The
    filesystem snapshot is what pins 'nothing', including the Syncthing marker: a warned
    `created` would still leave the note and the seen.db row behind.

    The message is asserted on the substring that DISCRIMINATES this refusal from the three
    other "vault refused lead" warnings in the module (`resolves to N notes`, `no name
    candidate is writable`, `last_seen bump raced repeatedly`): a looser match would pass on
    any of them."""
    v = Vault(str(tmp_path))
    with caplog.at_level("WARNING"):
        assert v.upsert(_lead(company="", title="", url="https://ex.invalid/1")).outcome == "refused"
    assert not list(tmp_path.rglob("*")), "a refusal must not touch the filesystem at all"
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("company and role both read back blank" in m for m in said), said


@pytest.mark.parametrize("company,title", [('"', ""), ("'", ""), ("", '"'), ("", "'")])
def test_upsert_refuses_a_lead_whose_only_field_parses_back_empty(tmp_path, company, title,
                                                                  caplog):
    """The defect the raw-field guard could not see, and the reason this gate now runs the
    READ's own chain instead of a second normalisation of its own.

    `_fm_dict` ends in `.strip().strip('"').strip("'")`, so a company of `"` or `'` is
    present to any truthiness test over `lead.company` and EMPTY once the note it was written
    into is parsed back. Measured before this fix: all four of these returned `created`,
    `read_leads` returned none of them, and the notes sat on disk (`- - .md`, `' - .md`,
    ` - -.md`, ` - '.md`) -- written, entered into `seen.db`, which has no removal path, and
    invisible to every command in the tool forever.

    Asserted through `read_leads` AND the filesystem, because `refused` alone would still pass
    if the note were written and merely mis-reported."""
    v = Vault(str(tmp_path))
    with caplog.at_level("WARNING"):
        assert v.upsert(_lead(company=company, title=title, url="https://ex.invalid/1")) \
            .outcome == "refused"
    assert not list(tmp_path.rglob("*")), "a refusal must not touch the filesystem at all"
    assert v.read_leads() == []
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("company and role both read back blank" in m for m in said), said


def test_upsert_still_creates_a_lead_whose_field_merely_CONTAINS_quotes(tmp_path):
    """The mirror harm of the quote refusal: only a value that parses back EMPTY may be
    refused. `"Acme"` survives `_fm_dict` as `Acme`, so it is a real, readable lead and must
    still be seated -- widening the gate from 'blank' to 'contains a quote' would bin it."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(company='"Acme"', title="", url="https://ex.invalid/1")).outcome == "created"
    assert [n.slug for n in v.read_leads()] == ["-Acme- - "]


def test_upsert_refuses_when_company_alone_has_an_embedded_newline(tmp_path, caplog):
    """Mixed-field OR-behavior (#131 decision 7, round 3): company unsafe, role safe --
    a naive AND-based check (mirroring the existing blank-identity gate's OR-satisfied
    shape) would wrongly let this through. role's safety must not rescue an unsafe
    company; this refuses via upsert's OWN new pre-check, before _render_new ever runs,
    so the injected newline never reaches disk at all.

    The message is asserted on the substring that discriminates THIS gate (a control
    character in company/role) from the blank-identity gate one line below it in
    upsert -- `refused` plus an untouched tree also holds for that gate, so without
    this a future change that let the blank-identity gate reject these SAME inputs
    would keep the test green with the printable gate itself deleted."""
    v = Vault(str(tmp_path))
    with caplog.at_level("WARNING"):
        assert v.upsert(_lead(company="Acme\nstatus: applied", title="Analyst",
                              url="https://a/2")).outcome == "refused"
    assert v.read_leads() == []
    # read_leads() == [] alone doesn't prove upsert wrote NOTHING -- it also passes if
    # a file was written that read_leads() happens to skip. Pin the stronger claim: the
    # unsafe identity is refused before any write, not merely before a readable one.
    assert not list(tmp_path.rglob("*"))
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("contains a control character" in m for m in said), said


def test_upsert_refuses_when_role_alone_has_an_embedded_newline(tmp_path, caplog):
    """Symmetric case to the one above -- role unsafe, company safe."""
    v = Vault(str(tmp_path))
    with caplog.at_level("WARNING"):
        assert v.upsert(_lead(company="Acme", title="Analyst\nstatus: applied",
                              url="https://a/3")).outcome == "refused"
    assert v.read_leads() == []
    assert not list(tmp_path.rglob("*"))
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.core.vault"]
    assert any("contains a control character" in m for m in said), said


@pytest.mark.parametrize("company,title,seated", [
    ("Acme", "", "Acme - "),
    ("", "Analyst", " - Analyst"),
])
def test_upsert_still_creates_a_lead_carrying_only_ONE_of_the_two(tmp_path, company, title,
                                                                 seated):
    """The MIRROR harm of the refusal above, and it needs its own witness: `or` -> `and` in
    the raw-field guard this one replaces survived the whole suite. One field is enough because
    `_is_lead_note` is satisfied by either alone, so both of these notes are real, readable
    leads -- measured shipped: `Acme - .md` and ` - Analyst.md`, both returned by `read_leads`.
    Tightening the guard to demand both refuses them instead: out of the vault, out of
    `seen.db`, re-reported every run, under a warning saying the note reads back blank when it
    does not.

    Asserted through `read_leads`, not just on the outcome string: the harm is that no read
    surfaces the note, so the read is the thing to check. `upsert` no longer carries its own
    copy of the predicate -- it calls `_is_lead_note` over the frontmatter it is about to
    write -- so this now pins that the gate AROUND that call cannot narrow what reaches the
    vault; `_is_lead_note`'s own `or` is pinned separately (test_vault_recursive_scan.py)."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(company=company, title=title, url="https://ex.invalid/1")) \
        .outcome == "created"
    assert [n.slug for n in v.read_leads()] == [seated]


@pytest.mark.parametrize("company,title", [("   ", ""), ("", " \t ")])
def test_upsert_refuses_a_lead_whose_only_field_is_whitespace(tmp_path, company, title):
    """The one place this gate is deliberately STRICTER than the read predicate it otherwise
    mirrors. An all-whitespace field survives `_fm_dict` as whitespace and is truthy, so
    `_is_lead_note` alone seats a note at `    - .md` and `read_leads` does return it -- not
    invisible, but carrying no identity to reconcile on, which is the condition the refusal is
    about. The strip is applied to the PARSED values, so it is one tightening on top of one
    normalisation rather than a second normalisation running beside it. Declining a create
    costs a re-report; seating an identity-less note costs a permanent `seen.db` row."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(company=company, title=title, url="https://ex.invalid/1")) \
        .outcome == "refused"
    assert not list(tmp_path.rglob("*")), "a refusal must not touch the filesystem at all"


def test_upsert_still_creates_a_lead_whose_field_merely_has_surrounding_space(tmp_path):
    """The mirror harm of the strip: only an ALL-whitespace field may be refused. A field
    with surrounding space carries a real value and must still seat a note."""
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(company=" Acme ", title="", url="https://ex.invalid/1")) \
        .outcome == "created"
    assert len(v.read_leads()) == 1


def test_noise_word_makes_a_split_merge_end_to_end(tmp_path, monkeypatch):
    # Config -> _make -> Vault -> same_opportunity: proves the noise knob reaches a verdict.
    import sluice.stores.vault as store_mod
    from sluice.core.config import Config
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    _seed_note(tmp_path, "X - Y", location="aaa", url="")
    plain = store_mod._make(Config()); plain._name_max_cache = 255
    assert plain.upsert(_lead(company="X", title="Y", location="bbb", url="")).outcome == "created"  # aaa vs bbb -> split
    for f in _leads_dir(tmp_path).glob("X - Y - *.md"):
        f.unlink()
    tuned = store_mod._make(Config(location_noise_words=["bbb"])); tuned._name_max_cache = 255
    assert tuned.upsert(_lead(company="X", title="Y", location="bbb", url="")).outcome == "merged"  # bbb noise -> UNKNOWN


def test_accepted_cost_same_location_different_job_reports_updated(tmp_path):
    # Two different teams, same company+title+location, different url -> SAME -> updated.
    # The one silent case, documented and pinned.
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    assert v.upsert(_lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/1")).outcome == "created"
    assert v.upsert(_lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/2")).outcome == "updated"
    assert len(list(_leads_dir(tmp_path).glob("*.md"))) == 1


def test_upsert_is_idempotent_across_three_runs_on_the_slug_set(tmp_path):
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    lead = _lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/1")
    for _ in range(3):
        v.upsert(lead)
    assert {p.name for p in _leads_dir(tmp_path).glob("*.md")} == {"X - Y.md"}


def test_note_name_sanitizes_backslash_no_traversal(tmp_path):
    # A scraped company/title carrying a Windows separator must not traverse out of the
    # leads dir; backslash is mapped to '-' like '/' and ':'.
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    assert v._note_name("..\\..\\etc - passwd") == "..-..-etc - passwd"
    assert "\\" not in v._note_name("a\\b - c")


def test_sanitize_maps_every_windows_reserved_char():
    # Windows forbids < > : " / \\ | ? * in filenames; any of them in a scraped
    # company/title/location makes the note simply uncreatable there. Each maps to '-'.
    for ch in '<>:"/\\|?*':
        assert _sanitize(f"a{ch}b") == "a-b", f"{ch!r} was not sanitized"


def test_sanitize_maps_c0_control_chars():
    # C0 control chars (\x00-\x1f, incl. TAB, LF, CR) are illegal in filenames on Windows
    # and hostile to Syncthing/Obsidian; map them to '-' too. \\x1f is the range boundary.
    for code in (0x00, 0x09, 0x0a, 0x0d, 0x1f):
        assert _sanitize(f"a{chr(code)}b") == "a-b", f"U+{code:04X} was not sanitized"


def test_sanitize_leaves_ordinary_char_at_range_boundary_untouched():
    # \\x20 (space) is the first char ABOVE the C0 range and must NOT be mapped -- else
    # every "Company - Title" separator would be mangled. Pins the range's upper edge.
    assert _sanitize("a b") == "a b"


def test_sanitize_is_length_preserving():
    # Length-preserving is load-bearing: candidate-1 note names stay byte-identical for
    # every real name (which contains none of these), so no existing note migrates.
    s = 'x<y>z:"|?*/\\w'
    assert len(_sanitize(s)) == len(s)


def test_long_titles_sharing_prefix_split_when_location_differs(tmp_path):
    # #5's 120-char-prefix collision, resolved via the LOCATION discriminator: two distinct
    # long titles that cap to the same stem still get two notes when their locations differ.
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    prefix = "Engineer " + "A" * 130                          # long enough that the tail truncates
    title_a, title_b = prefix + " Alpha", prefix + " Bravo"   # DIFFERENT titles, SAME capped stem
    assert v._note_name(f"X - {title_a}") == v._note_name(f"X - {title_b}")   # the prefix collides
    assert v.upsert(_lead(company="X", title=title_a, location=LOCATIONS[0], url="")).outcome == "created"
    assert v.upsert(_lead(company="X", title=title_b, location=LOCATIONS[1], url="")).outcome == "created"
    assert len(list(_leads_dir(tmp_path).glob("*.md"))) == 2  # split on the location, not the lost tail


def test_long_titles_sharing_prefix_and_location_split(tmp_path):
    # The residual #5 left open, now CLOSED: two DISTINCT long titles that cap to the same
    # filename AND share a location no longer merge. The FIRST keeps its clean, digest-less
    # name (zero migration); the second gets its own note (here via the location suffix, since
    # location is the primary discriminator -- see the digest-specific tests below).
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    prefix = "Engineer " + "A" * 130
    title_a, title_b = prefix + " Alpha", prefix + " Bravo"   # different titles, same capped stem
    clean = v._note_name(f"X - {title_a}")                    # candidate 1, the clean name
    assert v._note_name(f"X - {title_b}") == clean            # cand1 collides for both
    assert v.upsert(_lead(company="X", title=title_a, location=LOCATIONS[0], url="")).outcome == "created"
    assert v.upsert(_lead(company="X", title=title_b, location=LOCATIONS[0], url="")).outcome == "created"  # SAME loc
    names = {p.name for p in _leads_dir(tmp_path).glob("*.md")}
    assert len(names) == 2, "two distinct long titles at one location must not merge"
    assert f"{clean}.md" in names, "the first-seen title must keep its clean, digest-less name"


def test_long_titles_no_location_split_via_digest(tmp_path):
    # With NO location, the title-digest is the ONLY extra discriminator: two distinct long
    # titles must still split onto their stable digest names (without the digest candidate the
    # second would REFUSE and be dropped). The first keeps its clean name.
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    prefix = "Engineer " + "A" * 130
    title_a, title_b = prefix + " Alpha", prefix + " Bravo"
    clean = v._note_name(f"X - {title_a}")
    assert v.upsert(_lead(company="X", title=title_a, location="", url="")).outcome == "created"
    assert v.upsert(_lead(company="X", title=title_b, location="", url="")).outcome == "created"
    names = {p.name for p in _leads_dir(tmp_path).glob("*.md")}
    assert len(names) == 2, "two distinct long titles with no location must split on the digest"
    assert f"{clean}.md" in names, "the first-seen title keeps its clean, digest-less name"
    assert v._note_name(f"X - {title_b}", _title_digest(title_b)) + ".md" in names


def test_three_long_titles_same_location_split_via_digest(tmp_path):
    # THREE distinct long titles at one location: the location suffix distinguishes only the
    # second, so the THIRD relies on the title-digest. Without the digest candidate the third
    # would REFUSE -- a job silently dropped. All three must get their own note.
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    prefix = "Engineer " + "A" * 130
    for tail in ("Alpha", "Bravo", "Charlie"):
        assert v.upsert(_lead(company="X", title=prefix + " " + tail,
                              location=LOCATIONS[0], url="")).outcome == "created"
    assert len(list(_leads_dir(tmp_path).glob("*.md"))) == 3, \
        "three distinct long titles at one location must all get a note"


def test_long_title_digest_split_is_idempotent(tmp_path):
    # Re-scraping both distinct long titles must NOT keep minting notes: each resolves back to
    # its own note across runs.
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    prefix = "Engineer " + "A" * 130
    a = dict(company="X", title=prefix + " Alpha", location="", url="")
    b = dict(company="X", title=prefix + " Bravo", location="", url="")
    for _ in range(3):
        v.upsert(_lead(**a))
        v.upsert(_lead(**b))
    assert len(list(_leads_dir(tmp_path).glob("*.md"))) == 2


def test_url_stable_capped_title_updates_when_tail_drifts(tmp_path):
    # A matching non-empty URL is DEFINITIVE proof of the same posting. A >120-char title whose
    # tail drifts across scrapes on a url-stable posting must UPDATE in place -- title_lost must
    # NOT override the url match and mint a fresh digest note per drift.
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    prefix = "Engineer " + "A" * 130
    assert v.upsert(_lead(company="X", title=prefix + " Alpha",
                          location=LOCATIONS[0], url="https://a/1")).outcome == "created"
    assert v.upsert(_lead(company="X", title=prefix + " Bravo",   # same URL, drifted tail
                          location=LOCATIONS[0], url="https://a/1")).outcome == "updated"
    assert len(list(_leads_dir(tmp_path).glob("*.md"))) == 1, "a url-stable posting must not split on title drift"
