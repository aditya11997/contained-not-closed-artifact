"""Hand-built scenarios and an independent hash-chain verifier for sink tests."""
import hashlib
import json

from p4.sim.clock import EventClock
from p4.sim.processor import SimProcessor
from p4.sim.scenario import PaymentTruth, Scenario

GENESIS = "0" * 64


def payment(uid, D=10, d=None, amount=100, s=1, p=0.9, y=True, permitted=True):
    return PaymentTruth(uid=uid, amount=amount, d_nominal=D if d is None else d, D_actual=D,
                        s_cancel=s, p_score=p, y_suspect=y, permitted=permitted)


def sink(*payments):
    clock = EventClock()
    return SimProcessor(Scenario(payments=tuple(payments)), clock), clock


def chain_ok(entries):
    """Recompute the SHA-256 chain without using p4.sim's own hashing code."""
    prev = GENESIS
    for i, e in enumerate(entries):
        if e["seq"] != i or e["prev_hash"] != prev:
            return False
        body = {k: v for k, v in e.items() if k != "hash"}
        canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256((prev + canon).encode()).hexdigest() != e["hash"]:
            return False
        prev = e["hash"]
    return True


def effects(entries, uid=None):
    return [e for e in entries if e["type"] in ("CANCELLED", "CAPTURED")
            and (uid is None or e["uid"] == uid)]
