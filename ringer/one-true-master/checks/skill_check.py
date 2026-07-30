#!/usr/bin/env python3
"""Validate a packaged make-it-make-sense SKILL.md.

Substance over format: it checks that the protocol's load-bearing rules are
actually present and, for the public variant, that no private material leaked.
Every failure prints what is missing so the retry prompt is useful.

Usage: skill_check.py SKILL_PATH {private|public}
"""
import os
import re
import sys

# Concepts the skill is useless without. Each entry: (label, [accepted patterns]).
REQUIRED = [
    ("Pillar 5 PATH named", [r"\bPATH\b"]),
    ("Visible Router", [r"visible router|routing:"]),
    ("all five pillars", [r"PLAN", r"SHAPE", r"TRUST", r"INSIGHT"]),
    ("one-rung-one-goal rule", [r"one rung\s*=\s*one goal|one rung = one goal|rung.{0,40}one goal"]),
    ("scaffolding fold rule", [r"scaffold"]),
    ("the six rung lines", [r"GOAL", r"DONE", r"TEST", r"RED", r"STATE", r"WHO"]),
    ("RED is mandatory", [r"red is (mandatory|not optional)|never been seen fail|nobody has watched fail"]),
    ("human-gate honesty", [r"human gate"]),
    ("state vocabulary", [r"proven", r"building", r"broken", r"unknown"]),
    ("judge verdict vocabulary", [r"\bGO\b", r"\bHOLD\b", r"\bASK\b", r"\bGATE\b"]),
    ("decision block leads with meaning", [r"what it means"]),
    ("recommendation names its judge", [r"judge"]),
    ("grounded-in-data rule", [r"grounded|evidence|named data"]),
    ("plan-this-next flag", [r"plan this next"]),
    ("unmeasured is not zero", [r"never assumed zero|not assumed zero|unmeasured is"]),
    ("compound capture", [r"compound capture"]),
]

# Things that must never appear in the public variant.
PRIVATE_LEAKS = [
    (r"/home/[a-z0-9_]+", "an absolute home path"),
    (r"OB_mybcat|ob-mybcat", "the private brain's name"),
    (r"MyBCAT|mybcat", "the company name"),
    (r"\bCVC\b", "a client abbreviation"),
    (r"hubspot|HubSpot", "a private vendor integration"),
]


def main():
    if len(sys.argv) != 3 or sys.argv[2] not in {"private", "public"}:
        print("FAIL: checker misuse. Usage: skill_check.py SKILL_PATH {private|public}")
        return 1

    path, variant = sys.argv[1], sys.argv[2]
    if not os.path.isfile(path):
        parent = os.path.dirname(os.path.abspath(path)) or "."
        here = sorted(os.listdir(parent)) if os.path.isdir(parent) else []
        print("FAIL: %s was never written. Files present in %s: %s" % (path, parent, here))
        return 1

    text = open(path, encoding="utf-8", errors="replace").read()

    if len(text) < 3000:
        print("FAIL: skill is %d chars. A protocol this size cannot be expressed in under 3000; "
              "this looks like a stub.\n--- head ---\n%s" % (len(text), text[:600]))
        return 1

    # Frontmatter: a skill without it will not load.
    if not text.lstrip().startswith("---"):
        print("FAIL: no YAML frontmatter. The file must open with --- then name: and description:.\n"
              "--- first 200 chars ---\n%s" % text[:200])
        return 1
    front = text.split("---", 2)[1] if text.count("---") >= 2 else ""
    for key in ("name:", "description:"):
        if key not in front:
            print("FAIL: frontmatter is missing %s\n--- frontmatter ---\n%s" % (key, front[:400]))
            return 1

    missing = []
    for label, patterns in REQUIRED:
        for pat in patterns:
            if not re.search(pat, text, re.I):
                missing.append("%s (no match for /%s/)" % (label, pat))
                break
    if missing:
        print("FAIL: the skill omits rules it cannot work without:")
        for item in missing:
            print("  - " + item)
        return 1

    if variant == "public":
        leaks = []
        for pat, why in PRIVATE_LEAKS:
            for hit in re.findall(pat, text):
                leaks.append("%r (%s)" % (hit, why))
        if leaks:
            uniq = sorted(set(leaks))
            print("FAIL: the PUBLIC skill contains private material. Remove or generalize:")
            for item in uniq[:20]:
                print("  - " + item)
            return 1
    else:
        if "/home/" not in text:
            print("FAIL: the PRIVATE skill must point at the canonical text on disk, "
                  "and no absolute path is present.")
            return 1

    print("PASS: %s variant, %d chars, frontmatter valid, all %d required rules present%s."
          % (variant, len(text), len(REQUIRED),
             ", no private material" if variant == "public" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
