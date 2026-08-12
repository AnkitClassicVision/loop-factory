"""Shared explicit Factory custody fixture for record-emitting tests."""
from __future__ import annotations

import pytest

from factory import runrecord


@pytest.fixture
def factory_record_spool(tmp_path, monkeypatch):
    """Give record tests a signed spool; refusal tests remove or replace it."""
    monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", "test-factory-signing-key")
    spool = tmp_path / "factory-spool"
    runrecord.write_spool_marker(
        spool,
        run_id="fixture-run",
        department="example",
        release=None,
        trigger="daily",
        state_dir=tmp_path,
    )
    monkeypatch.setenv(runrecord.RECORD_SPOOL_ENV, str(spool))
    return spool
