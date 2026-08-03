from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[3]
DRIVER = REPO / "departments/social/runtime/social_daily.sh"


@pytest.fixture
def shell_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    state = tmp_path / "state"
    state.mkdir()
    (state / "backcatalog_index.json").write_text(
        json.dumps(
            {
                "item_id": "fixture-1",
                "source_type": "podcast",
                "url": "https://example.invalid/fixture",
                "title": "Fixture episode",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    for name, value in (
        ("brand.json", {"brand": {"voice": "fixture"}}),
        ("offer.json", {"offer": {"name": "fixture"}}),
        ("suppression.jsonl", ""),
        ("observations.jsonl", ""),
        ("approvals.yaml", "{}\n"),
        ("privacy_blocklist.yaml", "[]\n"),
        ("surface_counts.json", "{}\n"),
        ("engines.yaml", "{}\n"),
    ):
        path = state / name
        path.write_text(
            json.dumps(value) + "\n" if isinstance(value, dict) else value,
            encoding="utf-8",
        )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python_wrapper = bin_dir / "python3"
    python_wrapper.write_text(
        f"""#!{os.sys.executable}
import json, os, subprocess, sys
from pathlib import Path

real = {str(Path(os.sys.executable))!r}
args = sys.argv[1:]
if not args or args[0] == '-' or args[0].endswith('/record.py'):
    raise SystemExit(subprocess.call([real, *args]))

script = Path(args[0]).name
def option(name):
    return args[args.index(name) + 1]
out = Path(option('--out'))
out.parent.mkdir(parents=True, exist_ok=True)

if script == 'inventory_backcatalog.py':
    source = Path(option('--items'))
    payload = json.loads(source.read_text())
    out.write_text(''.join(json.dumps(row, sort_keys=True) + '\\n' for row in payload['items']))
    raise SystemExit(0)
if script == 'select_candidate.py':
    row = json.loads(Path(option('--index')).read_text().splitlines()[0])
    value = {{'status': 'selected', 'item': row}}
elif script == 'draft_post.py':
    value = {{'status': 'drafted', 'round': 0, 'body': 'fixture post', 'surface': 'linkedin'}}
elif script == 'qa_post.py':
    value = {{'status': 'complete', 'pass': True, 'defects': []}}
elif script == 'dispatch.py':
    value = {{'status': os.environ.get('FIXTURE_DISPATCH_STATUS', 'dispatched'), 'delivered_count': 0}}
elif script == 'delivery_verify.py':
    value = {{'status': 'verified', 'delivered_count': 0}}
elif script == 'create_review_card.py':
    if os.environ.get('FIXTURE_CARD_FAIL') == '1':
        value = {{'status': 'failed', 'reason': 'fixture card failure'}}
        out.write_text(json.dumps(value, sort_keys=True) + '\\n')
        raise SystemExit(7)
    value = {{'status': 'card_created', 'identifier': 'ANK-FIXTURE'}}
else:
    value = {{'status': 'ok'}}
out.write_text(json.dumps(value, sort_keys=True) + '\\n')
""",
        encoding="utf-8",
    )
    python_wrapper.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "SOCIAL_STATE_DIR": str(state),
            "SOCIAL_QA_RETRY_BACKOFF_SECONDS": "0",
        }
    )
    return state, env


def run_driver(shell_fixture, **overrides):
    state, env = shell_fixture
    env.update(overrides)
    result = subprocess.run(
        ["bash", str(DRIVER)],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    run_dirs = sorted((state / "receipts").iterdir())
    return state, run_dirs[-1], result


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_cap_yield_writes_terminal_record(shell_fixture):
    state, run_dir, result = run_driver(
        shell_fixture, FIXTURE_DISPATCH_STATUS="yielded"
    )
    assert result.returncode == 0, result.stderr
    assert (run_dir / "N9-record.json").is_file()
    assert read_json(state / "runs.jsonl")["payload_summary"]["status"] == "yielded"


def test_yield_record_has_normal_record_schema(shell_fixture):
    state, run_dir, result = run_driver(
        shell_fixture, FIXTURE_DISPATCH_STATUS="yielded"
    )
    assert result.returncode == 0, result.stderr
    record = read_json(run_dir / "N9-record.json")
    assert set(record) == {"node", "epoch", "timestamp", "shadow", "payload_summary"}
    assert record["node"] == "SG-REPUBLISH"
    assert record["shadow"] is True


def test_review_card_failure_is_receipted_and_nonzero(shell_fixture):
    _, run_dir, result = run_driver(shell_fixture, FIXTURE_CARD_FAIL="1")
    assert result.returncode == 2
    assert read_json(run_dir / "N10-review-card.json")["status"] == "failed"
    assert "invalid receipt: N10-review-card" in result.stderr


def test_review_card_success_has_receipt_and_run_continues(shell_fixture):
    state, run_dir, result = run_driver(shell_fixture)
    assert result.returncode == 0, result.stderr
    assert read_json(run_dir / "N10-review-card.json")["status"] == "card_created"
    assert read_json(state / "runs.jsonl")["payload_summary"]["status"] == "verified"


def test_full_happy_path_receipts_are_unchanged(shell_fixture):
    _, run_dir, result = run_driver(shell_fixture)
    assert result.returncode == 0, result.stderr
    names = {path.name for path in run_dir.iterdir()}
    expected = {
        "S6-kill.json", "S7-breaker.json", "N1-inventory-source.json",
        "N1-inventory.json", "N1-index-installed.json", "N2-candidate.json",
        "S1-index.json", "S1-resolved.json", "S2-eligible.json",
        "N3-brand-offer.json", "N3-context.json", "S3-sanitized.json",
        "S8-model-token.json", "N4-draft-r1-raw.json", "N4-draft-r1.json",
        "N5-qa-r1.json", "S4-S5-dispatch-token.json",
        "S6-kill-pre-dispatch.json", "S7-breaker-pre-dispatch.json",
        "N6-dispatch.json", "N7-delivery-verification.json", "N9-record.json",
        "N10-review-card.json",
    }
    assert names == expected
