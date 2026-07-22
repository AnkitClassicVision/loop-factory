"""The lock service — the ONLY thing a department may call to make anything
happen. It holds the signer, the clock, the gateways, and the durable
consumption/revocation ledgers; the department holds none of them.

Hardened against the kernel-v1 GLM adversarial review:
- The clock is the SERVER's (injected once at construction), never a per-call
  argument, so a department cannot forge time to revive an expired receipt
  (P0 #1).
- Revocation is enforced at use: `revoked` is threaded into every gateway
  verify, so `revoke()` actually kills an outstanding slip (P0 #2).
- The frequency gate is enforced INSIDE send issuance (a slot is reserved and
  bound into the receipt), so the effect path cannot be reached without it
  (P0 #3, P1 #7).
- Consumed nonces and revocations are DURABLE (jsonl ledgers loaded at
  construction), so a restart within the TTL window does not reopen replay or
  revocation; issuance/verify refuse if the ledger cannot be loaded (P0 #4).
- `ttl_s` is capped server-side at MAX_TTL_S (P1 #6).
- Nonces are random (P2 #15).
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets
import sys
import time

# The kernel is not installed as a package; make sibling modules importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import receipts  # noqa: E402
from gateways import budget as budget_mod  # noqa: E402
from gateways import dispatch  # noqa: E402
from gateways import frequency as freq_mod  # noqa: E402
from gateways import model  # noqa: E402
from gateways import read_broker  # noqa: E402


# charter budget.weekly_ceilings (mirror; source of truth is charter.yaml)
DEFAULT_CEILINGS = {"model_calls": 900, "dollars": 40, "worker_minutes": 1200}
MAX_TTL_S = 300  # server-side cap on any permission slip's lifetime


class LockServiceDown(RuntimeError):
    """A gateway raised an unexpected error; the request is refused, not allowed."""


class DurableNonceSet:
    """A set with a jsonl backing file, drop-in for the `seen_nonces`/`revoked`
    arguments of verify_receipt. `add` persists immediately (durable single-use
    consumption); the set is reloaded on construction so a restart keeps the
    record. A load failure raises so callers fail closed rather than start empty.
    """

    def __init__(self, path):
        self._path = pathlib.Path(path)
        self._mem: set = set()
        if self._path.exists():
            # GLM P1-N1: tolerate a torn trailing line from a crashed write
            # instead of aborting startup. A torn line is a consumption that did
            # not durably commit (fsync happens before the effect returns), so
            # skipping it is safe: that receipt's effect never completed.
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._mem.add(json.loads(line)["nonce"])
                except (ValueError, KeyError, TypeError):
                    continue

    def __contains__(self, nonce):
        return nonce in self._mem

    def add(self, nonce):
        if nonce in self._mem:
            return
        self._mem.add(nonce)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # GLM P1-N1: durably persist the consumption BEFORE the caller proceeds to
        # the effect, so a crash cannot lose a consumed nonce and reopen replay.
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"nonce": nonce}) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


class LockService:
    def __init__(self, signer, *, budget_ledger, freq_ledger, nonce_ledger,
                 clock=time.time, budget_ceilings=None, max_ttl_s=MAX_TTL_S):
        self.signer = signer
        self.clock = clock  # server clock; NOT caller-supplied per call
        self.max_ttl_s = max_ttl_s
        nonce_ledger = pathlib.Path(nonce_ledger)
        self.seen_nonces = DurableNonceSet(nonce_ledger.with_suffix(".consumed.jsonl"))
        self.revoked = DurableNonceSet(nonce_ledger.with_suffix(".revoked.jsonl"))
        self.budget = budget_mod.BudgetBroker(budget_ledger, budget_ceilings or DEFAULT_CEILINGS)
        self.freq = freq_mod.FrequencyService(freq_ledger)

    # --- trusted internals --------------------------------------------------- #

    def _now(self) -> float:
        return self.clock()

    def _nonce(self) -> str:
        return secrets.token_hex(16)

    def _ttl(self, requested) -> int:
        return min(int(requested), self.max_ttl_s)

    # --- eligibility gates --------------------------------------------------- #

    # Internal (GLM P2-N2): NOT public. A department holds the LockService
    # reference via kernel_bridge; a public reserve_* would let it consume
    # another principal's frequency slot or reserve arbitrary global budget with
    # no slip. Reservation happens only inside request_send / request_model, on
    # the caller's own behalf.
    def _reserve_slot(self, person, org):
        return self.freq.reserve_slot(person, org, self._now())

    def _reserve_budget(self, kind, amount):
        return self.budget.reserve(kind, amount, self._now())

    def read(self, source, record, fields):
        return read_broker.read(source, record, fields)

    def revoke(self, nonce):
        """Kill an outstanding permission slip. Durable and enforced at use."""
        self.revoked.add(nonce)

    # --- permission slips (frequency enforced INSIDE send issuance) ---------- #

    def request_send(self, to, subject, body, *, person, org, ttl_s=MAX_TTL_S) -> dict:
        # reserve the touch slot first — raises FrequencyDenied on a cap, so no
        # receipt can be minted for an over-frequency send.
        slot = self.freq.reserve_slot(person, org, self._now())
        nonce = self._nonce()
        binding = dispatch.send_binding(to, subject, body, slot)
        receipt = receipts.issue_receipt(
            "external_send", binding, self._ttl(ttl_s), self.signer, self._now(), nonce
        )
        return {"receipt": receipt, "slot": slot, "nonce": nonce}

    def request_model(self, prompt, *, sanitized=False, ttl_s=MAX_TTL_S) -> dict:
        # Codex review P0 #2: the caller must ATTEST the privacy preflight (S3)
        # ran over exactly this prompt — the lock service never labels a raw
        # prompt sanitized on its own. False/absent attestation = no receipt.
        # (Full trusted-sanitizer separation is a documented known limit.)
        if not sanitized:
            raise model.GatewayDenied(
                "prompt lacks a privacy-preflight attestation (sanitized=True)")
        # reserve the model-call spend first — raises BudgetExceeded /
        # BudgetReviewRequired on/near the ceiling, so no receipt is minted for a
        # call that would breach budget (GLM P0 #3, budget half).
        self.budget.reserve("model_calls", 1, self._now())
        nonce = self._nonce()
        binding = model.model_binding(prompt, sanitized=True)
        receipt = receipts.issue_receipt(
            "model_call", binding, self._ttl(ttl_s), self.signer, self._now(), nonce
        )
        return {"receipt": receipt, "nonce": nonce}

    # --- execution (server clock + revocation + durable single-use) ---------- #

    def send(self, to, subject, body, receipt, *, slot=None, sink=None, live=False) -> dict:
        try:
            return dispatch.dispatch(
                to, subject, body, receipt,
                signer=self.signer, now=self._now(),
                seen_nonces=self.seen_nonces, revoked=self.revoked,
                slot=slot, sink=sink, live=live,
            )
        except dispatch.GatewayDenied:
            raise
        except Exception as exc:  # gateway malfunction -> refuse, never allow
            raise LockServiceDown(f"dispatch gateway error: {exc}") from exc

    def call_model(self, prompt, receipt, *, runner) -> str:
        try:
            return model.call_model(
                prompt, receipt,
                signer=self.signer, now=self._now(),
                seen_nonces=self.seen_nonces, revoked=self.revoked, runner=runner,
            )
        except model.GatewayDenied:
            raise
        except Exception as exc:
            raise LockServiceDown(f"model gateway error: {exc}") from exc
