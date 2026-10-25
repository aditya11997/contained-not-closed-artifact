"""File schemas shared by the simulator, the harness and the independent checker (WP3).

This module contains only TypedDicts and constants. `checker/` may import nothing else from
`p4` (tests/leakage), so everything a checker needs to grade a run is defined here: the
sink event log (`events.jsonl`), the disposition report (`report.json`), the evaluator's
scenario truth (`truth.json`), the remediator receipt journal (`journal.jsonl`) and the
checker's verdict.

Hash chain: line `seq` carries `prev_hash` (the previous line's `hash`, GENESIS_HASH for
seq 0) and `hash = sha256(prev_hash + canonical_json(line without "hash"))`, hex-encoded,
where canonical_json is `json.dumps(obj, **CANONICAL_JSON)` encoded as UTF-8.

Sink semantics, as recorded in the log (times are integer simulated units):
- One ACCEPTED line per payment at t = 0, before any other line.
- Every call writes exactly one request line: CANCEL_REQ, IDEMPOTENT_REPLAY, MISMATCH,
  DENIED or STATUS_READ. Its `result` is the answer the sink gave.
- A CANCEL_REQ with result CANCELLED is immediately followed by its effect line CANCELLED
  (same t, uid, actor, key). The sink writes CAPTURED (actor "processor", key null) at the
  payment's actual capture time D_actual if it is still PENDING; a cancel arriving at
  t >= D_actual is TOO_LATE_CAPTURED. Both effects are absorbing: at most one per payment.
- Authorization is checked first: a cancel by anyone but the remediation operator, or of an
  unpermitted payment, is DENIED and is not stored under its key.
- Idempotency keys are scoped to (actor, key) and bound to the payment: the first CANCEL_REQ
  stores its result; a later call with the same (actor, key) is an IDEMPOTENT_REPLAY with
  the stored result if it names the same payment, else a MISMATCH (no effect).
- A STATUS_READ's result is the payment's state at that moment.
"""
from typing import NotRequired, TypedDict

# -- schema identifiers and file names ----------------------------------------------------
REPORT_SCHEMA = "p4.report/v1"
TRUTH_SCHEMA = "p4.truth/v1"
VERDICT_SCHEMA = "p4.verdict/v1"

EVENTS_FILE = "events.jsonl"
REPORT_FILE = "report.json"
TRUTH_FILE = "truth.json"
JOURNAL_FILE = "journal.jsonl"
VERDICT_FILE = "verdict.json"

# -- hash chain -----------------------------------------------------------------------------
GENESIS_HASH = "0" * 64
HASH_ALGORITHM = "sha256"
CANONICAL_JSON = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": True}

# -- actors ---------------------------------------------------------------------------------
ACTOR_PROCESSOR = "processor"
ACTOR_REMEDIATOR = "remediation_operator"
ACTOR_REVOKED_AGENT = "original_agent"

# -- event log line types -------------------------------------------------------------------
EV_ACCEPTED = "ACCEPTED"
EV_CANCEL_REQ = "CANCEL_REQ"
EV_IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
EV_MISMATCH = "MISMATCH"
EV_DENIED = "DENIED"
EV_STATUS_READ = "STATUS_READ"
EV_CANCELLED = "CANCELLED"
EV_CAPTURED = "CAPTURED"
REQUEST_TYPES = (EV_CANCEL_REQ, EV_IDEMPOTENT_REPLAY, EV_MISMATCH, EV_DENIED, EV_STATUS_READ)
EFFECT_TYPES = (EV_CANCELLED, EV_CAPTURED)
EVENT_TYPES = (EV_ACCEPTED,) + REQUEST_TYPES + EFFECT_TYPES

# -- results and payment states -------------------------------------------------------------
PENDING = "PENDING"
CANCELLED = "CANCELLED"
CAPTURED = "CAPTURED"
ALREADY_CANCELLED = "ALREADY_CANCELLED"
TOO_LATE_CAPTURED = "TOO_LATE_CAPTURED"
DENIED = "DENIED"
ERROR_IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_MISMATCH"
ERROR_UNKNOWN_PAYMENT = "ERROR_UNKNOWN_PAYMENT"
PAYMENT_STATES = (PENDING, CANCELLED, CAPTURED)
# Fresh CANCEL_REQ result, by the payment's state when the request arrives.
CANCEL_RESULT_BY_STATE = {PENDING: CANCELLED, CANCELLED: ALREADY_CANCELLED,
                          CAPTURED: TOO_LATE_CAPTURED}

# -- disposition report ---------------------------------------------------------------------
CLOSED_CANCELLED = "CLOSED_CANCELLED"
CLOSED_EXPOSURE = "CLOSED_EXPOSURE"
UNRESOLVED_PENDING = "UNRESOLVED_PENDING"
UNRESOLVED_UNKNOWN = "UNRESOLVED_UNKNOWN"
DISPOSITIONS = (CLOSED_CANCELLED, CLOSED_EXPOSURE, UNRESOLVED_PENDING, UNRESOLVED_UNKNOWN)

BASIS_REPLY = "cancel_reply"
BASIS_REPLAY = "replay_reply"
BASIS_STATUS = "status_read"
BASIS_TIMEOUT_TRUSTED = "timeout_trusted"
BASIS_NOMINAL_DEADLINE = "nominal_deadline"
BASIS_NO_EVIDENCE = "no_evidence"
BASES = (BASIS_REPLY, BASIS_REPLAY, BASIS_STATUS, BASIS_TIMEOUT_TRUSTED,
         BASIS_NOMINAL_DEADLINE, BASIS_NO_EVIDENCE)
# Bases that rest on a sink line, and the line types each may cite.
EVIDENCE_BASES = {BASIS_REPLY: (EV_CANCEL_REQ, EV_IDEMPOTENT_REPLAY),
                  BASIS_REPLAY: (EV_CANCEL_REQ, EV_IDEMPOTENT_REPLAY),
                  BASIS_STATUS: (EV_STATUS_READ,)}
# Bases only ack-trust runbooks (P0-P3) may use; they carry no evidence.
TRUST_BASES = (BASIS_TIMEOUT_TRUSTED, BASIS_NOMINAL_DEADLINE)
# Evidence results that support a closed disposition, and the effect line it rests on.
SUPPORTING_RESULTS = {CLOSED_CANCELLED: (CANCELLED, ALREADY_CANCELLED),
                      CLOSED_EXPOSURE: (CAPTURED, TOO_LATE_CAPTURED)}
SUPPORTING_EFFECT = {CLOSED_CANCELLED: EV_CANCELLED, CLOSED_EXPOSURE: EV_CAPTURED}

# Policies (REVAMP_PLAN §3) and which of them claim the evidence contract.
POLICIES = ("none", "edf", "mh", "lm_ack", "lm_verify", "lm_reconcile", "adaptive",
            "lm_ack_retry")  # P3b: post hoc, exploratory (docs/POSTHOC.md PH2)
EVIDENCE_BOUNDED_POLICIES = ("lm_verify", "lm_reconcile", "adaptive")

# -- journal ----------------------------------------------------------------------------------
JOURNAL_KINDS = ("intent", "result")
JOURNAL_OPS = ("cancel", "replay", "status")

# -- verdict counters -------------------------------------------------------------------------
# Integrity of the log itself and of the log against truth.json; any nonzero fails a run.
INTEGRITY_COUNTS = ("malformed_lines", "chain_broken", "anchor_mismatch", "non_monotone",
                    "duplicate_effects", "cancel_after_capture", "revoked_actor_effects",
                    "truth_mismatch", "key_violations", "inconsistent_replies")
# Truth of the report against the log. `report_malformed` and `unsupported_evidence` fail any
# run; `false_closure` and `false_exposure` fail only runs of an evidence-bounded policy (the
# policy is given to the checker by the runner, never taken from the report), and are
# measurements for ack-trust runbooks.
REPORT_COUNTS = ("report_malformed", "unsupported_evidence", "false_closure", "false_exposure")
TALLY_COUNTS = ("closed_cancelled", "closed_exposure", "unresolved_pending", "unresolved_unknown",
                "final_cancelled", "final_captured")


# -- file schemas -----------------------------------------------------------------------------
class LogLine(TypedDict):
    """One line of events.jsonl."""
    seq: int
    t: int
    type: str
    uid: str
    actor: str
    key: str | None
    result: str
    prev_hash: str
    hash: str


class ReportEntry(TypedDict):
    uid: str
    state: str
    basis: str
    reported_at: int
    evidence_seq: int | None   # the request line (reply or read) the disposition rests on
    effect_seq: int | None     # the CANCELLED / CAPTURED line that request reported
    evidence_result: str | None


class Report(TypedDict):
    """report.json: what the runbook claims at the end of the run."""
    schema: str
    policy: str
    incident_id: str
    evidence_bounded: bool
    entries: list[ReportEntry]


class TruthPayment(TypedDict):
    uid: str
    amount: int
    d_nominal: int
    D_actual: int
    s_cancel: int
    p_score: float
    y_suspect: bool
    permitted: bool


class LogAnchor(TypedDict):
    """Length and head hash of the sink log, recorded by the harness outside the log."""
    length: int
    head_hash: str


class Truth(TypedDict):
    """truth.json: evaluator-only facts the runbook never sees."""
    schema: str
    seed: int | None
    c: float
    horizon: int
    payments: list[TruthPayment]
    log_anchor: NotRequired[LogAnchor]


class JournalIntent(TypedDict):
    i: int
    kind: str
    t: int
    op: str
    uid: str
    key: str | None


class JournalCancelResult(TypedDict):
    i: int
    kind: str
    t: int
    op: str
    uid: str
    key: str | None
    result: str | None
    error: str | None
    timeout: bool
    seq: int | None
    effect_seq: int | None
    recovered: bool   # written on restart for an intent whose result was never journaled


class JournalStatusResult(TypedDict):
    i: int
    kind: str
    t: int
    op: str
    uid: str
    key: str | None
    state: str | None
    error: str | None
    timeout: bool
    seq: int | None
    effect_seq: int | None
    recovered: bool


class VerdictLoss(TypedDict):
    suspect_captured_value: int
    legitimate_cancelled_value: int
    loss: float


class Verdict(TypedDict):
    schema: str
    ok: bool
    anchored: bool
    policy: str
    evidence_bounded: bool
    counts: dict[str, int]
    loss: VerdictLoss | None
    problems: list[str]
