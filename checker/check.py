"""Independent checker CLI.

    python -m checker.check --log events.jsonl --report report.json \
        --scenario-truth truth.json --policy lm_verify --out verdict.json
    python -m checker.check --batch runs.jsonl --out verdicts.jsonl

The policy that produced the run is mandatory (the runner knows it): it decides whether the
evidence contract applies, so a report cannot opt out by relabelling itself. A batch file has
one JSON object per line: {"id", "log", "report", "truth", "policy"}; the output has one
verdict per line, in input order, each with that "id". Exit status: 0 if every verdict is ok,
1 if any is not, 2 on unreadable input, a missing or unknown policy, or bad arguments.

What is checked (everything from p4.schema; nothing is imported from the simulator):
- the log: line schema, hash chain, the anchor in truth.json (if present), monotone seq/t;
- the log replayed as a state machine: <= 1 effect per payment, no CANCELLED after CAPTURED,
  effects only by the remediation operator (cancel) or the processor (capture), every reply
  consistent with the state and idempotency store at that point, every CANCELLED before and
  every CAPTURED exactly at the payment's D_actual, one terminal effect per payment;
- the report: schema, coverage, and for each closed disposition that the log's final state
  agrees and that any cited evidence line exists, is about this payment and says so.
Loss is computed here from truth.json and the log's final states.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from p4 import schema as S

from .conform import conform

MAX_PROBLEMS = 200


class _Tally:
    def __init__(self):
        self.counts = dict.fromkeys(S.INTEGRITY_COUNTS + S.REPORT_COUNTS + S.TALLY_COUNTS, 0)
        self.problems: list[str] = []

    def flag(self, name: str, msg: str) -> None:
        self.counts[name] += 1
        if len(self.problems) < MAX_PROBLEMS:
            self.problems.append(f"{name}: {msg}")


def line_hash(prev_hash: str, line: dict) -> str:
    body = {k: v for k, v in line.items() if k != "hash"}
    canon = json.dumps(body, **S.CANONICAL_JSON)
    return hashlib.new(S.HASH_ALGORITHM, (prev_hash + canon).encode("utf-8")).hexdigest()


# -- the log ----------------------------------------------------------------------------------
def _check_log(lines: list, truth: dict, v: _Tally) -> dict[str, tuple[str, int, int]]:
    """Returns uid -> (effect type, t, seq) of each payment's first effect."""
    pay = {p["uid"]: p for p in truth["payments"]}
    prev_hash, prev_t = S.GENESIS_HASH, 0
    state: dict[str, str] = {}
    accepted: set[str] = set()
    first_effect: dict[str, tuple[str, int, int]] = {}
    keys: dict[tuple[str, str], tuple[str, str]] = {}   # (actor, key) -> (uid, stored result)
    in_accept_phase = True
    owed_effect = None  # (seq, uid, t, actor, key) of a CANCEL_REQ that must be followed by CANCELLED

    for i, line in enumerate(lines):
        where = f"seq {i}"
        problems = ["unparseable JSON"] if line is None else conform(line, S.LogLine)
        if problems:
            v.flag("malformed_lines", f"{where}: {problems[0]}")
            v.flag("chain_broken", f"{where}: unverifiable line")
            prev_hash = line.get("hash", "") if isinstance(line, dict) else ""
            owed_effect = None
            continue
        typ, uid, actor, key, res, t = (line["type"], line["uid"], line["actor"], line["key"],
                                        line["result"], line["t"])
        # chain and order
        if line["seq"] != i:
            v.flag("non_monotone", f"{where}: line says seq {line['seq']}")
        if line["prev_hash"] != prev_hash or line_hash(prev_hash, line) != line["hash"]:
            v.flag("chain_broken", f"{where}: hash chain does not verify")
        prev_hash = line["hash"]
        if t < prev_t or t < 0:
            v.flag("non_monotone", f"{where}: t={t} after t={prev_t}")
        prev_t = max(prev_t, t)
        if typ not in S.EVENT_TYPES:
            v.flag("malformed_lines", f"{where}: unknown type {typ!r}")
            continue

        if owed_effect is not None and typ != S.EV_CANCELLED:
            v.flag("inconsistent_replies", f"seq {owed_effect[0]}: CANCEL_REQ CANCELLED without its effect line")
            owed_effect = None

        if typ == S.EV_ACCEPTED:
            if (not in_accept_phase or uid not in pay or uid in accepted or t != 0
                    or actor != S.ACTOR_PROCESSOR or key is not None or res != S.PENDING):
                v.flag("truth_mismatch", f"{where}: unexpected ACCEPTED for {uid}")
            accepted.add(uid)
            state.setdefault(uid, S.PENDING)
            continue
        in_accept_phase = False
        known = uid in pay
        if known and uid not in state:
            state[uid] = S.PENDING  # flagged at the end as never accepted

        if typ == S.EV_DENIED:
            justified = actor != S.ACTOR_REMEDIATOR or (known and not pay[uid]["permitted"] and key is not None)
            if res != S.DENIED or not justified:
                v.flag("inconsistent_replies", f"{where}: DENIED without cause")
        elif typ == S.EV_STATUS_READ:
            expected = state[uid] if known else S.ERROR_UNKNOWN_PAYMENT
            if actor != S.ACTOR_REMEDIATOR or key is not None or res != expected:
                v.flag("inconsistent_replies", f"{where}: status read says {res}, state is {expected}")
        elif typ == S.EV_CANCEL_REQ:
            if actor != S.ACTOR_REMEDIATOR or key is None or (known and not pay[uid]["permitted"]):
                v.flag("inconsistent_replies", f"{where}: unauthorized cancel was not denied")
                continue
            if not known:
                if res != S.ERROR_UNKNOWN_PAYMENT:
                    v.flag("inconsistent_replies", f"{where}: cancel of unknown payment {uid} says {res}")
                continue
            if (actor, key) in keys:
                v.flag("key_violations", f"{where}: fresh request reuses key {key!r}")
            expected = S.CANCEL_RESULT_BY_STATE[state[uid]]
            if res != expected:
                v.flag("inconsistent_replies", f"{where}: cancel says {res}, expected {expected}")
            keys[(actor, key)] = (uid, res)
            if res == S.CANCELLED:
                owed_effect = (i, uid, t, actor, key)
        elif typ == S.EV_IDEMPOTENT_REPLAY:
            stored = keys.get((actor, key))
            if stored is None or stored[0] != uid:
                v.flag("key_violations", f"{where}: replay of key {key!r} not stored for {uid}")
            elif res != stored[1]:
                v.flag("inconsistent_replies", f"{where}: replay says {res}, stored {stored[1]}")
        elif typ == S.EV_MISMATCH:
            stored = keys.get((actor, key))
            if stored is None or stored[0] == uid:
                v.flag("key_violations", f"{where}: MISMATCH for key {key!r} without a conflicting use")
            if res != S.ERROR_IDEMPOTENCY_MISMATCH:
                v.flag("inconsistent_replies", f"{where}: MISMATCH says {res}")
        else:  # effects
            _check_effect(i, line, owed_effect, pay, state, first_effect, v)
            owed_effect = None

    if owed_effect is not None:
        v.flag("inconsistent_replies", f"seq {owed_effect[0]}: CANCEL_REQ CANCELLED without its effect line")
    for uid in pay:
        if uid not in accepted:
            v.flag("truth_mismatch", f"{uid}: no ACCEPTED line")
        if uid not in first_effect:
            v.flag("truth_mismatch", f"{uid}: no CANCELLED or CAPTURED line by the end of the log")
    return first_effect


def _check_effect(i, line, owed_effect, pay, state, first_effect, v: _Tally) -> None:
    where = f"seq {i}"
    typ, uid, actor, t = line["type"], line["uid"], line["actor"], line["t"]
    if uid not in pay:
        v.flag("truth_mismatch", f"{where}: {typ} of unknown payment {uid}")
        return
    if typ == S.EV_CANCELLED:
        if actor != S.ACTOR_REMEDIATOR:
            v.flag("revoked_actor_effects", f"{where}: CANCELLED by {actor!r}")
        if owed_effect is None or owed_effect[0] != i - 1 \
                or owed_effect[1:] != (uid, t, actor, line["key"]) or line["result"] != S.CANCELLED:
            v.flag("inconsistent_replies", f"{where}: CANCELLED is not the effect of the line before it")
        if t >= pay[uid]["D_actual"]:
            v.flag("truth_mismatch", f"{where}: CANCELLED at t={t} >= D_actual={pay[uid]['D_actual']}")
        if not pay[uid]["permitted"]:
            v.flag("truth_mismatch", f"{where}: CANCELLED an unpermitted payment")
    else:
        if actor != S.ACTOR_PROCESSOR:
            v.flag("revoked_actor_effects", f"{where}: CAPTURED by {actor!r}")
        if line["key"] is not None or line["result"] != S.CAPTURED:
            v.flag("inconsistent_replies", f"{where}: malformed CAPTURED line")
        if t != pay[uid]["D_actual"]:
            v.flag("truth_mismatch", f"{where}: CAPTURED at t={t}, D_actual={pay[uid]['D_actual']}")
    if state[uid] != S.PENDING:
        v.flag("duplicate_effects", f"{where}: second effect for {uid} (state {state[uid]})")
        if typ == S.EV_CANCELLED and state[uid] == S.CAPTURED:
            v.flag("cancel_after_capture", f"{where}: CANCELLED after CAPTURED for {uid}")
        return
    state[uid] = typ
    first_effect[uid] = (typ, t, i)


# -- the report -------------------------------------------------------------------------------
def _check_report(report, lines, truth, first_effect, v: _Tally, expect_policy: str) -> None:
    """The evidence contract applies iff `expect_policy` (given by the runner) is P4-P6."""
    bounded = expect_policy in S.EVIDENCE_BOUNDED_POLICIES
    problems = conform(report, S.Report)
    if problems:
        v.flag("report_malformed", problems[0])
        return
    policy = report["policy"]
    if report["schema"] != S.REPORT_SCHEMA:
        v.flag("report_malformed", f"schema {report['schema']!r}")
    if policy != expect_policy:
        v.flag("report_malformed", f"report is for {policy!r}, expected {expect_policy!r}")
    if report["evidence_bounded"] != bounded:
        v.flag("report_malformed", f"evidence_bounded={report['evidence_bounded']} for {policy!r}")

    pay = {p["uid"]: p for p in truth["payments"]}
    seen = set()
    for e in report["entries"]:
        uid, st, basis = e["uid"], e["state"], e["basis"]
        if uid in seen or uid not in pay or st not in S.DISPOSITIONS or basis not in S.BASES:
            v.flag("report_malformed", f"entry {uid!r}: duplicate/unknown uid, state or basis")
            continue
        seen.add(uid)
        final = first_effect.get(uid)
        if st == S.CLOSED_CANCELLED:
            v.counts["closed_cancelled"] += 1
            if final is None or final[0] != S.EV_CANCELLED or final[1] >= pay[uid]["D_actual"]:
                v.flag("false_closure", f"{uid}: reported CLOSED_CANCELLED, log final is {final and final[0]}")
        elif st == S.CLOSED_EXPOSURE:
            v.counts["closed_exposure"] += 1
            if final is None or final[0] != S.EV_CAPTURED:
                v.flag("false_exposure", f"{uid}: reported CLOSED_EXPOSURE, log final is {final and final[0]}")
        else:
            v.counts["unresolved_pending" if st == S.UNRESOLVED_PENDING else "unresolved_unknown"] += 1
        why = _evidence_problem(e, lines, bounded)
        if why:
            v.flag("unsupported_evidence", f"{uid}: {why}")
    missing = sorted(set(pay) - seen)
    if missing:
        v.flag("report_malformed", f"no entry for {missing}")


def _line(lines, seq):
    if isinstance(seq, int) and 0 <= seq < len(lines) and isinstance(lines[seq], dict):
        return lines[seq]
    return None


def _evidence_problem(e: dict, lines: list, bounded: bool) -> str | None:
    uid, st, basis, seq, eff = e["uid"], e["state"], e["basis"], e["evidence_seq"], e["effect_seq"]
    closed = st in S.SUPPORTING_RESULTS
    if basis in S.TRUST_BASES:
        if bounded or not closed:
            return f"basis {basis} is not evidence"
        return None if seq is None and eff is None else "trust basis cites evidence"
    if basis == S.BASIS_NO_EVIDENCE:
        if closed:
            return "closed with basis no_evidence"
        if seq is None and eff is None:
            return None
    if seq is None:
        return f"basis {basis} without an evidence line"
    line = _line(lines, seq)
    if line is None:
        return f"evidence_seq {seq} is not a log line"
    if line.get("uid") != uid:
        return f"evidence_seq {seq} is about {line.get('uid')!r}"
    if not closed:
        return None
    if line.get("type") not in S.EVIDENCE_BASES.get(basis, ()) or line.get("actor") != S.ACTOR_REMEDIATOR:
        return f"evidence_seq {seq} is a {line.get('type')} line, not a {basis}"
    if line.get("result") not in S.SUPPORTING_RESULTS[st] or line.get("result") != e["evidence_result"]:
        return f"evidence_seq {seq} says {line.get('result')}, not {st}"
    if line.get("t", 0) > e["reported_at"]:
        return f"evidence_seq {seq} at t={line.get('t')} is after reported_at={e['reported_at']}"
    effect = _line(lines, eff)
    if effect is None or effect.get("uid") != uid or effect.get("type") != S.SUPPORTING_EFFECT[st] \
            or effect.get("t", 0) > e["reported_at"]:
        return f"effect_seq {eff} is not a {S.SUPPORTING_EFFECT[st]} line for {uid}"
    return None


# -- verdict ----------------------------------------------------------------------------------
def check_run(lines: list, report, truth, policy: str) -> dict:
    """Grade one run of `policy`. `lines` are parsed log lines (None for an unparseable line)."""
    if policy not in S.POLICIES:
        raise ValueError(f"policy must be one of {S.POLICIES}, got {policy!r}")
    bounded = policy in S.EVIDENCE_BOUNDED_POLICIES
    v = _Tally()
    problems = conform(truth, S.Truth)
    if problems or truth.get("schema") != S.TRUTH_SCHEMA:
        raise ValueError(f"truth.json does not conform: {problems[:3] or truth.get('schema')}")
    first_effect = _check_log(lines, truth, v)
    anchor = truth.get("log_anchor")
    if anchor is not None:
        head = lines[-1].get("hash") if lines and isinstance(lines[-1], dict) else S.GENESIS_HASH
        if anchor["length"] != len(lines) or anchor["head_hash"] != head:
            v.flag("anchor_mismatch", f"log has {len(lines)} lines, anchor says {anchor['length']}")
    _check_report(report, lines, truth, first_effect, v, policy)

    finals = {uid: f[0] for uid, f in first_effect.items()}
    v.counts["final_cancelled"] = sum(f == S.EV_CANCELLED for f in finals.values())
    v.counts["final_captured"] = sum(f == S.EV_CAPTURED for f in finals.values())
    suspect = sum(p["amount"] for p in truth["payments"] if p["y_suspect"] and finals.get(p["uid"]) == S.EV_CAPTURED)
    legit = sum(p["amount"] for p in truth["payments"] if not p["y_suspect"] and finals.get(p["uid"]) == S.EV_CANCELLED)

    ok = all(v.counts[k] == 0 for k in S.INTEGRITY_COUNTS + ("report_malformed", "unsupported_evidence"))
    if bounded:
        ok = ok and v.counts["false_closure"] == 0 and v.counts["false_exposure"] == 0
    return {
        "schema": S.VERDICT_SCHEMA,
        "ok": ok,
        "anchored": anchor is not None,
        "policy": policy,
        "evidence_bounded": bounded,
        "counts": v.counts,
        "loss": {"suspect_captured_value": suspect, "legitimate_cancelled_value": legit,
                 "loss": suspect + truth["c"] * legit},
        "problems": v.problems,
    }


def read_log(path) -> list:
    lines = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            lines.append(None)
    return lines


def check_files(log, report, truth, policy: str) -> dict:
    return check_run(read_log(log), json.loads(Path(report).read_text(encoding="utf-8")),
                     json.loads(Path(truth).read_text(encoding="utf-8")), policy)


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m checker.check", description=__doc__.splitlines()[0])
    ap.add_argument("--log")
    ap.add_argument("--report")
    ap.add_argument("--scenario-truth", dest="truth")
    ap.add_argument("--policy", help="policy that produced the run (required)")
    ap.add_argument("--batch", help="JSONL of runs to check")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        if args.batch:
            if args.log or args.report or args.truth:
                ap.error("--batch excludes --log/--report/--scenario-truth")
            runs = [json.loads(x) for x in Path(args.batch).read_text(encoding="utf-8").splitlines() if x.strip()]
            for r in runs:
                if "policy" not in r:
                    raise ValueError(f"batch run {r.get('id')!r} has no policy")
            verdicts = [{"id": r["id"], **check_files(r["log"], r["report"], r["truth"], r["policy"])}
                        for r in runs]
            Path(args.out).write_text("".join(_dump(x) + "\n" for x in verdicts), encoding="utf-8")
        else:
            if not (args.log and args.report and args.truth and args.policy):
                ap.error("need --log, --report, --scenario-truth and --policy (or --batch)")
            verdicts = [check_files(args.log, args.report, args.truth, args.policy)]
            Path(args.out).write_text(_dump(verdicts[0]) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"checker: {exc}", file=sys.stderr)
        return 2
    return 0 if all(x["ok"] for x in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
