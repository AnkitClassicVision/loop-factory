#!/usr/bin/env python3
"""Executed check: run the podcast pipeline's real content QA on a posts file.

Exit 0 only when the LLM voice judge PASSES. Prints every problem it raised so
a retry prompt carries the actual critique rather than a bare failure.

Usage:
  content_qa_check.py --content posts.json --episode <episode_dir> [--repo PATH]

posts.json shape: {"posts": [{"platform": str, "text": str}, ...]}
"""
import argparse
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", required=True)
    ap.add_argument("--episode", required=True)
    ap.add_argument("--repo", default="/mnt/d_drive/repos/podcast")
    args = ap.parse_args()

    if not os.path.isfile(args.content):
        print(f"FAIL: content file not found: {args.content}")
        return 2
    try:
        payload = json.load(open(args.content))
    except (OSError, ValueError) as exc:
        print(f"FAIL: content file is not readable JSON: {exc}")
        return 2

    posts = payload.get("posts")
    if not isinstance(posts, list) or not posts:
        print("FAIL: no posts in content file")
        return 2
    for i, post in enumerate(posts):
        if not isinstance(post, dict) or not (post.get("text") or "").strip():
            print(f"FAIL: post {i} has no text")
            return 2

    sys.path.insert(0, args.repo)
    try:
        from server.pipeline.content_qa import run_content_qa
    except ImportError as exc:
        print(f"FAIL: cannot import content_qa from {args.repo}: {exc}")
        return 2

    ep_content = os.path.join(args.episode, "content", "episode_content.json")
    try:
        content = json.load(open(ep_content))["content"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"FAIL: cannot read episode content at {ep_content}: {exc}")
        return 2

    normalized = [{"platform": p.get("platform", "?"), "text": p["text"]} for p in posts]
    result = run_content_qa(
        content.get("title", ""),
        content.get("show_notes", ""),
        normalized,
        run_llm=True,
    )

    verdict = result.get("llm_verdict") or {}
    print("CONTENT_QA passed=%s voice_match=%s hook_quality=%s" % (
        result.get("passed"), verdict.get("voice_match"), verdict.get("hook_quality")))

    for key in ("hard_findings", "style_findings"):
        for finding in result.get(key) or []:
            print(f"  {key}: {json.dumps(finding, default=str)[:300]}")
    for problem in verdict.get("problems") or []:
        print(f"  PROBLEM: {problem}")

    if result.get("passed"):
        print("PASS: content QA cleared")
        return 0
    print("FAIL: content QA did not pass; every PROBLEM line above must be fixed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
