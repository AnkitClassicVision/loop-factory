#!/usr/bin/env python3
"""U5 check: the `.delivered` marker must key on the escalation body's CONTENT.

The check runs in two phases against the same scenario script, so a green result
means the harness actually exercised the defect before it accepted a fix.

  NEGATIVE CONTROL — drive the escalation functions extracted from the baseline
  (`git show <ref>:<script>`) and require that today's bug REPRODUCES: an
  escalation body that changed after a delivery is not delivered again. If the
  bug fails to reproduce, the harness is not reaching the marker gate at all and
  every later assertion is meaningless, so the check fails loudly rather than
  green-lighting a fix it never tested.

  FIXED — drive the identical scenarios against the working-tree script and
  require: first body delivers; an identical body is suppressed; a changed body
  delivers; a status line appends even after a prior delivery; the marker is a
  digest that tracks the body; and a failed delivery writes no marker.

The scenarios drive the three shell functions directly (extracted by name from
the script text) rather than running the whole runner, so no Ringer worker, no
ssh, and no Telegram call is involved. `LOOP` is a name outside the runner's
card-path case list, so delivery takes the plain-text path into a sink file.

Exit 0 only when every assertion holds; every failure prints what was expected,
what happened, and the sink/marker state that proves it.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FUNCS = ("telegram", "deliver_escalation_once", "append_abnormal_status_once")

BODY_A = "guest pipeline sits at 3 of the 6 required. approve the outreach batch.\n"
BODY_B = "guest pipeline dropped to 2 of 6. two candidates went cold overnight.\n"
BODY_C = "booking readiness blocked: no prep call slot inside the window.\n"
APPEND_REASON = "reviewer unverified"

SUMMARY_SECTIONS = ("# Fix Summary", "## Summary", "## Files Changed", "## Verification")

DRIVER = """#!/usr/bin/env bash
# U5 harness driver. Extracted escalation functions, no network, no worker.
set -u
LOOP="u5harness"
DATE_TAG="20260101"
RECEIPT_DIR="{sandbox}"
ESCALATE_FILE="$RECEIPT_DIR/${{LOOP}}-${{DATE_TAG}}.ESCALATE"
LOG="$RECEIPT_DIR/${{LOOP}}-${{DATE_TAG}}.log"
LOOP_TELEGRAM_SINK="$RECEIPT_DIR/sink.txt"
PEM="/dev/null"
VPS="u5-harness-invalid-host"
REPO="$RECEIPT_DIR"
PYTHON_BIN="/bin/false"
TG_FAILED=0

{functions}

case "${{1:-}}" in
  deliver)
    deliver_escalation_once
    echo "RC=$?"
    ;;
  deliver_no_telegram)
    telegram() {{ return 1; }}
    deliver_escalation_once
    echo "RC=$?"
    ;;
  append)
    append_abnormal_status_once "${{2:?reason required}}"
    echo "RC=$?"
    ;;
  *)
    echo "unknown op: ${{1:-<none>}}" >&2
    exit 64
    ;;
esac
"""


class HarnessError(RuntimeError):
    """The harness itself could not run — never a verdict about the fix."""


def extract_function(text: str, name: str) -> str:
    """Pull one `name() {` ... `}` block out of a bash script's source text."""
    lines = text.splitlines()
    header = f"{name}() {{"
    start = None
    for index, line in enumerate(lines):
        if line.strip() == header:
            start = index
            break
    if start is None:
        raise HarnessError(
            f"could not find the shell function `{name}` — the check extracts "
            f"functions by the exact header line `{header}` with the closing "
            f"brace in column 1. Keep that style."
        )
    for index in range(start + 1, len(lines)):
        if lines[index] == "}":
            return "\n".join(lines[start : index + 1])
    raise HarnessError(f"function `{name}` never closes with a `}}` in column 1")


def build_driver(script_text: str, sandbox: Path) -> Path:
    functions = "\n\n".join(extract_function(script_text, name) for name in FUNCS)
    driver = sandbox / "driver.sh"
    driver.write_text(DRIVER.format(sandbox=sandbox, functions=functions), encoding="utf-8")
    driver.chmod(0o755)
    return driver


class Sandbox:
    """One isolated receipt dir plus the driver built from one script version."""

    def __init__(self, root: Path, script_text: str, label: str) -> None:
        self.label = label
        self.dir = root / label
        self.dir.mkdir(parents=True)
        self.driver = build_driver(script_text, self.dir)
        self.escalate = self.dir / "u5harness-20260101.ESCALATE"
        self.marker = self.dir / "u5harness-20260101.ESCALATE.delivered"
        self.sink = self.dir / "sink.txt"

    def write_body(self, body: str) -> None:
        self.escalate.write_text(body, encoding="utf-8")

    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def sink_text(self) -> str:
        return self.read(self.sink)

    def marker_text(self) -> str:
        return self.read(self.marker)

    def run(self, *op: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.driver), *op],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(self.dir),
            env=env,
        )

    def digest_free_env(self) -> dict[str, str]:
        """A PATH where every common digest tool fails, so the fix cannot hash."""
        stub = self.dir / "stubbin"
        stub.mkdir(exist_ok=True)
        for tool in ("sha256sum", "shasum", "sha1sum", "md5sum", "b2sum", "cksum", "openssl"):
            path = stub / tool
            path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            path.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{stub}:{env.get('PATH', '')}"
        return env


def deliveries_of(sink: str, body: str) -> int:
    """How many times this body reached the sink."""
    return sink.count(body.strip())


def run_scenarios(sandbox: Sandbox, *, fixed: bool) -> list[str]:
    """Drive the shared scenario sequence. Returns failure strings."""
    failures: list[str] = []
    phase = "FIXED" if fixed else "BASELINE"

    def note(step: str, detail: str) -> None:
        failures.append(f"FAIL [{phase}/{step}]: {detail}")

    # S1 — first escalation of the day delivers.
    sandbox.write_body(BODY_A)
    sandbox.run("deliver")
    if deliveries_of(sandbox.sink_text(), BODY_A) != 1:
        note(
            "s1_first_delivery",
            "the first escalation body was not delivered exactly once; the harness "
            f"is not reaching the delivery path.\nsink:\n{sandbox.sink_text()!r}",
        )
        return failures  # nothing later is meaningful
    if not sandbox.marker.is_file():
        note("s1_marker_written", "delivery succeeded but no .delivered marker was written")
        return failures
    marker_after_a = sandbox.marker_text()

    # S2 — an identical body on a rerun stays suppressed (both phases).
    sandbox.run("deliver")
    if deliveries_of(sandbox.sink_text(), BODY_A) != 1:
        note(
            "s2_identical_suppressed",
            "an unchanged escalation body was delivered twice; the marker no longer "
            f"suppresses repeats.\nsink:\n{sandbox.sink_text()!r}",
        )
    if fixed and sandbox.marker_text() != marker_after_a:
        note(
            "s2_marker_stable",
            "the marker changed even though the body did not; it is not keyed on content",
        )

    # S3 — the discriminating case: a changed body on a same-day rerun.
    sandbox.write_body(BODY_B)
    sandbox.run("deliver")
    delivered_b = deliveries_of(sandbox.sink_text(), BODY_B)
    if fixed:
        if delivered_b != 1:
            note(
                "s3_changed_body_delivers",
                "a changed escalation body was NOT delivered — this is the whole unit. "
                f"Expected 1 delivery of the new body, saw {delivered_b}.\n"
                f"sink:\n{sandbox.sink_text()!r}\nmarker: {sandbox.marker_text()!r}",
            )
        if sandbox.marker_text() == marker_after_a:
            note(
                "s3_marker_tracks_body",
                "the marker did not change after a different body was delivered, so it "
                "does not record the delivered content",
            )
    else:
        if delivered_b != 0:
            note(
                "s3_negative_control",
                "the baseline delivered a changed body, so the bug this unit exists to "
                "fix did not reproduce. The harness is not exercising the marker gate "
                "and no result from it can be trusted.\n"
                f"sink:\n{sandbox.sink_text()!r}",
            )
        return failures  # baseline's remaining behaviour is not interesting

    # S4 — a runner status line must still reach a body that was already delivered.
    sandbox.run("append", APPEND_REASON)
    body_now = sandbox.read(sandbox.escalate)
    if APPEND_REASON not in body_now:
        note(
            "s4_status_line_appends",
            "append_abnormal_status_once refused to append after a delivery. It still "
            "guards on the marker's existence; with a content-keyed marker that guard "
            f"only hides the runner's own status from the alert.\nbody:\n{body_now!r}",
        )
    else:
        sandbox.run("deliver")
        if deliveries_of(sandbox.sink_text(), APPEND_REASON) < 1:
            note(
                "s4_appended_body_delivers",
                "the appended status line never reached the sink, so the changed body "
                f"was not re-delivered.\nsink:\n{sandbox.sink_text()!r}",
            )

    # S5 — the marker must be a digest, not a copy of the alert body.
    marker_now = sandbox.marker_text()
    if not marker_now.strip():
        note("s5_marker_non_empty", "the marker is empty, so it records no content at all")
    if len(marker_now.encode("utf-8")) > 200:
        note(
            "s5_marker_is_digest",
            f"the marker is {len(marker_now.encode('utf-8'))} bytes; a content hash is "
            "expected, not a copy of the escalation body",
        )
    if BODY_B.strip() in marker_now:
        note("s5_marker_is_digest", "the marker contains the raw escalation body instead of a digest")

    return failures


def run_failed_delivery_scenario(sandbox: Sandbox, *, fixed: bool) -> list[str]:
    """A delivery that fails must leave no marker, or the alert is lost forever."""
    failures: list[str] = []
    phase = "FIXED" if fixed else "BASELINE"
    sandbox.write_body(BODY_C)
    result = sandbox.run("deliver_no_telegram")
    if "RC=0" in result.stdout:
        failures.append(
            f"FAIL [{phase}/s6_failed_delivery_rc]: delivery failed but "
            f"deliver_escalation_once returned 0.\nstdout:\n{result.stdout}"
        )
    if sandbox.marker.is_file():
        failures.append(
            f"FAIL [{phase}/s6_no_marker_on_failure]: a marker was written even though "
            "delivery failed; the next run would treat the undelivered alert as sent.\n"
            f"marker: {sandbox.marker_text()!r}"
        )
    return failures


def run_digest_failure_scenario(sandbox: Sandbox, *, fixed: bool) -> list[str]:
    """A digest that cannot be computed must never read as 'already delivered'.

    Keying suppression on a hash introduces a way to fail that existence-keying
    never had: if the digest comes back empty and the recorded digest is also
    empty, an equality test suppresses an escalation nobody ever saw. This
    scenario makes every common digest tool fail and requires the alert to
    survive it — delivered, or a non-zero return the runner will surface. Only
    silence is a failure. An implementation that cannot fail this way passes it
    without noticing.
    """
    phase = "FIXED" if fixed else "BASELINE"
    sandbox.write_body(BODY_A)
    result = sandbox.run("deliver", env=sandbox.digest_free_env())
    delivered = deliveries_of(sandbox.sink_text(), BODY_A)
    if delivered == 0 and "RC=0" in result.stdout:
        return [
            f"FAIL [{phase}/s7_digest_failure_is_not_silence]: with every digest tool "
            "failing, the escalation was neither delivered nor reported as failed — "
            "deliver_escalation_once returned 0 and sent nothing. An empty digest must "
            "not compare equal to an absent marker and swallow the alert.\n"
            f"stdout:\n{result.stdout}\nsink:\n{sandbox.sink_text()!r}"
        ]
    return []


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def check_summary(summary: Path, exported: Path) -> list[str]:
    failures: list[str] = []
    if not summary.is_file():
        return [f"FAIL [summary_missing]: {summary} was not written"]
    text = summary.read_text(encoding="utf-8")
    missing = [section for section in SUMMARY_SECTIONS if section.lower() not in text.lower()]
    if missing:
        failures.append(f"FAIL [summary_sections]: fix-summary.md is missing {', '.join(missing)}")
    words = len(text.split())
    if words > 700:
        failures.append(f"FAIL [summary_length]: fix-summary.md is {words} words, ceiling is 700")
    if not failures:
        exported.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(summary, exported)
    return failures


def export_patch(worktree: Path, owned: str, patch: Path) -> list[str]:
    """Stage only the owned path, prove nothing else moved, export the diff."""
    failures: list[str] = []
    add = git(worktree, "add", "--", owned)
    if add.returncode != 0:
        return [f"FAIL [git_add_failed]: {add.stderr.strip()}"]

    status = git(worktree, "status", "--porcelain")
    if status.returncode != 0:
        return [f"FAIL [git_status_failed]: {status.stderr.strip()}"]
    for line in status.stdout.splitlines():
        code, _, path = line[:2], line[2:3], line[3:]
        if code == "??":
            continue  # worker.log, fix-summary.md and other scratch are not repo changes
        if path.strip('"') != owned:
            failures.append(
                f"FAIL [outside_owned_files]: {path} was modified but this task owns "
                f"only {owned}"
            )

    diff = git(worktree, "diff", "--cached", "--binary", "--", owned)
    if diff.returncode != 0:
        return failures + [f"FAIL [git_diff_failed]: {diff.stderr.strip()}"]
    if not diff.stdout.strip():
        return failures + [
            "FAIL [empty_patch]: no staged change to " f"{owned}; the script was never edited"
        ]
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(diff.stdout, encoding="utf-8")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", default=".", type=Path)
    parser.add_argument("--script", default="scripts/run_podcast_loop.sh")
    parser.add_argument("--baseline-ref", default="HEAD")
    parser.add_argument("--patch", type=Path, help="where to export the reviewed diff")
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    parser.add_argument(
        "--self-test-baseline",
        type=Path,
        help="developer mode: use this file as the baseline instead of git, and skip patch export",
    )
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    script_path = worktree / args.script
    if not script_path.is_file():
        print(f"FAIL [script_missing]: {script_path} does not exist")
        return 1
    fixed_text = script_path.read_text(encoding="utf-8")

    if args.self_test_baseline:
        baseline_text = args.self_test_baseline.read_text(encoding="utf-8")
    else:
        show = git(worktree, "show", f"{args.baseline_ref}:{args.script}")
        if show.returncode != 0:
            print(f"FAIL [baseline_unavailable]: git show {args.baseline_ref}:{args.script} "
                  f"failed: {show.stderr.strip()}")
            return 1
        baseline_text = show.stdout

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="u5-marker-") as tmp:
        root = Path(tmp)
        try:
            baseline = Sandbox(root, baseline_text, "baseline")
            fixed = Sandbox(root, fixed_text, "fixed")
        except HarnessError as exc:
            print(f"FAIL [function_extraction]: {exc}")
            return 1

        failures += run_scenarios(baseline, fixed=False)
        failures += run_scenarios(fixed, fixed=True)

        try:
            baseline_fail = Sandbox(root, baseline_text, "baseline-faildelivery")
            fixed_fail = Sandbox(root, fixed_text, "fixed-faildelivery")
        except HarnessError as exc:
            print(f"FAIL [function_extraction]: {exc}")
            return 1
        failures += run_failed_delivery_scenario(baseline_fail, fixed=False)
        failures += run_failed_delivery_scenario(fixed_fail, fixed=True)

        try:
            baseline_digest = Sandbox(root, baseline_text, "baseline-nodigest")
            fixed_digest = Sandbox(root, fixed_text, "fixed-nodigest")
        except HarnessError as exc:
            print(f"FAIL [function_extraction]: {exc}")
            return 1
        failures += run_digest_failure_scenario(baseline_digest, fixed=False)
        failures += run_digest_failure_scenario(fixed_digest, fixed=True)

    if args.exported_summary:
        failures += check_summary(args.summary, args.exported_summary)
    if args.patch:
        failures += export_patch(worktree, args.script, args.patch)

    if failures:
        for item in failures:
            print(item)
        print(f"\n{len(failures)} failure(s). Exit 1.")
        return 1

    print("PASS [u5_marker_content_keyed]")
    print("  baseline reproduced the bug: a changed escalation body was not delivered")
    print("  fixed: first body delivered, identical body suppressed, changed body delivered")
    print("  fixed: status line appends after a delivery and the changed body re-delivers")
    print("  fixed: marker is a digest that tracks the body, and none is written on failure")
    print("  fixed: an uncomputable digest does not silently swallow the escalation")
    if args.patch:
        print(f"  exported {args.patch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
