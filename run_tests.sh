#!/usr/bin/env bash
# Offline test suite (core + parsers): no Camofox, no container.
set -euo pipefail
cd "$(dirname "$0")"
.venv/bin/python -m pytest tests -q "$@"
