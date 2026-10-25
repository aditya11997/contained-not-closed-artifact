"""Simple lost-ack, missing-status and idempotent-replay demonstration."""
from tempfile import TemporaryDirectory
from pathlib import Path
from p4lab.model import Payment, SyntheticProcessor
from p4lab.assurance import Journal, cancel_and_reconcile

p=(Payment("P01",500,10,2,0.91,True),)
sink=SyntheticProcessor(p)
key="P01/incident-07/cancel"
with TemporaryDirectory() as td:
    journal=Journal(Path(td)/"journal.json")
    first=cancel_and_reconcile(sink,"P01",2,key,lose_ack=True,query_available=False)
    journal.put(first)
    print("1 Lost acknowledgment and status query unavailable:", first["disposition"])
    after_restart=cancel_and_reconcile(sink,"P01",2,key,lose_ack=False,query_available=True)
    journal=Journal(Path(td)/"journal.json")
    journal.put(after_restart)
    print("2 Replayed with same key and successful verification:", after_restart["disposition"])
    print("3 Distinct successful cancel effects in sink:", sum(e[2]=="CANCELLED" for e in sink.events))
