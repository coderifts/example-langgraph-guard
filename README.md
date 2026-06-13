# CodeRifts LangGraph Guard

A minimal [LangGraph](https://github.com/langchain-ai/langgraph) guard node that wires the
CodeRifts verdict directly into the agent loop.

Before the agent acts on a tool whose API contract may have drifted, the guard node diffs
the tool's old vs new spec against the zero-auth CodeRifts endpoint
(`POST https://app.coderifts.com/api/v1/demo`). On `BLOCK` the graph routes to `abort` and
the unsafe call never runs; on a safe verdict it proceeds to `execute`. No API key required.

## Run

```
pip install langgraph
python coderifts_langgraph_guard.py
```

On Python 3.9, pin the compatible release: `pip install "langgraph==0.6.11"`.

## Expected output

```
[guard] get_order_status: decision=BLOCK should_block=True breaking_changes=1 patterns=[FIELD_REMOVAL]
[abort] CodeRifts BLOCK -> get_order_status not called, agent halted
final: aborted get_order_status (CodeRifts BLOCK)
```

## What it shows

The sample before/after pair renames the response field `order_status` to `status`.
CodeRifts flags `FIELD_REMOVAL` (HIGH) and the `TOOL_RESULT_SHAPE_DRIFT` reflex escalates the
decision to `BLOCK` even though the raw risk score is low, so the agent halts before calling
the tool against the now-incompatible contract.

Swap `OLD_SPEC` / `NEW_SPEC` for your own before/after pair to watch the verdict shift.

## How the guard node reads the verdict

The guard treats the structured verdict as the source of truth: it blocks when
`should_block` is true or `omega_decision == "BLOCK"`, and surfaces the detected patterns
(e.g. `FIELD_REMOVAL`) and the reflex triggers that drove the decision.
