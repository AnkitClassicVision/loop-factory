from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def factory_signing_key(monkeypatch):
    monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", "test-factory-signing-key")
