"""Social runtime kernel wiring.

This is the department's only route to budget, frequency, model, and dispatch
gateways. Charter budget ceilings are always supplied to the kernel.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve()
_DEPT_DIR = _HERE.parent.parent
_DEPARTMENT = _DEPT_DIR.name
_REPO = _DEPT_DIR.parent.parent
_CHARTER_PATH = _DEPT_DIR / "charter.yaml"
DISPATCH_SUBJECT = "social_publish"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


def _charter_loader():
    return _load_module(
        "social_charter_loader", _REPO / "factory" / "charter_loader.py"
    )


def _bridge():
    return _load_module("social_kernel_bridge", _REPO / "kernel" / "bridge.py")


def _load_charter() -> dict[str, Any]:
    return _charter_loader().load_charter(
        _CHARTER_PATH, expect_department=_DEPARTMENT
    )


def autonomy_state() -> str:
    """Return the validated charter autonomy state."""
    loader = _charter_loader()
    return loader.autonomy_state(_load_charter())


def get_kernel(state_dir: str | Path):
    """Return the social LockService with charter-owned budget ceilings."""
    loader = _charter_loader()
    ceilings = loader.thresholds(_load_charter())["budget_ceilings"]
    return _bridge().load_kernel(state_dir, budget_ceilings=ceilings)


def require_shadow(live: bool = False) -> None:
    """Refuse a live request while the charter remains in shadow."""
    if not live:
        return
    state = autonomy_state()
    if state == "shadow":
        raise RuntimeError(
            f"{_DEPARTMENT} autonomy_state is 'shadow'; live dispatch is refused"
        )


def dispatch_fields(draft: dict[str, Any]) -> dict[str, str]:
    """Return the exact values bound into an S4 dispatch receipt."""
    surface = draft.get("surface")
    body = draft.get("body")
    if not isinstance(surface, str) or not surface:
        raise ValueError("draft.surface must be a non-empty string")
    if not isinstance(body, str) or not body:
        raise ValueError("draft.body must be a non-empty string")
    return {
        "to": surface,
        "subject": DISPATCH_SUBJECT,
        "body": body,
        "person": f"surface:{surface}",
        "org": f"surface:{surface}",
    }


def request_dispatch_token(
    state_dir: str | Path, draft: dict[str, Any], *, ttl_s: int = 300
) -> dict[str, Any]:
    """Reserve S5 frequency and mint the S4 one-time dispatch token."""
    fields = dispatch_fields(draft)
    kernel = get_kernel(state_dir)
    return kernel.request_send(
        fields["to"],
        fields["subject"],
        fields["body"],
        person=fields["person"],
        org=fields["org"],
        ttl_s=ttl_s,
    )


def model_prompt(bundle: dict[str, Any]) -> str:
    """Return the canonical prompt payload bound into an S8 model receipt."""
    if bundle.get("sanitized") is not True:
        raise ValueError("model input lacks sanitized=true")
    return json.dumps(bundle, sort_keys=True, separators=(",", ":"))


def request_model_token(
    state_dir: str | Path, bundle: dict[str, Any], *, ttl_s: int = 300
) -> dict[str, Any]:
    """Reserve model budget and mint a receipt for this exact sanitized input."""
    prompt = model_prompt(bundle)
    return get_kernel(state_dir).request_model(
        prompt, sanitized=True, ttl_s=ttl_s
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Social kernel authorization bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)
    authorize = subparsers.add_parser(
        "authorize-dispatch", help="reserve S5 and mint an S4 dispatch token"
    )
    authorize.add_argument("--state-dir", required=True)
    authorize.add_argument("--draft", required=True)
    authorize.add_argument("--out", required=True)
    authorize.add_argument("--ttl-s", type=int, default=300)
    authorize_model = subparsers.add_parser(
        "authorize-model", help="reserve S8 and mint a sanitized model token"
    )
    authorize_model.add_argument("--state-dir", required=True)
    authorize_model.add_argument("--bundle", required=True)
    authorize_model.add_argument("--out", required=True)
    authorize_model.add_argument("--ttl-s", type=int, default=300)
    args = parser.parse_args()

    try:
        if args.command == "authorize-dispatch":
            draft = _read_json(Path(args.draft))
            token = request_dispatch_token(args.state_dir, draft, ttl_s=args.ttl_s)
        else:
            bundle = _read_json(Path(args.bundle))
            token = request_model_token(args.state_dir, bundle, ttl_s=args.ttl_s)
        _write_json(Path(args.out), token)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        _write_json(Path(args.out), {"status": "blocked", "reason": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
