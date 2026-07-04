# Compaction Mode — Design Document

## Overview

When the agent loop's context approaches the window limit, compaction mode is triggered: the framework injects a user message instructing the model to organize its memory, and passes a `{summary: string}` business schema as the `result` field of the merged schema in `llm.py`. The model uses existing tools (`bash`, `read`, `write`, `edit`) to operate on files under `memory/`, then returns the summary via the merged schema's `result` (no tool calls). After exit, old messages are replaced with a single summary message and the normal loop continues.

## Core Design Decisions

| Item | Decision |
|------|----------|
| Enter mode | Framework behavior (no tool), auto-injected when token/msg threshold hit |
| Exit mode | Merged schema `result` field with schema `{summary: string}` |
| Tools | Unchanged (`bash`, `read`, `write`, `edit`) |
| System prompt | Unchanged — mode instruction appended as a user message |
| Response format | `{summary: string}` schema during compaction; `null` during normal operation |
| Message pruning | No framework-side cut point — the model organizes on its own; all old messages replaced with one summary on exit |
| Memory path | `memory/` (under config.home, not sandbox/memory) |

## Flow

```
Normal loop: system + [user requirement] + assistant/tool exchanges...
             (no response_format, tools=[bash,read,write,edit])
    ↓ should_compact() triggers
Inject user message: "context approaching limit, organize memory/..."
response_format = {summary: string} schema
    ↓ loop continues (tools unchanged, merged schema active)
Model: read/write memory files → result({summary: "..."})
    ↓ no tool_calls, content is JSON {"summary": "..."}
msgs = system + [user COMPACTED summary]          ← all old messages replaced
response_format = null                            ← restored
_last_compaction_msg_count = len(msgs)
    ↓ loop continues normally
```

## Files and Changes

### 1. `sdk/agent.py` — Rewrite compaction branch (core change)

**Remove existing logic:**
- `find_cut_point`, `summarize`, `extract_file_paths`, `format_file_note` call chain
- `_previous_summary`, `_last_compaction_msg_count` incremental merge logic
- Unused imports: `from sdk.compaction import find_cut_point, extract_file_paths, format_file_note, summarize`

**New logic:**

Compaction mode works by **switching response_format**:

```python
# ── Context compaction ──
if should_compact(...):
    logger.info(f"[compaction] triggered ({_reason})")

    # 1. Already in compaction mode — continue loop
    if _in_compaction_mode:
        continue

    # 2. Enter compaction mode: inject instruction + set response_format
    _in_compaction_mode = True
    msgs.append(ChatMessage(role="user",
                            content=_COMPACT_INSTRUCTION.format(
                                memory_dir=str(config.memory_dir))))
    _compact_response_format = ResponseFormat(
        type="json_schema",
        json_schema=JsonSchema(
            name="compaction_result",
            strict=True,
            schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string",
                                 "description": "Concise summary of session progress, decisions made, and next steps."},
                },
                "required": ["summary"],
                "additionalProperties": False,
            }
        )
    )
```

**During compaction mode, pass `_compact_response_format` to `client.chat()`:**

```python
resp = client.chat(
    msgs,
    temperature=temperature,
    max_tokens=_clamped_max,
    tools=tools if tools else None,
    response_format=_compact_response_format if _in_compaction_mode else _response_format,
)
```

**When the model returns result (no tool_calls), detect compaction mode:**

```python
if not tcs:
    content = _strip_think(message.content or "")
    if _in_compaction_mode:
        # Compaction mode exit — extract summary from JSON result
        _summary = json.loads(content).get("summary", content)
        _in_compaction_mode = False

        summary_msg = ChatMessage(role="user", content=(
            f"[COMPACTED HISTORY]\n{_summary}\n\nContinue working from where you left off."
        ))
        msgs = msgs[:_initial_len] + [summary_msg]
        _last_compaction_msg_count = len(msgs)
        logger.info(f"[compaction] done, summary={len(_summary)} chars, msgs now={len(msgs)}")
        continue  # resume normal loop

    # Normal session exit
    return { ... }
```

### 2. `_COMPACT_INSTRUCTION` template

```python
_COMPACT_INSTRUCTION = """\
[CONTEXT WINDOW ALERT] Your context is approaching the limit.

Please organize your working memory:
1. Read your current memory files under `{memory_dir}/` (INDEX.md, identity.md, goals/, issues/, journal/)
2. Update them with what you've learned and accomplished so far
3. When done, return a concise summary of the session's progress

After this, your conversation history will be replaced with just your summary."""
```

Placed as a plain template string in `sdk/agent.py`, formatted with `{memory_dir}` at injection time — keeps the sdk layer independent of app/config.

### 3. `app/config.py` — Add `memory_dir` property, update `runtime_dirs`

```python
@property
def memory_dir(self) -> Path:
    return self.home / "memory"
```

Add `memory_dir` to `runtime_dirs` so boot.py's `ensure_dir` loop creates it automatically.

### 4. `skills/memory/SKILL.md` — Paths from `sandbox/memory/` to `memory/`

All references to `sandbox/memory/` changed to `memory/`.

### 5. `skills/self-reflect/SKILL.md` — Same memory path update

### 6. `sdk/compaction.py` — Slimmed down

Removed all dead code:
- `find_cut_point`, `summarize`, `extract_file_paths`, `format_file_note`
- `serialize_conversation`, `COMPACT_SYSTEM/PROMPT/UPDATE_PROMPT`
- `compact_step`, `get_last_summary`, `ChatClient` interface
- `keep_recent_tokens`, `summarize_max_tokens` settings fields

**Retained (still used):**
- `CompactionSettings` — `enabled`, `reserve_tokens`, `compact_at`, `cooldown_messages`
- `estimate_tokens`, `estimate_context_tokens`, `clamp_max_tokens`
- `should_compact`

## Edge Cases

### 1. Repeated compaction triggers

`should_compact` has a `cooldown_messages` mechanism. After exit, `_last_compaction_msg_count = len(msgs)`. Since msgs contain only system + 1 summary message, msg count is small and cooldown activates naturally.

### 2. Empty or very short summary in `end_compaction`

If the model returns `{summary: ""}` or a very short summary (<20 chars), inject a user message asking the model to provide a meaningful summary — do not exit compaction mode.

### 3. Tool errors during compaction mode

Handled normally — tool result carries error flag, the model decides whether to retry or return the result. The model follows memory skill guidance on how to organize files.

## Data Flow Diagram

```
┌─────────────┐     should_compact()      ┌──────────────────┐
│  agent loop │ ───────────────────────▶  │ inject user msg  │
│  (normal)   │                           │ "organize memory"│
│             │                           │ response_format = │
│             │                           │ {summary: string} │
└─────────────┘                           └────────┬─────────┘
                                                   │
              ◀────────────────────────────────────┘
              loop continues with merged schema active

┌─────────────┐     loop continues        ┌──────────────────┐
│  agent loop │ ◀──────────────────────  │ model: read/write│
│(compaction) │                           │ memory files     │
└──────┬──────┘                           └────────┬─────────┘
       │                                           │
       │              result({summary})            │
       │              (no tool_calls)              │
       ◀───────────────────────────────────────────┘

┌─────────────┐     extract summary       ┌──────────────────┐
│  agent loop │ ───────────────────────▶  │ msgs = system    │
│  (normal)   │                           │ + [COMPACTED]    │
│             │                           │ response_format  │
│             │                           │ restored to null │
└─────────────┘                           └──────────────────┘
```

## Relationship with merged schema in `llm.py`

**No changes.** The merged schema (`_build_schema`, `_parse_response`) remains unchanged. It is used when both tools and response_format are passed — the `result` field is still the carrier for business schemas.

During compaction mode, we pass a `{summary: string}` schema as the business schema. The model can choose to call tools or return the result directly. This is standard merged-schema behavior — no new code needed in `llm.py`.

## summary vs memory skill — Differences and Relationship

These two serve different purposes:

| | compaction summary | memory skill |
|--|-------------------|--------------|
| **Output** | A message text, inserted back into conversation history | Files under `memory/` (persistent) |
| **Purpose** | Free context space; let the model know "what was done" | Cross-session persistence of identity, goals, issues, etc. |
| **Trigger** | Framework forced (token/msg approaching limit) | Model autonomous or self-reflect cycle |
| **Content focus** | Current conversation progress summary | Long-term memory organization (identity, goal tracking, journaling) |

The compaction mode instruction asks the model to do two things:
1. **Update memory files** — persist important information from this session (optional but recommended)
2. **Produce a summary** — serve as the compacted conversation history, containing task progress, key decisions, and next steps

The model in compaction mode can freely choose its focus: if memory files are already up-to-date, it can produce the summary directly; if it needs to organize memory first, it can do so. The `result` parameter of merged schema is the final summary text that gets inserted back into messages.

## Change Checklist (in dependency order)

1. `app/config.py` — Add `memory_dir` property, update `runtime_dirs`
2. `sdk/agent.py` — Rewrite compaction branch (core change)
3. `skills/memory/SKILL.md` — Paths `sandbox/memory/` → `memory/`
4. `skills/self-reflect/SKILL.md` — Same memory path update

## No Changes

- `sdk/llm.py` — merged schema unchanged
- `sdk/schemas.py` — no new schemas needed (ResponseFormat + JsonSchema already exist)
- `sdk/tools.py` — no new tools added
- `app/session.py` — no changes (run_agent signature unchanged)
- `app/solver.py`, `app/hotfixer.py` — no changes
- `app/main.py` — no changes
- `app/boot.py` — runtime_dirs automatically includes memory_dir, no explicit change needed
