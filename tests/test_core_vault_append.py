import tempfile, pathlib
from sluice.core.vault import Vault


def _note():
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    p = leads / "Acme - Analyst.md"
    p.write_text('---\ncompany: "Acme"\nstatus: interview\n---\n\nBODY\n')
    return Vault(root), str(p)


def test_append_body_section_adds_once():
    v, p = _note()
    tag = "track-materials-20260710"
    assert v.append_body_section(p, tag, f"## Interview materials <!--{tag}-->\n- https://x/deck") is True
    text = pathlib.Path(p).read_text()
    assert "Interview materials" in text and "https://x/deck" in text
    assert "BODY" in text and 'status: interview' in text  # preserved
    # second call with same tag is a no-op
    assert v.append_body_section(p, tag, "## Interview materials\n- dupe") is False
    assert pathlib.Path(p).read_text().count("Interview materials") == 1
