"""`locations` was declared, documented in `sluice.yaml.example`, and read by NOTHING.

`core/config.py`'s own comment called it "a loaded gun rather than a live bug, since the first
consumer to wire it into a search or a gate would have inherited a stranger's 'remote only'".
`sluice init` (#8) would have been that consumer -- a wizard that asks for geography and writes it
into a key nothing reads. So it is retired the way #80 retired `triage.dossier_dir`: loudly, and in
BOTH spellings, because the loader also honoured `$SLUICE_LOCATIONS`.
"""
import pytest

from sluice.core.config import load_config


def test_a_config_that_sets_locations_refuses_and_names_the_replacement(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("locations: [Example Place]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="target_locations"):
        load_config(str(path))


def test_the_env_spelling_refuses_too(tmp_path, monkeypatch):
    """Raising on the file and staying silent on the environment is exactly the asymmetry the
    fail-loudly rule exists to remove: a user who configured geography in their shell would watch
    it quietly stop being read."""
    monkeypatch.setenv("SLUICE_LOCATIONS", "Example Place")
    path = tmp_path / "c.yaml"
    path.write_text("lead_ttl_days: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="target_locations"):
        load_config(str(path))


def test_an_exported_but_EMPTY_env_spelling_refuses_too(tmp_path, monkeypatch):
    """`os.environ.get(...)` is falsy for an exported empty string, so `SLUICE_LOCATIONS=` slipped
    past the refusal -- the one case where a user has demonstrably touched the variable. Presence,
    not truthiness, is the question being asked."""
    monkeypatch.setenv("SLUICE_LOCATIONS", "")
    path = tmp_path / "c.yaml"
    path.write_text("lead_ttl_days: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="target_locations"):
        load_config(str(path))


# Geography placeholder: the repo's synthetic convention, so no real place name appears in tests.
@pytest.mark.parametrize("source", ["file", "env"])
def test_neither_message_echoes_the_value(tmp_path, monkeypatch, source):
    """Geography is personal, and an exception travels further than the file it came from -- logs,
    bug reports, pasted tracebacks. Same ruling as `refuse_retired_dossier_dir` and
    `dossier_allow_hosts`."""
    path = tmp_path / "c.yaml"
    if source == "file":
        path.write_text("locations: [Example Place]\n", encoding="utf-8")
    else:
        path.write_text("lead_ttl_days: 0\n", encoding="utf-8")
        monkeypatch.setenv("SLUICE_LOCATIONS", "Example Place")
    with pytest.raises(ValueError) as exc:
        load_config(str(path))
    assert "Example Place" not in str(exc.value)


def test_a_config_without_it_loads(tmp_path, monkeypatch):
    # Every sibling sets or clears this; inheriting it makes `load_config` raise the very refusal
    # under test, so the pass/fail here would depend on the developer's shell.
    monkeypatch.delenv("SLUICE_LOCATIONS", raising=False)
    path = tmp_path / "c.yaml"
    path.write_text("lead_ttl_days: 0\n", encoding="utf-8")
    assert load_config(str(path)) is not None


def test_no_config_file_at_all_still_loads(monkeypatch):
    monkeypatch.delenv("SLUICE_LOCATIONS", raising=False)
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    """The refusal must not fire on a fresh install, which is the case `sluice init` runs in."""
    assert load_config(None) is not None


def test_the_example_documents_target_locations_and_not_the_retired_key():
    """The wizard routes geography to `triage.target_locations`, the retirement message names it,
    and #8's documentation sweep asserts every key the wizard writes is documented here. Measured
    before this change: `target_locations` had 0 matches in the file."""
    example = open("sluice.yaml.example", encoding="utf-8").read()
    assert "target_locations:" in example
    for line in example.splitlines():
        assert not line.lstrip("# ").strip().startswith("locations:"), \
            "sluice.yaml.example still documents the retired root `locations` key"


def test_the_cli_reports_the_retirement_instead_of_crashing(tmp_path, monkeypatch, capsys):
    """`load_config()` runs inside `main()`, so a stale `$SLUICE_LOCATIONS` reached the user as a
    raw traceback -- and the command it blocked hardest was `sluice init`, the one that would have
    written them a correct config, plus `doctor`, which exists to diagnose exactly this.

    Reproduced before the fix. The message must also name the variable to unset: a refusal that
    does not say how to proceed leaves the user stuck on every command."""
    from sluice.cli import main
    monkeypatch.setenv("SLUICE_LOCATIONS", "Example Place")
    monkeypatch.setenv("SLUICE_CONFIG", str(tmp_path / "c.yaml"))

    rc = main(["doctor", "--offline"])
    err = capsys.readouterr().err

    assert rc == 2, "a retired key is a usage error, not a crash"
    assert "Traceback" not in err
    assert "target_locations" in err and "unset SLUICE_LOCATIONS" in err
    assert "Example Place" not in err, "the refusal must not echo the value"
