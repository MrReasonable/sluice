"""The catalogue is pure data. Its load-bearing property is that a BLANK answer is a SKIP for every
preference question -- a wizard that fills a gate because someone fumbled a prompt is 672ad2a with
a friendly face.

Two further tests belong to this file and are NOT here yet: `test_no_shipped_prose_names_an_exemplar`
and `test_the_prose_roster_covers_every_declared_constant`. Both read `tests/onboard_prose.py`, whose
roster sweeps `sluice.onboard.plan` (Task 5) and `sluice.onboard.ask` (Task 8) as well as this
module, so neither can exist before Task 8 lands the last of those three. They arrive with the
roster, together with the four falsifying witnesses that make the sweep trustworthy. Until then this
file pins the catalogue's own properties and the preference helper's matching, and NOTHING here may
be read as evidence that the shipped-prose surfaces are swept -- they are not, yet.
"""
import pytest

from sluice.onboard.questions import (BadAnswer, catalogue, expresses_a_preference, parse_choice,
                                      parse_csv, parse_int, parse_path, parse_url)

VAULT = "./vault"


def test_every_question_except_the_vault_skips_on_blank():
    for q in catalogue(default_vault=VAULT):
        if q.key == "vault_dir":
            continue
        assert q.default is None, f"{q.key} would fill a gate the user did not state"


def test_the_vault_question_takes_the_default_it_was_GIVEN():
    """A parameter, not an import: a pure catalogue must not depend on the concrete store."""
    qs = [q for q in catalogue(default_vault="/example/elsewhere") if q.key == "vault_dir"]
    assert len(qs) == 1 and qs[0].default == "/example/elsewhere"


def test_parse_int_rejects_bool_words_before_parsing_a_number():
    """PyYAML resolves yes/on/true to True and bool subclasses int, so `lead_ttl_days: yes` would
    load as a ONE DAY ttl and mark every lead stale with nothing raising (#75)."""
    for word in ("yes", "no", "on", "off", "true", "false", "YES", "True"):
        # Match the yes/no EXPLANATION, not merely BadAnswer. None of these words parses as an
        # int, so the not-a-number arm raises BadAnswer too and a bare `raises(BadAnswer)` cannot
        # tell the guard from its absence -- witnessed, deleting the guard left that version of
        # this test GREEN. What the guard buys is the message that names the #75 trap, so the
        # message is what has to be asserted for this test to witness anything.
        with pytest.raises(BadAnswer, match="yes/no word"):
            parse_int(word)
    assert parse_int("90") == 90 and parse_int(" 450 ") == 450
    for bad in ("ninety", "-1", ""):
        with pytest.raises(BadAnswer):
            parse_int(bad)


def test_parse_csv_splits_strips_and_drops_empties():
    assert parse_csv("a, b ,,c") == ["a", "b", "c"]
    assert parse_csv("  ") == []


def test_parse_url_requires_http_and_never_resolves_dns():
    assert parse_url("https://example.invalid/jobs") == "https://example.invalid/jobs"
    for bad in ("example.invalid/jobs", "ftp://example.invalid", "file:///etc/passwd", ""):
        with pytest.raises(BadAnswer):
            parse_url(bad)


def test_parse_path_expands_and_absolutises(tmp_path, monkeypatch):
    """A RELATIVE vault_dir is the 'second empty vault beside you' hazard README warns about,
    reintroduced by the wizard itself."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert parse_path("~/notes") == str(tmp_path / "notes")
    assert parse_path("./vault") == str(tmp_path / "vault")


def test_parse_choice_lists_the_valid_names():
    p = parse_choice("script", "weasyprint")
    assert p("weasyprint") == "weasyprint"
    with pytest.raises(BadAnswer, match="script"):
        p("wkhtmltopdf")


def test_the_backend_choices_match_the_registry():
    """Hand-listing a name-keyed registry is a second copy of it: register a fifth backend and the
    wizard silently cannot offer it. Same discovery shape as the fan-out sweep (#63)."""
    from sluice.core.app import Sluice
    q = {x.key: x for x in catalogue(default_vault=VAULT)}["primary_backend"]
    assert set(q.parse.allowed) == set(Sluice.available("backend"))


def test_the_renderer_choices_match_the_registry():
    from sluice.core.app import Sluice
    q = {x.key: x for x in catalogue(default_vault=VAULT)}["renderer"]
    assert set(q.parse.allowed) == set(Sluice.available("renderer"))


# ── the neutrality SMOKE TEST (named honestly; see the helper's docstring) ────
def test_the_preference_helper_rejects_a_synthetic_offender():
    """POSITIVE CONTROL. Without it the sweep below could pass because the helper never fires."""
    assert expresses_a_preference("Most people put a platform role here.")


def test_the_helper_matches_whole_words_only():
    """`senior` must not fire on `seniority` -- a bare substring match failed on the scaffold's own
    prose, and the tempting fix (deleting the word) shrinks the guard instead of the bug."""
    assert not expresses_a_preference("your background and seniority")


def test_catalogue_keys_are_unique():
    keys = [q.key for q in catalogue(default_vault=VAULT)]
    assert len(keys) == len(set(keys))
