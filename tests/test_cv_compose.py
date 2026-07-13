from sluice.cv import compose as C

class FakeBackend:
    def __init__(self, outputs): self.outputs = list(outputs); self.prompts = []
    def complete(self, prompt): self.prompts.append(prompt); return self.outputs.pop(0)

def test_prompt_contains_bundle_jd_and_forbids_em_dashes():
    p = C.build_prompt("BUNDLE-TEXT", "JD-TEXT", "Acme", "Analyst")
    assert "BUNDLE-TEXT" in p and "JD-TEXT" in p
    assert "Acme" in p and "Analyst" in p
    assert "NO em dashes" in p or "No em dashes" in p
    assert "[id]" in p                       # citation instruction present
    assert "\u2014" not in p                  # the prompt itself models no em dashes

def test_prompt_excludes_material_not_given():
    p = C.build_prompt("BUNDLE", "", "Acme", "Analyst")
    assert "Notion" not in p and "training data" not in p.lower()

def test_retry_prompt_appends_prior_violations():
    p = C.build_prompt("B", "J", "Acme", "Analyst", prior_violations=["UNCITED BULLET: x", "MISSING EMPLOYER: Driftwave"])
    assert "UNCITED BULLET: x" in p and "MISSING EMPLOYER: Driftwave" in p
    assert "FAILED THE GATE" in p

def test_compose_calls_backend_and_returns_text():
    be = FakeBackend(["CV TEXT"])
    out = C.compose(be, "B", "J", "Acme", "Analyst")
    assert out == "CV TEXT"
    assert "B" in be.prompts[0]
