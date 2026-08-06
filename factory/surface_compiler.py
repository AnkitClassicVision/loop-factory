"""Compile and check a department's human-inspectable workspace surface."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


class SurfaceError(ValueError):
    """The source topology or an existing generated surface is unusable."""


_BEGIN_RE = re.compile(
    r"^<!-- GENERATED:BEGIN section=([^\s]+) source=subgraphs\.json -->$", re.MULTILINE
)
_NUMBERED_DIR_RE = re.compile(r"^\d{2}_.+")


def _markers(section: str, body: str) -> str:
    return (
        f"<!-- GENERATED:BEGIN section={section} source=subgraphs.json -->\n"
        f"{body.rstrip()}\n"
        f"<!-- GENERATED:END section={section} -->\n"
    )


def _load(dept_dir: Path) -> dict:
    path = dept_dir / "subgraphs.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SurfaceError(f"subgraphs.json: unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("subgraphs"), list):
        raise SurfaceError("subgraphs.json: 'subgraphs' must be a list")
    for index, subgraph in enumerate(data["subgraphs"], 1):
        if not isinstance(subgraph, dict) or not isinstance(subgraph.get("id"), str):
            raise SurfaceError(f"subgraphs.json: subgraph {index} has no string id")
        if not isinstance(subgraph.get("nodes", []), list):
            raise SurfaceError(f"subgraphs.json: {subgraph['id']} nodes must be a list")
    return data


def _slug(subgraph_id: str) -> str:
    stem = subgraph_id[3:] if subgraph_id.upper().startswith("SG-") else subgraph_id
    return stem.lower().replace("-", "_")


def _workspace(index: int, subgraph: dict) -> str:
    return f"{index:02d}_{_slug(subgraph['id'])}"


def _concepts(subgraph: dict) -> str:
    refs = subgraph.get("concept_refs", [])
    if isinstance(refs, list):
        return ", ".join(str(ref) for ref in refs) or "none declared"
    return str(refs)


def _node_impls(subgraph: dict) -> list[str]:
    return [str(node["impl"]) for node in subgraph.get("nodes", []) if isinstance(node, dict) and node.get("impl")]


def _renderings(data: dict) -> dict[Path, tuple[str, str]]:
    subgraphs = data["subgraphs"]
    routes = [
        f"| `{_workspace(i, sg)}/` | `{sg['id']}` | {len(sg.get('nodes', []))} | {_concepts(sg)} |"
        for i, sg in enumerate(subgraphs, 1)
    ]
    route_lines = [
        "# Workspace Router", "", "| Workspace folder | Subgraph id | Node count | Concept refs |",
        "|---|---|---:|---|", *routes, "",
        "a task matching no workspace STOPS rather than guessing.",
    ]
    summary = [
        "# Department Agent Surface", "", "## Routing summary", "",
        *[f"- `{_workspace(i, sg)}/` routes to `{sg['id']}`." for i, sg in enumerate(subgraphs, 1)],
        "", "## Invariants", "",
        "- Route only through the workspace table in `ROUTER.md`.",
        "- Treat `subgraphs.json` as the machine topology source.",
        "- Stop when no workspace matches; never guess a route.",
        "- Keep owner prose outside generated marker pairs.",
    ]
    rendered: dict[Path, tuple[str, str]] = {
        Path("AGENTS.md"): ("agents", _markers("agents", "\n".join(summary)) + "\n_No owner notes yet._\n"),
        Path("CLAUDE.md"): ("claude", _markers("claude", "Read and follow `AGENTS.md`.")),
        Path("ROUTER.md"): ("router", _markers("router", "\n".join(route_lines))),
    }
    for index, subgraph in enumerate(subgraphs, 1):
        workspace = _workspace(index, subgraph)
        impls = _node_impls(subgraph)
        chain = [f"{number}. `{impl}`" for number, impl in enumerate(impls, 1)] or ["_No implementation nodes declared._"]
        state_paths = sorted({impl for impl in impls if impl.startswith("state/")})
        l4 = [f"- `{path}`" for path in state_paths] or ["- `state/` paths used by the node chain"]
        outputs = [f"- `{impl}`" for impl in impls] or ["- No implementation outputs declared"]
        context_lines = [
            f"# {subgraph['id']} Context", "", "## Purpose", "",
            f"Implement `{subgraph['id']}` for concept refs: {_concepts(subgraph)}.", "",
            "## Node chain", "", *chain, "", "## Inputs", "",
            "### L3", "", "- `charter.yaml`", "- `references/`", "", "### L4", "", *l4, "",
            "## Outputs", "", *outputs, "", "## Verify", "",
            f"Verify against the `{subgraph['id']}` row in `../procedural-graph.md`.",
        ]
        context_path = Path(workspace) / "CONTEXT.md"
        rendered[context_path] = (
            "context", _markers("context", "\n".join(context_lines)) + "\n_No owner notes yet._\n"
        )
        reference_path = Path(workspace) / "references" / "README.md"
        reference_body = (
            "# Reference freshness\n\n"
            "Every reference file carries Last-updated and a half-life; expired references are stale context."
        )
        rendered[reference_path] = ("references", _markers("references", reference_body))
    return rendered


def _region(text: str, section: str) -> str:
    begin = f"<!-- GENERATED:BEGIN section={section} source=subgraphs.json -->"
    end = f"<!-- GENERATED:END section={section} -->"
    if (
        text.count("<!-- GENERATED:BEGIN") != 1
        or text.count("<!-- GENERATED:END") != 1
        or text.count(begin) != 1
        or text.count(end) != 1
    ):
        raise SurfaceError("markers missing or mangled")
    start = text.index(begin)
    finish = text.index(end, start) + len(end)
    if finish <= start or _BEGIN_RE.findall(text)[0] != section:
        raise SurfaceError("markers missing or mangled")
    return text[start:finish]


def _merge(existing: str, expected: str, section: str) -> str:
    try:
        old_region = _region(existing, section)
    except SurfaceError:
        raise SurfaceError("existing file has missing or mangled markers")
    new_region = _region(expected, section)
    return existing.replace(old_region, new_region, 1)


def generate(dept_dir: Path) -> list[Path]:
    """Write the compiled surface, preserving all bytes outside marker pairs."""
    dept_dir = Path(dept_dir)
    renderings = _renderings(_load(dept_dir))
    written: list[Path] = []
    for relative, (section, expected) in renderings.items():
        path = dept_dir / relative
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise SurfaceError(f"{relative}: unreadable: {exc}") from exc
            content = _merge(existing, expected, section)
        else:
            content = expected
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def check_surface(dept_dir: Path) -> list[str]:
    """Return generated-region and topology drift failures for an adopted surface."""
    dept_dir = Path(dept_dir)
    if not (dept_dir / "ROUTER.md").exists():
        return []
    try:
        renderings = _renderings(_load(dept_dir))
    except SurfaceError as exc:
        return [str(exc)]
    failures: list[str] = []
    for relative, (section, expected) in renderings.items():
        path = dept_dir / relative
        if not path.is_file():
            failures.append(f"{relative}: missing file")
            continue
        try:
            actual_region = _region(path.read_text(encoding="utf-8"), section)
        except (OSError, UnicodeError) as exc:
            failures.append(f"{relative}: unreadable: {exc}")
            continue
        except SurfaceError as exc:
            failures.append(f"{relative}: {exc}")
            continue
        if actual_region != _region(expected, section):
            failures.append(f"{relative}: generated region drifted from subgraphs.json")
    expected_dirs = {path.parts[0] for path in renderings if len(path.parts) > 1}
    for path in sorted(dept_dir.iterdir()):
        if path.is_dir() and _NUMBERED_DIR_RE.fullmatch(path.name) and path.name not in expected_dirs:
            failures.append(f"{path.name}: numbered workspace is not in topology")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile or check a department surface")
    parser.add_argument("action", choices=("generate", "check"))
    parser.add_argument("--dept-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.action == "check":
        failures = check_surface(args.dept_dir)
        for failure in failures:
            print(failure)
        return 1 if failures else 0
    try:
        generate(args.dept_dir)
    except (SurfaceError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
