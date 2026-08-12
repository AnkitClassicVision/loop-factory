"""Release-bound, semantically strict run-manifest minting and verification.

A verdict is green only when a signed, release-bound run contract proves every
required node reached an allowed terminal state. Structural node presence is
never enough evidence of health.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import secrets
import shlex
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from factory import node_contract


ROSTER_REV = 3
MANIFEST_REV = 2
VERDICT_REV = 2
_ALLOWED_TERMINAL_STATUSES = frozenset({"ok", "blocked", "unknown", "hold"})


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


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_runtime_path(value: Any, suffix: str) -> str | None:
    if not _nonempty_string(value):
        return None
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not str(path).startswith("runtime/"):
        return None
    return str(path) if str(path).endswith(suffix) else None


def _validate_allowed_statuses(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    if any(not isinstance(status, str) for status in value):
        return None
    if len(set(value)) != len(value) or "ok" not in value:
        return None
    if not set(value).issubset(_ALLOWED_TERMINAL_STATUSES):
        return None
    return value


def _safe_unit_path(value: Any, suffix: str) -> str | None:
    if not isinstance(value, str):
        return None
    path = pathlib.PurePosixPath(value)
    if (
        len(path.parts) != 2
        or path.parts[0] != "systemd"
        or path.suffix != suffix
        or path.name != value.rsplit("/", 1)[-1]
    ):
        return None
    return str(path)


def _validate_legacy_roster(value: dict[str, Any], department: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read historical rev2 fixtures; C1/C2 never treats them as bound."""
    if value.get("department") != department or not isinstance(value.get("nodes"), list):
        raise ManifestRefused("roster_schema_invalid")
    entrypoint = value.get("entrypoint")
    if not isinstance(entrypoint, dict) or set(entrypoint) != {
        "timer", "service", "path", "driver", "timer_source", "service_source"
    }:
        raise ManifestRefused("entrypoint_schema_invalid")
    if not isinstance(entrypoint.get("timer"), str) or not entrypoint["timer"].endswith(".timer"):
        raise ManifestRefused("entrypoint_identity_invalid")
    if not isinstance(entrypoint.get("service"), str) or not entrypoint["service"].endswith(".service"):
        raise ManifestRefused("entrypoint_identity_invalid")
    timer_source = _safe_unit_path(entrypoint.get("timer_source"), ".timer")
    service_source = _safe_unit_path(entrypoint.get("service_source"), ".service")
    entrypoint_path = _safe_runtime_path(entrypoint.get("path"), ".sh")
    driver = entrypoint["driver"]
    driver_path = _safe_runtime_path(driver.get("path"), ".py") if isinstance(driver, dict) else None
    if not timer_source or not service_source or not entrypoint_path or not isinstance(driver, dict) or set(driver) != {"node", "path"} or not _nonempty_string(driver.get("node")) or not driver_path:
        raise ManifestRefused("entrypoint_schema_invalid")
    seen_nodes: set[str] = set()
    seen_ordinals: set[int] = set()
    for entry in value["nodes"]:
        if not isinstance(entry, dict) or set(entry) != {"ordinal", "node", "required", "allowed_terminal_statuses"}:
            raise ManifestRefused("roster_schema_invalid")
        if (not isinstance(entry["ordinal"], int) or isinstance(entry["ordinal"], bool)
                or entry["ordinal"] < 1 or not _nonempty_string(entry["node"])
                or not isinstance(entry["required"], bool)
                or _validate_allowed_statuses(entry["allowed_terminal_statuses"]) is None
                or entry["node"] in seen_nodes or entry["ordinal"] in seen_ordinals):
            raise ManifestRefused("roster_schema_invalid")
        seen_nodes.add(entry["node"])
        seen_ordinals.add(entry["ordinal"])
    if driver["node"] not in seen_nodes or not any(entry["node"] == driver["node"] and entry["required"] for entry in value["nodes"]):
        raise ManifestRefused("driver_not_required")
    return value["nodes"], {
        "timer": entrypoint["timer"], "service": entrypoint["service"],
        "timer_source": timer_source, "service_source": service_source,
        "path": entrypoint_path, "driver": {"node": driver["node"], "path": driver_path},
    }


def _validate_roster(value: Any, department: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(value, dict):
        raise ManifestRefused("roster_schema_invalid")
    if value.get("schema") != "run-roster":
        raise ManifestRefused("roster_schema_invalid")
    if value.get("rev") == 2:
        return _validate_legacy_roster(value, department)
    if value.get("rev") != ROSTER_REV:
        raise ManifestRefused("roster_schema_invalid")
    if value.get("department") != department or not isinstance(value.get("nodes"), list):
        raise ManifestRefused("roster_schema_invalid")
    contract = value.get("contract")
    if not isinstance(contract, dict) or set(contract) != {"schema", "path", "sha256"}:
        raise ManifestRefused("roster_contract_invalid")
    if contract.get("schema") != node_contract.SCHEMA or contract.get("path") != node_contract.CONTRACT_FILE:
        raise ManifestRefused("roster_contract_invalid")
    digest = contract.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ManifestRefused("roster_contract_invalid")

    entrypoint = value.get("entrypoint")
    if not isinstance(entrypoint, dict) or set(entrypoint) != {
        "timer", "service", "path", "driver", "timer_source", "service_source"
    }:
        raise ManifestRefused("entrypoint_schema_invalid")
    timer = entrypoint.get("timer")
    service = entrypoint.get("service")
    if (
        not isinstance(timer, str)
        or pathlib.PurePosixPath(timer).name != timer
        or not timer.endswith(".timer")
        or not isinstance(service, str)
        or pathlib.PurePosixPath(service).name != service
        or not service.endswith(".service")
    ):
        raise ManifestRefused("entrypoint_identity_invalid")
    timer_source = _safe_unit_path(entrypoint.get("timer_source"), ".timer")
    service_source = _safe_unit_path(entrypoint.get("service_source"), ".service")
    if not timer_source or not service_source:
        raise ManifestRefused("entrypoint_schema_invalid")
    entrypoint_path = _safe_runtime_path(entrypoint.get("path"), ".sh")
    driver = entrypoint.get("driver")
    if not entrypoint_path or not isinstance(driver, dict) or set(driver) != {"node", "subgraph", "node_id", "impl", "path"}:
        raise ManifestRefused("entrypoint_schema_invalid")
    driver_path = _safe_runtime_path(driver.get("path"), ".py")
    driver_impl = _safe_runtime_path(driver.get("impl"), ".py")
    if (
        not _nonempty_string(driver.get("node"))
        or not _nonempty_string(driver.get("subgraph"))
        or not _nonempty_string(driver.get("node_id"))
        or not driver_path
        or driver_impl != driver_path
    ):
        raise ManifestRefused("entrypoint_schema_invalid")

    nodes = value["nodes"]
    seen_nodes: set[tuple[str, str, str]] = set()
    seen_ordinals: set[int] = set()
    for entry in nodes:
        if not isinstance(entry, dict) or set(entry) != {
            "ordinal", "node", "subgraph", "node_id", "impl", "required", "allowed_terminal_statuses"
        }:
            raise ManifestRefused("roster_schema_invalid")
        ordinal = entry["ordinal"]
        node = entry["node"]
        subgraph = entry["subgraph"]
        node_id = entry["node_id"]
        impl = _safe_runtime_path(entry.get("impl"), ".py") or _safe_runtime_path(entry.get("impl"), ".sh")
        required = entry["required"]
        allowed = _validate_allowed_statuses(entry["allowed_terminal_statuses"])
        if (
            not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or ordinal < 1
            or not _nonempty_string(node)
            or not _nonempty_string(subgraph)
            or not _nonempty_string(node_id)
            or not impl
            or not isinstance(required, bool)
            or allowed is None
            or (subgraph, node_id, impl) in seen_nodes
            or ordinal in seen_ordinals
        ):
            raise ManifestRefused("roster_schema_invalid")
        seen_nodes.add((subgraph, node_id, impl))
        seen_ordinals.add(ordinal)

    driver_identity = (driver["subgraph"], driver["node_id"], driver["impl"])
    if driver_identity not in seen_nodes or not any(
        (entry["subgraph"], entry["node_id"], entry["impl"]) == driver_identity and entry["required"] for entry in nodes
    ):
        raise ManifestRefused("driver_not_required")
    return nodes, {
        "timer": timer,
        "service": service,
        "timer_source": timer_source,
        "service_source": service_source,
        "path": entrypoint_path,
        "driver": {
            "node": driver["node"],
            "subgraph": driver["subgraph"],
            "node_id": driver["node_id"],
            "impl": driver["impl"],
            "path": driver_path,
        },
    }


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


def _artifact_index(release: Any) -> dict[str, str]:
    if not isinstance(release, dict) or not isinstance(release.get("artifacts"), list):
        raise ManifestRefused("release_schema_invalid")
    indexed: dict[str, str] = {}
    for entry in release["artifacts"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ManifestRefused("release_schema_invalid")
        path = entry.get("path")
        digest = entry.get("sha256")
        if (
            not isinstance(path, str)
            or not path.strip()
            or not isinstance(digest, str)
            or not digest.strip()
            or path in indexed
        ):
            raise ManifestRefused("release_schema_invalid")
        indexed[path] = digest
    return indexed


def _active_source_ref(dept_path: pathlib.Path) -> str:
    supplied = os.environ.get("LOOP_FACTORY_SOURCE_REF")
    if supplied:
        return supplied.strip()
    repo = dept_path.parent.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError as exc:
        raise ManifestRefused("active_source_ref_unverifiable") from exc
    ref = result.stdout.strip()
    if result.returncode != 0 or not ref:
        raise ManifestRefused("active_source_ref_unverifiable")
    return ref


def _load_authority_map(dept_path: pathlib.Path, department: str) -> tuple[bytes, dict[str, Any]]:
    path = dept_path / "authority-map.json"
    try:
        payload = path.read_bytes()
        from factory.authority import load

        return payload, load(path, department=department)
    except (ImportError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise ManifestRefused("authority_map_invalid") from exc


def _unit_rows(path: pathlib.Path) -> list[str]:
    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ManifestRefused("entrypoint_unit_unreadable") from exc
    return [row.strip() for row in rows if row.strip() and not row.lstrip().startswith(("#", ";"))]


def _verify_installed_entrypoint(dept_path: pathlib.Path, entrypoint: dict[str, Any]) -> None:
    """Fail closed unless one installed timer/service pair targets this release path."""
    configured = os.environ.get("LOOP_FACTORY_SYSTEMD_DIR")
    unit_dir = pathlib.Path(configured) if configured else pathlib.Path.home() / ".config" / "systemd" / "user"
    timer_path = unit_dir / entrypoint["timer"]
    service_path = unit_dir / entrypoint["service"]
    timer_rows = _unit_rows(timer_path)
    service_rows = _unit_rows(service_path)
    repo_root = str(dept_path.parents[1].resolve())

    def is_canonical_driver(row: str) -> bool:
        if not row.startswith("ExecStart="):
            return False
        try:
            command = shlex.split(row.removeprefix("ExecStart="))
        except ValueError:
            return False
        if "factory.run_driver" not in command:
            return False
        try:
            department_index = command.index("--department")
            root_index = command.index("--root")
            return (
                command[department_index + 1] == dept_path.name
                and pathlib.Path(command[root_index + 1]).resolve() == pathlib.Path(repo_root)
            )
        except (IndexError, ValueError):
            return False

    if sum(is_canonical_driver(row) for row in service_rows) != 1:
        raise ManifestRefused("entrypoint_service_target_mismatch")
    if not any(row.startswith("OnCalendar=") for row in timer_rows):
        raise ManifestRefused("entrypoint_timer_invalid")
    explicit_unit = f"Unit={entrypoint['service']}"
    entrypoint_path = (dept_path / entrypoint["path"]).resolve()
    runtime_dir = entrypoint_path.parent
    runtime_reference_tokens = (
        entrypoint["path"],
        str(entrypoint_path),
        f"departments/{dept_path.name}/runtime/",
    )

    def bypasses_factory_wrapper(row: str) -> bool:
        if not row.startswith("ExecStart=") or "factory.run_driver" in row:
            return False
        try:
            command = shlex.split(row.removeprefix("ExecStart="))
        except ValueError:
            return False
        if any(token in row for token in runtime_reference_tokens):
            return True
        for token in command[1:]:
            candidate = pathlib.Path(token)
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
                if resolved == entrypoint_path or runtime_dir in resolved.parents:
                    return True
                script = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(reference in script for reference in runtime_reference_tokens):
                return True
        return False

    for candidate in unit_dir.glob("*.service"):
        if candidate == service_path:
            continue
        try:
            candidate_rows = _unit_rows(candidate)
        except ManifestRefused:
            continue
        exec_rows = [row for row in candidate_rows if row.startswith("ExecStart=")]
        if any(is_canonical_driver(row) for row in exec_rows):
            raise ManifestRefused("duplicate_entrypoint_service")
        if any(bypasses_factory_wrapper(row) for row in exec_rows):
            raise ManifestRefused("bypass_entrypoint_detected")
    for candidate in unit_dir.glob("*.timer"):
        if candidate == timer_path:
            continue
        try:
            candidate_rows = _unit_rows(candidate)
        except ManifestRefused:
            continue
        if (
            explicit_unit in candidate_rows
            or candidate.stem == pathlib.Path(entrypoint["service"]).stem
        ):
            raise ManifestRefused("duplicate_entrypoint_timer")


def _verify_release_binding(
    *,
    dept_path: pathlib.Path,
    department: str,
    release_hash: str,
    release: Any,
    roster_bytes: bytes,
    authority_bytes: bytes,
    entrypoint: dict[str, Any],
) -> str:
    if not isinstance(release, dict) or release.get("hash") != release_hash:
        raise ManifestRefused("release_hash_invalid")
    source_ref = release.get("source_ref")
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise ManifestRefused("release_source_ref_missing")
    if _active_source_ref(dept_path) != source_ref:
        raise ManifestRefused("release_source_ref_mismatch")

    roster_document = json.loads(roster_bytes)
    legacy_roster = roster_document.get("rev") == 2
    contract_document = None
    contract_sha256 = None
    if not legacy_roster:
        try:
            contract_ref = roster_document["contract"]
            contract_document = node_contract.load(dept_path)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            raise ManifestRefused("node_contract_invalid") from exc
        contract_sha256 = contract_document["contract_sha256"]
        if contract_ref.get("sha256") != contract_sha256:
            raise ManifestRefused("roster_contract_digest_mismatch")
        roster_ids = {
            (department, entry["subgraph"], entry["node_id"], entry["impl"])
            for entry in roster_document.get("nodes", [])
        }
        contract_ids = {
            (row["department"], row["subgraph"], row["node_id"], row["impl"])
            for row in contract_document["nodes"]
        }
        if roster_ids != contract_ids:
            raise ManifestRefused("roster_contract_inventory_mismatch")
        if release.get("node_contract_sha256") != contract_sha256:
            raise ManifestRefused("release_contract_digest_mismatch")

    artifacts = _artifact_index(release)
    required = {
        "runtime/run-roster.json": roster_bytes,
        "authority-map.json": authority_bytes,
        entrypoint["timer_source"]: None,
        entrypoint["service_source"]: None,
        entrypoint["path"]: None,
        entrypoint["driver"]["path"]: None,
    }
    if not legacy_roster:
        required[node_contract.CONTRACT_FILE] = (dept_path / node_contract.CONTRACT_FILE).read_bytes()
    for rel_path, known_bytes in required.items():
        expected = artifacts.get(rel_path)
        if expected is None:
            raise ManifestRefused("release_artifact_missing")
        try:
            current = known_bytes if known_bytes is not None else (dept_path / rel_path).read_bytes()
        except OSError as exc:
            raise ManifestRefused("release_artifact_unreadable") from exc
        if not secrets.compare_digest(hashlib.sha256(current).hexdigest(), expected):
            raise ManifestRefused("release_artifact_hash_mismatch")
    return source_ref


def mint(*, department: str, dept_dir: pathlib.Path | str,
         state_dir: pathlib.Path | str, trigger: str) -> dict[str, Any]:
    """Mint one signed, release-bound run manifest; refuse every unknown binding."""
    dept_path = pathlib.Path(dept_dir)
    state_path = pathlib.Path(state_dir)
    try:
        release_hash = (dept_path / "releases" / "current").read_text(
            encoding="utf-8"
        ).strip()
        if not release_hash:
            raise ValueError
        release = _load_json(dept_path / "releases" / release_hash / "manifest.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ManifestRefused("release_unreadable") from exc

    roster_path = dept_path / "runtime" / "run-roster.json"
    try:
        roster_bytes = roster_path.read_bytes()
        roster_doc = json.loads(roster_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestRefused("roster_unreadable") from exc
    roster_hash = hashlib.sha256(roster_bytes).hexdigest()
    roster, entrypoint = _validate_roster(roster_doc, department)
    authority_bytes, authority_map = _load_authority_map(dept_path, department)
    source_ref = _verify_release_binding(
        dept_path=dept_path,
        department=department,
        release_hash=release_hash,
        release=release,
        roster_bytes=roster_bytes,
        authority_bytes=authority_bytes,
        entrypoint=entrypoint,
    )
    _verify_installed_entrypoint(dept_path, entrypoint)
    try:
        signer = _local_signer()
    except Exception as exc:
        raise ManifestRefused("manifest_signing_unavailable") from exc

    run_id = _new_run_id()
    manifest = {
        "schema": "run-manifest",
        "rev": MANIFEST_REV,
        "run_id": run_id,
        "department": department,
        "created_at": _now(),
        "trigger": trigger,
        "release": {"hash": release_hash, "source_ref": source_ref},
        "roster_hash": roster_hash,
        "roster_rev": roster_doc["rev"],
        "roster": roster,
        "authority_map": authority_map,
        "entrypoint": entrypoint,
        "nonce": secrets.token_hex(16),
        "action_class": "run_manifest",
        "signature": None,
    }
    if roster_doc.get("rev") == ROSTER_REV:
        manifest["contract_sha256"] = roster_doc["contract"]["sha256"]
        manifest["contract"] = roster_doc["contract"]
    manifest["signature"] = signer.sign(_canonical_without_signature(manifest))
    path = state_path / "run-manifests" / f"{run_id}.json"
    try:
        _write_exclusive(path, manifest)
    except FileExistsError as exc:
        raise ManifestRefused("manifest_exists") from exc
    return {"run_id": run_id, "manifest": str(path), "signed": True}


def _sign_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    try:
        verdict["signature"] = _local_signer().sign(_canonical_without_signature(verdict))
    except Exception:
        verdict["signature"] = None
    return verdict


def _write_verdict(state: pathlib.Path, run_id: str, verdict: dict[str, Any]) -> None:
    _sign_verdict(verdict)
    directory = state / "run-manifests"
    directory.mkdir(parents=True, exist_ok=True)
    verdict_path = directory / f"{run_id}.verdict.json"
    verdict_path.write_text(json.dumps(verdict, sort_keys=True) + "\n", encoding="utf-8")
    observation = {
        "ts": verdict["checked_at"],
        "sensor": "runmanifest",
        "subject": f"runmanifest-{run_id}",
        "status": {"red": "alarm", "green": "ok"}.get(verdict["status"], "unknown"),
        "evidence": str(verdict_path),
        "detail": verdict["reason"],
        "metrics": {
            name: len(verdict[name])
            for name in (
                "missing", "unexpected", "duplicates", "reordered",
                "semantic_failures", "malformed_records", "blocked_contract_failures",
            )
        },
    }
    with (state / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, sort_keys=True) + "\n")


def _base_verdict(
    run_id: str,
    status: str,
    reason: str,
    *,
    entrypoint_exit_code: int | None = None,
) -> dict[str, Any]:
    return {
        "schema": "run-verdict",
        "rev": VERDICT_REV,
        "run_id": run_id,
        "status": status,
        "entrypoint_exit_code": entrypoint_exit_code,
        "missing": [],
        "unexpected": [],
        "duplicates": [],
        "reordered": [],
        "semantic_failures": [],
        "malformed_records": [],
        "blocked_contract_failures": [],
        "reason": reason,
        "checked_at": _now(),
        "signature": None,
    }


def verify_signed_verdict(verdict: Any) -> bool:
    if not isinstance(verdict, dict):
        return False
    required = {
        "schema", "rev", "run_id", "status", "missing", "unexpected", "duplicates",
        "reordered", "semantic_failures", "malformed_records",
        "blocked_contract_failures", "reason", "checked_at", "signature",
    }
    current_required = required | {"entrypoint_exit_code"}
    if set(verdict) not in (required, current_required) or verdict.get("schema") != "run-verdict" or verdict.get("rev") != VERDICT_REV:
        return False
    if "entrypoint_exit_code" in verdict and (
        verdict["entrypoint_exit_code"] is not None
        and (not isinstance(verdict["entrypoint_exit_code"], int) or isinstance(verdict["entrypoint_exit_code"], bool))
    ):
        return False
    signature = verdict.get("signature")
    if not isinstance(signature, str) or not signature:
        return False
    if verdict.get("status") not in {"green", "red", "unknown"}:
        return False
    if not all(isinstance(verdict.get(field), list) for field in required & {
        "missing", "unexpected", "duplicates", "reordered", "semantic_failures",
        "malformed_records", "blocked_contract_failures",
    }):
        return False
    try:
        return _local_signer().verify(_canonical_without_signature(verdict), signature)
    except Exception:
        return False


def _manifest_is_signed(manifest: dict[str, Any]) -> bool:
    signature = manifest.get("signature")
    if not isinstance(signature, str) or not signature:
        return False
    try:
        return _local_signer().verify(_canonical_without_signature(manifest), signature)
    except Exception:
        return False


def _read_rows(path: pathlib.Path, run_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    malformed: list[str] = []
    if not path.exists():
        return rows, malformed
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows, ["runs-v2.jsonl:unreadable"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed.append(f"line:{line_number}:json")
            continue
        if not isinstance(row, dict):
            malformed.append(f"line:{line_number}:not_object")
            continue
        if row.get("run_id") != run_id:
            continue
        try:
            from factory import runrecord
            validated = runrecord.validate_record(row)
        except Exception:
            malformed.append(f"line:{line_number}:run_record")
            continue
        rows.append(validated)
    return rows, malformed


def _valid_receipts(value: Any, state_dir: pathlib.Path) -> bool:
    if not isinstance(value, list) or not value:
        return False
    root = state_dir.resolve()
    for receipt in value:
        if not isinstance(receipt, dict) or receipt.get("schema") != "file-sha256/v1":
            return False
        path = receipt.get("path")
        digest = receipt.get("sha256")
        if (
            not isinstance(path, str)
            or not path.strip()
            or pathlib.Path(path).is_absolute()
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            return False
        try:
            receipt_path = pathlib.Path(path)
            if receipt_path.is_absolute():
                return False
            candidate = (root / receipt_path).resolve()
            candidate.relative_to(root)
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except (OSError, ValueError):
            return False
        if not secrets.compare_digest(actual, digest):
            return False
    return True


def _valid_block(row: dict[str, Any]) -> bool:
    block = row.get("block")
    if not isinstance(block, dict) or set(block) != {"owner", "deadline", "next_action"}:
        return False
    if not all(_nonempty_string(block.get(field)) for field in ("owner", "deadline", "next_action")):
        return False
    try:
        deadline = datetime.fromisoformat(block["deadline"].replace("Z", "+00:00"))
    except ValueError:
        return False
    return deadline.tzinfo is not None


def _verify_binding_after_execution(
    *, dept_path: pathlib.Path, manifest: dict[str, Any]
) -> str | None:
    try:
        release = manifest["release"]
        if not isinstance(release, dict):
            raise ManifestRefused("release_schema_invalid")
        roster_path = dept_path / "runtime" / "run-roster.json"
        roster_bytes = roster_path.read_bytes()
        roster_doc = json.loads(roster_bytes)
        department = manifest.get("department")
        if not isinstance(department, str) or not department:
            raise ManifestRefused("manifest_invalid")
        roster, entrypoint = _validate_roster(roster_doc, department)
        authority_bytes, authority_map = _load_authority_map(dept_path, department)
        if roster != manifest.get("roster") or entrypoint != manifest.get("entrypoint"):
            raise ManifestRefused("roster_changed_during_run")
        if authority_map != manifest.get("authority_map"):
            raise ManifestRefused("authority_map_changed_during_run")
        release_hash = release.get("hash")
        if not isinstance(release_hash, str) or not release_hash:
            raise ManifestRefused("release_hash_invalid")
        current = (dept_path / "releases" / "current").read_text(encoding="utf-8").strip()
        if current != release_hash:
            raise ManifestRefused("release_changed_during_run")
        release_doc = _load_json(dept_path / "releases" / release_hash / "manifest.json")
        source_ref = _verify_release_binding(
            dept_path=dept_path,
            department=department,
            release_hash=release_hash,
            release=release_doc,
            roster_bytes=roster_bytes,
            authority_bytes=authority_bytes,
            entrypoint=entrypoint,
        )
        _verify_installed_entrypoint(dept_path, entrypoint)
        return None if source_ref == release.get("source_ref") else "release_source_ref_changed_during_run"
    except (ManifestRefused, OSError, ValueError, json.JSONDecodeError) as exc:
        return str(exc)


def _verify_legacy_manifest(
    *, dept_path: pathlib.Path, state: pathlib.Path, run_id: str,
    manifest: dict[str, Any], roster: list[dict[str, Any]],
    entrypoint: dict[str, Any], entrypoint_exit_code: int | None,
) -> dict[str, Any]:
    """Preserve historical fixture verification without granting C1/C2 green."""
    rows, malformed_records = _read_rows(state / "runs-v2.jsonl", run_id)
    ordered = sorted(roster, key=lambda item: item["ordinal"])
    names = [item["node"] for item in ordered]
    required = [item["node"] for item in ordered if item["required"]]
    by_node: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_node.setdefault(row["node"], []).append(row)
    missing = [node for node in required if node not in by_node]
    unexpected = sorted(set(by_node) - set(names))
    duplicates = [node for node in names if len(by_node.get(node, [])) > 1]
    semantic: list[str] = []
    blocked: list[str] = []
    if entrypoint_exit_code not in (None, 0):
        semantic.append(f"entrypoint:exit_code={entrypoint_exit_code}")
    allowed = {entry["node"]: set(entry["allowed_terminal_statuses"]) for entry in ordered}
    for node, node_rows in by_node.items():
        if node not in allowed:
            continue
        for row in node_rows:
            if row.get("department") != manifest["department"]:
                semantic.append(f"{node}:department_mismatch")
            if row.get("release") != manifest.get("release"):
                semantic.append(f"{node}:release_mismatch")
            receipts = row.get("receipts")
            if not isinstance(receipts, list) or not receipts:
                semantic.append(f"{node}:proof_missing")
            elif not _valid_receipts(receipts, state):
                semantic.append(f"{node}:proof_invalid")
            if row.get("external_actions_taken") != 0:
                semantic.append(f"{node}:external_action_claimed")
            if row["status"] not in _ALLOWED_TERMINAL_STATUSES or row["status"] not in allowed[node]:
                semantic.append(f"{node}:{row['status']}")
            elif row["status"] in {"blocked", "unknown", "hold"} and not _valid_block(row):
                blocked.append(node)
    first = {node: min(row["ts"] for row in by_node[node]) for node in required if node in by_node}
    observed = sorted(first, key=lambda node: str(first[node]))
    expected = [node for node in required if node in first]
    reordered = observed if observed != expected else []
    if entrypoint["driver"]["node"] not in by_node:
        missing.append(entrypoint["driver"]["node"])
    failures = any((missing, unexpected, duplicates, reordered, semantic, malformed_records, blocked))
    verdict = _base_verdict(
        run_id, "red" if failures else "green",
        "entrypoint_nonzero" if entrypoint_exit_code not in (None, 0)
        else "semantic_contract_failed" if failures else "ok",
        entrypoint_exit_code=entrypoint_exit_code,
    )
    verdict.update(missing=sorted(set(missing)), unexpected=unexpected, duplicates=duplicates,
                   reordered=reordered, semantic_failures=sorted(set(semantic)),
                   malformed_records=malformed_records, blocked_contract_failures=sorted(set(blocked)))
    _write_verdict(state, run_id, verdict)
    return verdict


def verify(*, dept_dir: pathlib.Path | str, state_dir: pathlib.Path | str,
           run_id: str, entrypoint_exit_code: int | None = None) -> dict[str, Any]:
    """Verify signed semantic completion, driver execution, and release binding."""
    if entrypoint_exit_code is not None and (
        not isinstance(entrypoint_exit_code, int)
        or isinstance(entrypoint_exit_code, bool)
    ):
        raise TypeError("entrypoint_exit_code must be an int or None")
    dept_path = pathlib.Path(dept_dir)
    state = pathlib.Path(state_dir)
    manifest_path = state / "run-manifests" / f"{run_id}.json"
    try:
        manifest = _load_json(manifest_path)
        if (
            not isinstance(manifest, dict)
            or manifest.get("run_id") != run_id
            or manifest.get("schema") != "run-manifest"
            or manifest.get("rev") != MANIFEST_REV
        ):
            raise ValueError
    except (OSError, ValueError, json.JSONDecodeError):
        verdict = _base_verdict(
            run_id, "unknown", "manifest_missing",
            entrypoint_exit_code=entrypoint_exit_code,
        )
        _write_verdict(state, run_id, verdict)
        return verdict

    if not _manifest_is_signed(manifest):
        verdict = _base_verdict(
            run_id, "red", "manifest_signature_invalid_or_missing",
            entrypoint_exit_code=entrypoint_exit_code,
        )
        _write_verdict(state, run_id, verdict)
        return verdict
    binding_error = _verify_binding_after_execution(dept_path=dept_path, manifest=manifest)
    if binding_error:
        verdict = _base_verdict(
            run_id, "red", binding_error,
            entrypoint_exit_code=entrypoint_exit_code,
        )
        _write_verdict(state, run_id, verdict)
        return verdict

    roster_value = manifest.get("roster")
    entrypoint = manifest.get("entrypoint")
    department = manifest.get("department")
    if not isinstance(roster_value, list) or not isinstance(department, str) or not department:
        verdict = _base_verdict(
            run_id, "red", "manifest_invalid",
            entrypoint_exit_code=entrypoint_exit_code,
        )
        _write_verdict(state, run_id, verdict)
        return verdict
    try:
        manifest_contract = manifest.get("contract")
        manifest_roster_rev = manifest.get("roster_rev", ROSTER_REV)
        roster, validated_entrypoint = _validate_roster(
            {
                "schema": "run-roster", "rev": manifest_roster_rev,
                "department": department, "contract": manifest_contract,
                "nodes": roster_value,
                "entrypoint": entrypoint,
            },
            department,
        )
    except (ManifestRefused, KeyError, TypeError):
        verdict = _base_verdict(
            run_id, "red", "manifest_invalid",
            entrypoint_exit_code=entrypoint_exit_code,
        )
        _write_verdict(state, run_id, verdict)
        return verdict

    if manifest.get("roster_rev") == 2:
        if (dept_path / node_contract.CONTRACT_FILE).is_file():
            verdict = _base_verdict(run_id, "red", "legacy_roster_rev2_not_green_capable", entrypoint_exit_code=entrypoint_exit_code)
            _write_verdict(state, run_id, verdict)
            return verdict
        return _verify_legacy_manifest(
            dept_path=dept_path, state=state, run_id=run_id, manifest=manifest,
            roster=roster, entrypoint=validated_entrypoint,
            entrypoint_exit_code=entrypoint_exit_code,
        )

    rows, malformed_records = _read_rows(state / "runs-v2.jsonl", run_id)
    try:
        contract_document = node_contract.load(dept_path)
    except (node_contract.NodeContractRefused, OSError, ValueError) as exc:
        contract_document = None
        malformed_records.append(f"node-contract:{type(exc).__name__}")
    contract_by_identity = {}
    if contract_document is not None:
        contract_by_identity = {
            (item["department"], item["subgraph"], item["node_id"], item["impl"]): item
            for item in contract_document["nodes"]
        }
    ordered_roster = sorted(roster, key=lambda item: item["ordinal"])
    roster_ids = {
        (department, item["subgraph"], item["node_id"], item["impl"]): item
        for item in ordered_roster
    }
    required_entries = [item for item in ordered_roster if item["required"]]
    required_ids = [
        (department, item["subgraph"], item["node_id"], item["impl"])
        for item in required_entries
    ]
    rows_by_identity: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    unbound_rows: list[dict[str, Any]] = []
    for row in rows:
        binding = row.get("node_contract")
        if not isinstance(binding, dict) or set(binding) != {"department", "subgraph", "node_id", "impl"}:
            unbound_rows.append(row)
            continue
        identity = (
            binding.get("department"), binding.get("subgraph"),
            binding.get("node_id"), binding.get("impl"),
        )
        rows_by_identity.setdefault(identity, []).append(row)
    missing = [entry["node"] for entry, identity in zip(required_entries, required_ids) if identity not in rows_by_identity]
    unexpected = sorted(
        [row["node"] for row in unbound_rows]
        + [rows_by_identity[identity][0]["node"] for identity in rows_by_identity if identity not in roster_ids]
    )
    duplicates = [
        roster_ids[identity]["node"] for identity in roster_ids
        if len(rows_by_identity.get(identity, [])) > 1
    ]

    semantic_failures: list[str] = []
    blocked_contract_failures: list[str] = []
    if entrypoint_exit_code not in (None, 0):
        semantic_failures.append(f"entrypoint:exit_code={entrypoint_exit_code}")
    allowed_by_identity = {
        (department, row["subgraph"], row["node_id"], row["impl"]): set(row["allowed_terminal_statuses"])
        for row in roster
    }
    for identity, node_rows in rows_by_identity.items():
        node = node_rows[0]["node"]
        if identity not in allowed_by_identity:
            continue
        for row in node_rows:
            status = row["status"]
            if row.get("department") != department:
                semantic_failures.append(f"{node}:department_mismatch")
            expected_release = manifest.get("release")
            if row.get("release") != expected_release:
                semantic_failures.append(f"{node}:release_mismatch")
            receipts = row.get("receipts")
            if not isinstance(receipts, list) or not receipts:
                semantic_failures.append(f"{node}:proof_missing")
            elif not _valid_receipts(receipts, state):
                semantic_failures.append(f"{node}:proof_invalid")
            if row.get("external_actions_taken") != 0:
                semantic_failures.append(f"{node}:external_action_claimed")
            expected_contract = contract_by_identity.get(identity)
            if expected_contract is None:
                semantic_failures.append(f"{node}:contract_identity_unknown")
            else:
                if row.get("contract_sha256") != contract_document["contract_sha256"]:
                    semantic_failures.append(f"{node}:contract_digest_mismatch")
                if row.get("work_object_ref") != expected_contract["work_object"]:
                    semantic_failures.append(f"{node}:work_object_mismatch")
                if row.get("qa_receipt_ref") != expected_contract["qa"]:
                    semantic_failures.append(f"{node}:qa_evidence_mismatch")
            if status not in _ALLOWED_TERMINAL_STATUSES or status not in allowed_by_identity[identity]:
                semantic_failures.append(f"{node}:{status}")
            elif status in {"blocked", "unknown", "hold"} and not _valid_block(row):
                blocked_contract_failures.append(node)
    if unbound_rows:
        semantic_failures.extend(f"{row['node']}:node_contract_missing" for row in unbound_rows)

    first_ts = {
        entry["node"]: min(row["ts"] for row in rows_by_identity[identity])
        for entry, identity in zip(required_entries, required_ids)
        if identity in rows_by_identity
    }
    required = [entry["node"] for entry in required_entries]
    observed_required = sorted(first_ts, key=lambda node: str(first_ts[node]))
    expected_observed = [node for node in required if node in first_ts]
    reordered = observed_required if observed_required != expected_observed else []
    driver = validated_entrypoint["driver"]
    driver_identity = (department, driver["subgraph"], driver["node_id"], driver["impl"])
    if driver_identity not in rows_by_identity:
        driver_node = driver["node"]
        missing.append(driver_node)

    findings = any((
        missing, unexpected, duplicates, reordered, semantic_failures,
        malformed_records, blocked_contract_failures,
    ))
    verdict = _base_verdict(
        run_id,
        "red" if findings else "green",
        "entrypoint_nonzero" if entrypoint_exit_code not in (None, 0)
        else "semantic_contract_failed" if findings else "ok",
        entrypoint_exit_code=entrypoint_exit_code,
    )
    verdict.update(
        missing=sorted(set(missing), key=str),
        unexpected=unexpected,
        duplicates=duplicates,
        reordered=reordered,
        semantic_failures=sorted(set(semantic_failures)),
        malformed_records=malformed_records,
        blocked_contract_failures=sorted(set(blocked_contract_failures)),
    )
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
