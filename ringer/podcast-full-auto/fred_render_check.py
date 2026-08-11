#!/usr/bin/env python3
"""Receipt for Fred's render march: the publishable_render gate must PASS and a
real final/episode.mp4 must exist. Prints WHY on failure."""
import json
import os
import sys

EP = "/mnt/d_drive/repos/podcast/episodes/2026-06-10-fred-cho"


def main() -> int:
    try:
        d = json.load(open(os.path.join(EP, "episode.json")))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL cannot read episode.json: {exc}")
        return 1
    mg = d.get("media_gates") or {}
    pr = mg.get("publishable_render") or {} if isinstance(mg, dict) else {}
    passed = bool(pr.get("passed"))
    failures = pr.get("failures")
    mp4 = os.path.join(EP, "final", "episode.mp4")
    size = os.path.getsize(mp4) if os.path.exists(mp4) else 0
    print(f"stage: {d.get('stage')}")
    print(f"publishable_render.passed: {passed}")
    print(f"publishable_render.failures: {failures}")
    print(f"final/episode.mp4 bytes: {size}")
    ok = passed and size > 1_000_000
    if not ok:
        print("RESULT: FAIL — gate not passed and/or no real final render produced")
    else:
        print("RESULT: PASS — Fred rendered and cleared publishable_render")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
