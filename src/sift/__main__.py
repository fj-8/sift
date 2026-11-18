"""Make ``python -m sift`` behave like the installed ``sift`` script.

SPDX-License-Identifier: MIT OR Apache-2.0
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
