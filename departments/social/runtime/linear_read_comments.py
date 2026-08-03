#!/usr/bin/env python3
"""Read comments from a Linear issue by identifier (e.g. ANK-288).

Prints a JSON list of comment objects with body, createdAt, and user fields.
Used by outbox_listen as the reader command.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _get_key() -> str:
    env_key = os.environ.get("LINEAR_API_KEY")
    if env_key:
        return env_key.strip()
    cmd = [
        "aws", "secretsmanager", "get-secret-value",
        "--profile", os.environ.get("AWS_PROFILE", "deploy-admin"),
        "--region", os.environ.get("AWS_REGION", "us-east-1"),
        "--secret-id", os.environ.get(
            "LINEAR_SECRET_ID", "mybcat/integrations/api-keys/linear"
        ),
        "--query", "SecretString",
        "--output", "text",
    ]
    env = os.environ.copy()
    env["HOME"] = "/home/ankit114"
    return subprocess.check_output(cmd, env=env, text=True).strip()


def _gql(key: str, query: str, variables=None):
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Content-Type": "application/json", "Authorization": key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    if data.get("errors"):
        raise RuntimeError(
            "; ".join(e.get("message", str(e)) for e in data["errors"])
        )
    return data["data"]


def read_fixture(path: Path, issue: str) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get(issue, [])
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("comments fixture must be a list or issue-to-list mapping")
    return value


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--shadow", action="store_true",
        help="deny network access; return an empty list unless --fixture is supplied",
    )
    args = parser.parse_args()

    if args.fixture:
        comments = read_fixture(args.fixture, args.issue)
    elif args.shadow:
        comments = []
    else:
        key = _get_key()
        data = _gql(
            key,
            """query($id: String!) {
                issue(id: $id) {
                    comments { nodes { body createdAt user { name } } }
                }
            }""",
            {"id": args.issue},
        )
        comments = data["issue"]["comments"]["nodes"]
    rendered = json.dumps(comments, indent=2) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
