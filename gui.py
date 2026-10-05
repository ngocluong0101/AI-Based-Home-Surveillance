"""Desktop GUI entry point. Same as `python main.py --gui`, and forwards the other options."""

from __future__ import annotations

import sys

from main import main

if __name__ == "__main__":
    if "--gui" not in sys.argv[1:]:
        sys.argv.insert(1, "--gui")
    raise SystemExit(main())
