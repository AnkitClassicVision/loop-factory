"""Expectation manifests: audit reality against DECLARED expectations.

Owner decision (Ankit, 2026-08-04): every step of every flow declares what
artifacts it expects and by when; a deterministic reconciler diffs the
manifest against reality; every delta either carries an authorized-skip
receipt or summons healing. This is the absence-detection sibling of the
receipt-gating hard rule: receipts prove the steps that RAN; expectation
manifests expose the steps that never started. A dead webhook, a skipped
research pass, and an uncreated approval card all look like a healthy quiet
day until something knows what was supposed to exist by when.

Factory-level and department-agnostic: this module never names a department,
path convention, or threshold. Departments supply manifest YAML files,
filesystem roots, and snapshot JSON files; the reconciler only ever reads.

Manifest schema (expectation-manifest/v1)::

    schema: expectation-manifest/v1
    process: recording-intake
    instances:
      source: snapshot            # 'snapshot' | 'glob'
      snapshot: finished-items    # snapshot key holding [{id, anchor_ts}, ...]
      # source: glob
      # glob: "episodes/*/episode.json"   # anchor_ts = file mtime
      # id_from: parent_dir               # 'parent_dir' | 'stem'
    steps:
      - id: job-enqueued
        description: a processing job exists for every finished item
        deadline_minutes: 30
        expect:
          - kind: snapshot_member
            snapshot: enqueued-ids
          - kind: artifact
            glob: "episodes/{id}/raw/*.webm"
            min_count: 1
          - kind: json_field
            file: "episodes/{id}/episode.json"
            pointer: "guests/0/research"
            non_empty: true
        authorized_skip:
          glob: "episodes/{id}/skips/job-enqueued-*.json"
        heal: escalate            # heal playbook id, or 'escalate'

Reconcile semantics, per instance x step:
  * every ``expect`` item satisfied            -> ok
  * unsatisfied, age <  deadline_minutes       -> pending (not a delta)
  * unsatisfied, age >= deadline_minutes, an authorized-skip receipt exists
                                               -> skipped_authorized
  * unsatisfied, age >= deadline_minutes       -> DELTA (status 'missing')

Deny-by-default: an unreadable manifest, unknown expectation kind, missing
snapshot file/key, or malformed instance row raises ManifestError. Silence is
never assumed healthy; a broken reconciler run must alarm, not pass.
"""
from __future__ import annotations

import argparse
import glob as globlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCHEMA = "expectation-manifest/v1"
RECEIPT_SCHEMA = "expectation-reconcile-receipt/v1"
KINDS = ("artifact", "snapshot_member", "json_field")


class ManifestError(ValueError):
    """Manifest, snapshot, or instance data is unusable. Fail closed."""


@dataclass
class Instance:
    id: str
    anchor_ts: datetime
    evidence: str
    extras: dict = field(default_factory=dict)


@dataclass
class Manifest:
    process: str
    instances: dict
    steps: list
    path: str = ""
    raw: dict = field(default_factory=dict)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_ts(value, context: str) -> datetime:
    try:
        return _utc(datetime.fromisoformat(str(value)))
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"{context}: bad timestamp {value!r}") from exc


def load_manifest(path: str | Path) -> Manifest:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ManifestError(f"{path}: schema must be {SCHEMA!r}")
    process = str(raw.get("process") or "").strip()
    instances = raw.get("instances")
    steps = raw.get("steps")
    if not process or not isinstance(instances, dict) or not isinstance(steps, list) or not steps:
        raise ManifestError(f"{path}: process, instances, and steps are required")
    source = instances.get("source")
    if source not in ("snapshot", "glob"):
        raise ManifestError(f"{path}: instances.source must be 'snapshot' or 'glob'")
    if source == "snapshot" and not str(instances.get("snapshot") or "").strip():
        raise ManifestError(f"{path}: instances.snapshot key is required")
    if source == "glob" and not str(instances.get("glob") or "").strip():
        raise ManifestError(f"{path}: instances.glob is required")
    for step in steps:
        if not isinstance(step, dict) or not str(step.get("id") or "").strip():
            raise ManifestError(f"{path}: every step needs an id")
        if not isinstance(step.get("deadline_minutes"), (int, float)):
            raise ManifestError(f"{path}: step {step.get('id')}: deadline_minutes required")
        expect = step.get("expect")
        if not isinstance(expect, list) or not expect:
            raise ManifestError(f"{path}: step {step['id']}: expect list required")
        for item in expect:
            if not isinstance(item, dict) or item.get("kind") not in KINDS:
                raise ManifestError(
                    f"{path}: step {step['id']}: expect.kind must be one of {KINDS}"
                )
    return Manifest(process=process, instances=instances, steps=steps, path=str(path), raw=raw)


def load_snapshots(path: str | Path | None) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read snapshots {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: snapshots must be a JSON object")
    return data


def discover_instances(manifest: Manifest, root: Path, snapshots: dict) -> list[Instance]:
    spec = manifest.instances
    if spec["source"] == "snapshot":
        key = spec["snapshot"]
        if key not in snapshots:
            raise ManifestError(f"snapshot key {key!r} absent (fail closed, not empty)")
        rows = snapshots[key]
        if not isinstance(rows, list):
            raise ManifestError(f"snapshot {key!r} must be a list")
        out = []
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("id") or "").strip():
                raise ManifestError(f"snapshot {key!r}: rows need an id")
            out.append(Instance(
                id=str(row["id"]),
                anchor_ts=_parse_ts(row.get("anchor_ts"), f"snapshot {key!r} id {row['id']}"),
                evidence=f"snapshot:{key}",
                extras={k: str(v) for k, v in row.items()
                        if k not in ("id", "anchor_ts") and isinstance(v, (str, int, float))},
            ))
        return out
    pattern = str(root / spec["glob"])
    id_from = spec.get("id_from", "parent_dir")
    if id_from not in ("parent_dir", "grandparent_dir", "stem"):
        raise ManifestError(f"instances.id_from must be parent_dir|grandparent_dir|stem, got {id_from!r}")
    out = []
    for match in sorted(globlib.glob(pattern)):
        p = Path(match)
        if id_from == "parent_dir":
            instance_id = p.parent.name
        elif id_from == "grandparent_dir":
            instance_id = p.parent.parent.name
        else:
            instance_id = p.stem
        out.append(Instance(
            id=instance_id,
            anchor_ts=datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc),
            evidence=str(p),
        ))
    return out


def _json_pointer(value, pointer: str):
    node = value
    for part in [p for p in pointer.split("/") if p != ""]:
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        else:
            return None
    return node


def _satisfied(item: dict, instance: Instance, root: Path, snapshots: dict) -> tuple[bool, str]:
    kind = item["kind"]
    if kind == "artifact":
        try:
            rendered = str(item["glob"]).format(id=instance.id, **instance.extras)
        except KeyError as exc:
            raise ManifestError(
                f"artifact glob references {exc} absent from instance "
                f"{instance.id!r} (fail closed, not skipped)")
        pattern = str(root / rendered)
        found = globlib.glob(pattern)
        needed = int(item.get("min_count", 1))
        return len(found) >= needed, f"artifact {pattern} matched {len(found)}/{needed}"
    if kind == "snapshot_member":
        key = item["snapshot"]
        if key not in snapshots:
            raise ManifestError(f"snapshot key {key!r} absent (fail closed, not empty)")
        members = snapshots[key]
        if not isinstance(members, list):
            raise ManifestError(f"snapshot {key!r} must be a list")
        ids = {str(m.get("id")) if isinstance(m, dict) else str(m) for m in members}
        return instance.id in ids, f"snapshot {key} membership"
    if kind == "json_field":
        file_path = root / str(item["file"]).format(id=instance.id)
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False, f"json {file_path} unreadable"
        value = _json_pointer(data, str(item["pointer"]))
        if item.get("non_empty", True):
            ok = value not in (None, "", [], {})
        else:
            ok = value is not None
        return ok, f"json {file_path}#{item['pointer']} -> {'set' if ok else 'empty'}"
    raise ManifestError(f"unknown expectation kind {kind!r}")  # pragma: no cover


def reconcile(manifest: Manifest, root: str | Path, snapshots: dict | None = None,
              now: datetime | None = None) -> dict:
    root = Path(root)
    if not root.exists():
        raise ManifestError(f"root {root} does not exist (fail closed)")
    snapshots = snapshots or {}
    now = _utc(now or datetime.now(timezone.utc))
    instances = discover_instances(manifest, root, snapshots)
    deltas, pending, ok, skips = [], [], 0, 0
    for instance in instances:
        age_minutes = (now - instance.anchor_ts).total_seconds() / 60.0
        for step in manifest.steps:
            unmet = []
            for item in step["expect"]:
                satisfied, detail = _satisfied(item, instance, root, snapshots)
                if not satisfied:
                    unmet.append(detail)
            if not unmet:
                ok += 1
                continue
            record = {
                "process": manifest.process,
                "instance": instance.id,
                "step": step["id"],
                "unmet": unmet,
                "age_minutes": round(age_minutes, 1),
                "deadline_minutes": step["deadline_minutes"],
                "heal": step.get("heal", "escalate"),
                "anchor_evidence": instance.evidence,
            }
            if age_minutes < float(step["deadline_minutes"]):
                pending.append({**record, "status": "pending"})
                continue
            skip_spec = step.get("authorized_skip") or {}
            skip_glob = str(skip_spec.get("glob") or "").strip()
            if skip_glob and globlib.glob(str(root / skip_glob.format(id=instance.id))):
                skips += 1
                continue
            deltas.append({**record, "status": "missing"})
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": now.isoformat(),
        "process": manifest.process,
        "manifest": manifest.path,
        "instances": len(instances),
        "counts": {"ok": ok, "pending": len(pending), "authorized_skips": skips,
                   "deltas": len(deltas)},
        "pending": pending,
        "deltas": deltas,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--snapshots", default=None)
    parser.add_argument("--now", default=None, help="ISO timestamp override for replay/tests")
    parser.add_argument("--receipt", default=None, help="write the receipt JSON here")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        snapshots = load_snapshots(args.snapshots)
        now = _parse_ts(args.now, "--now") if args.now else None
        receipt = reconcile(manifest, args.root, snapshots, now)
    except ManifestError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    body = json.dumps(receipt, indent=1, sort_keys=True)
    if args.receipt:
        tmp = Path(args.receipt).with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(body + "\n", encoding="utf-8")
        os.replace(tmp, args.receipt)
    print(body)
    return 3 if receipt["deltas"] else 0


if __name__ == "__main__":
    sys.exit(main())
