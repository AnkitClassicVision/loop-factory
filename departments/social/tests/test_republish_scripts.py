import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
import pytest


REPO = Path(__file__).resolve().parents[3]
RUNTIME = REPO / "departments/social/runtime"


def invoke(node, *args):
    return subprocess.run(
        [sys.executable, str(RUNTIME / node), *map(str, args)],
        text=True,
        capture_output=True,
        check=False,
    )


def item(item_id, body, published="2025-01-01T00:00:00+00:00", last=None, score=0.0):
    return {
        "item_id": item_id, "source_type": "podcast", "title": f"Fake {item_id}",
        "url": f"https://example.invalid/{item_id}", "published_at": published,
        "body_path": str(body), "last_resurfaced_at": last,
        "prior_engagement": {"score": score},
    }


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_rss_fixture_parses_to_valid_index(tmp_path):
    rss, out = tmp_path / "feed.xml", tmp_path / "index.jsonl"
    rss.write_text(
        "<rss><channel><item><guid>ep-1</guid><title>Fake episode</title>"
        "<link>https://example.invalid/ep-1</link><pubDate>Wed, 01 Jan 2025 00:00:00 GMT</pubDate>"
        "<body_path>/tmp/fake-body.txt</body_path></item></channel></rss>",
        encoding="utf-8",
    )
    result = invoke("inventory_backcatalog.py", "--state-dir", tmp_path / "state",
                    "--out", out, "--rss", rss)
    assert result.returncode == 0
    row = json.loads(out.read_text())
    assert set(row) == {
        "item_id", "source_type", "title", "url", "published_at", "body_path",
        "last_resurfaced_at", "prior_engagement",
    }
    assert row["item_id"] == "ep-1"


def test_empty_source_exits_three(tmp_path):
    source, out = tmp_path / "items.json", tmp_path / "out.json"
    source.write_text("[]", encoding="utf-8")
    result = invoke("inventory_backcatalog.py", "--state-dir", tmp_path / "state",
                    "--out", out, "--items", source)
    assert result.returncode == 3
    assert json.loads(out.read_text())["status"] == "missing"


def test_selection_is_deterministic(tmp_path):
    index = tmp_path / "index.jsonl"
    write_jsonl(index, [item("b", "unused", score=1), item("a", "unused", score=1)])
    outputs = []
    for number in (1, 2):
        out = tmp_path / f"candidate-{number}.json"
        result = invoke("select_candidate.py", "--state-dir", tmp_path / "state",
                        "--out", out, "--index", index, "--as-of", "2026-01-01T00:00:00Z")
        assert result.returncode == 0
        outputs.append(json.loads(out.read_text()))
    assert outputs[0] == outputs[1]
    assert outputs[0]["item"]["item_id"] == "a"


def test_cooldown_and_suppression_are_excluded(tmp_path):
    index, suppression, out = tmp_path / "index.jsonl", tmp_path / "suppression.jsonl", tmp_path / "out.json"
    write_jsonl(index, [
        item("cool", "unused", last="2025-12-20T00:00:00Z", score=99),
        item("suppressed", "unused", published="2020-01-01T00:00:00Z", score=99),
        item("eligible", "unused", published="2025-01-01T00:00:00Z"),
    ])
    write_jsonl(suppression, [{"item_id": "suppressed", "status": "retracted"}])
    result = invoke("select_candidate.py", "--state-dir", tmp_path / "state", "--out", out,
                    "--index", index, "--suppression", suppression,
                    "--as-of", "2026-01-01T00:00:00Z")
    assert result.returncode == 0
    assert json.loads(out.read_text())["item"]["item_id"] == "eligible"


def test_context_incomplete_for_missing_body_and_todo_brand(tmp_path):
    candidate, brand, out = tmp_path / "candidate.json", tmp_path / "brand.yaml", tmp_path / "out.json"
    candidate.write_text(json.dumps({"item": item("x", tmp_path / "missing.txt"),
                                     "rank_score": 1.0, "rationale": "test"}), encoding="utf-8")
    brand.write_text("brand:\n  name: TODO_NAME\n  voice_notes: [clear]\n  audience: owners\n"
                     "offer:\n  name: Call\n  cta_url: https://example.invalid/book\n  description: Fake offer\n",
                     encoding="utf-8")
    result = invoke("assemble_context.py", "--state-dir", tmp_path / "state",
                    "--out", out, "--candidate", candidate, "--brand", brand)
    manifest = json.loads(out.read_text())
    assert result.returncode == 2
    assert manifest["complete"] is False
    assert "body_text" in manifest["missing"]
    assert "brand.name" in manifest["missing"]


def test_context_incomplete_for_todo_body_placeholder(tmp_path):
    body = tmp_path / "body.txt"
    candidate = tmp_path / "candidate.json"
    brand = tmp_path / "brand.yaml"
    out = tmp_path / "out.json"
    body.write_text("TODO_TRANSCRIBE_EPISODE", encoding="utf-8")
    candidate.write_text(
        json.dumps(
            {
                "item": item("placeholder-body", body),
                "rank_score": 1.0,
                "rationale": "test",
            }
        ),
        encoding="utf-8",
    )
    brand.write_text(
        yaml.safe_dump(
            {
                "podcast": {
                    "brand": {
                        "name": "Example Brand",
                        "voice_notes": ["direct"],
                        "audience": "fake owners",
                    },
                    "offer": {
                        "name": "Example Call",
                        "cta_url": "https://example.invalid/book",
                        "description": "An obviously fake offer",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    result = invoke(
        "assemble_context.py",
        "--state-dir",
        tmp_path / "state",
        "--out",
        out,
        "--candidate",
        candidate,
        "--brand",
        brand,
    )
    manifest = json.loads(out.read_text(encoding="utf-8"))

    assert result.returncode == 2
    assert manifest["complete"] is False
    assert "body_text" in manifest["missing"]


@pytest.mark.parametrize(
    ("field", "missing_entry"),
    [("title", "item.title"), ("url", "item.url")],
)
def test_context_incomplete_for_todo_item_placeholder(tmp_path, field, missing_entry):
    body = tmp_path / "body.txt"
    candidate = tmp_path / "candidate.json"
    brand = tmp_path / "brand.yaml"
    out = tmp_path / "out.json"
    body.write_text("Complete fake source body.", encoding="utf-8")
    candidate_item = item("placeholder-item", body)
    candidate_item[field] = f"TODO_{field.upper()}"
    candidate.write_text(
        json.dumps(
            {
                "item": candidate_item,
                "rank_score": 1.0,
                "rationale": "test",
            }
        ),
        encoding="utf-8",
    )
    brand.write_text(
        yaml.safe_dump(
            {
                "podcast": {
                    "brand": {
                        "name": "Example Brand",
                        "voice_notes": ["direct"],
                        "audience": "fake owners",
                    },
                    "offer": {
                        "name": "Example Call",
                        "cta_url": "https://example.invalid/book",
                        "description": "An obviously fake offer",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    result = invoke(
        "assemble_context.py",
        "--state-dir",
        tmp_path / "state",
        "--out",
        out,
        "--candidate",
        candidate,
        "--brand",
        brand,
    )
    manifest = json.loads(out.read_text(encoding="utf-8"))

    assert result.returncode == 2
    assert manifest["complete"] is False
    assert missing_entry in manifest["missing"]


def test_full_context_happy_path_embeds_body(tmp_path):
    body, candidate, brand, out = tmp_path / "body.txt", tmp_path / "candidate.json", tmp_path / "brand.yaml", tmp_path / "out.json"
    body.write_text("Full obviously-fake source body.\nSecond paragraph.", encoding="utf-8")
    candidate.write_text(json.dumps({"item": item("x", body),
                                     "rank_score": 1.0, "rationale": "test"}), encoding="utf-8")
    brand.write_text(yaml.safe_dump({
        "podcast": {
            "brand": {"name": "Example Brand", "voice_notes": ["direct"], "audience": "fake owners"},
            "offer": {"name": "Example Call", "cta_url": "https://example.invalid/book",
                      "description": "An obviously fake offer"},
        }
    }), encoding="utf-8")
    result = invoke("assemble_context.py", "--state-dir", tmp_path / "state",
                    "--out", out, "--candidate", candidate, "--brand", brand, "--version", "v1-test")
    manifest = json.loads(out.read_text())
    assert result.returncode == 0
    assert manifest["complete"] is True
    assert manifest["missing"] == []
    assert manifest["body_text"] == body.read_text(encoding="utf-8")
    assert manifest["version"] == "v1-test"
