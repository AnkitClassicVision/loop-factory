#!/usr/bin/env python3
"""U17 executed check: the credentials actually reach the feeder process.

The producer step in run_podcast_loop.sh has always been wrapped so it receives
a HubSpot key and a Gmail token. The candidate feeder was not, because until
2026-08-11 it never needed one — it read two JSON files. It now decides who is
safe to cold-email by reading four contact channels, and a channel it cannot
authenticate to is a channel it must report as unread, which holds everybody.

So an unwrapped feeder invocation does not fail loudly. It produces a permanent,
polite, fully-receipted zero. That is precisely the silent failure this loop was
built to remove, and a grep for the word 'HUBSPOT_API_KEY' in a shell script
would not tell you whether the variable survives into the process that needs it.

This check therefore EXECUTES the runner's own guest-acquisition feeder
invocation, extracted verbatim from the script, against a shim standing in for
the interpreter. The shim records which variables ARRIVED — names only, never
values, because the real secret injector runs in production and a check must not
be able to write a credential anywhere.

Usage: u17_feeder_env_arrival_check.py --worktree <tree> [--owned PATH ...]
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

GMAIL_KEYS = ("GMAIL_FULL_TOKEN_PATH", "GMAIL_CREDENTIALS_PATH")
HUBSPOT_KEYS = ("HUBSPOT_API_KEY",)
LINKEDIN_KEYS = ("LINKEDIN_UNIPILE_CREDENTIALS", "UNIPILE_ACCESS_TOKEN")

SHIM = '''#!/usr/bin/env python3
"""Stands in for the interpreter. Records arrivals; never records a value."""
import json, os, sys

argv = sys.argv[1:]
record = os.environ["U17_RECORD"]


def is_script(name):
    return any(str(arg).endswith(name) for arg in argv)


if is_script("guest_candidate_feed.py"):
    payload = {
        "reached_feeder": True,
        "env_keys": sorted(os.environ),
        "nonempty": {key: bool(str(os.environ.get(key, "")).strip())
                     for key in os.environ},
    }
    with open(record, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    sys.exit(0)

if is_script("secret_exec.py"):
    # Simulate the approved injector: honour --secret-env NAME=ref, then run the
    # command after --. No AWS call, so this check cannot depend on ambient
    # credentials and cannot ever hold a real secret.
    injected, rest, index = {}, [], 0
    while index < len(argv):
        item = argv[index]
        if item == "--secret-env" and index + 1 < len(argv):
            name, _, ref = argv[index + 1].partition("=")
            injected[name] = "stub-value-for-" + (ref or "unknown")
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

sys.exit(0)
'''


FAILURES: list[str] = []


def fail(where: str, why: str) -> None:
    FAILURES.append(f"FAIL [{where}]: {why}")


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def extract_block(text: str) -> str | None:
    """Pull the feeder invocation out of the runner, from `if !` to its `fi`."""
    lines = text.splitlines()
    anchor = next((i for i, line in enumerate(lines)
                   if "scripts/guest_candidate_feed.py" in line), None)
    if anchor is None:
        return None
    start = anchor
    while start >= 0 and not re.match(r"\s*if\s+!", lines[start]):
        start -= 1
    if start < 0:
        return None
    depth, end = 0, None
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if re.match(r"if\s+", stripped) or stripped == "if":
            depth += 1
        if stripped == "fi" or stripped.startswith("fi "):
            depth -= 1
            if depth == 0:
                end = index
                break
    if end is None:
        return None
    return "\n".join(lines[start:end + 1])


def check(worktree: Path) -> None:
    runner = worktree / RUNNER
    if not runner.is_file():
        fail("missing_runner", f"{RUNNER} does not exist")
        return

    syntax = subprocess.run(["bash", "-n", str(runner)], capture_output=True, text=True, timeout=60)
    if syntax.returncode != 0:
        fail("bash_syntax", f"the runner does not parse: {syntax.stderr.strip()[:300]}")
        return

    text = runner.read_text(encoding="utf-8")
    block = extract_block(text)
    if block is None:
        fail("block_not_found",
             "could not locate the guest-acquisition feeder invocation in the runner")
        return

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        shim = root / "shim-python"
        shim.write_text(SHIM, encoding="utf-8")
        shim.chmod(0o755)
        record = root / "arrivals.json"
        (root / "inbox.json").write_text('{"candidates": []}', encoding="utf-8")
        (root / "ledger.json").write_text('{"people": []}', encoding="utf-8")

        preamble = "\n".join([
            "set -u",
            f'REPO={json.dumps(str(worktree))}',
            f'PYTHON_BIN={json.dumps(str(shim))}',
            f'LOG={json.dumps(str(root / "run.log"))}',
            f'RECEIPT={json.dumps(str(root / "receipt.json"))}',
            f'GUEST_CANDIDATE_INBOX={json.dumps(str(root / "inbox.json"))}',
            f'GUEST_FUNNEL_LEDGER={json.dumps(str(root / "ledger.json"))}',
            f'GUEST_CANDIDATES={json.dumps(str(root / "candidates.json"))}',
            f'GUEST_SOURCE_TRUTH={json.dumps(str(root / "source-truth.json"))}',
            'REENTRY_ATTEMPT=1',
            'DATE_TAG=2026-08-11',
            f'RECEIPT_DIR={json.dumps(str(root))}',
            # Anything the failure branch would reach is neutralised. The shim
            # exits 0, so the branch is not taken; this only stops a stray send
            # if the extraction ever grabs more than intended.
            'telegram() { cat > /dev/null; }',
            'export -f telegram',
        ])

        # Scrub every credential carrier out of the inherited environment first.
        # Otherwise a variable that happens to be exported in whoever's shell ran
        # this check would arrive at the feeder and be credited to the runner,
        # and the check would pass here and fail in production — which is the
        # inverse of what it is for.
        clean_env = {key: value for key, value in os.environ.items()
                     if not (key.startswith(("GMAIL_", "UNIPILE_", "LINKEDIN_", "HUBSPOT_"))
                             or key in ("BEE_API_KEY",))}
        done = subprocess.run(
            ["bash", "-c", preamble + "\n" + block],
            capture_output=True, text=True, timeout=300, cwd=str(worktree),
            env={**clean_env, "U17_RECORD": str(record), "PYTHONDONTWRITEBYTECODE": "1"})

        if not record.is_file():
            fail("feeder_never_ran",
                 f"executing the runner's own feeder invocation never reached "
                 f"guest_candidate_feed.py (exit {done.returncode}). "
                 f"{(done.stdout + done.stderr).strip()[:300]}")
            return

        arrivals = json.loads(record.read_text(encoding="utf-8"))
        present = {key for key, ok in arrivals.get("nonempty", {}).items() if ok}

        def require(label: str, candidates: tuple[str, ...], why: str) -> None:
            if not (set(candidates) & present):
                fail(f"{label}_missing",
                     f"none of {', '.join(candidates)} arrived in the feeder's environment. {why}")

        require("gmail", GMAIL_KEYS,
                "Gmail is a first-order channel, so without it the resolver returns UNKNOWN for "
                "every candidate and no cold outreach can ever clear")
        require("linkedin", LINKEDIN_KEYS,
                "LinkedIn is a first-order channel and is now read through Unipile; without its "
                "credentials the channel is unread and every candidate is held")
        require("hubspot", HUBSPOT_KEYS,
                "HubSpot is second-order and does not block clearing, but without it the loop "
                "loses its only corroboration for a touch logged directly in the CRM")

        # A credential must never be a literal in the script. `--secret-env
        # NAME=some/store/ref` is the approved form and is not a literal: the
        # value is a reference resolved at run time, inside the child process.
        pattern = r"(UNIPILE_ACCESS_TOKEN|HUBSPOT_API_KEY|UNIPILE_DSN)\s*=\s*([^\s\"']+)"
        for match in re.finditer(pattern, text):
            value = match.group(2)
            preceding = text[max(0, match.start() - 20):match.start()]
            if "--secret-env" in preceding or value.startswith("$") or "/" in value:
                continue
            if re.fullmatch(r"[A-Za-z0-9_\-]{20,}", value):
                fail("literal_credential",
                     f"{match.group(1)} is assigned what looks like a literal value in the runner. "
                     "Credentials come from the secret store at run time, never from the file")


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
        print("u17_feeder_env_arrival_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u17_feeder_env_arrival_check: PASS — the runner's own guest-acquisition feeder "
          "invocation, executed as written, delivers Gmail, LinkedIn and HubSpot credentials into "
          "the feeder process, and no credential is a literal in the script")
    return 0


if __name__ == "__main__":
    sys.exit(main())
