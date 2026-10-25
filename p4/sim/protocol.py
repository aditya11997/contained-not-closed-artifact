"""Remediator-facing processor interface, shared by SimProcessor, FaultyTransport and the
Stripe test-mode adapter (WP5).

Runbooks import this module, never `p4.sim.processor` (CLAUDE.md rule 1). No method takes a
time argument: the sink owns the clock (CODE_AUDIT A3).
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

REMEDIATION_OPERATOR = "remediation_operator"
ORIGINAL_AGENT = "original_agent"
# The agent identity of the WP6 case study: it creates payments before the stop and is the
# one `incident_stop` revokes.
PROCUREMENT_AGENT = "procurement_agent"

# Cancel results that reflect sink state (and are stored under the idempotency key).
CANCELLED = "CANCELLED"
ALREADY_CANCELLED = "ALREADY_CANCELLED"
TOO_LATE_CAPTURED = "TOO_LATE_CAPTURED"
# Rejection (not stored; the same key can later be used by an authorized request).
DENIED = "DENIED"
# Errors (not stored, no effect).
ERROR_IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_MISMATCH"
ERROR_UNKNOWN_PAYMENT = "ERROR_UNKNOWN_PAYMENT"

# Payment states as reported by a status read.
PENDING = "PENDING"
CAPTURED = "CAPTURED"


@dataclass(frozen=True)
class Reply:
    """Reply to a cancel. Exactly one of `result`, `error`, `timeout` is set.

    `seq` is the sink log line recording this request; `effect_seq` is the CANCELLED or
    CAPTURED line the result rests on (None if none). Both are None on timeout.
    """
    result: str | None = None
    error: str | None = None
    timeout: bool = False
    seq: int | None = None
    effect_seq: int | None = None


@dataclass(frozen=True)
class StatusReply:
    """Reply to a status read. `state` is PENDING, CANCELLED or CAPTURED unless `error` or
    `timeout` is set."""
    state: str | None = None
    error: str | None = None
    timeout: bool = False
    seq: int | None = None
    effect_seq: int | None = None


@dataclass(frozen=True)
class CreateReply:
    """Reply to `create_payment`. On success `payment_id` names a payment held for capture
    (state PENDING) and `replayed` says the idempotency key returned an earlier create.
    Otherwise exactly one of `result` (DENIED), `error` or `timeout` is set."""
    payment_id: str | None = None
    state: str | None = None
    replayed: bool = False
    result: str | None = None
    error: str | None = None
    timeout: bool = False


@dataclass(frozen=True)
class StopReceipt:
    """What `incident_stop` did: the revoked actor and the payments left pending."""
    revoked: str
    pending: tuple[str, ...]


@runtime_checkable
class AgentGateway(Protocol):
    """The create path the WP6 agent uses, plus the stop. Implemented by the sim gateway
    (p4/sim/agent_gateway.py) and the Stripe test-mode adapter (p4/adapters/stripe_test.py)."""
    def create_payment(self, actor: str, amount: int, vendor: str, idem_key: str) -> CreateReply: ...

    def incident_stop(self, actor: str) -> StopReceipt: ...


@runtime_checkable
class Processor(Protocol):
    def cancel(self, actor: str, uid: str, idem_key: str) -> Reply: ...

    def status(self, actor: str, uid: str) -> StatusReply: ...


@runtime_checkable
class AsyncProcessor(Processor, Protocol):
    """Non-blocking calls for k parallel workers: the call occupies the caller's worker and
    `on_reply` runs when it completes on the shared clock."""
    def submit_cancel(self, actor: str, uid: str, idem_key: str,
                      on_reply: Callable[[Reply], None]) -> None: ...

    def submit_status(self, actor: str, uid: str,
                      on_reply: Callable[[StatusReply], None]) -> None: ...
