"""The shipped Obsidian Bases view (#240), and the couplings that make it work.

Every lead note `core/vault.py` writes carries `base: "[[Job Leads.base]]"`, and that key is
the view's own membership predicate. Until #240 nothing created the file it named, so each note
shipped an unresolved link and the user never got the table the link exists to open.

Two of the tests below are about a class of failure this artefact is unusually exposed to:
**a Bases view that does not match anything renders as an empty table, not as an error.** A
filter naming a property that no longer exists, or a status value that was never in the
vocabulary, looks exactly like "no leads in that state yet". Nothing on the sluice side can
notice, and nothing on the Obsidian side reports it.

That is not hypothetical. The real hand-built view this artefact was modelled on filters its
shortlist tab on `status == "shortlisted"`, and the canonical value is `shortlist`
(`core/status.py`). The tab has therefore been silently empty for its whole life. Both
`test_every_status_the_view_filters_on_is_canonical` and
`test_every_property_the_view_shows_is_one_the_vault_writes` exist to make that shape loud
here, and both derive their expected set rather than hand-listing it.
"""

import os
import re

# A plain import, not `importorskip`: PyYAML is a hard runtime dependency of the installed
# package, and a test that skips itself is how a guard goes silently absent (the tree-wide
# `test_no_test_module_uses_importorskip` sweep enforces that).
import yaml

from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.protocols import LEADS_VIEW_RELPATH
from sluice.core.status import CANONICAL
from sluice.core.vault import Vault
from sluice.onboard.plan import LEADS_VIEW_TEXT, build_plan


def _view():
    return yaml.safe_load(LEADS_VIEW_TEXT)


def _written_frontmatter_keys(tmp_path):
    """The frontmatter keys a REAL created lead note carries, read back off disk.

    Derived rather than hand-listed: a list here would be a second copy of
    `Vault._render`'s and would drift the moment a key is added, which is the failure this
    file exists to prevent rather than to reproduce.
    """
    # Values MIRROR README's sample lead note deliberately: Quickstart step 4 tells the reader
    # to save that exact note, so the test and the document must describe the same lead or the
    # transcript guard and this one would be pinning different things. That is why this is not
    # the seeded-faker `titles` fixture -- the coupling to README is the point. Both are already
    # swept by `test_fixture_name_neutrality.py`.
    vault = Vault(str(tmp_path / "vault"))
    vault.upsert(Lead(source="manual", search="", title="Senior Engineer",
                      company="Example Systems", location="Example City",
                      salary="", url="https://example.invalid/jobs/1",
                      job_type="", job_type_source="", first_seen="", last_seen=""))
    notes = [os.path.join(dp, f)
             for dp, _, fs in os.walk(vault.leads_dir) for f in fs if f.endswith(".md")]
    assert len(notes) == 1, f"expected exactly one created note, got {notes}"
    with open(notes[0], encoding="utf-8") as fh:
        body = fh.read()
    block = body.split("---")[1]
    return {line.split(":", 1)[0].strip() for line in block.strip().split("\n") if ":" in line}


def test_init_writes_the_view_and_never_overwrites_it(tmp_path):
    """The whole point: after init, the link every lead note carries resolves."""
    from sluice.stores.vault import _make

    store = _make(Config(vault_dir=str(tmp_path / "vault")))
    plan = build_plan({})
    assert plan.view_text, "build_plan no longer carries the view text"

    first = store.write_document(LEADS_VIEW_RELPATH, plan.view_text, only_if_absent=True)
    assert first, "the view was not written on a fresh vault"

    # A user's own edits to their view are theirs. `only_if_absent` is the whole guard.
    with open(os.path.join(str(tmp_path / "vault"), LEADS_VIEW_RELPATH), "w",
              encoding="utf-8") as fh:
        fh.write("filters:\n  and:\n    - note[\"base\"] == link(\"Job Leads.base\")\n")
    again = store.write_document(LEADS_VIEW_RELPATH, plan.view_text, only_if_absent=True)
    assert not again, "init overwrote a view the user had already edited"
    with open(os.path.join(str(tmp_path / "vault"), LEADS_VIEW_RELPATH), encoding="utf-8") as fh:
        assert "views:" not in fh.read(), "the user's edited view was clobbered"


def test_the_views_membership_predicate_matches_what_every_note_carries(tmp_path):
    """The load-bearing coupling. `core/vault.py` stamps `base: "[[Job Leads.base]]"` into
    every lead; the view selects on exactly that. Rename either and every note already in the
    vault falls out of the view, silently and permanently."""
    keys = _written_frontmatter_keys(tmp_path)
    assert "base" in keys, (
        "created lead notes no longer carry a `base` key, so the shipped view selects nothing")

    predicate = _view()["filters"]["and"]
    target = re.search(r'link\("([^"]+)"\)', " ".join(predicate))
    assert target, f"the view's filter no longer contains a link() predicate: {predicate}"

    # The filename the predicate names must be the file init actually writes.
    assert target.group(1) == os.path.basename(LEADS_VIEW_RELPATH), (
        f"the view selects on link({target.group(1)!r}) but init writes "
        f"{os.path.basename(LEADS_VIEW_RELPATH)!r}")

    # ...and the note's own value must name the same file.
    vault = Vault(str(tmp_path / "vault"))
    notes = [os.path.join(dp, f)
             for dp, _, fs in os.walk(vault.leads_dir) for f in fs if f.endswith(".md")]
    with open(notes[0], encoding="utf-8") as fh:
        note = fh.read()
    assert f'[[{target.group(1)}]]' in note, (
        f"lead notes do not link to {target.group(1)!r}, which is what the view selects on")


def test_every_status_the_view_filters_on_is_canonical():
    """A filter on a status that is not in the vocabulary renders an empty tab, forever, with
    nothing reporting it. The real vault this was modelled on has exactly that bug."""
    filters = []
    for view in _view()["views"]:
        for clause in (view.get("filters") or {}).get("and", []):
            filters.append(clause)
    # SCOPE: a parse that found no filters would satisfy the loop below having checked nothing.
    assert len(filters) >= 3, (
        f"only {len(filters)} view filters found; this sweep has stopped examining the views")

    matched, unmatched = [], []
    for clause in filters:
        m = re.match(r'^status == "([^"]+)"$', clause.strip())
        if not m:
            unmatched.append(clause)
            continue
        matched.append(m.group(1))
        assert m.group(1) in CANONICAL, (
            f"the view filters on status {m.group(1)!r}, which is not in the canonical "
            f"vocabulary {sorted(CANONICAL)}. That tab renders empty forever and nothing "
            "reports it.")
    # Scope on the MATCHED set, not the found set. The `continue` above means a change to Bases'
    # filter spelling would make this sweep examine zero statuses while the count of clauses
    # stayed healthy -- the assertion above would then hold vacuously and only a sibling test
    # would notice.
    assert not unmatched, (
        f"{len(unmatched)} of {len(filters)} view filters are no longer `status == \"...\"` "
        f"clauses, so this sweep silently stopped checking them: {unmatched}")


def test_every_property_the_view_shows_is_one_the_vault_writes(tmp_path):
    """Same failure shape on the column axis: a column naming a key no note carries is blank
    rather than broken."""
    keys = _written_frontmatter_keys(tmp_path)
    view = _view()

    named = set(view.get("properties") or {})
    for v in view["views"]:
        named.update(v.get("order") or [])
        if v.get("groupBy"):
            named.add(v["groupBy"]["property"])
        for s in v.get("sort") or []:
            named.add(s["property"])
    assert named, "the view names no properties at all, so this sweep examined nothing"

    # `file.*` are Bases' own built-ins, not frontmatter.
    unknown = {p for p in named if not p.startswith("file.")} - keys
    assert not unknown, (
        f"the view shows {sorted(unknown)}, which a created lead note does not carry. Those "
        f"columns render blank. Notes carry: {sorted(keys)}")


def test_the_shipped_view_names_no_place_employer_or_preference():
    """Neutrality (#27). A hand-built view of this kind naturally grows a tab per city the
    author is searching, and those tabs ARE the hunt geography that rule is about. This one
    ships to every user, so its tabs are keyed on sluice's own status vocabulary instead."""
    view = _view()
    names = [v["name"] for v in view["views"]]
    assert names, "no views defined"

    # Every filter must be on a status, or on the membership predicate. A `.contains(...)`
    # on location is precisely the shape that leaked before.
    for v in view["views"]:
        for clause in (v.get("filters") or {}).get("and", []):
            assert re.match(r'^status == "[^"]+"$', clause.strip()), (
                f"view {v['name']!r} filters on {clause!r}. Only status filters are neutral by "
                "construction; anything else risks shipping one person's search.")

    # SHAPES, never a gazetteer. An earlier version of this loop listed two real city names --
    # taken from the very hand-built view this artefact was modelled on -- and that is the trap
    # CLAUDE.md names for this repo: writing the removed values into `tests/` to forbid them puts
    # them back in the public tree, and a pair of cities listed together as "places a job-search
    # view must not name" IS the #27 captured-set shape rather than a check against it. It was
    # redundant too: the structural assertion above already rejects every non-status filter, and
    # a fixed list of two could never catch a third.
    #
    # What remains is keyed on shapes no place name is needed to describe: the location-filter
    # construct, and currency symbols. Nothing local can classify a bare word as a real place, so
    # the guarantee here is structural -- filters are status equalities (above) and properties are
    # keys a real note carries (`test_every_property_the_view_shows_is_one_the_vault_writes`).
    # A place name smuggled into a view's NAME would pass this file, and that is stated rather
    # than papered over with a word list that would only appear to close it.
    lowered = LEADS_VIEW_TEXT.lower()
    for token in ("contains(", "£", "$", "€"):
        assert token not in lowered, (
            f"the shipped view contains {token!r}: a location-filter construct or a pay figure, "
            "neither of which a neutral shipped view has any use for")
