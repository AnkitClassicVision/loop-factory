"""Receipt-gated social dispatch.

Shadow dispatch always traverses the kernel gateway, writes only to local
synthetic sinks, and proves delivered_count == 0. A future live adapter remains
behind both promotion state and an explicit promotion-runbook flag.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import kernel_bridge
except ImportError:  # standalone CLI
    import kernel_bridge


LOG = logging.getLogger("social.dispatch")
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


class DispatchBlocked(RuntimeError):
    """The dispatch gate refused the action."""


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


def _read_object(path: str | Path, label: str) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise DispatchBlocked(f"{label} source missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchBlocked(f"{label} is malformed: {exc}") from exc
    if not isinstance(value, dict):
        raise DispatchBlocked(f"{label} must be a JSON object")
    return value


def _validated_draft(draft: dict[str, Any]) -> dict[str, Any]:
    required = {"surface", "body", "cta_url", "sources", "engine", "round"}
    missing = sorted(required - set(draft))
    if missing:
        raise DispatchBlocked("draft missing fields: " + ", ".join(missing))
    if draft["surface"] not in SURFACES:
        raise DispatchBlocked(f"unknown draft surface: {draft['surface']!r}")
    if not isinstance(draft["body"], str) or not draft["body"].strip():
        raise DispatchBlocked("draft body must be non-empty text")
    if not isinstance(draft["cta_url"], str):
        raise DispatchBlocked("draft cta_url must be text")
    if not isinstance(draft["sources"], list):
        raise DispatchBlocked("draft sources must be a list")
    if not isinstance(draft["round"], int) or draft["round"] < 1:
        raise DispatchBlocked("draft round must be a positive integer")
    return draft


def _validated_qa(report: dict[str, Any]) -> None:
    if set(("pass", "defects", "engine")) - set(report):
        raise DispatchBlocked("qa_report is missing required fields")
    if report["pass"] is not True:
        raise DispatchBlocked("qa_report did not pass")
    if report["defects"] != []:
        raise DispatchBlocked("qa_report passed while defects remain")
    if not isinstance(report["engine"], str) or not report["engine"]:
        raise DispatchBlocked("qa_report engine is invalid")


def _validated_token(token: dict[str, Any]) -> tuple[str, str]:
    receipt = token.get("receipt")
    slot = token.get("slot")
    if not isinstance(receipt, str) or not receipt or not isinstance(slot, str) or not slot:
        raise DispatchBlocked("S4 token must contain a receipt and frequency slot")
    return receipt, slot


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise DispatchBlocked("zernio returned no JSON object")


def _scheduler_ref(value: dict[str, Any]) -> str:
    candidates = [value]
    for key in ("post", "data", "result"):
        if isinstance(value.get(key), dict):
            candidates.append(value[key])
    for candidate in candidates:
        for key in ("_id", "id", "post_ref", "postRef"):
            found = candidate.get(key)
            if isinstance(found, str) and found:
                return found
    raise DispatchBlocked("zernio create response has no scheduler post reference")


def _check_stop_markers(state_dir: Path, surface: str) -> None:
    if (state_dir / "KILLED").exists():
        raise DispatchBlocked("department KILLED marker blocks dispatch")
    if (state_dir / f"BREAKER_{surface}").exists():
        raise DispatchBlocked(f"circuit breaker blocks surface {surface}")


def dispatch(
    state_dir: str | Path,
    draft: dict[str, Any],
    qa_report: dict[str, Any],
    token: dict[str, Any],
    *,
    promoted_flag: bool = False,
    delivery_mode: str | None = None,
    zernio_cmd: str = "zernio",
    simulate_sink: str | Path | None = None,
) -> dict[str, Any]:
    """Dispatch one passed draft, through the kernel, or fail closed."""
    state_dir = Path(state_dir)
    draft = _validated_draft(draft)
    _validated_qa(qa_report)
    receipt, slot = _validated_token(token)
    surface = draft["surface"]
    _check_stop_markers(state_dir, surface)

    state = kernel_bridge.autonomy_state()
    derived_mode = "simulate" if state in {"shadow", "draft_only"} else "live"
    mode = delivery_mode or derived_mode
    if mode not in {"simulate", "live"}:
        raise DispatchBlocked(f"unknown delivery mode: {mode}")
    if state in {"shadow", "draft_only"} and mode != "simulate":
        raise DispatchBlocked(
            f"{state} charter requires delivery_mode=simulate"
        )
    live = mode == "live"
    if live and not promoted_flag:
        raise DispatchBlocked(
            "live dispatch requires --i-am-promoted from the promotion runbook"
        )
    try:
        kernel_bridge.require_shadow(live=live)
    except RuntimeError as exc:
        raise DispatchBlocked(str(exc)) from exc

    fields = kernel_bridge.dispatch_fields(draft)
    kernel = kernel_bridge.get_kernel(state_dir)
    kernel_audit_sink = state_dir / "kernel" / "dispatch_sink.jsonl"
    try:
        gateway_result = kernel.send(
            fields["to"],
            fields["subject"],
            fields["body"],
            receipt,
            slot=slot,
            sink=None if live else kernel_audit_sink,
            live=live,
        )
    except Exception as exc:
        raise DispatchBlocked(f"kernel dispatch gateway refused: {exc}") from exc

    if not live:
        if (
            not isinstance(gateway_result, dict)
            or gateway_result.get("mode") != "shadow"
            or gateway_result.get("delivered") is not False
        ):
            raise DispatchBlocked("kernel simulate result did not prove zero delivery")
        post_ref = f"sim-dispatch-{secrets.token_hex(12)}"
        platform_post_id = f"sim-platform-{secrets.token_hex(12)}"
        sink_path = Path(simulate_sink or state_dir / "simulate_delivery.jsonl")
        _append_jsonl(
            sink_path,
            {
                "post_ref": post_ref,
                "platform_post_id": platform_post_id,
                "surface": surface,
                "status": "simulated",
                "source": "simulate_sink",
                "ts": utc_now(),
            },
        )
        result = {
            "post_ref": post_ref,
            "surface": surface,
            "delivered_count": 0,
            "simulated": True,
            "ts": utc_now(),
        }
        if result["delivered_count"] != 0:
            raise DispatchBlocked("shadow law violated: delivered_count was not zero")
        return result

    command = shlex.split(zernio_cmd)
    if not command:
        raise DispatchBlocked("zernio command is empty")
    proc = subprocess.run(
        command
        + [
            "posts:create",
            "--text",
            draft["body"],
            "--accounts",
            surface,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise DispatchBlocked(
            f"zernio create failed with exit {proc.returncode}: "
            f"{(proc.stderr or '').strip()[:200]}"
        )
    post_ref = _scheduler_ref(_last_json(proc.stdout or ""))
    return {
        "post_ref": post_ref,
        "surface": surface,
        "delivered_count": 1,
        "simulated": False,
        "ts": utc_now(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Receipt-gated social dispatch")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--draft", required=True)
    parser.add_argument("--qa-report", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--simulate-sink")
    parser.add_argument("--delivery-mode", choices=("simulate", "live"))
    parser.add_argument("--zernio-cmd", default="zernio")
    parser.add_argument("--i-am-promoted", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = Path(args.out)
    try:
        result = dispatch(
            args.state_dir,
            _read_object(args.draft, "draft"),
            _read_object(args.qa_report, "qa_report"),
            _read_object(args.token, "S4 token"),
            promoted_flag=args.i_am_promoted,
            delivery_mode=args.delivery_mode,
            zernio_cmd=args.zernio_cmd,
            simulate_sink=args.simulate_sink,
        )
        atomic_write_json(out, result)
        return 0
    except (
        DispatchBlocked,
        OSError,
        subprocess.SubprocessError,
        ValueError,
        RuntimeError,
        TypeError,
        KeyError,
    ) as exc:
        LOG.error("dispatch blocked: %s", exc)
        atomic_write_json(out, {"status": "blocked", "reason": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
