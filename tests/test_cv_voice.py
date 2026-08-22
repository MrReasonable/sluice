from sluice.cv import voice as V


class FakeBackend:
    def __init__(self, out): self.out = out; self.prompt = None
    def complete(self, prompt): self.prompt = prompt; return self.out


def test_prompt_frames_the_judgement_as_voice_not_accuracy():
    p = V.build_voice_prompt("CV TEXT")
    assert "VOICE" in p and "CV TEXT" in p
    assert "not its accuracy" in p


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


def test_run_voice_calls_the_injected_backend_not_a_hardcoded_host():
    # The seam: run_voice has no network code of its own, and the backend it calls
    # is whatever was handed in -- cv/engine.py wires this to core/backends.
    backend = FakeBackend("")
    V.run_voice(backend, "CV TEXT")
    assert "CV TEXT" in backend.prompt
