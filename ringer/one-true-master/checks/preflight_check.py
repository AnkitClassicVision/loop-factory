#!/usr/bin/env python3
"""Gate a lane on named publish-preflight checks actually turning green.

The lane does not get to claim success; the episode's own preflight evaluator
decides. Failure output names each check that is still red and why, so the retry
prompt is useful.

Usage: preflight_check.py EPISODE_DIR check_a,check_b,...
"""
import os
import sys


def main():
    if len(sys.argv) != 3:
        print("FAIL: checker misuse: preflight_check.py EPISODE_DIR checks")
        return 1
    episode_dir, wanted = sys.argv[1], [c.strip() for c in sys.argv[2].split(",") if c.strip()]

    repo = "/mnt/d_drive/repos/podcast"
    if repo not in sys.path:
        sys.path.insert(0, repo)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    try:
        from server.pipeline.publish_preflight import evaluate_episode
    except Exception as exc:  # pragma: no cover - environment problem
        print("FAIL: could not import the preflight evaluator: %r" % exc)
        return 1

    try:
        result = evaluate_episode(episode_dir)
    except Exception as exc:
        print("FAIL: preflight evaluation raised: %r" % exc)
        return 1

    by_name = {c["name"]: c for c in result.get("checks", [])}
    unknown = [c for c in wanted if c not in by_name]
    if unknown:
        print("FAIL: these checks do not exist in the evaluator: %s\navailable: %s"
              % (", ".join(unknown), ", ".join(sorted(by_name))))
        return 1

    red = [c for c in wanted if by_name[c]["status"] != "pass"]
    if red:
        print("FAIL: %d of %d required checks are still not passing:" % (len(red), len(wanted)))
        for name in red:
            row = by_name[name]
            print("  - %-22s %-5s %s" % (name, row["status"], row.get("detail", "")))
        print("\nfull preflight state:")
        for name, row in by_name.items():
            print("  %-22s %s" % (name, row["status"]))
        return 1

    still_failing = [n for n, r in by_name.items() if r["status"] == "fail"]
    print("PASS: %s now passing. Remaining failures elsewhere in preflight: %s"
          % (", ".join(wanted), ", ".join(still_failing) if still_failing else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
