"""Generate grounded change proposals without applying any change."""
from __future__ import annotations

import argparse
import json
import logging
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable

import yaml

from factory.charter_loader import load_charter


LOGGER = logging.getLogger(__name__)
DEFAULT_CHARTER = Path(__file__).resolve().parents[1] / "charter.yaml"
VALID_CLASSES = frozenset({"process_change", "prompt_update", "other"})
VALID_KINDS = frozenset({"approve", "skip", "fix"})
Runner = Callable[[list[str], str], str]


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _engine_allowlist(charter_path: str | Path) -> frozenset[str]:
    charter = load_charter(charter_path, expect_department="social")
    configured = (charter.get("budget") or {}).get("engine_allowlist")
    if (
        not isinstance(configured, list)
        or not configured
        or not all(isinstance(item, str) and item.strip() for item in configured)
    ):
        raise ValueError("charter budget.engine_allowlist is missing or empty")
    return frozenset(item.strip() for item in configured)


def _engine_command(
    engines_file: str | Path, engine: str, allowed_engines: frozenset[str]
) -> list[str]:
    if engine not in allowed_engines:
        raise ValueError(f"engine {engine!r} is not subscription/OAuth allowlisted")
    config = yaml.safe_load(Path(engines_file).read_text(encoding="utf-8"))
    engines = config.get("engines", config) if isinstance(config, dict) else {}
    entry = engines.get(engine) if isinstance(engines, dict) else None
    if isinstance(entry, dict):
        entry = entry.get("cmd", entry.get("command"))
    if isinstance(entry, str):
        command = shlex.split(entry)
    elif isinstance(entry, list) and all(isinstance(part, str) for part in entry):
        command = list(entry)
    else:
        raise ValueError(f"engine {engine!r} has no valid command in {engines_file}")
    if not command:
        raise ValueError(f"engine {engine!r} command is empty")
    return command


def _subprocess_runner(command: list[str], prompt: str) -> str:
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"engine exited {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout


def _prompt(evidence_pack: dict[str, Any]) -> str:
    return (
        "Return JSON only: a list of proposal_card objects. Each object requires "
        "question, kind (approve|skip|fix), class "
        "(process_change|prompt_update|other), and evidence as SG-SENSE row IDs. "
        "Never propose directly editing or applying a change. Evidence pack:\n"
        + json.dumps(evidence_pack, sort_keys=True)
    )


def validate_proposals(raw: Any, evidence_pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Drop ungrounded or self-modifying output from the untrusted model."""
    proposals = raw.get("proposals") if isinstance(raw, dict) else raw
    if not isinstance(proposals, list):
        raise ValueError("engine output must be a proposal list")
    valid_evidence = {
        row["row_id"]
        for row in evidence_pack.get("rows", [])
        if isinstance(row, dict) and isinstance(row.get("row_id"), str)
    }
    accepted: list[dict[str, Any]] = []
    for proposal in proposals:
        if not isinstance(proposal, dict):
            LOGGER.warning("invalid_proposal: proposal is not an object")
            continue
        evidence = proposal.get("evidence")
        if (
            not isinstance(evidence, list)
            or not evidence
            or not all(isinstance(item, str) and item in valid_evidence for item in evidence)
        ):
            LOGGER.warning("ungrounded_proposal: evidence row ids are absent from pack")
            continue

        proposal_class = proposal.get("class")
        if proposal_class is None and proposal.get("change_type") == "prompt_update":
            proposal_class = "prompt_update"
        if proposal_class not in VALID_CLASSES:
            LOGGER.warning("invalid_proposal: missing or invalid class")
            continue
        question = proposal.get("question")
        kind = proposal.get("kind")
        if not isinstance(question, str) or not question.strip() or kind not in VALID_KINDS:
            LOGGER.warning("invalid_proposal: question/kind contract failed")
            continue
        forbidden = proposal.get("apply", proposal.get("self_modify", False))
        if forbidden:
            LOGGER.warning("self_modify_proposal: proposals may never apply changes")
            continue
        accepted.append(
            {
                "question": question.strip(),
                "kind": kind,
                "class": proposal_class,
                "evidence": list(evidence),
            }
        )
    return accepted


def propose(
    evidence_pack: dict[str, Any],
    *,
    engine: str,
    command: list[str],
    allowed_engines: frozenset[str],
    runner: Runner = _subprocess_runner,
) -> list[dict[str, Any]]:
    if engine not in allowed_engines:
        raise ValueError(f"engine {engine!r} is not subscription/OAuth allowlisted")
    response = runner(command, _prompt(evidence_pack))
    return validate_proposals(json.loads(response), evidence_pack)


def main() -> int:
    parser = argparse.ArgumentParser(description="Propose grounded SG-LEARN insights")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--evidence-pack", required=True)
    parser.add_argument("--charter", default=str(DEFAULT_CHARTER))
    parser.add_argument("--engines-file", required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--no-kernel", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if not args.no_kernel:
        LOGGER.error("model call blocked: kernel model-call seam is not wired; use test seam only")
        _write_json(args.out, {"status": "blocked", "reason": "kernel model-call seam not wired"})
        return 2
    try:
        evidence_pack = _load_json(args.evidence_pack)
        allowed_engines = _engine_allowlist(args.charter)
        command = _engine_command(args.engines_file, args.engine, allowed_engines)
        cards = propose(
            evidence_pack,
            engine=args.engine,
            command=command,
            allowed_engines=allowed_engines,
        )
    except FileNotFoundError as exc:
        LOGGER.error("%s", exc)
        _write_json(args.out, {"status": "missing", "reason": str(exc)})
        return 3
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        LOGGER.error("%s", exc)
        _write_json(args.out, {"status": "missing", "reason": str(exc)})
        return 3
    _write_json(args.out, cards)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
