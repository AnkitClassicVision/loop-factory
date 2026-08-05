#!/usr/bin/env python3
"""Independent oracle for the Approve/Respond button change in tg_approval.py.

Runs with cwd set to the podcast worktree. It imports the worker's refactored
pure builders and asserts the button contract directly, with no network and no
Telegram call.

The load-bearing assertion is the NEGATIVE one: --kind card is the live email
send-approval lane and must come back byte-identical in behaviour. The owner
chose to change both lanes in one pass; this is the containment that makes that
safe.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

FAILURES: list[str] = []
OWNED = ["scripts/tg_approval.py", "tests/test_tg_approval.py"]


def fail(message: str) -> None:
    FAILURES.append(message)


def load_module(repo: Path):
    path = repo / "scripts" / "tg_approval.py"
    if not path.exists():
        fail(f"{path} does not exist")
        return None
    spec = importlib.util.spec_from_file_location("tg_approval_under_test", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any import error is a real failure
        fail(f"importing scripts/tg_approval.py raised {type(exc).__name__}: {exc}")
        return None
    return module


def action_row(keyboard: dict) -> list[dict] | None:
    rows = keyboard.get("inline_keyboard") if isinstance(keyboard, dict) else None
    if not isinstance(rows, list) or not rows:
        fail(f"build_prompt must return a keyboard with an inline_keyboard list; got {keyboard!r}")
        return None
    return rows[0]


def callback_action(button: dict) -> str:
    data = button.get("callback_data", "")
    parts = data.split("|")
    return parts[1] if len(parts) > 1 else ""


def check_buttons(repo: Path) -> None:
    module = load_module(repo)
    if module is None:
        return
    if not hasattr(module, "build_prompt"):
        fail(
            "scripts/tg_approval.py must expose a pure build_prompt(kind, card, to, "
            "subject, preview, action_class, reescalation, first_raised) returning "
            "(text, keyboard) so the prompt can be verified without calling Telegram. "
            "cmd_prompt must call it rather than building the payload inline."
        )
        return

    def prompt(**kwargs):
        base = {
            "kind": "escalation",
            "card": "ANK-500",
            "to": "probe-loop",
            "subject": "probe subject",
            "preview": "probe preview body",
            "nonce": "n" * 32,
        }
        base.update(kwargs)
        try:
            return module.build_prompt(**base)
        except TypeError as exc:
            fail(f"build_prompt rejected the documented arguments: {exc}")
            return None, None

    # --- the negative assertion: the live email send lane must not move -------
    _, card_kb = prompt(kind="card")
    if card_kb is not None:
        row = action_row(card_kb)
        if row is not None:
            if len(row) != 3:
                fail(
                    "--kind card is the LIVE email send-approval lane and must keep "
                    f"its 3 buttons (Approve send / Fix / Skip); got {len(row)}: "
                    f"{[b.get('text') for b in row]}"
                )
            else:
                actions = [callback_action(b) for b in row]
                if actions != ["a", "f", "s"]:
                    fail(
                        "--kind card callback actions must stay ['a','f','s']; got "
                        f"{actions}. Changing the send lane was explicitly out of scope."
                    )

    # --- the change: escalation becomes Approve / Respond --------------------
    _, esc_kb = prompt(kind="escalation")
    if esc_kb is not None:
        row = action_row(esc_kb)
        if row is not None:
            texts = [str(b.get("text", "")) for b in row]
            if len(row) != 2:
                fail(
                    "--kind escalation must offer exactly 2 buttons, Approve and "
                    f"Respond; got {len(row)}: {texts}"
                )
            joined = " ".join(texts).lower()
            if "approve" not in joined:
                fail(f"the escalation keyboard has no Approve button; texts were {texts}")
            if "respond" not in joined:
                fail(f"the escalation keyboard has no Respond button; texts were {texts}")
            if "ack" in joined:
                fail(
                    "Acknowledge was removed by owner decision 2026-08-05: every tap "
                    f"is now a decision, Approve as-is or Respond. Texts were {texts}"
                )
            approve = next((b for b in row if "approve" in str(b.get("text", "")).lower()), None)
            respond = next((b for b in row if "respond" in str(b.get("text", "")).lower()), None)
            if approve is not None and callback_action(approve) != "a":
                fail(
                    "the escalation Approve button must carry callback action 'a' so it "
                    f"posts an APPROVE comment; got {callback_action(approve)!r}"
                )
            if respond is not None and callback_action(respond) != "f":
                fail(
                    "the escalation Respond button must carry callback action 'f' so it "
                    f"arms the existing force-reply path; got {callback_action(respond)!r}"
                )

    # --- action class: never external_send by accident ------------------------
    if hasattr(module, "build_comment"):
        try:
            default_body = module.build_comment("approve", "n" * 32, "2026-08-05T00:00:00+00:00")
        except TypeError as exc:
            fail(f"build_comment must keep working with its original 3 arguments: {exc}")
            default_body = ""
        first = str(default_body).splitlines()[0] if default_body else ""
        if first.strip() != "APPROVE proposal":
            fail(
                "with no action class supplied, an Approve tap must post 'APPROVE "
                "proposal', an inert class. It must NEVER default to external_send: "
                "that string is a real send authorization consumed by "
                f"open_engine_approved_send_executor.py. Got {first!r}"
            )
        try:
            explicit = module.build_comment(
                "approve", "n" * 32, "2026-08-05T00:00:00+00:00", action_class="external_send"
            )
            if str(explicit).splitlines()[0].strip() != "APPROVE external_send":
                fail(
                    "an explicitly passed action_class must still be honoured, so the "
                    "email send lane keeps working; got "
                    f"{str(explicit).splitlines()[0]!r}"
                )
        except TypeError as exc:
            fail(f"build_comment must accept an action_class keyword: {exc}")
    else:
        fail("scripts/tg_approval.py no longer exposes build_comment")

    # --- re-escalation header -------------------------------------------------
    text, _ = prompt(reescalation=4, first_raised="2026-08-01T09:00:00+00:00")
    if text is not None:
        lowered = str(text).lower()
        if "re-escalation" not in lowered and "reescalation" not in lowered:
            fail(
                "a re-escalated prompt must say so in the text so the owner knows this "
                f"is not a new item; got: {str(text)[:300]!r}"
            )
        if "4" not in str(text):
            fail("the re-escalation count must appear in the prompt text")
        if "2026-08-01" not in str(text):
            fail("the first-raised date must appear so the owner sees how long it has waited")

    first_text, _ = prompt()
    if first_text is not None and (
        "re-escalation" in str(first_text).lower() or "reescalation" in str(first_text).lower()
    ):
        fail("a FIRST prompt must not claim to be a re-escalation")


def check_suite(repo: Path) -> None:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PLACEHOLDER_MODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_tg_approval.py", "tests/test_obe_tg_notify.py", "-q", "-p", "no:cacheprovider"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
    )
    if result.returncode != 0:
        fail(f"the podcast tg tests failed (exit {result.returncode}):\n{(result.stdout or '')[-1500:]}")


def check_ownership_and_export(repo: Path, export: Path, notes: str | None) -> None:
    guarded = ("scripts/", "tests/", "server/")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)
    touched = {line[3:].strip() for line in status.stdout.splitlines() if line[3:].strip()}
    stray = sorted(p for p in touched if p not in OWNED and p.startswith(guarded))
    if stray:
        fail(f"you own only {OWNED}, but git status also shows {stray}. Revert them.")
    if notes:
        source = repo / "notes.md"
        if not source.exists():
            fail("./notes.md was not written at the worktree root; it is required")
        else:
            target = Path(notes)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    subprocess.run(["git", "add", "--"] + OWNED, cwd=repo, capture_output=True, text=True)
    diff = subprocess.run(["git", "diff", "--cached"], cwd=repo, capture_output=True, text=True)
    export.mkdir(parents=True, exist_ok=True)
    (export / "buttons.patch").write_text(diff.stdout, encoding="utf-8")
    if not diff.stdout.strip():
        fail("the exported patch is empty; a passing worktree is deleted so the work would be lost")
    else:
        print(f"exported {len(diff.stdout.splitlines())} patch lines")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--export", required=True)
    parser.add_argument("--notes")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()

    check_buttons(repo)
    check_suite(repo)
    check_ownership_and_export(repo, Path(args.export).resolve(), args.notes)

    if FAILURES:
        print(f"\nCHECK FAILED — {len(FAILURES)} problem(s):\n")
        for index, message in enumerate(FAILURES, start=1):
            print(f"{index}. {message}\n")
        return 1
    print("CHECK PASS: escalation is Approve/Respond, the email send lane is unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
