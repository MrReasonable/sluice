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

def test_unsupported_claims_is_the_unsupported_subset():
    # The sign-off gate (#60) blocks on `unsupported` ONLY. `paraphrase` (same fact
    # reworded) is legitimate tailoring -- blocking on it would fire on nearly every
    # CV and train rubber-stamping -- so it stays advisory. This filter is the sole
    # thing given a consequence; run_audit and CvResult.audit_flags are unchanged.
    _, flagged = A.run_audit(FakeBackend(
        "supported\tled team\tSF3\nparaphrase\tgrew it\tSF3\n"
        "unsupported\tMotivated by placeholder\tNONE"), "CV", "BUNDLE")
    assert A.unsupported_claims(flagged) == ["unsupported\tMotivated by placeholder\tNONE"]

def test_unsupported_claims_empty_when_no_unsupported():
    assert A.unsupported_claims([]) == []
    assert A.unsupported_claims(["paraphrase\tgrew it\tSF3"]) == []
