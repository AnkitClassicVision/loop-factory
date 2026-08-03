"""Deterministic, proposal-only triage for escalation outboxes.

The consumer starts at each outbox's current EOF, classifies only later
appends, and converts repairable incidents into Ringer and self-heal
proposals.  It never launches a worker, changes runtime state, or repairs a
unit.  Human notification is a bounded digest through an injected argv
template.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from factory.charter_loader import CharterError, load_charter
from factory.selfheal import propose_patch


LOGGER = logging.getLogger(__name__)

CLASSES = (
    "infra_restartable",
    "release_drift",
    "auth_needed",
    "objective_breach",
    "approval_pending",
    "unknown",
)
AUTO_CLASSES = frozenset({"infra_restartable", "release_drift"})
HUMAN_CLASSES = frozenset({"auth_needed", "objective_breach", "unknown"})

_APPROVAL = re.compile(r"\b(?:approval[_ -]?pending|pending[_ -]?approval|awaiting approval)\b", re.I)
_AUTH = re.compile(
    r"\b(?:sso|oauth|log[ -]?in|token|credentials?|unauthori[sz]ed|auth(?:entication)?|401)\b",
    re.I,
)
_DRIFT = re.compile(
    r"\b(?:release[_ -]?drift|drift|mismatches?|re-?pin(?:ned|ning)?|pin(?:ned|ning)?|stale release)\b",
    re.I,
)
_OBJECTIVE = re.compile(
    r"OBJECTIVE_BELOW_MIN|\bbelow (?:the )?(?:minimum|min|setpoint)\b|"
    r"\bunder (?:the )?(?:minimum|min|setpoint)\b|<\s*(?:minimum|min|setpoint)\b",
    re.I,
)
_INFRA = re.compile(
    r"receipt[_ -]?stale|\bheartbeat(?:[_ -]?(?:gaps?|missing|stale|late))\b|"
    r"\b(?:unit|timer|service)\b.{0,48}\b(?:fail(?:ed|ure)?|inactive|dead|missing|stale|gap)\b|"
    r"\b(?:fail(?:ed|ure)?|inactive|dead|missing|stale|gap)\b.{0,48}\b(?:unit|timer|service)\b",
    re.I,
)
_UNIT = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9_.@-]*\.(?:service|timer)\b")
_SAFE_NODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _utc_now(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("--now must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--now must include a timezone")
    return parsed.astimezone(timezone.utc)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    encoded = b"".join(
        (json.dumps(row, sort_keys=True) + "\n").encode("utf-8") for row in rows
    )
    if not encoded:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        written = 0
        while written < len(encoded):
            written += os.write(descriptor, encoded[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_config(path: str | Path) -> dict[str, Any]:
    """Load and validate the deliberately small triage configuration."""
    config_path = Path(path).expanduser()
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid triage config at {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("triage config must be a mapping")
    outboxes = config.get("outboxes")
    if not isinstance(outboxes, list) or not outboxes or any(
        not isinstance(item, str) or not item for item in outboxes
    ):
        raise ValueError("config.outboxes must be a non-empty list of paths")
    ping = config.get("ping")
    if not isinstance(ping, list) or not ping or any(
        not isinstance(item, str) or not item for item in ping
    ):
        raise ValueError("config.ping must be a non-empty argv list")
    if not any("{text}" in item for item in ping):
        raise ValueError("config.ping must contain a {text} placeholder")
    max_pings = config.get("max_pings_per_run", 1)
    cooldown = config.get("digest_cooldown_hours", 6)
    if isinstance(max_pings, bool) or not isinstance(max_pings, int) or max_pings < 0:
        raise ValueError("config.max_pings_per_run must be a non-negative integer")
    if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)) or cooldown < 0:
        raise ValueError("config.digest_cooldown_hours must be non-negative")
    return {
        "outboxes": outboxes,
        "ping": ping,
        "max_pings_per_run": max_pings,
        "digest_cooldown_hours": float(cooldown),
    }


def classify(row: dict[str, Any]) -> str:
    """Classify one escalation using only finite issue/code patterns."""
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    material = " ".join(
        str(value)
        for value in (
            row.get("issue"),
            row.get("eli5"),
            row.get("code"),
            row.get("finding_code"),
            row.get("failure_class"),
            context.get("code"),
            context.get("finding"),
            context.get("finding_code"),
            context.get("failure_class"),
        )
        if value is not None
    )
    if _APPROVAL.search(material):
        return "approval_pending"
    if _AUTH.search(material):
        return "auth_needed"
    if _DRIFT.search(material):
        return "release_drift"
    if _OBJECTIVE.search(material):
        return "objective_breach"
    if _INFRA.search(material):
        return "infra_restartable"
    return "unknown"


def _fingerprint(row: dict[str, Any]) -> str:
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    supplied = row.get("fingerprint") or context.get("fingerprint")
    if isinstance(supplied, str) and supplied:
        return supplied
    department = str(row.get("department") or "factory")
    code = str(
        context.get("finding_code")
        or context.get("finding")
        or context.get("code")
        or context.get("failure_class")
        or row.get("finding_code")
        or row.get("code")
        or row.get("failure_class")
        or classify(row)
    )
    subject = str(
        context.get("subject")
        or context.get("target")
        or context.get("unit")
        or context.get("objective")
        or context.get("objective_key")
        or re.sub(r"\s+", " ", str(row.get("issue") or "unknown").strip().lower())
    )
    return hashlib.sha256(f"{department}|{code}|{subject}".encode("utf-8")).hexdigest()[:12]


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON at {path}: {exc}") from exc


def _active_fingerprints(path: Path) -> set[str]:
    active: set[str] = set()
    if not path.exists():
        return active
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid seen fingerprint row {path}:{line_number}: {exc}") from exc
        fingerprint = row.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            continue
        marker = row.get("marker") or row.get("status")
        if marker == "resolved" or row.get("resolved") is True:
            active.discard(fingerprint)
        elif marker == "delivered" or row.get("delivered") is True:
            active.add(fingerprint)
    return active


def _resolve_outbox(repo_root: Path, configured: str) -> Path:
    path = Path(configured).expanduser()
    return path if path.is_absolute() else repo_root / path


def _registered_departments(repo_root: Path) -> set[str]:
    """Return department names advertised on disk or by the estate registry."""
    names: set[str] = set()
    departments_dir = repo_root / "departments"
    if departments_dir.is_dir():
        names.update(path.name for path in departments_dir.iterdir() if path.is_dir())
    registry_dir = repo_root / "estate" / "registry.d"
    if not registry_dir.is_dir():
        return names
    for path in sorted(registry_dir.glob("*.yaml")):
        names.add(path.stem)
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            LOGGER.warning("cannot inspect triage registry entry %s: %s", path, exc)
            continue
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in ("id", "department", "name"):
                value = entry.get(key)
                if isinstance(value, str) and value:
                    names.add(value)
    return names


def _department_for_unit(repo_root: Path, unit: str) -> str | None:
    for department in sorted(_registered_departments(repo_root), key=len, reverse=True):
        if unit == department or (
            unit.startswith(department)
            and len(unit) > len(department)
            and unit[len(department)] in "-_.@"
        ):
            return department
    return None


def _department_for_outbox(repo_root: Path, trigger_path: str) -> str | None:
    path = Path(trigger_path).expanduser()
    resolved = (path if path.is_absolute() else repo_root / path).resolve()
    departments_dir = (repo_root / "departments").resolve()
    try:
        relative = resolved.relative_to(departments_dir)
    except ValueError:
        return None
    if not relative.parts:
        return None
    department = relative.parts[0]
    return department if department in _registered_departments(repo_root) else None


def _trigger_context(
    repo_root: Path,
    trigger_unit: str | None,
    trigger_path: str | None,
) -> tuple[dict[str, str], str | None]:
    if trigger_unit and trigger_path:
        raise ValueError("--trigger-unit and --trigger-path are mutually exclusive")
    if trigger_unit:
        trigger = {"kind": "unit_failure", "ref": trigger_unit}
        department = _department_for_unit(repo_root, trigger_unit)
    elif trigger_path:
        trigger = {"kind": "outbox_append", "ref": trigger_path}
        department = _department_for_outbox(repo_root, trigger_path)
    else:
        trigger = {"kind": "timer", "ref": "loop-factory-triage.timer"}
        department = None
    if department is not None:
        charter_path = repo_root / "departments" / department / "charter.yaml"
        try:
            load_charter(charter_path, expect_department=department)
        except CharterError as exc:
            LOGGER.warning("summoning department charter unavailable for %s: %s", department, exc)
    return trigger, department


def _read_appends(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    end = path.stat().st_size
    if offset > end:
        LOGGER.warning("outbox truncated; resetting cursor to current EOF: %s", path)
        return [], end
    with path.open("rb") as handle:
        handle.seek(offset)
        payload = handle.read()
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(payload.splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid appended outbox row {path}:+{line_number}: {exc}") from exc
        if isinstance(row, dict):
            rows.append(row)
    return rows, end


def _objective_key(row: dict[str, Any], objectives: dict[str, Any]) -> str | None:
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    candidates = (
        context.get("objective"),
        context.get("objective_key"),
        context.get("metric"),
        context.get("setpoint"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate in objectives:
            return candidate
    material = f"{row.get('issue', '')} {row.get('eli5', '')}".lower()
    for key, value in objectives.items():
        label = value.get("label") if isinstance(value, dict) else None
        if key.lower() in material or (isinstance(label, str) and label.lower() in material):
            return key
    return None


def _observed(row: dict[str, Any]) -> Any:
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    for key in ("observed", "actual", "value", "count"):
        if key in context:
            return context[key]
        if key in row:
            return row[key]
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*<", str(row.get("issue") or ""))
    if match:
        number = float(match.group(1))
        return int(number) if number.is_integer() else number
    return None


def enrich_objective(repo_root: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Attach charter-owned objective language to an objective breach."""
    enriched = dict(row)
    department = row.get("department")
    if not isinstance(department, str) or not department:
        enriched["goal_error"] = "department missing"
        return enriched
    charter_path = repo_root / "departments" / department / "charter.yaml"
    try:
        charter = load_charter(charter_path, expect_department=department)
    except CharterError as exc:
        enriched["goal_error"] = str(exc)
        return enriched
    objectives = (charter.get("setpoints") or {}).get("objectives") or {}
    if not isinstance(objectives, dict):
        enriched["goal_error"] = "charter setpoints.objectives is not a mapping"
        return enriched
    key = _objective_key(row, objectives)
    if key is None or not isinstance(objectives.get(key), dict):
        enriched["goal_error"] = "objective not found in charter"
        return enriched
    objective = objectives[key]
    enriched["objective"] = {
        "key": key,
        "label": objective.get("label") or key.replace("_", " "),
        "minimum": objective.get("minimum"),
        "target": objective.get("target", objective.get("setpoint")),
        "unit": objective.get("unit", ""),
        "observed": _observed(row),
    }
    return enriched


def _incident_nodes(row: dict[str, Any]) -> list[str]:
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    nodes: list[str] = []
    for key in ("unit", "node", "subject", "target"):
        value = context.get(key)
        if isinstance(value, str) and _SAFE_NODE.fullmatch(value):
            nodes.append(value)
    nodes.extend(_UNIT.findall(str(row.get("issue") or "")))
    # findall returns the full match because _UNIT has no capturing group.
    if not nodes:
        department = str(row.get("department") or "factory")
        suffix = "release" if classify(row) == "release_drift" else "runtime"
        candidate = f"{department}-{suffix}"
        nodes.append(candidate if _SAFE_NODE.fullmatch(candidate) else "factory-runtime")
    return list(dict.fromkeys(nodes))


def _diagnosis(item: dict[str, Any]) -> str:
    row = item["row"]
    return str(row.get("eli5") or row.get("issue") or "Escalation requires diagnosis.").strip()


def _selfheal_nodes(row: dict[str, Any]) -> list[str]:
    """Return only nodes accepted by selfheal's path-safe node contract."""
    return [node for node in _incident_nodes(row) if _SAFE_NODE.fullmatch(node)]


def _check_for(item: dict[str, Any]) -> str:
    row = item["row"]
    if item["class"] == "release_drift":
        department = str(row.get("department") or "")
        if _SAFE_NODE.fullmatch(department):
            return (
                "PYTHONDONTWRITEBYTECODE=1 python3 loopfactory.py qa --name "
                + shlex.quote(department)
            )
        return "PYTHONDONTWRITEBYTECODE=1 python3 loopfactory.py check"
    units = [node for node in _incident_nodes(row) if node.endswith((".service", ".timer"))]
    if units:
        return " && ".join(
            "systemctl --user is-active --quiet " + shlex.quote(unit)
            for unit in units
        )
    return "test \"$(systemctl --user --failed --plain --no-legend | wc -l)\" -eq 0"


def _manifest(repo_root: Path, class_name: str, items: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    tasks = []
    for index, item in enumerate(items, 1):
        row = item["row"]
        department = str(row.get("department") or "factory")
        nodes = _incident_nodes(row)
        diagnosis = _diagnosis(item)
        spec = (
            "Diagnose and propose a bounded repair for this loop-factory incident. "
            f"Department: {department}. Failing nodes/units: {', '.join(nodes)}. "
            f"Observed diagnosis: {diagnosis}. Incident fingerprint: {item['fingerprint']}. "
            "Read the relevant charter and runtime evidence before editing. Preserve shadow-first, "
            "deny-by-default, receipt-gated behavior. Do not change governance, autonomy, promotion, "
            "or human-gate files. Implement only the smallest evidence-backed fix, run the supplied "
            "check, and leave changes uncommitted for coordinator review."
        )
        tasks.append(
            {
                "key": f"{class_name}-{index}",
                "engine": "codex",
                "task_type": "code-fix",
                "timeout_s": 3600,
                "spec": spec,
                "check": _check_for(item),
                "expect_files": [],
                "verified": "post-fix service or release-state check passes",
            }
        )
    return {
        "run_name": f"triage-{class_name}",
        "workdir": str(repo_root),
        "max_parallel": 1,
        "worktrees": True,
        "repo": str(repo_root),
        "created_at": now.isoformat(),
        "source_fingerprints": [item["fingerprint"] for item in items],
        "tasks": tasks,
    }


def _manifest_path(repo_root: Path, class_name: str, now: datetime) -> Path:
    return repo_root / "ringer" / "triage-proposals" / f"{now.strftime('%Y%m%d')}-{class_name}.json"


def _goal_phrase(row: dict[str, Any]) -> str:
    objective = row.get("objective")
    if isinstance(objective, dict):
        label = objective.get("label") or objective.get("key") or "objective"
        observed = objective.get("observed")
        minimum = objective.get("minimum")
        if observed is not None and minimum is not None:
            return f"{label} {observed} < min {minimum}"
        return f"{label} is below minimum"
    return str(row.get("eli5") or row.get("issue") or "objective below minimum").strip()


def _human_phrase(item: dict[str, Any]) -> str:
    row = item["row"]
    if item["class"] == "objective_breach":
        return _goal_phrase(row)
    phrase = str(row.get("eli5") or row.get("issue") or item["class"]).strip()
    phrase = re.sub(r"^\[[^]]+\]\s*", "", phrase)
    return phrase.rstrip(".")


def _digest(
    items: list[dict[str, Any]],
    queued: int,
    trigger: dict[str, str],
    summoning_department: str | None,
) -> str:
    phrases = [_human_phrase(item) for item in items]
    detail = "; ".join(phrases)
    suffix = f" {queued} fix proposal{'s' if queued != 1 else ''} queued." if queued else ""
    if trigger["kind"] == "unit_failure":
        unit = re.sub(r"\.(?:service|timer)$", "", trigger["ref"])
        lead = f"[triage] summoned by {unit} failure: "
    elif trigger["kind"] == "outbox_append":
        source = summoning_department or Path(trigger["ref"]).name
        lead = f"[triage] summoned by {source} outbox append: "
    else:
        lead = "[triage] "
    return f"{lead}{len(items)} need you: {detail}.{suffix}".replace("..", ".")


def _digest_is_cooling(path: Path, fingerprint: str, now: datetime, hours: float) -> bool:
    previous = _read_json(path, {})
    if not isinstance(previous, dict) or previous.get("fingerprint") != fingerprint:
        return False
    try:
        sent_at = _utc_now(previous.get("sent_at"))
    except (TypeError, ValueError):
        return False
    return now - sent_at < timedelta(hours=hours)


def _is_resolution(row: dict[str, Any]) -> bool:
    marker = row.get("marker") or row.get("status") or row.get("kind")
    return marker in {"resolved", "resolution"} or row.get("resolved") is True


def run(
    repo_root: str | Path,
    config: dict[str, Any],
    *,
    execute: bool = False,
    now: str | datetime | None = None,
    trigger_unit: str | None = None,
    trigger_path: str | None = None,
) -> dict[str, Any]:
    """Plan or execute one bounded triage pass and return its receipt."""
    root = Path(repo_root).resolve()
    now_dt = _utc_now(now)
    trigger, summoning_department = _trigger_context(root, trigger_unit, trigger_path)
    triage_dir = root / "state" / "triage"
    cursor_path = triage_dir / "cursor.json"
    seen_path = triage_dir / "seen_fingerprints.jsonl"
    audit_path = triage_dir / "audit.jsonl"
    digest_path = triage_dir / "last_digest.json"

    raw_cursor = _read_json(cursor_path, {})
    if not isinstance(raw_cursor, dict) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in raw_cursor.values()
    ):
        raise ValueError(f"invalid cursor state at {cursor_path}")
    cursor = dict(raw_cursor)
    resolved_paths = [_resolve_outbox(root, value) for value in config["outboxes"]]
    initialized: list[str] = []
    for outbox in resolved_paths:
        key = str(outbox)
        if key not in cursor:
            cursor[key] = outbox.stat().st_size if outbox.exists() else 0
            initialized.append(key)

    # A newly configured source always starts at its current EOF. Existing
    # sources can still be consumed in the same pass.
    incoming: list[tuple[dict[str, Any], str]] = []
    for outbox in resolved_paths:
        key = str(outbox)
        if key in initialized:
            continue
        rows, end = _read_appends(outbox, cursor[key])
        incoming.extend((row, key) for row in rows)
        cursor[key] = end

    # A failed unit is itself finite evidence even if its department outbox did
    # not append. It enters the same dedupe, proposal, audit, and cooldown lanes
    # as every other escalation; no repair or restart is performed here.
    if trigger["kind"] == "unit_failure":
        unit = trigger["ref"]
        incoming.append(
            (
                {
                    "kind": "escalation",
                    "department": summoning_department or "factory",
                    "issue": f"systemd unit {unit} failed",
                    "context": {
                        "unit": unit,
                        "finding_code": "UNIT_FAILURE",
                        "fingerprint": f"unit-failure:{unit}",
                    },
                },
                f"systemd:{unit}",
            )
        )

    if summoning_department is not None:
        incoming.sort(
            key=lambda pair: 0
            if pair[0].get("department") == summoning_department
            else 1
        )

    active = _active_fingerprints(seen_path)
    seen_events: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    unique_items: list[dict[str, Any]] = []

    for row, source in incoming:
        fingerprint = _fingerprint(row)
        if _is_resolution(row):
            active.discard(fingerprint)
            event = {
                "fingerprint": fingerprint,
                "marker": "resolved",
                "timestamp": now_dt.isoformat(),
            }
            seen_events.append(event)
            audit_rows.append(
                {
                    "fingerprint": fingerprint,
                    "row_fingerprint": fingerprint,
                    "class": "unknown",
                    "action": "skipped",
                    "reason": "resolution_marker",
                    "source": source,
                    "timestamp": now_dt.isoformat(),
                    "trigger": trigger,
                }
            )
            continue
        if row.get("kind") not in (None, "escalation"):
            continue
        class_name = classify(row)
        item_row = enrich_objective(root, row) if class_name == "objective_breach" else dict(row)
        item = {
            "fingerprint": fingerprint,
            "class": class_name,
            "row": item_row,
            "source": source,
        }
        if fingerprint in active:
            audit_rows.append(
                {
                    "fingerprint": fingerprint,
                    "row_fingerprint": fingerprint,
                    "class": class_name,
                    "action": "suppressed_duplicate",
                    "source": source,
                    "timestamp": now_dt.isoformat(),
                    "trigger": trigger,
                }
            )
            decisions.append({**item, "action": "suppressed_duplicate"})
            continue
        active.add(fingerprint)
        seen_events.append(
            {
                "fingerprint": fingerprint,
                "marker": "delivered",
                "timestamp": now_dt.isoformat(),
            }
        )
        unique_items.append(item)

    by_class = Counter(item["class"] for item in unique_items)
    auto_groups = {
        class_name: [item for item in unique_items if item["class"] == class_name]
        for class_name in AUTO_CLASSES
    }
    auto_groups = {key: value for key, value in auto_groups.items() if value}
    proposal_plan: list[dict[str, Any]] = []
    proposal_files: list[str] = []
    queued_repairs = sum(len(items) for items in auto_groups.values())

    for class_name, items in sorted(auto_groups.items()):
        manifest_path = _manifest_path(root, class_name, now_dt)
        manifest = _manifest(root, class_name, items, now_dt)
        proposal_plan.append(
            {
                "class": class_name,
                "manifest": str(manifest_path),
                "tasks": len(manifest["tasks"]),
                "selfheal_nodes": [
                    node for item in items for node in _selfheal_nodes(item["row"])
                ],
            }
        )
        if execute:
            _atomic_json(manifest_path, manifest)
            proposal_files.append(str(manifest_path))
            fix_class = "runtime_config" if class_name == "infra_restartable" else "stale_artifact"
            for item in items:
                for node in _selfheal_nodes(item["row"]):
                    card = propose_patch(
                        triage_dir,
                        {
                            "node": node,
                            "incident_fingerprint": f"{item['fingerprint']}-{node}",
                            "fix_class": fix_class,
                            "diagnosis": _diagnosis(item),
                            "proposed_action": (
                                "Review and launch the matching consolidated Ringer proposal; "
                                "accept only after its post-fix check passes."
                            ),
                        },
                        "L2",
                        now=now_dt,
                    )
                    proposal_files.append(str(card))

    human_items = [item for item in unique_items if item["class"] in HUMAN_CLASSES]
    digest_text = (
        _digest(human_items, queued_repairs, trigger, summoning_department)
        if human_items
        else None
    )
    digest_fingerprint = (
        hashlib.sha256(digest_text.encode("utf-8")).hexdigest()[:16]
        if digest_text is not None
        else None
    )
    cooling = bool(
        digest_fingerprint
        and _digest_is_cooling(
            digest_path,
            digest_fingerprint,
            now_dt,
            config["digest_cooldown_hours"],
        )
    )
    ping_planned = bool(human_items and config["max_pings_per_run"] > 0 and not cooling)
    pinged = False
    if execute and ping_planned and digest_text is not None:
        argv = [part.replace("{text}", digest_text) for part in config["ping"]]
        subprocess.run(argv, check=True, text=True, capture_output=True)
        _atomic_json(
            digest_path,
            {
                "fingerprint": digest_fingerprint,
                "sent_at": now_dt.isoformat(),
                "text": digest_text,
            },
        )
        pinged = True

    for item in unique_items:
        if item["class"] in AUTO_CLASSES:
            action = "proposed"
            reason = None
        elif item["class"] == "approval_pending":
            action = "skipped"
            reason = "approval rows belong to the existing approval lane"
        elif ping_planned:
            action = "digested"
            reason = None
        else:
            action = "skipped"
            reason = "digest_cooldown" if cooling else "ping_limit_zero"
        decision = {**item, "action": action}
        if reason:
            decision["reason"] = reason
        decisions.append(decision)
        audit_row = {
            "fingerprint": item["fingerprint"],
            "row_fingerprint": item["fingerprint"],
            "class": item["class"],
            "action": action,
            "source": item["source"],
            "timestamp": now_dt.isoformat(),
            "trigger": trigger,
        }
        if reason:
            audit_row["reason"] = reason
        audit_rows.append(audit_row)

    if execute:
        _append_jsonl(seen_path, seen_events)
        _append_jsonl(audit_path, audit_rows)
        _atomic_json(cursor_path, cursor)

    receipt = {
        "trigger": trigger,
        "dry_run": not execute,
        "rows": len(incoming),
        "by_class": {name: by_class.get(name, 0) for name in CLASSES if by_class.get(name, 0)},
        "proposals_written": len(proposal_files),
        "pinged": pinged,
        "initialized_outboxes": initialized,
        "proposal_plan": proposal_plan,
        "digest": {
            "text": digest_text,
            "fingerprint": digest_fingerprint,
            "would_ping": ping_planned,
            "suppressed_by_cooldown": cooling,
        }
        if digest_text is not None
        else None,
        "decisions": [
            {
                "fingerprint": item["fingerprint"],
                "class": item["class"],
                "action": item["action"],
                **({"objective": item["row"]["objective"]} if "objective" in item["row"] else {}),
            }
            for item in decisions
        ],
    }
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Triage loop-factory escalation outboxes")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--now", help="timezone-aware ISO timestamp (test/replay seam)")
    trigger_group = parser.add_mutually_exclusive_group()
    trigger_group.add_argument("--trigger-unit", help="systemd unit that summoned triage")
    trigger_group.add_argument("--trigger-path", help="outbox path whose append summoned triage")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    receipt = run(
        args.repo_root,
        load_config(args.config),
        execute=args.execute,
        now=args.now,
        trigger_unit=args.trigger_unit,
        trigger_path=args.trigger_path,
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
