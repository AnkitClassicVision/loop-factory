#!/usr/bin/env python3
"""U11a — the owner's first-draft gate, proven by execution on both sides.

Owner decision, Ankit 2026-08-10: "Hold the first THREE drafts for my eyes, then
revert to the autosend lane. Three letters is enough to see a pattern in the
copy, one is luck."

The requirement he attached to it is the interesting part: the hold must be
counted by EVIDENCE, not by a config toggle someone forgets to flip and not by a
date that quietly passes. So the count comes from the producer's own ledger of
drafts it has created.

This check watches four things, each by running the real code:

  1. Drafts 1, 2 and 3 are recorded hold_for_human and are NOT autosend eligible.
  2. Draft 4 flips: autosend_eligible true, hold_reason gone.
  3. The BRIDGE honours the hold. A held draft id must fail the bridge's own
     autosend validator, because the producer's receipt is worthless if the
     thing that actually sends never reads it.
  4. A corrupt ledger REFUSES rather than resetting the count to zero. That is
     the attack this design exists to survive: lose the ledger, and draft four
     would otherwise autosend as if it were draft one.

Exit 0 PASS, 1 FAIL. Every failure prints what was expected and what happened.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def fail(message: str) -> None:
    print(f"u11a_first_draft_gate_check: FAIL — {message}")
    raise SystemExit(1)


def run_producer(repo: Path, workdir: Path, candidates: list[dict], ordinal: int) -> dict:
    """Run the REAL producer CLI in PLACEHOLDER_MODE and return its receipt."""
    candidates_path = workdir / f"candidates-{ordinal}.json"
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
    receipt_path = workdir / f"receipt-{ordinal}.json"
    truth_path = workdir / "source-truth.json"
    env = dict(os.environ)
    env["PLACEHOLDER_MODE"] = "1"
    done = subprocess.run(
        [sys.executable, "-m", "server.pipeline.guest_outreach_draft",
         "--receipt", str(receipt_path), "--ledger", str(workdir / "ledger.json"),
         "--source-truth", str(truth_path), "--candidates", str(candidates_path)],
        cwd=repo, env=env, capture_output=True, text=True,
    )
    if not receipt_path.is_file():
        fail(f"run {ordinal} wrote no receipt at all; rc={done.returncode} "
             f"stderr={done.stderr[-400:]}")
    return json.loads(receipt_path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    workdir = args.out

    # A fresh, passing source-truth receipt so the freshness gate is not the
    # thing under test here.
    subprocess.run(
        [sys.executable, "scripts/source_truth_revalidate.py",
         "--out", str(workdir / "source-truth.json")],
        cwd=repo, check=True, capture_output=True, text=True,
    )

    def candidate(n: int) -> dict:
        return {
            "alias": f"gate-cand-{n}", "temperature": "warm", "channel": "email",
            "podcast_status": "new_inbound", "email_present": True,
            "cleared_by_human": False, "to": f"gate{n}@example.invalid",
            "subject": "quick question", "body": f"Hey there,\n\nShort note {n}.\n\nAnkit",
        }

    # 1 through 3 must be held; 4 must not.
    held_ids: list[str] = []
    for n in (1, 2, 3):
        receipt = run_producer(repo, workdir, [candidate(n)], n)
        if receipt.get("status") != "drafted":
            fail(f"draft {n} did not draft: status={receipt.get('status')} "
                 f"violation={receipt.get('violation')}")
        if receipt.get("draft_ordinal") != n:
            fail(f"draft {n} recorded ordinal {receipt.get('draft_ordinal')}; the ledger "
                 "is not counting, so the owner's gate is not evidence-backed")
        if receipt.get("hold_for_human") is not True:
            fail(f"draft {n} is NOT held. The owner asked for the first three to reach "
                 f"his eyes; this one would have autosent. receipt={receipt}")
        if receipt.get("autosend_eligible") is not False:
            fail(f"draft {n} reports autosend_eligible={receipt.get('autosend_eligible')}")
        held_ids.append(str(receipt.get("draft_id")))

    fourth = run_producer(repo, workdir, [candidate(4)], 4)
    if fourth.get("status") != "drafted":
        fail(f"draft 4 did not draft: {fourth.get('status')} / {fourth.get('violation')}")
    if fourth.get("hold_for_human") is not False:
        fail("draft 4 is still held. The gate never releases, so the lane Ankit chose "
             f"never resumes. receipt={fourth}")
    if fourth.get("autosend_eligible") is not True:
        fail(f"draft 4 is not autosend eligible: {fourth.get('autosend_eligible')}")
    if fourth.get("hold_reason") is not None:
        fail(f"draft 4 still carries a hold reason: {fourth.get('hold_reason')!r}")

    # 3. The bridge must honour it. A producer receipt nothing reads is decoration.
    sys.path.insert(0, str(repo / "scripts"))
    env_backup = os.environ.get("GUEST_DRAFT_LEDGER")
    os.environ["GUEST_DRAFT_LEDGER"] = str(workdir / "ledger.json")
    try:
        import importlib
        bridge = importlib.import_module("obe_draft_to_linear_bridge")
        importlib.reload(bridge)
        ids = bridge._held_guest_draft_ids()
        for draft_id in held_ids:
            if draft_id not in ids:
                fail(f"the bridge does not see held draft {draft_id!r} as held; it would "
                     f"autosend one of the owner's first three. bridge saw {sorted(ids)}")
        if str(fourth.get("draft_id")) in ids:
            fail("the bridge treats draft 4 as held; the gate never releases")

        # 4. Corrupt the ledger. The count must not reset to zero.
        (workdir / "ledger.json").write_text("{ this is not json", encoding="utf-8")
        if "*" not in bridge._held_guest_draft_ids():
            fail("a CORRUPT ledger did not hold everything. Losing the ledger would let "
                 "an unreviewed email leave under Ankit's name.")
        receipt = run_producer(repo, workdir, [candidate(5)], 5)
        if receipt.get("status") != "error":
            fail("the producer accepted a corrupt ledger and returned "
                 f"{receipt.get('status')!r}; it must refuse, because a reset count "
                 "makes draft 4 look like draft 1")
    finally:
        if env_backup is None:
            os.environ.pop("GUEST_DRAFT_LEDGER", None)
        else:
            os.environ["GUEST_DRAFT_LEDGER"] = env_backup

    print("u11a_first_draft_gate_check: PASS — drafts 1-3 are held for the owner and the "
          "bridge honours the hold, draft 4 releases to the autosend lane, and a corrupt "
          "ledger refuses instead of resetting the count")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
