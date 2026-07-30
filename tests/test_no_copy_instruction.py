"""No shipped doc may instruct a `cp sluice.yaml.example`. The file ships ACTIVE gates -- measured,
`is_relevant("Senior Software Engineer")` is False against a verbatim copy -- so an instruction to
copy it hands a stranger a closed gate with nothing saying so. `sluice init` exists to replace it."""
import glob
import re


def test_no_shipped_doc_tells_anyone_to_copy_the_example():
    docs = ["README.md", ".rulesync/rules/CLAUDE.md", "docs/ARCHITECTURE.md"]
    docs += glob.glob("docs/*.md")
    checked = 0
    for path in docs:
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        checked += 1
        assert not re.search(r"^\s*cp\b.*sluice\.yaml\.example", text, re.M), \
            f"{path} instructs a copy of the example config"
    assert checked >= 3, "the sweep read nothing"          # SCOPE
