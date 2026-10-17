"""`Sluice.prep_brief` (#195): the interview prep brief through the facade and a real `Vault`.

Spec: `docs/superpowers/specs/2026-10-16-interview-prep-design.md`, sections 2, 3 and 5, and
its Testing (PR 1) rows for entries, the JD, degradations, the write, resolution and rendering
safety.

Fixtures follow `tests/test_followups_facade.py`'s conventions (an injected `today`,
`Vault(str(tmp_path))`), with one deliberate difference in how a lead is seeded: as LITERAL
note text carrying a quoted `company: "Example Ltd"` frontmatter line and an
`Example Ltd - <role>` filename, the two shapes `tests/test_fixture_name_neutrality.py`'s
collectors read (Amendment A2: a `Lead(company=...)` kwarg is read by none of them, measured
in Task 4, and stated as a residual in that file). Stories are literal note text carrying
`Company: Example Ltd`. Roles come from the seeded `titles` fixture. The hostile-value rows are
the one exception: their company base is `COMPANY`, a literal roster value asserted to be on
`_REVIEWED_FIXTURE_IDENTITIES` where each row uses it, and prefixed with markup.
"""
import dataclasses
import json
import os
import pathlib
import shlex
from datetime import datetime

import pytest

import sluice.core.prepbrief as prepbrief
import sluice.core.protocols as protocols
import sluice.cv.bundle as cvbundle
from sluice.core import followups, jdsection
from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.dossier import DossierCache
from sluice.core.protocols import EVIDENCE_KINDS, VaultConflict
from sluice.core.rank import jd_keywords
from sluice.core.vault import Vault
from tests.conftest import UNREADABLE_DIR
from tests.test_fixture_name_neutrality import _REVIEWED_FIXTURE_IDENTITIES
from tests.test_prepbrief import _GRAMMAR, _HOSTILE_PREFIXES, _HOSTILE_ROLES

# A date the host clock cannot produce: a facade method reading the real clock goes red.
TODAY = "2031-03-14"
KIND = "stories"
COMPANY = "Example Ltd"
# Adds `[a-z]{4,}` words beyond any seeded role (asserted where a row relies on it).
JD = "Build the data pipeline and deploy it with monitoring."
SENTINEL = "ZZ-SENTINEL-77"
BUILT = datetime(2031, 3, 1, 9, 30)


@pytest.fixture(autouse=True)
def _role(titles):
    # Titles come from the seeded faker pool, never typed by hand.
    global ROLE
    ROLE = titles[0][0].title()


# ── seeding ─────────────────────────────────────────────────────────────────────

def _dos(root):
    return os.path.join(str(root), "dos")


def _leads_dir(root):
    return os.path.join(str(root), "Job Applications", "Job Leads")


# Passed as `_seed(role=NO_ROLE)` to write a lead note with no `role:` frontmatter line at
# all, the one shape that reaches the facade's `or ""` default for a missing key.
NO_ROLE = object()


def _seed(root, *, status="interview", jd_section=JD, name=None, extra=(), role=None,
          url="https://example.invalid/1", tail="", fm_company=None):
    """One lead note as literal text; returns its slug. `jd_section` (or None) becomes a
    hand-typed `## Job description` section; `tail` is appended raw after it. `fm_company`
    is for the hostile rows only, which compose their value at run time. `role=NO_ROLE`
    omits the `role:` line; the filename still carries the seeded role."""
    omit_role = role is NO_ROLE
    role = ROLE if role is None or omit_role else role
    os.makedirs(_leads_dir(root), exist_ok=True)
    company_line = ('company: "Example Ltd"' if fm_company is None
                    else f'company: "{fm_company}"')
    fm = [company_line, *([] if omit_role else [f'role: "{role}"']), f"status: {status}",
          'source: "s"', f'url: "{url}"', *extra]
    body = "\n# Example Ltd\n" if omit_role else f"\n# Example Ltd - {role}\n"
    if jd_section is not None:
        body += f"\n## Job description\n\n{jd_section}\n"
    body += tail
    # The default name is spelled out with its `.md` so the lead-note filename collector reads
    # it as the identity it is.
    fname = f"{name}.md" if name else f"Example Ltd - {role}.md"
    with open(os.path.join(_leads_dir(root), fname), "w", encoding="utf-8") as fh:
        fh.write("---\n" + "\n".join(fm) + "\n---\n" + body)
    return fname[:-3]


def _kind_dir(root, kind=KIND, inbox=False):
    parts = EVIDENCE_KINDS[kind].relpath.split("/") + (["_inbox"] if inbox else [])
    return os.path.join(str(root), *parts)


def _story(root, title, best_for="pipeline", body="**Situation.** it went well", verified=True,
           category="", inbox=False):
    """One story note, as literal text whose `Company:` line the evidence collector reads."""
    base = _kind_dir(root, inbox=inbox)
    os.makedirs(base, exist_ok=True)
    lines = ["Company: Example Ltd"]
    if best_for:
        lines.append(f"Best For: {best_for}")
    if category:
        lines.append(f"Category: {category}")
    if verified:
        lines.append("verified: 2031-01-01")
    path = os.path.join(base, f"{title}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("---\n" + "\n".join(lines) + f"\n---\n{body}\n")
    return path


def _ready(root, **kw):
    """A write-ready lead: one verified story, a usable JD section, no open fence."""
    _story(root, "Told")
    return _seed(root, **kw)


def _app(root, **cfg):
    return Sluice(Config(dossier_dir=_dos(root), **cfg), store=Vault(str(root)),
                  today=lambda: TODAY)


def _note(root, slug):
    return next(n for n in Vault(str(root)).read_leads() if n.slug == slug)


def _bytes(root, slug):
    return pathlib.Path(_note(root, slug).ref).read_bytes()


def _stub_cache(root, jd, clock=BUILT):
    return DossierCache(_dos(root), 7, fetcher=lambda lead: {"jd": {"markdown": jd}},
                        clock=lambda: clock)


def _cache_put(root, slug, jd=JD, clock=BUILT):
    """A cached dossier for the lead, seeded through `get_or_build` with a stub fetcher."""
    _stub_cache(root, jd, clock).get_or_build(_note(root, slug).fm)
    return _stub_cache(root, jd)._path(_note(root, slug).fm)


def _cache_raw(root, slug, text):
    """A cache file `get_or_build` would not persist, written at the real key derivation."""
    cache = _stub_cache(root, "")
    path = cache._path(_note(root, slug).fm)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _codes(r):
    return [d["code"] for d in r.brief["degradations"]]


def _sentence(r, code):
    return next(d["sentence"] for d in r.brief["degradations"] if d["code"] == code)


def _appended(before: bytes, after: bytes) -> str:
    assert after.startswith(before)
    return after[len(before):].decode("utf-8")


# ── entries ─────────────────────────────────────────────────────────────────────

def test_labels_and_the_inbox(tmp_path):
    # Witness: read the pending entries into the brief.
    _story(tmp_path, "Told")
    _story(tmp_path, "Typed", verified=False)
    _story(tmp_path, "Drafted", best_for="inboxonlyword", body="inbox body text", inbox=True)
    slug = _seed(tmp_path)
    r = _app(tmp_path).prep_brief(lead=slug)
    assert r.outcome == "ok"
    md, doc = r.markdown, json.dumps(r.brief)
    told = md.split("#### Told\n", 1)[1].split("\n#### ", 1)[0]
    typed = md.split("#### Typed\n", 1)[1].split("\n#### ", 1)[0]
    assert "- Verified: yes" in told and "- Verified: no" in typed
    assert r.brief["kinds"][KIND]["pending"] == 1
    assert ("1 entry is waiting in the inbox. Run `job-sluice stories verify`, then delete "
            "this section and run `leads prep --write` again, to include it.") in md
    for leaked in ("Drafted", "inboxonlyword", "inbox body text"):
        assert leaked not in md and leaked not in doc


@pytest.mark.parametrize("arm", ["kind_symlink", "inbox_symlink", "bad_utf8"])
def test_entries_unreadable(tmp_path, arm):
    # Witnesses: delete each read from the try; drop UnicodeDecodeError.
    slug = _seed(tmp_path)
    if arm == "kind_symlink":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        os.makedirs(os.path.dirname(_kind_dir(tmp_path)), exist_ok=True)
        os.symlink(elsewhere, _kind_dir(tmp_path))
    elif arm == "inbox_symlink":
        _story(tmp_path, "Told")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        os.symlink(elsewhere, _kind_dir(tmp_path, inbox=True))
        # The precondition that makes this row distinct: the folder read itself succeeds.
        assert Vault(str(tmp_path)).read_evidence(KIND, verified_only=False)
    else:
        os.makedirs(_kind_dir(tmp_path), exist_ok=True)
        with open(os.path.join(_kind_dir(tmp_path), "Bad.md"), "wb") as fh:
            fh.write(b"---\nCompany: Example Ltd\n---\n\xff\xfe broken\n")
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "entries_unreadable"
    assert r.reason.startswith(f"{KIND}: ")
    assert r.markdown is None and r.brief is None
    assert _bytes(tmp_path, slug) == before


@UNREADABLE_DIR
def test_an_unreadable_story_gives_a_path_free_reason(tmp_path):
    # Witness: format with str(exc).
    path = _story(tmp_path, "Told")
    slug = _seed(tmp_path)
    os.chmod(path, 0)
    try:
        r = _app(tmp_path).prep_brief(lead=slug)
    finally:
        os.chmod(path, 0o644)
    assert r.outcome == "entries_unreadable"
    assert r.reason == f"{KIND}: PermissionError: Permission denied"
    for leak in (path, str(tmp_path), os.environ["HOME"]):
        assert leak not in r.reason


@pytest.mark.parametrize("relative", [False, True])
def test_no_entry_path_reaches_the_brief(tmp_path, monkeypatch, relative):
    root = tmp_path
    if relative:
        monkeypatch.chdir(tmp_path)
        root = pathlib.Path("v")
    _story(root, "Told")
    slug = _seed(root)
    paths = [e["path"] for e in Vault(str(root)).read_evidence(KIND, verified_only=False)]
    assert paths and all(os.path.isabs(p) != relative for p in paths)
    r = Sluice(Config(dossier_dir=_dos(root)), store=Vault(str(root))).prep_brief(lead=slug)
    for p in paths:
        assert p not in r.markdown and p not in json.dumps(r.brief)


def _only_prep(monkeypatch, *kinds):
    """Rewire `read_by_prep` so exactly `kinds` carry it (the dict every reader shares)."""
    for k, spec in list(EVIDENCE_KINDS.items()):
        monkeypatch.setitem(protocols.EVIDENCE_KINDS, k,
                            dataclasses.replace(spec, read_by_prep=k in kinds))


def _skill(root, title, domain="pipeline", inbox=False):
    base = _kind_dir(root, "skills", inbox=inbox)
    os.makedirs(base, exist_ok=True)
    extra = "" if inbox else "verified: 2031-01-01\n"
    with open(os.path.join(base, f"{title}.md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\nDomain: {domain}\n{extra}---\nA skill body.\n")


def test_the_flag_has_a_consumer(tmp_path, monkeypatch):
    # Witnesses: hard-code "stories" in prep_brief; hard-code the noun in render_markdown.
    _only_prep(monkeypatch, "skills")
    _story(tmp_path, "Told")
    _skill(tmp_path, "Plumbing")
    _skill(tmp_path, "Waiting", inbox=True)
    slug = _seed(tmp_path)
    r = _app(tmp_path).prep_brief(lead=slug)
    assert list(r.brief["kinds"]) == ["skills"]
    assert "### Skills" in r.markdown and "#### Plumbing" in r.markdown
    assert "`job-sluice skills verify`" in r.markdown
    for text in (r.markdown, json.dumps(r.brief)):
        assert "stories" not in text.lower()
    os.remove(os.path.join(_kind_dir(tmp_path, "skills"), "Plumbing.md"))
    empty = _app(tmp_path).prep_brief(lead=slug)
    assert "`job-sluice skills add`" in empty.markdown
    assert "stories" not in empty.markdown.lower()


def test_the_kinds_shown_are_derived_from_the_flag(tmp_path):
    wanted = {k for k, s in EVIDENCE_KINDS.items() if s.read_by_prep}
    assert wanted, "no kind carries read_by_prep: this row would pass vacuously"
    for kind in EVIDENCE_KINDS:
        base = _kind_dir(tmp_path, kind)
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, f"One {kind}.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nverified: 2031-01-01\n---\nBody.\n")
    slug = _seed(tmp_path)
    r = _app(tmp_path).prep_brief(lead=slug)
    assert set(r.brief["kinds"]) == wanted


def test_two_prep_kinds_with_one_empty(tmp_path, monkeypatch):
    _only_prep(monkeypatch, "stories", "skills")
    slug = _ready(tmp_path)
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert set(r.brief["kinds"]) == {"stories", "skills"}
    assert r.outcome == "not_writable" and _codes(r) == [prepbrief.NO_ENTRIES]
    assert _bytes(tmp_path, slug) == before and r.markdown is not None
    assert "`job-sluice skills add`" in _sentence(r, prepbrief.NO_ENTRIES)


def test_a_hand_typed_category_word_shows_on_matched(tmp_path):
    # Amendment A4. Witness: drop `category` from prepbrief.entry_from_store.
    assert "monitoring" not in jd_keywords(ROLE, "")
    _story(tmp_path, "Plain", best_for="", category="monitoring")
    slug = _seed(tmp_path)
    r = _app(tmp_path).prep_brief(lead=slug)
    block = r.markdown.split("#### Plain\n", 1)[1]
    assert "- Matched: monitoring" in block.split("\n")


def test_a_lead_with_no_role_key_never_matches_none(tmp_path):
    # Spec Testing (PR 1), core/rank.py block, "No `role` key". The facade's `or ""` default
    # is the only thing between a missing key and `jd_keywords(None, ...)`, which reads the
    # string 'None' and adds the keyword `none`. Witness: pass `note.fm.get("role")` instead
    # of the `""` default in Sluice.prep_brief.
    assert "none" not in JD.lower() and "pipeline" in jd_keywords("", JD)
    _story(tmp_path, "Piped", best_for="none pipeline")
    slug = _seed(tmp_path, role=NO_ROLE)
    # Scope: the seeded note really lacks the key, so the default is what this row reaches.
    assert "role" not in _note(tmp_path, slug).fm
    r = _app(tmp_path).prep_brief(lead=slug)
    block = r.markdown.split("#### Piped\n", 1)[1]
    assert "- Matched: pipeline" in block.split("\n")


# ── the job description ─────────────────────────────────────────────────────────

def test_the_section_comes_first(tmp_path):
    # Witness: swap the JD order.
    slug = _ready(tmp_path)
    _cache_put(tmp_path, slug, jd=f"{SENTINEL} cached words only")
    r = _app(tmp_path).prep_brief(lead=slug)
    assert r.jd_source == "note" and r.jd_text == JD
    assert r.brief["jd"]["reason"] == "" and "- Job description: from the note (" in r.markdown


def test_a_second_jd_section_is_named_and_the_first_is_used(tmp_path, caplog):
    # Witness: pass jd_sections=1 to build_brief in Sluice.prep_brief.
    second = "An updated posting pasted below the first."
    slug = _ready(tmp_path, tail=f"\n## Job description\n\n{second}\n")
    assert jdsection.read(_note(tmp_path, slug).body) == (JD, 2)
    with caplog.at_level("WARNING"):
        r = _app(tmp_path).prep_brief(lead=slug, write=True)
    # Informational only: the brief is still written, built on the FIRST section.
    assert r.outcome == "written" and r.jd_text == JD and _codes(r) == []
    assert (f"- Job description: from the note ({len(JD)} characters; the note has 2 job "
            f"description sections and this is the first, the one triage and cv read)"
            ) in r.markdown.split("\n")
    assert "2 '## Job description' sections; the first was used" in caplog.text


def test_the_cache_comes_next_and_shows_built_at(tmp_path):
    slug = _ready(tmp_path, jd_section=None)
    _cache_put(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug)
    assert r.jd_source == "cached" and r.jd_text == JD
    assert r.brief["jd"]["built_at"] == BUILT.isoformat()
    assert r.brief["jd"]["reason"] == ""
    assert (f"- Job description: from a cached copy built {BUILT.isoformat()} "
            f"({len(JD)} characters)") in r.markdown.split("\n")


@pytest.mark.parametrize("arm, code", [
    ("none", "no_source"), ("empty", "cache_empty"), ("bad_json", "cache_unreadable")])
def test_each_none_arm_gives_its_code(tmp_path, arm, code):
    # Witness: restore the `"no_source"` initialiser (the found-JD rows above go red).
    slug = _ready(tmp_path, jd_section=None)
    if arm == "empty":
        cache = _stub_cache(tmp_path, "")
        _cache_raw(tmp_path, slug, json.dumps(
            cache._assemble(_note(tmp_path, slug).fm, {"jd": {"markdown": ""}})))
    elif arm == "bad_json":
        _cache_raw(tmp_path, slug, "{not json")
    r = _app(tmp_path).prep_brief(lead=slug)
    assert r.jd_source == "none" and r.jd_text == ""
    assert r.brief["jd"]["reason"] == code
    assert _codes(r) == [prepbrief.NO_JD]


@pytest.mark.parametrize("arm, reason", [
    ("bad_json", "the cached dossier is not valid JSON"),
    ("json_list", "the cached dossier is not a JSON object"),
    pytest.param("chmod", "PermissionError: Permission denied", marks=UNREADABLE_DIR)])
def test_an_unreadable_cache_is_reported_path_free(tmp_path, arm, reason):
    # Witnesses: drop OSError from the catch; format with str(exc); put the path in peek's
    # message; pass jd_reason_text="" to build_brief.
    slug = _ready(tmp_path, jd_section=None)
    before = _bytes(tmp_path, slug)
    if arm == "chmod":
        path = _cache_put(tmp_path, slug)
        os.chmod(path, 0)
    else:
        path = _cache_raw(tmp_path, slug, "[1, 2]" if arm == "json_list" else "{not json")
    try:
        r = _app(tmp_path).prep_brief(lead=slug, write=True)
    finally:
        os.chmod(path, 0o644)
    assert r.outcome == "not_writable" and r.brief["jd"]["reason"] == "cache_unreadable"
    # The path-free reason itself reaches the brief's JD line (spec section 3).
    assert (f"- Job description: none (the cached copy could not be read: {reason})"
            in r.markdown.split("\n"))
    assert _bytes(tmp_path, slug) == before
    for text in (r.reason, r.markdown, json.dumps(r.brief)):
        for leak in (path, _dos(tmp_path), os.environ["HOME"]):
            assert leak not in text


@pytest.mark.parametrize("arm", ["section", "cached", "none", "unreadable", "write"])
def test_prep_never_fetches(tmp_path, monkeypatch, arm):
    slug = _ready(tmp_path, jd_section=JD if arm in ("section", "write") else None)
    if arm == "cached":
        _cache_put(tmp_path, slug)
    elif arm == "unreadable":
        _cache_raw(tmp_path, slug, "{not json")
    calls = []
    monkeypatch.setattr(Sluice, "fetcher", lambda self: calls.append("fetcher"))
    monkeypatch.setattr(DossierCache, "get_or_build",
                        lambda self, *a, **k: calls.append("get_or_build"))
    r = _app(tmp_path).prep_brief(lead=slug, write=arm == "write")
    assert r.outcome in ("ok", "written")
    assert calls == []


def test_a_jd_reader_error_propagates(tmp_path, monkeypatch):
    slug = _ready(tmp_path)

    def broken(body):
        raise RuntimeError("reader bug")

    monkeypatch.setattr(jdsection, "read", broken)
    with pytest.raises(RuntimeError, match="reader bug"):
        _app(tmp_path).prep_brief(lead=slug)


@pytest.mark.parametrize("source", ["section", "cached"])
def test_the_jd_is_never_embedded(tmp_path, source):
    jd = f"{SENTINEL} {JD}"
    slug = _ready(tmp_path, jd_section=jd if source == "section" else None)
    if source == "cached":
        _cache_put(tmp_path, slug, jd=jd)
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "written"
    assert SENTINEL in r.jd_text
    assert SENTINEL not in r.markdown
    assert SENTINEL not in _appended(before, _bytes(tmp_path, slug))
    assert SENTINEL not in json.dumps(r.brief)


# ── degradations through the facade ─────────────────────────────────────────────

def test_a_recased_unjudgeable_status_is_a_rejected_page(tmp_path):
    # Witness: read note.fm["status"] raw instead of the store-normalised note.status.
    slug = _ready(tmp_path, status="Unjudgeable", jd_section=None)
    _cache_put(tmp_path, slug)
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "not_writable" and _codes(r) == [prepbrief.JD_REJECTED_PAGE]
    assert "move the lead's status off `unjudgeable` by hand" in r.reason
    assert _bytes(tmp_path, slug) == before and r.markdown is not None


def test_a_stamped_research_lead_is_a_rejected_page(tmp_path):
    slug = _ready(tmp_path, status="research", jd_section=None,
                  extra=("triage_blocked: 2031-03-01",))
    _cache_put(tmp_path, slug)
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "not_writable" and _codes(r) == [prepbrief.JD_REJECTED_PAGE]
    assert _bytes(tmp_path, slug) == before and r.markdown is not None


def test_a_typed_word_in_the_stamp_is_not_a_stamp(tmp_path):
    # Witness: read triage_blocked directly instead of through blocked_stamp.
    assert jd_keywords(ROLE, JD) != jd_keywords(ROLE, "")
    slug = _ready(tmp_path, status="research", jd_section=None,
                  extra=("triage_blocked: my own note",))
    _cache_put(tmp_path, slug)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"


@pytest.mark.parametrize("status, extra", [
    ("unjudgeable", ()), ("research", ("triage_blocked: 2031-03-01",))])
def test_controls_with_their_own_section_write(tmp_path, status, extra):
    # Witness: refuse every unjudgeable lead whatever the JD source.
    slug = _ready(tmp_path, status=status, extra=extra)
    _cache_put(tmp_path, slug)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"


def test_write_refuses_a_note_ending_in_an_open_fence(tmp_path):
    # Amendment A5 / Review Focus 1. Witness: pass open_fence=False.
    slug = _ready(tmp_path, tail="\n```\nposting")
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "not_writable" and _codes(r) == [prepbrief.NOTE_OPEN_FENCE]
    assert _bytes(tmp_path, slug) == before
    assert r.markdown is not None


@pytest.mark.parametrize("source", ["section", "cached"])
@pytest.mark.parametrize("floor_delta, outcome", [(0, "written"), (1, "not_writable")])
def test_floor_boundary_through_the_facade(tmp_path, source, floor_delta, outcome):
    # Amendment A5: a JD of exactly min_jd_chars writes; one character short refuses.
    # Witness: compare len(jd) to the floor in the facade instead of calling jd_arrived.
    assert jd_keywords(ROLE, JD) != jd_keywords(ROLE, "")
    slug = _ready(tmp_path, jd_section=JD if source == "section" else None)
    if source == "cached":
        _cache_put(tmp_path, slug)
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path, min_jd_chars=len(JD) + floor_delta).prep_brief(lead=slug, write=True)
    assert r.outcome == outcome
    assert _codes(r) == ([] if floor_delta == 0 else [prepbrief.JD_BELOW_FLOOR])
    assert r.markdown is not None
    if outcome == "not_writable":
        assert _bytes(tmp_path, slug) == before


def test_two_at_once(tmp_path):
    slug = _seed(tmp_path, jd_section=None)
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "not_writable"
    assert _bytes(tmp_path, slug) == before and r.markdown is not None
    assert _codes(r) == [prepbrief.NO_ENTRIES, prepbrief.NO_JD]
    assert r.reason == "; ".join(d["sentence"] for d in r.brief["degradations"])


def test_a_stale_good_copy_behind_a_failed_refetch_is_refused(tmp_path):
    """A good cached copy older than the TTL, then a refetch returning nothing through
    triage's pre-judge gate, which stamps the `research` lead and persists nothing."""
    import sluice.triage.engine as eng
    from sluice.triage.audit import AuditLog
    from sluice.triage.config import TriageConfig
    from tests.test_triage_blocked import _Unjudgeable
    _story(tmp_path, "Told")
    slug = _seed(tmp_path, status="research", jd_section=None,
                 extra=('location: "remote"', 'role_type: "permanent"', "score: 0",
                        'relevance_notes: ""'))
    _cache_put(tmp_path, slug, clock=datetime(2031, 1, 1))
    cfg = TriageConfig()
    cfg.accept_titles = [ROLE.lower()]
    refetch = _stub_cache(tmp_path, "", clock=datetime(2031, 3, 14))
    judge = _Unjudgeable()
    eng.run(Vault(str(tmp_path)), cfg, judge, refetch,
            AuditLog(str(tmp_path / "audit.jsonl")), statuses=("research",))
    assert judge.prompts == []
    assert _note(tmp_path, slug).fm.get("triage_blocked")
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.jd_source == "cached" and r.jd_text == JD
    assert r.outcome == "not_writable" and _codes(r) == [prepbrief.JD_REJECTED_PAGE]
    assert _bytes(tmp_path, slug) == before and r.markdown is not None
    assert ("a later fetch came back empty while this older copy stayed cached"
            in _sentence(r, prepbrief.JD_REJECTED_PAGE))


# ── each way out unblocks a write (Review Focus 3) ──────────────────────────────

def _rejected(tmp_path, status, extra):
    slug = _ready(tmp_path, status=status, jd_section=None, extra=extra)
    _cache_put(tmp_path, slug)
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "not_writable" and _codes(r) == [prepbrief.JD_REJECTED_PAGE]
    assert _bytes(tmp_path, slug) == before and r.markdown is not None
    return slug, _sentence(r, prepbrief.JD_REJECTED_PAGE)


def _hand_edit(tmp_path, slug, old, new):
    path = pathlib.Path(_note(tmp_path, slug).ref)
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_unpark_unblocks_a_stamped_research_lead(tmp_path):
    slug, sentence = _rejected(tmp_path, "research", ("triage_blocked: 2031-03-01",))
    assert f"`job-sluice leads unpark --apply {shlex.quote(slug)}`" in sentence
    assert "needs_review" in sentence
    _, results = _app(tmp_path).unpark(slugs=[slug])
    assert results == [(slug, "moved")]
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"


def test_a_hand_delete_unblocks_a_stamped_lead_moved_to_interview(tmp_path):
    # Witness: name `leads unpark` for every stamped lead.
    slug, sentence = _rejected(tmp_path, "interview", ("triage_blocked: 2031-03-01",))
    assert "delete the `triage_blocked` value in the note by hand" in sentence
    assert "unpark" not in sentence
    _hand_edit(tmp_path, slug, "triage_blocked: 2031-03-01", 'triage_blocked: ""')
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"


def test_leads_answer_unblocks_a_lead_awaiting_a_reply(tmp_path):
    slug, sentence = _rejected(tmp_path, "research",
                               ("triage_blocked: 2031-03-01", "followup_asked: 2031-03-02"))
    assert f"`job-sluice leads answer --lead {shlex.quote(slug)} --text ...`" in sentence
    assert "unpark" not in sentence
    assert _app(tmp_path).record_answer(lead=slug, text="the reply").outcome == "answered"
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"


def test_a_hand_status_change_unblocks_an_unjudgeable_lead(tmp_path):
    slug, sentence = _rejected(tmp_path, "unjudgeable", ())
    assert "move the lead's status off `unjudgeable` by hand" in sentence
    assert "triage_blocked" not in sentence and "unpark" not in sentence
    _hand_edit(tmp_path, slug, "status: unjudgeable", "status: shortlist")
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"


def test_a_stamped_unjudgeable_lead_names_both_actions(tmp_path):
    # Amendment A5: following the named way out ONCE unblocks the write.
    slug, sentence = _rejected(tmp_path, "unjudgeable", ("triage_blocked: 2031-03-01",))
    assert "delete the `triage_blocked` value" in sentence
    assert "move the lead's status off `unjudgeable`" in sentence
    assert "unpark" not in sentence
    _hand_edit(tmp_path, slug, "triage_blocked: 2031-03-01", 'triage_blocked: ""')
    _hand_edit(tmp_path, slug, "status: unjudgeable", "status: shortlist")
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"


def test_a_hostile_slug_is_only_inside_single_quotes(tmp_path):
    # Amendment A5 / A10: a slug carrying `$(`, a backtick and a double quote.
    assert COMPANY in _REVIEWED_FIXTURE_IDENTITIES
    hostile = f'$(x)`{COMPANY}" - {ROLE}'
    slug = _ready(tmp_path, status="research", jd_section=None, name=hostile,
                  extra=("triage_blocked: 2031-03-01",))
    assert slug == hostile
    _cache_put(tmp_path, slug)
    sentence = _sentence(_app(tmp_path).prep_brief(lead=slug), prepbrief.JD_REJECTED_PAGE)
    quoted = shlex.quote(slug)
    assert quoted.startswith("'") and quoted in sentence
    assert sentence.count(slug) == sentence.count(quoted) == 1


# ── the write ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ends_in_newline", [True, False])
def test_the_byte_relation(tmp_path, ends_in_newline):
    slug = _ready(tmp_path)
    path = pathlib.Path(_note(tmp_path, slug).ref)
    if not ends_in_newline:
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
    before = path.read_text(encoding="utf-8")
    assert before.endswith("\n") == ends_in_newline
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "written"
    after = path.read_text(encoding="utf-8")
    sep = "" if before.endswith("\n") else "\n"
    assert after == before + sep + "\n" + r.markdown + "\n"
    head = before.split("\n---\n", 1)[0]
    assert after.split("\n---\n", 1)[0] == head


def test_reruns_are_already_written(tmp_path):
    slug = _ready(tmp_path)
    first = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert (first.outcome, first.appended) == ("written", True)
    written = _bytes(tmp_path, slug)
    again = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert (again.outcome, again.appended) == ("already_written", False)
    assert _bytes(tmp_path, slug) == written
    _story(tmp_path, "Another", best_for="deploy")
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "already_written" and "#### Another" in r.markdown
    assert _bytes(tmp_path, slug) == written


def test_a_pending_story_then_verify_then_rerun(tmp_path):
    # Review Focus 4.
    _story(tmp_path, "Told")
    _story(tmp_path, "Drafted", best_for="deploy", inbox=True)
    slug = _seed(tmp_path)
    before = _bytes(tmp_path, slug)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"
    section = prepbrief._own_sections(_note(tmp_path, slug).body)[0]
    assert ("then delete this section and run `leads prep --write` again, to include it"
            in section)
    assert "Drafted" not in section
    store = Vault(str(tmp_path))
    assert store.verify_evidence(KIND, "Drafted", today=TODAY,
                                 reviewed=store.read_pending_evidence_text(KIND, "Drafted"))
    written = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "already_written" and "#### Drafted" in r.markdown
    assert _bytes(tmp_path, slug) == written
    # Delete the section by hand, and write again: the story is now in the note.
    pathlib.Path(_note(tmp_path, slug).ref).write_bytes(before)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"
    assert "#### Drafted" in prepbrief._own_sections(_note(tmp_path, slug).body)[0]


def test_a_degraded_rerun_says_the_note_already_has_a_brief(tmp_path):
    slug = _ready(tmp_path)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"
    os.remove(os.path.join(_kind_dir(tmp_path), "Told.md"))
    written = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "not_writable" and _codes(r) == [prepbrief.NO_ENTRIES]
    assert r.reason == _sentence(r, prepbrief.NO_ENTRIES) + "; the note already has a brief"
    assert _bytes(tmp_path, slug) == written and r.markdown is not None


class _FenceFirst(Vault):
    """A concurrent edit: an unclosed fence lands before EOF, then the real append runs."""

    def append_body_section(self, ref, tag, section_md, **kw):
        path = pathlib.Path(ref)
        path.write_text(path.read_text(encoding="utf-8") + "\n```\n", encoding="utf-8")
        return super().append_body_section(ref, tag, section_md, **kw)


def test_a_concurrent_fence_gives_written_hidden(tmp_path):
    # Witness: classify by heading presence, not content.
    slug = _ready(tmp_path)
    app = Sluice(Config(dossier_dir=_dos(tmp_path)), store=_FenceFirst(str(tmp_path)))
    assert app.prep_brief(lead=slug, write=True).outcome == "written_hidden"


def test_the_tag_out_of_place_is_present_unreadable(tmp_path):
    # Witness: read with jdsection.sections(body, HEADING, TAG), the optional-tag form.
    slug = _ready(tmp_path, extra=(f'notes: "{prepbrief.TAG}"',),
                  tail=f"\n{prepbrief.HEADING}\n\nan older hand-typed brief\n")
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == "present_unreadable"
    assert _bytes(tmp_path, slug) == before


def test_recased_sections(tmp_path):
    slug = _ready(tmp_path)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"
    # The whole tagged line recased: the exact-case tag is gone, so it appends again.
    _hand_edit(tmp_path, slug, prepbrief.TAGGED_HEADING, prepbrief.TAGGED_HEADING.upper())
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"
    assert len(jdsection.sections(_note(tmp_path, slug).body, prepbrief.TAGGED_HEADING)) == 2


def test_recased_heading_words_with_the_exact_tag_are_already_written(tmp_path):
    slug = _ready(tmp_path)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"
    _hand_edit(tmp_path, slug, prepbrief.TAGGED_HEADING,
               f"{prepbrief.HEADING.upper()} {prepbrief.TAG}")
    before = _bytes(tmp_path, slug)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "already_written"
    assert _bytes(tmp_path, slug) == before


def test_a_trailing_whitespace_heading_is_still_the_brief(tmp_path):
    # Amendment A5: sections(TAGGED_HEADING) matches a trailing-whitespace heading line.
    slug = _ready(tmp_path)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"
    _hand_edit(tmp_path, slug, prepbrief.TAGGED_HEADING, prepbrief.TAGGED_HEADING + "  \t")
    before = _bytes(tmp_path, slug)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "already_written"
    assert _bytes(tmp_path, slug) == before


class _GoneAfter(Vault):
    def append_body_section(self, ref, tag, section_md, **kw):
        out = super().append_body_section(ref, tag, section_md, **kw)
        os.remove(ref)
        return out


def test_the_note_gone_after_the_write_is_not_found(tmp_path):
    slug = _ready(tmp_path)
    app = Sluice(Config(dossier_dir=_dos(tmp_path)), store=_GoneAfter(str(tmp_path)))
    r = app.prep_brief(lead=slug, write=True)
    assert r.outcome == "not_found" and r.slug == slug
    # The append landed before the note went, and the result says so.
    assert r.appended is True and r.markdown is not None


@pytest.mark.parametrize("exc, outcome", [
    (VaultConflict("race"), "conflict"),
    (FileNotFoundError(2, "No such file or directory", "/abs/vault/note.md"), "conflict"),
    (PermissionError(13, "Permission denied", "/abs/vault/note.md"), "write_failed"),
])
def test_write_exceptions(tmp_path, monkeypatch, exc, outcome):
    # Witnesses: catch only VaultConflict; catch only FileNotFoundError.
    slug = _ready(tmp_path)

    def raising(self, *a, **k):
        raise exc

    monkeypatch.setattr(Vault, "append_body_section", raising)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome == outcome and r.slug == slug
    if outcome == "write_failed":
        assert r.reason == "PermissionError: Permission denied"
        assert "/abs" not in r.reason
    else:
        assert r.reason == ""


@pytest.mark.parametrize("status", ["new", "shortlist", "interview", "rejected"])
def test_any_status_writes_and_no_status_is_written(tmp_path, status):
    slug = _ready(tmp_path, status=status)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"
    assert _note(tmp_path, slug).fm["status"] == status


def test_no_llm_and_no_citation(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("prep must not reach this")

    monkeypatch.setattr(Sluice, "backend", boom)
    monkeypatch.setattr(cvbundle, "build_bundle", boom)
    monkeypatch.setattr(cvbundle, "assign_codes", boom)
    slug = _ready(tmp_path)
    assert _app(tmp_path).prep_brief(lead=slug, write=True).outcome == "written"
    assert "[" not in prepbrief._own_sections(_note(tmp_path, slug).body)[0]


# ── resolve ─────────────────────────────────────────────────────────────────────

def test_an_exact_slug_resolves(tmp_path):
    slug = _ready(tmp_path)
    r = _app(tmp_path).prep_brief(lead=slug)
    assert r.outcome == "ok" and r.slug == slug and r.status == "interview"


def test_a_fragment_suggests_an_interview_lead_and_writes_nothing(tmp_path):
    # Review Focus 2. Witness: compute suggestions from a triage-status-filtered read.
    slug = _ready(tmp_path, status="interview")
    before = _bytes(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=ROLE.split()[0].lower(), write=True)
    assert r.outcome == "not_found" and r.suggestions == [slug]
    assert r.more_suggestions == 0 and r.markdown is None and r.appended is False
    assert _bytes(tmp_path, slug) == before


@pytest.mark.parametrize("fragment", ["", "e"])
def test_suggestions_are_bounded(tmp_path, fragment):
    # Witness: drop the suggestion bound.
    extra = 3
    slugs = [_seed(tmp_path, name=f"Example Ltd - {ROLE} {i:02d}", role=f"{ROLE} {i:02d}",
                   url=f"https://example.invalid/{i}")
             for i in range(prepbrief.SUGGESTION_LIMIT + extra)]
    r = _app(tmp_path).prep_brief(lead=fragment)
    assert r.outcome == "not_found"
    assert r.suggestions == sorted(slugs)[:prepbrief.SUGGESTION_LIMIT]
    assert r.more_suggestions == extra


def test_twins_are_ambiguous(tmp_path):
    _story(tmp_path, "Told")
    for sub in ("Active", "Archive"):
        os.makedirs(os.path.join(_leads_dir(tmp_path), sub))
        _seed(tmp_path, name=os.path.join(sub, f"Example Ltd - {ROLE}"))
    r = _app(tmp_path).prep_brief(lead=f"Example Ltd - {ROLE}", write=True)
    assert r.outcome == "ambiguous" and len(r.candidates) == 2


def test_a_case_changed_slug_is_not_found_and_suggests_the_real_one(tmp_path):
    slug = _ready(tmp_path)
    r = _app(tmp_path).prep_brief(lead=slug.upper())
    assert r.outcome == "not_found" and r.suggestions == [slug]


# ── rendering safety (Review Focus 5) ───────────────────────────────────────────

# Pinned literally (spec, Rendering safety): a JD heading, an unclosed fence, an unclosed
# comment, then a follow-up answers heading.
HOSTILE_BODY = ("## Job description\n\nstory text one\n\n```\nstory text two\n\n"
                "<!-- story text three\n\n## Follow-up answers\n\nstory text four")


def test_a_hostile_story_with_a_cache_only_jd(tmp_path):
    # Witness: drop the blockquote prefix (raw, the read gives count 1).
    _story(tmp_path, "Hostile", body=HOSTILE_BODY)
    slug = _seed(tmp_path, jd_section=None)
    _cache_put(tmp_path, slug)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome != "not_writable", r.reason
    assert jdsection.read(_note(tmp_path, slug).body) == ("", 0)
    assert r.outcome == "written"


def test_a_hostile_story_with_the_notes_own_jd(tmp_path):
    # Witness: drop the blockquote prefix (count 2).
    _story(tmp_path, "Hostile", body=HOSTILE_BODY)
    slug = _seed(tmp_path)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome != "not_writable", r.reason
    assert jdsection.read(_note(tmp_path, slug).body) == (JD, 1)
    assert r.outcome == "written"


def test_a_followup_answer_after_a_hostile_brief(tmp_path):
    # Witness: drop the blockquote prefix.
    _story(tmp_path, "Hostile", body=HOSTILE_BODY)
    slug = _seed(tmp_path, status="research")
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome != "not_writable", r.reason
    a = _app(tmp_path).record_answer(lead=slug, text="the reply")
    assert a.outcome == "answered"
    assert followups.answer_sections(_note(tmp_path, slug).body) == [
        followups.dated_answer("the reply", TODAY)]
    assert r.outcome == "written"


@pytest.mark.parametrize("prefix, hostile_role", list(zip(_HOSTILE_PREFIXES, _HOSTILE_ROLES)))
def test_the_line_grammar_holds_in_a_written_note(tmp_path, prefix, hostile_role):
    # Amendment A5. Witness: delete the whole `- Lead: ` / `- Role: ` prefix.
    assert COMPANY in _REVIEWED_FIXTURE_IDENTITIES
    company = prefix + COMPANY
    _story(tmp_path, "Told")
    slug = _seed(tmp_path, status="research", name=f"Example Ltd - {ROLE}",
                 role=hostile_role, fm_company=company)
    r = _app(tmp_path).prep_brief(lead=slug, write=True)
    assert r.outcome != "not_writable", r.reason
    templates = {d["sentence"] for d in r.brief["degradations"]}
    bad = [ln for ln in r.markdown.split("\n")
           if ln not in templates and not any(p.match(ln) for p in _GRAMMAR)]
    assert bad == []
    assert company in r.markdown and hostile_role in r.markdown
    body = _note(tmp_path, slug).body
    assert prepbrief._own_sections(body) == [r.markdown.split("\n", 1)[1].strip()]
    assert _app(tmp_path).record_answer(lead=slug, text="the reply").outcome == "answered"
    assert followups.answer_sections(_note(tmp_path, slug).body) == [
        followups.dated_answer("the reply", TODAY)]
    assert r.outcome == "written"
