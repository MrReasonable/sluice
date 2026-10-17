"""DossierCache.peek (#195): the read-only look at a lead's cached dossier that `leads prep` uses.

It ignores the TTL and the content, never calls the fetcher, never creates a directory or a
file, and refuses a broken file with a FIXED, path-free message. The key-arm rows seed REAL lead
notes in a real `Vault` and hand `note.fm` to both `get_or_build` and `peek`, because that is
the dict the facade passes (see `DossierCache.forget` for why a dossier dict is never passed)."""
import os
from datetime import datetime, timedelta

import pytest

from sluice.core.dossier import DossierCache, _slug
from sluice.core.vault import Vault

JD = "a real posting " * 20
NOW = datetime(2031, 3, 14, 9, 0)


def _fetcher(calls):
    def fetch(lead):
        calls.append(lead)
        return {"jd": {"markdown": JD}, "glassdoor": {}}
    return fetch


def _note_fm(vault_dir, title, arm):
    """A real lead note's frontmatter for one of `cache_key`'s three arms: `lead_id` set through
    `update_fields`, a url and no `lead_id`, or neither (an empty url). Written as note text,
    because a quoted `company:` frontmatter line is the shape the collectors in
    tests/test_fixture_name_neutrality.py read, and an `upsert(Lead(company=...))` keyword is
    not (measured: they find no company in that call)."""
    url = "" if arm == "neither" else "https://example.invalid/job/1"
    leads = vault_dir / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    (leads / f"Example Ltd - {title}.md").write_text(
        f'---\ncompany: "Example Ltd"\nrole: "{title}"\nurl: "{url}"\nstatus: new\n---\n\nB\n',
        encoding="utf-8")
    v = Vault(str(vault_dir))
    (note,) = v.read_leads()
    if arm == "lead_id":
        assert v.update_fields(note.ref, {"lead_id": "L1"})
        (note,) = Vault(str(vault_dir)).read_leads()
    return note.fm


@pytest.mark.parametrize("arm", ["lead_id", "url", "neither"])
def test_peek_finds_what_get_or_build_wrote_through_every_key_arm(tmp_path, titles, arm):
    fm = _note_fm(tmp_path / "vault", titles[0][0].title(), arm)
    calls = []
    writer = DossierCache(str(tmp_path / "cache"), 7, _fetcher(calls), clock=lambda: NOW)
    # Scope: each row really exercises the arm it names, or a note shape that changed under
    # it (say, the vault starting to stamp a `lead_id`) would leave three rows on one arm.
    key = writer.cache_key(fm)
    assert {"lead_id": key == "L1", "url": key.startswith("url-"),
            "neither": key == _slug(fm) and not fm.get("url")}[arm]
    writer.get_or_build(fm)
    assert len(calls) == 1
    d = DossierCache(str(tmp_path / "cache"), 7, _fetcher(calls)).peek(fm)
    assert d is not None and d["jd"]["markdown"] == JD and d["lead_id"] == key
    assert len(calls) == 1


def test_peek_ignores_the_ttl_and_the_floor_and_never_fetches(tmp_path):
    calls = []
    lead = {"lead_id": "L"}
    old = DossierCache(str(tmp_path), 7, _fetcher(calls), clock=lambda: NOW - timedelta(days=99))
    old.get_or_build(lead)
    assert len(calls) == 1
    c = DossierCache(str(tmp_path), 7, _fetcher(calls), clock=lambda: NOW,
                     min_jd_chars=10**6)
    # Precondition: this entry is both STALE and BELOW the floor, so `get_or_build` would
    # refetch it -- the two properties `peek` is asserted to ignore.
    assert not c._fresh(c._path(lead)) and not c.jd_arrived(old.peek(lead))
    d = c.peek(lead)
    assert d is not None and d["jd"]["markdown"] == JD
    assert len(calls) == 1


def test_peek_on_a_missing_file_returns_none_and_creates_nothing(tmp_path):
    calls = []
    assert DossierCache(str(tmp_path), 7, _fetcher(calls)).peek({"lead_id": "L"}) is None
    assert os.listdir(tmp_path) == [] and calls == []


def test_peek_on_a_missing_directory_returns_none_and_creates_nothing(tmp_path):
    calls = []
    d = tmp_path / "absent"
    assert DossierCache(str(d), 7, _fetcher(calls)).peek({"lead_id": "L"}) is None
    assert not d.exists() and calls == []


@pytest.mark.parametrize("payload,msg", [
    ("{not json", "the cached dossier is not valid JSON"),
    ("[1, 2]", "the cached dossier is not a JSON object"),
])
def test_peek_refuses_a_broken_file_with_a_path_free_message(tmp_path, payload, msg):
    c = DossierCache(str(tmp_path), 7, _fetcher([]))
    with open(c._path({"lead_id": "L"}), "w", encoding="utf-8") as f:
        f.write(payload)
    with pytest.raises(ValueError) as e:
        c.peek({"lead_id": "L"})
    # Exact text: the facade hands it to `unreadable_reason`, which passes an errno-less
    # message through as it is, so anything more would reach the brief and its --json.
    assert str(e.value) == msg and str(tmp_path) not in str(e.value)


def test_peek_on_invalid_utf8_raises_a_value_error_naming_no_path(tmp_path):
    # The facade's catch is `(OSError, ValueError)`, so a decode failure must land in that
    # tuple (UnicodeDecodeError is a ValueError) and say nothing about where the file lives.
    c = DossierCache(str(tmp_path), 7, _fetcher([]))
    with open(c._path({"lead_id": "L"}), "wb") as f:
        f.write(b'{"jd": "\xff"}')
    with pytest.raises(ValueError) as e:
        c.peek({"lead_id": "L"})
    assert str(tmp_path) not in str(e.value)


def test_peek_lets_any_other_os_error_through(tmp_path):
    # Only a missing file means "no cached copy". Anything else (here a directory sitting at
    # the entry's path) is the caller's to report as `cache_unreadable`, never a quiet None.
    c = DossierCache(str(tmp_path), 7, _fetcher([]))
    os.mkdir(c._path({"lead_id": "L"}))
    with pytest.raises(OSError) as e:
        c.peek({"lead_id": "L"})
    assert not isinstance(e.value, FileNotFoundError)


@pytest.mark.parametrize("lead_id", ["../../outside", "a/b", "..", ".", "a\\b", "x\0y"])
def test_an_unsafe_lead_id_keeps_every_cache_path_inside_the_cache_dir(tmp_path, lead_id):
    """A hand-typed `lead_id` is a filename under the cache dir: one that could escape it
    (a separator, `.`/`..`, a NUL) is hashed, so peek/get_or_build/forget stay inside it.
    A flat value keeps its existing key, so no cache entry moves."""
    cache_dir = tmp_path / "cache"
    c = DossierCache(str(cache_dir), 7, lambda lead: {})
    path = os.path.realpath(c._path({"lead_id": lead_id}))
    assert os.path.dirname(path) == os.path.realpath(str(cache_dir))
    assert c.cache_key({"lead_id": "L-123_ab.c"}) == "L-123_ab.c"
