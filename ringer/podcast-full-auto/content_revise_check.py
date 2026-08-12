#!/usr/bin/env python3
"""Receipt for the content-QA auto-revise loop.

Fred E104 parked at awaiting-host-input with content QA showing NO hard findings
and NO style findings: it failed only on the voice judge (voice_match 0.72,
hook_quality 0.60, floor 0.80) with 13 specific critiques. Artwork generation,
publish preflight and publish-queued all sit AFTER qa-passed in the orchestrator,
so that one park blocked every remaining deliverable.

The outreach lane already auto-revises against voice QA
(scripts/obe_draft_voice_qa.py). Episode content had no such loop.

This proves the loop exists and is bounded:
  1. content that fails only on scores is regenerated and re-scored, and a pass
     on a later attempt advances the episode,
  2. the QA critiques are actually fed into the regeneration (not a blind retry),
  3. attempts are bounded and a persistent failure still parks with the
     attempts recorded,
  4. HARD findings are never auto-revised: they park immediately.

Prints WHY on failure.
"""
import os
import subprocess
import sys

REPO = "/mnt/d_drive/repos/podcast"


def fail(msg):
    print(f"FAIL {msg}")
    return 1


def main() -> int:
    sys.path.insert(0, REPO)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    from server.pipeline import orchestrator as orch_mod  # noqa: E402

    if not hasattr(orch_mod, "CONTENT_QA_MAX_REVISIONS"):
        return fail("orchestrator has no CONTENT_QA_MAX_REVISIONS bound — an unbounded "
                    "revise loop can burn the model budget forever")
    max_rev = orch_mod.CONTENT_QA_MAX_REVISIONS
    if not isinstance(max_rev, int) or not 1 <= max_rev <= 5:
        return fail(f"CONTENT_QA_MAX_REVISIONS={max_rev!r} is not a sane small bound")

    cls = orch_mod.PipelineOrchestrator
    if not hasattr(cls, "_revise_content_until_qa_passes"):
        return fail("PipelineOrchestrator._revise_content_until_qa_passes is missing")

    orch = cls.__new__(cls)

    # --- 1. score-only failure gets revised, and the critiques are passed in ---
    calls = {"generate": 0, "qa": 0, "problems_seen": []}

    def fake_generate(*, problems=None, **kwargs):
        calls["generate"] += 1
        calls["problems_seen"].append(list(problems or []))
        return {"title": f"attempt {calls['generate']}", "posts": []}

    def fake_qa(content, **kwargs):
        calls["qa"] += 1
        passed = calls["qa"] >= 2  # fails once, then passes
        return {"passed": passed, "hard_findings": [], "style_findings": [],
                "llm_verdict": {"voice_match": 0.72 if not passed else 0.91,
                                "hook_quality": 0.60 if not passed else 0.85,
                                "problems": ["no contractions", "reused hook"]},
                "confidence_floor": 0.8}

    first = fake_qa({}, )
    result = orch._revise_content_until_qa_passes(
        content={"title": "original", "posts": []},
        qa_result=first,
        regenerate=fake_generate,
        run_qa=fake_qa,
    )
    if not isinstance(result, dict):
        return fail(f"_revise_content_until_qa_passes returned {type(result).__name__}, expected dict")
    if not (result.get("qa") or {}).get("passed"):
        return fail("a score-only failure that would pass on retry did NOT reach a passing QA")
    if calls["generate"] < 1:
        return fail("content was never regenerated — the loop is a no-op")
    if not any(calls["problems_seen"]):
        return fail("the QA critiques were never passed into regeneration — this is a blind "
                    "retry, which will reproduce the same voice failure")
    print(f"score-only failure: regenerated {calls['generate']}x, "
          f"critiques fed in: {bool(any(calls['problems_seen']))}, final passed=True")

    # --- 2. persistent failure is bounded and parks ---
    calls2 = {"generate": 0}

    def always_fail_qa(content, **kwargs):
        return {"passed": False, "hard_findings": [], "style_findings": [],
                "llm_verdict": {"voice_match": 0.5, "hook_quality": 0.4,
                                "problems": ["still off voice"]},
                "confidence_floor": 0.8}

    def counting_generate(*, problems=None, **kwargs):
        calls2["generate"] += 1
        return {"title": "again", "posts": []}

    persistent = orch._revise_content_until_qa_passes(
        content={"title": "original", "posts": []},
        qa_result=always_fail_qa({}),
        regenerate=counting_generate,
        run_qa=always_fail_qa,
    )
    if (persistent.get("qa") or {}).get("passed"):
        return fail("a persistently failing QA reported passed — the gate was defeated")
    if calls2["generate"] > max_rev:
        return fail(f"regenerated {calls2['generate']}x against a bound of {max_rev}")
    attempts = persistent.get("attempts")
    if not isinstance(attempts, list) or len(attempts) < 1:
        return fail("the attempts were not recorded, so a human cannot see what was tried")
    print(f"persistent failure: bounded at {calls2['generate']} regenerations "
          f"(max {max_rev}), attempts recorded={len(attempts)}, passed=False")

    # --- 3. hard findings must never be auto-revised ---
    calls3 = {"generate": 0}

    def hard_qa(content, **kwargs):
        return {"passed": False, "hard_findings": ["unresolved {{placeholder}}"],
                "style_findings": [], "llm_verdict": {}, "confidence_floor": 0.8}

    def hard_generate(*, problems=None, **kwargs):
        calls3["generate"] += 1
        return {"title": "nope", "posts": []}

    hard = orch._revise_content_until_qa_passes(
        content={"title": "original", "posts": []},
        qa_result=hard_qa({}),
        regenerate=hard_generate,
        run_qa=hard_qa,
    )
    if calls3["generate"] != 0:
        return fail(f"a HARD finding was auto-revised ({calls3['generate']} regenerations) — "
                    f"placeholder/banned-phrase/bad-link classes must park, not be rewritten")
    if (hard.get("qa") or {}).get("passed"):
        return fail("a hard finding reported passed")
    print("hard findings: 0 regenerations, parked (correct)")

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX="/tmp")
    pt = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_content_qa.py", "tests/test_orchestrator.py",
         "-q", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, env=env, timeout=2400)
    print((pt.stdout or "")[-1200:])
    if pt.returncode != 0:
        return fail(f"content-qa / orchestrator tests failed (rc={pt.returncode})")

    print("RESULT: PASS — score-only failures are revised with the critiques fed back, "
          "the loop is bounded and parks with attempts recorded, and hard findings never revise")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
