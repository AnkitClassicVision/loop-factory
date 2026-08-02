"""Supervise the podcast pipeline's exported DAG projection without scheduling it."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory import runrecord
from departments.podcast.runtime import record as record_node


SCHEMA = "dag-projection-v1"
DEFAULT_STATE_DIR = REPO_ROOT / "departments" / "podcast" / "state"
STALE_AFTER = timedelta(hours=48)
LOGGER = logging.getLogger(__name__)


def _canonical_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _finding(
    kind: str,
    detail: str,
    severity: str,
    *,
    episode_id: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "episode_id": episode_id,
        "detail": detail,
        "severity": severity,
    }


def _parse_iso8601(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp is absent")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no UTC offset")
    return parsed.astimezone(timezone.utc)


def validate_projection(projection: dict, *, now: datetime) -> list[dict]:
    """Return deterministic findings for one frozen pipeline DAG projection."""
    if projection.get("schema") != SCHEMA:
        return [
            _finding(
                "schema_mismatch",
                f"expected schema {SCHEMA!r}, got {projection.get('schema')!r}",
                "critical",
            )
        ]

    findings: list[dict] = []
    steps = projection.get("steps")
    try:
        actual_dag_hash = _canonical_hash(steps)
    except (TypeError, ValueError):
        actual_dag_hash = "unrecomputable"
    expected_dag_hash = projection.get("dag_hash")
    if actual_dag_hash != expected_dag_hash:
        findings.append(
            _finding(
                "dag_hash_mismatch",
                f"declared dag_hash {expected_dag_hash!r} does not match recomputed {actual_dag_hash!r}",
                "critical",
            )
        )

    try:
        generated_at = _parse_iso8601(projection.get("generated_at"))
        comparison_now = now.astimezone(timezone.utc)
        stale = comparison_now - generated_at > STALE_AFTER
    except (AttributeError, TypeError, ValueError):
        stale = True
    if stale:
        findings.append(
            _finding(
                "stale_projection",
                f"generated_at {projection.get('generated_at')!r} is unparseable or older than 48 hours",
                "high",
            )
        )

    episodes = projection.get("episodes", [])
    if not isinstance(episodes, list):
        episodes = []
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        raw_episode_id = episode.get("episode_id")
        episode_id = raw_episode_id if isinstance(raw_episode_id, str) else None
        audit = episode.get("audit")
        if not isinstance(audit, dict):
            audit = {}

        silent_skips = audit.get("silent_skips")
        if isinstance(silent_skips, list) and silent_skips:
            findings.append(
                _finding(
                    "silent_skip",
                    f"audit reports silent skips: {json.dumps(silent_skips, sort_keys=True)}",
                    "critical",
                    episode_id=episode_id,
                )
            )

        invalid_skips = audit.get("invalid_skips")
        if isinstance(invalid_skips, list) and invalid_skips:
            findings.append(
                _finding(
                    "invalid_skip_artifact",
                    f"audit reports invalid skips: {json.dumps(invalid_skips, sort_keys=True)}",
                    "critical",
                    episode_id=episode_id,
                )
            )

        skip_artifacts = episode.get("skip_artifacts")
        if isinstance(skip_artifacts, dict):
            for step_id in sorted(skip_artifacts):
                artifact = skip_artifacts[step_id]
                declared_hash = None
                recomputed_hash = "unrecomputable"
                if isinstance(artifact, dict):
                    hash_material = dict(artifact)
                    declared_hash = hash_material.pop("content_hash", None)
                    try:
                        recomputed_hash = _canonical_hash(hash_material)
                    except (TypeError, ValueError):
                        pass
                if declared_hash != recomputed_hash:
                    findings.append(
                        _finding(
                            "invalid_skip_artifact",
                            f"skip artifact for step {step_id!r} has content_hash {declared_hash!r}; recomputed {recomputed_hash!r}",
                            "critical",
                            episode_id=episode_id,
                        )
                    )

        if episode.get("stage") == "unreadable":
            findings.append(
                _finding(
                    "unreadable_episode",
                    "pipeline projection marks the episode stage unreadable",
                    "critical",
                    episode_id=episode_id,
                )
            )
    return findings


def _append_observation(state_dir: Path, observation: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, sort_keys=True) + "\n")


def _load_candidates(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("incident_candidates.json must contain a list")
    return [row for row in value if isinstance(row, dict)]


def _incident_candidate(
    finding: dict[str, Any], *, timestamp: str, evidence: str
) -> dict[str, Any]:
    return {
        "ts": timestamp,
        "sensor": "dag_supervisor",
        "subject": finding.get("episode_id") or "dag-projection",
        "failure_class": finding["kind"],
        "severity": "critical",
        "setpoint": "fresh, content-verified dag-projection-v1 with no silent or invalid skips",
        "observed": finding["detail"],
        "evidence": [evidence],
        "one_question": "What pipeline-owned correction will restore a trustworthy DAG projection?",
    }


def _sense(
    projection_path: str | Path,
    state_dir: str | Path,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Observe one projection receipt and write only supervisory department state."""
    projection_path = Path(projection_path)
    state_dir = Path(state_dir)
    projection: dict[str, Any] | None = None
    try:
        loaded = json.loads(projection_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("projection root must be an object")
        projection = loaded
        findings = validate_projection(projection, now=now)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        findings = [
            _finding(
                "projection_missing",
                f"projection is missing or unreadable: {type(exc).__name__}: {exc}",
                "critical",
            )
        ]

    dag_hash = projection.get("dag_hash") if projection is not None else None
    dag_hash_prefix = str(dag_hash)[:12] if dag_hash else "unavailable"
    evidence = f"{projection_path} dag_hash={dag_hash_prefix}"
    metrics = dict(sorted(Counter(row["kind"] for row in findings).items()))
    status = "ok" if not findings else "alarm"
    timestamp = now.astimezone(timezone.utc).isoformat()
    observation = {
        "ts": timestamp,
        "sensor": "dag_supervisor",
        "subject": "pipeline-dag-projection",
        "status": status,
        "evidence": evidence,
        "detail": "projection healthy" if not findings else f"{len(findings)} projection finding(s)",
        "metrics": metrics,
    }

    critical = [row for row in findings if row["severity"] == "critical"]
    with record_node.records_lock(state_dir):
        _append_observation(state_dir, observation)
        if critical:
            candidates_path = state_dir / "incident_candidates.json"
            candidates = _load_candidates(candidates_path)
            candidates.extend(
                _incident_candidate(row, timestamp=timestamp, evidence=evidence)
                for row in critical
            )
            record_node.atomic_write_json(candidates_path, candidates)

    return {
        "status": status,
        "findings": findings,
        "observation": observation,
        "incident_candidates_appended": len(critical),
    }


def _emit_run_record(
    state_dir: Path,
    *,
    started: float,
    status: str,
    errors: list[str],
    artifacts: list[Path],
) -> None:
    try:
        runrecord.emit_record(
            state_dir,
            department="podcast",
            node="dag_supervisor",
            status=status,
            release=runrecord.read_release(state_dir.parent),
            trigger={
                "kind": "time",
                "id": "podcast-daily",
                "dedupe_key": (
                    f"{datetime.now(timezone.utc).date().isoformat()}-dag_supervisor"
                ),
            },
            duration_ms=int((time.perf_counter() - started) * 1000),
            errors=errors,
            artifacts=[str(path) for path in artifacts if path.exists()],
            external_actions_taken=0,
        )
    except Exception:
        LOGGER.exception("dag_supervisor failed to append its runs-v2 record")
        raise


def sense(
    projection_path: str | Path,
    state_dir: str | Path,
    *,
    now: datetime,
) -> dict[str, Any]:
    state_path = Path(state_dir)
    started = time.perf_counter()
    observation_path = state_path / "observations.jsonl"
    try:
        result = _sense(projection_path, state_path, now=now)
    except Exception as exc:
        _emit_run_record(
            state_path,
            started=started,
            status="error",
            errors=[type(exc).__name__],
            artifacts=[observation_path],
        )
        raise
    errors = [str(finding["kind"]) for finding in result["findings"]]
    artifacts = [observation_path]
    if result["incident_candidates_appended"]:
        artifacts.append(state_path / "incident_candidates.json")
    _emit_run_record(
        state_path,
        started=started,
        status="error" if errors else "ok",
        errors=errors,
        artifacts=artifacts,
    )
    return result


def _arg_now(value: str) -> datetime:
    try:
        return _parse_iso8601(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the pipeline-owned podcast DAG projection"
    )
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--now", type=_arg_now, default=None)
    args = parser.parse_args(argv)
    result = sense(
        args.projection,
        args.state_dir,
        now=args.now or datetime.now(timezone.utc),
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
