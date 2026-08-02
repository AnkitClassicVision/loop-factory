"""Generic kernel bridge: the ONE way a department obtains its LockService.

A department never constructs gateways, signers, or ledgers itself — it calls
`load_kernel(state_dir)` and gets a LockService whose ledgers live under its
own state directory. The signing key comes from the environment of the TRUSTED
context running the kernel (OE_KERNEL_SIGNING_KEY); department processes are
launched WITHOUT that variable (factory/launch.py), so a department that tries
to stand up its own kernel fails closed on the missing key.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_KERNEL_DIR = Path(__file__).resolve().parent


def _ensure_kernel_package() -> None:
    """Register the real kernel package for file-path-loaded department lanes.

    Social loads this bridge under a standalone module name with no repository
    root on ``sys.path``. Registering the package here keeps package-absolute
    gateway imports working and preserves one ``kernel.jsonl_store`` identity.
    """
    existing = sys.modules.get("kernel")
    if existing is not None:
        package_paths = {
            Path(path).resolve() for path in getattr(existing, "__path__", ())
        }
        if _KERNEL_DIR not in package_paths:
            raise ImportError("loaded kernel package does not match bridge directory")
        return
    spec = importlib.util.spec_from_file_location(
        "kernel",
        _KERNEL_DIR / "__init__.py",
        submodule_search_locations=[str(_KERNEL_DIR)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot register kernel package")
    package = importlib.util.module_from_spec(spec)
    sys.modules["kernel"] = package
    try:
        spec.loader.exec_module(package)
    except Exception:
        sys.modules.pop("kernel", None)
        raise


def _load(name: str):
    _ensure_kernel_package()
    spec = importlib.util.spec_from_file_location(name, _KERNEL_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


def load_kernel(state_dir, *, signer=None, budget_ceilings=None):
    """Build a LockService with durable ledgers under <state_dir>/kernel/.

    signer defaults to LocalSigner reading OE_KERNEL_SIGNING_KEY — raising if
    absent/empty (fail closed, never an unsigned kernel). Pass a KMSSigner for
    production classes.
    """
    receipts = _load("receipts")
    lock_service = _load("lock_service")
    state_dir = Path(state_dir)
    kdir = state_dir / "kernel"
    if signer is None:
        signer = receipts.LocalSigner()  # raises on missing/empty key
    return lock_service.LockService(
        signer,
        budget_ledger=kdir / "budget.jsonl",
        freq_ledger=kdir / "frequency.jsonl",
        nonce_ledger=kdir / "nonces.jsonl",
        budget_ceilings=budget_ceilings,
    )
