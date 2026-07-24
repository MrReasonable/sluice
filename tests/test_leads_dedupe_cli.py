"""sluice leads dedupe orchestration: report, targeted merge, conflict/stale refusal,
never-regress, idempotence. Synthetic; locations are Alfa/Bravo placeholders."""
import os

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.leads import Lead
from sluice.core.vault import Vault
from tests.conftest import LOCATIONS


def _app(tmp_path, monkeypatch, **cfg):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    return Sluice(Config(**cfg))


# A clusterable duplicate pair CANNOT be two notes with the same company+title+compatible
# location: upsert MERGES those into one note (same_opportunity UNKNOWN/SAME -> merge). The
# #23 duplicate arises from STRING DRIFT -- two notes whose filenames differ (so upsert
# creates both) but whose normalized tokens match under clustering. So the survivor and its
# drifted re-post differ by a configured title-noise token ("Analyst" vs "Analyst remote",
# with dedupe_title_noise_words=["remote"]); both sit at the SAME location so they cluster
# (a DIFFERENT location would make cluster_duplicates split them -- the opposite failure).
def _seed(v, url, *, title="Analyst", location=LOCATIONS[0], status=None,
          last_seen="2026-07-10", **fm):
    v.upsert(Lead(source="b", search="s", title=title, company="Foo",
                  location=location, url=url, first_seen="2026-07-10", last_seen=last_seen))
    note = next(n for n in v.read_leads() if n.fm.get("url") == url)
    fields = dict(fm)
    if status:
        fields["status"] = status
    if fields:
        v.update_fields(note.ref, fields)
    return note


def _seed_pair(v, *, s1=None, s2=None, ls1="2026-07-10", ls2="2026-07-10"):
    """Two notes for one drifted opportunity: base title vs the same title + a noise token,
    same firm+location -> two distinct notes at upsert that cluster under noise=['remote']."""
    _seed(v, "https://ex.invalid/1", title="Analyst", status=s1, last_seen=ls1)
    _seed(v, "https://ex.invalid/2", title="Analyst remote", status=s2, last_seen=ls2)


def test_report_clusters_drifted_duplicate(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v)
    report = app.dedupe_report()
    assert len(report) == 1 and not report[0].conflict
    assert {n.fm["url"] for n in report[0].members} == {"https://ex.invalid/1", "https://ex.invalid/2"}
    assert report[0].flagged_losers == []   # both `new` (triage) -> no loser flag


def test_merge_then_idempotent(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v)
    cid = app.dedupe_report()[0].id
    assert app.dedupe_merge([cid]) == [(cid, "merged")]
    assert len(v.read_leads()) == 1                 # one survivor
    assert app.dedupe_report() == []                # idempotent: nothing left to cluster


def test_never_regress_survivor_stays_rejected(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v, s1="rejected", ls1="2026-07-05")   # /2 is a fresh `new` re-post
    cid = app.dedupe_report()[0].id
    assert app.dedupe_merge([cid]) == [(cid, "merged")]
    survivors = v.read_leads()
    assert len(survivors) == 1 and survivors[0].status == "rejected"   # not resurrected


def test_first_seen_minimised_across_cluster(tmp_path, monkeypatch):
    # first_seen must aggregate across the WHOLE cluster to the true earliest date, not
    # just the survivor's own -- and a member entirely MISSING first_seen (a legacy/
    # hand-edited note) must not poison that aggregate to "" and hide a genuinely
    # earlier date held by a THIRD member. Three notes so the differentiation is real:
    # under the buggy `min(n.fm.get("first_seen", "") for n in members)`, the missing
    # member's "" is lexicographically smaller than every real date, so the whole min()
    # collapses to "" (falsy) and the survivor's write is skipped -- it would stay at
    # its own later 2026-07-15 instead of the cluster's true 2026-07-01. A 2-member
    # cluster where every member's first_seen is present cannot witness this: min()
    # over present-only values is identical whether or not the empty-default filter
    # exists, so it would pass unchanged on both the buggy and the fixed code.
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    survivor = _seed(v, "https://ex.invalid/1", title="Analyst",
                      first_seen="2026-07-15", last_seen="2026-07-20")
    missing = _seed(v, "https://ex.invalid/2", title="Analyst remote",
                     first_seen="2026-07-12", last_seen="2026-07-10")
    _seed(v, "https://ex.invalid/3", title="remote Analyst",
          first_seen="2026-07-01", last_seen="2026-07-05")
    assert survivor.fm["url"] == "https://ex.invalid/1"

    # Strip first_seen entirely from one member's frontmatter -- simulating a legacy
    # note that predates the field -- the exact shape a bare .get(..., "") default
    # silently loses.
    text = open(missing.ref, encoding="utf-8").read()
    text = "\n".join(line for line in text.split("\n")
                     if not line.strip().startswith("first_seen:"))
    with open(missing.ref, "w", encoding="utf-8") as f:
        f.write(text)

    cid = app.dedupe_report()[0].id
    assert app.dedupe_merge([cid]) == [(cid, "merged")]
    survivors = v.read_leads()
    assert len(survivors) == 1
    assert survivors[0].fm["url"] == "https://ex.invalid/1"      # highest last_seen -> survivor
    assert survivors[0].fm["first_seen"] == "2026-07-01"         # the cluster's TRUE earliest


def test_conflict_cluster_is_refused(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v, s1="rejected", s2="interview")     # terminal + live -> conflict
    c = app.dedupe_report()[0]
    assert c.conflict
    assert app.dedupe_merge([c.id]) == [(c.id, "conflict")]
    assert len(v.read_leads()) == 2                  # nothing merged


def test_stale_id_is_refused(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v)
    assert app.dedupe_merge(["deadbeef"]) == [("deadbeef", "stale")]


def test_partial_archive_reported(tmp_path, monkeypatch):
    # A per-loser archive OSError (e.g. a permissions/ENOSPC blip) must not be reported
    # as a full "merged" -- the survivor's CAS write (routed through os.replace, on the
    # SURVIVOR's own path, not under _merged/) still lands, so the audit trail is
    # unioned, but the loser stays in the active view because it was never archived.
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v)
    cid = app.dedupe_report()[0].id

    import sluice.core.vault as vault_mod
    real_link = vault_mod.os.link

    def flaky_link(src, dst):
        # Fail only the archive-into-_merged/ step; the survivor's own note path is
        # untouched, so its CAS write still succeeds through the real os.replace.
        if os.path.basename(os.path.dirname(dst)) == "_merged":
            raise OSError("simulated: could not archive this loser")
        return real_link(src, dst)

    monkeypatch.setattr(vault_mod.os, "link", flaky_link)
    assert app.dedupe_merge([cid]) == [(cid, "partial")]
    monkeypatch.undo()

    assert len(v.read_leads()) == 2   # survivor unioned, loser still active (not archived)


def test_loser_flag_fires_on_app_owned_loser(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, dedupe_title_noise_words=["remote"])
    v = Vault(str(tmp_path))
    _seed_pair(v, s1="interview", s2="applied", ls1="2026-07-20")
    c = app.dedupe_report()[0]
    assert not c.conflict and c.survivor.fm["url"] == "https://ex.invalid/1"   # interview > applied
    assert [n.fm["url"] for n in c.flagged_losers] == ["https://ex.invalid/2"]  # app-owned loser flagged


def test_cli_report_runs_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    from sluice.cli import _build_parser, cmd_leads_dedupe
    from sluice.core.config import Config
    v = Vault(str(tmp_path))
    _seed_pair(v)
    args = _build_parser().parse_args(["leads", "dedupe"])
    assert cmd_leads_dedupe(args, Config(dedupe_title_noise_words=["remote"])) == 0
    assert "survivor=" in capsys.readouterr().out


def test_cli_merge_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    from sluice.cli import _build_parser, cmd_leads_dedupe
    from sluice.core.config import Config
    v = Vault(str(tmp_path))
    _seed_pair(v)
    cid = Sluice(Config(dedupe_title_noise_words=["remote"])).dedupe_report()[0].id
    args = _build_parser().parse_args(["leads", "dedupe", "--merge", cid])
    assert cmd_leads_dedupe(args, Config(dedupe_title_noise_words=["remote"])) == 0
    assert "merged" in capsys.readouterr().err
