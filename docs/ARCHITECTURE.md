# NYX Architecture — Current vs Target

## 1. Current Structure (Phase 3 后)

```
core/                         ← 基础设施（不依赖 app/）
    boot.py                   ← 入口：import entry → crash → self_heal
    config.py                 ← 路径 + settings.json 解析
    git.py                    ← Git wrapper
    log.py                    ← 日志
    self_heal.py              ← crash 恢复：调 hotfixer

sdk/                          ← SDK（core 依赖它，app 也依赖它）
    llm.py        (105 lines) ← HTTP client + chat() + strip_think + prune
    agent.py      (182 lines) ← run_agent loop + guard + dedup + compaction
    compaction.py (298 lines) ← token estimation + cut point + serialize + summarize
    tools.py                    ← 4 base tools + schemas
    fs.py                       ← 文件系统工具
    skills.py                   ← skill 扫描

app/                          ← 业务逻辑
    agent.py                  ← tick loop: scheduler → evolver → solver
    scheduler.py              ← task 生命周期管理
    solver.py                 ← run_agent(llm, executor, ...) 解任务
    hotfixer.py               ← run_agent(llm, executor, ...) 修 bug
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
                      ├─ solver.solve() → sdk.agent.run_agent(llm, executor, ...)
                      │    while True:
                      │       llm._post() → model (tool calling)
                      │       execute tools
                      │       duplicate detection
                      │       repetitive call guard
                      │       compaction: sdk.compaction.summarize(llm, ...)
                      │       └─ return {content, calls, results}
                      └─ git dirty? → commit + os.execv(restart)
```

**模块依赖方向：**

```
app/solver.py  ──→  sdk.agent.run_agent(llm, ...)
app/hotfixer.py ──→  sdk.agent.run_agent(llm, ...)
                       │
                       ├── sdk.compaction (summarize, should_compact, find_cut_point, ...)
                       └── sdk.llm (_strip_think, _prune_tool_output)

sdk/compaction.py ———— 零外部依赖（不 import core/config/sdk/llm/sdk/tools）
```

---

## 2. 已解决的问题

### ✅ P0: `llm.py` 职责混乱 → 拆为三个文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `sdk/llm.py` | 105 | HTTP client + chat() + strip_think + prune |
| `sdk/agent.py` | 182 | run_agent loop + guard + dedup + compaction delegation |
| `sdk/compaction.py` | 298 | token estimation + cut point + serialize + summarize |

### ✅ P1: Compaction 每次从头生成 → 增量合并

`_previous_summary` 在 `run_agent` 中维护，每次 compact 传给 `summarize()`，用 `COMPACT_UPDATE_PROMPT` 增量合并。

### ✅ P2: 原始 messages 直接传给摘要模型 → 对话序列化

`serialize_conversation()` 转成 `[User]: ... / [Assistant]: ... / [Tool result]: ...` 纯文本，包在 `<conversation>` tag 里，防止模型续写对话。

### ✅ P3: Agent loop 内嵌太多副作用 → 拆到 agent.py + compaction.py

Loop 核心逻辑在 `sdk/agent.py`（无状态纯函数），compaction 委托给 `sdk/compaction.py`。

### ✅ P4: `_system_msg` 参数未使用 → 已修复

传给摘要模型作为 `System prompt:` 前置上下文。

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

    harness/compaction/:            ← compaction 独立模块
        serializeConversation()     ← 消息 → [User]:/[Assistant]:/[Tool result]: 文本
        generateSummary()           ← <conversation> + prompt → 结构化摘要
        prepareCompaction()         ← 切分点检测
        compact()                   ← 生成摘要 + 文件操作列表

@earendil-works/pi-coding-agent     ← TUI / RPC / CLI "壳"
    AgentSession:                   ← 事件订阅层
        subscribe(agent_events)     ← message_start/end, turn_end, agent_end
        _handleAgentEvent()         ← persistence + auto-compaction check + retry
```

**关键设计差异：**

| | Pi | NYX (Phase 3 后) |
|---|----|------------------|
| Loop 实现 | `agent-loop.js` 无状态纯函数 + `Agent` class 管状态 | `agent.py` 无状态纯函数，调用方传 client |
| Compaction 触发 | `AgentSession` 事件回调里异步检查 | `run_agent()` while 循环内同步（方案 A） |
| 摘要格式 | 固定 markdown（Goal/Progress/Decisions/Next Steps） | ✅ 已实现结构化格式 + 增量合并 |
| 多次压缩 | `previousSummary` + UPDATE prompt 增量合并 | ✅ 已实现 |
| 对话序列化 | `<conversation>` tag + `[Role]: prefix` | ✅ 已实现 |
| 摘要注入 | 独立 `compactionSummary` 消息类型 | user 消息（暂不改） |

---

## 4. Phase 4: 清理和一致性（可选）

### 4.1 `_tool_brief` / `_result_summary` / `format_tool_log` 归属确认

当前在 `sdk/tools.py`，但它们是日志辅助函数，不属于 tools 本身。两个选项：

- **A. 不动**：tools.py 已经有这些函数，solver/hotfixer 的 `on_step` 回调在用
- **B. 移到独立模块**：如 `sdk/logging.py` 或留在调用方（solver/hotfixer）内部

建议：**不动**。改动收益小，且 tools.py 不依赖它们（它们是 module-level 函数不是 class 方法）。

### 4.2 统一 env var 命名

当前 compaction config 用 `_CONTEXT_WINDOW`、`_COMPACTION_RESERVE` 等下划线前缀，与 `NYX_LLM_TIMEOUT` 风格不一致。

建议：**不动**。这些变量只在 `sdk/compaction.py` 内部使用，不需要外部配置。如果后续需要暴露给运维，再统一命名。

### 4.3 Duplicate output detection 归属

当前在 `sdk/agent.py` 的 `run_agent()` 里（MD5 hash dedup）。它和 compaction 有关联（dedup 减少需要 compact 的内容），但不属于 compaction 本身。

建议：**不动**。它在 loop 内实时检测，放在 agent.py 是合理的。

---

## 5. 不在范围内的事项

以下事项与本次架构重构无关，暂不处理：

- **并行工具执行**：Pi 支持 parallel/sequential tool execution，NYX 是串行的
- **流式输出**：Pi 用 EventStream 流式返回，NYX 是非流式的
- **Session 持久化格式**：Pi 用 JSONL session file，NYX 用 `sessions/*.jsonl`
- **Extension system**：Pi 有完整的 extension/hooks 体系，NYX 用 skills
- **有状态 Agent class**：Pi 的 `Agent` class 管理 steer/followUp/subscribe，NYX 当前不需要

---

## 6. 迁移历史

| Phase | 状态 | 改动 |
|-------|------|------|
| Phase 1: Compaction 质量改进 | ✅ DONE | 对话序列化 + 结构化 prompt + 增量合并 |
| Phase 2: 拆 compaction.py | ✅ DONE | token estimation + serialize + summarize 抽到独立模块 |
| Phase 3: 拆 agent.py | ✅ DONE | run_agent 移到独立模块，llm.py 只剩 HTTP client |
| Phase 4: 清理和一致性 | ⏭️ SKIP | 收益小，暂不动 |
