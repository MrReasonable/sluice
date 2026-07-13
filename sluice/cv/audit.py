"""Advisory LLM fabrication audit. Classifies each CV claim supported | paraphrase |
unsupported against the bundle and returns the flagged lines for the candidate's
sign-off. It NEVER blocks (advisory only). The model call goes through
core/backends, not a hardcoded host path."""


def build_audit_prompt(cv_text, bundle_text):
    return (
        "You are auditing a CV for fabrication. SOURCE BUNDLE is the ONLY truth.\n"
        "For each factual claim in the CV, output one line: <verdict>\t<claim>\t<cited-id-or-NONE>\n"
        "verdict in {supported, paraphrase, unsupported}. 'paraphrase' = same fact, reworded. "
        "'unsupported' = not derivable from the bundle. Do not invent support.\n\n"
        "=== SOURCE BUNDLE ===\n" + bundle_text + "\n\n=== CV ===\n" + cv_text + "\n"
    )


def run_audit(backend, cv_text, bundle_text):
    report = backend.complete(build_audit_prompt(cv_text, bundle_text))
    flagged = [line for line in report.splitlines()
               if line.strip().lower().startswith(("unsupported", "paraphrase"))]
    return report, flagged
