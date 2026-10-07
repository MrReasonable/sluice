import os

import pytest

from sluice.core.vault import Vault


def test_an_unreadable_document_raises(tmp_path):
    v = Vault(str(tmp_path))
    # A directory where the note should be: unreadable for every uid, root included
    # (tests/conftest.py::_cannot_unread_a_dir explains why chmod is not enough).
    os.makedirs(tmp_path / "Job Applications" / "Dir.md")
    with pytest.raises(OSError):
        v.read_document("Job Applications/Dir.md")


def test_undecodable_bytes_raise(tmp_path):
    v = Vault(str(tmp_path))
    (tmp_path / "Job Applications").mkdir()
    (tmp_path / "Job Applications" / "Bad.md").write_bytes(b"\xff\xfe\xfa")
    with pytest.raises(ValueError):
        v.read_document("Job Applications/Bad.md")
