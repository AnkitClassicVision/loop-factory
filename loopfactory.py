#!/usr/bin/env python3
"""loop-factory CLI — one entry point for the whole factory.

Commands map 1:1 to the factory pipeline (see runbooks/factory-pipeline.md):

  scaffold   F0  stand a department skeleton (charter template, dirs, registry entry)
  interview  F1  create the intent-interview artifact for an agent/owner session
  validate   F2  charter + procedural-map QA (guard-matrix lint + traceability)
  release    F4  pin / verify a content-addressed release, flip `current`
  manager        run one department manager cycle (Sense→Compare→Decide→Record)
  estate         run one estate watchdog cycle over the registry
  heal           record a node failure/success against the self-heal ladder
  hil            human-in-the-loop queue: push / apply / escalate
  qa             full deterministic QA: charter + maps + release drift
  check          factory self-test: compileall + pytest

Everything here is deterministic and model-free. The LLM-directed parts of the
factory (interview questioning, concept-map authoring, node drafting) are done
by an agent following CLAUDE.md / AGENTS.md — this CLI is the rails they run on.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cmd_scaffold(args) -> int:
    scaffold = _load("scaffold", "factory/scaffold.py")
    out = scaffold.scaffold_department(args.name, root=args.root or ROOT, owner=args.owner)
    print(json.dumps(out, indent=2))
    return 0


def cmd_interview(args) -> int:
    root = Path(args.root or ROOT)
    dept = root / "departments" / args.name
    if not dept.exists():
        print(f"department '{args.name}' not scaffolded yet — run: "
              f"python3 loopfactory.py scaffold --name {args.name}")
        return 1
    template = (ROOT / "templates" / "intent-interview.md.tmpl").read_text(encoding="utf-8")
    out_path = dept / "interview" / "intent-interview.md"
    if out_path.exists() and not args.force:
        print(f"interview artifact already exists: {out_path} (use --force to overwrite)")
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(template.replace("{name}", args.name), encoding="utf-8")
    print(json.dumps({
        "artifact": str(out_path),
        "protocol": str(ROOT / "interview" / "INTERVIEW.md"),
        "question_bank": str(ROOT / "interview" / "QUESTION_BANK.md"),
        "next": "an agent runs the interview per interview/INTERVIEW.md, records "
                "answers VERBATIM in the artifact, and stops at INTENT LOCK "
                "(owner sign-off) — the lock is always human",
    }, indent=2))
    return 0


def cmd_validate(args) -> int:
    root = Path(args.root or ROOT)
    dept_dir = root / "departments" / args.name
    graphs = _load("graphs", "factory/graphs.py")
    loader = _load("charter_loader", "factory/charter_loader.py")
    result: dict = {"department": args.name}
    try:
        charter = loader.load_charter(dept_dir / "charter.yaml")
        result["charter"] = {"ok": True, "autonomy_state": loader.autonomy_state(charter)}
    except loader.CharterError as exc:
        result["charter"] = {"ok": False, "error": str(exc)}
    result["maps"] = graphs.qa(dept_dir)
    ok = result["charter"]["ok"] and result["maps"]["ok"]
    result["ok"] = ok
    print(json.dumps(result, indent=2))
    return 0 if ok else 1


def cmd_release(args) -> int:
    root = Path(args.root or ROOT)
    dept_dir = root / "departments" / args.name
    releases = dept_dir / "releases"
    release = _load("release", "factory/release.py")
    graphs = _load("graphs", "factory/graphs.py")
    if args.action == "pin":
        # process-change QA gate: a release can only pin a valid map state
        verdict = graphs.qa(dept_dir)
        if not (not verdict["lint"] and not verdict["traceability"]):
            print(json.dumps({"ok": False, "blocked_by_map_qa": verdict}, indent=2))
            return 1
        h = release.pin_release(dept_dir, releases, source_ref=args.source_ref)
        if args.flip:
            release.flip_current(releases, h)
        print(json.dumps({"ok": True, "hash": h, "current": release.read_current(releases)}))
        return 0
    current = release.read_current(releases)
    if current is None:
        print(json.dumps({"ok": False, "reason": "no release pinned"}))
        return 1
    out = release.verify_release(dept_dir, releases / current)
    print(json.dumps({"current": current, **out}))
    return 0 if out["ok"] else 1


def cmd_manager(args) -> int:
    argv = ["--department", args.name, "--root", str(args.root or ROOT)]
    if args.outbox:
        argv += ["--outbox", args.outbox]
    return _run_module("factory/manager.py", argv)


def cmd_estate(args) -> int:
    root = Path(args.root or ROOT)
    argv = ["--registry-dir", str(root / "estate" / "registry.d"),
            "--estate-state-dir", str(root / "estate" / "state")]
    if args.outbox:
        argv += ["--outbox", args.outbox]
    return _run_module("factory/estate_manager.py", argv)


def cmd_heal(args) -> int:
    argv = ["--state", args.state, "--node", args.node, "--now", str(args.now)]
    if args.success:
        argv.append("--success")
    return _run_module("factory/heal_ladder.py", argv)


def cmd_hil(args) -> int:
    return _run_module("factory/human_in_the_loop.py", args.hil_args)


def cmd_qa(args) -> int:
    root = Path(args.root or ROOT)
    dept_dir = root / "departments" / args.name
    graphs = _load("graphs", "factory/graphs.py")
    verdict = graphs.qa(dept_dir, release_root=dept_dir / "releases")
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["ok"] else 1


def cmd_check(args) -> int:
    ok = True
    for step in (
        [sys.executable, "-m", "compileall", "-q", str(ROOT / "factory"),
         str(ROOT / "kernel"), str(ROOT / "tests"), str(ROOT / "loopfactory.py")],
        [sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q"],
    ):
        print("$", " ".join(step[1:]))
        if subprocess.run(step, cwd=ROOT).returncode != 0:
            ok = False
    print("CHECK", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _run_module(rel: str, argv: list[str]) -> int:
    proc = subprocess.run([sys.executable, str(ROOT / rel), *argv])
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="loop-factory: interview → governed department")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scaffold", help="F0: stand a department skeleton")
    p.add_argument("--name", required=True)
    p.add_argument("--owner", default="owner")
    p.add_argument("--root", default=None)
    p.set_defaults(fn=cmd_scaffold)

    p = sub.add_parser("interview", help="F1: create the intent-interview artifact")
    p.add_argument("--name", required=True)
    p.add_argument("--root", default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_interview)

    p = sub.add_parser("validate", help="F2: charter + map QA")
    p.add_argument("--name", required=True)
    p.add_argument("--root", default=None)
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("release", help="F4: pin/verify a content-addressed release")
    p.add_argument("action", choices=["pin", "verify"])
    p.add_argument("--name", required=True)
    p.add_argument("--source-ref", default="unpinned")
    p.add_argument("--flip", action="store_true")
    p.add_argument("--root", default=None)
    p.set_defaults(fn=cmd_release)

    p = sub.add_parser("manager", help="run one department manager cycle")
    p.add_argument("--name", required=True)
    p.add_argument("--outbox", default=None)
    p.add_argument("--root", default=None)
    p.set_defaults(fn=cmd_manager)

    p = sub.add_parser("estate", help="run one estate watchdog cycle")
    p.add_argument("--outbox", default=None)
    p.add_argument("--root", default=None)
    p.set_defaults(fn=cmd_estate)

    p = sub.add_parser("heal", help="record a node failure/success on the heal ladder")
    p.add_argument("--state", required=True)
    p.add_argument("--node", required=True)
    p.add_argument("--now", type=float, required=True)
    p.add_argument("--success", action="store_true")
    p.set_defaults(fn=cmd_heal)

    p = sub.add_parser("hil", help="human-in-the-loop bridge (push/apply/escalate)")
    p.add_argument("hil_args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_hil)

    p = sub.add_parser("qa", help="full deterministic QA incl. release drift")
    p.add_argument("--name", required=True)
    p.add_argument("--root", default=None)
    p.set_defaults(fn=cmd_qa)

    p = sub.add_parser("check", help="factory self-test (compileall + pytest)")
    p.set_defaults(fn=cmd_check)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
