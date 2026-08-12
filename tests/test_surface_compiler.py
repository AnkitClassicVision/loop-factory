import json
from pathlib import Path

import pytest

from factory import graphs
from factory.surface_compiler import SurfaceError, check_surface, generate


@pytest.fixture
def fake_dept(tmp_path):
    dept = tmp_path / "example"
    dept.mkdir()
    data = {
        "subgraphs": [
            {
                "id": "SG-ALPHA",
                "concept_refs": ["C1", "C2"],
                "nodes": [
                    {"id": "A1", "impl": "runtime/alpha_one.py"},
                    {"id": "A2", "impl": "runtime/alpha_two.py"},
                ],
                "not_applicable": {
                    guard: "read-only fixture" for guard in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")
                },
            },
            {
                "id": "SG-BETA",
                "concept_refs": ["C3"],
                "nodes": [{"id": "B1", "impl": "runtime/beta.py"}],
                "not_applicable": {
                    guard: "read-only fixture" for guard in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")
                },
            },
        ],
        "untraced_allowed": {"runtime/helper.py": "fixture helper"},
    }
    (dept / "subgraphs.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return dept


def _surface_files(dept: Path):
    return sorted(path for path in dept.rglob("*.md") if path.is_file())


def test_generate_creates_full_surface(fake_dept):
    generate(fake_dept)
    expected = [
        "AGENTS.md", "CLAUDE.md", "ROUTER.md",
        "01_alpha/CONTEXT.md", "01_alpha/references/README.md",
        "02_beta/CONTEXT.md", "02_beta/references/README.md",
    ]
    for relpath in expected:
        assert (fake_dept / relpath).is_file()
    router = (fake_dept / "ROUTER.md").read_text(encoding="utf-8")
    assert "01_alpha" in router and "02_beta" in router
    assert "a task matching no workspace STOPS rather than guessing" in router
    assert len((fake_dept / "CLAUDE.md").read_text(encoding="utf-8").splitlines()) == 3
    for path in _surface_files(fake_dept):
        text = path.read_text(encoding="utf-8")
        assert "<!-- GENERATED:BEGIN " in text
        assert "<!-- GENERATED:END " in text


def test_generate_is_idempotent(fake_dept):
    generate(fake_dept)
    before = {p.relative_to(fake_dept): p.read_bytes() for p in _surface_files(fake_dept)}
    generate(fake_dept)
    after = {p.relative_to(fake_dept): p.read_bytes() for p in _surface_files(fake_dept)}
    assert after == before


def test_human_region_survives_regeneration(fake_dept):
    generate(fake_dept)
    context = fake_dept / "01_alpha/CONTEXT.md"
    owner_prose = "\nOwner prose, byte-for-byte.\n"
    context.write_text(context.read_text(encoding="utf-8") + owner_prose, encoding="utf-8")
    data = json.loads((fake_dept / "subgraphs.json").read_text(encoding="utf-8"))
    data["subgraphs"][0]["nodes"].append({"id": "A3", "impl": "runtime/alpha_three.py"})
    (fake_dept / "subgraphs.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    generate(fake_dept)
    result = context.read_text(encoding="utf-8")
    assert result.endswith(owner_prose)
    assert "runtime/alpha_three.py" in result


def test_check_surface_skips_unadopted(fake_dept):
    assert check_surface(fake_dept) == []


def test_check_surface_fails_on_drift(fake_dept):
    generate(fake_dept)
    data = json.loads((fake_dept / "subgraphs.json").read_text(encoding="utf-8"))
    data["subgraphs"][0]["nodes"].append({"id": "A3", "impl": "runtime/alpha_three.py"})
    (fake_dept / "subgraphs.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    failures = check_surface(fake_dept)
    assert any("01_alpha/CONTEXT.md" in failure for failure in failures)


def test_check_surface_fails_on_mangled_markers(fake_dept):
    generate(fake_dept)
    context = fake_dept / "01_alpha/CONTEXT.md"
    text = context.read_text(encoding="utf-8")
    context.write_text(text.replace("<!-- GENERATED:END section=context -->\n", "", 1), encoding="utf-8")
    failures = check_surface(fake_dept)
    assert any("01_alpha/CONTEXT.md" in failure and "markers" in failure for failure in failures)


def test_qa_includes_surface_key(fake_dept):
    generate(fake_dept)
    data = json.loads((fake_dept / "subgraphs.json").read_text(encoding="utf-8"))
    data["subgraphs"][0]["nodes"].append({"id": "A3", "impl": "runtime/alpha_three.py"})
    (fake_dept / "subgraphs.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    result = graphs.qa(fake_dept)
    assert "surface" in result
    assert result["surface"]
    assert result["ok"] is False


def test_generated_regions_never_contain_todo(fake_dept):
    generate(fake_dept)
    joined = "\n".join(path.read_text(encoding="utf-8") for path in _surface_files(fake_dept))
    assert "TBD" not in joined
    assert "TODO" not in joined
    assert "_No owner notes yet._" in joined


def test_generate_renders_done_floor_and_conditional_router_column(fake_dept):
    data = json.loads((fake_dept / "subgraphs.json").read_text(encoding="utf-8"))
    data["subgraphs"][0]["stage"] = "alpha_stage"
    data["subgraphs"][0]["done"] = {
        "conditions": ["first condition", "second condition"],
        "receipt": "alpha receipt",
    }
    (fake_dept / "subgraphs.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    generate(fake_dept)

    alpha = (fake_dept / "01_alpha/CONTEXT.md").read_text(encoding="utf-8")
    beta = (fake_dept / "02_beta/CONTEXT.md").read_text(encoding="utf-8")
    router = (fake_dept / "ROUTER.md").read_text(encoding="utf-8")
    assert "## DONE means\n\n- first condition\n- second condition\nReceipt: alpha receipt" in alpha
    assert (
        "## Floor\n\nThis stage holds the `alpha_stage` floor. Current values live in "
        "`../floors.yaml` (machine-written; numbers are never copied here — two copies "
        "guarantees one stale)."
    ) in alpha
    assert "## DONE means" not in beta
    assert "## Floor" not in beta
    assert "| Workspace folder | Subgraph id | Node count | Concept refs | DONE means |" in router
    assert "| `01_alpha/` | `SG-ALPHA` | 2 | C1, C2 | first condition (+1 more) |" in router
    assert "| `02_beta/` | `SG-BETA` | 1 | C3 | - |" in router


def test_generate_rejects_empty_done_conditions_with_subgraph_name(fake_dept):
    data = json.loads((fake_dept / "subgraphs.json").read_text(encoding="utf-8"))
    data["subgraphs"][0]["done"] = {"conditions": [], "receipt": "alpha receipt"}
    (fake_dept / "subgraphs.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    with pytest.raises(SurfaceError, match="SG-ALPHA"):
        generate(fake_dept)


def test_generate_without_v2_fields_preserves_v1_surface_shape(fake_dept):
    generate(fake_dept)

    joined = "\n".join(path.read_text(encoding="utf-8") for path in _surface_files(fake_dept))
    router = (fake_dept / "ROUTER.md").read_text(encoding="utf-8")
    assert "## DONE means" not in joined
    assert "## Floor" not in joined
    assert "DONE means" not in router
    assert "| Workspace folder | Subgraph id | Node count | Concept refs |" in router
