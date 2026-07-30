#!/usr/bin/env python3
"""Create a clean Linear review card for a completed social draft.

Reads the draft JSON, creates a card assigned to Ankit in Review state,
and writes a ledger entry so the listener can track the decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--team-key", default="ANK")
    parser.add_argument("--assignee-email", default="ankit@mybcat.com")
    args = parser.parse_args()

    draft = json.loads(args.draft.read_text())
    candidate = json.loads(args.candidate.read_text())
    item = candidate.get("item", candidate)
    title = item.get("title", "Unknown episode")
    episode_url = item.get("url", "")
    thumbnail = draft.get("thumbnail_url", "")
    body_text = draft.get("body", "")
    surface = draft.get("surface", "linkedin")

    short_title = title.split(" - ")[0].strip() if " - " in title else title[:50]
    card_title = f"Review: {surface} post - {short_title}"

    card_body = f"APPROVE to queue | SKIP to discard | FIX to request changes\n\n"
    if thumbnail:
        card_body += f"![Episode art]({thumbnail})\n\n"
    card_body += f"---\n\n{body_text}\n\n"
    if episode_url:
        card_body += f"Listen: {episode_url}\n"

    key = _get_key()

    snap = _gql(key, """query($tk:String!) {
        teams(first:10) { nodes { id key } }
        workflowStates(first:50) { nodes { id name team { key } } }
        users(first:20) { nodes { id email } }
    }""", {"tk": args.team_key})

    team = next(t for t in snap["teams"]["nodes"] if t["key"] == args.team_key)
    review_state = next(
        (s for s in snap["workflowStates"]["nodes"]
         if s["name"] == "Review" and s.get("team", {}).get("key") == args.team_key),
        None,
    )
    assignee = next(
        (u for u in snap["users"]["nodes"]
         if u.get("email", "").lower() == args.assignee_email.lower()),
        None,
    )

    create_input = {
        "teamId": team["id"],
        "title": card_title,
        "description": card_body,
    }
    if review_state:
        create_input["stateId"] = review_state["id"]
    if assignee:
        create_input["assigneeId"] = assignee["id"]

    result = _gql(key, """mutation($i:IssueCreateInput!) {
        issueCreate(input:$i) { success issue { id identifier url } }
    }""", {"i": create_input})

    issue = result["issueCreate"]["issue"]

    row_hash = hashlib.sha256(
        f"social-{item.get('item_id','')}-{args.run_id}".encode()
    ).hexdigest()
    ledger_row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "row_hash": row_hash,
        "department": "social",
        "kind": "human_review",
        "card_identifier": issue["identifier"],
        "status": "open",
    }
    with args.ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ledger_row, sort_keys=True) + "\n")

    receipt = {
        "status": "card_created",
        "identifier": issue["identifier"],
        "url": issue["url"],
        "row_hash": row_hash,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    args.out.write_text(json.dumps(receipt, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
