#!/usr/bin/env python3
"""U2a + U2b executed check: the guest-acquisition loop creates a real draft.

Owner sign-off 2026-08-10: wave 2 approved, and Ankit chose "ride the existing
autosend lane now" — a QA-passed draft sends with no APPROVE, behind a SKIP
window (owner promotion 2026-07-22). That choice makes the PRODUCER the only
place the charter's volume ceilings exist: measured 2026-08-10, the bridge's
`_caps_check` enforces ONLY a 7-day per-recipient cooldown
(obe_draft_to_linear_bridge.py:546-554) plus MAX_CARDS_PER_RUN=5. Nothing in
that lane enforces 12 outbound/day, 5 new contacts/day, or 300 touches/week.
So this check treats ceiling enforcement as a first-class safety assertion, not
a nicety.

Two halves, because the risk lives in two places:

  MODULE  server/pipeline/guest_outreach_draft.py driven directly with a fake
          Gmail service and a fake voice gate. This is where a cold candidate
          must never produce a draft, where a gate-blocked draft must be
          DELETED (or the live bridge cards it 30 minutes later and autosends a
          letter the gate rejected), and where the ceilings must bite.
  RUNNER  scripts/loop_shadow_run.py drives the REAL runner for three
          scenarios, because "the loop invokes the producer and handles its
          exit code" is a control-flow claim, and control-flow claims in this
          job are only ever proven by execution.

FROZEN MODULE CONTRACT (the worker spec restates this verbatim):

  from server.pipeline.guest_outreach_draft import run_guest_outreach_draft
  run_guest_outreach_draft(
      *, candidates: list[dict], gmail_service, voice_gate: Callable[[str], dict],
      ledger_path: Path, receipt_path: Path, gate_runner: Callable[[str, dict], dict],
      now: datetime | None = None, ceilings: dict | None = None,
  ) -> dict

  candidates   each {"alias", "temperature", "channel", "podcast_status",
                     "email_present", "cleared_by_human", "to", "subject", "body"}
  gate_runner  (gate_name, payload) -> {"ok": bool, "violation": str|None}
               the caller wires this to server.pipeline.prose_gates
  voice_gate   (draft_id) -> {"verdict": "pass"|"fail", "iterations": [...],
                              "receipt_path": str}
  ceilings     defaults to the charter values: outbound_per_day 12,
               new_contacts_per_day 5, weekly_touches 300

  Returns, and writes to receipt_path, a dict with:
      schema "obe.guest.outreach.draft.v1"
      status one of: drafted | gate_blocked | no_candidate | capped | error
      drafts_created int, sent False ALWAYS (this module never sends)
      candidate_key str, draft_deleted bool, gates dict, violation str|None,
      iterations list (present on gate_blocked), ceiling str|None (on capped)

  Process exit codes when run as a module: 0 drafted, 2 no legal candidate
  (gate blocked, capped, or none eligible — a clean stop), 1 error.

Usage: u2a_producer_check.py --repo <tree> --out <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

FAILURES: list[str] = []
CHARTER_CEILINGS = {"outbound_per_day": 12, "new_contacts_per_day": 5, "weekly_touches": 300}


def fail(where: str, why: str, extra: object = None) -> None:
    FAILURES.append(f"CHECK FAIL ({where}): {why}" + (f" [{extra}]" if extra is not None else ""))


# --------------------------------------------------------------------------
# MODULE HALF — drive the producer directly with fakes.
# --------------------------------------------------------------------------

DRIVER = r'''
import json, sys
from datetime import datetime, timezone
from pathlib import Path

repo, outdir, case = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, repo)
from server.pipeline.guest_outreach_draft import run_guest_outreach_draft

CALLS = []

class FakeGmail:
    """Records every mutation. A cold candidate must never reach create()."""
    def __init__(self):
        self.drafts_created = []
        self.drafts_deleted = []
    def create_draft(self, *, to, subject, body, bcc=None):
        CALLS.append({"op": "create", "to_len": len(to)})
        draft_id = f"draft-{len(self.drafts_created) + 1}"
        self.drafts_created.append(draft_id)
        return draft_id
    def delete_draft(self, draft_id):
        CALLS.append({"op": "delete", "draft_id": draft_id})
        self.drafts_deleted.append(draft_id)

WARM = {"alias": "cand-warm", "temperature": "warm", "channel": "email",
        "podcast_status": "new_inbound", "email_present": True,
        "cleared_by_human": False, "to": "guest@example.invalid",
        "subject": "quick question about the show", "body": "Hey there, short note."}
COLD = dict(WARM, alias="cand-cold", temperature="cold")
NOMINATED = dict(WARM, alias="cand-nom", podcast_status="nominated")

def gate_runner_real(name, payload):
    """Wire the REAL prose_gates module, exactly as production must."""
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(payload, fh)
        path = fh.name
    done = subprocess.run([sys.executable, "-m", "server.pipeline.prose_gates",
                           "--gate", name, "--input", path],
                          capture_output=True, text=True, cwd=repo,
                          env={**__import__("os").environ, "PYTHONPATH": repo})
    if done.returncode == 0:
        return {"ok": True, "violation": None}
    try:
        payload_out = json.loads(done.stdout.strip().splitlines()[-1])
        return {"ok": False, "violation": payload_out.get("violation") or "blocked"}
    except Exception:
        return {"ok": False, "violation": f"gate exit {done.returncode}"}

def voice_pass(draft_id):
    return {"verdict": "pass", "iterations": [{"iteration": 1, "judgment": {"verdict": "pass"}}],
            "receipt_path": str(outdir / "voice-receipt.json")}

def voice_fail(draft_id):
    return {"verdict": "fail",
            "iterations": [{"iteration": 1, "judgment": {"verdict": "revise"}, "rewrote": True},
                           {"iteration": 2, "judgment": {"verdict": "revise"},
                            "note": "no rewrite produced"}],
            "receipt_path": str(outdir / "voice-receipt.json")}

gmail = FakeGmail()
kwargs = dict(gmail_service=gmail, gate_runner=gate_runner_real,
              ledger_path=outdir / "ledger.json", receipt_path=outdir / "receipt.json",
              now=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc))

if case == "warm-drafts":
    result = run_guest_outreach_draft(candidates=[WARM], voice_gate=voice_pass, **kwargs)
elif case == "cold-blocked":
    result = run_guest_outreach_draft(candidates=[COLD], voice_gate=voice_pass, **kwargs)
elif case == "nominated-blocked":
    result = run_guest_outreach_draft(candidates=[NOMINATED], voice_gate=voice_pass, **kwargs)
elif case == "gate-fail-deletes":
    result = run_guest_outreach_draft(candidates=[WARM], voice_gate=voice_fail, **kwargs)
elif case == "daily-ceiling":
    result = run_guest_outreach_draft(
        candidates=[WARM], voice_gate=voice_pass,
        ceilings={"outbound_per_day": 12, "new_contacts_per_day": 5, "weekly_touches": 300},
        sent_today=12, new_contacts_today=0, touches_this_week=0, **kwargs)
elif case == "weekly-ceiling":
    result = run_guest_outreach_draft(
        candidates=[WARM], voice_gate=voice_pass,
        ceilings={"outbound_per_day": 12, "new_contacts_per_day": 5, "weekly_touches": 300},
        sent_today=0, new_contacts_today=0, touches_this_week=300, **kwargs)
else:
    raise SystemExit(f"unknown case {case}")

print(json.dumps({"result": result, "calls": CALLS,
                  "created": gmail.drafts_created, "deleted": gmail.drafts_deleted}))
'''


def drive_module(repo: Path, out: Path, case: str) -> dict | None:
    case_dir = out / f"module-{case}"
    case_dir.mkdir(parents=True, exist_ok=True)
    driver = out / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(driver), str(repo), str(case_dir), case],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(repo)},
    )
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        fail(f"module:{case}", "driver produced no JSON", done.stderr[-300:])
        return None


def check_module(repo: Path, out: Path) -> None:
    module = repo / "server/pipeline/guest_outreach_draft.py"
    if not module.is_file():
        fail("module", f"{module} does not exist — the U2a producer was not built")
        return

    got = drive_module(repo, out, "warm-drafts")
    if got:
        r = got["result"]
        if r.get("status") != "drafted" or r.get("drafts_created") != 1:
            fail("module:warm-drafts", "a warm, gate-clean candidate must produce exactly one draft", r)
        if r.get("sent") is not False:
            fail("module:warm-drafts", "this module must NEVER report a send; sending belongs to the executor", r)
        if len(got["created"]) != 1:
            fail("module:warm-drafts", "exactly one Gmail draft must be created", got["created"])

    for case, gate_word in (("cold-blocked", "channel"), ("nominated-blocked", "nominated")):
        got = drive_module(repo, out, case)
        if not got:
            continue
        r = got["result"]
        if got["created"]:
            fail(f"module:{case}",
                 "a blocked candidate reached Gmail — a draft was created before the gate refused. "
                 "This lane AUTOSENDS; a draft that exists can be sent.", got["created"])
        if r.get("status") not in {"gate_blocked", "no_candidate"}:
            fail(f"module:{case}", "a gate-blocked candidate must not report a drafted status", r)
        if gate_word not in json.dumps(r).lower():
            fail(f"module:{case}", f"the receipt must name the {gate_word} gate that refused", r)

    got = drive_module(repo, out, "gate-fail-deletes")
    if got:
        r = got["result"]
        if not got["created"]:
            fail("module:gate-fail-deletes", "the voice gate can only judge a draft that was created")
        if got["created"] and got["created"] != got["deleted"]:
            fail("module:gate-fail-deletes",
                 "a voice-QA-FAILED draft was left alive in Gmail. The live bridge cards drafts every "
                 "30 minutes and autosends QA-passed ones; an abandoned draft is a letter the gate "
                 "rejected waiting to be sent. It MUST be deleted.",
                 {"created": got["created"], "deleted": got["deleted"]})
        if r.get("status") != "gate_blocked" or r.get("draft_deleted") is not True:
            fail("module:gate-fail-deletes", "status must be gate_blocked with draft_deleted true", r)
        iterations = r.get("iterations")
        if not isinstance(iterations, list) or len(iterations) != 2:
            fail("module:gate-fail-deletes",
                 "the receipt must carry BOTH voice-gate iterations (U2c: exactly two, not three)", r)
        blob = json.dumps(r)
        if "@" in blob:
            fail("module:gate-fail-deletes",
                 "the receipt leaks an email-shaped address; receipts are pasted into Telegram", blob[:120])

    for case, ceiling in (("daily-ceiling", "outbound_per_day"), ("weekly-ceiling", "weekly_touches")):
        got = drive_module(repo, out, case)
        if not got:
            continue
        r = got["result"]
        if got["created"]:
            fail(f"module:{case}",
                 f"the {ceiling} ceiling was already reached and a draft was created anyway. The bridge "
                 "does NOT enforce this ceiling (measured: only a 7-day per-recipient cooldown), so the "
                 "producer is the only thing standing between a quota-driven loop and the charter limit.",
                 got["created"])
        if r.get("status") != "capped" or r.get("ceiling") != ceiling:
            fail(f"module:{case}", f"status must be capped naming {ceiling}", r)


# --------------------------------------------------------------------------
# RUNNER HALF — drive the real runner through the shadow harness.
# --------------------------------------------------------------------------

def run_scenario(repo: Path, scenario: str, out: Path) -> dict | None:
    out_dir = out / f"runner-{scenario}"
    if out_dir.exists():
        subprocess.run(["rm", "-rf", str(out_dir)], check=True)
    done = subprocess.run(
        [sys.executable, str(repo / "scripts/loop_shadow_run.py"),
         "--repo", str(repo), "--scenario", scenario, "--out", str(out_dir)],
        capture_output=True, text=True, timeout=900,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        fail(f"runner:{scenario}", "harness produced no JSON report", done.stderr[-300:])
        return None


def producer_calls(report: dict) -> list[dict]:
    return [c for c in report.get("external_calls", []) if c.get("kind", "").startswith("guest-producer")]


def check_runner(repo: Path, out: Path) -> None:
    report = run_scenario(repo, "guest-producer-drafted", out)
    if report:
        calls = producer_calls(report)
        if not calls or calls[0]["kind"] != "guest-producer":
            fail("runner:drafted",
                 "the guest-acquisition loop never invoked the draft producer — the loop still cannot "
                 "put anything on the conveyor", report.get("external_calls"))
        if report.get("exit_code") != 0:
            fail("runner:drafted", "a successful draft run must exit 0", report)
        if not report.get("tree_unchanged"):
            fail("runner:drafted", "the runner wrote into the driven tree", report)

    report = run_scenario(repo, "guest-producer-gate-blocked", out)
    if report:
        if not producer_calls(report):
            fail("runner:gate-blocked", "the producer was never invoked", report.get("external_calls"))
        if report.get("exit_code") != 0:
            fail("runner:gate-blocked",
                 "a gate-blocked letter is a clean stop for THAT candidate, not a failed run: exit 0", report)
        sink = "\n".join(report.get("sink", []))
        if "voice" not in sink.lower() and "gate" not in sink.lower():
            fail("runner:gate-blocked",
                 "the blocked letter must be escalated naming the gate that stopped it", report.get("sink"))
        if "@" in sink:
            fail("runner:gate-blocked", "the escalation leaks an email-shaped address", sink[:160])
        if report.get("verdict") == "DROVE":
            fail("runner:gate-blocked", "a blocked letter is not a send and must not compute DROVE", report)

    report = run_scenario(repo, "guest-producer-crash", out)
    if report:
        if report.get("exit_code") == 0:
            fail("runner:crash", "a crashed producer must fail the unit loudly, never exit 0", report)
        if not any("FAILED" in line for line in report.get("sink", [])):
            fail("runner:crash", "the producer crash must reach the alert sink", report.get("sink"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--module-only", action="store_true")
    parser.add_argument("--runner-only", action="store_true")
    args = parser.parse_args()
    repo, out = args.repo.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    if not args.runner_only:
        check_module(repo, out)
    if not args.module_only:
        check_runner(repo, out)

    if FAILURES:
        print("\n".join(FAILURES))
        print(f"u2a_producer_check: {len(FAILURES)} failure(s)")
        return 1
    print("u2a_producer_check: PASS — cold never drafts, a rejected draft is deleted, the charter "
          "ceilings bite, and the real runner drives all of it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
