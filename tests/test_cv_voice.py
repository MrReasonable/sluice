from sluice.cv import voice as V


class FakeBackend:
    def __init__(self, out): self.out = out; self.prompt = None
    def complete(self, prompt): self.prompt = prompt; return self.out


def test_prompt_frames_the_judgement_as_voice_not_accuracy():
    p = V.build_voice_prompt("CV TEXT")
    assert "VOICE" in p and "CV TEXT" in p
    assert "not its accuracy" in p


def test_prompt_names_its_input_an_excerpt_and_rules_absence_out_of_scope():
    """cv/engine.py hands this an EXCERPT (the scoped PROFILE/WORK lines), so the text
    really is missing headings, employers and contact details. Every finding rides the
    engine's retry into cv/compose.py under "re-emit the FULL CV" -- so a model that
    answered with an ABSENCE would be instructing the composer to ADD material, which
    is the fabrication pressure the scoping exists to remove."""
    p = V.build_voice_prompt("CV TEXT")
    assert "EXCERPT" in p
    assert "is not a finding" in p


def test_run_voice_returns_the_flagged_lines():
    backend = FakeBackend("flag\tThis reads like a press release.\n")
    report, findings = V.run_voice(backend, "PROFILE\nProse.\n")
    assert findings == ["flag\tThis reads like a press release."]
    assert report == "flag\tThis reads like a press release.\n"


def test_clean_writing_yields_no_findings():
    backend = FakeBackend("")
    _report, findings = V.run_voice(backend, "PROFILE\nProse.\n")
    assert findings == []


def test_a_non_flag_line_is_not_a_finding():
    # The backend replying with commentary that is not shaped as the requested
    # `flag\t...` line must not be mistaken for one.
    backend = FakeBackend("note\tsome other output\n")
    _report, findings = V.run_voice(backend, "CV")
    assert findings == []


def test_a_sentence_that_merely_starts_with_flag_is_not_a_finding():
    # The prompt says "output nothing at all if the writing is clean", and an agentic
    # backend routinely answers that in a SENTENCE. A prefix match counted this as a
    # finding, which spends the one retry and -- under cv.style_hold -- withholds the
    # send-ready pointer from a CV with nothing wrong with it. The false positive landing
    # on the CLEAN case is what makes it worth a named test.
    for reply in ("Flagged nothing: the writing is clean.",
                  "Flagship product experience is well described.",
                  "flags: none"):
        _report, findings = V.run_voice(FakeBackend(reply), "CV")
        assert findings == [], f"{reply!r} was mistaken for a finding"


def test_a_real_tab_delimited_finding_is_still_reported():
    # The other direction: matching the token exactly must not stop the check working.
    # Without this row, rejecting every line passes the test above while making the
    # voice check silently inert.
    backend = FakeBackend("flag\tleveraged synergies\tempty intensifier\n")
    _report, findings = V.run_voice(backend, "CV")
    assert findings == ["flag\tleveraged synergies\tempty intensifier"]


def test_run_voice_calls_the_injected_backend_not_a_hardcoded_host():
    # The seam: run_voice has no network code of its own, and the backend it calls
    # is whatever was handed in -- cv/engine.py wires this to core/backends.
    backend = FakeBackend("")
    V.run_voice(backend, "CV TEXT")
    assert "CV TEXT" in backend.prompt
