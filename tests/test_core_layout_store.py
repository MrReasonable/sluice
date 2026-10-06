"""Vault.read_cv_layout: the vault-specific outcomes, and YAML read through the store."""
import os

import pytest

from sluice.core.protocols import CV_LAYOUT_RELPATH, LayoutError
from sluice.core.vault import Vault
from tests.conftest import layout_yaml


def _vault(tmp_path, text=None, raw=None):
    v = Vault(str(tmp_path))
    if text is not None:
        v.write_document(CV_LAYOUT_RELPATH, text)
    if raw is not None:
        path = os.path.join(str(tmp_path), *CV_LAYOUT_RELPATH.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(raw)
    return v


def test_a_symlinked_note_is_refused_as_unreadable(tmp_path):
    target = tmp_path / "elsewhere.md"
    target.write_text(layout_yaml(), encoding="utf-8")
    path = tmp_path.joinpath(*CV_LAYOUT_RELPATH.split("/"))
    path.parent.mkdir(parents=True)
    path.symlink_to(target)
    with pytest.raises(OSError):
        Vault(str(tmp_path)).read_cv_layout()


def test_a_non_utf8_note_is_unreadable_not_malformed(tmp_path):
    v = _vault(tmp_path, raw=b"---\nroles: \xff\n---\n")
    with pytest.raises(ValueError) as exc:
        v.read_cv_layout()
    assert not isinstance(exc.value, LayoutError)


def test_pyyaml_unavailable_is_a_layout_error_naming_it(tmp_path, monkeypatch):
    import sluice.core.vault as vault_mod
    v = _vault(tmp_path, layout_yaml())
    monkeypatch.setattr(vault_mod, "yaml", None)
    with pytest.raises(LayoutError, match="PyYAML"):
        v.read_cv_layout()


def test_runaway_nesting_is_a_layout_error(tmp_path):
    v = _vault(tmp_path, "---\nroles: " + "[" * 5000 + "]" * 5000 + "\n---\n")
    with pytest.raises(LayoutError):
        v.read_cv_layout()


def test_a_note_without_frontmatter_is_malformed(tmp_path):
    with pytest.raises(LayoutError, match="frontmatter"):
        _vault(tmp_path, "roles: written in the body\n").read_cv_layout()


def test_an_inline_list_splits_an_unquoted_comma_and_a_block_list_does_not(tmp_path):
    # The trap docs/CONFIGURATION.md warns about, measured through the real route.
    flow = "---\nroles:\n  - heading: Example Alpha\n    from: 01/2020\n    to: present\n" \
           "    employers: [Example, Inc]\n---\n"
    assert _vault(tmp_path / "a", flow).read_cv_layout().roles[0].employers == (
        "Example", "Inc")
    block = layout_yaml([{"heading": "Example Alpha", "from": "01/2020", "to": "present",
                          "employers": ["Example, Inc"]}])
    assert _vault(tmp_path / "b", block).read_cv_layout().roles[0].employers == (
        "Example, Inc",)


def test_a_quoted_comma_survives_in_any_role_and_education(tmp_path):
    layout = _vault(tmp_path, layout_yaml(any_role=["Example, Inc"],
                                          education=["Example University, BSc"])
                    ).read_cv_layout()
    assert layout.any_role == ("Example, Inc",)
    assert layout.education == ("Example University, BSc",)


@pytest.mark.parametrize("module", ["sluice.core.layout", "sluice.core.vault"])
def test_each_module_imports_first_in_a_fresh_interpreter(module):
    """Spec 9.5: core/vault.py imports parse_layout, and core/layout.py needs vault's fold.
    Module-scope imports on both sides would be an ImportError at startup, in whichever
    order a fresh process imports them -- which an in-process test, with both already
    loaded, can never see."""
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-c", f"import {module}"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_a_symlinked_parent_directory_is_refused_as_unreadable(tmp_path):
    outside = tmp_path / "outside"
    note = outside / "CV Layout.md"
    note.parent.mkdir(parents=True)
    note.write_text(layout_yaml(), encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / CV_LAYOUT_RELPATH.split("/")[0]).symlink_to(outside)
    with pytest.raises(OSError, match="symlink"):
        Vault(str(vault)).read_cv_layout()


def test_a_directory_at_the_note_path_is_unreadable_not_absent(tmp_path):
    os.makedirs(os.path.join(str(tmp_path), *CV_LAYOUT_RELPATH.split("/")))
    with pytest.raises(IsADirectoryError):
        Vault(str(tmp_path)).read_cv_layout()


def test_a_symlink_refusal_names_the_relative_path_never_the_vault_directory(tmp_path):
    # These messages reach doctor rows and so MCP clients: a vault-relative path names the
    # link just as well and carries no home directory off the machine.
    outside = tmp_path / "outside"
    outside.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    top = CV_LAYOUT_RELPATH.split("/")[0]
    (vault / top).symlink_to(outside)
    with pytest.raises(OSError) as exc:
        Vault(str(vault)).read_cv_layout()
    assert f"'{top}'" in str(exc.value)
    assert str(vault) not in str(exc.value) and str(tmp_path) not in str(exc.value)
