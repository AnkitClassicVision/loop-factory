"""Apply deterministic sales qualification bars to received subjects."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from factory import runrecord
from factory.events_ledger import append_event


LANES = ("icaregrow", "podcast_handoffs", "pfs_warm", "website_forms", "luma")
ALLOWED_ROLES = {"owner", "decision_maker"}


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _events(state_dir: Path) -> list[dict]:
    path = state_dir / "events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except (ValueError, TypeError):
            continue
    return rows


def _source_index(state_dir: Path, salt: str) -> dict[str, tuple[str, dict]]:
    result: dict[str, tuple[str, dict]] = {}
    for lane in LANES:
        path = state_dir / "sources" / f"{lane}.json"
        if not path.is_file():
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            email = str(row.get("email") or "").strip().lower()
            if email:
                subject_id = hashlib.sha256((salt + email).encode()).hexdigest()[:16]
                result.setdefault(subject_id, (lane, row))
    return result


def _existing_receipt_subjects(path: Path) -> set[str]:
    if not path.exists():
        return set()
    found = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            found.add(json.loads(line)["subject_id"])
        except (ValueError, TypeError, KeyError):
            continue
    return found


def _work(state_dir: Path) -> list[dict]:
    salt = (state_dir / "sources" / ".id_salt").read_text(encoding="utf-8")
    if not salt:
        raise ValueError("id salt must not be empty")
    events = _events(state_dir)
    received = {row["subject_id"] for row in events if row.get("to_stage") == "received"}
    qualified = {row["subject_id"] for row in events if row.get("to_stage") == "qualified"}
    already_parked = _existing_receipt_subjects(state_dir / "parked_out.jsonl")
    sources = _source_index(state_dir, salt)
    qualified_count = parked_count = skipped_count = 0
    now = datetime.now(timezone.utc).isoformat()
    for subject_id in sorted(received - qualified):
        match = sources.get(subject_id)
        if match is None:
            if subject_id not in already_parked:
                _append(state_dir / "parked_out.jsonl", {"subject_id": subject_id, "reason": "source row unavailable", "ts": now})
                parked_count += 1
            else:
                skipped_count += 1
            continue
        lane, row = match
        role = str(row.get("role") or "").strip().lower()
        seller = lane.startswith("pfs_")
        signal_name = "exit_intent" if seller else "icp_fit"
        signal = row.get(signal_name) is True
        bar = "seller" if seller else "services"
        evidence = {"role": role, signal_name: signal}
        if role in ALLOWED_ROLES and signal:
            append_event(state_dir, subject_id=subject_id, from_stage="received", to_stage="qualified", ts=now, meta={"source": lane})
            _append(state_dir / "qualifications.jsonl", {"subject_id": subject_id, "bar": bar, "evidence": evidence, "ts": now})
            qualified_count += 1
        elif subject_id not in already_parked:
            reasons = []
            if role not in ALLOWED_ROLES:
                reasons.append("role below bar")
            if not signal:
                reasons.append(f"{signal_name} false")
            _append(state_dir / "parked_out.jsonl", {"subject_id": subject_id, "reason": "; ".join(reasons), "ts": now})
            parked_count += 1
        else:
            skipped_count += 1
    observation = {
        "ts": now,
        "sensor": "qualify",
        "subject": "sales-qualification",
        "status": "ok",
        "evidence": "deterministic-bars",
        "detail": f"qualified {qualified_count}; parked {parked_count}; skipped {skipped_count}",
        "metrics": {"qualified": qualified_count, "parked": parked_count, "skipped": skipped_count},
    }
    _append(state_dir / "observations.jsonl", observation)
    return [observation]


def _emit(state_dir: Path, started: float, status: str, errors: list[str]) -> None:
    runrecord.emit_record(
        state_dir,
        department="sales",
        node="qualify_scorer",
        status=status,
        release=runrecord.read_release(state_dir.parent),
        trigger={"kind": "time", "id": "qualify_scorer", "dedupe_key": f"{datetime.now(timezone.utc).date()}-qualify_scorer"},
        duration_ms=int((time.perf_counter() - started) * 1000),
        errors=errors,
        artifacts=[str(path) for path in (state_dir / "events.jsonl", state_dir / "qualifications.jsonl", state_dir / "parked_out.jsonl", state_dir / "observations.jsonl") if path.exists()],
        external_actions_taken=0,
    )


def run(state_dir: Path) -> list[dict]:
    state_dir = Path(state_dir)
    started = time.perf_counter()
    try:
        observations = _work(state_dir)
    except Exception as exc:
        _emit(state_dir, started, "error", [type(exc).__name__])
        raise
    _emit(state_dir, started, "ok", [])
    return observations


def main() -> int:
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=repo / "departments/sales/state")
    parser.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    run(args.state_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
