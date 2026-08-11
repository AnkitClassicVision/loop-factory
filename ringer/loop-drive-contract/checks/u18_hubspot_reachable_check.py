#!/usr/bin/env python3
"""U18 executed check: HubSpot must not refuse itself over a value it never uses.

Measured live on 2026-08-11 with real credentials injected exactly as the
scheduled runner injects them: gmail reached, linkedin reached, bee reached,
hubspot UNREACHED — "HubSpot credentials were unavailable". The API key was
present and valid. The adapter refused because `build_client()` also demands
HUBSPOT_PORTAL_ID, which it stores on the client and never reads: HubSpot v3
authenticates with the bearer token alone, and nothing supplies a portal id
anywhere in the repo.

HubSpot is second-order, so this does not block a cold open. It costs the loop
its only corroboration for a touch logged directly in the CRM rather than
through email — which is exactly the case the second-order tier exists for. An
unreachable channel is meant to mean "we could not read it", not "we declined
to try".

This check is small and its job is narrow: the presence of a bearer token is
sufficient to attempt a read, and the absence of one is still refused.

Usage: u18_hubspot_reachable_check.py --worktree <tree> [--owned PATH ...]
                                      [--patch OUT] [--summary fix-summary.md]
                                      [--exported-summary OUT]
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

SUMMARY_SECTIONS = ("what changed", "how i verified", "risk")
ENV_KEYS = ("HUBSPOT_API_KEY", "HUBSPOT_PORTAL_ID")

FAILURES: list[str] = []


def fail(where: str, why: str) -> None:
    FAILURES.append(f"FAIL [{where}]: {why}")


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def load(worktree: Path, relative: str, name: str):
    path = worktree / relative
    if not path.is_file():
        fail("missing_file", f"{relative} does not exist")
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fail("unimportable", f"{relative} could not be loaded")
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        fail("import_error", f"{relative} raised {exc.__class__.__name__}: {exc}")
        return None
    return module


def check(worktree: Path) -> None:
    module = load(worktree, "server/pipeline/contact_sources/hubspot_source.py",
                  "u18_hubspot_source")
    if module is None:
        return

    saved = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
    # The adapter also falls back to a file in the operator's home. Point that
    # lookup at nothing so this check measures the code, not the machine.
    home = os.environ.get("HOME")
    try:
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["HOME"] = str(worktree / "no-such-home")

        if module.build_client() is not None:
            fail("built_without_key",
                 "build_client() returned a client with no HUBSPOT_API_KEY at all. Without a "
                 "bearer token there is nothing to authenticate with, and a client that cannot "
                 "authenticate must not be presented as a readable channel")

        os.environ["HUBSPOT_API_KEY"] = "token-for-this-check-only"
        client = module.build_client()
        if client is None:
            fail("refused_with_valid_key",
                 "a bearer token was present and build_client() still returned None. HubSpot v3 "
                 "authenticates with that token alone, and the portal id it is waiting for is "
                 "stored and never read — nothing in this repo supplies one, so this channel "
                 "reports unreachable on every run while being perfectly readable")

        # And with both present it must still work, so nobody 'fixes' this by
        # inverting the condition.
        os.environ["HUBSPOT_PORTAL_ID"] = "12345678"
        if module.build_client() is None:
            fail("refused_with_both",
                 "build_client() returned None when BOTH the token and a portal id were present")
    finally:
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(saved)
        if home is not None:
            os.environ["HOME"] = home


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", default=".", type=Path)
    parser.add_argument("--owned", action="append", default=[])
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    sys.path.insert(0, str(worktree))
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
        print("u18_hubspot_reachable_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u18_hubspot_reachable_check: PASS — a bearer token is enough to attempt a read, no "
          "token is still refused, and supplying a portal id as well changes nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
