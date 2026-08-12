"""Release-bound run-manifest minting and verification.

Implements Task 1 and the frozen interfaces in
docs/superpowers/plans/2026-08-05-p1-manifest-custody.md.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import secrets
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any


class ManifestRefused(ValueError):
    """Minting was safely refused because an input failed its contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{os.urandom(4).hex()}"


def _canonical_without_signature(value: dict[str, Any]) -> bytes:
    unsigned = {key: item for key, item in value.items() if key != "signature"}
    return json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _local_signer():
    module_name = "run_manifest_receipts"
    module = sys.modules.get(module_name)
    if module is None:
        path = pathlib.Path(__file__).resolve().parent / "receipts.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load receipts module from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module.LocalSigner()


def _load_json(path: pathlib.Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _validate_roster(value: Any, department: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise ManifestRefused("roster_schema_invalid")
    if value.get("schema") != "run-roster" or value.get("rev") != 1:
        raise ManifestRefused("roster_schema_invalid")
    if value.get("department") != department or not isinstance(value.get("nodes"), list):
        raise ManifestRefused("roster_schema_invalid")
    nodes = value["nodes"]
    seen_nodes: set[str] = set()
    seen_ordinals: set[int] = set()
    for entry in nodes:
        if not isinstance(entry, dict) or set(entry) != {"ordinal", "node", "required"}:
            raise ManifestRefused("roster_schema_invalid")
        ordinal = entry["ordinal"]
        node = entry["node"]
        required = entry["required"]
        if (
            not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or ordinal < 1
            or not isinstance(node, str)
            or not node
            or not isinstance(required, bool)
            or node in seen_nodes
            or ordinal in seen_ordinals
        ):
            raise ManifestRefused("roster_schema_invalid")
        seen_nodes.add(node)
        seen_ordinals.add(ordinal)
    return nodes


def _write_exclusive(path: pathlib.Path | str, value: dict[str, Any]) -> None:
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(target, flags, 0o600)
    try:
        payload = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def mint(*, department: str, dept_dir: pathlib.Path | str,
         state_dir: pathlib.Path | str, trigger: str) -> dict[str, Any]:
    """Mint one release-bound run manifest, refusing drift or omission."""
    dept_path = pathlib.Path(dept_dir)
    state_path = pathlib.Path(state_dir)
    try:
        release_hash = (dept_path / "releases" / "current").read_text(
            encoding="utf-8"
        ).strip()
        if not release_hash:
            raise ValueError
        release = _load_json(
            dept_path / "releases" / release_hash / "manifest.json"
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ManifestRefused("release_unreadable") from exc

    artifacts = release.get("artifacts") if isinstance(release, dict) else None
    entry = None
    if isinstance(artifacts, list):
        entry = next(
            (item for item in artifacts if isinstance(item, dict)
             and item.get("path") == "runtime/run-roster.json"),
            None,
        )
    if entry is None or not isinstance(entry.get("sha256"), str):
        raise ManifestRefused("roster_release_entry_missing")

    roster_path = dept_path / "runtime" / "run-roster.json"
    try:
        roster_bytes = roster_path.read_bytes()
    except OSError as exc:
        raise ManifestRefused("roster_unreadable") from exc
    roster_hash = hashlib.sha256(roster_bytes).hexdigest()
    if not secrets.compare_digest(roster_hash, entry["sha256"]):
        raise ManifestRefused("roster_hash_mismatch")
    try:
        roster_doc = json.loads(roster_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestRefused("roster_schema_invalid") from exc
    roster = _validate_roster(roster_doc, department)

    run_id = _new_run_id()
    manifest = {
        "schema": "run-manifest",
        "rev": 1,
        "run_id": run_id,
        "department": department,
        "created_at": _now(),
        "trigger": trigger,
        "release": {"hash": release_hash},
        "roster_hash": roster_hash,
        "roster": roster,
        "nonce": secrets.token_hex(16),
        "action_class": "run_manifest",
        "signature": None,
    }
    if os.environ.get("OE_KERNEL_SIGNING_KEY"):
        manifest["signature"] = _local_signer().sign(
            _canonical_without_signature(manifest)
        )
    path = state_path / "run-manifests" / f"{run_id}.json"
    try:
        _write_exclusive(path, manifest)
    except FileExistsError as exc:
        raise ManifestRefused("manifest_exists") from exc
    return {"run_id": run_id, "manifest": str(path),
            "signed": manifest["signature"] is not None}


def _write_verdict(state: pathlib.Path, run_id: str, verdict: dict[str, Any]) -> None:
    directory = state / "run-manifests"
    directory.mkdir(parents=True, exist_ok=True)
    verdict_path = directory / f"{run_id}.verdict.json"
    verdict_path.write_text(json.dumps(verdict, sort_keys=True) + "\n", encoding="utf-8")
    observation = {
        "ts": verdict["checked_at"],
        "sensor": "runmanifest",
        "subject": f"runmanifest-{run_id}",
        # green must NOT masquerade as blindness: ok rows are skipped by
        # compare, alarm raises missing-steps, unknown raises unverified.
        "status": {"red": "alarm", "green": "ok"}.get(verdict["status"], "unknown"),
        "evidence": str(verdict_path),
        "detail": verdict["reason"],
        "metrics": {name: len(verdict[name]) for name in
                    ("missing", "unexpected", "duplicates", "reordered")},
    }
    with (state / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, sort_keys=True) + "\n")


def _base_verdict(run_id: str, status: str, reason: str) -> dict[str, Any]:
    return {
        "schema": "run-verdict", "rev": 1, "run_id": run_id,
        "status": status, "missing": [], "unexpected": [],
        "duplicates": [], "reordered": [], "reason": reason,
        "checked_at": _now(), "signature": None,
    }


def verify(*, dept_dir: pathlib.Path | str, state_dir: pathlib.Path | str,
           run_id: str) -> dict[str, Any]:
    """Verify the manifest signature and compare its roster with run rows."""
    del dept_dir  # Reserved by the frozen interface for later release checks.
    state = pathlib.Path(state_dir)
    manifest_path = state / "run-manifests" / f"{run_id}.json"
    try:
        manifest = _load_json(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
            raise ValueError
    except (OSError, ValueError, json.JSONDecodeError):
        verdict = _base_verdict(run_id, "unknown", "manifest_missing")
        _write_verdict(state, run_id, verdict)
        return verdict

    signature = manifest.get("signature")
    if signature is not None and os.environ.get("OE_KERNEL_SIGNING_KEY"):
        try:
            valid = isinstance(signature, str) and _local_signer().verify(
                _canonical_without_signature(manifest), signature
            )
        except Exception:
            valid = False
        if not valid:
            verdict = _base_verdict(run_id, "red", "signature_invalid")
            _write_verdict(state, run_id, verdict)
            return verdict

    roster = manifest.get("roster")
    if not isinstance(roster, list):
        verdict = _base_verdict(run_id, "red", "manifest_invalid")
        _write_verdict(state, run_id, verdict)
        return verdict
    rows = []
    rows_path = state / "runs-v2.jsonl"
    if rows_path.exists():
        with rows_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if isinstance(row, dict) and row.get("run_id") == run_id:
                    rows.append(row)

    ordered_roster = sorted(roster, key=lambda item: item["ordinal"])
    roster_nodes = [item["node"] for item in ordered_roster]
    required = [item["node"] for item in ordered_roster if item["required"]]
    observed_nodes = [row.get("node") for row in rows]
    missing = [node for node in required if node not in observed_nodes]
    unexpected = sorted({node for node in observed_nodes if node not in roster_nodes},
                        key=str)
    ok_counts = Counter(row.get("node") for row in rows if row.get("status") == "ok")
    duplicates = [node for node in roster_nodes if ok_counts[node] > 1]
    first_ts: dict[str, Any] = {}
    for row in rows:
        node = row.get("node")
        if node in required and node not in first_ts:
            first_ts[node] = row.get("ts", "")
    observed_required = sorted(first_ts, key=lambda node: first_ts[node])
    expected_observed = [node for node in required if node in first_ts]
    reordered = observed_required if observed_required != expected_observed else []

    findings = any((missing, unexpected, duplicates, reordered))
    reason_parts = ["differences_found" if findings else "ok"]
    if signature is None:
        reason_parts.append("unsigned")
    verdict = _base_verdict(run_id, "red" if findings else "green",
                            ",".join(reason_parts))
    verdict.update(missing=missing, unexpected=unexpected,
                   duplicates=duplicates, reordered=reordered)
    _write_verdict(state, run_id, verdict)
    return verdict


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    mint_parser = commands.add_parser("mint")
    mint_parser.add_argument("--department", required=True)
    mint_parser.add_argument("--dept-dir", required=True)
    mint_parser.add_argument("--state-dir", required=True)
    mint_parser.add_argument("--trigger", required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--dept-dir", required=True)
    verify_parser.add_argument("--state-dir", required=True)
    verify_parser.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "mint":
            result = mint(department=args.department, dept_dir=args.dept_dir,
                          state_dir=args.state_dir, trigger=args.trigger)
            print(json.dumps(result, sort_keys=True))
            return 0
        result = verify(dept_dir=args.dept_dir, state_dir=args.state_dir,
                        run_id=args.run_id)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "green" else 2
    except ManifestRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
