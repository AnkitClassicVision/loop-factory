from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factory.runrecord import validate_record


REPO = Path(__file__).resolve().parents[3]
DRIVER = REPO / "departments/social/runtime/social_daily.sh"
RUNTIME = DRIVER.parent


def _seed_state(state: Path) -> None:
    state.mkdir(parents=True)
    (state / "backcatalog_index.json").write_text(
        json.dumps({
            "item_id": "fixture-1", "source_type": "podcast",
            "url": "https://example.invalid/fixture", "title": "Fixture episode",
        }) + "\n",
        encoding="utf-8",
    )
    values = {
        "brand.json": {"brand": {"voice": "fixture"}},
        "offer.json": {"offer": {"name": "fixture"}},
        "suppression.jsonl": "",
        "observations.jsonl": "",
        "approvals.yaml": "{}\n",
        "privacy_blocklist.yaml": "[]\n",
        "surface_counts.json": "{}\n",
        "engines.yaml": "{}\n",
    }
    for name, value in values.items():
        (state / name).write_text(
            json.dumps(value) + "\n" if isinstance(value, dict) else value,
            encoding="utf-8",
        )


def _stub_python(path: Path) -> None:
    path.write_text(
        f"""#!{sys.executable}
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path

real = {sys.executable!r}
args = sys.argv[1:]
name = Path(args[0]).name if args and args[0] != '-' else ''
if not args or args[0] == '-' or name in ('record.py', 'harvest_review_asks.py'):
    os.execv(real, [real, *args])
def option(flag):
    return args[args.index(flag) + 1]
out = Path(option('--out'))
out.parent.mkdir(parents=True, exist_ok=True)
if name == 'inventory_backcatalog.py':
    payload = json.loads(Path(option('--items')).read_text())
    out.write_text(''.join(json.dumps(row) + '\\n' for row in payload['items']))
    raise SystemExit(0)
if name == 'select_candidate.py':
    value = {{'status': 'selected', 'item': json.loads(Path(option('--index')).read_text().splitlines()[0])}}
elif name == 'draft_post.py':
    value = {{'status': 'drafted', 'round': 0, 'body': 'fixture', 'surface': 'linkedin'}}
elif name == 'qa_post.py':
    value = {{'status': 'complete', 'pass': True, 'defects': []}}
elif name == 'dispatch.py':
    value = {{'status': 'dispatched', 'delivered_count': 0}}
elif name == 'delivery_verify.py':
    value = {{'status': 'verified', 'delivered_count': 0}}
elif name == 'create_review_card.py':
    ledger = Path(option('--ledger'))
    ledger.write_text(json.dumps({{
        'ts': datetime.now(timezone.utc).isoformat(), 'run_id': option('--run-id'),
        'department': 'social', 'kind': 'human_review',
        'card_identifier': 'ANK-FIXTURE', 'status': 'open',
    }}) + '\\n')
    value = {{'status': 'card_created', 'identifier': 'ANK-FIXTURE'}}
else:
    value = {{'status': 'ok'}}
out.write_text(json.dumps(value) + '\\n')
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_fixture(tmp_path: Path, *, break_v2: bool = False):
    state = tmp_path / "state"
    _seed_state(state)
    if break_v2:
        (state / "runs-v2.jsonl").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_python(bin_dir / "python3")
    comments = tmp_path / "comments.json"
    comments.write_text('{"ANK-FIXTURE": []}\n', encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "SOCIAL_STATE_DIR": str(state),
        "SOCIAL_QA_RETRY_BACKOFF_SECONDS": "0",
        "SOCIAL_LINEAR_COMMENTS_FIXTURE": str(comments),
    })
    completed = subprocess.run(
        ["bash", str(DRIVER)], cwd=REPO, env=env, text=True,
        capture_output=True, timeout=20,
    )
    return state, completed


@pytest.fixture(scope="module")
def completed_cycle(tmp_path_factory):
    state, completed = _run_fixture(tmp_path_factory.mktemp("v2-cycle"))
    assert completed.returncode == 0, completed.stderr
    return state


def _v2_rows(state: Path) -> list[dict]:
    return [json.loads(line) for line in (state / "runs-v2.jsonl").read_text(encoding="utf-8").splitlines()]


def test_stage_rows_validate_against_factory_contract(completed_cycle):
    rows = _v2_rows(completed_cycle)
    assert len(rows) >= 20
    assert all(validate_record(row) == row for row in rows)


def test_daily_cycle_uses_one_shared_run_id(completed_cycle):
    rows = _v2_rows(completed_cycle)
    assert len({row["run_id"] for row in rows}) == 1


def test_forced_v2_append_failure_fails_stage(tmp_path):
    _, completed = _run_fixture(tmp_path, break_v2=True)
    assert completed.returncode == 2
    assert "v2 append failed: S6-kill" in completed.stderr


def test_harvest_stage_is_receipt_gated_in_real_shell_chain(completed_cycle):
    run_dir = sorted((completed_cycle / "receipts").iterdir())[-1]
    receipt = json.loads((run_dir / "N11-review-harvest.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "complete"
    assert receipt["checked"] == 1
    assert any(row["node"] == "N11" for row in _v2_rows(completed_cycle))


def test_ask_older_than_sla_produces_breach_receipt_and_outbox(tmp_path):
    ledger = tmp_path / "card_ledger.jsonl"
    old = datetime.now(timezone.utc) - timedelta(hours=49)
    ledger.write_text(json.dumps({
        "ts": old.isoformat(), "card_identifier": "ANK-OLD", "status": "open",
    }) + "\n", encoding="utf-8")
    fixture = tmp_path / "comments.json"
    fixture.write_text('{"ANK-OLD": []}\n', encoding="utf-8")
    out = tmp_path / "harvest.json"
    outbox = tmp_path / "decisions_outbox.jsonl"
    completed = subprocess.run([
        sys.executable, str(RUNTIME / "harvest_review_asks.py"),
        "--ledger", str(ledger), "--outbox", str(outbox),
        "--reader", str(RUNTIME / "linear_read_comments.py"),
        "--fixture", str(fixture), "--return-sla-hours", "48", "--out", str(out),
    ], cwd=REPO, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["sla_breaches"] == 1
    escalation = json.loads(outbox.read_text(encoding="utf-8"))
    assert escalation["kind"] == "escalation"
    assert escalation["department"] == "social"


def test_graph_declaration_carries_return_contract():
    graph = json.loads((REPO / "departments/social/subgraphs.json").read_text(encoding="utf-8"))
    republish = next(item for item in graph["subgraphs"] if item["id"] == "SG-REPUBLISH")
    review = next(node for node in republish["nodes"] if node["id"] == "N10")
    assert review["emits_ask"] is True
    assert review["return_path"] == "runtime/linear_read_comments.py"
    assert review["return_sla_hours"] == 48
    assert review["_return_sla_note"] == "# DEFAULT pending owner review"


def test_shadow_dispatch_stage_records_zero_external_actions(completed_cycle):
    dispatch_rows = [row for row in _v2_rows(completed_cycle) if row["node"] == "N6"]
    assert len(dispatch_rows) == 1
    assert dispatch_rows[0]["external_actions_taken"] == 0
