"""Assemble and gate the full source plus brand/offer context manifest."""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml


LOG = logging.getLogger(__name__)
BRAND_FIELDS = ("name", "voice_notes", "audience")
OFFER_FIELDS = ("name", "cta_url", "description")


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _placeholder(value: object) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return True
    if isinstance(value, str):
        normalized = value.strip().upper()
        return not normalized or "TODO_" in normalized or normalized == "TODO"
    if isinstance(value, list):
        return any(_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(_placeholder(item) for item in value.values())
    return False


def _packet(root: dict, source_type: str) -> dict:
    if "brand" in root or "offer" in root:
        return root
    for container_name in ("contexts", "source_types", "brands"):
        container = root.get(container_name)
        if isinstance(container, dict) and isinstance(container.get(source_type), dict):
            return container[source_type]
    value = root.get(source_type)
    return value if isinstance(value, dict) else {}


def run(
    *, candidate_path: Path, brand_path: Path, version: str,
    state_dir: Path, out: Path,
) -> int:
    try:
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        item = candidate["item"]
        raw_packet = yaml.safe_load(brand_path.read_text(encoding="utf-8"))
        if not isinstance(raw_packet, dict):
            raise ValueError("brand packet must be a YAML mapping")
        packet = _packet(raw_packet, str(item.get("source_type", "")))
        brand = packet.get("brand") if isinstance(packet.get("brand"), dict) else {}
        offer = packet.get("offer") if isinstance(packet.get("offer"), dict) else {}
        missing = []
        body_text = ""
        body_path = Path(str(item.get("body_path") or ""))
        if not str(item.get("body_path") or "").strip():
            missing.append("item.body_path")
        else:
            try:
                body_text = body_path.read_text(encoding="utf-8")
            except OSError:
                missing.append("body_text")
        if not body_text.strip() and "body_text" not in missing:
            missing.append("body_text")
        for field in BRAND_FIELDS:
            if _placeholder(brand.get(field)):
                missing.append(f"brand.{field}")
        for field in OFFER_FIELDS:
            if _placeholder(offer.get(field)):
                missing.append(f"offer.{field}")
        manifest = {
            "version": version,
            "assembled_at": datetime.now(timezone.utc).isoformat(),
            "item": item,
            "body_text": body_text,
            "brand": brand,
            "offer": offer,
            "complete": not missing,
            "missing": missing,
        }
        _write(out, manifest)
        if missing:
            item_id = str(item.get("item_id") or "context")
            _write(
                state_dir / "quarantine" / f"{item_id}.json",
                {"item_id": item_id, "reasons": missing},
            )
            return 2
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, yaml.YAMLError) as exc:
        reason = f"context source unreadable or invalid: {exc}"
        LOG.error(reason)
        _write(out, {"status": "missing", "reason": reason})
        return 3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--brand", type=Path, required=True)
    parser.add_argument("--version", default="v1")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(run(
        candidate_path=args.candidate, brand_path=args.brand,
        version=args.version, state_dir=args.state_dir, out=args.out,
    ))


if __name__ == "__main__":
    main()
