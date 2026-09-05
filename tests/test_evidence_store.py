"""Vault-level tests for the evidence corpus (#164).

Field NAMES ship in this repo; example VALUES do not. Fixtures here use neutral
placeholders only.
"""
import os

import pytest

import sluice.core.vault as _mod
from sluice.core.protocols import EVIDENCE_KINDS, EvidenceKind
from sluice.core.vault import Vault


def test_the_registry_names_exactly_the_three_kinds():
    assert set(EVIDENCE_KINDS) == {"experience", "skills", "stories"}


def test_no_kind_carries_the_store_managed_verified_key_as_a_user_field():
    """`verified` is what makes an entry citable by the hard fabrication gate.

    The CLI derives `add`'s flags from these tuples, so a kind listing `verified`
    here would generate a `--verified` flag -- the exact thing decision 2 says
    exists nowhere. This is the guard for that, not a comment about it.
    """
    for kind, spec in EVIDENCE_KINDS.items():
        assert "verified" not in spec.fields, \
            f"{kind} lists the store-managed 'verified' key as a user field"


def test_a_floor_map_naming_a_key_that_is_not_a_floor_key_is_refused_at_construction():
    """The floor keys are the four `FLOOR_FIELD_SOURCES` names and nothing else.

    `Vault._evidence_entries` spreads `floor_sources()` in among the literal
    `path`/`title`/`verified`/`body` keys of the entry dict it builds. Measured against
    that literal: a `floor_map` naming `title` or `path` OVERWRITES them with a
    user-supplied frontmatter value, and `verified` -- the key that decides citability --
    escaped only because the spread happens to sit ABOVE it, so a tidy-up reorder of a
    dict literal would have handed a user the citability key. Restricting the floor key
    at CONSTRUCTION makes the floor disjoint from every literal key, which is what stops
    that ordering from being load-bearing.

    `verified` is asserted alongside the two that were measurably reachable, because it
    is the one whose harm is silent and irreversible, and the guard must not be narrowed
    to "the keys someone reproduced".
    """
    for stolen in ("title", "path", "verified", "body"):
        with pytest.raises(ValueError, match="not a text floor key"):
            EvidenceKind("Job Applications/X", ("Domain",),
                         floor_map=((stolen, "Domain"),))


def test_a_floor_map_naming_an_undeclared_frontmatter_key_is_refused_at_construction():
    """`floor_sources()` feeds `fm.get(key, "")`, so a typo'd frontmatter key yields the
    EMPTY STRING for every entry with nothing red anywhere. That is exactly the shape of
    the zero-score bug the `floor_map` was added to fix -- a skills entry in domain
    `platform` scoring zero in `cv/bundle.py`'s `rank()` against the keyword `platform`
    -- silently re-opened by a misspelling. The kind's own `fields` is the only honest
    thing to check against."""
    with pytest.raises(ValueError, match="does not declare"):
        EvidenceKind("Job Applications/X", ("Domain",),
                     floor_map=(("best_for", "Domian"),))


def test_every_shipped_kind_passes_its_own_construction_guard():
    """SCOPE, not violations. A guard whose sweep enumerates nothing satisfies every
    assertion over it, so this pins that the registry was actually built through
    `__post_init__` -- re-constructing each shipped kind from its own attributes, which
    raises if any of them would be refused -- and that at least one kind exercises the
    `floor_map` arm at all (with none, the guard could be inert and this file green)."""
    assert EVIDENCE_KINDS, "the registry is empty -- every assertion below is vacuous"
    assert any(spec.floor_map for spec in EVIDENCE_KINDS.values()), \
        "no shipped kind uses floor_map -- the guard's mapped arm is unexercised"
    for kind, spec in EVIDENCE_KINDS.items():
        rebuilt = EvidenceKind(spec.relpath, spec.fields,
                               cited_by_gate=spec.cited_by_gate,
                               read_by_composer=spec.read_by_composer,
                               floor_map=spec.floor_map)
        assert rebuilt == spec, kind


def test_every_relpath_is_a_slash_separated_contract_key():
    """Not os.path.join: `_doc_path` splits on "/" and re-joins with the platform
    separator, so a backslash here would not survive Windows."""
    for kind, spec in EVIDENCE_KINDS.items():
        assert "\\" not in spec.relpath, f"{kind}'s relpath is not a contract key"
        assert spec.relpath.startswith("Job Applications/"), kind


def test_the_citability_key_is_written_in_exactly_one_function():
    """The single-writer half of the citability invariant, as a check rather than prose.

    `.rulesync/rules/CLAUDE.md` now states it ("Citability has ONE writer:
    `Store.verify_evidence`"), and a mechanism stated in prose goes stale silently --
    this repo's most repeated review finding. So it is swept: in `core/vault.py`, the
    only function that hands `VERIFIED_KEY` to the frontmatter WRITER is
    `verify_evidence`. Every other reference to the constant must be a read
    (`fm.get(VERIFIED_KEY)`).

    SCOPE, stated honestly. This is keyed on the CONSTANT and on `_set_fm` by name, so
    a hypothetical second store, or code that assembled the key from a raw `"verified"`
    literal, is outside it -- the conformance suite's
    `test_propose_alone_is_never_citable` and
    `test_a_caller_cannot_supply_the_citability_key_by_any_route` are what bind a store
    BEHAVIOURALLY. What this adds is the thing behaviour cannot: a second write site
    appearing in this file gets named at the moment it appears, rather than after
    someone notices the invariant is now wrong.
    """
    import ast
    import pathlib

    tree = ast.parse((pathlib.Path(__file__).resolve().parents[1] / "sluice" / "core"
                      / "vault.py").read_text(encoding="utf-8"))
    writers, readers = set(), set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call)
                    and any(isinstance(a, ast.Name) and a.id == "VERIFIED_KEY"
                            for a in node.args)):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            (readers if name == "get" else writers).add(fn.name)

    assert readers, ("the sweep found no VERIFIED_KEY read at all -- the matcher is "
                     "broken, not the vault; the writer assertion below would then be "
                     "certifying nothing")
    assert writers == {"verify_evidence"}, (
        f"VERIFIED_KEY reaches a non-read callee in {sorted(writers)}; citability has "
        f"exactly one writer, and a second one is a new trust root, not a convenience")


def test_read_by_composer_names_exactly_the_kinds_the_cv_engine_reads():
    """#164's mechanism, retargeted at the flag it actually answers. Whichever kinds
    `cv/engine.py` reads must be exactly the ones flagged `read_by_composer`.

    The scope assertion is load-bearing, not decoration: a matcher that found NOTHING
    would leave the equality below comparing two empty sets, green forever -- this repo's
    own `all([])` trap, one layer up.
    """
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[1] / "sluice" / "cv" / "engine.py"
           ).read_text(encoding="utf-8")
    reached = set(re.findall(r"""read_evidence\(\s*["']([a-z]+)["']""", src))
    assert reached, ("the sweep found no evidence read in sluice/cv/engine.py -- the "
                     "matcher is broken, not the engine; without this the equality below "
                     "would compare two empty sets and pass vacuously")
    assert reached == {k for k, spec in EVIDENCE_KINDS.items() if spec.read_by_composer}, (
        f"cv/engine.py reads {sorted(reached)}, but EVIDENCE_KINDS flags "
        f"{sorted(k for k, s in EVIDENCE_KINDS.items() if s.read_by_composer)} as "
        f"read_by_composer")


def test_cited_by_gate_is_exactly_what_bundle_sources_actually_licenses():
    """#164 derived this by grepping the engine, on the assumption that a corpus the engine
    READS is a corpus the gate CITES. #165 broke that assumption on purpose: skills reach
    the composer's prompt and are licensed nowhere.

    So derive it by EXECUTION instead. Give each read kind a distinct sentinel digit, build
    a real bundle, and ask `bundle_sources` which sentinels it licensed. A source grep
    cannot answer this -- citability is decided by `bundle_sources`, which walks
    `bundle["entries"]` and knows nothing about kinds -- and this oracle cannot go stale,
    because it IS the mechanism.
    """
    from sluice.cv import bundle as B

    sentinels = {"experience": "8801", "skills": "8802"}
    assert set(sentinels) == {k for k, s in EVIDENCE_KINDS.items() if s.read_by_composer}, (
        "a read_by_composer kind has no sentinel here, so it sits outside this comparison "
        "entirely and the equality below cannot see it")
    b = B.build_bundle(
        [{"title": "t", "company": "Example Co", "best_for": "", "category": "",
          "metrics": sentinels["experience"], "body": ""}],
        "baseline", [], [], {},
        skills=[{"title": "s", "best_for": "", "body": "",
                 "fields": {"Domain": "", "Proficiency": sentinels["skills"],
                            "Evidence": "", "Signal Value": ""}}])
    sources = B.bundle_sources(b)
    licensed = set().union(*sources.nums.values(), sources.baseline)
    assert sentinels["experience"] in licensed, (
        "the experience sentinel was not licensed -- the fixture is wrong, and the "
        "equality below would pass for the wrong reason")
    assert {k for k, d in sentinels.items() if d in licensed} \
        == {k for k, s in EVIDENCE_KINDS.items() if s.cited_by_gate}


_EIGHT = {"path", "title", "company", "category", "best_for", "metrics", "verified", "body"}


def _seed(root, kind, name, inner, body="Body text.", inbox=False):
    base = os.path.join(str(root), *EVIDENCE_KINDS[kind].relpath.split("/"))
    if inbox:
        base = os.path.join(base, "_inbox")
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\n{inner}\n---\n{body}\n")


def test_an_unknown_kind_raises_and_lists_the_valid_names(tmp_path):
    """Not a quiet []. `read_evidence`'s contract records the harm of an
    empty evidence read: the bundle has no ids, every WORK bullet violates the gate,
    and the user is told `skipped-gate` -- a fabrication verdict against their
    composer -- after paying for a dossier fetch and a full compose."""
    v = Vault(str(tmp_path))
    with pytest.raises(ValueError, match="experience"):
        v.read_evidence("skils")


def test_read_evidence_returns_the_eight_key_floor_plus_fields_for_every_kind(tmp_path):
    """Three of skills' four user fields map to none of the eight legacy keys (`Domain`
    is the exception, routed onto `best_for` by `EvidenceKind.floor_map`), so pinning the
    return to those eight alone would write four fields per skill and read back zero."""
    v = Vault(str(tmp_path))
    _seed(tmp_path, "skills", "alpha",
          "Proficiency: P\nDomain: D\nEvidence: E\nSignal Value: S\nverified: 2026-01-01")
    entry = v.read_evidence("skills")[0]
    assert _EIGHT <= set(entry), "the eight-key floor is missing"
    assert entry["fields"] == {"Proficiency": "P", "Domain": "D",
                               "Evidence": "E", "Signal Value": "S"}


def test_a_skills_entrys_domain_reaches_the_ranker_the_cv_bundle_actually_uses(tmp_path):
    """#164 review, M3 -- measured, then closed.

    The eight-key floor was an identity mapping on title-cased names, and `skills`' four
    fields collide with NONE of them. So `best_for` and `category` were the empty string
    for every skill, and `cv/bundle.py`'s `rank()` -- which scores on
    `best_for`/`category`/`title` -- gave a skills entry in domain `platform` a score of
    ZERO against the JD keyword `platform`. That is rework #165 walks straight into.

    Driven through the REAL `rank()`, not through an assertion on the entry dict: the
    dict shape is the mechanism, and pinning the mechanism instead of the outcome is how
    a floor key that is populated but read under a different name would still pass.
    The non-matching entry is seeded FIRST (`aaa` sorts before `zzz`) so a `rank` that
    scored everything zero would leave it in front -- without that, stable sorting would
    hand back the right order for the wrong reason.
    """
    from sluice.cv.bundle import rank

    v = Vault(str(tmp_path))
    _seed(tmp_path, "skills", "aaa", "Domain: frontend\nverified: 2026-01-01")
    _seed(tmp_path, "skills", "zzz", "Domain: platform\nverified: 2026-01-01")
    entries = v.read_evidence("skills")
    assert [e["title"] for e in entries] == ["aaa", "zzz"], "precondition: seeded order"

    assert [e["title"] for e in rank(entries, ["platform"])] == ["zzz", "aaa"]


def test_the_floor_map_leaves_company_unfilled_for_a_kind_that_has_no_employer(tmp_path):
    """The deliberate NON-mapping, pinned so it is not "fixed" later by someone reading
    M3 as "map everything". `company` is rendered to the composer as `(<company>)`, so
    filling it with a skill's `Domain` would put a technology in the slot labelled
    employer -- fabrication pressure aimed at the gate that exists to prevent it. A
    companyless entry takes `cv/bundle.py`'s documented `XX` prefix fallback and is
    still uniquely sequenced, which is that fallback working rather than a gap.
    """
    from sluice.cv.bundle import assign_codes

    v = Vault(str(tmp_path))
    _seed(tmp_path, "skills", "alpha", "Domain: platform\nverified: 2026-01-01")
    _seed(tmp_path, "skills", "beta", "Domain: frontend\nverified: 2026-01-01")
    entries = v.read_evidence("skills")
    assert [e["company"] for e in entries] == ["", ""], \
        "a skills field leaked into the slot the composer reads as an employer"
    assert [e["id"] for e in assign_codes(entries, {})] == ["XX1", "XX2"]


def test_verified_only_filters_and_an_inbox_entry_is_invisible_at_both_settings(tmp_path):
    """`_inbox/` is hidden by the FLAT listing, not by a by-name exclusion -- adding
    one beside the existing `.endswith('.md')` check would be an equivalent mutant.
    This test is what would go red if the reader ever became recursive without a
    _PRIVATE_SUBDIRS-style prune."""
    v = Vault(str(tmp_path))
    _seed(tmp_path, "experience", "kept", "Company: Alpha\nverified: 2026-01-01")
    _seed(tmp_path, "experience", "draft", "Company: Beta")
    _seed(tmp_path, "experience", "pending", "Company: Gamma", inbox=True)
    assert {e["title"] for e in v.read_evidence("experience", verified_only=True)} == {"kept"}
    assert {e["title"] for e in v.read_evidence("experience", verified_only=False)} == \
        {"kept", "draft"}
    assert {e["title"] for e in v.read_pending_evidence("experience")} == {"pending"}


def test_an_absent_store_reads_empty_and_is_not_an_error(tmp_path):
    assert Vault(str(tmp_path)).read_evidence("stories") == []
    assert Vault(str(tmp_path)).read_pending_evidence("stories") == []


def test_a_name_that_does_not_reduce_to_a_filename_component_is_refused(tmp_path):
    """The slug is computed FIRST and its SHAPE asserted, rather than joining the raw
    name and checking containment afterwards. Ordering those the other way makes the
    containment check unfirable (no slug contains a separator), which is an equivalent
    mutant; this assertion goes red the moment the slugifier stops reducing."""
    v = Vault(str(tmp_path))
    # NB: "../escape" is deliberately NOT in this tuple. Under the exact reduction
    # below it reduces to the valid slug "escape" -- separators collapse to a single
    # "-" which then strips away, leaving real content behind. That is not a bug: it
    # is the traversal-survives-slugging case the NEXT test exercises on purpose
    # (name="../../escaped"). This tuple is only the "reduces to nothing usable" set.
    for bad in ("..", "", "   ", "///"):
        with pytest.raises(ValueError, match="filename component"):
            v.propose_evidence("skills", name=bad, fields={})


def test_a_traversal_name_that_survives_slugging_still_lands_inside_the_inbox(tmp_path):
    v = Vault(str(tmp_path))
    path = v.propose_evidence("skills", name="../../escaped", fields={})
    inbox = os.path.realpath(v._evidence_dir("skills", inbox=True))
    assert os.path.dirname(os.path.realpath(path)) == inbox


def test_slug_safe_pattern_is_pinned_so_a_widening_cannot_silently_arm_the_basename_guard():
    """`evidence_slug`'s `os.path.basename(slug) != slug` half is INERT under the CURRENT
    character class: `_SLUG_SAFE`'s alphabet (`[a-z0-9-]`) can never produce a `/`, a `\\`,
    a `:` or a `.`, so for every string `_SLUG_SAFE` matches, `os.path.basename` already
    returns that same string back -- under posixpath OR ntpath, checked below rather than
    just read off the class. A comment alone cannot enforce that a FUTURE widening of the
    character class gets noticed, so this pins the pattern textually: a change here should
    force whoever makes it to re-read `evidence_slug`'s docstring and confirm the basename
    guard still catches whatever the new class now admits, rather than the widening
    shipping while the guard is silently still believed load-bearing (or still believed
    inert, if the widening actually closes the gap)."""
    import ntpath
    import posixpath

    from sluice.core.vault import _SLUG_SAFE
    assert _SLUG_SAFE.pattern == r"\A[a-z0-9][a-z0-9-]*\Z", (
        "_SLUG_SAFE's pattern changed -- re-read evidence_slug's docstring and confirm "
        "the 'os.path.basename(slug) != slug' guard still does something: as pinned, no "
        "match can contain a path separator or a dot, so that guard is currently INERT, "
        "and this change may be exactly what arms it for the first time")
    for separator in ("/", "\\", ":", "."):
        assert not _SLUG_SAFE.match(f"a{separator}b"), (
            f"_SLUG_SAFE now admits {separator!r} -- evidence_slug's basename guard may "
            f"just have become load-bearing; re-read its docstring before shipping this")
    # Ground truth for the docstring's "already equals its own basename" claim, not
    # merely read off the character class.
    for slug in ("a", "a-b", "abc123", "a" * 80):
        assert _SLUG_SAFE.match(slug)
        assert posixpath.basename(slug) == slug
        assert ntpath.basename(slug) == slug


def test_a_symlinked_inbox_is_refused_rather_than_resolved(tmp_path):
    """os.path.realpath on the inbox would make a symlink AT _inbox/ structurally
    invisible: `_inbox -> ..` puts every proposal straight into the citable directory.
    core/vault.py already refuses a symlinked lead write folder for the mirror reason."""
    v = Vault(str(tmp_path))
    inbox = v._evidence_dir("skills", inbox=True)
    os.makedirs(os.path.dirname(inbox), exist_ok=True)
    os.symlink(os.path.dirname(inbox), inbox)
    with pytest.raises(OSError, match="refusing to write through it"):
        v.propose_evidence("skills", name="alpha", fields={})


def test_a_dangling_symlinked_inbox_witnesses_the_islink_before_makedirs_order(tmp_path):
    """The test above does not, on its own, pin the ORDER of the islink check relative
    to makedirs: os.makedirs(path, exist_ok=True) is a no-op when `path` is a symlink to
    a real, EXISTING directory (which is what that test symlinks to), so moving the
    islink check to AFTER makedirs would still raise there afterwards and leave that test
    green -- the order is correct in the code but nothing holds it there.

    A DANGLING symlink (target does not exist) discriminates the two orderings.
    os.path.islink is True either way -- it inspects the entry itself via lstat, never
    the target -- but os.makedirs on a dangling symlink raises a BARE FileExistsError
    ("File exists") if it runs FIRST: mkdir fails because the entry already exists, and
    exist_ok's rescue check (`os.path.isdir`) follows the symlink to a target that isn't
    there, so it does not treat this as the harmless already-a-directory case. That is
    the SAME exception TYPE (OSError) our own guard raises, with a DIFFERENT message --
    this repo's own rule ("a guard raising the SAME exception type as the path it
    precedes cannot be witnessed by asserting the TYPE") is why this asserts the
    MESSAGE, not merely OSError.

    The match string is NOT the bare word "symlink", even though that is what our own
    guard's message contains: pytest derives `tmp_path` from the TEST's OWN function
    name, and this test (and the one above) are named with "symlink[ed]" in them, so
    the bare FileExistsError's message -- which embeds the full failing PATH, which
    embeds tmp_path's name -- spuriously contains "symlink" even when the guard never
    ran. Measured: match("symlink", ".../pytest-N/test_a_dangling_symlinked_inbo0/.../_inbox'") is True on the BUGGY ordering alone, which would have made this test pass
    for the wrong reason. "refusing to write through it" is long and distinctive enough
    to appear only in our own guard's real message, never in a bare OSError or a path."""
    v = Vault(str(tmp_path))
    inbox = v._evidence_dir("skills", inbox=True)
    os.makedirs(os.path.dirname(inbox), exist_ok=True)
    os.symlink(os.path.join(os.path.dirname(inbox), "does-not-exist"), inbox)
    with pytest.raises(OSError, match="refusing to write through it"):
        v.propose_evidence("skills", name="alpha", fields={})


def test_a_symlinked_inbox_is_refused_on_the_read_and_verify_paths_too(tmp_path):
    """#164 whole-branch review, H1 -- reproduced end to end, then closed.

    The islink refusal used to live in `propose_evidence`'s BODY, so it bound the write
    path and nothing else. Measured against exactly this fixture: propose refused,
    `read_pending_evidence` listed the foreign directory's entries anyway (`['alpha']`),
    `verify_evidence` returned True, and its `os.unlink(src)` DELETED a file outside the
    vault -- a store operation destroying a file the user never told it about.

    The victim assertion is the load-bearing one. A test that only asserted the three
    refusals would stay green against an implementation that refused the read but
    unlinked first, and this bug's whole harm is the deletion, not the listing.
    """
    v = Vault(str(tmp_path))
    outside = os.path.join(str(tmp_path), "outside-the-vault")
    os.makedirs(outside)
    victim = os.path.join(outside, "alpha.md")
    with open(victim, "w", encoding="utf-8") as fh:
        fh.write("---\nProficiency: P\n---\nnot the vault's file\n")
    inbox = v._evidence_dir("skills", inbox=True)
    os.makedirs(os.path.dirname(inbox), exist_ok=True)
    os.symlink(outside, inbox)

    with pytest.raises(OSError, match="refusing to write through it"):
        v.propose_evidence("skills", name="alpha", fields={})
    with pytest.raises(OSError, match="refusing to write through it"):
        v.read_pending_evidence("skills")
    with pytest.raises(OSError, match="refusing to write through it"):
        v.verify_evidence("skills", "alpha", today="2026-08-22", reviewed="")
    assert os.path.exists(victim), "a file outside the vault was deleted"


def test_a_symlinked_kind_directory_is_refused_on_every_path(tmp_path):
    """A leaf-only probe walks straight past this shape: with the KIND directory
    symlinked, `_inbox` is an ordinary subdirectory of a foreign tree, so
    `os.path.islink` on the inbox is False while every read and write still lands
    outside the vault. `_evidence_dir` walks every component below the vault, so this
    is the test that goes red if that walk is narrowed back to the leaf.

    All four entry points are exercised -- the citable read, the pending read, propose
    and verify -- because each resolves the directory through its own
    `_evidence_dir(...)` call and a guard reached by only some of them is the exact
    asymmetry the test above records.
    """
    v = Vault(str(tmp_path))
    outside = os.path.join(str(tmp_path), "outside-the-vault")
    os.makedirs(os.path.join(outside, "_inbox"))
    kind_dir = v._evidence_dir("skills")
    os.makedirs(os.path.dirname(kind_dir), exist_ok=True)
    os.symlink(outside, kind_dir)
    assert not os.path.islink(os.path.join(kind_dir, "_inbox")), \
        "precondition: the INBOX is not itself a link -- a leaf-only guard sees nothing here"

    with pytest.raises(OSError, match="refusing to write through it"):
        v.read_evidence("skills")
    with pytest.raises(OSError, match="refusing to write through it"):
        v.read_pending_evidence("skills")
    with pytest.raises(OSError, match="refusing to write through it"):
        v.propose_evidence("skills", name="alpha", fields={})
    with pytest.raises(OSError, match="refusing to write through it"):
        v.verify_evidence("skills", "alpha", today="2026-08-22", reviewed="")


def test_a_symlinked_ancestor_directory_is_refused_on_every_path(tmp_path):
    """The level BEYOND the two this guard used to name, and the one that proves the
    walk is a walk rather than a longer list.

    Every kind's relpath begins `Job Applications/`, and that first component was
    unchecked: with `vault/Job Applications -> <outside>`, BOTH the kind directory and
    `_inbox` are ordinary directories of a foreign tree, so a check naming `_inbox` and
    the kind directory sees no link anywhere. Measured against exactly this fixture with
    the two-name guard restored: `read_pending_evidence` listed `['alpha']`,
    `verify_evidence` returned True, and its `os.unlink` deleted the victim outside the
    vault.

    The victim assertion is the load-bearing one, for the same reason it is in the
    `_inbox` test above: a test asserting only the refusals stays green against an
    implementation that refuses the read but unlinks first, and the deletion is the harm.

    `os.path.join` rather than `_evidence_dir`, because that resolver is the thing under
    test here and calling it to BUILD the fixture would make the test depend on the
    behaviour it is checking.
    """
    v = Vault(str(tmp_path))
    outside = os.path.join(str(tmp_path), "outside-the-vault")
    inbox = os.path.join(outside, "Skills Inventory", "_inbox")
    os.makedirs(inbox)
    victim = os.path.join(inbox, "alpha.md")
    body = "---\nProficiency: P\n---\nnot the vault's file\n"
    with open(victim, "w", encoding="utf-8") as fh:
        fh.write(body)
    ancestor = os.path.join(str(tmp_path), "Job Applications")
    os.symlink(outside, ancestor)
    assert not os.path.islink(os.path.join(ancestor, "Skills Inventory")), \
        "precondition: the KIND directory is not itself a link"
    assert not os.path.islink(os.path.join(ancestor, "Skills Inventory", "_inbox")), \
        "precondition: the INBOX is not itself a link either"

    with pytest.raises(OSError, match="refusing to write through it"):
        v.read_evidence("skills")
    with pytest.raises(OSError, match="refusing to write through it"):
        v.read_pending_evidence("skills")
    with pytest.raises(OSError, match="refusing to write through it"):
        v.propose_evidence("skills", name="alpha", fields={})
    with pytest.raises(OSError, match="refusing to write through it"):
        v.verify_evidence("skills", "alpha", today="2026-08-22", reviewed=body)
    assert os.path.exists(victim), "a file outside the vault was deleted"


def test_the_refusal_names_the_outermost_symlink_not_an_inner_one(tmp_path):
    """Outermost-first is a behaviour, not a comment -- but it takes TWO links to
    witness, which is the whole reason this fixture looks the way it does.

    A single symlinked ancestor does NOT discriminate the two orders: every path below
    it is a real directory, so an inward-out walk finds no link until it reaches the
    ancestor and names it anyway. Measured -- with the loop's direction reversed and
    nothing else changed, the whole suite stayed green. So this nests a second link
    (`_inbox` inside the foreign tree) BELOW the symlinked ancestor: outermost-first
    names the ancestor, innermost-first names `_inbox`, and only one of those is a path
    the user can act on, since the message's one instruction is "move the real folder
    into the vault" and moving the inner one changes nothing.
    """
    v = Vault(str(tmp_path))
    outside = os.path.join(str(tmp_path), "outside-the-vault")
    inbox_target = os.path.join(str(tmp_path), "outside-again")
    os.makedirs(os.path.join(outside, "Skills Inventory"))
    os.makedirs(inbox_target)
    inner = os.path.join(outside, "Skills Inventory", "_inbox")
    os.symlink(inbox_target, inner)
    ancestor = os.path.join(str(tmp_path), "Job Applications")
    os.symlink(outside, ancestor)

    with pytest.raises(OSError) as excinfo:
        v.read_pending_evidence("skills")
    message = str(excinfo.value)
    assert f"{ancestor!r} is a symlink" in message, \
        f"the refusal did not name the OUTERMOST link: {message}"
    assert "_inbox" not in message, \
        f"the refusal named an inner link the user cannot act on: {message}"


def test_a_symlinked_entry_file_is_refused_rather_than_promoted(tmp_path):
    """The DIRECTORY guard says nothing about the entries inside a real directory, and
    the harm here is the mirror image of the directory case: content INJECTION, not
    deletion.

    Measured with the entry-file refusal removed and everything else real: an
    `_inbox/alpha.md` symlinked to a file outside the vault was listed, read through by
    `read_pending_evidence_text`, promoted by `verify_evidence` (True) into the citable
    directory carrying the foreign file's body -- and the `os.unlink` removed only the
    LINK, so the foreign file survived. That entry is then citable by the hard
    fabrication gate.

    The citable-copy assertion is the load-bearing one: a test asserting only the three
    refusals would stay green against an implementation that refused the reads and still
    promoted.
    """
    v = Vault(str(tmp_path))
    outside = os.path.join(str(tmp_path), "outside-the-vault")
    os.makedirs(outside)
    foreign = os.path.join(outside, "someone-elses.md")
    body = "---\nProficiency: P\n---\ncontent that was never in the vault\n"
    with open(foreign, "w", encoding="utf-8") as fh:
        fh.write(body)
    inbox = v._evidence_dir("skills", inbox=True)
    os.makedirs(inbox)
    os.symlink(foreign, os.path.join(inbox, "alpha.md"))

    with pytest.raises(OSError, match="refusing to read an entry from behind it"):
        v.read_pending_evidence("skills")
    with pytest.raises(OSError, match="refusing to read an entry from behind it"):
        v.read_pending_evidence_text("skills", "alpha")
    with pytest.raises(OSError, match="refusing to read an entry from behind it"):
        v.verify_evidence("skills", "alpha", today="2026-08-22", reviewed=body)
    assert not os.path.exists(os.path.join(v._evidence_dir("skills"), "alpha.md")), \
        "foreign content was promoted into the citable set"
    assert os.path.exists(foreign), "the link's target was disturbed"


def test_a_symlinked_entry_file_is_refused_in_the_citable_directory_too(tmp_path):
    """The citable read needs its own row, and it is not the same bug one level over: no
    promotion is involved at all. A symlinked entry sitting in the KIND directory is read
    by `read_evidence` -- so `cv/bundle.py` gets a citable id whose numbers come from a
    file outside the vault, and the hard fabrication gate certifies them.

    `read_evidence` and `read_pending_evidence` share `_evidence_entries`, which is why
    the guard lives there; this is the test that goes red if it is narrowed to the inbox.
    """
    v = Vault(str(tmp_path))
    outside = os.path.join(str(tmp_path), "outside-the-vault")
    os.makedirs(outside)
    foreign = os.path.join(outside, "someone-elses.md")
    with open(foreign, "w", encoding="utf-8") as fh:
        fh.write("---\nProficiency: P\nverified: 2026-08-22\n---\nfrom outside\n")
    citable = v._evidence_dir("skills")
    os.makedirs(citable)
    os.symlink(foreign, os.path.join(citable, "alpha.md"))

    with pytest.raises(OSError, match="refusing to read an entry from behind it"):
        v.read_evidence("skills")


def test_an_unknown_field_key_is_refused_by_name(tmp_path):
    """The round-trip CANNOT catch this: it compares value fidelity, and
    {'verified': ...} round-trips equal to itself."""
    v = Vault(str(tmp_path))
    with pytest.raises(ValueError, match="verified"):
        v.propose_evidence("skills", name="alpha", fields={"verified": "2099-01-01"})


def test_a_newline_inside_a_field_value_cannot_smuggle_a_key(tmp_path):
    """_parse_fm_spaced rebuilds keys line-by-line, so a value carrying a newline
    creates a NEW key. Measured: 'Proficiency: Expert\\nverified: 2099-01-01' parses
    to keys ['Domain', 'Proficiency', 'verified']. The whole-note round-trip is what
    catches it; a key allow-list alone does not."""
    v = Vault(str(tmp_path))
    with pytest.raises(ValueError, match="round-trip"):
        v.propose_evidence("skills", name="alpha",
                           fields={"Proficiency": "Expert\nverified: 2099-01-01"})


def test_a_body_opening_with_its_own_fence_cannot_become_the_frontmatter(tmp_path):
    """_FM_RE is \\A-anchored, so whatever fence the FILE starts with is frontmatter.
    The writer always emits its own leading fence -- even when `fields` is empty, which
    is reachable because `stories` has two optional fields -- so the non-greedy match
    takes the real block."""
    v = Vault(str(tmp_path))
    path = v.propose_evidence("stories", name="alpha", fields={},
                              body="---\nverified: 2099-01-01\n---\nreal body")
    entry = v.read_pending_evidence("stories")[0]
    assert entry["verified"] is None, "a hostile body reached the frontmatter"
    assert path.endswith(os.path.join("_inbox", "alpha.md"))


def test_a_body_line_shaped_like_a_citation_code_is_refused(tmp_path):
    """Written when `cv/validate.py` recovered ids by parsing the rendered bundle text
    (`nums[cur] = ...`, an ASSIGNMENT), so such a line REBOUND another entry's permitted
    numbers and a fabricated figure cleared the hard gate. #174 has since deleted that
    parse entirely -- the gate is handed its source set structurally now, so there is
    nothing left to rebind that way. The guard stays for the smaller residual #174's
    design accepts instead; see core/vault.py's `_refuse_citation_shaped_body`."""
    v = Vault(str(tmp_path))
    with pytest.raises(ValueError, match="citation code"):
        v.propose_evidence("experience", name="alpha", fields={},
                           body="[AL1] delivered 4200 units")


def test_verify_refuses_a_hand_placed_body_line_shaped_like_a_citation_code(tmp_path):
    """#164 review, M1 -- reproduced, then closed.

    The guard used to live only in `_render_evidence_note`, i.e. only on the `propose`
    path. An entry a human dropped into `_inbox/` themselves never goes through propose,
    and hand-editing the vault is a first-class workflow here -- so measured (at the
    time), an entry whose body was `[NC1] delivered 987 things` verified True and landed
    CITABLE, and `cv/validate.py`'s then-parser (`nums[cur] = set(...)`) rebound NC1's
    permitted numbers in the bundle the hard gate read. #174 has since deleted that
    parser; the guard's current reason is narrower -- see core/vault.py's
    `_refuse_citation_shaped_body`.

    Asserting the citable set is empty afterwards is the load-bearing half: a refusal
    raised after the destination write would satisfy `pytest.raises` and still have made
    the entry citable.
    """
    v = Vault(str(tmp_path))
    _seed(tmp_path, "experience", "alpha", "Company: Alpha",
          body="[NC1] delivered 987 things", inbox=True)
    reviewed = v.read_pending_evidence_text("experience", "alpha")

    with pytest.raises(ValueError, match="citation code"):
        v.verify_evidence("experience", "alpha", today="2026-08-22", reviewed=reviewed)
    assert v.read_evidence("experience", verified_only=False) == [], \
        "a citation-shaped body reached the citable set"
    assert len(v.read_pending_evidence("experience")) == 1, "the refusal ate the entry"


def test_the_citation_shaped_body_guard_is_one_function_reached_by_both_writes(tmp_path):
    """Closing a gap class for ONE instance does not close it for the identical instance
    beside it -- this repo's own standing lesson, and M1 was exactly that shape. Pins
    that the two write paths share ONE guard rather than two copies that can drift: the
    same input is refused with the same message on both."""
    v = Vault(str(tmp_path))
    body = "[NC1] delivered 987 things"
    with pytest.raises(ValueError, match="citation code") as proposed:
        v.propose_evidence("experience", name="alpha", fields={}, body=body)
    _seed(tmp_path, "experience", "beta", "Company: Alpha", body=body, inbox=True)
    with pytest.raises(ValueError, match="citation code") as verified:
        v.verify_evidence("experience", "beta", today="2026-08-22",
                          reviewed=v.read_pending_evidence_text("experience", "beta"))
    assert str(proposed.value) == str(verified.value)


def test_propose_never_stamps_verified_and_lands_only_in_the_inbox(tmp_path):
    v = Vault(str(tmp_path))
    v.propose_evidence("skills", name="alpha", fields={"Proficiency": "P"})
    assert v.read_evidence("skills", verified_only=False) == []
    pending = v.read_pending_evidence("skills")
    assert len(pending) == 1 and pending[0]["verified"] is None


def test_proposing_onto_a_taken_inbox_name_refuses_rather_than_overwrites(tmp_path):
    v = Vault(str(tmp_path))
    v.propose_evidence("skills", name="alpha", fields={"Proficiency": "First"})
    with pytest.raises(FileExistsError):
        v.propose_evidence("skills", name="alpha", fields={"Proficiency": "Second"})
    assert v.read_pending_evidence("skills")[0]["fields"]["Proficiency"] == "First"


def test_proposing_onto_a_name_already_taken_in_the_verified_set_refuses(tmp_path):
    """#164 review, H2b. `propose_evidence` used to probe the INBOX alone, so `add
    alpha` after `alpha` had been verified succeeded -- and the clash then surfaced from
    inside an interactive `verify`, as the promotion's exclusive create raising a bare
    `[Errno 17] File exists`. Refusing at propose time puts the refusal where the user is
    typing the name and can pick another one.

    The message is asserted, not merely the type: a bare `pytest.raises(FileExistsError)`
    would pass identically against the INBOX clash beside it, which is a different
    situation needing a different answer, and the CLI prints this message verbatim."""
    v = Vault(str(tmp_path))
    _seed(tmp_path, "skills", "alpha", "Proficiency: Existing\nverified: 2026-01-01")
    with pytest.raises(FileExistsError, match="already named 'alpha'"):
        v.propose_evidence("skills", name="alpha", fields={"Proficiency": "New"})
    assert v.read_pending_evidence("skills") == [], "a refused proposal still wrote"


def test_a_taken_inbox_name_refuses_with_a_named_message_not_an_errno(tmp_path):
    """The other half of the pair, and the reason both messages exist: a caller (the CLI
    handler, the `init` wizard) prints these verbatim, and `[Errno 17] File exists:
    <path>` names neither the entry nor anything to do about it. The two clashes must
    also be TELLABLE APART -- the answer to one is "pick another name", the answer to the
    other is "you already proposed this"."""
    v = Vault(str(tmp_path))
    v.propose_evidence("skills", name="alpha", fields={"Proficiency": "First"})
    with pytest.raises(FileExistsError, match="already proposed") as caught:
        v.propose_evidence("skills", name="alpha", fields={"Proficiency": "Second"})
    assert "Errno" not in str(caught.value), "the raw errno reached the caller"


def test_a_non_directory_inbox_is_not_reported_as_a_name_clash(tmp_path):
    """The THIRD way this method can raise FileExistsError, and the only one that is not
    a name clash: `os.makedirs(inbox, exist_ok=True)` raises it when something that is
    not a directory already occupies the inbox path -- `exist_ok=True` suppresses the
    error only for an existing DIRECTORY.

    Two things made that worth separating rather than leaving as an accepted errno.
    The contract says both name-clash refusals carry a message a caller may print
    verbatim, NEVER a bare errno, and this arm leaked exactly that (`[Errno 17] File
    exists: <absolute path>`) from the same method. And a caller cannot tell the arms
    apart by TYPE, so `mcpserver.propose_evidence` -- which reports FileExistsError as
    a recoverable `refused` outcome meaning "the name is taken" -- reported a broken
    vault as a name clash, whose documented recovery is to pick another name. That
    recovery can never succeed, so an agent renames forever.

    NotADirectoryError, deliberately: still an OSError, so every existing
    `except OSError` handler around this call keeps working, but NOT a FileExistsError,
    so no caller keyed on that type can mistake it for a clash again."""
    v = Vault(str(tmp_path))
    inbox = v._evidence_dir("skills", inbox=True)
    os.makedirs(os.path.dirname(inbox), exist_ok=True)
    with open(inbox, "w", encoding="utf-8") as fh:
        fh.write("not a directory\n")

    with pytest.raises(NotADirectoryError) as caught:
        v.propose_evidence("skills", name="alpha", fields={"Proficiency": "First"})
    assert not isinstance(caught.value, FileExistsError), (
        "a broken vault still raises the type callers read as 'that name is taken'")
    assert "Errno" not in str(caught.value), "the raw errno reached the caller"
    assert "_inbox" in str(caught.value), (
        "the message does not say WHICH path is not a directory, so it names nothing "
        "a human could go and fix")


def test_id_shaped_matches_every_generated_code():
    """`vault._ID_SHAPED` is a deliberate LOCAL COPY of the bundle citation shape -- core/
    must not import cv/, so it cannot be the same object -- and this pins it against the
    thing that DEFINES that shape.

    It used to pin textual equality with `cv.validate._ID_RE`, the regex the gate used to
    parse ids out of the rendered bundle. #174 deleted that regex: the gate is handed its
    ids structurally now, so there is no counterpart pattern left to compare against.

    The source of truth moved to the GENERATOR, and pinning against generated output is
    strictly stronger than the regex-to-regex equality it replaces: it fails if
    `_prefix`/`assign_codes` change the shape they emit for ANY reason, not only if
    somebody edits a regex. The direction that matters is unchanged -- this guard must
    match at least every code the generator can produce, or an authored body line could
    carry a token the bundle will later treat as a real entry's code.

    The company names below are deliberately awkward (punctuation, digits, one-letter,
    non-ASCII, empty) because `_prefix` coerces ALL of them to exactly two A-Z letters --
    that coercion is what makes a single pattern sufficient, and it is what would break
    silently if the generator ever stopped coercing.

    Witnessed against five mutations of `_ID_SHAPED`: a three-letter prefix, a one-letter
    prefix, widening to accept lowercase, and making the sequence number optional all go
    RED here. Dropping the leading `^` does NOT, and that is an equivalent mutant rather
    than a hole: every caller uses `.match()`, which anchors at position 0 on its own, so
    the `^` is decorative for this pattern. Recorded so a later mutation round reads that
    survivor as expected rather than as evidence this test is inert.
    """
    from sluice.core.vault import _ID_SHAPED
    from sluice.cv.bundle import assign_codes

    companies = ["Example Alpha", "example beta", "7 Digits Ltd", "Ω-Only", "X", "",
                 "Punctuation!!! Co", "Example Alpha"]
    coded = assign_codes([{"company": c} for c in companies], {})
    assert len(coded) == len(companies), "assign_codes dropped an entry"
    for e in coded:
        assert _ID_SHAPED.match(f"[{e['id']}] delivered 4200 units"), e["id"]

    # ...and it must NOT match the near-misses, or the guard refuses bodies it should
    # accept and a legitimate entry becomes unwritable.
    for s in ("[al1] lowercase does not count", "[ABC1] three-letter prefix does not count",
              "[A1] one-letter prefix does not count", "[AL] no sequence number",
              "no code here", " [AL1] leading space is not line-initial"):
        assert not _ID_SHAPED.match(s), s


def _pending_text(v, kind, slug):
    with open(v._evidence_dir(kind, inbox=True) + os.sep + f"{slug}.md", encoding="utf-8") as fh:
        return fh.read()


def test_verify_promotes_exactly_one_entry_and_stamps_it(tmp_path):
    v = Vault(str(tmp_path))
    v.propose_evidence("skills", name="alpha", fields={"Proficiency": "P"})
    v.propose_evidence("skills", name="beta", fields={"Proficiency": "Q"})
    assert v.verify_evidence("skills", "alpha", today="2026-08-22",
                             reviewed=_pending_text(v, "skills", "alpha")) is True
    promoted = v.read_evidence("skills", verified_only=True)
    assert [e["title"] for e in promoted] == ["alpha"]
    assert promoted[0]["verified"] == "2026-08-22"
    assert [e["title"] for e in v.read_pending_evidence("skills")] == ["beta"]


def test_verify_abstains_when_the_entry_changed_after_review(tmp_path):
    """Compare-and-set: a human approved specific bytes, and promoting an edit made
    after that approval would put unreviewed content into the citable set."""
    v = Vault(str(tmp_path))
    v.propose_evidence("skills", name="alpha", fields={"Proficiency": "P"})
    assert v.verify_evidence("skills", "alpha", today="2026-08-22",
                             reviewed="something the human never saw") is False
    assert v.read_evidence("skills", verified_only=False) == []
    assert len(v.read_pending_evidence("skills")) == 1


def test_verify_refuses_a_taken_verified_name_without_mutating_the_pending_entry(tmp_path):
    """The refusal lands at the exclusive create, BEFORE the source is touched -- so a
    routine name clash cannot leave a stamped entry stranded in the inbox.

    BOTH entries are seeded by hand rather than through `propose_evidence`, because
    `propose_evidence` now refuses a name already taken in the citable set (#164 review,
    H2b) -- which is the point of that fix, and would make this fixture unreachable
    through the `add` path. The state itself is still perfectly reachable: a human
    dropping a file into `_inbox/` is a first-class workflow here, and so is losing the
    race between two `verify` runs. `verify_evidence`'s own exclusive create is what
    holds the property under both, which is exactly what this row exercises."""
    v = Vault(str(tmp_path))
    _seed(tmp_path, "skills", "alpha", "Proficiency: Existing\nverified: 2026-01-01")
    _seed(tmp_path, "skills", "alpha", "Proficiency: New", inbox=True)
    before = _pending_text(v, "skills", "alpha")
    with pytest.raises(FileExistsError):
        v.verify_evidence("skills", "alpha", today="2026-08-22", reviewed=before)
    assert _pending_text(v, "skills", "alpha") == before
    assert v.read_evidence("skills")[0]["fields"]["Proficiency"] == "Existing"


def test_a_source_edited_between_the_create_and_the_unlink_is_kept_not_destroyed(tmp_path):
    """Step 5's conditional unlink is a DATA-LOSS guard: deleting the condition
    silently destroys a human's post-approval edit. The residual is a duplicate, never
    a loss -- which is what separates this from the os.link+os.unlink shape
    _reserve_and_move's docstring records as rejected on #23."""
    v = Vault(str(tmp_path))
    v.propose_evidence("skills", name="alpha", fields={"Proficiency": "P"})
    reviewed = _pending_text(v, "skills", "alpha")
    real_write = _mod._write

    def _edit_after_create(path, text, *, exclusive=False):
        real_write(path, text, exclusive=exclusive)
        if not path.endswith(os.path.join("_inbox", "alpha.md")):
            with open(v._evidence_dir("skills", inbox=True) + os.sep + "alpha.md",
                      "w", encoding="utf-8") as fh:
                fh.write(reviewed + "\nan edit the human made after approving\n")

    _mod._write = _edit_after_create
    try:
        assert v.verify_evidence("skills", "alpha", today="2026-08-22",
                                 reviewed=reviewed) is True
    finally:
        _mod._write = real_write
    assert len(v.read_evidence("skills", verified_only=True)) == 1
    # Asserting on the COUNT alone proves a file is still in the inbox, not that the edited
    # BYTES survived -- an implementation that unlinked src and then rewrote `current` back
    # would satisfy a bare count check while destroying exactly what this test is named for.
    # Assert on the CONTENT first; the count is a secondary, weaker check beside it.
    assert _pending_text(v, "skills", "alpha").endswith(
        "an edit the human made after approving\n"), "the human's edit was destroyed"
    assert len(v.read_pending_evidence("skills")) == 1, "the human's edit was destroyed"


def test_verifying_an_absent_entry_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Vault(str(tmp_path)).verify_evidence("skills", "nope", today="2026-08-22",
                                             reviewed="")


def test_a_hand_added_entry_whose_name_is_not_a_slug_is_verifiable_under_that_name(tmp_path):
    """The #164 whole-branch review's IMPORTANT 2, reproduced end to end at the store.

    Hand-editing the vault is a first-class workflow for this tool, so an entry a human
    drops into `_inbox/` themselves is a real user path, not a corner case. Before the
    fix, `verify_evidence` re-reduced the name it was given: `read_pending_evidence`
    reported this entry's title as `My Entry` (its real basename), `evidence_slug` turned
    that back into `my-entry`, and the lookup went to a file that does not exist -- so the
    entry was listed by `... list --pending` and permanently unverifiable behind a raw
    FileNotFoundError.

    Both halves are asserted, because only the second one distinguishes the fix from a
    lookup that merely stopped crashing: the entry is promoted (True), AND the promoted
    copy keeps the human's own basename rather than a slug they never chose.
    """
    v = Vault(str(tmp_path))
    _seed(tmp_path, "skills", "My Entry", "Proficiency: P", inbox=True)
    [entry] = v.read_pending_evidence("skills")
    assert entry["title"] == "My Entry", "precondition: the title IS the on-disk basename"
    with open(entry["path"], encoding="utf-8") as fh:
        reviewed = fh.read()

    assert v.verify_evidence("skills", entry["title"], today="2026-08-22",
                             reviewed=reviewed) is True
    assert [e["title"] for e in v.read_evidence("skills")] == ["My Entry"]
    assert v.read_pending_evidence("skills") == []


def test_verify_refuses_a_name_that_is_not_a_bare_filename_component(tmp_path):
    """Containment survives moving the reduction to CREATE time.

    Until this fix, `verify_evidence` could not escape the inbox as a SIDE EFFECT of
    slugging its argument (`_SLUG_SAFE`'s alphabet cannot express a separator). Looking
    an entry up by its real on-disk name removes that side effect, so the property is
    now asserted directly -- and this is the test that goes red if that assertion is
    deleted.

    Set up so a lookup that DID escape would succeed rather than fail for an unrelated
    reason: a real note sits one level above the inbox and its exact bytes are passed as
    `reviewed`, so an unguarded `../escapee` would pass compare-and-set, stamp it, and
    write the promoted copy at `<kind dir>/../escapee.md` -- outside the kind directory
    entirely. The final assertion pins that nothing landed there.
    """
    v = Vault(str(tmp_path))
    _seed(tmp_path, "skills", "escapee", "Proficiency: P")  # in the CITABLE dir, above _inbox/
    kind_dir = v._evidence_dir("skills")
    with open(os.path.join(kind_dir, "escapee.md"), encoding="utf-8") as fh:
        reviewed = fh.read()

    for outside in ("../escapee", os.path.join("..", "escapee"), "sub/escapee"):
        with pytest.raises(ValueError, match="bare filename component"):
            v.verify_evidence("skills", outside, today="2026-08-22", reviewed=reviewed)
    assert not os.path.exists(os.path.join(os.path.dirname(kind_dir), "escapee.md")), \
        "a promoted copy landed outside the kind directory"


def test_a_source_that_vanishes_after_the_citable_write_is_still_a_promotion(tmp_path,
                                                                            monkeypatch):
    """Round-2 review, L2. Everything past `verify_evidence`'s citable write is CLEANUP:
    the approved bytes are already stamped and already in the citable directory, which is
    exactly what this method returning True means.

    The post-write `_read(src)` was unguarded, so a source that vanished in that window --
    a sync client, a second `verify`, a human tidying `_inbox/` in Obsidian -- raised
    FileNotFoundError out of a promotion that had ALREADY SUCCEEDED. One layer up,
    `Sluice.verify_evidence_interactive`'s per-item `except (OSError, ValueError)` turned
    that into `not promoted: <title> -- it is no longer in the inbox` and exit 1, for an
    entry that IS citable with the bytes the human approved. Wrong in the direction that
    makes the user act: the natural response is to re-add the entry, which then clashes
    with the one already sitting there.

    Driven through the real `_write`, with the vanish happening inside it -- the actual
    window, not a stubbed `_read`. Asserted on the RESULT and on the citable entry's
    CONTENT, so a fix that swallowed the error and returned False, or one that promoted
    the wrong bytes, still fails.
    """
    v = Vault(str(tmp_path))
    v.propose_evidence("skills", name="alpha", fields={"Proficiency": "P"})
    reviewed = _pending_text(v, "skills", "alpha")
    inbox = os.path.join(v._evidence_dir("skills", inbox=True), "alpha.md")
    real_write = _mod._write

    def _write_then_vanish(path, text, *, exclusive=False):
        real_write(path, text, exclusive=exclusive)
        # Only on the CITABLE write, never on the inbox write `propose_evidence` did:
        # this is the window between that write and the source re-read below it.
        if os.path.dirname(path) != os.path.dirname(inbox):
            os.unlink(inbox)

    monkeypatch.setattr(_mod, "_write", _write_then_vanish)
    assert v.verify_evidence("skills", "alpha", today="2026-08-22", reviewed=reviewed) is True

    monkeypatch.undo()
    [entry] = v.read_evidence("skills", verified_only=True)
    assert entry["title"] == "alpha"
    assert entry["verified"] == "2026-08-22"
    assert entry["fields"]["Proficiency"] == "P", "the approved bytes are not what landed"
    assert v.read_pending_evidence("skills") == []


def test_a_vanished_source_is_reported_as_promoted_by_the_facade_too(tmp_path, monkeypatch):
    """The half a user actually reads. The store returning True buys nothing if the
    command still prints `not promoted:` and exits 1, and that whole path -- the per-item
    `except (OSError, ValueError)` and `_evidence_failure_reason`'s FileNotFoundError arm --
    sits one layer up in `sluice/core/app.py`, not in the store.
    """
    from sluice.core.app import Sluice
    from sluice.core.config import Config

    # `stores/vault.py:_make` is env-first, and tests/conftest.py's autouse sandbox sets
    # VAULT_DIR -- so a bare `Config(vault_dir=...)` would build the facade against a
    # DIFFERENT vault, find an empty pending queue and pass this test vacuously.
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    v = Vault(str(tmp_path))
    v.propose_evidence("skills", name="alpha", fields={"Proficiency": "P"})
    inbox = os.path.join(v._evidence_dir("skills", inbox=True), "alpha.md")
    real_write = _mod._write

    def _write_then_vanish(path, text, *, exclusive=False):
        real_write(path, text, exclusive=exclusive)
        if os.path.dirname(path) != os.path.dirname(inbox):
            os.unlink(inbox)

    class _YesAsker:
        interactive = True

        def confirm(self, prompt):
            return True

    monkeypatch.setattr(_mod, "_write", _write_then_vanish)
    report = Sluice(Config(vault_dir=str(tmp_path))).verify_evidence_interactive(
        kind="skills", asker=_YesAsker(), today="2026-08-22")

    assert report["promoted"] == ["alpha"]
    assert report["failed"] == [], \
        "a completed promotion was reported as a failure the user is asked to redo"


def test_a_pending_entry_carrying_the_citability_key_is_still_reported_as_pending(tmp_path):
    """Round-2 review, T3. `read_pending_evidence` states "no verified filter" and nothing
    tested it: replacing its return with a `verified`-filtered list left the suite green.

    Filtering there hides the entry from ALL THREE of this reader's consumers at once --
    `<kind> list --pending`, the queue `verify` offers, and `doctor`'s pending count --
    while it sits in `_inbox/` doing nothing, and is NOT citable, because `read_evidence`
    cannot see `_inbox/` at all. Nowhere left that could report it is precisely the
    silent-inert state the pending count exists to surface.

    The entry is HAND-PLACED, which is how this state is actually reached: hand-editing
    the vault is a first-class workflow here. It is deliberately NOT set up as a crash in
    `verify_evidence`'s stamp/unlink window -- measured with a simulated crash immediately
    after the citable write, the inbox copy survives UNSTAMPED (`verified` is None),
    because the stamp is written to the destination and the source is never touched. That
    window is a real dual-copy state, but it is not a route to this one, and
    `read_pending_evidence`'s docstring used to claim it was.

    All four assertions matter: it is pending, it is NOT citable, its stamp is visible
    (a reader that returned the entry with `verified` blanked would hide the same fact by
    a different route), and doctor's own count agrees.
    """
    v = Vault(str(tmp_path))
    inbox = v._evidence_dir("skills", inbox=True)
    os.makedirs(inbox)
    with open(os.path.join(inbox, "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nProficiency: P\nverified: 2026-01-01\n---\nHand placed.\n")

    pending = v.read_pending_evidence("skills")
    assert [e["title"] for e in pending] == ["alpha"], \
        "a stamped inbox entry vanished from the only readers that could report it"
    assert pending[0]["verified"] == "2026-01-01", \
        "the stamp itself is hidden, which hides the same fact by another route"
    assert v.read_evidence("skills", verified_only=True) == [], \
        "an _inbox/ entry became citable merely by carrying the key"
    assert v.preflight()["skills_pending"] == 1


def test_a_cited_kind_that_is_not_composed_from_is_refused_at_construction():
    """#165 split `cited_by_gate` into two flags, and this is the invariant that keeps the
    pair coherent: the fabrication gate can only license content the composer actually put
    in the bundle. In `__post_init__` rather than only a test, because a registry invariant
    pinned by a test alone is one a kind constructed anywhere else never has to satisfy."""
    with pytest.raises(ValueError, match="cited_by_gate"):
        EvidenceKind("X", ("A",), cited_by_gate=True, read_by_composer=False)


def test_the_registry_flags_are_what_this_change_intends():
    """SCOPE: pins all three kinds, so a kind silently dropped from the registry or a flag
    flipped in either direction reddens here rather than passing vacuously."""
    assert {k: (s.read_by_composer, s.cited_by_gate) for k, s in EVIDENCE_KINDS.items()} \
        == {"experience": (True, True), "skills": (True, False), "stories": (False, False)}
