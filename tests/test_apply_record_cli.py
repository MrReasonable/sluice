"""`sluice apply record` at the CLI layer: the --url structural-character guard's drop
must be visible to the human who typed it (#111 follow-up, invariant review of PR
fix/triage-followups-109) -- not just silently absent from the written note."""
from sluice.cli import _build_parser, cmd_apply_record
from sluice.core.config import Config
from sluice.core.vault import Vault


def _lead(tmp_path, fm):
    leads = tmp_path / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    (leads / "Example Northgate - Analyst.md").write_text("---\n" + fm + "\n---\n\nBODY\n")


_SHORTLIST = ('company: "Example Northgate"\nrole: "Analyst"\nstatus: shortlist\n'
              'url: "https://example-northgate.invalid/x"\ntailored_cv: CV_deadbeef.pdf (2026-07-09)')


def test_cmd_apply_record_warns_when_the_url_flag_is_dropped(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    _lead(tmp_path, _SHORTLIST)
    note = Vault(str(tmp_path)).read_leads({"shortlist"})[0]

    args = _build_parser().parse_args(
        ["apply", "record", "--lead", note.slug, "--url", 'https://x/apply"; status: applied'])
    assert cmd_apply_record(args, Config()) == 0
    err = capsys.readouterr().err
    assert "applied_url" in err and "dropped" in err


def test_cmd_apply_record_does_not_warn_for_an_ordinary_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    _lead(tmp_path, _SHORTLIST)
    note = Vault(str(tmp_path)).read_leads({"shortlist"})[0]

    args = _build_parser().parse_args(
        ["apply", "record", "--lead", note.slug, "--url", "https://x/apply"])
    assert cmd_apply_record(args, Config()) == 0
    assert "dropped" not in capsys.readouterr().err


def test_cmd_apply_record_warns_when_the_ats_flag_is_dropped(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    _lead(tmp_path, _SHORTLIST)
    note = Vault(str(tmp_path)).read_leads({"shortlist"})[0]

    args = _build_parser().parse_args(
        ["apply", "record", "--lead", note.slug, "--ats", 'greenhouse"; status: applied'])
    assert cmd_apply_record(args, Config()) == 0
    err = capsys.readouterr().err
    # The success line's own "(ats=(dropped) ...)" already satisfies a bare "ats" +
    # "dropped" substring check on its own -- assert on the warning block's own
    # wording instead, so deleting that block (round-2 review finding) fails this test.
    assert "ats dropped: the ATS name was unsafe for frontmatter" in err


def test_cmd_apply_record_prints_a_distinct_message_on_raced(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    _lead(tmp_path, _SHORTLIST)
    v = Vault(str(tmp_path))
    note = v.read_leads({"shortlist"})[0]   # STALE snapshot, captured before the "concurrent" write
    v.update_fields(note.ref, {"status": "applied"})   # a "concurrent" writer wins first

    # `cmd_apply_record` resolves its own note fresh, via `select.resolve`, which is
    # itself a `read_leads({"shortlist"})` filter -- by the time that filter runs here,
    # it would never find this lead (it already reads "applied" on disk), short-
    # circuiting as "no_match" before ever reaching record()'s require_status guard.
    # Monkeypatch that resolve seam to hand back the STALE note directly instead --
    # the same shape a genuinely concurrent second reader would race through -- so the
    # real `record()` call still runs against the real (already-mutated) file and its
    # require_status re-check still fires for real. test_apply_record.py already
    # proves the CAS mechanism itself directly; this one only proves the CLI's message.
    monkeypatch.setattr("sluice.apply.select.resolve", lambda vault, slug: [note])

    args = _build_parser().parse_args(["apply", "record", "--lead", note.slug])
    assert cmd_apply_record(args, Config()) == 1
    err = capsys.readouterr().err
    assert "status=raced" not in err   # the old generic wording would have been misleading
    assert "race" in err
