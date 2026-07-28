"""Enumerate deterministic and cross-model defects in one social draft."""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - guarded at runtime
    yaml = None


LOGGER = logging.getLogger("social.qa_post")
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
BANNED_WORDS = (
    "leverage",
    "seamless",
    "holistic",
    "delve",
    "game-changer",
    "unlock",
    "revolutionize",
    "transform your practice",
)
URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")
NUMBER_RE = re.compile(r"(?<![\w])(?:\$\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?%?)(?![\w])")
TIME_ANCHOR_RE = re.compile(
    r"\b(?:today|tonight|yesterday|tomorrow|now|currently|recently|latest|"
    r"this\s+(?:week|month|quarter|year)|last\s+(?:week|month|quarter|year)|"
    r"next\s+(?:week|month|quarter|year)|as\s+of)\b",
    flags=re.IGNORECASE,
)


class GateBlocked(RuntimeError):
    """A governance gate refused to run QA."""

    def __init__(self, *reasons: str):
        self.reasons = [reason for reason in reasons if reason]
        super().__init__("; ".join(self.reasons))


class SourceUnavailable(RuntimeError):
    """A declared input is missing."""


class EngineUnavailable(RuntimeError):
    """The cross-model engine did not return a usable defect list."""


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
            "node": "qa_post",
            "reasons": reasons,
            "status": "blocked",
        },
    )


def _load_engines(path: Path) -> dict[str, list[str]]:
    if yaml is None:
        raise EngineUnavailable("PyYAML is required for the engines file")
    if not path.is_file():
        raise EngineUnavailable(f"engines file missing: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise EngineUnavailable(f"engines file is invalid: {exc}") from exc
    if isinstance(loaded, dict) and isinstance(loaded.get("engines"), dict):
        loaded = loaded["engines"]
    if not isinstance(loaded, dict):
        raise EngineUnavailable("engines file must map engine names to argv lists")
    engines: dict[str, list[str]] = {}
    for name, argv in loaded.items():
        if not isinstance(name, str) or not isinstance(argv, list) or not argv:
            raise EngineUnavailable("each engine must have a non-empty argv list")
        if not all(isinstance(part, str) and part for part in argv):
            raise EngineUnavailable(
                f"engine {name!r} argv must contain non-empty strings"
            )
        engines[name] = list(argv)
    return engines


def _load_charter_policy(path: Path) -> tuple[frozenset[str], int]:
    loader_path = Path(__file__).resolve().parents[3] / "factory" / "charter_loader.py"
    spec = importlib.util.spec_from_file_location("charter_loader_for_social_qa", loader_path)
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
) -> list[str]:
    engines = _load_engines(engines_file)
    if engine not in engines:
        raise EngineUnavailable(f"allowlisted engine {engine!r} is absent from engines file")
    template = engines[engine]
    if not any(
        "{prompt}" in part or "{prompt_file}" in part
        for part in template
    ):
        raise EngineUnavailable(
            f"engine {engine!r} argv must contain {{prompt}} or {{prompt_file}}"
        )
    try:
        return [
            part.format(prompt=prompt, prompt_file=str(prompt_file))
            for part in template
        ]
    except (KeyError, ValueError) as exc:
        raise EngineUnavailable(
            f"engine {engine!r} has an invalid argv template: {exc}"
        ) from exc


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
        raise EngineUnavailable(f"engine process unavailable: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise EngineUnavailable(
            f"engine exited {completed.returncode}: {detail[:300] or 'no detail'}"
        )
    if not completed.stdout.strip():
        raise EngineUnavailable("engine returned an empty response")
    return completed.stdout.strip()


def _load_kernel_bridge(path: Path):
    spec = importlib.util.spec_from_file_location("social_kernel_bridge_for_qa", path)
    if spec is None or spec.loader is None:
        raise EngineUnavailable("kernel bridge could not be loaded")
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
) -> str:
    state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="social-qa-", dir=state_dir) as temp_dir:
        prompt_file = Path(temp_dir) / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        argv = _engine_argv(engine, engines_file, prompt, prompt_file)

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
        except EngineUnavailable:
            raise
        except Exception as exc:
            raise EngineUnavailable(f"kernel model gateway unavailable: {exc}") from exc


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
    raise EngineUnavailable("engine response did not contain valid JSON")


def _number_tokens(text: str) -> set[str]:
    return {match.group(0) for match in NUMBER_RE.finditer(text)}


def _allowed_source_ids(bundle: Any) -> list[str]:
    # Keep this derivation trivially identical to draft_post._allowed_source_ids.
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


def _source_claims(draft: dict) -> list[str]:
    raw = draft.get("sources")
    if not isinstance(raw, list):
        return []
    return [
        source["claim"]
        for source in raw
        if isinstance(source, dict) and isinstance(source.get("claim"), str)
    ]


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


def deterministic_defects(draft: Any, bundle: Any) -> list[dict[str, str]]:
    defects: list[dict[str, str]] = []

    def add(code: str, detail: str) -> None:
        defects.append({"code": code, "detail": detail})

    if not isinstance(bundle, dict) or bundle.get("sanitized") is not True:
        add(
            "unsanitized_bundle",
            "bundle sanitized flag must be true before any model-capable QA",
        )
    if not isinstance(draft, dict):
        add("invalid_draft", "draft must be a JSON object")
        return defects

    body = draft.get("body")
    if not isinstance(body, str):
        add("invalid_body", "draft.body must be a string")
        body = ""
    if "\u2014" in body:
        add("em_dash", "body contains an em dash")

    folded = body.casefold()
    for phrase in BANNED_WORDS:
        if re.search(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", folded):
            add("banned_word", f"body contains banned phrase: {phrase}")

    urls = _cta_urls(draft)
    cta_url = draft.get("cta_url")
    if (
        not isinstance(cta_url, str)
        or not cta_url.strip()
        or len(urls) != 1
        or cta_url.strip() not in urls
    ):
        add(
            "cta_url_count",
            f"draft must contain exactly one distinct HTTP(S) CTA URL; found {len(urls)}",
        )

    surface = draft.get("surface")
    if surface not in SURFACE_LIMITS:
        add("unknown_surface", f"unknown surface: {surface!r}")
    elif len(body) > SURFACE_LIMITS[surface]:
        add(
            "surface_length_exceeded",
            f"{surface} body length {len(body)} exceeds hard cap {SURFACE_LIMITS[surface]}",
        )

    sources = draft.get("sources")
    if not isinstance(sources, list) or not sources:
        add(
            "missing_sources",
            "draft sources must contain at least one entry",
        )
    if isinstance(sources, list):
        allowed_source_ids = _allowed_source_ids(bundle)
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                add("invalid_source", f"sources[{index}] must be an object")
                continue
            source_ref = source.get("source")
            if not isinstance(source_ref, str) or not source_ref.strip():
                add(
                    "invalid_source",
                    f"sources[{index}].source must be a non-empty string",
                )
                continue
            if source_ref not in allowed_source_ids:
                add(
                    "invalid_source",
                    f"sources[{index}].source {source_ref!r} is not in "
                    "ALLOWED_SOURCE_IDS",
                )

    grounded: set[str] = set()
    for claim in _source_claims(draft):
        grounded.update(_number_tokens(claim))
    ungrounded = sorted(_number_tokens(body) - grounded)
    for token in ungrounded:
        add(
            "ungrounded_number",
            f"number token {token!r} does not appear in any sources[].claim",
        )

    anchors = sorted({match.group(0) for match in TIME_ANCHOR_RE.finditer(body)})
    for anchor in anchors:
        add("time_anchor", f"body contains time-anchored phrase: {anchor}")
    return defects


def _model_prompt(draft: dict, bundle: dict) -> str:
    return f"""You are the cross-model voice and grounded-content QA gate.
Review the sanitized bundle and draft. Enumerate every defect. Check voice
against a specific, plain, non-generic style; reject unsupported factual claims,
fabricated statistics or outcomes, medical advice, political takes, guarantees,
and any CTA not supported by the bundle. Do not rewrite the draft.

Return exactly one JSON object:
{{"defects":[{{"code":"short_machine_code","detail":"specific defect"}}]}}
Return an empty defects list only when there are no model-judged defects.

SANITIZED BUNDLE:
{json.dumps(bundle, sort_keys=True)}

DRAFT:
{json.dumps(draft, sort_keys=True)}
"""


def _model_defects(response_text: str) -> list[dict[str, str]]:
    response = _extract_json(response_text)
    if isinstance(response, list):
        raw_defects = response
    elif isinstance(response, dict):
        raw_defects = response.get("defects")
    else:
        raw_defects = None
    if not isinstance(raw_defects, list):
        raise EngineUnavailable("engine response must contain defects[]")
    defects: list[dict[str, str]] = []
    for index, defect in enumerate(raw_defects):
        if not isinstance(defect, dict):
            raise EngineUnavailable(f"engine defect {index} is not an object")
        code = defect.get("code")
        detail = defect.get("detail")
        if not isinstance(code, str) or not code.strip():
            raise EngineUnavailable(f"engine defect {index} has no code")
        if not isinstance(detail, str) or not detail.strip():
            raise EngineUnavailable(f"engine defect {index} has no detail")
        defects.append({"code": code.strip(), "detail": detail.strip()})
    return defects


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--engines-file", type=Path, required=True)
    parser.add_argument("--charter", type=Path, default=DEFAULT_CHARTER)
    parser.add_argument("--no-kernel", action="store_true")
    parser.add_argument("--engine-timeout", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args(argv)
    draft: Any = None
    bundle: Any = None
    try:
        draft = _read_json(args.draft, "draft")
        bundle = _read_json(args.bundle, "sanitized bundle")
        allowed_engines, max_rounds = _load_charter_policy(args.charter)
        if args.engine not in allowed_engines:
            raise GateBlocked(
                f"engine {args.engine!r} is not allowlisted; allowed: "
                + ", ".join(sorted(allowed_engines))
            )
        if not isinstance(draft, dict):
            raise GateBlocked("draft must be a JSON object")
        round_number = draft.get("round")
        if (
            isinstance(round_number, bool)
            or not isinstance(round_number, int)
            or not 0 <= round_number <= max_rounds
        ):
            raise GateBlocked(
                f"draft round must be an integer from 0 through {max_rounds}"
            )
        if draft.get("engine") == args.engine:
            raise GateBlocked(
                "cross-model QA required: qa engine must differ from draft engine"
            )

        defects = deterministic_defects(draft, bundle)
        sanitized = isinstance(bundle, dict) and bundle.get("sanitized") is True
        if sanitized:
            try:
                defects.extend(
                    _model_defects(
                        _call_engine(
                            _model_prompt(draft, bundle),
                            engine=args.engine,
                            engines_file=args.engines_file,
                            state_dir=args.state_dir,
                            no_kernel=args.no_kernel,
                            timeout=args.engine_timeout,
                        )
                    )
                )
            except EngineUnavailable as exc:
                defects.append(
                    {"code": "qa_engine_unavailable", "detail": str(exc)}
                )
        report = {"pass": not defects, "defects": defects, "engine": args.engine}
        _write_json(args.out, report)
        return 0
    except GateBlocked as exc:
        reasons = exc.reasons or [str(exc)]
        _write_json(args.out, {"status": "blocked", "reasons": reasons})
        _quarantine(args.state_dir, _item_id(bundle), reasons)
        LOGGER.error("qa_post blocked: %s", "; ".join(reasons))
        return 2
    except SourceUnavailable as exc:
        _write_json(args.out, {"status": "missing", "reason": str(exc)})
        LOGGER.error("qa_post source unavailable: %s", exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())
