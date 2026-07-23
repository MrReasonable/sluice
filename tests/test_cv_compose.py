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

def test_prompt_is_a_tailoring_task_and_forbids_invention():
    # The observed incident's root cause was the profile rule "lead with what
    # {company} values", which points the profile at the JD (not a permitted source).
    # These are WORDING assertions: they pin that the anti-fabrication instructions
    # are present, not that fabrication cannot occur.
    p = C.build_prompt("BUNDLE-TEXT", "JD-TEXT", "Acme", "Analyst")
    assert "lead with what" not in p                    # the JD-pull is gone
    assert "TAILOR, NOT TO WRITE" in p                  # the task frame
    assert "an invented match is a failure" in p        # the JD-gap omit rule
    assert "Introduce nothing not in the bundle" in p   # hardened profile framing
    assert "—" not in p                 # still no em dash (matches the existing guard)


def test_cv_prompt_expresses_no_role_or_culture_preference():
    # neu-001: the triage guard test_shipped_prompt_expresses_no_role_or_culture_
    # preference (tests/test_prompt.py) covers only the TRIAGE prompt, not this CV
    # _RULES. Mirror it here so the hardened CV prompt cannot grow an opinion about
    # which jobs are good. Tokens are chosen to avoid the prompt's own vocabulary
    # (it names slop words to ban them); reconcile with the triage guard's token
    # list if that changes.
    p = C.build_prompt("BUNDLE", "", "Acme", "Analyst").lower()
    for token in ("startup", "enterprise", "faang", "remote-first", "fast-paced",
                  "unicorn", "rockstar", "ninja", "equity", "salary", "well-funded"):
        assert token not in p, token
