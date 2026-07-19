from sluice.cli import main


def _cord_line(out):
    return next(l for l in out.splitlines() if l.startswith("cord"))


def test_list_sources_runs(capsys):
    assert main(["ingest", "list-sources"]) == 0
    out = capsys.readouterr().out
    assert "cord" in out
    assert "wttj" in out


def test_disable_then_list_shows_disabled(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLUICE_DISABLED", str(tmp_path / "disabled.json"))
    assert main(["ingest", "disable", "cord"]) == 0
    capsys.readouterr()  # clear
    main(["ingest", "list-sources"])
    assert "disabled" in _cord_line(capsys.readouterr().out)


def test_enable_reverses_disable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLUICE_DISABLED", str(tmp_path / "disabled.json"))
    main(["ingest", "disable", "cord"])
    main(["ingest", "enable", "cord"])
    capsys.readouterr()
    main(["ingest", "list-sources"])
    assert "enabled" in _cord_line(capsys.readouterr().out)


def test_health_command_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLUICE_HEALTH", str(tmp_path / "h.json"))
    assert main(["health"]) == 0
    assert "cord" in capsys.readouterr().out


def test_list_sources_health_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLUICE_HEALTH", str(tmp_path / "h.json"))
    assert main(["ingest", "list-sources", "--health"]) == 0
    assert "baseline=" in capsys.readouterr().out


def test_print_report_surfaces_skipped(capsys):
    from sluice.cli import _print_report

    class _R:
        sources = []
        written = {"created": 1, "updated": 2, "skipped": 3}

    _print_report(_R())
    err = capsys.readouterr().err        # the summary prints to stderr
    assert "3 skipped" in err


def test_print_report_surfaces_merged_and_refused(capsys):
    from sluice.cli import _print_report

    class _R:
        sources = []
        written = {"created": 1, "updated": 0, "merged": 2, "refused": 3, "skipped": 0}

    _print_report(_R())
    err = capsys.readouterr().err
    assert "2 merged" in err and "3 refused" in err
