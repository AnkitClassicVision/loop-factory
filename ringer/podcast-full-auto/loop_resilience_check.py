#!/usr/bin/env python3
"""Receipt for the podcast loop-runner resilience fix.

Independently proves — with a stub Ringer that HANGS exactly the way the real
GLM/Z.AI reviewer lane hung on 2026-07-24 — that:

  1. a hung reviewer can no longer consume the systemd unit budget: the runner
     regains control and exits non-zero on its own,
  2. the worker's escalation still reaches the owner's notify sink,
  3. the happy path still succeeds and delivers the escalation exactly once,
  4. the QA gate is not weakened (a missing/unreadable verdict still fails).

Also runs the repo's own tests for the runner. Prints WHY on failure.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO = "/mnt/d_drive/repos/podcast"
RUNNER = os.path.join(REPO, "scripts", "run_podcast_loop.sh")
TOKEN = "ESCALATION-TOKEN-8f3a21"

STUB_HANG = '''#!/usr/bin/env python3
import os, sys, time
if len(sys.argv) > 1 and sys.argv[1] == "lint":
    print("lint: clean (stub)")
    raise SystemExit(0)
rd = os.environ["LOOP_RECEIPT_DIR"]
tag = os.environ["STUB_DATE_TAG"]
open(os.path.join(rd, "health-%s.md" % tag), "w").write(
    "VERDICT: NEEDS_ANKIT\\nstub receipt for the resilience proof\\n")
open(os.path.join(rd, "health-%s.ESCALATE" % tag), "w").write(
    "{token} pipeline needs you today\\n")
sys.stdout.flush()
time.sleep(600)
'''

STUB_OK = '''#!/usr/bin/env python3
import os, sys
if len(sys.argv) > 1 and sys.argv[1] == "lint":
    print("lint: clean (stub)")
    raise SystemExit(0)
rd = os.environ["LOOP_RECEIPT_DIR"]
tag = os.environ["STUB_DATE_TAG"]
open(os.path.join(rd, "health-%s.md" % tag), "w").write(
    "VERDICT: NEEDS_ANKIT\\nstub receipt for the resilience proof\\n")
open(os.path.join(rd, "health-%s.ESCALATE" % tag), "w").write(
    "{token} pipeline needs you today\\n")
open(os.path.join(rd, "health-%s.QA.md" % tag), "w").write(
    "QA: PASS\\nstub reviewer verdict\\n")
raise SystemExit(0)
'''

STUB_NOQA = '''#!/usr/bin/env python3
import os, sys
if len(sys.argv) > 1 and sys.argv[1] == "lint":
    print("lint: clean (stub)")
    raise SystemExit(0)
rd = os.environ["LOOP_RECEIPT_DIR"]
tag = os.environ["STUB_DATE_TAG"]
open(os.path.join(rd, "health-%s.md" % tag), "w").write(
    "VERDICT: OK\\nstub receipt, reviewer produced nothing\\n")
raise SystemExit(0)
'''


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def run_case(stub_src, budget, wall_limit, label):
    """Run the runner against a stub ringer. Returns (rc, sink_text, elapsed)."""
    workdir = tempfile.mkdtemp(prefix=f"loopres-{label}-")
    tag = time.strftime("%Y%m%d")
    stub = os.path.join(workdir, "stub_ringer.py")
    with open(stub, "w") as fh:
        fh.write(stub_src.replace("{token}", TOKEN))
    os.chmod(stub, 0o755)
    sink = os.path.join(workdir, "telegram_sink.txt")
    env = dict(os.environ)
    env.update({
        "LOOP_RECEIPT_DIR": workdir,
        "LOOP_RINGER_BIN": stub,
        "LOOP_TELEGRAM_SINK": sink,
        "LOOP_RUN_BUDGET_S": str(budget),
        "STUB_DATE_TAG": tag,
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    start = time.time()
    try:
        proc = subprocess.run(["bash", RUNNER, "health"], env=env,
                              capture_output=True, text=True, timeout=wall_limit)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        rc = None
    elapsed = time.time() - start
    text = open(sink).read() if os.path.exists(sink) else ""
    shutil.rmtree(workdir, ignore_errors=True)
    return rc, text, elapsed


def main() -> int:
    if not os.path.exists(RUNNER):
        return fail(f"runner missing at {RUNNER}")

    src = open(RUNNER, encoding="utf-8").read()
    if "LOOP_QA_ENGINE:-claude-lean" not in src:
        return fail("the global cross-model reviewer default is not claude-lean "
                    "(expected LOOP_QA_ENGINE:-claude-lean in the runner)")
    if "opencode" in src and "LOOP_QA_ENGINE:-opencode" in src:
        return fail("the runner still defaults a reviewer lane to the hung opencode/GLM engine")
    if not re.search(r"^\s*trap\s", src, re.M):
        return fail("the runner has no trap — a SIGTERM from systemd would still strand the escalation")

    syn = subprocess.run(["bash", "-n", RUNNER], capture_output=True, text=True)
    if syn.returncode != 0:
        return fail(f"runner does not parse: {syn.stderr.strip()[:400]}")

    # CASE 1 — the reviewer lane hangs (today's real failure).
    rc, sink, elapsed = run_case(STUB_HANG, budget=5, wall_limit=180, label="hang")
    print(f"[hang] rc={rc} elapsed={elapsed:.1f}s sink_bytes={len(sink)}")
    if rc is None:
        return fail("hung-reviewer case: the runner never returned — it is still "
                    "capable of burning the whole systemd budget")
    if elapsed > 120:
        return fail(f"hung-reviewer case: runner took {elapsed:.0f}s to regain control (budget was 5s)")
    if rc == 0:
        return fail("hung-reviewer case: runner reported success despite an unverified receipt")
    if TOKEN not in sink:
        return fail("hung-reviewer case: the worker's escalation NEVER reached the notify sink — "
                    "this is exactly the silent failure the fix must remove")
    if not re.search(r"budget|timed out|timeout|interrupt", sink, re.I):
        return fail("hung-reviewer case: nothing in the notify sink explains WHY the run ended")

    # CASE 2 — happy path still succeeds, and delivers the escalation once.
    rc, sink, elapsed = run_case(STUB_OK, budget=120, wall_limit=180, label="ok")
    print(f"[ok] rc={rc} elapsed={elapsed:.1f}s occurrences={sink.count(TOKEN)}")
    if rc != 0:
        return fail(f"happy path: runner exited {rc}, expected 0")
    if sink.count(TOKEN) != 1:
        return fail(f"happy path: escalation delivered {sink.count(TOKEN)} times, expected exactly 1")

    # CASE 3 — the QA gate is NOT weakened: no verdict is still a failed run.
    rc, sink, elapsed = run_case(STUB_NOQA, budget=120, wall_limit=180, label="noqa")
    print(f"[noqa] rc={rc} elapsed={elapsed:.1f}s")
    if rc == 0:
        return fail("missing-QA case: runner reported success with no reviewer verdict — "
                    "the deny-by-default QA gate was weakened")

    # The repo's own tests for this runner must still pass.
    tests = ["tests/test_run_podcast_loop_referral_automation.py"]
    extra = os.path.join(REPO, "tests", "test_run_podcast_loop_resilience.py")
    if not os.path.exists(extra):
        return fail("tests/test_run_podcast_loop_resilience.py was not added")
    tests.append("tests/test_run_podcast_loop_resilience.py")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX="/tmp")
    pt = subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=900)
    tail = (pt.stdout or "")[-1500:] + (pt.stderr or "")[-500:]
    print(tail)
    if pt.returncode != 0:
        return fail(f"pytest failed (rc={pt.returncode}) — see output above")

    print("RESULT: PASS — a hung reviewer is bounded, the escalation still lands, "
          "the happy path is intact, and the QA gate still denies by default")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
