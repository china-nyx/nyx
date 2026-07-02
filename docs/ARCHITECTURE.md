# NYX Architecture — Current vs Target

## 1. Current Structure

```
core/                         ← 基础设施（不依赖 app/）
    boot.py                   ← 入口：import entry → crash → self_heal
    config.py                 ← 路径 + settings.json 解析
    git.py                    ← Git wrapper
    log.py                    ← 日志
    self_heal.py              ← crash 恢复：调 hotfixer

sdk/                          ← SDK（core 依赖它，app 也依赖它）
    llm.py                    ← LLM client + agent loop + compaction （臃肿）
    tools.py                  ← 4 base tools + 日志辅助函数
    fs.py                     ← 文件系统工具
    skills.py                 ← skill 扫描

app/                          ← 业务逻辑
    agent.py                  ← tick loop: scheduler → evolver → solver
    scheduler.py              ← task 生命周期管理
    solver.py                 ← 调 llm.run_agent() 解任务
    hotfixer.py               ← 调 llm.run_agent() 修 bug
    evolver.py                ← 跑 agent_fn → git dirty? → commit + re-exec
```

**调用链：**

```
boot.main()
  └─ app.agent:run()
       └─ Agent.tick()
            ├─ scheduler.ingest_inbox()
            ├─ scheduler.pick_next_task()
            └─ Agent._execute_task()
                 └─ evolver.evolve(solver.solve)
                      ├─ solver.solve(llm, executor, tools, requirement, note)
                      │    └─ llm.run_agent(messages, tool_executor, ...)
                      │         while True:
                      │            post → model (tool calling)
                      │            execute tools
                      │            duplicate detection
                      │            repetitive call guard
                      │            context compaction (LLM-based summary)
                      │            └─ return {content, calls, results}
                      └─ git dirty? → commit + os.execv(restart)
```

---

## 2. 当前问题

### P0: `sdk/llm.py` 职责混乱

一个文件干了三层的事：

| 职责 | 代码位置 | 行数 |
|------|----------|------|
| HTTP client (`_post`, `chat`) | `LLM` class | ~40 |
| Agent loop (`run_agent`) | `LLM.run_agent()` | ~180 |
| Compaction (token estimation, cut point, summarization) | module-level + `run_agent` 内嵌 | ~100 |
| Guard/dedup (repetitive call, duplicate output) | `run_agent` 内嵌 | ~60 |
| Thinking tag stripping | `_strip_think()` | ~30 |

Pi 把这三层拆成了三个包：`pi-ai` (HTTP) → `pi-agent-core` (loop + compaction) → `pi-coding-agent` (UI)。NYX 全揉进了一个文件。

### P1: Compaction 每次从头生成，不增量合并

当前 `_llm_summarize_compaction()` 把 compactable messages 发给模型生成摘要，但**没有保留上一次压缩的摘要**。多次压缩后，早期历史信息会丢失。

Pi 的做法：
- 首次压缩 → `SUMMARIZATION_PROMPT`（固定结构格式）
- 再次压缩 → `UPDATE_SUMMARIZATION_PROMPT` + `<previous-summary>` + 新对话
- 模型合并更新，信息不丢失

### P2: Compaction 摘要可能触发模型续写对话

当前直接把原始 `messages` 数组（含 assistant/tool role）传给 `self.chat()`。模型可能误以为要继续那个对话而不是做摘要。

Pi 的做法：`serializeConversation()` 把消息转成 `[User]: ...` / `[Assistant tool calls]: ...` / `[Tool result]: ...` 纯文本，包在 `<conversation>` tag 里。

### P3: Agent loop 内嵌太多副作用

`run_agent()` 的 `while True` 里同时做了：
- LLM 调用
- 工具执行
- 重复检测（MD5 hash dedup）
- 连续重复守卫
- 上下文压缩
- terminal tool 提前退出
- token-aware max_tokens clamping

Pi 的 loop (`agent-loop.js`) 只管：stream response → execute tools → emit events。Compaction、persistence、retry 在 `AgentSession` 的事件回调里做。

### P4: `_system_msg` 参数未使用

`_llm_summarize_compaction(self, system_msg, compactable)` 接收了 `system_msg` 但没有传给模型。

---

## 3. Pi 的三层架构（参考）

```
@earendil-works/pi-ai               ← 纯 HTTP/流式客户端
    streamSimple(model, context, options) → EventStream
    completeSimple(model, context, options) → AssistantMessage

@earendil-works/pi-agent-core       ← Agent loop + compaction + session
    agent-loop.js:                  ← 无状态纯函数：runLoop(), executeToolCalls()
        while (hasMoreToolCalls || pendingMessages):
            inject steering messages
            stream assistant response     ← pi-ai
            execute tool calls (parallel/sequential)
            prepareNextTurn
            check shouldStopAfterTurn

    agent.js:                       ← 有状态 class：Agent
        _state          ← messages, tools, isStreaming, pendingToolCalls
        steeringQueue   ← steer() 队列
        followUpQueue   ← followUp() 队列
        listeners       ← event subscribers
        prompt() → runAgentLoop()    ← 启动无状态 loop
        continue() → runAgentLoopContinue()
        steer(followUp)              ← 排队消息
        subscribe(listener)          ← 事件订阅

    harness/compaction/:            ← compaction 独立模块
        serializeConversation()     ← 消息 → [User]:/[Assistant]:/[Tool result]: 文本
        generateSummary()           ← <conversation> + prompt → 结构化摘要
        prepareCompaction()         ← 切分点检测
        compact()                   ← 生成摘要 + 文件操作列表

    harness/session/:               ← JSONL 持久化
        session.js, jsonl-repo.js

@earendil-works/pi-coding-agent     ← TUI / RPC / CLI "壳"
    AgentSession:                   ← 事件订阅层
        subscribe(agent_events)     ← message_start/end, turn_end, agent_end
        _handleAgentEvent()         ← persistence + auto-compaction check + retry
```

**关键设计差异：**

| | Pi | NYX (当前) |
|---|----|------------|
| Loop 实现 | `agent-loop.js` 无状态纯函数 + `Agent` class 管状态 | `LLM.run_agent()` 一个方法干所有事 |
| Compaction 触发 | `AgentSession` 事件回调里异步检查 | `run_agent()` while 循环内同步阻塞 |
| 摘要格式 | 固定 markdown（Goal/Progress/Decisions/Next Steps） | 自由文本 |
| 多次压缩 | `previousSummary` + UPDATE prompt 增量合并 | 丢弃旧摘要，重新生成 |
| 对话序列化 | `<conversation>` tag + `[Role]: prefix` | 原始 messages 数组直接传入 |
| 摘要注入 | 独立 `compactionSummary` 消息类型 | 塞进 user 消息 |

---

## 4. Target Architecture

NYX 不需要拆成三个包（1700 行的项目没必要），但需要**文件级别的责任分离**：

```
sdk/llm.py          ← LLMClient: HTTP client + chat()  [~80 lines]
sdk/agent.py        ← AgentLoop: tool-calling loop     [~200 lines]
sdk/compaction.py   ← Compaction: token estimation, cut point, summarization  [~150 lines]
sdk/tools.py        ← Tools: base tools + schemas       [不变]
```

### 4.1 `sdk/llm.py` — LLMClient

只负责跟 LLM API 通信，一个接口：

```python
class LLMClient:
    def __init__(self, url, model, api_key, timeout): ...

    def chat(self, messages, *, temperature=0.6, max_tokens=2048,
             response_format: dict | None = None) -> str:
        """Chat completion。返回 assistant 纯文本。

        response_format: 可选的 JSON schema 约束（OpenAI-compatible）。
        当前无调用方使用，保留以备后续需要结构化输出的场景。

        Pi 的 pi-ai (completeSimple) 也不区分这个参数——统一走同一个接口，
        结构化摘要靠 prompt 约束而非 response_format。
        """
        ...
```

- 保留 `_strip_think()`、retry logic、`_post()`
- **不包含** agent loop、compaction、guard

### 4.2 `sdk/agent.py` — AgentLoop（无状态纯函数 + 可选有状态 wrapper）

参考 pi 的 `agent-loop.js`（无状态纯函数）+ `Agent` class（有状态 wrapper）的分层。

**核心：无状态的 `run_agent()` 纯函数**

```python
def run_agent(client: LLMClient, messages, tool_executor, *,
              tools, temperature, on_step, terminal_tools, response_format) -> AgentResult:
    """Tool-calling agent loop。无状态，接收 messages 返回结果。

    Returns when model produces text (no tool calls) or terminal tool fires.
    Does NOT handle compaction — caller is responsible for context management.
    """
    while True:
        resp = client.chat_with_tools(msgs, tools, ...)
        if no_tool_calls: return AgentResult(content=resp.text, final=True)
        msgs.append(assistant_msg)
        for tc in tool_calls:
            # repetitive call guard (stays here — it's loop logic)
            res, err = tool_executor(name, args)
            msgs.append(tool_result)
            if name in terminal_tools: return AgentResult(content=res, final=True)
```

**可选：有状态的 `Agent` class（NYX 当前不需要，预留）**

如果后续需要 steer/followUp/subscribe 能力（类似 pi 的 `Agent` class），再加一层：

```python
class Agent:
    """Stateful wrapper around run_agent().

    Holds: messages, tools, steeringQueue, followUpQueue, listeners.
    prompt() / continue() → calls run_agent() internally.
    """
```

NYX 当前是一次性 session（solver/hotfixer 调完就结束），不需要有状态 wrapper。先实现纯函数版本。

- 保留：repetitive call guard、terminal tool、`on_step` 回调
- **移除**：compaction（移到 `sdk/compaction.py`，由调用方管理）
- **移除**：duplicate output detection（可以移到这里或 compaction）

### 4.3 `sdk/compaction.py` — Compaction

独立模块，提供压缩能力但不耦合进 loop：

```python
def estimate_tokens(text: str) -> int: ...
def estimate_context_tokens(messages) -> int: ...
def clamp_max_tokens(requested, context_tokens) -> int: ...

def serialize_conversation(messages) -> str:
    """Convert messages to [User]:/[Assistant]:/[Tool result]: text for summarization."""
    ...

def should_compact(context_tokens, context_window, reserve_tokens) -> bool: ...

def find_cut_point(messages, initial_len, keep_recent_tokens) -> int: ...

def summarize(client: LLMClient, system_msg: str, compactable: list[dict],
              *, previous_summary: str | None = None) -> CompactionResult:
    """Generate or update a compaction summary using the LLM.

    Uses structured format (Goal/Progress/Decisions/Next Steps).
    If previous_summary is provided, uses UPDATE prompt to merge.
    Falls back to rule-based summary on error.

    Returns CompactionResult(summary_text, file_ops, is_update).
    """
    ...

def compact_messages(client: LLMClient, msgs, initial_len, *,
                     reserve_tokens, keep_recent_tokens, previous_summary) -> tuple[list[dict], str]:
    """Full compaction: check trigger → find cut → summarize → splice.

    Returns (new_msgs, new_previous_summary).
    Called by solver/hotfixer between agent loop iterations or after run_agent returns partial results.
    """
    ...
```

### 4.4 调用方变化（solver.py / hotfixer.py）

Compaction 从 `run_agent` 内部移到调用方：

```python
# solver.py (conceptual)
from sdk.llm import LLMClient
from sdk.agent import run_agent
from sdk.compaction import should_compact, compact_messages, estimate_context_tokens

def solve(client, executor, tools, requirement, note, tid):
    messages = [system_msg, user_msg]
    prev_summary = None

    while True:
        result = run_agent(client, messages, executor, tools=tools, ...)
        if result.is_final:
            return result.content

        # Check if we need to compact before continuing
        ctx_tokens = estimate_context_tokens(messages)
        if should_compact(ctx_tokens, CONTEXT_WINDOW, RESERVE):
            messages, prev_summary = compact_messages(
                client, messages, initial_len,
                previous_summary=prev_summary,
            )
```

**但 NYX 的实际场景是 `run_agent` 一次跑完整个 session**，不像 pi 那样有 turn-by-turn 的交互。所以 compaction 有两种方案：

**方案 A（推荐）：compaction 留在 loop 内，但抽到独立模块**

NYX 的 agent session 是一次性跑完的（没有外部 turn 控制），compaction 在 loop 内部检查最自然。只是把代码从 `llm.py` 抽到 `compaction.py`：

```python
# sdk/agent.py
def run_agent(client, messages, tool_executor, *, tools, ...):
    while True:
        # ... loop logic ...

        # Compaction check (delegated to compaction module)
        from sdk.compaction import maybe_compact
        msgs = maybe_compact(client, msgs, _initial_len, ...)
```

**方案 B：调用方管理 compaction，loop 暴露中间状态**

loop 每次 iteration 后 yield/回调，调用方决定是否 compact。这需要 loop 改成 generator 或加 `on_iteration` 回调。改动较大。

**推荐方案 A**，理由：NYX 没有 pi 那样的 turn-by-turn UI 交互，session 是一次性的，compaction 在 loop 内检查最自然。先做文件拆分，compaction 逻辑抽到独立模块但仍在 loop 内调用。后续如果需要支持外部控制再改方案 B。

---

## 5. Compaction 改进（无论 A/B 都要做）

### 5.1 对话序列化

```python
def serialize_conversation(messages: list[dict]) -> str:
    """Convert LLM messages to plain text for summarization prompts.

    Prevents the model from treating the summary request as a conversation to continue.
    Format: [User]: ... / [Assistant tool calls]: ... / [Tool result]: ...
    """
    parts = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "assistant":
            text = (msg.get("content") or "").strip()
            tool_calls = msg.get("tool_calls") or []
            if text:
                parts.append(f"[Assistant]: {text}")
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "?")
                args_str = fn.get("arguments", "{}")
                parts.append(f"[Assistant tool call]: {name}({args_str})")
        elif role == "tool":
            content = (msg.get("content") or "")[:2000]  # truncate for budget
            if len(msg.get("content") or "") > 2000:
                content += "\n... [truncated]"
            parts.append(f"[Tool result]: {content}")
        elif role == "user":
            parts.append(f"[User]: {msg.get('content', '')}")
    return "\n\n".join(parts)
```

### 5.2 结构化摘要格式 + 增量合并

```python
SUMMARIZATION_SYSTEM = (
    "You are a context summarization assistant. Read the conversation and produce "
    "a structured summary. Do NOT continue the conversation."
)

SUMMARIZATION_PROMPT = """\
The messages above are a conversation to summarize. Use this EXACT format:

## Goal
[What is being accomplished?]

## Progress
### Done
- [x] [Completed items]
### In Progress
- [ ] [Current work]
### Blocked
- [Issues, if any]

## Key Decisions
- **[Decision]**: [Rationale]

## Next Steps
1. [Ordered next actions]

## Critical Context
- [File paths, error messages, data needed to continue]
"""

UPDATE_SUMMARIZATION_PROMPT = """\
The messages above are NEW conversation messages to incorporate into the existing summary.

<previous-summary>
{previous_summary}
</previous-summary>

Update the summary: PRESERVE existing info, ADD new progress, UPDATE Next Steps.
Use the same format as above.
"""
```

### 5.3 摘要注入格式

当前把摘要塞进 user 消息。改为更清晰的格式：

```python
summary_msg = {
    "role": "user",
    "content": (
        f"[COMPACTED — {len(compactable)} exchanges removed, keeping recent context]\n"
        f"{summary_text}\n"
        f"{file_note}\n\n"
        f"Continue from where you left off."
    ),
}
```

---

## 6. Migration Plan

按优先级和改动量排序，每一步都是独立可提交的改进：

### Phase 1: Compaction 质量改进（不动架构） ✅ DONE

**改动文件：** `sdk/llm.py`

- [x] 1.1 实现 `_serialize_conversation()` — 消息转 `[Role]: prefix` 文本，包在 `<conversation>` tag 里
- [x] 1.2 摘要 prompt 改为结构化格式（Goal/Progress/Decisions/Next Steps/Critical Context）
- [x] 1.3 修复 `_system_msg` 未使用的问题（传给摘要模型作为上下文）
- [x] 1.4 `run_agent` 维护 `_previous_summary`，下次 compact 用 UPDATE prompt 增量合并
- [x] 删除 `_summarize_tool_result()` — 不再需要

**风险：** 低。只改 prompt 和序列化逻辑，不影响 loop 行为。

### Phase 2: 文件拆分 — `llm.py` → `llm.py` + `compaction.py` ✅ DONE

**改动文件：** 新建 `sdk/compaction.py`（298 lines），修改 `sdk/llm.py`

- [x] 2.1 抽 `estimate_tokens`, `estimate_context_tokens`, `clamp_max_tokens` → `compaction.py`
- [x] 2.2 抽 `extract_file_paths`, `serialize_conversation`, compaction prompts → `compaction.py`
- [x] 2.3 抽 `should_compact`, `find_cut_point` → `compaction.py`
- [x] 2.4 `compaction.summarize(client, ...)` — 通过 `ChatClient` 协议解耦，不依赖 LLM class
- [x] 2.5 `run_agent` 内 compaction 块调用 `summarize(self, ...)` + `extract_file_paths()` + `format_file_note()`
- [x] 2.6 `compaction.py` 无外部依赖（不 import core/config、sdk/llm、sdk/tools）

**风险：** 中。纯重构，功能不变。需要确保 import 路径正确。

### Phase 3: 文件拆分 — `llm.py` → `llm.py` + `agent.py` ✅ DONE

**改动文件：** 新建 `sdk/agent.py`（182 lines），重写 `sdk/llm.py`

- [x] 3.1 `LLM` class 保留，移除 `run_agent()` body → 委托给 `sdk.agent.run_agent(self, ...)`
- [x] 3.2 `run_agent(client, messages, tool_executor, ...)` 移到 `sdk/agent.py`（纯函数）
- [x] 3.3 `_strip_think`, `_prune_tool_output` 留在 `llm.py`（client 层职责）
- [x] 3.4 `app/solver.py`, `app/hotfixer.py` 无需改 — `LLM.run_agent()` 方法签名不变，内部委托
- [x] 3.5 `sdk/llm.py` 最终只含：HTTP client + `chat()` + `_strip_think` + `_prune_tool_output`
- [x] 无 circular import（agent → llm 只导两个函数，llm → agent 是 lazy import in method）

**风险：** 中。涉及 solver/hotfixer 的调用方修改。

### Phase 4: 清理和一致性（可选）

- [ ] 4.1 `_tool_brief` / `_result_summary` / `format_tool_log` 从 `tools.py` 移到独立模块或确认归属
- [ ] 4.2 统一 env var 命名：`_CONTEXT_WINDOW` → `NYX_CONTEXT_WINDOW`（与 `NYX_LLM_TIMEOUT` 一致）
- [ ] 4.3 考虑 `run_agent` 的 duplicate output detection 是否移到 compaction 模块

---

## 7. 文件行数目标（Phase 3 后）

| 文件 | 当前 | 目标 | 说明 |
|------|------|------|------|
| `sdk/llm.py` | ~560 | ~120 | HTTP client + chat + strip_think + prune |
| `sdk/agent.py` | 0 (新建) | ~250 | run_agent loop + guard + dedup |
| `sdk/compaction.py` | 0 (新建) | ~180 | token estimation + cut point + summarize + serialize |
| `sdk/tools.py` | ~170 | ~170 | 不变 |
| `app/solver.py` | ~140 | ~130 | import 路径调整 |
| `app/hotfixer.py` | ~80 | ~75 | import 路径调整 |

---

## 8. 不在范围内的事项

以下事项与本次架构重构无关，暂不处理：

- **并行工具执行**：Pi 支持 parallel/sequential tool execution，NYX 是串行的。这是功能增强不是架构问题。
- **流式输出**：Pi 用 EventStream 流式返回，NYX 是非流式的。改动量大且非本次目标。
- **Session 持久化**：Pi 用 JSONL session file，NYX 用 `sessions/*.jsonl`。格式不同但不影响架构拆分。
- **Extension system**：Pi 有完整的 extension/hooks 体系，NYX 用 skills。设计哲学不同。
