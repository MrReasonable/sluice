"""Packaged template data for the `template` renderer.

This file exists so `[tool.setuptools.packages.find]` descends into the directory --
`find` (as opposed to `find_namespace`) will not select a directory without one. The
template BESIDE it ships only because `[tool.setuptools.package-data]` names it;
measured 2026-08-06, this __init__ alone puts the package in the wheel and leaves the
.j2 file out. See tests/test_packaging.py.
"""
