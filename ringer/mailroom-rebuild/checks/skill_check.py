#!/usr/bin/env python3
"""Independent oracle for the human-in-loop-oe master skill and the sync fix.

Both tasks write into their own disposable task directory rather than editing a
repo, because Ankit-open-skills has core.bare=true and cannot host a worktree.
The orchestrator places the accepted files afterwards.

Usage: skill_check.py --task skill|sync --dir <taskdir>
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FAILURES: list[str] = []
# Anything that only exists inside one vendor's agent runtime. A master skill
# that is meant to propagate to Claude, Codex, Hermes and Gemini cannot assume
# any of them.
NON_PORTABLE = [
    "allowed-tools",
    "allowed_tools",
    "mcp__",
    "claude code only",
    "codex exec -s",
    "$ARGUMENTS",
]
SECRET_SHAPES = re.compile(
    r"(bot\d{6,}:[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{20,}|lin_api_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY)"
)


def fail(message: str) -> None:
    FAILURES.append(message)


def require(text: str, needles: list[str], where: str, why: str) -> None:
    lowered = text.lower()
    missing = [n for n in needles if n.lower() not in lowered]
    if missing:
        fail(f"{where} is missing {missing}. {why}")


def check_single_line_commands(text: str, where: str) -> None:
    """Ankit's standing rule: every shell command he may copy-paste is ONE line."""
    for block in re.findall(r"```(?:bash|sh|shell|zsh)\n(.*?)```", text, re.DOTALL):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if len(lines) > 1:
            fail(
                f"{where} contains a multi-line shell block. Ankit copy-pastes these; "
                "every command must be a single line, chained with && or ;. Offending "
                f"block starts: {lines[0][:80]!r}"
            )
        if any(l.rstrip().endswith("\\") for l in lines):
            fail(f"{where} uses a backslash line continuation in a shell block; not allowed")


def check_skill(taskdir: Path) -> None:
    root = taskdir / "human-in-loop-oe"
    skill = root / "SKILL.md"
    setup = root / "references" / "codex-macmini-setup.md"
    if not skill.exists():
        fail(f"{skill} does not exist. The skill body is the deliverable.")
        return
    text = skill.read_text(encoding="utf-8", errors="replace")

    if not text.lstrip().startswith("---"):
        fail("SKILL.md must open with YAML frontmatter delimited by ---")
    else:
        front = text.split("---", 2)[1] if text.count("---") >= 2 else ""
        if "name:" not in front:
            fail("SKILL.md frontmatter has no name:")
        if "description:" not in front:
            fail("SKILL.md frontmatter has no description:; that is what makes it discoverable")

    require(
        text,
        ["raise", "poll", "packet_id", "decisions.jsonl"],
        "SKILL.md",
        "The four-step contract is raise a packet, wait, poll for your decision row, never claim approval without it.",
    )
    require(
        text,
        ["urgency", "due", "action_class", "source_surface"],
        "SKILL.md",
        "The packet field contract must be stated or a caller cannot build one.",
    )

    lowered = text.lower()
    if "never" not in lowered or ("approv" not in lowered):
        fail("SKILL.md must state explicitly that an approval is never assumed")
    if "timeout" not in lowered and "expire" not in lowered:
        fail(
            "SKILL.md must say that waiting does NOT become consent. A timeout that "
            "silently approves is the worst failure this system can have."
        )
    if "credential" not in lowered and "token" not in lowered:
        fail(
            "SKILL.md must state the no-credentials rule: a remote surface writes a "
            "packet and holds no Linear token, no Telegram token, and no ssh key."
        )

    found = [n for n in NON_PORTABLE if n.lower() in lowered]
    if found:
        fail(
            f"SKILL.md contains platform-locked constructs {found}. This is a MASTER "
            "skill that propagates to Claude, Codex, Hermes and Gemini; per-platform "
            "differences belong in surface-adapters.yaml, not in the skill body."
        )

    if SECRET_SHAPES.search(text):
        fail("SKILL.md appears to contain a real credential. Never embed one.")

    check_single_line_commands(text, "SKILL.md")

    if not setup.exists():
        fail(f"{setup} does not exist. The Mac mini setup reference is a required deliverable.")
        return
    setup_text = setup.read_text(encoding="utf-8", errors="replace")
    require(
        setup_text,
        ["install", "verify"],
        "references/codex-macmini-setup.md",
        "It must cover installing the skill and verifying it end to end.",
    )
    if "no decision" not in setup_text.lower() and "never raised" not in setup_text.lower():
        fail(
            "the setup reference must include a DELIBERATE FAILURE step: poll for a "
            "packet_id that was never raised and confirm the agent reports no decision "
            "rather than assuming one. A check nobody has watched fail is not a check."
        )
    if SECRET_SHAPES.search(setup_text):
        fail("the setup reference appears to contain a real credential")
    check_single_line_commands(setup_text, "references/codex-macmini-setup.md")


def check_sync(taskdir: Path) -> None:
    script = taskdir / "sync-skills-to-surfaces.sh"
    if not script.exists():
        fail(f"{script} does not exist")
        return

    # FAIL CLOSED BEFORE EXECUTING. The shipped script has hardcoded live paths
    # (~/.claude/skills, ~/.agents/skills) and ignores overrides, so merely
    # testing it writes to the operator's real surfaces. Learned by doing it:
    # a probe run on 2026-08-05 planted base/ and program/ on both surfaces and
    # had to be cleaned up. Never run this script until it can be redirected.
    source = script.read_text(encoding="utf-8", errors="replace")
    for variable in ("SKILLS_SRC", "SKILLS_DST"):
        if variable not in source:
            fail(
                f"the script does not reference {variable}. It must take its source and "
                "destination from those environment variables (falling back to the "
                "current defaults when unset) so it can be verified without writing to "
                "the operator's live skill surfaces. Refusing to execute it."
            )
    if FAILURES:
        return

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        src = tmp / "skills"
        # The REAL tree has skills at three different depths, which a two-level
        # fixture cannot see. Measured on the live repo 2026-08-05:
        #   depth 2  aws-secrets/SKILL.md                       (top-level skill)
        #   depth 3  base/caliber/SKILL.md                      (family)
        #   depth 4  program/hubspot-sales/gmail-radar-reader/  (sub-family)
        # A first fix hardcoded -mindepth 3 -maxdepth 3 and silently dropped 8
        # of 18 skills. Silent drops are the exact failure class this project
        # exists to remove, so the fixture now covers all three shapes.
        expected = {
            "toplevel": src / "toplevel",
            "alpha": src / "base" / "alpha",
            "beta": src / "program" / "beta",
            "deep": src / "program" / "subfamily" / "deep",
        }
        for path in expected.values():
            path.mkdir(parents=True)
            (path / "SKILL.md").write_text(f"{path.name}\n", encoding="utf-8")
        # A directory with no SKILL.md is not a skill and must be ignored, never
        # installed as an empty shell.
        (src / "base" / "not-a-skill" / "src").mkdir(parents=True)
        dst = tmp / "surface"
        dst.mkdir()

        env = dict(os.environ)
        env["SKILLS_SRC"] = str(src)
        env["SKILLS_DST"] = str(dst)
        result = subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, timeout=180, env=env
        )
        if result.returncode != 0:
            fail(
                "the sync script must honour SKILLS_SRC and SKILLS_DST overrides so it "
                f"can be tested without touching a live surface; exit {result.returncode}, "
                f"stderr: {(result.stderr or '')[:400]}"
            )
            return
        for family in ("base", "program"):
            if (dst / family).exists():
                fail(
                    f"skills landed under <surface>/{family}/. Surfaces scan FLAT, so "
                    "nothing inside a family directory is ever discovered. The family "
                    "level must be flattened on the way in."
                )
        for name in expected:
            if not (dst / name / "SKILL.md").exists():
                fail(
                    f"'{name}' did not land at <surface>/{name}/SKILL.md. Every skill "
                    "must be found regardless of how deep its family nesting is: the "
                    "real tree holds skills at depth 2, 3 and 4 under the source root. "
                    "Silently installing only some of them is worse than failing."
                )
        if (dst / "not-a-skill").exists():
            fail(
                "<surface>/not-a-skill/ was created from a directory with no SKILL.md. "
                "Only directories that actually contain a SKILL.md are skills."
            )
        landed = sorted(p.parent.name for p in dst.rglob("SKILL.md"))
        if landed != sorted(expected):
            fail(f"expected exactly {sorted(expected)} to land; got {landed}")

        # A same-named skill in two families must fail loudly, never silently win.
        (src / "program" / "alpha").mkdir(parents=True)
        (src / "program" / "alpha" / "SKILL.md").write_text("other alpha\n", encoding="utf-8")
        dst2 = tmp / "surface2"
        dst2.mkdir()
        env["SKILLS_DST"] = str(dst2)
        collide = subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, timeout=180, env=env
        )
        if collide.returncode == 0:
            fail(
                "two families both define a skill named 'alpha'. Flattening makes that a "
                "silent overwrite, so the script must fail loudly and name the collision. "
                "It exited 0 instead."
            )
        elif "alpha" not in ((collide.stderr or "") + (collide.stdout or "")):
            fail("the collision failure must name the colliding skill")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=["skill", "sync"])
    parser.add_argument("--dir", default=os.getcwd())
    args = parser.parse_args()
    taskdir = Path(args.dir).resolve()

    notes = taskdir / "notes.md"
    if not notes.exists():
        fail("./notes.md was not written; it is a required deliverable")

    {"skill": check_skill, "sync": check_sync}[args.task](taskdir)

    if FAILURES:
        print(f"\nCHECK FAILED for '{args.task}' — {len(FAILURES)} problem(s):\n")
        for index, message in enumerate(FAILURES, start=1):
            print(f"{index}. {message}\n")
        return 1
    print(f"CHECK PASS for '{args.task}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
