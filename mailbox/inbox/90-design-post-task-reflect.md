# Design: Post-Task Reflection via `after_llm_call` Hook

## Problem

Current `_maybe_post_task_reflect()` drops an inbox file → new task → cold-start LLM session. The reflection has no context of what just happened.

Desired: after the agent loop finishes normally, continue it with one more turn so the model reflects on the full conversation — using the same hooks, compaction, and environment.

## Pi's Hook Model (Reference)

Pi's agent lifecycle provides these relevant hook points:

| Phase | Pi event/hook | What it can do |
|-------|--------------|----------------|
| Before agent starts | `before_agent_start` | Inject message, modify system prompt |
| Before each LLM call | `context` | Modify messages (compaction, pruning) |
| Before tool executes | `tool_call` | **Block** or modify args |
| After tool result | `tool_result` | Modify result content |
| Turn boundary | `turn_start` / `turn_end` | Observer only |
| Agent finished | `agent_end` | Observer only |
| Continue after finish | `followUp()` | Queue user message delivered when agent stops → loop continues |

Pi's approach to "continue after agent finishes" is `followUp()` — a message queue mechanism, not a hook. The queued message is appended when the agent has no more tool calls, causing the loop to run another turn.

## NYX's Current Hook Model

| Phase | NYX hook | Status |
|-------|----------|--------|
| Before each LLM call | `before_llm_call` | ✅ Active — used by compaction, pruning |
| After LLM response | `after_llm_call` | ❌ **Defined but never called** — dead code |
| Before tool executes | `before_tool_call` | ✅ Active — used by repetitive guard |
| After tool result | `after_tool_call` | ✅ Active — used for terminate batch |
| Turn boundary | `_emit("turn_start")` / `_emit("turn_end")` | Partial — only `on_event`, no hook callback |
| Agent finished | - | **Missing** |

## Issues with Current `run_agent`

1. **`after_llm_call` is dead code** — defined in Protocol and CompositeHooks but never invoked in `run_agent`
2. **No "continue after finish" mechanism** — pi has `followUp()`, NYX needs something similar
3. **Exit path asymmetry** — assistant messages with tool calls are appended to `msgs`, but the final text-only assistant message is NOT appended before returning
4. **No `agent_end` equivalent** — no hook point when the agent loop finishes

## Solution: Activate `after_llm_call` + extend it for flow control

### 1. Call `after_llm_call` in `run_agent` after every LLM response

Place it right after getting the message from the LLM, BEFORE the tool-calls check:

```python
message = resp.choices[0].message

# ── after_llm_call hook (after each LLM response) ──
r = hooks.after_llm_call(message.model_dump(), ctx)
if r is not None:
    message = ChatResponseMessage(**r)
```

### 2. Extend `AfterLlmCallResult` with flow control

Currently `after_llm_call` returns `Optional[Dict[str, Any]]` — too loose. Define a proper result type:

```python
@dataclass(frozen=True)
class AfterLlmCallResult:
    """Return from after_llm_call to modify the message or control flow."""
    message: Optional[Dict[str, Any]] = None       # replace message fields (original behavior)
    continue_loop: bool = False                     # if True, don't exit even with no tool calls
    messages_to_append: List[ChatMessage] = None   # append these before continuing
```

### 3. Use it in the exit path

In `run_agent`'s "No tool calls → exit" block:

```python
if not tool_calls:
    content = _strip_think(message.content or "")

    # Check if after_llm_call requested to continue
    if _continue_after_turn:
        msgs.append(ChatMessage(role=message.role, content=message.content))
        if _append_msgs:
            msgs.extend(_append_msgs)
        _continue_after_turn = False
        _append_msgs = None
        continue  # run another iteration

    _emit("turn_end", {"content": content})
    return AssistantMessage(content=content), msgs
```

The `_continue_after_turn` and `_append_msgs` are set by the `after_llm_call` hook handler.

### 4. Return accumulated messages

Change return type to `Tuple[AssistantMessage, List[ChatMessage]]` (or `AgentResult` dataclass) so callers can access the full conversation if needed.

### 5. Reflection Hook (app layer)

```python
class PostTaskReflectHook:
    def __init__(self, prompt: str):
        self._prompt = prompt
        self._used = False

    def after_llm_call(self, message: Dict[str, Any], ctx: HookContext):
        if self._used:
            return None
        # Only trigger on first text-only turn (no tool calls)
        if not message.get("tool_calls"):
            self._used = True
            return AfterLlmCallResult(
                continue_loop=True,
                messages_to_append=[
                    ChatMessage(role="user", content=self._prompt),
                ],
            )
        return None
```

## Changes Summary

| File | Change |
|------|--------|
| `sdk/agent_hooks.py` | Add `AfterLlmCallResult` dataclass; change `after_llm_call` return type from `Optional[Dict]` to `Optional[AfterLlmCallResult]`; update CompositeHooks chaining |
| `sdk/agent.py` | Call `hooks.after_llm_call()` after each LLM response; if result has `continue_loop=True`, append assistant msg + hook's messages and `continue` loop; return `(AssistantMessage, msgs)` tuple |
| `app/hooks/post_task_reflect.py` | New: `PostTaskReflectHook` implementing `after_llm_call` |
| `app/hooks/__init__.py` | Export `PostTaskReflectHook` |
| `app/session.py` | Unpack new return from `run_agent`; add optional `reflect_prompt` param; if set, create `PostTaskReflectHook(prompt)` and add to hooks chain |
| `app/solver.py` | Accept optional `reflect_prompt`, pass through to `run_session()` |
| `app/main.py` | `_execute_task()`: build reflection prompt from task-reflect SKILL.md, pass to solver; handle reflection output |
| `app/daily_reflect.py` | **No changes** — daily reflection via inbox is correct |

## Backward Compatibility

Existing hooks that implement `after_llm_call` returning `Optional[Dict]` need migration. Currently no one uses it (confirmed by grep), so this is a clean break.
