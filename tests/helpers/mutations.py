"""Tamperings of an exported run (log lines + report) for the checker mutation tests.

Each mutation takes deep copies of (lines, report) and a numpy Generator and returns the
tampered (lines, report), or None if this run offers nothing to tamper with (e.g. no
CANCELLED line). With `rechain=True` the forger also renumbers `seq`, re-points the report's
evidence/effect seqs and recomputes the whole hash chain with its own code, so only the
checker's semantic checks (and the external anchor) can catch it.
"""
import copy
import hashlib
import json

GENESIS = "0" * 64
REVOKED = "original_agent"


def _hash(prev, line):
    body = {k: v for k, v in line.items() if k != "hash"}
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


def rechain(lines, report):
    """Renumber, re-point report seqs at the moved lines, recompute every hash."""
    moved = {line["seq"]: i for i, line in enumerate(lines) if line.get("seq") is not None}
    for e in report["entries"]:
        for name in ("evidence_seq", "effect_seq"):
            if e[name] is not None and e[name] in moved:
                e[name] = moved[e[name]]
    prev = GENESIS
    for i, line in enumerate(lines):
        line["seq"], line["prev_hash"] = i, prev
        line["hash"] = prev = _hash(prev, line)
    return lines, report


def _effects(lines, typ):
    return [i for i, x in enumerate(lines) if x["type"] == typ]


def forged_closure(lines, report, rng):
    captured = {lines[i]["uid"]: i for i in _effects(lines, "CAPTURED")}
    targets = [e for e in report["entries"] if e["uid"] in captured and e["state"] != "CLOSED_CANCELLED"]
    if not targets:
        return None
    e = targets[rng.integers(len(targets))]
    reads = [i for i, x in enumerate(lines) if x["uid"] == e["uid"] and x["type"] == "STATUS_READ"]
    e.update(state="CLOSED_CANCELLED", basis="status_read", evidence_result="CANCELLED",
             evidence_seq=reads[-1] if reads else captured[e["uid"]], effect_seq=captured[e["uid"]])
    return lines, report


def delete_captured(lines, report, rng):
    idx = _effects(lines, "CAPTURED")
    if not idx:
        return None
    del lines[idx[rng.integers(len(idx))]]
    return lines, report


def edit_timestamp(lines, report, rng):
    """Back-date a CANCELLED line or delay a CAPTURED line by one time unit."""
    idx = _effects(lines, "CANCELLED") + _effects(lines, "CAPTURED")
    i = idx[rng.integers(len(idx))]
    lines[i]["t"] += -1 if lines[i]["type"] == "CANCELLED" and lines[i]["t"] > 0 else 1
    return lines, report


def duplicate_key_second_effect(lines, report, rng):
    """Re-use a cancel key for a fresh CANCEL_REQ that produces a second CANCELLED effect."""
    idx = _effects(lines, "CANCELLED")
    if not idx:
        return None
    src = lines[idx[rng.integers(len(idx))]]
    at = max(i for i, x in enumerate(lines) if x["t"] <= src["t"]) + 1
    req = {**src, "type": "CANCEL_REQ", "seq": None}
    eff = {**src, "seq": None}
    lines[at:at] = [req, eff]
    return lines, report


def broken_hash(lines, report, rng):
    line = lines[rng.integers(len(lines))]
    line["hash"] = line["hash"][:-1] + ("0" if line["hash"][-1] != "0" else "1")
    return lines, report


def wrong_uid_evidence(lines, report, rng):
    cited = [e for e in report["entries"] if e["evidence_seq"] is not None]
    if not cited:
        return None
    e = cited[rng.integers(len(cited))]
    src = lines[e["evidence_seq"]]
    same = [i for i, x in enumerate(lines) if x["uid"] != e["uid"]
            and x["type"] == src["type"] and x["result"] == src["result"]]
    other = same or [i for i, x in enumerate(lines) if x["uid"] != e["uid"]]
    e["evidence_seq"] = other[rng.integers(len(other))]
    return lines, report


def revoked_actor_effect(lines, report, rng):
    """Attribute a cancel and its CANCELLED effect to the revoked agent."""
    idx = _effects(lines, "CANCELLED")
    if not idx:
        return None
    i = idx[rng.integers(len(idx))]
    lines[i]["actor"] = REVOKED
    if i > 0 and lines[i - 1]["type"] == "CANCEL_REQ":
        lines[i - 1]["actor"] = REVOKED
    return lines, report


LOG_MUTATIONS = {
    "deleted_captured": delete_captured,
    "edited_timestamp": edit_timestamp,
    "duplicate_key_second_effect": duplicate_key_second_effect,
    "revoked_actor_effect": revoked_actor_effect,
}
RAW_ONLY = {"broken_hash": broken_hash}
REPORT_MUTATIONS = {"forged_closed_cancelled": forged_closure, "evidence_wrong_uid": wrong_uid_evidence}
ALL = {**LOG_MUTATIONS, **RAW_ONLY, **REPORT_MUTATIONS}


def apply(name, lines, report, rng, rechain_log=False):
    lines, report = copy.deepcopy(lines), copy.deepcopy(report)
    out = ALL[name](lines, report, rng)
    if out is None:
        return None
    if rechain_log:
        return rechain(*out)
    for i, line in enumerate(out[0]):  # a naive forger numbers inserted lines, nothing else
        if line["seq"] is None:
            line["seq"] = i
    return out
