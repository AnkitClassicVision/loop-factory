"""Read-only shadow conductor: sense state and append a decision ledger."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from factory.charter_loader import CharterError, load_charter, thresholds
from kernel.lease import LeaseHeld, acquire, refusal_receipt


def _utc(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temp_name = handle.name
            json.dump(value, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
            rows.append(row)
    return rows


def _node(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        for key in ("node", "name", "id"):
            if isinstance(value.get(key), str) and value[key]:
                return value[key]
    return None


def _manifest(state: Path) -> tuple[str | None, list[tuple[dict, datetime]]]:
    directory = state / "run-manifests"
    if not directory.exists():
        return None, []
    manifests = [path for path in directory.glob("*.json") if not path.name.endswith(".verdict.json")]
    if not manifests:
        return None, []
    newest = max(manifests, key=lambda path: (path.stat().st_mtime, path.name))
    manifest = json.loads(newest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest is not an object")
    run_id = manifest.get("run_id") or newest.stem
    if not isinstance(run_id, str):
        raise ValueError("run id is not a string")
    verdict = json.loads(newest.with_name(f"{newest.stem}.verdict.json").read_text(encoding="utf-8"))
    if not isinstance(verdict, dict):
        raise ValueError("verdict is not an object")
    found = []
    if verdict.get("status") == "red":
        evidence_at = datetime.fromtimestamp(newest.stat().st_mtime, timezone.utc)
        for field in ("missing", "unexpected", "duplicates"):
            values = verdict.get(field, [])
            if not isinstance(values, list):
                raise ValueError(f"verdict {field} is not a list")
            for value in values:
                name = _node(value)
                if name is None:
                    raise ValueError(f"verdict {field} has invalid node")
                found.append(({"kind": "unblock", "action": "rerun_node", "node": name, "run_id": run_id}, evidence_at))
    return run_id, found


def _incidents(state: Path, now: datetime) -> list[tuple[dict, datetime]]:
    path = state / "incidents.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("incidents must be an object")
    found = []
    for fingerprint, row in data.items():
        if not isinstance(row, dict) or row.get("state") not in {"open", "department_defect"}:
            continue
        evidence = None
        for key in ("last_escalated_at", "escalated_at", "last_seen", "ts", "first_seen"):
            evidence = _time(row.get(key))
            if evidence is not None:
                break
        if evidence is not None and now - evidence > timedelta(hours=48):
            found.append(({"kind": "unblock", "action": "re_escalate", "fingerprint": str(fingerprint)}, evidence))
    return found


def _approvals(state: Path, now: datetime) -> list[tuple[dict, datetime]]:
    path = state / "approval_queue.jsonl"
    if not path.exists():
        return []
    found, seen = [], set()
    for row in _jsonl(path):
        if row.get("status") != "pending_approval":
            continue
        queued = _time(row.get("queued_at"))
        # Hard rule 6: never copy a raw CRM/contact identifier into records.
        # With no safe identifier, use a content hash of the row instead.
        card = row.get("decision_id") or row.get("id") or row.get("fingerprint") or row.get("card_ref")
        if card is None:
            card = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:12]
        if queued is not None and now - queued > timedelta(hours=24) and card is not None and str(card) not in seen:
            seen.add(str(card))
            found.append(({"kind": "unblock", "action": "remind_owner", "card": str(card)}, queued))
    return found


def _heals(state: Path) -> list[tuple[dict, datetime]]:
    path = state / "heals.jsonl"
    if not path.exists():
        return []
    pending: dict[tuple[str, str], tuple[dict, datetime]] = {}
    for row in _jsonl(path):
        fingerprint, playbook = row.get("fingerprint"), row.get("playbook")
        if not isinstance(fingerprint, str) or not isinstance(playbook, str):
            continue
        key = (fingerprint, playbook)
        result = row.get("result")
        if result == "proposed":
            pending[key] = (row, _time(row.get("ts")) or datetime.max.replace(tzinfo=timezone.utc))
        elif result in {"applied", "verified"}:
            pending.pop(key, None)
    return [({"kind": "routine", "action": "apply_heal_shadow", "fingerprint": key[0], "playbook": key[1]}, value[1]) for key, value in pending.items()]


def _floors(state: Path) -> list[tuple[dict, datetime]]:
    path = state / "floors-history.jsonl"
    if not path.exists():
        return []
    rows = _jsonl(path)
    if not rows:
        return []
    row = rows[-1]
    evidence = _time(row.get("computed_at") or row.get("ts")) or datetime.max.replace(tzinfo=timezone.utc)
    if row.get("status") == "frozen":
        return [({"kind": "floor_gap", "action": "repair_floor_inputs", "reason": str(row.get("reason", "unknown"))}, evidence)]
    changes = row.get("changes")
    if row.get("status") == "ok" and isinstance(changes, list) and changes:
        stages = sorted({str(change.get("stage")) for change in changes if isinstance(change, dict) and change.get("stage") is not None})
        return [({"kind": "floor_gap", "action": "review_floor_move", "stages": stages}, evidence)]
    return []


def _identity(decision: dict) -> tuple[str, str, str]:
    key = decision.get("node") or decision.get("fingerprint") or decision.get("card") or decision.get("reason") or ",".join(decision.get("stages", []))
    if decision.get("playbook"):
        key = f"{key}|{decision['playbook']}"
    return str(decision.get("kind")), str(decision.get("action")), str(key)


def _previous_three(state: Path) -> list[dict]:
    path = state / "conductor-shadow.jsonl"
    if not path.exists():
        return []
    return _jsonl(path)[-3:]


def tick(dept_dir, state_dir, *, holder="conductor", now=None) -> dict:
    dept, state, current = Path(dept_dir), Path(state_dir), _utc(now)
    try:
        acquire(state, holder=holder, ttl_s=26 * 60 * 60, now=current)
    except LeaseHeld as exc:
        refusal_receipt(state, loser=holder, holder=exc.holder, now=current)
        return {"run_id": None, "decisions": [], "held_lease": False, "refused_by": exc.holder}

    candidates: list[tuple[dict, datetime]] = []
    run_id = None
    sources = (("run-manifests", lambda: _manifest(state)), ("incidents.json", lambda: _incidents(state, current)), ("approval_queue.jsonl", lambda: _approvals(state, current)), ("heals.jsonl", lambda: _heals(state)), ("floors-history.jsonl", lambda: _floors(state)))
    for name, sense in sources:
        try:
            result = sense()
            if name == "run-manifests":
                run_id, rows = result
                candidates.extend(rows)
            else:
                candidates.extend(result)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            candidates.append(({"kind": "unknown_source", "source": name}, datetime.min.replace(tzinfo=timezone.utc)))

    previous = _previous_three(state)
    counts: dict[tuple[str, str, str], int] = {}
    for ledger in previous:
        for decision in ledger.get("decisions", []):
            if isinstance(decision, dict):
                identity = _identity(decision)
                counts[identity] = counts.get(identity, 0) + 1
    for decision, _ in candidates:
        if counts.get(_identity(decision)) == 3:
            decision["kind"] = {"routine": "floor_gap", "floor_gap": "unblock"}.get(decision.get("kind"), decision.get("kind"))

    rank = {"unblock": 0, "unknown_source": 0, "floor_gap": 1, "routine": 2}
    candidates.sort(key=lambda item: (rank.get(item[0].get("kind"), 0), item[1], _identity(item[0])))
    decisions = [decision for decision, _ in candidates]
    try:
        ceiling = thresholds(load_charter(dept / "charter.yaml", expect_department=dept.name))["budget_ceilings"]["model_calls"]
        if len(decisions) > 0.2 * ceiling:
            decisions.insert(0, {"kind": "unblock", "action": "halt_and_review_budget"})
    except (CharterError, OSError, ValueError, TypeError, KeyError):
        decisions.insert(0, {"kind": "unknown_source", "source": "budget_headroom_check"})

    ledger = {"ts": current.isoformat(), "holder": holder, "run_id": run_id, "decisions": decisions, "refused_by": None}
    _append(state / "conductor-shadow.jsonl", ledger)
    heartbeat_path = state / "conductor-heartbeat.json"
    epoch = 0
    if heartbeat_path.exists():
        try:
            epoch = int(json.loads(heartbeat_path.read_text(encoding="utf-8"))["epoch"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            epoch = 0
    _atomic_json(heartbeat_path, {"ts": current.isoformat(), "epoch": epoch + 1})
    return {"run_id": run_id, "decisions": decisions, "held_lease": True, "refused_by": None}
