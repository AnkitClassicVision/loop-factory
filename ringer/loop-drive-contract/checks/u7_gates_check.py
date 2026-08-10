#!/usr/bin/env python3
"""U7-module executed check: four prose gates become executable checks.

Wave-1 scope (Fable plan, owner-signed 2026-08-10): a standalone module, NOT yet
wired into the runner — wiring is wave 2, which keeps this unit's file ownership
disjoint from U4's runner edit. The flagship-model gate is EXCLUDED (its inputs
are not recordable today; measured 2026-08-07).

Frozen module contract this check enforces (the spec restates it verbatim):

  File      server/pipeline/prose_gates.py (new; the unit's only tracked file
            besides its tests).
  CLI       python3 -m server.pipeline.prose_gates --gate <name> --input <json>
            exit 0 = PASS.
            exit 2 = BLOCK, stdout carries ONE json object:
                     {"gate", "violation", "offending_span", "fix_hint"}
                     all four values non-empty strings, gate == the one asked.
            exit 3 = unknown gate or unreadable/invalid input. Never silent.
  Gates     source_truth_resolved_before_intake:
                input {"manifest_path": str, "max_age_days": int}
                BLOCK when the manifest is missing, unparseable, has no
                generated_at, or generated_at is older than max_age_days.
                Fail closed: an unreadable source-truth packet is stale.
            channel_rule_cold_postcard_linkedin_warm_email_text:
                input {"candidate": {"alias", "temperature", "channel"}}
                BLOCK when temperature==cold and channel in {email, text};
                BLOCK unknown temperature or channel (fail closed);
                PASS cold+postcard, cold+linkedin, warm+anything known.
            neutralize_preexisting_nominated_before_draft:
                input {"candidate": {"alias", "podcast_status",
                                      "email_present", "cleared_by_human"}}
                BLOCK when podcast_status==nominated and email_present is true
                and cleared_by_human is not true (CMQA-001); else PASS.
            cross_model_qa_pass_before_done:
                input {"qa_file": str, "worker_model": str, "qa_model": str}
                BLOCK when the QA file is missing, its first line is not
                exactly 'QA: PASS', either model name is empty, or
                worker_model equals qa_model case-insensitively (self-graded
                QA is the exact failure this gate exists to catch).

Fixture values are RANDOMIZED per run (aliases, dates), so a module that
hardcodes fixture bytes cannot pass twice. Every failure prints WHY.

Usage: u7_gates_check.py --repo <candidate-tree> --out <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

FAILURES: list[str] = []


def fail(gate: str, why: str) -> None:
    FAILURES.append(f"CHECK FAIL ({gate}): {why}")


def run_gate(repo: Path, gate: str, payload: dict, out: Path, tag: str) -> tuple[int, str, str]:
    input_path = out / f"{gate}-{tag}.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "server.pipeline.prose_gates",
         "--gate", gate, "--input", str(input_path)],
        capture_output=True, text=True, timeout=60, cwd=str(repo),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(repo)},
    )
    return done.returncode, done.stdout, done.stderr


def expect_block(repo: Path, gate: str, payload: dict, out: Path, tag: str, why: str) -> None:
    rc, stdout, stderr = run_gate(repo, gate, payload, out, tag)
    if rc != 2:
        fail(gate, f"{why}: expected exit 2 BLOCK, got rc={rc}; stderr tail: {stderr[-200:]}")
        return
    try:
        verdict = json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        fail(gate, f"{why}: BLOCK stdout is not a JSON object: {stdout[-200:]!r}")
        return
    for key in ("gate", "violation", "offending_span", "fix_hint"):
        if not isinstance(verdict.get(key), str) or not verdict[key].strip():
            fail(gate, f"{why}: BLOCK json field {key!r} is missing or empty: {verdict}")
            return
    if verdict["gate"] != gate:
        fail(gate, f"{why}: BLOCK json names gate {verdict['gate']!r}, not the one invoked")


def expect_pass(repo: Path, gate: str, payload: dict, out: Path, tag: str, why: str) -> None:
    rc, stdout, stderr = run_gate(repo, gate, payload, out, tag)
    if rc != 0:
        fail(gate, f"{why}: expected exit 0 PASS, got rc={rc}; out: {stdout[-200:]!r} err: {stderr[-200:]!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    nonce = secrets.token_hex(4)
    now = datetime.now(timezone.utc)

    module = repo / "server/pipeline/prose_gates.py"
    if not module.is_file():
        print(f"CHECK FAIL: {module} does not exist — the U7 module was not built")
        return 1

    # Gate 1: source truth freshness (fail closed on every unreadable shape).
    gate = "source_truth_resolved_before_intake"
    fresh = out / f"manifest-fresh-{nonce}.json"
    fresh.write_text(json.dumps({
        "schema": "source-room-authority/v1",
        "generated_at": (now - timedelta(days=1)).isoformat(),
        "status": "resolved",
    }), encoding="utf-8")
    stale = out / f"manifest-stale-{nonce}.json"
    stale.write_text(json.dumps({
        "schema": "source-room-authority/v1",
        "generated_at": (now - timedelta(days=8)).isoformat(),
        "status": "resolved",
    }), encoding="utf-8")
    garbled = out / f"manifest-garbled-{nonce}.json"
    garbled.write_text("{not json", encoding="utf-8")
    expect_pass(repo, gate, {"manifest_path": str(fresh), "max_age_days": 7}, out, "pass",
                "a 1-day-old manifest inside a 7-day window must pass")
    expect_block(repo, gate, {"manifest_path": str(stale), "max_age_days": 7}, out, "stale",
                 "an 8-day-old manifest must block")
    expect_block(repo, gate, {"manifest_path": str(out / f"absent-{nonce}.json"), "max_age_days": 7},
                 out, "missing", "a missing manifest must block (fail closed)")
    expect_block(repo, gate, {"manifest_path": str(garbled), "max_age_days": 7}, out, "garbled",
                 "an unparseable manifest must block (fail closed)")

    # Gate 2: cold never gets email or text.
    gate = "channel_rule_cold_postcard_linkedin_warm_email_text"
    alias = f"cand-{nonce}"
    expect_block(repo, gate, {"candidate": {"alias": alias, "temperature": "cold", "channel": "email"}},
                 out, "cold-email", "cold + email must block")
    expect_block(repo, gate, {"candidate": {"alias": alias, "temperature": "cold", "channel": "text"}},
                 out, "cold-text", "cold + text must block")
    expect_block(repo, gate, {"candidate": {"alias": alias, "temperature": "lukewarm", "channel": "email"}},
                 out, "unknown-temp", "an unknown temperature must block (fail closed)")
    expect_pass(repo, gate, {"candidate": {"alias": alias, "temperature": "cold", "channel": "postcard"}},
                out, "cold-postcard", "cold + postcard is the allowed cold path")
    expect_pass(repo, gate, {"candidate": {"alias": alias, "temperature": "cold", "channel": "linkedin"}},
                out, "cold-linkedin", "cold + linkedin is the allowed cold path")
    expect_pass(repo, gate, {"candidate": {"alias": alias, "temperature": "warm", "channel": "email"}},
                out, "warm-email", "warm + email is allowed")

    # Gate 3: a pre-nominated candidate with a live email needs a human first.
    gate = "neutralize_preexisting_nominated_before_draft"
    expect_block(repo, gate, {"candidate": {"alias": alias, "podcast_status": "nominated",
                                            "email_present": True, "cleared_by_human": False}},
                 out, "nominated", "nominated + email present + not cleared must block (CMQA-001)")
    expect_pass(repo, gate, {"candidate": {"alias": alias, "podcast_status": "nominated",
                                           "email_present": True, "cleared_by_human": True}},
                out, "cleared", "a human-cleared nominated candidate may proceed")
    expect_pass(repo, gate, {"candidate": {"alias": alias, "podcast_status": "new_inbound",
                                           "email_present": True, "cleared_by_human": False}},
                out, "not-nominated", "a non-nominated candidate is not this gate's business")

    # Gate 4: cross-model QA actually passed, and was not self-graded.
    gate = "cross_model_qa_pass_before_done"
    qa_pass = out / f"qa-pass-{nonce}.md"
    qa_pass.write_text("QA: PASS\nreviewer notes\n", encoding="utf-8")
    qa_revise = out / f"qa-revise-{nonce}.md"
    qa_revise.write_text("QA: REVISE\nreviewer notes\n", encoding="utf-8")
    expect_pass(repo, gate, {"qa_file": str(qa_pass), "worker_model": "gpt-5.6-sol",
                             "qa_model": "claude-sonnet-5"}, out, "pass",
                "a PASS verdict from a different model must pass")
    expect_block(repo, gate, {"qa_file": str(qa_revise), "worker_model": "gpt-5.6-sol",
                              "qa_model": "claude-sonnet-5"}, out, "revise",
                 "a REVISE verdict must block")
    expect_block(repo, gate, {"qa_file": str(out / f"qa-absent-{nonce}.md"), "worker_model": "a",
                              "qa_model": "b"}, out, "missing", "a missing QA file must block")
    expect_block(repo, gate, {"qa_file": str(qa_pass), "worker_model": "gpt-5.6-sol",
                              "qa_model": "GPT-5.6-SOL"}, out, "self-graded",
                 "the SAME model grading itself must block — self-graded QA is the target failure")
    expect_block(repo, gate, {"qa_file": str(qa_pass), "worker_model": "", "qa_model": "x"},
                 out, "unknown-model", "an empty worker model must block (cross-model is unprovable)")

    # r8 review hardening: hostile JSON shapes must BLOCK cleanly, never crash.
    gate = "channel_rule_cold_postcard_linkedin_warm_email_text"
    expect_block(repo, gate, {"candidate": {"alias": alias, "temperature": [], "channel": "email"}},
                 out, "unhashable-temp", "an array temperature must block cleanly, not raise TypeError")
    expect_block(repo, gate, {"candidate": {"alias": alias, "temperature": "cold", "channel": {"a": 1}}},
                 out, "unhashable-channel", "an object channel must block cleanly, not raise TypeError")
    gate = "source_truth_resolved_before_intake"
    expect_block(repo, gate, {"manifest_path": "\x00", "max_age_days": 7}, out, "nul-path",
                 "a NUL-byte manifest path must block cleanly, not raise ValueError")
    gate = "cross_model_qa_pass_before_done"
    expect_block(repo, gate, {"qa_file": "\x00", "worker_model": "a", "qa_model": "b"},
                 out, "nul-qa-path", "a NUL-byte QA path must block cleanly, not raise ValueError")
    gate = "neutralize_preexisting_nominated_before_draft"
    expect_block(repo, gate, {"candidate": {"alias": alias, "podcast_status": "Nominated",
                                            "email_present": True, "cleared_by_human": False}},
                 out, "case-nominated", "status matching is case-insensitive: 'Nominated' must still block")
    expect_block(repo, gate, {"candidate": {"alias": alias, "podcast_status": 123,
                                            "email_present": True, "cleared_by_human": False}},
                 out, "nonstring-status", "a non-string podcast_status must block (fail closed)")

    # Contract edges: unknown gate and unreadable input are exit 3, never silent 0.
    rc, _, _ = run_gate(repo, "no_such_gate", {}, out, "unknown-gate")
    if rc != 3:
        fail("cli-contract", f"an unknown gate must exit 3, got rc={rc}")
    done = subprocess.run(
        [sys.executable, "-m", "server.pipeline.prose_gates",
         "--gate", "cross_model_qa_pass_before_done", "--input", str(out / f"nope-{nonce}.json")],
        capture_output=True, text=True, timeout=60, cwd=str(repo),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(repo)},
    )
    if done.returncode != 3:
        fail("cli-contract", f"an unreadable input file must exit 3, got rc={done.returncode}")

    if FAILURES:
        print("\n".join(FAILURES))
        print(f"u7_gates_check: {len(FAILURES)} failure(s)")
        return 1
    print("u7_gates_check: PASS — all four gates block their violation, pass their fixture, and fail closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
