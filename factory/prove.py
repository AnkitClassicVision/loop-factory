"""Stage 11 post-integration proof runner.

The runner executes a fixed registry of adversarial drills, writes a Markdown
verification report and a machine-readable JSON bundle, and exits successfully
only when every supported drill passes.  Unsupported Stage 11 contracts remain
visible failures unless the caller explicitly supplies ``--allow-unsupported``.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from factory import boardfeed, human_in_the_loop, manager, release, rollup, runner, runrecord, scaffold
from kernel.receipts import LocalSigner


REPORT_VERSION = "proof-bundle/v1"
STAGE11_DRILLS = (
    "duplicate-trigger dedupe",
    "auth block",
    "record-write failure blocks advancement",
    "worker kill",
    "evaluator rejection",
    "objective breach surfaces",
    "escalation delivery",
    "receipt rebuild",
    "board truth",
    "drift",
    "zero-external-effects",
)


@dataclass(frozen=True)
class ProofContext:
    name: str
    departments_root: Path
    department: Path


def _result(name: str, passed: bool, evidence: str) -> dict:
    return {"name": name, "pass": bool(passed), "evidence": evidence}


def _unsupported(name: str, why: str) -> dict:
    return _result(name, False, f"unsupported: {why}")


def _node(node_id: str, impl: str) -> dict:
    schema = {
        "type": "object",
        "required": ["status"],
        "properties": {"status": {"type": "string"}},
    }
    return {
        "id": node_id,
        "impl": impl,
        "runtime_mode": "script",
        "action_class": "observe",
        "inputs": {"type": "object"},
        "outputs": schema,
        "receipt_schema": schema,
        "failure_policy": {"max_retries": 0, "backoff_s": 0, "on_fail": "escalate"},
        "concept_ref": "PROOF-C1",
        "interview_ref": "PROOF-Q1",
    }


def _make_runner_probe(root: Path) -> Path:
    scaffold.scaffold_department("proofprobe", root=root, owner="proof-owner")
    dept = root / "departments" / "proofprobe"
    marker_script = (
        "import json, pathlib\n"
        "state = pathlib.Path(__file__).resolve().parents[1] / 'state'\n"
        "with (state / 'proof-marker.txt').open('a', encoding='utf-8') as handle:\n"
        "    handle.write('executed\\n')\n"
        "print(json.dumps({'status': 'ok', 'delivered_count': 0}))\n"
    )
    (dept / "runtime" / "proof_node.py").write_text(marker_script, encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "subgraphs": [{
            "id": "SG-PROOF",
            "concept_refs": ["PROOF-C1"],
            "entry": "N1",
            "nodes": [_node("N1", "runtime/proof_node.py")],
            "edges": [{"from": "N1", "kind": "terminal", "when": "true"}],
        }],
    }
    (dept / "subgraphs.json").write_text(json.dumps(manifest), encoding="utf-8")
    digest = release.pin_release(dept, dept / "releases", source_ref="proof-probe")
    release.flip_current(dept / "releases", digest)
    return dept


def drill_duplicate_trigger(ctx: ProofContext) -> dict:
    name = "duplicate-trigger dedupe"
    try:
        with tempfile.TemporaryDirectory(prefix="loop-factory-proof-") as raw:
            root = Path(raw)
            dept = _make_runner_probe(root)
            signer = LocalSigner(key="ephemeral-proof-signer")
            first = runner.run_graph(
                dept, trigger_fingerprint="same-proof-trigger", signer=signer,
                root=root, sleep_fn=lambda _seconds: None,
            )
            second = runner.run_graph(
                dept, trigger_fingerprint="same-proof-trigger", signer=signer,
                root=root, sleep_fn=lambda _seconds: None,
            )
            marker_count = len((dept / "state" / "proof-marker.txt").read_text(
                encoding="utf-8").splitlines())
            passed = (
                first["run_id"] == second["run_id"]
                and second.get("duplicate") is True
                and marker_count == 1
            )
            return _result(name, passed, f"same_run_id={first['run_id'] == second['run_id']}; duplicate={second.get('duplicate')}; executions={marker_count}")
    except Exception as exc:
        return _result(name, False, f"{type(exc).__name__}: {exc}")


def drill_record_write_failure(ctx: ProofContext) -> dict:
    name = "record-write failure blocks advancement"
    try:
        with tempfile.TemporaryDirectory(prefix="loop-factory-proof-") as raw:
            state = Path(raw) / "state"
            state.mkdir()
            (state / "runs-v2.jsonl").mkdir()  # deterministic write failure, even as root
            advanced = False
            blocked = False
            try:
                runrecord.emit_record(
                    state, department=ctx.name, node="proof-record",
                    status="ok", external_actions_taken=0,
                )
                advanced = True
            except (OSError, IsADirectoryError):
                blocked = True
            return _result(name, blocked and not advanced, f"record_error={blocked}; advanced={advanced}")
    except Exception as exc:
        return _result(name, False, f"{type(exc).__name__}: {exc}")


def drill_objective_breach(ctx: ProofContext) -> dict:
    name = "objective breach surfaces"
    try:
        with tempfile.TemporaryDirectory(prefix="loop-factory-proof-") as raw:
            root = Path(raw)
            scaffold.scaffold_department("proofprobe", root=root, owner="proof-owner")
            dept = root / "departments" / "proofprobe"
            (dept / "charter.yaml").write_text(
                "department: proofprobe\nowner: proof-owner\nautonomy_state: shadow\n"
                "immutable_safety_invariants:\n  heal_may_not_modify: [autonomy_state]\n"
                "setpoints:\n  objectives:\n    proof_quality:\n"
                "      label: Proof quality\n      minimum: 80\n      target: 100\n      unit: percent\n",
                encoding="utf-8",
            )
            (dept / "state" / "objectives_observed.json").write_text(json.dumps({
                "schema": "objectives-observed/v1",
                "ts": datetime.now(timezone.utc).isoformat(),
                "values": {"proof_quality": 79},
            }), encoding="utf-8")
            manager.run_manager_cycle(
                state_dir=dept / "state",
                autonomy_state="shadow",
                escalate_fn=lambda _issue, context=None: None,
            )
            rollup.rebuild(root)
            feed = root / "proof-feed.ndjson"
            boardfeed.build_feed(root, out=feed)
            rows = [json.loads(line) for line in feed.read_text(encoding="utf-8").splitlines()]
            breaches = [row for row in rows if row.get("kind") == "andon" and row.get("data", {}).get("code") == "OBJECTIVE_BELOW_MIN"]
            return _result(name, len(breaches) == 1, f"OBJECTIVE_BELOW_MIN rows={len(breaches)}")
    except Exception as exc:
        return _result(name, False, f"{type(exc).__name__}: {exc}")


def drill_escalation_delivery(ctx: ProofContext) -> dict:
    name = "escalation delivery"
    try:
        with tempfile.TemporaryDirectory(prefix="loop-factory-proof-") as raw:
            outbox = Path(raw) / "human-outbox.jsonl"
            result = human_in_the_loop.escalate(ctx.name, "stage11-proof", outbox)
            rows = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
            delivered = result.get("escalated") is True and len(rows) == 1 and rows[0].get("issue") == "stage11-proof"
            return _result(name, delivered, f"escalated={result.get('escalated')}; outbox_rows={len(rows)}")
    except Exception as exc:
        return _result(name, False, f"{type(exc).__name__}: {exc}")


def drill_receipt_rebuild(ctx: ProofContext) -> dict:
    name = "receipt rebuild"
    try:
        with tempfile.TemporaryDirectory(prefix="loop-factory-proof-") as raw:
            state = Path(raw) / "state"
            for index in range(2):
                runrecord.emit_record(state, department=ctx.name, node=f"proof-{index}", status="ok", external_actions_taken=0)
            path = state / "runs-v2.jsonl"
            first = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            rebuilt = [runrecord.validate_record(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines()]
            passed = len(first) == len(rebuilt) == 2 and [r["run_id"] for r in first] == [r["run_id"] for r in rebuilt]
            return _result(name, passed, f"written={len(first)}; rebuilt={len(rebuilt)}; identities_preserved={passed}")
    except Exception as exc:
        return _result(name, False, f"{type(exc).__name__}: {exc}")


def drill_zero_external_effects(ctx: ProofContext) -> dict:
    name = "zero-external-effects"
    try:
        with tempfile.TemporaryDirectory(prefix="loop-factory-proof-") as raw:
            state = Path(raw) / "state"
            runrecord.emit_record(state, department=ctx.name, node="proof-shadow", status="ok", external_actions_taken=0)
            rows = [json.loads(line) for line in (state / "runs-v2.jsonl").read_text(encoding="utf-8").splitlines()]
            counts = [row.get("external_actions_taken") for row in rows]
            return _result(name, bool(rows) and all(value == 0 for value in counts), f"records={len(rows)}; external_actions_taken={counts}")
    except Exception as exc:
        return _result(name, False, f"{type(exc).__name__}: {exc}")


DRILLS: tuple[Callable[[ProofContext], dict], ...] = (
    drill_duplicate_trigger,
    lambda _ctx: _unsupported("auth block", "no Stage 11 auth fixture contract exists"),
    drill_record_write_failure,
    lambda _ctx: _unsupported("worker kill", "no bounded worker-kill fixture is exposed by the scaffold"),
    lambda _ctx: _unsupported("evaluator rejection", "the scaffold has an eval registry but no executable evaluator fixture"),
    drill_objective_breach,
    drill_escalation_delivery,
    drill_receipt_rebuild,
    lambda _ctx: _unsupported("board truth", "no canonical Stage 11 board-truth assertion is defined"),
    lambda _ctx: _unsupported("drift", "no non-mutating drift injection API exists"),
    drill_zero_external_effects,
)


def _pinned_release(department: Path) -> str:
    current = department / "releases" / "current"
    try:
        value = current.read_text(encoding="utf-8").strip()
        return value or "unpinned"
    except OSError:
        return "unpinned"


def run_proof(name: str, departments_root: Path | str, *, allow_unsupported: bool = False, now: datetime | None = None) -> dict:
    departments_root = Path(departments_root)
    department = departments_root / name
    if not department.is_dir():
        raise FileNotFoundError(f"department not found: {department}")
    ctx = ProofContext(name=name, departments_root=departments_root, department=department)
    results = [drill(ctx) for drill in DRILLS]
    unsupported = [row for row in results if row["evidence"].startswith("unsupported:")]
    effective_pass = all(row["pass"] or (allow_unsupported and row in unsupported) for row in results)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    proof_dir = departments_root.parent / "proof"
    proof_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{name}-verification-{stamp}"
    bundle_path = proof_dir / f"{stem}.json"
    report_path = proof_dir / f"{stem}.md"
    bundle = {
        "schema": REPORT_VERSION,
        "department": name,
        "pinned_release": _pinned_release(department),
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "allow_unsupported": allow_unsupported,
        "pass": effective_pass,
        "drills": results,
    }
    bundle_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        f"# Stage 11 verification: {name}", "",
        f"- Schema: `{REPORT_VERSION}`",
        f"- Pinned release: `{bundle['pinned_release']}`",
        f"- Verdict: **{'PASS' if effective_pass else 'FAIL'}**", "",
        "| Drill | Result | Evidence |", "|---|---:|---|",
    ]
    for row in results:
        status = "PASS" if row["pass"] else ("ALLOWED UNSUPPORTED" if allow_unsupported and row in unsupported else "FAIL")
        evidence = str(row["evidence"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {row['name']} | {status} | {evidence} |")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bundle["report_path"] = str(report_path)
    bundle["bundle_path"] = str(bundle_path)
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Stage 11 department proof gate")
    parser.add_argument("--name", required=True)
    parser.add_argument("--departments-root", type=Path, default=Path("departments"))
    parser.add_argument("--allow-unsupported", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_proof(args.name, args.departments_root, allow_unsupported=args.allow_unsupported)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"pass": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
