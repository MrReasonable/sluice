"""The catalogue is pure data. Its load-bearing property is that a BLANK answer is a SKIP for every
preference question -- a wizard that fills a gate because someone fumbled a prompt is 672ad2a with
a friendly face.

The two roster tests at the bottom sweep `tests/onboard_prose.py`, which reads all three of
`questions`, `plan` and `ask` -- so they could only land once Task 8 added the last of those. They
are what makes the claim "no shipped surface names an exemplar" true of the WHOLE package rather
than of the catalogue alone, and each was witnessed against a separately planted offender before
being trusted.
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


def test_no_rendered_artefact_names_an_exemplar():
    """THE load-bearing arm. Sweeps the BYTES the user receives -- the written config and the
    written Judging Profile -- rather than the constants that feed them.

    The previous roster read module-level constants only, so an exemplar planted in
    `plan._render_profile`'s inline preamble (text that lands in a stranger's vault and reaches the
    judge as authoritative criteria) left the FULL SUITE green. Three reviewers found that
    independently. A whole-artefact sweep cannot go stale as literals move in and out of function
    bodies, which is what makes this the arm to trust."""
    from tests.onboard_prose import rendered_artefacts, terminal_transcript
    for label, text in list(rendered_artefacts()) + list(terminal_transcript()):
        assert not expresses_a_preference(text), f"{label} names an exemplar"


def test_the_terminal_transcript_covers_the_prompts_it_claims_to():
    """SCOPE for the terminal arm. A transcript that captured nothing would satisfy the sweep
    above, and the prompts are printed BEFORE the read, so a blank answer still emits them."""
    from tests.onboard_prose import terminal_transcript
    text = dict(terminal_transcript())["terminal:asker transcript"]
    assert "Where is your Obsidian vault?" in text          # a catalogue prompt
    assert "blank = skip" in text                           # a TtyAsker bracket line
    assert "boards" in text                                 # ask_ids
    assert "search label" in text                           # the per-source walk
    assert "$EDITOR" in text                                # ask_prose


def test_the_rendered_sweep_covers_something():
    """SCOPE. `rendered_artefacts` strips `_DEFAULT_CRITERIA`'s prose from the profile (it has its
    own guard in triage), and a strip that removed everything would leave the sweep above passing
    over an empty string."""
    from tests.onboard_prose import rendered_artefacts
    surfaces = dict(rendered_artefacts())
    assert len(surfaces) == 2
    for label, text in surfaces.items():
        assert text.strip(), f"{label} swept nothing"
    # The walked arm of _render_sources, not just its commented-example arm.
    assert "example_source" in surfaces["rendered:config_text"]


def test_no_shipped_prose_names_an_exemplar():
    """Sweeps EVERY surface this package puts in front of a user or into their files -- not just
    the catalogue. Round 1 flagged that `_HEADER` and `_SECTION_BLURB` land in every user's config
    and were covered by nothing; the first fix corrected the MATCHING and left the SCOPE alone,
    which is the same enumeration failure one round later."""
    from tests.onboard_prose import shipped_prose
    surfaces = shipped_prose()
    assert len(surfaces) >= 20                 # SCOPE: a sweep over nothing passes
    for label, text in surfaces:
        assert not expresses_a_preference(text), f"{label} names an exemplar"


def test_the_prose_roster_covers_every_declared_constant():
    """A new module-level constant must be either swept or NAMED as not-prose. Without this the
    roster is an enumeration, and this repo's enumerations have leaked four times."""
    from tests.onboard_prose import _NOT_PROSE, _declared_string_constants, shipped_prose
    declared = _declared_string_constants()
    assert declared, "the constant sweep found nothing"
    swept = {lbl.split("[")[0].split(".")[-1] for lbl, _ in shipped_prose()}
    swept |= {"catalogue"}
    for module, name in sorted(declared):
        if (module, name) in _NOT_PROSE:
            continue
        assert name in swept or name.lstrip("_") in swept, \
            f"{module}.{name} is neither swept as prose nor named in _NOT_PROSE"


def test_every_value_bearing_question_states_its_consequence():
    """A question whose answer changes what the pipeline DOES must say so in the post-write report.

    `cv_employers` was the sole exception, and it was also the one whose hint described the
    opposite of its mechanism -- `cv/validate.py` runs a case-sensitive COMPLETENESS check, so a
    lower-case answer makes every `cv run` skip every lead. Silent, permanent, and the report never
    mentioned the key at all. Exempted keys are named, not pattern-matched, so a new question
    cannot join them by accident."""
    # These three configure the tool rather than gating leads: the vault is a location, and the
    # provider names are reported by `sluice doctor`, not by a lead-level consequence.
    exempt = {"vault_dir", "primary_backend", "fallback_backend", "renderer", "cv_name",
              "cv_contact"}
    missing = [q.key for q in catalogue(default_vault=VAULT)
               if q.key not in exempt and not q.consequence]
    assert not missing, f"these answers change behaviour but the report never says so: {missing}"


def test_the_employers_hint_describes_the_check_that_actually_runs():
    """Pins the hint against `cv/validate.py`'s real behaviour, so the two cannot drift apart
    again. The check is COMPLETENESS and case-SENSITIVE; probed here rather than asserted."""
    from sluice.cv.validate import validate
    cv = "WORK EXPERIENCE\nPROFILE\nExample Alpha Ltd did a thing."
    assert any("MISSING EMPLOYER" in v for v in validate(cv, "", employers=["example alpha ltd"]))
    assert not any("MISSING EMPLOYER" in v for v in validate(cv, "", employers=["Example Alpha Ltd"]))
    hint = {q.key: q for q in catalogue(default_vault=VAULT)}["cv_employers"].hint
    assert "VERBATIM" in hint and "case" in hint.lower()


def test_catalogue_keys_are_unique():
    keys = [q.key for q in catalogue(default_vault=VAULT)]
    assert len(keys) == len(set(keys))
