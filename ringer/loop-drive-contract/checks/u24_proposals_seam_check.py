#!/usr/bin/env python3
"""U24 executed check: identity resolution is a driven stage, not a file someone finds.

The 2026-08-11 drive audit's unanimous finding: this system writes and notifies
but does not resolve. Concrete instance: the feeder emits an awaiting_identity
queue, scripts/identity_proposals.py exists and works, and NO scheduled path
connects them — the live proposal receipt exists only because a human ran the
script by hand. Work stalled invisibly in a queue with no consumer.

This check EXECUTES the runner's own guest-acquisition block (extracted
verbatim, driven against a recording stand-in interpreter, credentials scrubbed
from the inherited environment) and asserts:

  1. identity_proposals.py is invoked AFTER the feeder, with --queue pointing at
     the feeder's reasons receipt (the queue travels as aliases; no names).
  2. Its environment carries the HubSpot and Unipile credentials — a generator
     invoked without them writes a permanently empty queue that reads as
     "nobody needed resolving".
  3. Its output lands as a dated private artifact under the receipt directory.
  4. It is advisory, not a gate: when the proposals step fails, the loop
     CONTINUES (the producer must still run — identity resolution failing must
     not stop drafts for people who already have addresses) and the failure is
     named in the log rather than swallowed.

Usage: u24_proposals_seam_check.py --worktree <tree> [--owned PATH ...]
                                   [--patch OUT] [--summary fix-summary.md]
                                   [--exported-summary OUT]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SUMMARY_SECTIONS = ("what changed", "how i verified", "risk")
RUNNER = "scripts/run_podcast_loop.sh"

SHIM = '''#!/usr/bin/env python3
"""Stands in for the interpreter. Records invocations; never records values."""
import json, os, sys

argv = sys.argv[1:]
record_dir = os.environ["U24_RECORD_DIR"]


def script_is(name):
    return bool(argv) and str(argv[0]).endswith(name)


def flag(name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def append(payload):
    with open(os.path.join(record_dir, "invocations.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\\n")


if script_is("secret_exec.py"):
    injected, rest, index = {}, [], 0
    while index < len(argv):
        item = argv[index]
        if item == "--secret-env" and index + 1 < len(argv):
            name, _, ref = argv[index + 1].partition("=")
            injected[name] = "stub-for-" + (ref or "unknown")
            index += 2
            continue
        if item == "--":
            rest = argv[index + 1:]
            break
        index += 1
    if not rest:
        sys.exit(0)
    environment = dict(os.environ)
    environment.update(injected)
    os.execvpe(rest[0], rest, environment)

if script_is("source_truth_revalidate.py"):
    out = flag("--out")
    if out:
        open(out, "w").write("{}")
    append({"script": "source_truth_revalidate"})
    sys.exit(0)

if script_is("guest_candidate_feed.py"):
    out = flag("--out")
    reasons = flag("--reasons")
    if out and not reasons:
        base = os.path.basename(out)
        stem = base[:-5] if base.endswith(".json") else base
        reasons = os.path.join(os.path.dirname(out), stem + ".reasons.json")
    if out:
        open(out, "w").write("[]")
    if reasons:
        json.dump({"schema": "guest-candidate-feed-report/v1", "considered": 2, "selected": 0,
                   "awaiting_identity": [{"alias": "cand-abc123", "channel": "email",
                                          "reason": "warm candidate with no address"}],
                   "dropped": [], "dropped_by_reason": {}, "contact_coverage": []},
                  open(reasons, "w"))
    append({"script": "guest_candidate_feed", "out": out, "reasons": reasons})
    sys.exit(0)

if script_is("identity_proposals.py"):
    append({"script": "identity_proposals", "argv": argv[1:],
            "env_nonempty": sorted(k for k, v in os.environ.items()
                                   if str(v).strip() and (k.startswith(("HUBSPOT_", "UNIPILE_", "LINKEDIN_")))),
            "queue": flag("--queue"), "out": flag("--out")})
    rc = int(os.environ.get("U24_PROPOSALS_RC", "0"))
    out = flag("--out")
    if rc == 0 and out:
        json.dump({"schema": "identity-proposals/v1", "proposals": [], "unresolved": [],
                   "coverage": []}, open(out, "w"))
    sys.exit(rc)

if script_is("guest_ceiling_counts.py"):
    out = flag("--out")
    if out:
        open(out, "w").write("{}")
    append({"script": "guest_ceiling_counts"})
    print("--sent-today 0 --new-contacts-today 0 --sent-this-week 0")
    sys.exit(0)

append({"script": "other", "head": os.path.basename(str(argv[0])) if argv else ""})
sys.exit(0)
'''

FAILURES: list[str] = []


def fail(where: str, why: str) -> None:
    FAILURES.append(f"FAIL [{where}]: {why}")


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def extract_block(text: str) -> str | None:
    """From the feeder's `if !` up to (not including) the U10 ceiling step."""
    lines = text.splitlines()
    anchor = next((i for i, line in enumerate(lines)
                   if "scripts/guest_candidate_feed.py" in line), None)
    if anchor is None:
        return None
    start = anchor
    while start >= 0 and not re.match(r"\s*if\s+!", lines[start]):
        start -= 1
    end = next((i for i in range(anchor, len(lines))
                if "guest_ceiling_counts.py" in lines[i]), None)
    if start < 0 or end is None:
        return None
    while end > start and not re.match(r"\s*(#|GUEST_COUNTS_RECEIPT=|if\s+!)", lines[end - 1] + " "):
        end -= 1
    # Walk back to the start of the ceiling step's `if !` or its comment block.
    while end > anchor and (lines[end - 1].strip().startswith("#") or not lines[end - 1].strip()):
        end -= 1
    while end > anchor and "guest_ceiling_counts" not in lines[end] and re.match(r"\s*if\s+!", lines[end]) is None:
        break
    return "\n".join(lines[start:end])


def run_block(worktree: Path, block: str, proposals_rc: int) -> tuple[subprocess.CompletedProcess, list[dict], Path]:
    scratch = Path(tempfile.mkdtemp(prefix="u24-"))
    shim = scratch / "shim-python"
    shim.write_text(SHIM, encoding="utf-8")
    shim.chmod(0o755)
    (scratch / "inbox.json").write_text('{"candidates": []}', encoding="utf-8")
    (scratch / "ledger.json").write_text('{"people": []}', encoding="utf-8")
    preamble = "\n".join([
        "set -u",
        f'REPO={json.dumps(str(worktree))}',
        f'PYTHON_BIN={json.dumps(str(shim))}',
        f'LOG={json.dumps(str(scratch / "run.log"))}',
        f'RECEIPT={json.dumps(str(scratch / "receipt.json"))}',
        f'RECEIPT_DIR={json.dumps(str(scratch))}',
        f'GUEST_CANDIDATE_INBOX={json.dumps(str(scratch / "inbox.json"))}',
        f'GUEST_FUNNEL_LEDGER={json.dumps(str(scratch / "ledger.json"))}',
        f'GUEST_CANDIDATES={json.dumps(str(scratch / "guest-candidates-20260811.json"))}',
        f'GUEST_SOURCE_TRUTH={json.dumps(str(scratch / "source-truth.json"))}',
        f'GMAIL_FULL_TOKEN={json.dumps(str(scratch / "gmail-token.json"))}',
        'DATE_TAG=2026-08-11',
        'REENTRY_ATTEMPT=1',
        'telegram() { cat > /dev/null; }',
        'export -f telegram',
    ])
    clean_env = {key: value for key, value in os.environ.items()
                 if not (key.startswith(("GMAIL_", "UNIPILE_", "LINKEDIN_", "HUBSPOT_"))
                         or key in ("BEE_API_KEY",))}
    done = subprocess.run(
        ["bash", "-c", preamble + "\n" + block],
        capture_output=True, text=True, timeout=300, cwd=str(worktree),
        env={**clean_env, "U24_RECORD_DIR": str(scratch),
             "U24_PROPOSALS_RC": str(proposals_rc), "PYTHONDONTWRITEBYTECODE": "1"})
    invocations = []
    record = scratch / "invocations.jsonl"
    if record.is_file():
        invocations = [json.loads(line) for line in record.read_text().splitlines() if line.strip()]
    return done, invocations, scratch


def check(worktree: Path) -> None:
    runner = worktree / RUNNER
    if subprocess.run(["bash", "-n", str(runner)], capture_output=True, timeout=60).returncode != 0:
        fail("bash_syntax", "the runner does not parse")
        return
    block = extract_block(runner.read_text(encoding="utf-8"))
    if block is None:
        fail("block_not_found", "could not extract the feeder→ceiling window from the runner")
        return

    done, invocations, scratch = run_block(worktree, block, proposals_rc=0)
    order = [entry["script"] for entry in invocations]
    if "identity_proposals" not in order:
        fail("not_wired",
             f"executing the runner's own block never invoked identity_proposals.py. Scripts "
             f"seen, in order: {order}. The queue still has no consumer")
        return
    if order.index("identity_proposals") < order.index("guest_candidate_feed"):
        fail("wrong_order", f"identity_proposals ran before the feeder: {order}")

    proposal = next(entry for entry in invocations if entry["script"] == "identity_proposals")
    feeder = next(entry for entry in invocations if entry["script"] == "guest_candidate_feed")
    if not proposal.get("queue"):
        fail("no_queue_arg",
             "identity_proposals was invoked without --queue; it would re-read the raw inbox and "
             "re-decide routing, which is the split-brain the audit condemned")
    elif feeder.get("reasons") and proposal["queue"] != feeder["reasons"]:
        fail("queue_mismatch",
             f"--queue is {proposal['queue']!r} but the feeder's reasons receipt is "
             f"{feeder['reasons']!r}; the consumer must read the queue the producer wrote")
    env_seen = set(proposal.get("env_nonempty") or [])
    if "HUBSPOT_API_KEY" not in env_seen:
        fail("no_hubspot_credential",
             f"identity_proposals ran without a HubSpot credential; the email lane would report "
             f"an empty queue forever. Credential-ish env seen: {sorted(env_seen)}")
    if not ({"UNIPILE_ACCESS_TOKEN", "LINKEDIN_UNIPILE_CREDENTIALS"} & env_seen):
        fail("no_unipile_credential",
             f"identity_proposals ran without Unipile credentials; the LinkedIn lane would report "
             f"an empty queue forever. Seen: {sorted(env_seen)}")
    out = proposal.get("out") or ""
    if str(scratch) not in out or "identity-proposals" not in os.path.basename(out):
        fail("artifact_misplaced",
             f"the proposals artifact goes to {out!r}; expected a dated identity-proposals file "
             f"under the receipt directory")

    # Advisory, not a gate: a failing proposals step must not stop the loop.
    done, invocations, scratch = run_block(worktree, block, proposals_rc=1)
    if done.returncode != 0:
        fail("proposals_failure_gates_loop",
             f"the proposals step failed and the block exited {done.returncode}. Identity "
             "resolution is advisory; its failure must not stop drafts for people who already "
             "have addresses")
    log = (scratch / "run.log")
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    combined = (log_text + done.stdout + done.stderr).lower()
    if "identity" not in combined and "proposal" not in combined:
        fail("failure_swallowed",
             "the proposals step failed and nothing in the log names it; a stalled identity lane "
             "would be invisible, which is the resolution failure this whole unit exists to fix")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", default=".", type=Path)
    parser.add_argument("--owned", action="append", default=[])
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    check(worktree)

    if args.exported_summary:
        if not args.summary.is_file():
            fail("summary_missing", f"{args.summary} was not written")
        else:
            body = args.summary.read_text(encoding="utf-8").lower()
            missing = [s for s in SUMMARY_SECTIONS if s not in body]
            if missing:
                fail("summary_sections", f"fix-summary.md is missing {', '.join(missing)}")
            else:
                args.exported_summary.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(args.summary, args.exported_summary)

    if args.patch and args.owned:
        add = git(worktree, "add", "--", *args.owned)
        if add.returncode != 0:
            fail("git_add_failed", add.stderr.strip())
        for line in git(worktree, "status", "--porcelain").stdout.splitlines():
            code, path = line[:2], line[3:].strip('"')
            if code != "??" and path not in args.owned:
                fail("outside_owned_files", f"{path} changed; this task owns {args.owned}")
        diff = git(worktree, "diff", "--cached", "--binary", "--", *args.owned)
        if not diff.stdout.strip():
            fail("empty_patch", "nothing staged; no owned file was edited")
        else:
            args.patch.parent.mkdir(parents=True, exist_ok=True)
            args.patch.write_text(diff.stdout, encoding="utf-8")

    if FAILURES:
        print("u24_proposals_seam_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u24_proposals_seam_check: PASS — the runner invokes identity_proposals after the "
          "feeder with the feeder's own queue and real credentials, the artifact lands dated in "
          "the receipt directory, and a failing proposals step is named in the log without "
          "stopping the loop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
