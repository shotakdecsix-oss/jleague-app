"""
[非推奨] validate_round.py に一般化されました。
互換のため残していますが、今後はこちらを直接使ってください:
    python scripts/validate_round.py --league j1 --round 1 [--fixture ...] [--write]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_round import main as _main  # noqa: E402

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "--league", "j1", "--round", "1", *sys.argv[1:]]
    _main()
