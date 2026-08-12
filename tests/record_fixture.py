"""Test-only Factory spool and promotion helpers."""
from __future__ import annotations

import json
import os
from pathlib import Path

from factory import runrecord


def promote_factory_records(state_dir: Path) -> int:
    """Promote the signed fixture spool into this test's canonical state."""
    spool = Path(os.environ[runrecord.RECORD_SPOOL_ENV])
    marker = json.loads((spool / runrecord.SPOOL_MARKER).read_text(encoding="utf-8"))
    runrecord.verify_factory_spool(
        spool,
        run_id=marker["run_id"],
        department=marker["department"],
        trigger=marker["trigger"],
        state_dir=Path(marker["state_dir"]),
    )
    stream = spool / "runs-v2.jsonl"
    if not stream.exists():
        return 0
    rows = [
        runrecord.validate_record(json.loads(line))
        for line in stream.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if rows:
        runrecord._append_canonical_records(state_dir, rows)
    stream.unlink()
    return len(rows)
