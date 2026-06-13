"""
CodeRifts guard decorator
==========================

Collapses the agent-loop guard into one line. Decorate any tool function (or
LangGraph / AutoGen / LangChain node) with @coderifts_guard(old_spec, new_spec).
Before the wrapped function runs, the decorator diffs the old vs new API contract
against the zero-auth CodeRifts endpoint and halts the agent before an unsafe call.
The verdict is cached per spec pair (the contract does not change between calls),
so the endpoint is hit once, not on every invocation.

Decision semantics for agents
------------------------------
    BLOCK             Breaking contract change. ALWAYS halts (raises CodeRiftsBlocked).
                      Every breaking change resolves to BLOCK, so this is the reliable
                      signal an agent keys off.
    REQUIRE_APPROVAL  Flagged for human review: not a hard break, but not auto-safe
                      either (e.g. a dangerous-but-nonbreaking change). Proceeds by
                      default; halts when the guard runs in strict mode.
    WARN / ALLOW      Proceeds.

Use strict=True for human-in-the-loop agents that must not auto-proceed on anything
CodeRifts flags. Leave strict=False (default) to halt only on genuine breaks.

Zero extra dependencies (standard library only). No API key required.

    python coderifts_decorator.py
"""

import functools
import json
import urllib.request
import urllib.error

CODERIFTS_DEMO_URL = "https://app.coderifts.com/api/v1/demo"

_VERDICT_CACHE = {}


class CodeRiftsBlocked(Exception):
    """Raised by @coderifts_guard to halt the agent before an unsafe call.

    Raised on BLOCK always, and on REQUIRE_APPROVAL when the guard runs in strict
    mode. Inspect .verdict for the full decision object and .decision for the
    decision string ('BLOCK' or 'REQUIRE_APPROVAL').
    """

    def __init__(self, verdict):
        self.verdict = verdict
        self.decision = _decision(verdict)
        patterns = _pattern_names(verdict)
        super().__init__(f"CodeRifts {self.decision} (patterns=[{patterns}])")


def _decision(verdict):
    return verdict.get("omega_decision") or verdict.get("decision") or "UNKNOWN"


def _pattern_names(verdict):
    return ", ".join(
        p.get("name") or p.get("type", "") for p in verdict.get("detected_patterns", [])
    ) or "none"


def _call_coderifts(old_spec, new_spec):
    payload = json.dumps({"old_spec": old_spec, "new_spec": new_spec}).encode()
    req = urllib.request.Request(
        CODERIFTS_DEMO_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"CodeRifts returned HTTP {e.code}: {body}") from None


def _verdict(old_spec, new_spec):
    key = json.dumps([old_spec, new_spec], sort_keys=True)
    if key not in _VERDICT_CACHE:
        _VERDICT_CACHE[key] = _call_coderifts(old_spec, new_spec)
    return _VERDICT_CACHE[key]


def coderifts_guard(old_spec, new_spec, strict=False):
    """Decorator. Diff old vs new spec via CodeRifts before running the wrapped
    function, and halt the agent before an unsafe call.

    strict=False (default): halt on BLOCK only. Since every breaking change
        resolves to BLOCK, this catches all genuine contract breaks.
    strict=True: also halt on REQUIRE_APPROVAL, so nothing CodeRifts flags is
        auto-executed. For human-in-the-loop / max-caution agents.

    See the module docstring for the full decision semantics.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            verdict = _verdict(old_spec, new_spec)
            decision = _decision(verdict)
            blocked = bool(verdict.get("should_block")) or decision == "BLOCK"
            halt = blocked or (strict and decision == "REQUIRE_APPROVAL")
            print(
                f"[coderifts_guard] {fn.__name__}: decision={decision} "
                f"should_block={verdict.get('should_block')} strict={strict} "
                f"patterns=[{_pattern_names(verdict)}]"
            )
            if halt:
                reason = "BLOCK" if blocked else "REQUIRE_APPROVAL (strict)"
                print(f"[coderifts_guard] {reason} -> {fn.__name__} not called, agent halted")
                raise CodeRiftsBlocked(verdict)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# --- Demo 1: a breaking change. The response field `order_status` -> `status`.
OLD_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Orders API", "version": "1.0.0"},
    "paths": {"/orders/{id}": {"get": {"responses": {"200": {"description": "ok",
        "content": {"application/json": {"schema": {"type": "object",
            "properties": {"order_status": {"type": "string"}}}}}}}}}},
}
NEW_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Orders API", "version": "1.0.0"},
    "paths": {"/orders/{id}": {"get": {"responses": {"200": {"description": "ok",
        "content": {"application/json": {"schema": {"type": "object",
            "properties": {"status": {"type": "string"}}}}}}}}}},
}


@coderifts_guard(OLD_SPEC, NEW_SPEC)
def get_order_status(order_id):
    # The real tool call. Only runs when CodeRifts clears the contract change.
    return f"status for {order_id}"


# --- Demo 2: a safe, additive change (new optional field). Default guard proceeds;
# the same change halts under strict=True, since it returns REQUIRE_APPROVAL.
SAFE_OLD = {
    "openapi": "3.0.0",
    "info": {"title": "Orders API", "version": "1.0.0"},
    "paths": {"/orders": {"get": {"responses": {"200": {"description": "ok",
        "content": {"application/json": {"schema": {"type": "object",
            "properties": {"id": {"type": "string"}}}}}}}}}},
}
SAFE_NEW = {
    "openapi": "3.0.0",
    "info": {"title": "Orders API", "version": "1.0.0"},
    "paths": {"/orders": {"get": {"responses": {"200": {"description": "ok",
        "content": {"application/json": {"schema": {"type": "object",
            "properties": {"id": {"type": "string"},
                           "note": {"type": "string"}}}}}}}}}},
}


@coderifts_guard(SAFE_OLD, SAFE_NEW, strict=True)
def list_orders():
    return "orders list"


if __name__ == "__main__":
    print("Demo 1 - breaking change (halts in any mode):")
    try:
        print("result:", get_order_status("order-123"))
    except CodeRiftsBlocked as e:
        print("aborted:", e)

    print("\nDemo 2 - safe additive change under strict=True:")
    try:
        print("result:", list_orders())
    except CodeRiftsBlocked as e:
        print("aborted:", e, "(strict mode halts on REQUIRE_APPROVAL)")
