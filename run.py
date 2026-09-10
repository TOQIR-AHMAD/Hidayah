#!/usr/bin/env python
"""Entry point for the Quran video generator.

    python run.py validate
    python run.py preview
    python run.py render
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow "python run.py" to work from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

if sys.version_info < (3, 11):
    sys.stderr.write(
        f"This project needs Python 3.11 or newer; you are running "
        f"{sys.version_info.major}.{sys.version_info.minor}.\n"
        "Install a newer Python from https://www.python.org/downloads/\n"
    )
    raise SystemExit(2)

from app.main import main  # noqa: E402  (import must follow the path setup)

if __name__ == "__main__":
    raise SystemExit(main())
