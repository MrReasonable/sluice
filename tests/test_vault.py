import os

import pytest

from sluice.core.leads import Lead
from sluice.core.vault import _CREATE_RACE_RETRIES, Vault, _clamp_bytes, _sanitize
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


def test_upsert_does_not_regress_last_seen_on_older_rescrape(tmp_path):
    # last_seen must be MONOTONIC. A board re-lists a role carrying a STALE date (older
    # than the note's stored last_seen); the re-scrape must NOT drag the marker into the
    # past -- the note WAS seen on the newer date. The upsert still reports "updated";
    # only the write is suppressed.
    v = Vault(str(tmp_path))
    assert v.upsert(_lead(last_seen="2026-07-14")) == "created"
    assert v.upsert(_lead(last_seen="2026-07-09")) == "updated"   # older re-scrape
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
    assert v.upsert(_lead(**same, last_seen="2026-07-14")) == "merged"
    assert v.upsert(_lead(**same, last_seen="2026-07-09")) == "merged"   # older re-scrape
    txt = (_leads_dir(tmp_path) / "X - Y.md").read_text()
    assert "last_seen: 2026-07-14" in txt
    assert "last_seen: 2026-07-09" not in txt


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
    outcome = v.upsert(lead)

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
    outcome = v.upsert(_lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/1"))
    assert outcome == "refused"
    assert attempts == _CREATE_RACE_RETRIES == 3, "the create must be bounded to a fixed retry count"
    assert list(_leads_dir(tmp_path).glob("*.md")) == []   # nothing written


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


def test_make_threads_noise_words_from_config(tmp_path, monkeypatch):
    import sluice.stores.vault as store_mod
    from sluice.core.config import Config
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    v = store_mod._make(Config(location_noise_words=["remote"]))
    assert v._noise == frozenset({"remote"})


def _seed_note(tmp_path, name, location="", url=""):
    from sluice.core.vault import _LEADS_SUBDIR
    d = tmp_path / _LEADS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f'---\ncompany: "X"\nrole: "Y"\nlocation: "{location}"\nurl: "{url}"\n---\n\nbody\n')


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
    assert v.upsert(_lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/1")) == "created"
    assert v.upsert(_lead(company="X", title="Y", location=LOCATIONS[1], url="https://a/2")) == "created"
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
    assert v.upsert(_lead(company="X", title="Y", location="", url="", last_seen="2026-07-19")) == "merged"
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
    assert v.upsert(_lead(company="X", title="Y", location=LOCATIONS[1], url="")) == "refused"
    assert _tree(tmp_path) == before, "refuse mutated the filesystem"
    assert not (tmp_path / ".stfolder").exists(), "refuse created the Syncthing marker"


def test_noise_word_makes_a_split_merge_end_to_end(tmp_path, monkeypatch):
    # Config -> _make -> Vault -> same_opportunity: proves the noise knob reaches a verdict.
    import sluice.stores.vault as store_mod
    from sluice.core.config import Config
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    _seed_note(tmp_path, "X - Y", location="aaa", url="")
    plain = store_mod._make(Config()); plain._name_max_cache = 255
    assert plain.upsert(_lead(company="X", title="Y", location="bbb", url="")) == "created"  # aaa vs bbb -> split
    for f in _leads_dir(tmp_path).glob("X - Y - *.md"):
        f.unlink()
    tuned = store_mod._make(Config(location_noise_words=["bbb"])); tuned._name_max_cache = 255
    assert tuned.upsert(_lead(company="X", title="Y", location="bbb", url="")) == "merged"  # bbb noise -> UNKNOWN


def test_accepted_cost_same_location_different_job_reports_updated(tmp_path):
    # Two different teams, same company+title+location, different url -> SAME -> updated.
    # The one silent case, documented and pinned.
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    assert v.upsert(_lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/1")) == "created"
    assert v.upsert(_lead(company="X", title="Y", location=LOCATIONS[0], url="https://a/2")) == "updated"
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
    # (When location ALSO matches they merge -- a documented residual, same class as the
    # accepted cost: the store cannot see the truncated title tail, so it has no evidence.)
    v = Vault(str(tmp_path)); v._name_max_cache = 255
    prefix = "Engineer " + "A" * 130                          # long enough that the tail truncates
    title_a, title_b = prefix + " Alpha", prefix + " Bravo"   # DIFFERENT titles, SAME capped stem
    assert v._note_name(f"X - {title_a}") == v._note_name(f"X - {title_b}")   # the prefix collides
    assert v.upsert(_lead(company="X", title=title_a, location=LOCATIONS[0], url="")) == "created"
    assert v.upsert(_lead(company="X", title=title_b, location=LOCATIONS[1], url="")) == "created"
    assert len(list(_leads_dir(tmp_path).glob("*.md"))) == 2  # split on the location, not the lost tail
