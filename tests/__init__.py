"""Test discovery shim: make ``src/sift`` importable without installing.

SPDX-License-Identifier: MIT OR Apache-2.0

``python -m unittest discover -s tests`` imports this package first, so the
path is set up once for every test module. A checkout can therefore be tested
with nothing but the interpreter -- no install step, no build, no dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
