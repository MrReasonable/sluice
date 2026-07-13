import os, tempfile
import pytest
from sluice.cv import render as R

def test_strip_removes_short_codes_only():
    assert R.strip_citations("- Grew team from 3 to 8 [SF3] [TV1]") == "- Grew team from 3 to 8"
    # a bracketed non-citation is left alone
    assert R.strip_citations("- see note [1]") == "- see note [1]"

def test_render_writes_clean_txt_and_invokes_renderer_with_neutral_name():
    calls = {}
    def fake_runner(argv, **kw):
        calls["argv"] = argv; calls["env_home"] = kw["env"]["HOME"]
        open(argv[3], "wb").write(b"%PDF-1.4 fake")  # renderer writes the pdf
        class P: returncode = 0; stderr = ""
        return P()
    out_dir = tempfile.mkdtemp()
    pdf = R.render("- Did it [SF3]\n", out_dir, render_script="/x/cv_render_v2.py",
                   python_bin="/usr/bin/python3", home="/tmp/render-home",
                   neutral_name="CV.pdf", runner=fake_runner)
    assert os.path.basename(pdf) == "CV.pdf"
    assert "[SF3]" not in open(calls["argv"][2]).read()   # clean.txt was stripped
    assert calls["env_home"] == "/tmp/render-home"

def test_render_raises_when_renderer_exits_0_but_writes_no_pdf():
    def fake_runner(argv, **kw):
        class P: returncode = 0; stderr = ""  # exits clean but writes nothing
        return P()
    out_dir = tempfile.mkdtemp()
    with pytest.raises(RuntimeError):
        R.render("- Did it [SF3]\n", out_dir, render_script="/x/cv_render_v2.py",
                 python_bin="/usr/bin/python3", home="/tmp/render-home",
                 neutral_name="CV.pdf", runner=fake_runner)

def test_serve_copies_with_opaque_hashed_name():
    d = tempfile.mkdtemp(); served = tempfile.mkdtemp()
    pdf = os.path.join(d, "CV.pdf"); open(pdf, "wb").write(b"%PDF data")
    name = R.serve(pdf, served)
    assert name.startswith("CV_") and name.endswith(".pdf")
    assert os.path.exists(os.path.join(served, name))
    assert name != "CV.pdf"   # opaque hash, not the plain source filename

def test_serve_respects_served_prefix():
    d = tempfile.mkdtemp(); served = tempfile.mkdtemp()
    pdf = os.path.join(d, "CV.pdf"); open(pdf, "wb").write(b"%PDF data")
    name = R.serve(pdf, served, served_prefix="MyCV")
    assert name.startswith("MyCV_") and name.endswith(".pdf")
