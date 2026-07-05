# Design: Daily Reflection via Conversation Continuation

## Problem

Current `daily_reflect.py` drops an inbox file → scheduler creates a new task → solver runs a **separate** LLM session. This means the daily reflection has no context of what just happened — it's a cold start every time.

The desired behavior: after `run_agent()` finishes solving a task, send **one more user message** on the **same conversation** asking the model to reflect. The model sees the full task-solving history and can produce meaningful introspection.

## Call Chain (current)

```
main.py: Agent.tick() → _execute_task(tid)
  → executor.run(solver.solve(...))
    → solver.solve() → run_session() → run_agent() → returns AssistantMessage
  → scheduler.mark_done(tid, result)
  → _maybe_post_task_reflect(tid, requirement)   # drops inbox file (separate task)

main.py: Agent.tick() → _maybe_daily_reflect()
  → daily_reflect.maybe_drop()                   # drops inbox file (separate task)
```

## Design Decisions

### 1. `run_agent` return value

**Option A:** Add `messages` field to `AssistantMessage` pydantic model  
→ Reject: pollutes the message schema; `AssistantMessage` represents a single chat message, not session state.

**Option B:** Return `Tuple[AssistantMessage, list[ChatMessage]]`  
→ Works but fragile — positional tuple is easy to misuse at call sites.

**Option C:** Dedicated result dataclass `AgentResult(message, messages)`  
→ **Chosen.** Explicit fields, self-documenting, backward-compatible via `.message`.

### 2. Where does the reflection prompt get injected?

**Option A:** Inside `run_session()` — add a `reflect_prompt` parameter  
→ Cleanest for callers. `run_session` already owns the LLM client and hooks; it can send one more turn.

**Option B:** Inside `run_agent()` — add a `post_hook` callback  
→ Too low-level; reflection is an app-layer concern, not agent-loop logic.

**Option C:** In `_execute_task()` directly — manually call `llm.chat()` after `run_session()` returns  
→ Reject: leaks LLM call details into main.py; duplicates session setup (hooks, logging).

**Decision:** **Option A** — `run_session(reflect_prompt=...)` sends one extra turn if the prompt is provided. The reflection uses no tools (text-only response), so it's a simple single-turn `llm.chat()` call on the accumulated messages + one user message.

### 3. Daily vs Post-task reflection

Both are "send one more message after task completion" but with different triggers:

- **Post-task reflection** (`_maybe_post_task_reflect`): triggered after every task, lightweight prompt about what just happened
- **Daily reflection**: triggered by time threshold, heavier audit prompt

They share the same mechanism. The difference is only which prompt to use.

**Proposal:** Merge them into one `reflect_prompt` parameter on `run_session`. The caller decides whether to pass a prompt based on:
- Always pass post-task reflection prompt (lightweight)
- Pass daily reflection prompt if time threshold met (can be concatenated with post-task)

### 4. What about the SKILL.md approach?

The current `skills/daily-reflect/SKILL.md` is a detailed audit checklist designed for a full agent session with tools. For conversation-continuation reflection, we don't need tool access — just ask the model to reflect on what it did. The skill can remain as a fallback inbox task if needed, but the primary path becomes inline.

## Proposed Changes Summary

| File | Change |
|------|--------|
| `sdk/agent_result.py` | New: `AgentResult(message, messages)` dataclass |
| `sdk/agent.py` | `run_agent()` returns `AgentResult` instead of `AssistantMessage` |
| `app/session.py` | `run_session()` unpacks `AgentResult`; add optional `reflect_prompt` param that sends one more LLM turn; return `(output, reflect_output)` tuple or just `output` if no reflection |
| `app/solver.py` | `solve()` passes through new return shape |
| `app/hotfixer.py` | `fix()` passes through new return shape |
| `app/daily_reflect.py` | Simplify: remove inbox-file dropping; add `build_prompt(last_task_requirement, last_task_result)` function that returns the reflection prompt text |
| `app/main.py` | `_execute_task()`: decide whether to reflect based on time threshold; pass `reflect_prompt` to solver; handle reflection output (e.g. write to memory/journal) |

## Reflection Output Handling

The reflection response is text (no tools). It should be:
1. Appended to the task's journal or a dedicated `reflection.md` in the task directory
2. Or written to `memory/` as a journal entry

Simplest: append to `task/<tid>/reflection.md` alongside `result.md`.

## Open Questions

1. Should daily reflection replace post-task reflection, or can both fire on the same task? → Both, concatenated into one prompt.
2. What if the task just failed/errored? → Still reflect — that's valuable.
3. Context window overflow? → The accumulated messages already go through compaction hooks in `run_agent`; for the extra turn we rely on the same context window limit. If it overflows, the LLM will truncate naturally.
