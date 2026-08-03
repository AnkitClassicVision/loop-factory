"""Validate a department cadence contract and render disabled systemd units."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import yaml


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "systemd"
_DEPARTMENT = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")
_ROUTES = {"proposal", "human"}
_OVERLAP_POLICIES = {"skip", "queue"}


class CadenceError(ValueError):
    """A cadence contract or its rendered units failed closed."""


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CadenceError(f"WHY: {where} must be a mapping")
    return value


def _positive_number(value: Any, where: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise CadenceError(f"WHY: {where} must be greater than zero")


def _nonnegative_number(value: Any, where: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise CadenceError(f"WHY: {where} must be zero or greater")


def load_contract(path: str | Path) -> dict[str, Any]:
    contract_path = Path(path)
    try:
        value = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CadenceError(f"WHY: cannot read cadence contract {contract_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CadenceError(f"WHY: malformed YAML in cadence contract {contract_path}: {exc}") from exc
    return _mapping(value, "cadence contract")


def validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    department = contract.get("department")
    if not isinstance(department, str) or not _DEPARTMENT.fullmatch(department):
        raise CadenceError("WHY: department must be a lowercase systemd-safe slug")

    triggers = contract.get("triggers")
    if not isinstance(triggers, list) or not triggers:
        raise CadenceError("WHY: triggers must be a nonempty list")
    time_specs: list[dict[str, Any]] = []
    for index, raw_trigger in enumerate(triggers):
        trigger = _mapping(raw_trigger, f"triggers[{index}]")
        kind = trigger.get("kind")
        if kind not in {"time", "goal", "event"}:
            raise CadenceError(f"WHY: triggers[{index}].kind is unknown: {kind!r}")
        spec = _mapping(trigger.get("spec"), f"triggers[{index}].spec")
        if kind == "time":
            on_calendar = spec.get("on_calendar")
            if not isinstance(on_calendar, str) or not on_calendar.strip() or "\n" in on_calendar:
                raise CadenceError(f"WHY: triggers[{index}].spec.on_calendar must be a nonempty single line")
            if not isinstance(spec.get("persistent"), bool):
                raise CadenceError(f"WHY: triggers[{index}].spec.persistent must be boolean")
            if spec.get("catch_up") not in {"skip", "coalesce"}:
                raise CadenceError(f"WHY: triggers[{index}].spec.catch_up must be skip or coalesce")
            expected_persistent = spec["catch_up"] == "coalesce"
            if spec["persistent"] is not expected_persistent:
                raise CadenceError(
                    f"WHY: triggers[{index}] catch_up={spec['catch_up']} requires "
                    f"persistent={str(expected_persistent).lower()}"
                )
            time_specs.append(spec)
        else:
            source_path = spec.get("source_path")
            if not isinstance(source_path, str) or not source_path.strip() or "\n" in source_path:
                raise CadenceError(f"WHY: triggers[{index}].spec.source_path must be a nonempty path")
            cursor = _mapping(spec.get("cursor_policy"), f"triggers[{index}].spec.cursor_policy")
            if cursor.get("first_run") not in {"eof", "replay"}:
                raise CadenceError(
                    f"WHY: triggers[{index}].spec.cursor_policy.first_run must be eof or replay"
                )
    if len(time_specs) != 1:
        raise CadenceError("WHY: exactly one time trigger is required to render one timer pair")

    concurrency = _mapping(contract.get("concurrency"), "concurrency")
    maximum = concurrency.get("max_concurrent")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise CadenceError("WHY: concurrency.max_concurrent must be an integer >= 1")
    if concurrency.get("overlap_policy") not in _OVERLAP_POLICIES:
        raise CadenceError("WHY: concurrency.overlap_policy must be skip or queue")

    alerting = _mapping(contract.get("alerting"), "alerting")
    table = alerting.get("classification_table")
    if not isinstance(table, list) or not table:
        raise CadenceError("WHY: alerting.classification_table must be a nonempty list")
    codes: set[str] = set()
    for index, raw_row in enumerate(table):
        row = _mapping(raw_row, f"alerting.classification_table[{index}]")
        code = row.get("code")
        if not isinstance(code, str) or not code.strip():
            raise CadenceError(f"WHY: classification_table[{index}].code must be nonempty")
        if code in codes:
            raise CadenceError(f"WHY: classification code {code!r} is duplicated")
        codes.add(code)
        if row.get("route") not in _ROUTES:
            raise CadenceError(f"WHY: classification_table[{index}].route must be proposal or human")
    _nonnegative_number(alerting.get("dedupe_window_minutes"), "alerting.dedupe_window_minutes")
    digest = _mapping(alerting.get("digest"), "alerting.digest")
    cap = digest.get("cap_per_day")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise CadenceError("WHY: alerting.digest.cap_per_day must be an integer >= 1")
    _nonnegative_number(digest.get("cooldown_minutes"), "alerting.digest.cooldown_minutes")

    escalation = _mapping(contract.get("escalation"), "escalation")
    if not isinstance(escalation.get("owner"), str) or not escalation["owner"].strip():
        raise CadenceError("WHY: escalation.owner must be nonempty")
    _positive_number(escalation.get("sla_hours"), "escalation.sla_hours")

    activation = _mapping(contract.get("activation"), "activation")
    if activation.get("enabled_by_default") is not False:
        raise CadenceError("WHY: activation.enabled_by_default must be false; owner activation is deliberate")
    return time_specs[0]


def _render_template(path: Path, values: dict[str, str]) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CadenceError(f"WHY: cannot read systemd template {path}: {exc}") from exc
    for name, value in values.items():
        text = text.replace("{{" + name + "}}", value)
    if re.search(r"\{\{[A-Z_]+\}\}", text):
        raise CadenceError(f"WHY: unresolved marker remains in systemd template {path}")
    return text


def _parse_unit(text: str, name: str) -> dict[str, list[str]]:
    section: str | None = None
    parsed: dict[str, list[str]] = {}
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]") and len(line) > 2:
            section = line[1:-1]
            parsed.setdefault(section, [])
        elif section is None or "=" not in line or not line.split("=", 1)[0].strip():
            raise CadenceError(f"WHY: rendered {name}:{line_number} is not valid key=value unit syntax")
        else:
            parsed[section].append(line)
    if not parsed:
        raise CadenceError(f"WHY: rendered {name} has no unit sections")
    return parsed


def _verify_units(timer: str, service: str, time_spec: dict[str, Any]) -> None:
    timer_parsed = _parse_unit(timer, "timer")
    service_parsed = _parse_unit(service, "service")
    timer_rows = timer_parsed.get("Timer", [])
    expected_calendar = f"OnCalendar={time_spec['on_calendar']}"
    expected_persistent = f"Persistent={str(time_spec['persistent']).lower()}"
    calendars = [row for row in timer_rows if row.startswith("OnCalendar=")]
    persistent_rows = [row for row in timer_rows if row.startswith("Persistent=")]
    if calendars != [expected_calendar]:
        raise CadenceError("WHY: rendered timer OnCalendar does not exactly match the contract")
    if persistent_rows != [expected_persistent]:
        raise CadenceError("WHY: rendered timer Persistent does not exactly match the contract")
    for rows in service_parsed.values():
        for row in rows:
            if row.startswith("ExecStart=") and "systemctl" in row.casefold():
                raise CadenceError("WHY: rendered service ExecStart must not contain systemctl")


def render_units(
    contract: dict[str, Any],
    render_dir: str | Path,
    *,
    template_dir: str | Path = TEMPLATE_DIR,
) -> list[Path]:
    time_spec = validate_contract(contract)
    department = contract["department"]
    templates = Path(template_dir)
    values = {
        "DEPARTMENT": department,
        "REPO": str(Path.cwd().resolve()),
        "ONCALENDAR": time_spec["on_calendar"],
    }

    def once() -> tuple[str, str]:
        timer = _render_template(templates / "dept-loop.timer.tmpl", values)
        persistent = str(time_spec["persistent"]).lower()
        timer, replacements = re.subn(
            r"(?m)^Persistent=.*$", f"Persistent={persistent}", timer
        )
        if replacements != 1:
            raise CadenceError(
                "WHY: timer template must contain exactly one Persistent setting"
            )
        return timer, _render_template(templates / "dept-loop.service.tmpl", values)

    first = once()
    second = once()
    if first != second:
        raise CadenceError("WHY: rendering the same cadence contract twice was not byte-identical")
    _verify_units(first[0], first[1], time_spec)
    output = Path(render_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = [output / f"{department}-loop.timer", output / f"{department}-loop.service"]
    for path, content in zip(paths, first):
        path.write_text(content, encoding="utf-8")
    return paths


def check_contract(
    contract_path: str | Path,
    render_dir: str | Path | None = None,
    *,
    template_dir: str | Path = TEMPLATE_DIR,
) -> list[Path]:
    path = Path(contract_path)
    contract = load_contract(path)
    destination = Path(render_dir) if render_dir is not None else path.parent / "rendered-systemd"
    return render_units(contract, destination, template_dir=template_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="validate and render a cadence contract")
    check.add_argument("--contract", required=True)
    check.add_argument("--render-dir")
    args = parser.parse_args(argv)
    try:
        rendered = check_contract(args.contract, args.render_dir)
    except (CadenceError, OSError) as exc:
        if not str(exc).startswith("WHY:"):
            exc = CadenceError(f"WHY: could not render cadence units: {exc}")
        print(exc)
        return 1
    print("OK: cadence contract valid; rendered " + ", ".join(str(path) for path in rendered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
