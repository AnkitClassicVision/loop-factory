import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import receipts
from kernel.jsonl_store import append_jsonl

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


class GatewayDenied(RuntimeError):
    pass


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


def model_binding(prompt, sanitized=True) -> dict:
    return {"prompt_hash": prompt_hash(prompt), "sanitized": bool(sanitized)}


SCHEMA_VERSION = "step-telemetry/v1"
# Runner-injected spool location (canonical name: kernel/capabilities.py).
# When present, telemetry rows land in the spool instead of any caller-given
# canonical path; the runner stamps identity at promotion.
RECORD_SPOOL_ENV = "OE_RECORD_SPOOL"
AUTH_ROUTES = frozenset(
    {"oauth_cli", "service_oauth", "local_model", "vault_api_key", "blocked"}
)
DEFAULT_PRICE_TABLE = Path(__file__).resolve().parents[2] / "factory" / "prices.json"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}\Z")
_PROVIDERS = frozenset(
    {
        "openai",
        "anthropic",
        "aws.bedrock",
        "azure.ai.openai",
        "gcp.vertex_ai",
        "google.gemini",
        "local",
        "other",
    }
)
_FINISH_REASONS = frozenset(
    {"stop", "length", "tool_calls", "content_filter", "error", "other"}
)
_ENGINES = frozenset(
    {"codex_oauth", "claude_subscription", "glm_oauth", "glm_coding_plan", "local_model"}
)


def _optional_token(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or null")
    return value


def _optional_identifier(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} must be a safe identifier")
    return value


def _normalize_result(result: Any) -> tuple[str, dict[str, Any]]:
    """Accept legacy text runners and metadata-aware result mappings.

    Mapping keys are allowlisted. The generated text is returned to the caller
    but is deliberately excluded from telemetry.
    """
    if isinstance(result, str):
        return result, {}
    if not isinstance(result, dict) or not isinstance(result.get("output"), str):
        raise TypeError("model runner must return text or a mapping with string output")
    usage = result.get("usage") or {}
    if not isinstance(usage, dict):
        raise TypeError("model runner usage must be a mapping")
    finish_reasons = result.get("finish_reasons")
    if finish_reasons is not None:
        if isinstance(finish_reasons, str):
            finish_reasons = [finish_reasons]
        if not isinstance(finish_reasons, list):
            raise TypeError("finish_reasons must be a list of strings or null")
        if len(finish_reasons) > 8:
            raise ValueError("finish_reasons must contain at most 8 values")
        finish_reasons = [
            _optional_identifier(reason, "finish_reason") for reason in finish_reasons
        ]
        if any(reason is None for reason in finish_reasons):
            raise TypeError("finish_reasons entries must be strings")
        if any(reason not in _FINISH_REASONS for reason in finish_reasons):
            raise ValueError("finish_reasons contains an unsupported value")
    metadata = {
        "provider": _optional_identifier(result.get("provider"), "provider"),
        "request_model": _optional_identifier(result.get("request_model"), "request_model"),
        "response_model": _optional_identifier(
            result.get("response_model", result.get("model")), "response_model"
        ),
        "input_tokens": _optional_token(
            usage.get("input_tokens", result.get("input_tokens")), "input_tokens"
        ),
        "output_tokens": _optional_token(
            usage.get("output_tokens", result.get("output_tokens")), "output_tokens"
        ),
        "finish_reasons": finish_reasons,
        "auth_route": _optional_identifier(result.get("auth_route"), "auth_route"),
        "engine": _optional_identifier(result.get("engine"), "engine"),
        "estimated": result.get("estimated", False),
    }
    if not isinstance(metadata["estimated"], bool):
        raise TypeError("estimated must be boolean")
    if metadata["provider"] is not None and metadata["provider"] not in _PROVIDERS:
        raise ValueError("provider is not allowlisted")
    return result["output"], metadata


def compute_cost_usd(
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    price_table_path: str | Path = DEFAULT_PRICE_TABLE,
) -> float | None:
    """Compute token cost from the versioned table, or return null honestly."""
    if model is None or input_tokens is None or output_tokens is None:
        return None
    table = _load_price_table(price_table_path)
    if table.get("ratified") is not True:
        raise ValueError("model price table is not owner-ratified")
    price = (table.get("models") or {}).get(model)
    if not isinstance(price, dict):
        return None
    try:
        input_rate = Decimal(str(price["input_usd_per_1m_tokens"]))
        output_rate = Decimal(str(price["output_usd_per_1m_tokens"]))
        cost = (
            Decimal(input_tokens) * input_rate
            + Decimal(output_tokens) * output_rate
        ) / Decimal(1_000_000)
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return None
    return float(cost)


def _load_price_table(price_table_path: str | Path) -> dict[str, Any]:
    table = json.loads(Path(price_table_path).read_text(encoding="utf-8"))
    if not isinstance(table, dict) or table.get("schema_version") != "model-prices/v1":
        raise ValueError("unsupported model price schema")
    _optional_identifier(table.get("effective_date"), "price effective_date")
    if not isinstance(table.get("ratified"), bool):
        raise ValueError("model price table ratified must be boolean")
    models = table.get("models")
    if not isinstance(models, dict):
        raise ValueError("model price table models must be an object")
    for model_name, price in models.items():
        _optional_identifier(model_name, "price model")
        if not isinstance(price, dict):
            raise ValueError("model price entry must be an object")
    return table


def _price_model_allowlist(price_table_path: str | Path) -> frozenset[str]:
    """Return the owner-reviewed model identifiers safe to persist."""
    return frozenset(_load_price_table(price_table_path)["models"])


def call_model(
    prompt,
    receipt,
    *,
    signer,
    now,
    seen_nonces,
    revoked=frozenset(),
    runner,
    telemetry_path=None,
    department=None,
    run_id=None,
    step_id=None,
    node=None,
    operation_name="chat",
    provider_name=None,
    request_model=None,
    auth_route="blocked",
    engine=None,
    price_table_path=DEFAULT_PRICE_TABLE,
    budget_broker=None,
    budget_reservation_id=None,
) -> str:
    """Verify, execute, account, and append one payload-free telemetry row.

    Invalid declared attribution and unratified declared models refuse before
    provider invocation. Telemetry persistence is best-effort after execution:
    loss is logged and chained to an existing call failure, but telemetry loss
    alone never discards a successful provider result.
    """
    started = time.perf_counter()
    invoked = False
    output = None
    metadata: dict[str, Any] = {}
    failure: Exception | None = None
    secondary_failure: Exception | None = None
    safe_operation = None
    safe_provider = None
    safe_request_model = None
    safe_engine = None
    safe_department = None
    safe_run_id = None
    safe_step_id = None
    safe_node = None
    safe_auth_route = "blocked"
    try:
        safe_operation = _optional_identifier(operation_name, "operation_name")
        safe_provider = _optional_identifier(provider_name, "provider_name")
        if safe_provider is not None and safe_provider not in _PROVIDERS:
            safe_provider = None
            raise ValueError("provider is not allowlisted")
        safe_request_model = _optional_identifier(request_model, "request_model")
        safe_engine = _optional_identifier(engine, "engine")
        if safe_engine is not None and safe_engine not in _ENGINES:
            safe_engine = None
            raise ValueError("engine is not allowlisted")
        safe_department = _optional_identifier(department, "department")
        safe_run_id = _optional_identifier(run_id, "run_id")
        safe_step_id = _optional_identifier(step_id, "step_id")
        safe_node = _optional_identifier(node, "node")
        safe_auth_route = _optional_identifier(auth_route, "auth_route")
        if safe_auth_route not in AUTH_ROUTES:
            safe_auth_route = "blocked"
            raise ValueError("auth route is not allowlisted")
        if safe_request_model is not None:
            price_table = _load_price_table(price_table_path)
            if safe_request_model not in price_table["models"]:
                safe_request_model = None
                raise ValueError("request model is not allowlisted")
            if price_table["ratified"] is not True:
                raise ValueError("model price table is not owner-ratified")
        if receipt is None:
            raise GatewayDenied("no sanitation receipt")
        chk = receipts.verify_receipt(
            receipt,
            "model_call",
            model_binding(prompt),
            signer=signer,
            now=now,
            seen_nonces=seen_nonces,
            revoked=revoked,
        )
        if not chk.ok:
            raise GatewayDenied("receipt: " + chk.reason)
        invoked = True
        output, metadata = _normalize_result(runner(prompt))
    except Exception as exc:
        failure = exc

    resolved_route = metadata.get("auth_route") or safe_auth_route
    if resolved_route not in AUTH_ROUTES:
        if failure is None:
            failure = ValueError("auth route is not allowlisted")
        resolved_route = "blocked"
    input_tokens = metadata.get("input_tokens")
    output_tokens = metadata.get("output_tokens")
    resolved_request_model = metadata.get("request_model") or safe_request_model
    resolved_response_model = metadata.get("response_model")
    resolved_engine = metadata.get("engine") or safe_engine
    if resolved_engine is not None and resolved_engine not in _ENGINES:
        if failure is None:
            failure = ValueError("engine is not allowlisted")
        resolved_engine = None
    if resolved_request_model is not None or resolved_response_model is not None:
        try:
            model_allowlist = _price_model_allowlist(price_table_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            model_allowlist = frozenset()
            if failure is None:
                failure = exc
        if resolved_request_model not in model_allowlist:
            if failure is None:
                failure = ValueError("request model is not allowlisted")
            resolved_request_model = None
        if (
            resolved_response_model is not None
            and resolved_response_model not in model_allowlist
        ):
            if failure is None:
                failure = ValueError("response model is not allowlisted")
            resolved_response_model = None
    price_model = resolved_response_model or resolved_request_model
    try:
        cost = compute_cost_usd(
            price_model, input_tokens, output_tokens, price_table_path
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        cost = None
        if failure is None:
            failure = exc
    price_schema_version = None
    price_effective_date = None
    if price_model is not None and input_tokens is not None and output_tokens is not None:
        try:
            price_table = _load_price_table(price_table_path)
            price_schema_version = _optional_identifier(
                price_table.get("schema_version"), "price schema_version"
            )
            price_effective_date = _optional_identifier(
                price_table.get("effective_date"), "price effective_date"
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    if budget_broker is not None and budget_reservation_id is not None:
        try:
            if invoked:
                budget_broker.commit(budget_reservation_id, 1)
            else:
                budget_broker.release(budget_reservation_id)
        except Exception as exc:
            if failure is None:
                failure = exc
            else:
                secondary_failure = exc
                LOGGER.error(
                    "model budget accounting failed while preserving %s",
                    type(failure).__name__,
                    exc_info=True,
                )
    row = {
        "schema_version": SCHEMA_VERSION,
        "ts": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "gen_ai.operation.name": safe_operation,
        "gen_ai.provider.name": metadata.get("provider") or safe_provider,
        "gen_ai.request.model": resolved_request_model,
        "gen_ai.response.model": resolved_response_model,
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "gen_ai.response.finish_reasons": metadata.get("finish_reasons"),
        "duration_ms": max(0, round((time.perf_counter() - started) * 1000)),
        "error.type": type(failure).__name__ if failure is not None else None,
        "loopfactory.cost_usd": cost,
        "loopfactory.auth.route": resolved_route,
        "loopfactory.engine": resolved_engine,
        "loopfactory.price.schema_version": price_schema_version,
        "loopfactory.price.effective_date": price_effective_date,
        "loopfactory.department": safe_department,
        "loopfactory.run_id": safe_run_id,
        "loopfactory.step_id": safe_step_id,
        "loopfactory.node": safe_node,
        "loopfactory.telemetry.source": (
            "runner_reported" if metadata else "legacy_null"
        ),
        "estimated": metadata.get("estimated", False),
    }
    # Runner-mediated appends (review B1, Option C): inside a node process
    # the canonical telemetry path is unreachable — rows land in the spool
    # and only the runner's promotion step writes the canonical stream.
    spool = os.environ.get(RECORD_SPOOL_ENV)
    if spool:
        telemetry_path = Path(spool) / "telemetry.jsonl"
    telemetry_error: Exception | None = None
    if telemetry_path is not None:
        try:
            append_jsonl(telemetry_path, row)
        except Exception as exc:
            telemetry_error = exc
            LOGGER.error(
                "model telemetry append failed after %s",
                "provider invocation" if invoked else "preflight refusal",
                exc_info=True,
            )
    if failure is not None:
        if telemetry_error is not None:
            raise failure from telemetry_error
        if secondary_failure is not None:
            raise failure from secondary_failure
        raise failure
    return output
