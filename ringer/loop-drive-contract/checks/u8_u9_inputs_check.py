#!/usr/bin/env python3
"""U8 + U9 executed check: the two inputs that decide whether outreach happens.

Measured 2026-08-10, and both measurements changed the design:

  * `process/proofs/source_room_authority_manifest.json` has 8 sources, NO hash
    fields, and 2 `location_ref` values that are not single paths (one is a glob
    `process/nodes/*.aac.json`, one is a semicolon-joined list of 5 test files).
    A naive revalidator would report permanent drift on those two, the freshness
    gate would block every candidate forever, and the block would look
    legitimate. That is the silent failure again, one layer up.
  * `episodes/FOCUS-LIST.json` is a WORK QUEUE (kind=production_unstick,
    person_or_episode, reason), not a prospect list. The real pool is
    `episodes/CANDIDATE-INBOX.json` (14 entries carrying email, name, fit_score,
    confidence, source, evidence) joined against `episodes/FUNNEL-LEDGER.json`
    people (stage, last_touch, hold) which carry NO email.

So U9 is a JOIN, and its most consequential output is `temperature`, because a
cold candidate is barred from email by the charter and the channel gate. Getting
that wrong in the permissive direction mails strangers; getting it wrong in the
strict direction produces an honest zero. This check forces the strict direction.

Usage: u8_u9_inputs_check.py --repo <tree> --out <dir> [--u8-only|--u9-only]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FAILURES: list[str] = []


def fail(where: str, why: str, extra: object = None) -> None:
    FAILURES.append(f"CHECK FAIL ({where}): {why}" + (f" [{extra}]" if extra is not None else ""))


def run(repo: Path, argv: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *argv], capture_output=True, text=True, timeout=300, cwd=str(repo),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(repo),
             **(env_extra or {})})


def gate(repo: Path, out: Path, manifest_path: Path, max_age_days: int = 7) -> tuple[int, str]:
    """Run the REAL freshness gate against a receipt. This is the seam that matters."""
    payload = out / "gate-input.json"
    payload.write_text(json.dumps({"manifest_path": str(manifest_path),
                                   "max_age_days": max_age_days}), encoding="utf-8")
    done = run(repo, ["-m", "server.pipeline.prose_gates", "--gate",
                      "source_truth_resolved_before_intake", "--input", str(payload)])
    return done.returncode, done.stdout + done.stderr


# ---------------------------------------------------------------- U8
def check_u8(repo: Path, out: Path) -> None:
    script = repo / "scripts/source_truth_revalidate.py"
    if not script.is_file():
        fail("u8", f"{script} does not exist — the revalidator was not built")
        return

    receipt = out / "source_truth_revalidation.json"
    if receipt.exists():
        receipt.unlink()
    done = run(repo, [str(script), "--out", str(receipt)])
    if not receipt.is_file():
        fail("u8", "the revalidator wrote no receipt. THE ARTIFACT IS THE AUTHORITY: no file "
                   "means failure whatever the exit status said.",
             f"rc={done.returncode} err={done.stderr[-200:]}")
        return
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail("u8", f"the receipt is not valid JSON: {exc}")
        return

    for key in ("schema", "generated_at", "blocking_gaps", "sources_checked"):
        if key not in payload:
            fail("u8", f"the receipt lacks required key {key!r}", list(payload))
    if done.returncode not in (0, 2):
        fail("u8", "exit 0 clean or 2 drift-found; anything else is an error", done.returncode)

    # THE POINT OF THE UNIT: the real gate must accept a fresh receipt.
    rc, output = gate(repo, out, receipt)
    if rc != 0:
        fail("u8", "the REAL freshness gate BLOCKED a receipt written seconds ago. Intake would be "
                   "blocked forever and the block would look legitimate.", output[-300:])

    # And the glob / multi-path refs must not fabricate drift.
    gaps = payload.get("blocking_gaps") or []
    for gap in gaps:
        ref = str(gap.get("location_ref", ""))
        if "*" in ref or ";" in ref:
            fail("u8", "a glob or multi-path location_ref was reported as drift. Those two refs "
                       "exist in the live manifest; treating them as missing blocks intake "
                       "permanently for a formatting reason.", ref)

    # Drift must be DETECTABLE, or the unit proves nothing. Corrupt a real source.
    corrupt = out / "corrupt-tree"
    if corrupt.exists():
        shutil.rmtree(corrupt)
    sources = payload.get("sources_checked") or []
    single = next((s for s in sources
                   if isinstance(s, dict) and "*" not in str(s.get("location_ref", ""))
                   and ";" not in str(s.get("location_ref", ""))
                   and (repo / str(s.get("location_ref"))).is_file()), None)
    if single is None:
        fail("u8", "no single-file source was checked, so drift detection cannot be proven")
        return
    drift_receipt = out / "drift-receipt.json"
    done2 = run(repo, [str(script), "--out", str(drift_receipt),
                       "--simulate-drift", str(single["location_ref"])])
    if not drift_receipt.is_file():
        fail("u8", "no receipt written on the drift path", done2.stderr[-200:])
        return
    drift = json.loads(drift_receipt.read_text(encoding="utf-8"))
    if not drift.get("blocking_gaps"):
        fail("u8", "a corrupted source produced NO blocking gap. The revalidator cannot see drift, "
                   "which makes it decorative.", drift)
    rc2, out2 = gate(repo, out, drift_receipt)
    if rc2 != 2:
        fail("u8", "the gate did not BLOCK a receipt carrying blocking gaps", f"rc={rc2} {out2[-200:]}")


# ---------------------------------------------------------------- U9
def check_u9(repo: Path, out: Path) -> None:
    script = repo / "scripts/guest_candidate_feed.py"
    if not script.is_file():
        fail("u9", f"{script} does not exist — the candidate feeder was not built")
        return

    inbox = out / "inbox.json"
    ledger = out / "ledger.json"
    # One warm (referral/inbound source), one cold (sourced), one held, one recently touched.
    inbox.write_text(json.dumps({"schema": "candidate-inbox-v1", "candidates": [
        {"name": "Warm One", "email": "warm@example.invalid", "fit_score": 9,
         "confidence": "high", "source": "referral", "evidence": "referred by a past guest",
         "note": "", "first_seen": "2026-08-01"},
        {"name": "Cold One", "email": "cold@example.invalid", "fit_score": 9,
         "confidence": "high", "source": "sourced-list", "evidence": "found via directory",
         "note": "", "first_seen": "2026-08-01"},
        {"name": "Held One", "email": "held@example.invalid", "fit_score": 9,
         "confidence": "high", "source": "inbound", "evidence": "applied via site",
         "note": "", "first_seen": "2026-08-01"},
        {"name": "Fresh Touch", "email": "fresh@example.invalid", "fit_score": 9,
         "confidence": "high", "source": "inbound", "evidence": "applied via site",
         "note": "", "first_seen": "2026-08-01"}]}), encoding="utf-8")
    ledger.write_text(json.dumps({"schema": "funnel-ledger/v1", "people": [
        {"id": "p3", "name": "Held One", "stage": "new_inbound", "hold": "until October",
         "last_touch": None, "evidence": "", "provenance": "", "kind": "guest",
         "first_seen": "2026-08-01", "updated_at": "2026-08-01"},
        {"id": "p4", "name": "Fresh Touch", "stage": "new_inbound", "hold": None,
         "last_touch": "2026-08-09", "evidence": "", "provenance": "", "kind": "guest",
         "first_seen": "2026-08-01", "updated_at": "2026-08-09"}]}), encoding="utf-8")

    result = out / "candidates.json"
    if result.exists():
        result.unlink()
    done = run(repo, [str(script), "--inbox", str(inbox), "--ledger", str(ledger),
                      "--out", str(result), "--now", "2026-08-10"])
    if not result.is_file():
        fail("u9", "the feeder wrote no candidates file. The runner passes this path to the "
                   "producer; absent means the producer sees nothing and honestly reports "
                   "no_candidate, which is indistinguishable from a real drought.",
             f"rc={done.returncode} err={done.stderr[-200:]}")
        return
    try:
        payload = json.loads(result.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail("u9", f"candidates file is not valid JSON: {exc}")
        return
    if not isinstance(payload, list):
        fail("u9", "the producer requires a JSON LIST of candidates", type(payload).__name__)
        return

    by_alias = {}
    required = {"alias", "temperature", "channel", "podcast_status", "email_present",
                "cleared_by_human", "to", "subject", "body"}
    for entry in payload:
        if not isinstance(entry, dict) or not required.issubset(entry):
            fail("u9", "every candidate must carry the producer's full declared shape",
                 sorted(set(required) - set(entry if isinstance(entry, dict) else {})))
            return
        by_alias[entry["alias"]] = entry

    warm = [e for e in payload if e["temperature"] == "warm"]
    if not warm:
        fail("u9", "no warm candidate was produced from a referral and an inbound applicant. "
                   "A feeder that marks everyone cold produces a permanent honest zero.")
    for entry in payload:
        if entry["temperature"] not in {"warm", "cold"}:
            fail("u9", "temperature must be exactly warm or cold; the gate fails closed on anything "
                       "else, which silently blocks the lane", entry["temperature"])
        if entry["temperature"] == "cold" and entry["channel"] in {"email", "text"}:
            fail("u9", "a COLD candidate was routed to email. The charter allows cold only by "
                       "postcard or LinkedIn, and this lane autosends.", entry["alias"])

    names = json.dumps(payload)
    if "Held One" in names or "held@example.invalid" in names:
        fail("u9", "a candidate on an explicit hold was emitted for outreach")
    if "fresh@example.invalid" in names:
        fail("u9", "a candidate touched 1 day ago was emitted; the charter's per-contact cadence "
                   "floor is 4 days")
    if "cold@example.invalid" in names and any(
            e["temperature"] == "warm" for e in payload if e.get("to") == "cold@example.invalid"):
        fail("u9", "a sourced-list candidate was marked warm; source must decide temperature")

    # Empty inbox must be an honest, explained zero, not a crash and not a silent one.
    empty = out / "empty-inbox.json"
    empty.write_text(json.dumps({"schema": "candidate-inbox-v1", "candidates": []}), encoding="utf-8")
    empty_out = out / "empty-candidates.json"
    done2 = run(repo, [str(script), "--inbox", str(empty), "--ledger", str(ledger),
                       "--out", str(empty_out), "--now", "2026-08-10"])
    if not empty_out.is_file():
        fail("u9", "an empty pool must still write a candidates file (an empty list), so the "
                   "producer's 'no_candidate' means 'nobody qualified' and never 'the feeder "
                   "silently did not run'", done2.stderr[-200:])
    elif json.loads(empty_out.read_text(encoding="utf-8")) != []:
        fail("u9", "an empty pool must produce exactly []")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--u8-only", action="store_true")
    parser.add_argument("--u9-only", action="store_true")
    args = parser.parse_args()
    repo, out = args.repo.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    if not args.u9_only:
        check_u8(repo, out)
    if not args.u8_only:
        check_u9(repo, out)

    if FAILURES:
        print("\n".join(FAILURES))
        print(f"u8_u9_inputs_check: {len(FAILURES)} failure(s)")
        return 1
    print("u8_u9_inputs_check: PASS — the freshness gate accepts a fresh receipt and blocks real "
          "drift; the feeder produces warm candidates, never cold-to-email, never held, never "
          "inside the cadence floor, and an empty pool is an explained zero")
    return 0


if __name__ == "__main__":
    sys.exit(main())
