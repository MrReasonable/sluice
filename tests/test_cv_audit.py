from sluice.cv import audit as A

class FakeBackend:
    def __init__(self, out): self.out = out; self.prompt = None
    def complete(self, prompt): self.prompt = prompt; return self.out

def test_prompt_frames_bundle_as_only_truth():
    p = A.build_audit_prompt("CV", "BUNDLE")
    assert "SOURCE BUNDLE" in p and "CV" in p and "BUNDLE" in p

def test_flagged_are_unsupported_and_paraphrase_lines():
    report = "supported\tled team\tSF3\nparaphrase\tgrew it\tSF3\nunsupported\tran Meridian Trust\tNONE"
    be = FakeBackend(report)
    got_report, flagged = A.run_audit(be, "CV", "BUNDLE")
    assert got_report == report
    assert len(flagged) == 2
    assert any("unsupported" in f for f in flagged)

def test_all_supported_yields_no_flags():
    be = FakeBackend("supported\tled team\tSF3")
    _, flagged = A.run_audit(be, "CV", "BUNDLE")
    assert flagged == []
