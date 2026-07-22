"""Release-pinning + calibration for a department (P4, finishing B4).

A release is content-addressed: its id is a hash over the exact bytes of the
department's runtime artifacts (charter, graph, subgraphs, runtime code,
manifests). A `current` pointer flips atomically between releases so nothing
mutates under an in-flight run (spec §13.6). A model-free verifier recomputes
artifact hashes from the live tree and alarms on drift (§13.10). The calibration
log records the hand-authoring cost (human-minutes, hand-patch count,
judgment-vs-mechanical split) that feeds the B8 emitter go/no-go decision.

No wall-clock is read here (spec forbids nondeterministic time in some contexts);
callers pass source_ref (a git sha) and any timestamps.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


# Artifacts that define a release. Missing ones are simply skipped.
ARTIFACT_GLOBS = (
    "charter.yaml",
    "procedural-graph.md",
    "subgraphs.json",
    "runtime/*.py",
    "runtime/*.json",
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_artifacts(dept_dir: Path) -> list[tuple[str, str]]:
    """Return sorted (relative_path, sha256) for every release artifact."""
    dept_dir = Path(dept_dir)
    found: list[tuple[str, str]] = []
    for pattern in ARTIFACT_GLOBS:
        for path in sorted(dept_dir.glob(pattern)):
            if path.is_file():
                found.append((str(path.relative_to(dept_dir)), _sha256_file(path)))
    return sorted(set(found))


def _tree_hash(artifacts: list[tuple[str, str]]) -> str:
    h = hashlib.sha256()
    for rel, digest in artifacts:
        h.update(rel.encode())
        h.update(b"\0")
        h.update(digest.encode())
        h.update(b"\n")
    return h.hexdigest()[:16]


def pin_release(dept_dir, release_root, source_ref: str) -> str:
    """Pin the current artifact set as a content-addressed release; return its
    hash. Idempotent: the same bytes always produce the same release dir."""
    dept_dir = Path(dept_dir)
    release_root = Path(release_root)
    artifacts = _collect_artifacts(dept_dir)
    release_hash = _tree_hash(artifacts)
    release_dir = release_root / release_hash
    release_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "hash": release_hash,
        "source_ref": source_ref,
        "artifacts": [{"path": rel, "sha256": digest} for rel, digest in artifacts],
    }
    _atomic_write(release_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return release_hash


def load_manifest(release_dir) -> dict:
    return json.loads((Path(release_dir) / "manifest.json").read_text(encoding="utf-8"))


def verify_release(dept_dir, release_dir) -> dict:
    """Recompute artifact hashes from the live tree and compare to the pinned
    manifest. Model-free integrity check (§13.10)."""
    dept_dir = Path(dept_dir)
    manifest = load_manifest(release_dir)
    pinned = {a["path"]: a["sha256"] for a in manifest["artifacts"]}
    live = dict(_collect_artifacts(dept_dir))
    mismatches = []
    for path, digest in pinned.items():
        if live.get(path) != digest:
            mismatches.append(path)
    for path in live:
        if path not in pinned:
            mismatches.append(path)
    return {"ok": not mismatches, "mismatches": sorted(set(mismatches))}


def flip_current(release_root, release_hash: str) -> None:
    """Atomically point `current` at a release."""
    _atomic_write(Path(release_root) / "current", release_hash + "\n")


def read_current(release_root):
    path = Path(release_root) / "current"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def record_calibration(log_path, *, dept: str, human_minutes: int, hand_patches: int,
                       judgment_minutes: int, mechanical_minutes: int, source_ref: str) -> dict:
    """Append the hand-authoring cost of a department to the calibration log."""
    row = {
        "dept": dept,
        "human_minutes": human_minutes,
        "hand_patches": hand_patches,
        "judgment_minutes": judgment_minutes,
        "mechanical_minutes": mechanical_minutes,
        "source_ref": source_ref,
    }
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pin / verify a department release")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pin")
    p.add_argument("--dept", required=True)
    p.add_argument("--releases", required=True)
    p.add_argument("--source-ref", required=True)
    p.add_argument("--flip", action="store_true", help="also flip current to the new release")
    v = sub.add_parser("verify")
    v.add_argument("--dept", required=True)
    v.add_argument("--release-dir", required=True)
    args = parser.parse_args()
    if args.cmd == "pin":
        h = pin_release(args.dept, args.releases, args.source_ref)
        if args.flip:
            flip_current(args.releases, h)
        print(json.dumps({"hash": h, "current": read_current(args.releases)}))
    else:
        print(json.dumps(verify_release(args.dept, args.release_dir)))


if __name__ == "__main__":
    main()
