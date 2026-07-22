"""Apply only commands rendered from the podcast heal allowlist."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import string
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .heal_select import (
        DEFAULT_PLAYBOOKS_PATH,
        DEFAULT_STATE_DIR,
        ImmutableHealError,
        append_heal_receipt,
        assert_heal_target_allowed,
        load_playbooks,
    )
    from . import kernel_bridge
except ImportError:  # direct script execution
    from heal_select import (
        DEFAULT_PLAYBOOKS_PATH,
        DEFAULT_STATE_DIR,
        ImmutableHealError,
        append_heal_receipt,
        assert_heal_target_allowed,
        load_playbooks,
    )
    import kernel_bridge


_SAFE_PARAMETER = re.compile(r"^[A-Za-z0-9_.@:/+-]+$")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _fields(template: str) -> set[str]:
    return {
        field
        for _, field, _, _ in string.Formatter().parse(template)
        if field is not None
    }


def render_commands(playbook: dict[str, Any], params: dict[str, str]) -> list[str]:
    """Render exact templates after proving params cannot add argv tokens."""
    expected: set[str] = set()
    for template in playbook["commands"]:
        expected.update(_fields(template))
    if set(params) != expected:
        missing = sorted(expected - set(params))
        extra = sorted(set(params) - expected)
        raise ValueError(f"parameter mismatch: missing={missing}, extra={extra}")
    for name, value in params.items():
        if not isinstance(value, str) or not _SAFE_PARAMETER.fullmatch(value):
            raise ValueError(f"unsafe value for parameter {name}")
    return [template.format_map(params) for template in playbook["commands"]]


def _attempt_key(fingerprint: str, playbook_id: str) -> str:
    return f"{fingerprint}:{playbook_id}"


def _reserve_attempt(
    state_dir: Path,
    fingerprint: str,
    playbook_id: str,
    cap: int,
    now: datetime,
) -> tuple[bool, str]:
    path = state_dir / "heal_attempts.json"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "heal_attempts.lock"
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        attempts: dict[str, dict[str, int]] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("counter is not an object")
                attempts = loaded
            except (OSError, ValueError, TypeError) as exc:
                return False, f"heal attempt counter unreadable — refused: {exc}"
        day = now.date().isoformat()
        day_counts = attempts.setdefault(day, {})
        if not isinstance(day_counts, dict):
            return False, "heal attempt counter unreadable — refused: daily counter is not an object"
        key = _attempt_key(fingerprint, playbook_id)
        count = day_counts.get(key, 0)
        if not isinstance(count, int) or count < 0:
            return False, "heal attempt counter unreadable — refused: count is invalid"
        if count >= cap:
            return False, f"max attempts per day reached ({cap})"
        day_counts[key] = count + 1
        _atomic_json(path, attempts)
        return True, f"attempt {count + 1}/{cap} reserved"


def _find_playbook(playbook_id: str, path) -> dict[str, Any] | None:
    return next(
        (playbook for playbook in load_playbooks(path) if playbook["id"] == playbook_id),
        None,
    )


def _bound_incident(
    state_dir: Path,
    fingerprint: str,
    playbook: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Return the incident only when this playbook is bound to it."""
    try:
        incidents = json.loads(
            (state_dir / "incidents.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        return None, f"incident data unavailable: {exc}"
    incident = incidents.get(fingerprint) if isinstance(incidents, dict) else None
    if not isinstance(incident, dict):
        return None, "fingerprint does not name an existing incident"
    if incident.get("state") not in {"open", "department_defect"}:
        return None, "incident is not open or a department defect"
    if incident.get("failure_class") != playbook.get("matches_failure_class"):
        return None, "incident failure class does not match the selected playbook"
    return incident, None


def _evidence_bound_params(
    playbook: dict[str, Any],
    incident: dict[str, Any],
    caller_params: dict[str, str],
) -> dict[str, str]:
    """Derive command parameters from incident evidence, never the caller."""
    expected: set[str] = set()
    for template in playbook["commands"]:
        expected.update(_fields(template))
    extra = sorted(set(caller_params) - expected)
    if extra:
        raise ValueError(f"parameter mismatch: missing=[], extra={extra}")
    if not expected:
        return {}

    evidence = incident.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("incident evidence must be a list of evidence entries")
    entries = [value for value in evidence if isinstance(value, str) and value]
    derived: dict[str, str] = {}
    if "unit" in expected:
        units = {
            value.removeprefix("systemd://")
            for value in entries
            if value.startswith("systemd://") and value.removeprefix("systemd://")
        }
        if len(units) != 1:
            raise ValueError(
                f"incident evidence must name exactly one systemd unit; found {len(units)}"
            )
        derived["unit"] = next(iter(units))
    if "path" in expected:
        paths = {value for value in entries if "://" not in value}
        if len(paths) != 1:
            raise ValueError(
                f"incident evidence must name exactly one filesystem path; found {len(paths)}"
            )
        derived["path"] = next(iter(paths))

    unsupported = sorted(expected - set(derived))
    if unsupported:
        raise ValueError(
            f"playbook has parameters without evidence derivation rules: {unsupported}"
        )
    disagreements = sorted(
        name
        for name, value in caller_params.items()
        if value != derived.get(name)
    )
    if disagreements:
        raise ValueError(
            f"caller parameters disagree with incident evidence: {disagreements}"
        )
    return derived


def apply_heal(
    state_dir,
    fingerprint: str,
    playbook_id: str,
    params: dict[str, str] | None = None,
    *,
    shadow: bool = True,
    playbooks_path=DEFAULT_PLAYBOOKS_PATH,
    executor: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Propose in shadow; execute argv-only commands in live mode."""
    state_dir = Path(state_dir)
    mode = "proposed" if shadow else "applied"
    params = params or {}
    now = now or datetime.now(timezone.utc)
    if (
        not shadow
        and Path(playbooks_path).resolve() != DEFAULT_PLAYBOOKS_PATH.resolve()
    ):
        return append_heal_receipt(
            state_dir, fingerprint=fingerprint, playbook=playbook_id, mode=mode,
            commands=[], result="refused",
            detail="live mode requires the canonical podcast playbook allowlist",
            now=now,
        )
    try:
        playbook = _find_playbook(playbook_id, playbooks_path)
    except (OSError, ValueError) as exc:
        return append_heal_receipt(
            state_dir, fingerprint=fingerprint, playbook=playbook_id, mode=mode,
            commands=[], result="refused", detail=f"playbook allowlist unavailable: {exc}",
            now=now,
        )
    if playbook is None:
        return append_heal_receipt(
            state_dir, fingerprint=fingerprint, playbook=playbook_id, mode=mode,
            commands=[], result="refused", detail="playbook id is not allowlisted", now=now,
        )
    incident, incident_refusal = _bound_incident(state_dir, fingerprint, playbook)
    if incident_refusal is not None:
        return append_heal_receipt(
            state_dir, fingerprint=fingerprint, playbook=playbook_id, mode=mode,
            commands=[], result="refused", detail=incident_refusal, now=now,
        )
    try:
        assert_heal_target_allowed(playbook["heal_target"])
        bound_params = _evidence_bound_params(playbook, incident, params)
        commands = render_commands(playbook, bound_params)
    except (ImmutableHealError, ValueError, KeyError) as exc:
        return append_heal_receipt(
            state_dir, fingerprint=fingerprint, playbook=playbook_id, mode=mode,
            commands=[], result="refused", detail=str(exc), now=now,
        )

    if shadow:
        return append_heal_receipt(
            state_dir, fingerprint=fingerprint, playbook=playbook_id,
            mode="proposed", commands=commands, result="proposed",
            detail="shadow mode; commands not executed", now=now,
        )

    try:
        kernel_bridge.require_shadow(live=True)
    except RuntimeError as exc:
        return append_heal_receipt(
            state_dir, fingerprint=fingerprint, playbook=playbook_id,
            mode="applied", commands=commands, result="refused",
            detail=str(exc), now=now,
        )

    reserved, detail = _reserve_attempt(
        state_dir,
        fingerprint,
        playbook_id,
        playbook["max_attempts_per_day"],
        now,
    )
    if not reserved:
        return append_heal_receipt(
            state_dir, fingerprint=fingerprint, playbook=playbook_id,
            mode="applied", commands=commands, result="refused", detail=detail, now=now,
        )

    run = executor or subprocess.run
    for command in commands:
        try:
            completed = run(
                shlex.split(command),
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return append_heal_receipt(
                state_dir, fingerprint=fingerprint, playbook=playbook_id,
                mode="applied", commands=commands, result="failed",
                detail=f"command execution failed: {exc}", now=now,
            )
        returncode = getattr(completed, "returncode", 1)
        if returncode != 0:
            diagnostic = (
                getattr(completed, "stderr", "")
                or getattr(completed, "stdout", "")
                or ""
            ).strip()
            suffix = f": {diagnostic}" if diagnostic else ""
            return append_heal_receipt(
                state_dir, fingerprint=fingerprint, playbook=playbook_id,
                mode="applied", commands=commands, result="failed",
                detail=f"command exited {returncode}{suffix}", now=now,
            )
    return append_heal_receipt(
        state_dir, fingerprint=fingerprint, playbook=playbook_id,
        mode="applied", commands=commands, result="proposed",
        detail=f"commands executed; verification required; {detail}", now=now,
    )


def _params(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError("params must use NAME=VALUE")
        name, item = value.split("=", 1)
        if not name or name in result:
            raise argparse.ArgumentTypeError(f"invalid or duplicate param: {name}")
        result[name] = item
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply an allowlisted podcast heal")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--playbook", required=True)
    parser.add_argument("--param", action="append", default=[])
    parser.add_argument("--playbooks", default=str(DEFAULT_PLAYBOOKS_PATH))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", dest="shadow", action="store_true")
    mode.add_argument("--live", dest="shadow", action="store_false")
    parser.set_defaults(shadow=True)
    args = parser.parse_args()
    try:
        params = _params(args.param)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    row = apply_heal(
        args.state_dir,
        args.fingerprint,
        args.playbook,
        params,
        shadow=args.shadow,
        playbooks_path=args.playbooks,
    )
    print(json.dumps(row, sort_keys=True))


if __name__ == "__main__":
    main()
