"""Deterministically derive funnel flow and stock floors from governed inputs."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from factory.charter_loader import funnel_config, load_charter
from factory.events_ledger import LedgerError, read_transitions
from kernel.run_manifest import MANIFEST_REV, _manifest_is_signed, verify_signed_verdict


HEADER = "# MACHINE-WRITTEN — derived; humans set goals in charter.yaml\n"


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _previous(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        floors = value.get("floors", value)
        return floors if isinstance(floors, dict) else {}
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return {}


def _run_manifest_freeze_reason(dept: Path, manifest_path: Path) -> str | None:
    """Return a refusal reason unless the selected run proof is fully bound."""
    verdict_path = manifest_path.with_name(f"{manifest_path.stem}.verdict.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return "selected run-manifest is unreadable"
    if not isinstance(manifest, dict):
        return "selected run-manifest schema is invalid"
    if not _manifest_is_signed(manifest):
        return "selected run-manifest signature is invalid or missing"
    if (
        manifest.get("schema") != "run-manifest"
        or manifest.get("rev") != MANIFEST_REV
        or manifest.get("run_id") != manifest_path.stem
        or not isinstance(manifest.get("created_at"), str)
        or not isinstance(manifest.get("department"), str)
        or not manifest["department"].strip()
    ):
        return "selected run-manifest schema is invalid"

    release = manifest.get("release")
    if (
        not isinstance(release, dict)
        or set(release) != {"hash", "source_ref"}
        or not isinstance(release.get("hash"), str)
        or not release["hash"].strip()
        or not isinstance(release.get("source_ref"), str)
        or not release["source_ref"].strip()
    ):
        return "selected run-manifest release binding is invalid"

    current_path = dept / "releases" / "current"
    release_path = dept / "releases" / release["hash"] / "manifest.json"
    try:
        current_hash = current_path.read_text(encoding="utf-8").strip()
        release_doc = json.loads(release_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return "selected run-manifest release binding is unreadable"
    if (
        current_hash != release["hash"]
        or not isinstance(release_doc, dict)
        or release_doc.get("hash") != release["hash"]
        or release_doc.get("source_ref") != release["source_ref"]
    ):
        return "selected run-manifest release/source binding does not match current release"

    try:
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return "selected run-manifest verdict is unreadable"
    if not verify_signed_verdict(verdict):
        return "selected run-manifest verdict signature is invalid or missing"
    if not isinstance(verdict, dict) or verdict.get("run_id") != manifest["run_id"]:
        return "selected run-manifest verdict lineage does not match manifest"
    if verdict.get("release") is not None and verdict.get("release") != release:
        return "selected run-manifest verdict release binding does not match manifest"
    if verdict.get("manifest_run_id") is not None and verdict.get("manifest_run_id") != manifest["run_id"]:
        return "selected run-manifest verdict lineage does not match manifest"
    if verdict.get("status") != "green":
        return f"newest run-manifest verdict is not green: {verdict.get('status')!r}"

    try:
        created_at = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
        checked_at = datetime.fromisoformat(verdict["checked_at"].replace("Z", "+00:00"))
        if created_at.tzinfo is None or checked_at.tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return "selected run-manifest verdict lineage is stale or unreadable"
    if checked_at < created_at:
        return "selected run-manifest verdict lineage is stale"
    return None


def _freeze_reason(state: Path, now: datetime, dept: Path | None = None) -> str | None:
    objectives = state / "objectives_observed.json"
    if objectives.exists():
        try:
            values = json.loads(objectives.read_text(encoding="utf-8"))["values"]
            for key in ("state_drift", "unledgered_inbound"):
                if values.get(key) != 0 and key in values:
                    return f"{key} != 0"
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass
    manifest_dir = state / "run-manifests"
    if manifest_dir.is_dir():
        manifests = [p for p in manifest_dir.glob("*.json") if not p.name.endswith(".verdict.json")]
        if manifests:
            try:
                newest = max(manifests, key=lambda path: path.stat().st_mtime)
            except OSError:
                return "selected run-manifest is unreadable"
            if dept is None:
                return "selected run-manifest release binding cannot be verified"
            refusal = _run_manifest_freeze_reason(dept, newest)
            if refusal:
                return refusal
    malformed = read_transitions(
        state, from_stage="__scan__", to_stage="__scan__",
        since=datetime.min.replace(tzinfo=timezone.utc), until=now,
    ).malformed
    if malformed:
        return f"events ledger has {malformed} malformed line(s)"
    return None


def _all_events(state: Path) -> list[dict]:
    path = state / "events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            timestamp = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
            row = dict(row)
            row["_ts"] = timestamp.astimezone(timezone.utc)
            rows.append(row)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return rows


def _rate(row: dict, state: Path, now: datetime, old: float) -> tuple[float, str]:
    events = _all_events(state)
    cutoff = now - timedelta(days=28)
    maturity_cutoff = now - timedelta(days=row["maturity_days"])
    entries: dict[str, datetime] = {}
    for event in events:
        if event.get("to_stage") == row["from"] and cutoff <= event["_ts"] <= maturity_cutoff:
            entries.setdefault(event["subject_id"], event["_ts"])
    converted = set()
    for event in events:
        subject = event.get("subject_id")
        if (
            subject in entries
            and event.get("from_stage") == row["from"]
            and event.get("to_stage") == row["to"]
            and entries[subject] <= event["_ts"] <= now
        ):
            converted.add(subject)
    if len(entries) < 30 or len(converted) < 10:
        return float(row["prior_rate"]), "prior"
    measured = len(converted) / len(entries)
    return 0.75 * old + 0.25 * measured, "blended"


def _cap(old: int, proposed: int) -> int:
    return min(math.floor(old * 1.2), max(math.ceil(old * 0.8), proposed))


def _persist(state: Path, result: dict) -> None:
    state.mkdir(parents=True, exist_ok=True)
    with (state / "floors-history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True) + "\n")


def compile_floors(dept_dir, state_dir, *, now=None) -> dict:
    dept, state, current = Path(dept_dir), Path(state_dir), _utc(now)
    computed_at = current.isoformat()
    config = funnel_config(load_charter(dept / "charter.yaml", expect_department=dept.name))
    if config is None:
        result = {"status": "unconfigured", "reason": "charter has no funnel section", "floors": {}, "changes": [], "computed_at": computed_at}
        _persist(state, result)
        return result
    floors_path = dept / "floors.yaml"
    previous = _previous(floors_path)
    freeze = _freeze_reason(state, current, dept)
    if freeze:
        result = {"status": "frozen", "reason": freeze, "floors": previous, "changes": [], "computed_at": computed_at}
        _persist(state, result)
        return result

    terminal = config["end_goal"]
    proposed: dict[str, dict[str, Any]] = {
        terminal["stage"]: {"flow_per_week": terminal["per_week"], "stock_min": 0, "rate_used": 1.0, "rate_source": "prior"}
    }
    downstream_flow = terminal["per_week"]
    for transition in reversed(config["transitions"]):
        old_rate = float(previous.get(transition["from"], {}).get("rate_used", transition["prior_rate"]))
        rate, source = _rate(transition, state, current, old_rate)
        flow = math.ceil(downstream_flow / rate * (1 + transition["buffer"]))
        stock = math.ceil(flow * transition["lead_days"] / 7 * (1 + transition["stock_buffer"]))
        proposed[transition["from"]] = {"flow_per_week": flow, "stock_min": stock, "rate_used": rate, "rate_source": source}
        downstream_flow = flow

    changes = []
    if previous:
        for stage, row in proposed.items():
            old_row = previous.get(stage)
            if not isinstance(old_row, dict):
                continue
            for field in ("flow_per_week", "stock_min"):
                old, wanted = old_row.get(field), row[field]
                if isinstance(old, int) and old != wanted:
                    row[field] = _cap(old, wanted)
                    if row[field] != old:
                        changes.append({"stage": stage, "field": field, "old": old, "new": row[field]})
    result = {"status": "ok", "reason": "floors compiled", "floors": proposed, "changes": changes, "computed_at": computed_at}
    dept.mkdir(parents=True, exist_ok=True)
    floors_path.write_text(HEADER + yaml.safe_dump(result, sort_keys=False), encoding="utf-8")
    _persist(state, result)
    return result
