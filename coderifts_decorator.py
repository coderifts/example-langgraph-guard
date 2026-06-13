"""
CodeRifts guard decorator
==========================

Collapses the agent-loop guard into one line. Decorate any tool function (or
LangGraph / AutoGen node) with @coderifts_guard(old_spec, new_spec). Before the
wrapped function runs, the decorator diffs the old vs new API contract against the
zero-auth CodeRifts endpoint. On BLOCK it raises CodeRiftsBlocked, so the unsafe
call never executes. The verdict is cached per spec pair (the contract does not
change between calls), so the endpoint is hit once, not on every invocation.

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
    """Raised by @coderifts_guard when CodeRifts blocks the contract change."""

    def __init__(self, verdict):
        self.verdict = verdict
        patterns = _pattern_names(verdict)
        super().__init__(f"CodeRifts BLOCK (patterns=[{patterns}])")


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


def coderifts_guard(old_spec, new_spec):
    """Decorator. Diff old vs new spec via CodeRifts before running the wrapped
    function; raise CodeRiftsBlocked on BLOCK so the unsafe call never runs."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            verdict = _verdict(old_spec, new_spec)
            blocked = bool(verdict.get("should_block")) or verdict.get("omega_decision") == "BLOCK"
            print(
                f"[coderifts_guard] {fn.__name__}: decision={verdict.get('omega_decision')} "
                f"should_block={verdict.get('should_block')} patterns=[{_pattern_names(verdict)}]"
            )
            if blocked:
                print(f"[coderifts_guard] BLOCK -> {fn.__name__} not called, agent halted")
                raise CodeRiftsBlocked(verdict)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# A breaking change: the response field `order_status` is renamed to `status`.
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


if __name__ == "__main__":
    try:
        result = get_order_status("order-123")
        print("result:", result)
    except CodeRiftsBlocked as e:
        print("aborted:", e)
