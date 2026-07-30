#!/usr/bin/env python3
"""Close (archive or transition) a Linear issue by identifier.

Used by outbox_listen as the closer command.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


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


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()

    key = _get_key()

    if args.state in ("approved", "skipped", "fix_requested"):
        _gql(
            key,
            "mutation($id: String!) { issueArchive(id: $id) { success } }",
            {"id": args.issue},
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
