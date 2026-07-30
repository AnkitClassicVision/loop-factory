"""Fail-closed deterministic safety guards for the social department."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import yaml


LOG = logging.getLogger("social.guards")
SURFACES = frozenset(
    {
        "linkedin_mybcat",
        "linkedin_personal",
        "linkedin_podcast",
        "facebook_mybcat",
        "instagram_mybcat",
        "tiktok_mybcat",
        "x_mybcat",
        "youtube_mybcat",
        "youtube_podcast",
    }
)
CONTENT_FIELDS = (
    "item_id",
    "source_type",
    "title",
    "url",
    "published_at",
    "body_path",
    "last_resurfaced_at",
    "prior_engagement",
)
INDEPENDENT_SOURCE_DENY = {
    "self",
    "department",
    "department_self_report",
    "social",
    "social_department",
}

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.I)
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}(?!\d)"
)
SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
DOB_RE = re.compile(
    r"(?i)\b(?:dob|date\s+of\s+birth)\s*[:#-]?\s*"
    r"(?:\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}|"
    r"(?:19|20)\d{2}-\d{2}-\d{2})\b"
)
BARE_DOB_RE = re.compile(
    r"(?<!\d)(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])"
    r"[/-](?:19|20)\d{2}(?!\d)"
)
INSURANCE_RE = re.compile(
    r"(?i)\b(?:insurance|member|policy|subscriber)\s*(?:id|number|no\.?)"
    r"\s*[:#-]?\s*[A-Z0-9][A-Z0-9-]{4,}\b"
)
TIME_ANCHORED_RE = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"today|tomorrow|join\s+us\s+live)\b|"
    r"\b(?:19|20)\d{2}-\d{1,2}-\d{1,2}\b|"
    r"\b\d{1,2}[/-]\d{1,2}(?:[/-](?:\d{2}|\d{4}))?\b|"
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?\b",
    re.I,
)
NAMED_PARTY_RE = re.compile(
    r"(?im)\b(?:guest|client|practice)\s*[:=-]\s*"
    r"([A-Z][A-Za-z0-9&.'’-]*(?:[ \t]+[A-Z][A-Za-z0-9&.'’-]*){0,5})"
)


class SourceMissing(RuntimeError):
    """A declared input is absent or unavailable."""


class GateBlocked(RuntimeError):
    """A safety guard denied the item."""

    def __init__(self, reason: str, *, item_id: str = "unknown"):
        super().__init__(reason)
        self.reason = reason
        self.item_id = item_id or "unknown"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _read_json(path: str | Path) -> Any:
    path = Path(path)
    if not path.exists():
        raise SourceMissing(f"source missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateBlocked(f"malformed JSON input: {path}: {exc}") from exc


def _read_yaml(path: str | Path) -> Any:
    path = Path(path)
    if not path.exists():
        raise SourceMissing(f"source missing: {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GateBlocked(f"malformed YAML input: {path}: {exc}") from exc


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        raise SourceMissing(f"source missing: {path}")
    rows: list[dict[str, Any]] = []
    line_number = 0
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
            rows.append(row)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise GateBlocked(
            f"malformed JSONL input: {path}:{line_number}: {exc}"
        ) from exc
    return rows


def _content_item(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("item"), dict):
        value = value["item"]
    if not isinstance(value, dict):
        raise GateBlocked("content item must be a JSON object")
    missing = [field for field in CONTENT_FIELDS if field not in value]
    if missing:
        raise GateBlocked(f"content item missing fields: {', '.join(missing)}")
    for field in (
        "item_id",
        "source_type",
        "title",
        "url",
        "published_at",
        "body_path",
    ):
        if not isinstance(value[field], str) or not value[field].strip():
            raise GateBlocked(f"content item field {field!r} must be non-empty text")
    if value["last_resurfaced_at"] is not None and not isinstance(
        value["last_resurfaced_at"], str
    ):
        raise GateBlocked("content item last_resurfaced_at must be text or null")
    engagement = value["prior_engagement"]
    if not isinstance(engagement, dict) or not isinstance(
        engagement.get("score"), (int, float)
    ):
        raise GateBlocked("content item prior_engagement.score must be numeric")
    canonical = {field: value[field] for field in CONTENT_FIELDS}
    canonical["prior_engagement"] = {"score": float(engagement["score"])}
    if isinstance(value.get("thumbnail_url"), str):
        canonical["thumbnail_url"] = value["thumbnail_url"]
    return canonical


def _index_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, dict) and isinstance(value.get("items"), list):
        raw_items = value["items"]
    elif isinstance(value, dict):
        raw_items = list(value.values())
    else:
        raise GateBlocked("content index must be a list or object")
    return [_content_item(item) for item in raw_items]


def _item_id_from_path(path: str | Path) -> str:
    try:
        value = _read_json(path)
        if isinstance(value, dict):
            if isinstance(value.get("item"), dict):
                value = value["item"]
            if isinstance(value.get("item_id"), str):
                return value["item_id"]
    except (SourceMissing, GateBlocked):
        pass
    return "unknown"


def _quarantine(
    state_dir: str | Path, item_id: str, guard: str, reasons: Iterable[str]
) -> None:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", item_id or "unknown")
    atomic_write_json(
        Path(state_dir) / "quarantine" / f"{safe_id}.json",
        {
            "item_id": item_id or "unknown",
            "status": "blocked",
            "guard": guard,
            "reasons": list(reasons),
            "ts": utc_now(),
        },
    )


def resolve(
    item_path: str | Path,
    index_path: str | Path,
    surface: str,
) -> dict[str, Any]:
    if surface not in SURFACES:
        raise GateBlocked(f"unknown surface: {surface}", item_id=_item_id_from_path(item_path))
    requested_raw = _read_json(item_path)
    requested = _content_item(requested_raw)
    indexed = _index_items(_read_json(index_path))
    matches = [row for row in indexed if row["item_id"] == requested["item_id"]]
    if len(matches) != 1:
        raise GateBlocked(
            f"item {requested['item_id']!r} resolved to {len(matches)} index rows",
            item_id=requested["item_id"],
        )
    canonical = matches[0]
    for identity_field in ("source_type", "url"):
        if requested[identity_field] != canonical[identity_field]:
            raise GateBlocked(
                f"item identity conflicts with index field {identity_field}",
                item_id=requested["item_id"],
            )
    return {
        "status": "resolved",
        "item": canonical,
        "surface": surface,
    }


def _body_text(item: dict[str, Any], body: str | None, body_file: str | None) -> str:
    if body is not None:
        return body
    path = Path(body_file or item["body_path"])
    if not path.exists():
        raise SourceMissing(f"source missing: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SourceMissing(f"source unavailable: {path}: {exc}") from exc


def _approved_names(value: Any) -> set[str]:
    if isinstance(value, list):
        names = value
    elif isinstance(value, dict):
        names = []
        for key in ("approved_names", "guests", "clients", "practices", "names"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                names.extend(candidate)
    else:
        raise GateBlocked("approvals YAML must be a list or mapping")
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise GateBlocked("approvals contain an invalid name")
    return {name.strip().casefold() for name in names}


def eligibility(
    item_path: str | Path,
    suppression_path: str | Path,
    *,
    approvals_path: str | Path | None = None,
    body: str | None = None,
    body_file: str | None = None,
    cta_url: str | None = None,
) -> dict[str, Any]:
    item = _content_item(_read_json(item_path))
    rows = _read_jsonl(suppression_path)
    for row in rows:
        suppressed_id = row.get("item_id")
        if not isinstance(suppressed_id, str):
            raise GateBlocked(
                "suppression row missing item_id", item_id=item["item_id"]
            )
        if suppressed_id == item["item_id"]:
            raise GateBlocked(
                "item is permanently suppressed", item_id=item["item_id"]
            )

    text = _body_text(item, body, body_file)
    if TIME_ANCHORED_RE.search(text):
        raise GateBlocked(
            "body contains time-anchored language", item_id=item["item_id"]
        )

    named_parties = [match.group(1).strip() for match in NAMED_PARTY_RE.finditer(text)]
    if named_parties:
        if approvals_path is None:
            raise GateBlocked(
                "guest/client/practice named without approvals file",
                item_id=item["item_id"],
            )
        approved = _approved_names(_read_yaml(approvals_path))
        unapproved = [name for name in named_parties if name.casefold() not in approved]
        if unapproved:
            raise GateBlocked(
                "named party lacks written approval: " + ", ".join(unapproved),
                item_id=item["item_id"],
            )

    if cta_url is not None:
        parsed = urlparse(cta_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise GateBlocked(
                "cta_url must have http(s) scheme and host", item_id=item["item_id"]
            )

    return {
        "status": "eligible",
        "item": item,
        "cta_url": cta_url,
    }


def _blocklist_tokens(value: Any) -> list[str]:
    tokens: list[str] = []
    if value is None:
        return tokens
    if isinstance(value, str):
        tokens.append(value)
    elif isinstance(value, list):
        for child in value:
            tokens.extend(_blocklist_tokens(child))
    elif isinstance(value, dict):
        for child in value.values():
            tokens.extend(_blocklist_tokens(child))
    else:
        raise GateBlocked("blocklist YAML contains a non-text token")
    cleaned = [token.strip() for token in tokens if token.strip()]
    if any(len(token) < 3 for token in cleaned):
        raise GateBlocked("blocklist tokens must contain at least three characters")
    return cleaned


def _privacy_patterns(tokens: Iterable[str]) -> list[tuple[str, re.Pattern[str]]]:
    patterns = [
        ("email", EMAIL_RE),
        ("phone", PHONE_RE),
        ("ssn", SSN_RE),
        ("dob", DOB_RE),
        ("dob", BARE_DOB_RE),
        ("insurance_id", INSURANCE_RE),
    ]
    patterns.extend(
        (
            "blocklist",
            re.compile(r"(?<!\w)" + re.escape(token) + r"(?!\w)", re.I),
        )
        for token in tokens
    )
    return patterns


def _redact_text(
    text: str, patterns: Iterable[tuple[str, re.Pattern[str]]]
) -> tuple[str, int]:
    count = 0
    redacted = text
    for _, pattern in patterns:
        redacted, matches = pattern.subn("[REDACTED]", redacted)
        count += matches
    return redacted, count


def _find_hits(text: str, patterns: Iterable[tuple[str, re.Pattern[str]]]) -> list[str]:
    return sorted({name for name, pattern in patterns if pattern.search(text)})


def _redact_value(
    value: Any,
    patterns: list[tuple[str, re.Pattern[str]]],
) -> tuple[Any, int]:
    if isinstance(value, str):
        return _redact_text(value, patterns)
    if isinstance(value, list):
        out = []
        total = 0
        for child in value:
            sanitized, count = _redact_value(child, patterns)
            out.append(sanitized)
            total += count
        return out, total
    if isinstance(value, dict):
        out_dict = {}
        total = 0
        for key, child in value.items():
            sanitized, count = _redact_value(child, patterns)
            out_dict[key] = sanitized
            total += count
        return out_dict, total
    return value, 0


def privacy(
    manifest_path: str | Path,
    blocklist_path: str | Path,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise GateBlocked("context manifest must be a JSON object")
    required = {
        "version",
        "assembled_at",
        "item",
        "body_text",
        "brand",
        "offer",
        "complete",
        "missing",
    }
    absent = sorted(required - set(manifest))
    if absent:
        raise GateBlocked("context manifest missing fields: " + ", ".join(absent))
    item = _content_item(manifest["item"])
    if manifest["complete"] is not True or manifest["missing"] != []:
        raise GateBlocked(
            "context manifest is incomplete", item_id=item["item_id"]
        )
    if not isinstance(manifest["body_text"], str):
        raise GateBlocked("context manifest body_text must be text", item_id=item["item_id"])
    if not isinstance(manifest["brand"], dict) or not isinstance(
        manifest["offer"], dict
    ):
        raise GateBlocked(
            "context manifest brand and offer must be objects",
            item_id=item["item_id"],
        )

    patterns = _privacy_patterns(_blocklist_tokens(_read_yaml(blocklist_path)))
    structural_values = {
        "version": manifest["version"],
        "assembled_at": manifest["assembled_at"],
        "item.item_id": item["item_id"],
        "item.source_type": item["source_type"],
        "item.url": item["url"],
        "item.published_at": item["published_at"],
        "item.body_path": item["body_path"],
        "item.last_resurfaced_at": item["last_resurfaced_at"],
    }
    structural_hits = {
        key: _find_hits(str(value), patterns)
        for key, value in structural_values.items()
        if value is not None and _find_hits(str(value), patterns)
    }
    if structural_hits:
        raise GateBlocked(
            "sensitive data in structural field: "
            + ", ".join(sorted(structural_hits)),
            item_id=item["item_id"],
        )

    sanitized = dict(manifest)
    sanitized_item = dict(item)
    sanitized_item["title"], title_count = _redact_text(item["title"], patterns)
    sanitized["item"] = sanitized_item
    sanitized["body_text"], body_count = _redact_text(
        manifest["body_text"], patterns
    )
    sanitized["brand"], brand_count = _redact_value(manifest["brand"], patterns)
    sanitized["offer"], offer_count = _redact_value(manifest["offer"], patterns)
    sanitized["sanitized"] = True
    sanitized["redactions"] = (
        title_count + body_count + brand_count + offer_count
    )
    return sanitized


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return str(value).strip().casefold() in {
        "true",
        "yes",
        "1",
        "strike",
        "ban",
        "breach",
        "tripped",
        "killed",
    }


def _validated_observations(path: str | Path) -> list[dict[str, Any]]:
    rows = _read_jsonl(path)
    for row in rows:
        if not isinstance(row.get("metric"), str):
            raise GateBlocked("observation row missing metric")
        if not isinstance(row.get("source"), str) or not row["source"].strip():
            raise GateBlocked("observation row missing independent source")
        if row["source"].strip().casefold() in INDEPENDENT_SOURCE_DENY:
            raise GateBlocked(
                f"observation source is not independent: {row['source']}"
            )
        if not isinstance(row.get("ts"), str) or not row["ts"].strip():
            raise GateBlocked("observation row missing timestamp")
        if "value" not in row and row.get("status") != "missing":
            raise GateBlocked("observation row must carry value or status=missing")
    return rows


def kill(
    state_dir: str | Path,
    observations_path: str | Path,
    charter_path: str | Path,
) -> dict[str, Any]:
    marker = Path(state_dir) / "KILLED"
    if marker.exists():
        raise GateBlocked("department KILLED marker is present", item_id="department")
    charter = _read_yaml(charter_path)
    if not isinstance(charter, dict) or not isinstance(charter.get("kill_if"), list):
        raise GateBlocked("charter kill_if is missing or malformed", item_id="department")
    configured = set()
    for condition in charter["kill_if"]:
        if isinstance(condition, dict):
            configured.update(str(key) for key in condition)
        else:
            raise GateBlocked("charter kill_if entry is malformed", item_id="department")

    rows = _validated_observations(observations_path)
    tripped: list[str] = []
    for row in rows:
        if row.get("status") == "missing":
            continue
        metric = row["metric"]
        value = row.get("value")
        if (
            "reputation_event" in configured
            and metric
            in {
                "reputation_event",
                "platform_strike",
                "platform_ban",
                "client_blowback",
                "public_blowback",
            }
            and _truthy(value)
        ):
            tripped.append(metric)
        elif (
            "floor_breach_on_external_action" in configured
            and metric == "floor_breach_on_external_action"
            and _truthy(value)
        ):
            tripped.append(metric)
        elif (
            "gaming_own_numbers" in configured
            and metric == "gaming_own_numbers"
            and _truthy(value)
        ):
            tripped.append(metric)
        elif (
            "cost_exceeds_value_4_consecutive_weeks_post_burn_in" in configured
            and metric in {"cost_exceeds_value_consecutive_weeks", "cost_value_bad_weeks"}
            and isinstance(value, (int, float))
            and value >= 4
        ):
            tripped.append(metric)
        elif (
            "plate_load_net_positive_2_weeks" in configured
            and metric
            in {"plate_load_net_positive_consecutive_weeks", "owner_plate_load_weeks"}
            and isinstance(value, (int, float))
            and value >= 2
        ):
            tripped.append(metric)

    if tripped:
        result = {
            "status": "killed",
            "tripped": sorted(set(tripped)),
            "ts": utc_now(),
        }
        atomic_write_json(marker, result)
        raise GateBlocked(
            "kill condition tripped: " + ", ".join(result["tripped"]),
            item_id="department",
        )
    return {"status": "clear", "tripped": [], "ts": utc_now()}


def breaker(
    state_dir: str | Path,
    observations_path: str | Path,
    surface: str,
    *,
    streak_threshold: int = 3,
) -> dict[str, Any]:
    if surface not in SURFACES:
        raise GateBlocked(f"unknown surface: {surface}", item_id=surface)
    if streak_threshold < 1:
        raise GateBlocked("streak threshold must be positive", item_id=surface)
    marker = Path(state_dir) / f"BREAKER_{surface}"
    if marker.exists():
        raise GateBlocked(f"breaker marker is present for {surface}", item_id=surface)

    rows = _validated_observations(observations_path)
    streak = 0
    tripped: list[str] = []
    for row in rows:
        if row.get("surface") not in (None, surface) or row.get("status") == "missing":
            continue
        metric = row["metric"]
        value = row.get("value")
        if metric in {
            "platform_strike",
            "platform_strike_flag",
            "platform_ban",
        } and _truthy(value):
            tripped.append(metric)
        elif metric == "delivery_failure_streak":
            if not isinstance(value, (int, float)):
                raise GateBlocked("delivery_failure_streak must be numeric", item_id=surface)
            streak = max(streak, int(value))
        elif metric == "delivery_failure":
            if _truthy(value):
                streak += 1
            else:
                streak = 0
        elif metric in {"delivery_success", "delivery_verified"} and _truthy(value):
            streak = 0
    if streak >= streak_threshold:
        tripped.append(f"delivery_failure_streak:{streak}")
    if tripped:
        result = {
            "status": "open",
            "surface": surface,
            "tripped": sorted(set(tripped)),
            "ts": utc_now(),
        }
        atomic_write_json(marker, result)
        raise GateBlocked(
            f"circuit breaker tripped for {surface}: " + ", ".join(result["tripped"]),
            item_id=surface,
        )
    return {
        "status": "clear",
        "surface": surface,
        "delivery_failure_streak": streak,
        "ts": utc_now(),
    }


def _missing_output(out: Path, reason: str) -> int:
    atomic_write_json(out, {"status": "missing", "reason": reason})
    return 3


def _blocked_output(
    out: Path,
    state_dir: Path,
    guard_name: str,
    blocked: GateBlocked,
) -> int:
    result = {
        "status": "blocked",
        "guard": guard_name,
        "reason": blocked.reason,
        "item_id": blocked.item_id,
    }
    atomic_write_json(out, result)
    _quarantine(state_dir, blocked.item_id, guard_name, [blocked.reason])
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Social safety guards")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-dir", required=True)
    common.add_argument("--out", required=True)

    resolve_parser = subparsers.add_parser("resolve", parents=[common])
    resolve_parser.add_argument("--item", required=True)
    resolve_parser.add_argument("--index", required=True)
    resolve_parser.add_argument("--surface", required=True)

    eligibility_parser = subparsers.add_parser("eligibility", parents=[common])
    eligibility_parser.add_argument("--item", required=True)
    eligibility_parser.add_argument("--suppression", required=True)
    eligibility_parser.add_argument("--approvals")
    body_group = eligibility_parser.add_mutually_exclusive_group()
    body_group.add_argument("--body")
    body_group.add_argument("--body-file")
    eligibility_parser.add_argument("--cta-url")

    privacy_parser = subparsers.add_parser("privacy", parents=[common])
    privacy_parser.add_argument("--manifest", required=True)
    privacy_parser.add_argument("--blocklist", required=True)

    kill_parser = subparsers.add_parser("kill", parents=[common])
    kill_parser.add_argument("--observations", required=True)
    kill_parser.add_argument(
        "--charter",
        default=str(Path(__file__).resolve().parent.parent / "charter.yaml"),
    )

    breaker_parser = subparsers.add_parser("breaker", parents=[common])
    breaker_parser.add_argument("--observations", required=True)
    breaker_parser.add_argument("--surface", required=True)
    breaker_parser.add_argument("--streak-threshold", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = Path(args.out)
    state_dir = Path(args.state_dir)
    item_id = _item_id_from_path(args.item) if hasattr(args, "item") else "unknown"
    try:
        if args.command == "resolve":
            result = resolve(args.item, args.index, args.surface)
        elif args.command == "eligibility":
            result = eligibility(
                args.item,
                args.suppression,
                approvals_path=args.approvals,
                body=args.body,
                body_file=args.body_file,
                cta_url=args.cta_url,
            )
        elif args.command == "privacy":
            result = privacy(args.manifest, args.blocklist)
        elif args.command == "kill":
            result = kill(args.state_dir, args.observations, args.charter)
        elif args.command == "breaker":
            result = breaker(
                args.state_dir,
                args.observations,
                args.surface,
                streak_threshold=args.streak_threshold,
            )
        else:  # pragma: no cover - argparse prevents this
            raise GateBlocked(f"unsupported guard: {args.command}", item_id=item_id)
        atomic_write_json(out, result)
        return 0
    except SourceMissing as exc:
        return _missing_output(out, str(exc))
    except GateBlocked as exc:
        if exc.item_id == "unknown":
            exc.item_id = item_id
        return _blocked_output(out, state_dir, args.command, exc)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        LOG.exception("guard %s failed closed", args.command)
        return _blocked_output(
            out,
            state_dir,
            args.command,
            GateBlocked(f"guard input or state malformed: {exc}", item_id=item_id),
        )


if __name__ == "__main__":
    raise SystemExit(main())
