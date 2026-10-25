"""WP5 adapter, offline: key refusal, the HTTP layer, the token bucket, the fault proxy and the
adapter's state mapping, all against a mocked HTTP layer (tests/helpers/fake_stripe.py).
No network: a test that reached urllib's real urlopen would fail."""
import io
import json
import threading
import urllib.error

import pytest
from fake_stripe import FakeStripe

from p4.adapters import stripe_test as st
from p4.adapters.stripe_test import (
    HttpResponse,
    RateLimitedClient,
    StripeKeyError,
    StripeTestProcessor,
    StripeUnavailable,
    TokenBucket,
    TransportError,
    UrllibHttpClient,
    WallClockDriver,
    check_test_key,
    classify,
    form_encode,
    load_test_key,
)
from p4.harness import check_runs, export_run, run_episode
from p4.sim.agent_gateway import CREATE_UID, AgentPaymentRules
from p4.sim.clock import EventClock
from p4.sim.faults import FaultConfig, fault_draw, load_fault_presets
from p4.sim.protocol import (
    ALREADY_CANCELLED,
    CANCELLED,
    CAPTURED,
    DENIED,
    ERROR_IDEMPOTENCY_MISMATCH,
    ERROR_UNKNOWN_PAYMENT,
    PENDING,
    PROCUREMENT_AGENT,
    REMEDIATION_OPERATOR,
    TOO_LATE_CAPTURED,
    AgentGateway,
    Processor,
)

AGENT, OP = PROCUREMENT_AGENT, REMEDIATION_OPERATOR
# Built at runtime so no key-shaped literal sits in the repo (scripts/check_secrets.py).
FAKE_TEST_KEY = "sk_" + "test_" + "FAKE0000000000000000"
FAKE_LIVE_KEY = "sk_" + "live_" + "FAKE0000000000000000"
RULES = AgentPaymentRules(d_min=5, d_max=5)   # D_actual = 5 for every payment


def types(log, uid=None):
    return [(e["type"], e["result"]) for e in log if uid is None or e["uid"] == uid]


def adapter(fake=None, *, faults=None, purchases=((1200, "acme"), (800, "globex")), stop=True, rules=RULES):
    fake = fake or FakeStripe()
    a = StripeTestProcessor(fake, run_id="unit", seed=100777, rules=rules, faults=faults, sleep=lambda s: None)
    for i, (amount, vendor) in enumerate(purchases):
        assert a.create_payment(AGENT, amount, vendor, f"po-{i}").state == PENDING
    if stop:
        a.incident_stop(AGENT)
    return a, fake


# -- keys -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [None, "", "sk_test_", FAKE_LIVE_KEY, "rk_" + "test_abc", "pk_" + "test_abc",
                                 "sk_" + "org_abc", " " + FAKE_TEST_KEY])
def test_refuses_any_key_not_starting_with_sk_test(bad):
    with pytest.raises(StripeKeyError) as exc:
        check_test_key(bad)
    assert not bad or bad == "sk_test_" or bad not in str(exc.value)
    with pytest.raises(StripeKeyError):
        UrllibHttpClient(bad)


def test_key_comes_only_from_stripe_test_key():
    assert load_test_key({"STRIPE_TEST_KEY": FAKE_TEST_KEY}) == FAKE_TEST_KEY
    for env in ({}, {"STRIPE_SECRET_KEY": FAKE_TEST_KEY}, {"STRIPE_API_KEY": FAKE_TEST_KEY}):
        with pytest.raises(StripeKeyError):
            load_test_key(env)
    with pytest.raises(StripeKeyError) as exc:
        load_test_key({"STRIPE_TEST_KEY": FAKE_LIVE_KEY})
    assert FAKE_LIVE_KEY not in str(exc.value)


# -- HTTP layer ---------------------------------------------------------------------------------------
class _Resp(io.BytesIO):
    def __init__(self, status, headers, body):
        super().__init__(json.dumps(body).encode())
        self.status, self.headers = status, headers


def test_urllib_client_sends_auth_version_key_and_form_body():
    seen = []

    def urlopen(req, timeout):
        seen.append(req)
        return _Resp(200, {"Idempotent-Replayed": "true", "Request-Id": "req_1"}, {"id": "pi_1"})

    c = UrllibHttpClient(FAKE_TEST_KEY, urlopen=urlopen)
    assert FAKE_TEST_KEY not in repr(c) and "redacted" in repr(c)
    r = c.request("POST", "/v1/payment_intents/pi_1/cancel", {"metadata": {"a": "b"}},
                  {"Idempotency-Key": "p4/x/op/k"})
    assert r.replayed and r.headers["request-id"] == "req_1" and r.body == {"id": "pi_1"}
    (req,) = seen
    assert req.full_url == "https://api.stripe.com/v1/payment_intents/pi_1/cancel"
    assert req.get_header("Authorization").startswith("Basic ")
    assert req.get_header("Stripe-version") == st.STRIPE_VERSION
    assert req.get_header("Idempotency-key") == "p4/x/op/k"
    assert req.data == b"metadata%5Ba%5D=b"


def test_urllib_client_returns_http_errors_and_raises_transport_errors():
    def http_error(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {"Request-Id": "r"},
                                     io.BytesIO(b'{"error":{"type":"idempotency_error"}}'))

    r = UrllibHttpClient(FAKE_TEST_KEY, urlopen=http_error).request("GET", "/v1/payment_intents/pi_1")
    assert r.status == 400 and r.error["type"] == "idempotency_error"

    def down(req, timeout):
        raise urllib.error.URLError("connection refused")

    with pytest.raises(TransportError) as exc:
        UrllibHttpClient(FAKE_TEST_KEY, urlopen=down).request("GET", "/v1/payment_intents/pi_1")
    assert FAKE_TEST_KEY not in str(exc.value)


@pytest.mark.parametrize("method,path", [("DELETE", "/v1/x"), ("GET", "/v2/x"), ("GET", "https://evil/v1/x"),
                                         ("GET", "/v1/../x"), ("GET", "//evil.example/v1/x")])
def test_urllib_client_talks_only_to_the_v1_api(method, path):
    with pytest.raises(ValueError):
        UrllibHttpClient(FAKE_TEST_KEY, urlopen=lambda *a, **k: pytest.fail("sent")).request(method, path)


def test_form_encode_matches_stripe_conventions():
    assert form_encode({"amount": 5, "confirm": True, "automatic_payment_methods": {"enabled": True},
                        "types": ["card", "link"], "skip": None}) == (
        "amount=5&confirm=true&automatic_payment_methods%5Benabled%5D=true&types%5B%5D=card&types%5B%5D=link")


def test_classify_names_only_remediator_calls():
    assert classify("POST", "/v1/payment_intents/pi_1/cancel") == ("cancel", "pi_1")
    assert classify("GET", "/v1/payment_intents/pi_1") == ("status", "pi_1")
    assert classify("POST", "/v1/payment_intents/pi_1/capture") == (None, None)
    assert classify("POST", "/v1/payment_intents") == (None, None)


# -- rate limiting -----------------------------------------------------------------------------------
class FakeTime:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def test_token_bucket_allows_a_burst_then_holds_the_rate():
    ft = FakeTime()
    b = TokenBucket(rate=10, burst=3, monotonic=ft.monotonic, sleep=ft.sleep)
    for _ in range(3):
        b.acquire()
    assert ft.sleeps == []
    for _ in range(20):
        b.acquire()
    assert ft.t == pytest.approx(2.0)          # 20 more requests at 10/s
    assert 23 / ft.t <= 10 + 3 / ft.t + 1e-9    # never above rate + burst


def test_rate_limited_client_retries_429_with_the_same_key():
    fake, ft = FakeStripe(), FakeTime()
    fake.rate_limit_next = 2
    c = RateLimitedClient(fake, TokenBucket(monotonic=ft.monotonic, sleep=ft.sleep), sleep=ft.sleep)
    r = c.request("POST", "/v1/payment_intents", {"amount": 100}, {"Idempotency-Key": "k"})
    assert r.status == 200 and c.retries == 2 and len(fake.pis) == 1
    assert {h["Idempotency-Key"] for _, _, _, h in fake.requests} == {"k"}
    fake.rate_limit_next = 10
    c2 = RateLimitedClient(fake, TokenBucket(monotonic=ft.monotonic, sleep=ft.sleep), max_retries=2, sleep=ft.sleep)
    assert c2.request("GET", "/v1/payment_intents/pi_fake0000").status == 429


# -- adapter: create path and stop -------------------------------------------------------------------------
def test_implements_both_protocols():
    a, _ = adapter(stop=False)
    assert isinstance(a, AgentGateway) and isinstance(a, Processor)


def test_create_is_a_confirmed_manual_capture_payment_intent():
    a, fake = adapter(stop=False, purchases=((1200, "acme"),))
    ((method, path, params, headers),) = fake.requests
    assert (method, path) == ("POST", "/v1/payment_intents")
    assert params["capture_method"] == "manual" and params["confirm"] is True
    assert params["payment_method"] == "pm_card_visa" and params["amount"] == 1200
    assert params["automatic_payment_methods"] == {"enabled": True, "allow_redirects": "never"}
    assert headers["Idempotency-Key"] == "p4/unit/procurement_agent/po-0"
    assert a.pi_of("A000") == "pi_fake0000"


def test_create_replay_and_mismatch_come_from_stripe():
    a, fake = adapter(stop=False, purchases=())
    first = a.create_payment(AGENT, 1200, "acme", "po-1")
    again = a.create_payment(AGENT, 1200, "acme", "po-1")
    assert again.payment_id == first.payment_id == "A000" and again.replayed and not first.replayed
    assert a.create_payment(AGENT, 1300, "acme", "po-1").error == ERROR_IDEMPOTENCY_MISMATCH
    assert len(fake.pis) == 1 and len(fake.requests) == 3


def test_invalid_or_unauthorized_creates_never_reach_stripe():
    a, fake = adapter(stop=False, purchases=())
    assert a.create_payment(AGENT, 0, "acme", "k").error == "INVALID_REQUEST"
    assert a.create_payment(OP, 100, "acme", "k").result == DENIED
    assert fake.requests == []


def test_create_timeout_is_reported_and_the_retry_recovers_the_payment():
    a, fake = adapter(stop=False, purchases=())
    fake.lose_next = 1
    assert a.create_payment(AGENT, 900, "acme", "po-9").timeout
    r = a.create_payment(AGENT, 900, "acme", "po-9")
    assert r.payment_id == "A000" and r.replayed and len(fake.pis) == 1


def test_post_stop_calls_by_the_revoked_agent_are_denied_logged_and_never_sent():
    a, fake = adapter()
    n = len(fake.requests)
    a.clock.run_until(1)
    assert a.create_payment(AGENT, 100, "acme", "late").result == DENIED
    assert a.cancel(AGENT, "A000", "k").result == DENIED
    assert a.status(AGENT, "A000").error == DENIED
    assert len(fake.requests) == n
    assert [(e["type"], e["actor"], e["uid"], e["t"]) for e in a.log if e["type"] == "DENIED"] == [
        ("DENIED", AGENT, CREATE_UID, 1), ("DENIED", AGENT, "A000", 1), ("DENIED", AGENT, "A000", 1)]
    post = [c for c in a.calls if c["phase"] == "post_stop" and c["op"] != "incident_stop"]
    assert [(c["op"], c["result"] or c["error"]) for c in post] == [("create", DENIED), ("cancel", DENIED),
                                                                    ("status", DENIED)]


# -- adapter: the mapped transitions (same expectations as tests/conformance) --------------------------------
def test_cancel_in_requires_capture_then_capture_is_refused():
    a, fake = adapter()
    a.clock.run_until(2)
    r = a.cancel(OP, "A000", "c0")
    assert r.result == CANCELLED and a.log[r.effect_seq]["type"] == "CANCELLED"
    assert fake.sent("POST", "/cancel")[0][3]["Idempotency-Key"] == "p4/unit/remediation_operator/c0"
    a.clock.run_until(6)   # merchant captures both at D = 5
    assert types(a.log, "A000") == [("ACCEPTED", PENDING), ("CANCEL_REQ", CANCELLED), ("CANCELLED", CANCELLED)]
    assert types(a.log, "A001")[-1] == ("CAPTURED", CAPTURED)
    capture_obs = [o for o in a.observations if o.op == "capture" and o.uid == "A000"]
    assert [(o.http, o.error_code, o.pi_status) for o in capture_obs] == [(400, "payment_intent_unexpected_state", "canceled")]
    assert a.status(OP, "A000").state == CANCELLED and a.status(OP, "A001").state == CAPTURED
    assert a.anomalies == []


def test_cancel_after_capture_is_too_late_and_replays_say_so():
    a, _ = adapter()
    a.clock.run_until(5)
    r = a.cancel(OP, "A000", "c0")
    assert r.result == TOO_LATE_CAPTURED and a.log[r.effect_seq]["type"] == "CAPTURED"
    assert a.cancel(OP, "A000", "c0").result == TOO_LATE_CAPTURED
    assert types(a.log, "A000")[-2:] == [("CANCEL_REQ", TOO_LATE_CAPTURED), ("IDEMPOTENT_REPLAY", TOO_LATE_CAPTURED)]


def test_replay_mismatch_and_already_cancelled():
    a, fake = adapter()
    first = a.cancel(OP, "A000", "c0")
    replay = a.cancel(OP, "A000", "c0")
    assert replay.result == CANCELLED and replay.effect_seq == first.effect_seq
    assert a.log[replay.seq]["type"] == "IDEMPOTENT_REPLAY"
    mismatch = a.cancel(OP, "A001", "c0")       # same key, other payment (other endpoint path)
    assert mismatch.error == ERROR_IDEMPOTENCY_MISMATCH and a.log[mismatch.seq]["type"] == "MISMATCH"
    assert fake.pis[a.pi_of("A001")]["status"] == "requires_capture"
    assert a.cancel(OP, "A000", "c1").result == ALREADY_CANCELLED
    assert a.cancel(OP, "A999", "c2").error == ERROR_UNKNOWN_PAYMENT
    assert a.anomalies == []


def test_the_operator_is_denied_before_the_stop_and_on_unpermitted_payments():
    a, _ = adapter(stop=False)
    assert a.cancel(OP, "A000", "k").result == DENIED and a.status(OP, "A000").error == DENIED
    b, fake2 = adapter(rules=AgentPaymentRules(d_min=5, d_max=5, permitted=False))
    n = len(fake2.requests)
    assert b.cancel(OP, "A000", "k").result == DENIED and len(fake2.requests) == n


# -- faults ---------------------------------------------------------------------------------------------
def test_drop_resp_reaches_stripe_and_the_log_but_not_the_caller():
    a, fake = adapter(faults=FaultConfig("lost", f_resp=1.0))
    r = a.cancel(OP, "A000", "c0")
    assert r.timeout and fake.pis[a.pi_of("A000")]["status"] == "canceled"
    assert types(a.log, "A000")[-2:] == [("CANCEL_REQ", CANCELLED), ("CANCELLED", CANCELLED)]
    assert a.status(OP, "A000").timeout       # f_resp applies to status reads too
    assert [p.fault for p in a.proxy.trace] == ["drop_resp", "drop_resp"]


def test_drop_req_never_sends():
    a, fake = adapter(faults=FaultConfig("gone", f_req=1.0))
    n = len(fake.requests)
    assert a.cancel(OP, "A000", "c0").timeout and len(fake.requests) == n
    assert fake.pis[a.pi_of("A000")]["status"] == "requires_capture"
    assert types(a.log, "A000") == [("ACCEPTED", PENDING)]


def test_proxy_faults_are_the_simulators_realization():
    cfg = load_fault_presets()["high"]
    a, _ = adapter(faults=cfg, purchases=tuple((500 + i, "v") for i in range(6)))
    for uid in ("A000", "A001", "A002", "A003", "A004", "A005"):
        for attempt in range(3):
            a.cancel(OP, uid, "c")
    for rec in a.proxy.trace:
        d = fault_draw(a.seed, cfg, rec.op, rec.uid, rec.attempt)
        expected = "drop_req" if d.drop_req else "drop_resp" if d.drop_resp else "ok"
        if rec.fault in ("drop_req", "drop_resp", "ok"):
            assert rec.fault == expected, rec


def test_real_network_failure_is_reconciled_with_the_same_key():
    a, fake = adapter()
    fake.lose_next = 1
    r = a.cancel(OP, "A000", "c0")
    assert r.result == CANCELLED and a.log[r.seq]["type"] == "CANCEL_REQ"   # first response seen by the log
    keys = [h["Idempotency-Key"] for _, _, _, h in fake.sent("POST", "/cancel")]
    assert keys == ["p4/unit/remediation_operator/c0"] * 2 and a._recorder.reconciled == 1
    fake.fail_next = 10
    with pytest.raises(StripeUnavailable):
        a.cancel(OP, "A001", "c1")


# -- whole runs ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("policy", ["lm_ack", "lm_reconcile", "adaptive"])
def test_harness_episode_on_the_adapter_checks_clean(tmp_path, policy):
    a, _ = adapter(purchases=((1200, "acme"), (800, "shady"), (500, "globex")),
                      rules=AgentPaymentRules(d_min=4, d_max=9, flagged_vendors={"shady"}))
    ep = run_episode(a.scenario, policy, load_fault_presets()["none"], a.seed, 1, sink_factory=a.sink_factory)
    paths = export_run(ep, a.scenario, 0.5, tmp_path / "run")
    (verdict,) = check_runs([paths], [policy], tmp_path / "v.jsonl")
    assert verdict["ok"], verdict["problems"]
    assert verdict["counts"]["revoked_actor_effects"] == 0 and a.anomalies == []


def test_no_secret_reaches_any_export(tmp_path):
    a, _ = adapter()
    a.cancel(OP, "A000", "c0")
    a.clock.run_until(6)
    a.export_log(tmp_path / "events.jsonl")
    a.export_calls(tmp_path / "calls.jsonl")
    a.export_observations(tmp_path / "obs.jsonl")
    text = "".join(p.read_text() for p in tmp_path.iterdir())
    assert "secret" not in text and "sk_" not in text and "pi_fake0000" in text


def test_attach_only_before_any_call():
    a, _ = adapter()
    a.attach(EventClock())
    a.cancel(OP, "A000", "c0")
    with pytest.raises(RuntimeError):
        a.attach(EventClock())


class BlockingStripe(FakeStripe):
    """Holds the first create inside the HTTP call until `release` is set."""

    def __init__(self):
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def request(self, method, path, params=None, headers=None):
        if (method, path) == ("POST", "/v1/payment_intents") and not self.release.is_set():
            self.entered.set()
            assert self.release.wait(5)
        return super().request(method, path, params, headers)


def test_create_in_flight_at_the_stop_finishes_first_and_is_in_the_scenario():
    fake = BlockingStripe()
    a = StripeTestProcessor(fake, run_id="unit", seed=100777, rules=RULES, sleep=lambda s: None)
    out = {}
    creator = threading.Thread(target=lambda: out.update(create=a.create_payment(AGENT, 1200, "acme", "po-0")))
    stopper = threading.Thread(target=lambda: out.update(stop=a.incident_stop(AGENT)))
    creator.start()
    assert fake.entered.wait(5)
    stopper.start()
    stopper.join(0.1)
    assert stopper.is_alive()   # the stop waits for the create already at Stripe
    fake.release.set()
    creator.join(5)
    stopper.join(5)
    assert out["create"].payment_id == "A000" and out["stop"].pending == ("A000",)
    assert [p.uid for p in a.scenario.payments] == ["A000"]
    n = len(fake.requests)
    assert a.create_payment(AGENT, 300, "acme", "po-1").result == DENIED and len(fake.requests) == n


def test_stop_clock_keeps_calls_made_before_the_episode(tmp_path):
    a, _ = adapter()
    assert a.create_payment(AGENT, 400, "acme", "po-late").result == DENIED   # after the stop, before the episode
    seq = a.calls[-1]["seq"]
    assert a.log[seq]["type"] == "DENIED"
    with pytest.raises(RuntimeError):   # a fresh clock would discard that line
        run_episode(a.scenario, "lm_reconcile", load_fault_presets()["none"], a.seed, 1, sink_factory=a.sink_factory)
    ep = run_episode(a.scenario, "lm_reconcile", load_fault_presets()["none"], a.seed, 1,
                     clock=a.clock, sink_factory=a.sink_factory)
    assert ep.log[seq]["type"] == "DENIED" and ep.log[seq]["actor"] == AGENT
    paths = export_run(ep, a.scenario, 0.5, tmp_path / "run")
    (verdict,) = check_runs([paths], ["lm_reconcile"], tmp_path / "v.jsonl")
    assert verdict["ok"], verdict["problems"]
    assert verdict["counts"]["revoked_actor_effects"] == 0


def test_wall_clock_episode_captures_at_wall_time(tmp_path):
    ft = FakeTime()
    captures = []

    class TimedStripe(FakeStripe):
        def request(self, method, path, params=None, headers=None):
            if path.endswith("/capture"):
                captures.append((path.split("/")[3], ft.t))
            return super().request(method, path, params, headers)

    a = StripeTestProcessor(TimedStripe(), run_id="unit", seed=100777, sleep=lambda s: None,
                            rules=AgentPaymentRules(d_min=4, d_max=9, flagged_vendors={"shady"}),
                            clock_factory=lambda: WallClockDriver(0.5, monotonic=ft.monotonic, sleep=ft.sleep))
    for i, (amount, vendor) in enumerate(((1200, "acme"), (800, "shady"), (500, "globex"))):
        a.create_payment(AGENT, amount, vendor, f"po-{i}")
    a.incident_stop(AGENT)
    ep = run_episode(a.scenario, "adaptive", load_fault_presets()["none"], a.seed, 1,
                     clock=a.clock, sink_factory=a.sink_factory)
    due = {a.pi_of(p.uid): 0.5 * p.D_actual for p in a.scenario.payments}
    assert captures and all(t == due[pi] for pi, t in captures)
    assert ft.t == 0.5 * max(ep.end_of_run, a.scenario.horizon) and a.clock.max_lag == 0
    paths = export_run(ep, a.scenario, 0.5, tmp_path / "run")
    (verdict,) = check_runs([paths], ["adaptive"], tmp_path / "v.jsonl")
    assert verdict["ok"], verdict["problems"]


def test_http_response_helpers():
    r = HttpResponse(400, {}, {"error": "not a dict"})
    assert r.error == {} and not r.replayed
