#!/usr/bin/env python3
"""Executed check for the department-audit lanes. Prints WHY on every failure.

Modes:
  gates  — verify gate-report.md carries all three read-only gate sections
           with recorded exit codes, then export it.
  prove  — verify prove-report.md carries a drill table with real PASS/FAIL
           evidence lines, then export it.
  review — verify report.md is a structured findings report whose cited repo
           paths exist, verify the worktree stayed read-only, then export.
"""
import argparse
import pathlib
import re
import shutil
import subprocess
import sys

REPO = pathlib.Path("/mnt/d_drive/repos/loop-factory")


def fail(why: str) -> None:
    print(f"CHECK FAIL: {why}")
    sys.exit(1)


def export(src: pathlib.Path, out: pathlib.Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, out)
    print(f"exported {src.name} -> {out}")


def check_gates(report: pathlib.Path, out: pathlib.Path) -> None:
    if not report.is_file():
        fail(f"{report} missing — the gate runner wrote nothing")
    text = report.read_text(encoding="utf-8")
    for section in ("## validate", "## objectives", "## qa"):
        if section not in text:
            fail(f"gate report lacks section '{section}'")
    rcs = re.findall(r"^exit_code=(\d+)$", text, re.M)
    if len(rcs) < 3:
        fail(f"gate report records {len(rcs)} exit codes; need one per gate (3)")
    if len(text) < 400:
        fail("gate report suspiciously short — gates likely did not run")
    export(report, out)


def check_prove(report: pathlib.Path, out: pathlib.Path) -> None:
    if not report.is_file():
        fail(f"{report} missing — prove produced no report")
    text = report.read_text(encoding="utf-8")
    drills = re.findall(r'"name": "([^"]+)",\s*\n\s*"pass": (true|false)', text)
    if len(drills) < 6:
        fail(f"prove report shows {len(drills)} drills; the runner defines more — truncated output")
    if "exit_code=" not in text:
        fail("prove report does not record the prove exit code")
    export(report, out)


def check_review(report: pathlib.Path, out: pathlib.Path) -> None:
    if not report.is_file():
        fail(f"{report} missing — reviewer wrote no report")
    text = report.read_text(encoding="utf-8")
    findings = len(re.findall(r"^Evidence:", text, re.M))
    cleans = len(re.findall(r"\bclean\b", text, re.I))
    if findings < 1 and cleans < 3:
        fail("report has zero findings and fewer than 3 explicit 'clean' dimension verdicts — not a completed audit")
    for label in ("Impact:", "Fix:"):
        if findings and label not in text:
            fail(f"findings present but no '{label}' lines — incomplete finding structure")
    cited = set(re.findall(r"\b((?:departments|factory|kernel|runbooks)/[A-Za-z0-9_\-./]+?\.(?:py|sh|json|md|yaml))\b", text))
    negation = re.compile(r"missing|absent|not exist|does not|no such|none|lacks|would live|expected at|should (?:be|live|exist)", re.I)
    invented = []
    for c in sorted(cited):
        if (REPO / c).exists():
            continue
        # A nonexistent path is INVENTED evidence only when no line citing it
        # describes it as missing/expected — reporting a gap by its would-be
        # path is a legitimate finding, not fabrication.
        lines = [l for l in text.splitlines() if c in l]
        if not all(negation.search(l) for l in lines):
            invented.append(c)
    if invented:
        fail(f"report cites nonexistent paths (invented evidence): {invented[:5]}")
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
    dirty = [l for l in status.splitlines() if l.split(None, 1)[-1] not in ("report.md", "worker.log")]
    if dirty:
        fail(f"read-only reviewer modified the worktree: {dirty[:5]}")
    export(report, out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["gates", "prove", "review"])
    ap.add_argument("--report", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    report, out = pathlib.Path(args.report), pathlib.Path(args.out)
    {"gates": check_gates, "prove": check_prove, "review": check_review}[args.mode](report, out)
    print("CHECK PASS")


if __name__ == "__main__":
    main()
