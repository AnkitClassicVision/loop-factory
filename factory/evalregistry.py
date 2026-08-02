"""Load evaluator policy and apply its deterministic verdict rules.

This module only describes and evaluates policy.  It never invokes a model.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard, not logic
    yaml = None


DEFAULT_THRESHOLDS = {
    "block_critical": 1,
    "block_major": 3,
    "revise_major": 1,
}
_THRESHOLD_FIELDS = tuple(DEFAULT_THRESHOLDS)
_SEVERITIES = {"critical", "major", "minor"}


def _field_error(field: str, message: str) -> ValueError:
    return ValueError(f"eval registry field {field}: {message}")


def _validate_tier1(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise _field_error(field, "must be a list of check ids")
    if any(not isinstance(check_id, str) or not check_id.strip() for check_id in value):
        raise _field_error(field, "must contain only non-empty string check ids")
    normalized = [check_id.strip() for check_id in value]
    if len(normalized) != len(set(normalized)):
        raise _field_error(field, "must not contain duplicate check ids")
    return normalized


def _validate_thresholds(value: Any, field: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise _field_error(field, "must be a mapping")
    missing = [name for name in _THRESHOLD_FIELDS if name not in value]
    if missing:
        raise _field_error(field, f"missing {', '.join(missing)}")
    unexpected = sorted(set(value) - set(_THRESHOLD_FIELDS))
    if unexpected:
        raise _field_error(field, f"unknown field(s): {', '.join(unexpected)}")
    normalized: dict[str, int] = {}
    for name in _THRESHOLD_FIELDS:
        threshold = value[name]
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
            raise _field_error(f"{field}.{name}", "must be a positive integer")
        normalized[name] = threshold
    if normalized["block_major"] < normalized["revise_major"]:
        raise _field_error(
            field,
            "block_major must be greater than or equal to revise_major",
        )
    return normalized


def _validate_tier2(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _field_error(field, "must be a mapping")
    required_fields = {"required", "cross_model", "golden_set", "verdict_thresholds"}
    missing = sorted(required_fields - set(value))
    if missing:
        raise _field_error(field, f"missing {', '.join(missing)}")
    unexpected = sorted(set(value) - required_fields)
    if unexpected:
        raise _field_error(field, f"unknown field(s): {', '.join(unexpected)}")
    for name in ("required", "cross_model"):
        if not isinstance(value[name], bool):
            raise _field_error(f"{field}.{name}", "must be a boolean")
    # The locked evaluator contract never permits same-engine Tier-2 judgment.
    if value["cross_model"] is not True:
        raise _field_error(f"{field}.cross_model", "must be true for Tier-2")
    golden_set = value["golden_set"]
    if golden_set is not None and (
        not isinstance(golden_set, str) or not golden_set.strip()
    ):
        raise _field_error(f"{field}.golden_set", "must be a non-empty path or null")
    return {
        "required": value["required"],
        "cross_model": value["cross_model"],
        "golden_set": golden_set.strip() if isinstance(golden_set, str) else None,
        "verdict_thresholds": _validate_thresholds(
            value["verdict_thresholds"], f"{field}.verdict_thresholds"
        ),
    }


def load_registry(path: str | Path) -> dict[str, dict[str, Any]]:
    """Parse and validate one department evaluator registry."""
    if yaml is None:
        raise ValueError("PyYAML is required to load an eval registry")
    registry_path = Path(path)
    try:
        loaded = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"eval registry path {registry_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"eval registry YAML {registry_path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise _field_error("root", "must map node classes to evaluator configs")

    registry: dict[str, dict[str, Any]] = {}
    for node_class, raw_config in loaded.items():
        if not isinstance(node_class, str) or not node_class.strip():
            raise _field_error("node_class", "must be a non-empty string")
        field = node_class.strip()
        if not isinstance(raw_config, dict):
            raise _field_error(field, "must be a mapping")
        unexpected = sorted(set(raw_config) - {"tier1", "tier2"})
        if unexpected:
            raise _field_error(field, f"unknown field(s): {', '.join(unexpected)}")
        if "tier1" not in raw_config:
            raise _field_error(f"{field}.tier1", "is required")
        config: dict[str, Any] = {
            "tier1": _validate_tier1(raw_config["tier1"], f"{field}.tier1")
        }
        if "tier2" in raw_config:
            config["tier2"] = _validate_tier2(raw_config["tier2"], f"{field}.tier2")
        registry[field] = config
    return registry


def resolve(registry: dict[str, dict[str, Any]], node_class: str) -> dict[str, Any]:
    """Return an isolated node config; unknown nodes default to Tier-1 only."""
    config = registry.get(node_class)
    if config is None:
        return {"tier1": []}
    return deepcopy(config)


def classify_defects(defects: list[dict[str, Any]], thresholds: dict[str, int]) -> str:
    """Classify weighted defects conservatively into block/revise/allow."""
    limits = _validate_thresholds(thresholds, "verdict_thresholds")
    counts = {severity: 0 for severity in _SEVERITIES}
    for defect in defects:
        severity = defect.get("severity") if isinstance(defect, dict) else None
        if severity not in _SEVERITIES:
            severity = "major"
        counts[severity] += 1
    if counts["critical"] >= limits["block_critical"]:
        return "block"
    if counts["major"] >= limits["block_major"]:
        return "block"
    if counts["major"] >= limits["revise_major"]:
        return "revise"
    return "allow"


def gating(config: dict[str, Any], golden_set_passed: bool) -> str:
    """State whether Tier-2 verdicts gate, advise, or are not configured."""
    tier2 = config.get("tier2") if isinstance(config, dict) else None
    if not isinstance(tier2, dict):
        return "none"
    if (
        tier2.get("required") is True
        and tier2.get("golden_set") is not None
        and golden_set_passed is True
    ):
        return "gating"
    return "advisory"
