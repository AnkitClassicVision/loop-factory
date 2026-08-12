#!/usr/bin/env python3
"""Emit the single release-bound terminal receipt for the Social daily driver."""
from __future__ import annotations

import argparse
import os
from pathlib import Path



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--dept-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)

    if not os.environ.get("LOOP_FACTORY_RUN_ID"):
        raise SystemExit("LOOP_FACTORY_RUN_ID is required")
    if not args.receipt.is_file():
        raise SystemExit("final social receipt is missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
