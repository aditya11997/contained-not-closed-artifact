"""An in-memory HttpClient that follows Stripe's *documented* PaymentIntent and idempotency
behaviour (docs/STRIPE_MAPPING.md, "documented" column). For offline unit tests of the
adapter only; it is a model of the docs, never evidence of what Stripe does."""
import json

from p4.adapters.stripe_test import HttpResponse, TransportError


class FakeStripe:
    def __init__(self):
        self.pis: dict[str, dict] = {}
        self.keys: dict[str, tuple[str, str, str, HttpResponse]] = {}
        self.requests: list[tuple[str, str, dict | None, dict]] = []
        self.fail_next = 0          # raise TransportError before reaching "Stripe"
        self.lose_next = 0          # execute, then raise TransportError (lost response)
        self.rate_limit_next = 0    # answer 429 without executing

    def request(self, method, path, params=None, headers=None):
        headers = dict(headers or {})
        self.requests.append((method, path, params, headers))
        if self.fail_next:
            self.fail_next -= 1
            raise TransportError("ConnectionResetError")
        if self.rate_limit_next:
            self.rate_limit_next -= 1
            return HttpResponse(429, {"stripe-rate-limited-reason": "global-rate"},
                                {"error": {"type": "invalid_request_error", "code": "rate_limit"}})
        key = headers.get("Idempotency-Key")
        fp = json.dumps(params, sort_keys=True)
        if method == "POST" and key in self.keys:
            m, p, f, stored = self.keys[key]
            if (m, p, f) != (method, path, fp):
                return HttpResponse(400, {}, {"error": {"type": "idempotency_error",
                                                        "message": "Keys for idempotent requests can only be used with the same parameters"}})
            return HttpResponse(stored.status, {**stored.headers, "idempotent-replayed": "true"}, stored.body)
        resp = self._execute(method, path, params)
        if method == "POST" and key is not None and resp.status != 429:
            self.keys[key] = (method, path, fp, resp)
        if self.lose_next:
            self.lose_next -= 1
            raise TransportError("TimeoutError")
        return resp

    # -- endpoints --------------------------------------------------------------------------
    def _execute(self, method, path, params):
        parts = path.strip("/").split("/")
        if method == "POST" and parts == ["v1", "payment_intents"]:
            pi = f"pi_fake{len(self.pis):04d}"
            self.pis[pi] = {"id": pi, "object": "payment_intent", "amount": params["amount"],
                            "status": "requires_capture", "capture_method": "manual",
                            "client_secret": f"{pi}_secret_DO_NOT_STORE", "livemode": False}
            return self._ok(pi)
        pi = parts[2] if len(parts) >= 3 else None
        if pi not in self.pis:
            return HttpResponse(404, {}, {"error": {"type": "invalid_request_error", "code": "resource_missing"}})
        obj = self.pis[pi]
        if method == "GET":
            return self._ok(pi)
        action = parts[3]
        if obj["status"] != "requires_capture":
            return HttpResponse(400, {"request-id": "req_fake"}, {"error": {
                "type": "invalid_request_error", "code": "payment_intent_unexpected_state",
                "payment_intent": dict(obj)}})
        obj["status"] = "canceled" if action == "cancel" else "succeeded"
        return self._ok(pi)

    def _ok(self, pi):
        return HttpResponse(200, {"request-id": "req_fake"}, dict(self.pis[pi]))

    def sent(self, method=None, suffix=None):
        return [r for r in self.requests
                if (method is None or r[0] == method) and (suffix is None or r[1].endswith(suffix))]
