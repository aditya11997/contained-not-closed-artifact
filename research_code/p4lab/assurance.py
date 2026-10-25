"""Disposition receipts with safe unknown-outcome handling.
Demonstrates semantics; NOT a production-grade distributed evidence store.
"""
import json
import os
from pathlib import Path


def verify_disposition(processor, uid: str, at_time: int, query_available: bool = True) -> str:
    """Closure is based on independently checked sink status, not a tool reply."""
    if not query_available:
        return "UNRESOLVED_UNKNOWN"
    state = processor.independent_state(uid, at_time)
    if state == "CANCELLED":
        return "CLOSED_CANCELLED"
    if state == "CAPTURED":
        # Closed as accounted for, NOT remediated or financially recovered.
        return "CLOSED_EXPOSURE"
    return "UNRESOLVED_PENDING"


def cancel_and_reconcile(processor, uid: str, finish_at: int, key: str,
                         lose_ack: bool = False, query_available: bool = True) -> dict:
    sink_reply = processor.cancel(uid, finish_at, "remediation_operator", key)
    observed_reply = "ACK_LOST" if lose_ack else sink_reply
    result = verify_disposition(processor, uid, finish_at, query_available)
    return {"uid":uid, "idempotency_key":key, "reply":observed_reply,
            "disposition":result, "checked_at":finish_at}


class Journal:
    """Small durable JSON record for replay demonstrations; atomic local writes."""
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data=json.loads(self.path.read_text()) if self.path.exists() else {}

    def put(self, receipt):
        self.data[receipt["idempotency_key"]]=receipt
        tmp=self.path.with_suffix(self.path.suffix+".tmp")
        tmp.write_text(json.dumps(self.data,indent=2,sort_keys=True),encoding="utf-8")
        os.replace(tmp,self.path)

    def get(self, key):
        return self.data.get(key)
