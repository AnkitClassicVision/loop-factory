"""Create or revise one sanitized social draft through an allowlisted engine."""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - guarded at runtime
    yaml = None


LOGGER = logging.getLogger("social.draft_post")
DEFAULT_CHARTER = Path(__file__).resolve().parents[1] / "charter.yaml"
SURFACE_LIMITS = {
    "linkedin_mybcat": 3000,
    "linkedin_personal": 3000,
    "linkedin_podcast": 3000,
    "facebook_mybcat": 2000,
    "instagram_mybcat": 2200,
    "tiktok_mybcat": 2200,
    "x_mybcat": 280,
    "youtube_mybcat": 5000,
    "youtube_podcast": 5000,
}
URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")
NUMBER_RE = re.compile(r"(?<![\w])(?:\$\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?%?)(?![\w])")


class GateBlocked(RuntimeError):
    """An immutable floor or finite output contract was violated."""

    def __init__(self, *reasons: str):
        self.reasons = [reason for reason in reasons if reason]
        super().__init__("; ".join(self.reasons))


class SourceUnavailable(RuntimeError):
    """A required file or model source is missing or unavailable."""


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _read_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise SourceUnavailable(f"{label} missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateBlocked(f"{label} is not readable JSON: {exc}") from exc


def _item_id(bundle: Any) -> str:
    if isinstance(bundle, dict):
        item = bundle.get("item")
        if isinstance(item, dict):
            raw = str(item.get("item_id") or "").strip()
            if raw:
                return raw
    return "unknown-item"


def _safe_item_id(item_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", item_id).strip("._")
    return safe or "unknown-item"


def _quarantine(state_dir: Path, item_id: str, reasons: list[str]) -> None:
    _write_json(
        state_dir / "quarantine" / f"{_safe_item_id(item_id)}.json",
        {
            "item_id": item_id,
            "node": "draft_post",
            "reasons": reasons,
            "status": "blocked",
        },
    )


def _record_rejected_attempt(
    state_dir: Path,
    *,
    response: dict,
    bundle: dict,
    engine: str,
    round_number: int,
) -> None:
    cta_url = response.get("cta_url")
    urls = _cta_urls(response)
    offer = bundle.get("offer")
    offer_cta_url = offer.get("cta_url") if isinstance(offer, dict) else None
    body = response.get("body")
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "engine": engine,
        "round": round_number,
        "cta_url_type": type(cta_url).__name__,
        "cta_url_present": "cta_url" in response and cta_url is not None,
        "url_count": len(urls),
        "equals_offer": len(urls) == 1 and next(iter(urls)) == offer_cta_url,
        "occurs_in_body": (
            isinstance(body, str)
            and isinstance(offer_cta_url, str)
            and offer_cta_url in body
        ),
    }
    path = state_dir / "rejected_attempts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _load_engines(path: Path) -> dict[str, dict[str, Any]]:
    if yaml is None:
        raise SourceUnavailable("PyYAML is required for the engines file")
    if not path.is_file():
        raise SourceUnavailable(f"engines file missing: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise GateBlocked(f"engines file is invalid: {exc}") from exc
    if isinstance(loaded, dict) and isinstance(loaded.get("engines"), dict):
        loaded = loaded["engines"]
    if not isinstance(loaded, dict):
        raise GateBlocked("engines file must map engine names to argv lists")
    engines: dict[str, dict[str, Any]] = {}
    for name, value in loaded.items():
        config = {"command": value} if isinstance(value, list) else value
        if not isinstance(name, str) or not isinstance(config, dict):
            raise GateBlocked("each engine must be an argv list or config object")
        argv = config.get("command")
        if not isinstance(argv, list) or not argv:
            raise GateBlocked("each engine must have a non-empty command argv list")
        if not all(isinstance(part, str) and part for part in argv):
            raise GateBlocked(f"engine {name!r} argv must contain non-empty strings")
        auth_probe = config.get("auth_probe")
        if auth_probe not in (None, []) and (
            not isinstance(auth_probe, list)
            or not all(isinstance(part, str) and part for part in auth_probe)
        ):
            raise GateBlocked(f"engine {name!r} auth_probe must be an argv list")
        engines[name] = {
            "command": list(argv),
            "auth_class": config.get("auth_class"),
            "auth_probe": list(auth_probe or []),
            "telemetry_contract": isinstance(value, dict),
        }
    return engines


def parse_engine_envelope(stdout: str, engine_cfg: dict) -> dict:
    """Parse the last complete JSON object without fabricating telemetry."""
    decoder = json.JSONDecoder()
    candidates: list[tuple[int, Any]] = []
    for index, character in enumerate(stdout):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append((index + end, value))
    empty_usage = {
        "input_tokens": None,
        "output_tokens": None,
        "cache_read": None,
        "cache_creation": None,
    }
    if not candidates:
        return {
            "model": None, "usage": empty_usage, "duration_ms": None,
            "raw_ok": False, "reason": "stdout did not contain a complete JSON object",
        }
    envelope = max(candidates, key=lambda item: item[0])[1]
    usage_raw = envelope.get("usage")
    if not isinstance(usage_raw, dict):
        usage_raw = {}
    model = envelope.get("model")
    model_usage = envelope.get("modelUsage") or envelope.get("model_usage")
    if not isinstance(model, str) and isinstance(model_usage, dict) and model_usage:
        model = next(iter(model_usage))
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens"),
        "output_tokens": ("output_tokens", "outputTokens"),
        "cache_read": ("cache_read", "cache_read_input_tokens", "cacheReadInputTokens"),
        "cache_creation": ("cache_creation", "cache_creation_input_tokens", "cacheCreationInputTokens"),
    }
    usage = {
        target: next((usage_raw[key] for key in keys if key in usage_raw), None)
        for target, keys in aliases.items()
    }
    duration = envelope.get("duration_ms", envelope.get("durationMs"))
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
        duration = None
    elif duration is not None:
        duration = int(duration)
    return {
        "model": model if isinstance(model, str) else None,
        "usage": usage,
        "duration_ms": duration,
        "raw_ok": True,
    }


def _load_charter_policy(path: Path) -> tuple[frozenset[str], int]:
    loader_path = Path(__file__).resolve().parents[3] / "factory" / "charter_loader.py"
    spec = importlib.util.spec_from_file_location("charter_loader_for_social_draft", loader_path)
    if spec is None or spec.loader is None:
        raise SourceUnavailable("charter loader could not be loaded")
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    try:
        charter = loader.load_charter(path, expect_department="social")
        return loader.engine_allowlist(charter), loader.max_edit_rounds(charter)
    except loader.CharterError as exc:
        raise GateBlocked(f"charter policy invalid: {exc}") from exc


def _engine_argv(
    engine: str,
    engines_file: Path,
    prompt: str,
    prompt_file: Path,
    allowed_engines: frozenset[str],
) -> list[str]:
    if engine not in allowed_engines:
        raise GateBlocked(
            f"engine {engine!r} is not allowlisted; allowed: "
            + ", ".join(sorted(allowed_engines))
        )
    engines = _load_engines(engines_file)
    if engine not in engines:
        raise GateBlocked(f"allowlisted engine {engine!r} is absent from engines file")
    template = engines[engine]["command"]
    if not any(
        "{prompt}" in part or "{prompt_file}" in part
        for part in template
    ):
        raise GateBlocked(
            f"engine {engine!r} argv must contain {{prompt}} or {{prompt_file}}"
        )
    try:
        return [
            part.format(prompt=prompt, prompt_file=str(prompt_file))
            for part in template
        ]
    except (KeyError, ValueError) as exc:
        raise GateBlocked(f"engine {engine!r} has an invalid argv template: {exc}") from exc


def _run_argv(argv: list[str], timeout: int) -> str:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SourceUnavailable(f"engine process unavailable: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise SourceUnavailable(
            f"engine exited {completed.returncode}: {detail[:300] or 'no detail'}"
        )
    if not completed.stdout.strip():
        raise SourceUnavailable("engine returned an empty response")
    return completed.stdout.strip()


def _auth_probe(engine_cfg: dict, timeout: int) -> bool:
    argv = engine_cfg.get("auth_probe") or []
    if not argv:
        return True
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _record_run(state_dir: Path, node: str, payload: dict) -> None:
    record_path = Path(__file__).with_name("record.py")
    spec = importlib.util.spec_from_file_location(f"social_record_for_{node}", record_path)
    if spec is None or spec.loader is None:
        raise SourceUnavailable("record writer could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Run records carry telemetry only — draft bodies/sources stay in the
    # receipt files, never in runs.jsonl.
    summary_keys = (
        "status", "error", "engine", "model", "usage",
        "duration_ms", "auth_class", "round", "surface", "pass",
    )
    summary = {key: payload[key] for key in summary_keys if key in payload}
    if isinstance(payload.get("defects"), list):
        summary["defect_codes"] = [
            d.get("code") for d in payload["defects"] if isinstance(d, dict)
        ]
    module.write_record(state_dir, node, summary, shadow=True)


def _load_kernel_bridge(path: Path):
    spec = importlib.util.spec_from_file_location("social_kernel_bridge_for_draft", path)
    if spec is None or spec.loader is None:
        raise SourceUnavailable("kernel bridge could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_engine(
    prompt: str,
    *,
    engine: str,
    engines_file: Path,
    state_dir: Path,
    no_kernel: bool,
    timeout: int,
    allowed_engines: frozenset[str],
) -> str:
    state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="social-draft-", dir=state_dir) as temp_dir:
        prompt_file = Path(temp_dir) / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        argv = _engine_argv(
            engine,
            engines_file,
            prompt,
            prompt_file,
            allowed_engines,
        )

        def runner(_prompt: str) -> str:
            return _run_argv(argv, timeout)

        bridge_path = Path(__file__).with_name("kernel_bridge.py")
        if no_kernel or not bridge_path.is_file():
            return runner(prompt)
        try:
            bridge = _load_kernel_bridge(bridge_path)
            kernel = bridge.get_kernel(state_dir)
            reservation = kernel.request_model(prompt, sanitized=True)
            return kernel.call_model(prompt, reservation["receipt"], runner=runner)
        except GateBlocked:
            raise
        except Exception as exc:
            raise SourceUnavailable(f"kernel model gateway unavailable: {exc}") from exc


def _extract_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
            return value
        except json.JSONDecodeError:
            continue
    raise GateBlocked("engine response did not contain valid JSON")


def _all_bundle_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        out: set[str] = set()
        for child in value.values():
            out.update(_all_bundle_strings(child))
        return out
    if isinstance(value, list):
        out = set()
        for child in value:
            out.update(_all_bundle_strings(child))
        return out
    return set()


def _allowed_source_ids(bundle: Any) -> list[str]:
    # Keep this derivation trivially identical to qa_post._allowed_source_ids.
    if not isinstance(bundle, dict):
        return []
    item = bundle.get("item")
    offer = bundle.get("offer")
    candidates = [
        item.get("url") if isinstance(item, dict) else None,
        item.get("item_id") if isinstance(item, dict) else None,
        item.get("title") if isinstance(item, dict) else None,
        offer.get("cta_url") if isinstance(offer, dict) else None,
    ]
    allowed: list[str] = []
    for value in candidates:
        if isinstance(value, str) and value and value not in allowed:
            allowed.append(value)
    return allowed


def _number_tokens(text: str) -> set[str]:
    return {match.group(0) for match in NUMBER_RE.finditer(text)}


def _validate_bundle(bundle: Any) -> dict:
    if not isinstance(bundle, dict):
        raise GateBlocked("bundle must be a JSON object")
    if bundle.get("sanitized") is not True:
        raise GateBlocked(
            "bundle sanitized flag must be true; raw context may not reach draft_post"
        )
    if not isinstance(bundle.get("item"), dict):
        raise GateBlocked("bundle.item must be an object")
    if not isinstance(bundle.get("body_text"), str):
        raise GateBlocked("bundle.body_text must be a string")
    if not isinstance(bundle.get("brand"), dict):
        raise GateBlocked("bundle.brand must be an object")
    if not isinstance(bundle.get("offer"), dict):
        raise GateBlocked("bundle.offer must be an object")
    if bundle.get("complete") is not True:
        raise GateBlocked("bundle.complete must be true; drafting from fragments is forbidden")
    missing = bundle.get("missing")
    if not isinstance(missing, list) or missing:
        raise GateBlocked("bundle.missing must be an empty list")
    return bundle


def _cta_urls(draft: dict) -> set[str]:
    raw = draft.get("cta_url")
    candidates: list[str]
    if isinstance(raw, str):
        candidates = [raw]
    elif isinstance(raw, list):
        candidates = [value for value in raw if isinstance(value, str)]
    else:
        candidates = []
    urls: set[str] = set()
    for value in candidates:
        urls.update(URL_RE.findall(value))
    body = draft.get("body")
    if isinstance(body, str):
        urls.update(URL_RE.findall(body))
    return urls


def _normalize_draft(
    response: Any,
    *,
    surface: str,
    engine: str,
    round_number: int,
    bundle: dict,
) -> dict:
    if not isinstance(response, dict):
        raise GateBlocked("engine draft response must be a JSON object")
    draft = {
        "surface": surface,
        "body": response.get("body"),
        "cta_url": response.get("cta_url"),
        "sources": response.get("sources"),
        "engine": engine,
        "round": round_number,
    }
    draft["thumbnail_url"] = bundle.get("thumbnail_url", "")
    reasons: list[str] = []
    body = draft["body"]
    if not isinstance(body, str) or not body.strip():
        reasons.append("draft body must be a non-empty string")
    elif len(body) > SURFACE_LIMITS[surface]:
        reasons.append(
            f"{surface} body length {len(body)} exceeds hard cap {SURFACE_LIMITS[surface]}"
        )
    if isinstance(body, str) and "\u2014" in body:
        reasons.append("draft body contains an em dash")

    cta_url = draft["cta_url"]
    urls = _cta_urls(draft)
    if not isinstance(cta_url, str) or not cta_url.strip() or len(urls) != 1:
        reasons.append("draft must contain exactly one distinct HTTP(S) cta_url")
    elif cta_url.strip() not in urls:
        reasons.append("draft cta_url must be the single CTA URL")
    elif cta_url.strip() not in _all_bundle_strings(bundle):
        reasons.append("draft cta_url is not present in the sanitized bundle")

    sources = draft["sources"]
    valid_sources: list[dict[str, str]] = []
    if not isinstance(sources, list):
        reasons.append("draft sources must be a list")
    else:
        if not sources:
            reasons.append(
                "missing_sources: draft sources must contain at least one entry"
            )
        allowed_source_ids = _allowed_source_ids(bundle)
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                reasons.append(f"sources[{index}] must be an object")
                continue
            claim = source.get("claim")
            source_ref = source.get("source")
            if not isinstance(claim, str) or not claim.strip():
                reasons.append(f"sources[{index}].claim must be a non-empty string")
                continue
            if not isinstance(source_ref, str) or not source_ref.strip():
                reasons.append(f"sources[{index}].source must be a non-empty string")
                continue
            if source_ref not in allowed_source_ids:
                reasons.append(
                    f"sources[{index}].source {source_ref!r} is not in "
                    "ALLOWED_SOURCE_IDS"
                )
                continue
            valid_sources.append({"claim": claim, "source": source_ref})
        draft["sources"] = valid_sources

    if isinstance(body, str):
        grounded = set()
        for source in valid_sources:
            grounded.update(_number_tokens(source["claim"]))
        ungrounded = sorted(_number_tokens(body) - grounded)
        if ungrounded:
            reasons.append("ungrounded number token(s): " + ", ".join(ungrounded))
    if reasons:
        raise GateBlocked(*reasons)
    return draft


def _prompt(
    bundle: dict,
    surface: str,
    *,
    round_number: int,
    prior_draft: dict | None,
    defects: list[dict] | None,
) -> str:
    hard_cap = SURFACE_LIMITS[surface]
    allowed_source_ids = _allowed_source_ids(bundle)
    enumerated_source_ids = "\n".join(
        f"{index}. {json.dumps(source_id, ensure_ascii=False)}"
        for index, source_id in enumerate(allowed_source_ids, start=1)
    )
    preferred = (
        "Prefer 1300 characters or fewer; the absolute hard cap is 3000."
        if surface.startswith("linkedin_")
        else f"The absolute hard cap is {hard_cap} characters."
    )
    offer_cta_url = bundle.get("offer", {}).get("cta_url")
    revision = ""
    if prior_draft is not None:
        revision = (
            "\nThis is an enumerate-then-edit revision. Correct every enumerated defect "
            "without introducing new claims.\nPRIOR DRAFT:\n"
            + json.dumps(prior_draft, sort_keys=True)
            + "\nENUMERATED DEFECTS:\n"
            + json.dumps(defects or [], sort_keys=True)
        )
    return f"""You are drafting a governed back-catalog social post.
Return exactly one JSON object with keys body, cta_url, and sources.
sources must be a list of objects with keys claim and source.
every sources[].source MUST be exactly one of these identifiers; every factual claim and every number in the body must appear in a sources[].claim.
Use the offer cta_url identifier only for CTA-related claims. Every draft must
contain at least one source entry. Do not invent claims, statistics, testimonials,
outcomes, or URLs.

ALLOWED_SOURCE_IDS:
{enumerated_source_ids}

Surface: {surface}
Round: {round_number}
Length: {preferred}
Use exactly one official HTTP(S) CTA URL. Do not use an em dash.
CTA CONTRACT: Set output cta_url to the exact offer.cta_url value from the
SANITIZED BUNDLE: {offer_cta_url}
Include that same URL verbatim in body. Emit NO other HTTP(S) URL anywhere.
Do not use these words or phrases: leverage, seamless, holistic, delve,
game-changer, unlock, revolutionize, transform your practice.
Avoid time-anchored language such as today, this week, currently, or latest.
Keep the voice specific, plain, and grounded in the supplied source.

SANITIZED BUNDLE:
{json.dumps(bundle, sort_keys=True)}
{revision}
"""


def _revision_inputs(
    args: argparse.Namespace,
    max_rounds: int,
) -> tuple[dict | None, list[dict] | None, int]:
    revise_value = args.revise
    revision_requested = revise_value is not None
    prior_path = args.prior_draft
    if isinstance(revise_value, str) and revise_value != "__REVISION_FLAG__":
        prior_path = Path(revise_value)
    if not revision_requested:
        if prior_path is not None or args.qa_report is not None:
            raise GateBlocked("--prior-draft/--qa-report require --revise")
        return None, None, 0
    if prior_path is None or args.qa_report is None:
        raise GateBlocked("--revise requires --prior-draft and --qa-report")
    prior = _read_json(prior_path, "prior draft")
    report = _read_json(args.qa_report, "qa report")
    if not isinstance(prior, dict):
        raise GateBlocked("prior draft must be a JSON object")
    if not isinstance(report, dict) or not isinstance(report.get("defects"), list):
        raise GateBlocked("qa report must contain defects[]")
    try:
        previous_round = int(prior.get("round"))
    except (TypeError, ValueError) as exc:
        raise GateBlocked("prior draft round must be an integer") from exc
    next_round = previous_round + 1
    if next_round > max_rounds:
        raise GateBlocked(f"revision would exceed maximum round {max_rounds}")
    if report.get("engine") == args.engine:
        raise GateBlocked(
            "cross-model edit loop required: revision engine must differ from qa engine"
        )
    return prior, report["defects"], next_round


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--surface", choices=sorted(SURFACE_LIMITS), required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--engines-file", type=Path, required=True)
    parser.add_argument("--charter", type=Path, default=DEFAULT_CHARTER)
    parser.add_argument(
        "--revise",
        nargs="?",
        const="__REVISION_FLAG__",
        default=None,
        metavar="PRIOR_DRAFT",
        help="revise; optionally supplies the prior draft path",
    )
    parser.add_argument("--prior-draft", "--draft", dest="prior_draft", type=Path)
    parser.add_argument("--qa-report", type=Path)
    parser.add_argument("--no-kernel", action="store_true")
    parser.add_argument("--engine-timeout", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args(argv)
    bundle: Any = None
    try:
        bundle = _read_json(args.bundle, "sanitized bundle")
        allowed_engines, max_rounds = _load_charter_policy(args.charter)
        if args.engine not in allowed_engines:
            raise GateBlocked(
                f"engine {args.engine!r} is not allowlisted; allowed: "
                + ", ".join(sorted(allowed_engines))
            )
        bundle = _validate_bundle(bundle)
        prior, defects, round_number = _revision_inputs(args, max_rounds)
        prompt = _prompt(
            bundle,
            args.surface,
            round_number=round_number,
            prior_draft=prior,
            defects=defects,
        )
        engine_cfg = _load_engines(args.engines_file).get(args.engine)
        if engine_cfg is None:
            raise GateBlocked(f"allowlisted engine {args.engine!r} is absent from engines file")
        if not _auth_probe(engine_cfg, args.engine_timeout):
            blocked = {
                "status": "blocked", "error": {"code": "AUTH_EXPIRED"},
                "engine": args.engine, "model": None,
                "usage": {"input_tokens": None, "output_tokens": None, "cache_read": None, "cache_creation": None},
                "duration_ms": None, "auth_class": "blocked",
            }
            _write_json(args.out, blocked)
            _record_run(args.state_dir, "draft_post", blocked)
            return 2
        stdout = _call_engine(
                prompt,
                engine=args.engine,
                engines_file=args.engines_file,
                state_dir=args.state_dir,
                no_kernel=args.no_kernel,
                timeout=args.engine_timeout,
                allowed_engines=allowed_engines,
            )
        telemetry = parse_engine_envelope(stdout, engine_cfg)
        envelope = _extract_json(stdout)
        result = envelope.get("result") if isinstance(envelope, dict) else None
        response = _extract_json(result) if isinstance(result, str) else envelope
        try:
            draft = _normalize_draft(
                response,
                surface=args.surface,
                engine=args.engine,
                round_number=round_number,
                bundle=bundle,
            )
        except GateBlocked:
            if isinstance(response, dict):
                _record_rejected_attempt(
                    args.state_dir,
                    response=response,
                    bundle=bundle,
                    engine=args.engine,
                    round_number=round_number,
                )
            raise
        if engine_cfg.get("telemetry_contract"):
            draft.update({
                "model": telemetry["model"], "usage": telemetry["usage"],
                "duration_ms": telemetry["duration_ms"],
                "auth_class": engine_cfg.get("auth_class"),
            })
        _write_json(args.out, draft)
        _record_run(args.state_dir, "draft_post", draft)
        return 0
    except GateBlocked as exc:
        reasons = exc.reasons or [str(exc)]
        item_id = _item_id(bundle)
        _write_json(args.out, {"status": "blocked", "reasons": reasons})
        _quarantine(args.state_dir, item_id, reasons)
        LOGGER.error("draft_post blocked: %s", "; ".join(reasons))
        return 2
    except SourceUnavailable as exc:
        _write_json(args.out, {"status": "missing", "reason": str(exc)})
        LOGGER.error("draft_post source unavailable: %s", exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())
