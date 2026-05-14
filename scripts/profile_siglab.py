from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from siglab.hardening_profile import build_profile, profile_as_text, strict_failure_count


def main() -> int:
    parser = argparse.ArgumentParser(prog="profile_siglab")
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on critical/high findings.")
    args = parser.parse_args()

    profile = build_profile(Path(args.root))
    if args.json:
        print(json.dumps(profile, indent=2, sort_keys=True, default=str))
    else:
        print(profile_as_text(profile))

    if args.strict:
        return min(strict_failure_count(profile), 125)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
