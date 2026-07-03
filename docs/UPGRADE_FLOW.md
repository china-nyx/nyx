# NYX 升级流程设计 (Upgrade Flow)

## 问题背景

当前设计中，solver 直接修改主仓，evolver 检测变化后自动提交 + 重启。

但某些场景需要更精细的控制：
1. **多步升级**：需要先修改代码，再运行测试，再提交
2. **临时隔离**：不想在主仓直接修改，但又不想用 worktree（性能开销）
3. **子任务链**：一个升级可能需要多个步骤，每个步骤由不同的 agent 完成

## 设计目标

1. **保留直接修改主仓的 simplicity**
2. **支持升级子任务（不是 solver 的递归）**
3. **避免 worktree 的开销**

## 新设计：Upgrader 子任务

### 核心概念

```
solver 发现需要升级 → 创建 evolver 子任务 → evolver 修改主仓 → 提交 → 恢复 solver
```

### 任务类型

| 类型 | 职责 | 修改权限 | 状态流转 |
|------|------|----------|----------|
| `solver` | 求解任务 | 只读主仓 | `new` → `running` → `done` / `needs_upgrade` |
| `evolver` | 升级子任务 | 可写主仓 | `new` → `running` → `done` |

### 交互流程

```
1. Solver 运行 → 发现需要升级 (status="needs_upgrade")
2. Scheduler 创建 evolver 子任务 (priority=99, parent_tid=<solver_tid>)
3. Solver 状态设为 `upgrade-waiting`
4. Evolver 运行 → 修改主仓代码
5. Evolver 提交 → 状态 `done`
6. Executor 检测到变化 → 重启
7. Scheduler 恢复父任务 → solver 继续运行
```

### 组件命名

| 当前名 | 新名 | 职责 |
|--------|------|------|
| `solver` | `solver` | 求解任务（不修改主仓） |
| `hotfixer` | `evolver` | 升级主仓（修改主仓 + 自动提交） |
| `evolver` | `executor` | 执行 agent + 监控变化 + 重启 |

**为什么这样命名？**

| 新名 | 含义 | 职责匹配 |
|------|------|----------|
| `executor` | 执行者 | ✅ 执行 agent，监控变化，触发重启 |
| `evolver` | 进化者 | ✅ 修改主仓代码，实现进化 |

**注意：** `evolver` 的实际功能是"修改主仓"，"进化"是其目的而非手段。

**evolver 的职责：**
- 修改源代码（read/write/edit）
- 添加/修改技能（SKILL.md）
- 更新配置
- 运行 smoke check
- 提交所有变更

### 任务状态表

```
Task States:

Solver:
  new → running → done (success)
  new → running → upgrade-waiting → (after evolver done) → running → done

Upgrader:
  new → running → done
```

### 文件结构

```
task/<solver_tid>/
  state          ← new | running | upgrade-waiting | done
  parent_tid     ← (for evolver) points to solver
  status         ← done | needs_upgrade (for solver)
  ...
  
task/<evolver_tid>/
  state          ← new | running | done
  parent_tid     ← solver_tid
  upgrade_type   ← code | skill | config | ...
  ...
```

### 实现要点

1. **Scheduler 支持任务类型**
   ```python
   def create_task(requirement, type="solver", parent_tid=None, ...):
       # ...
   ```

2. **Solver 返回结构化结果**
   ```python
   {
     "status": "done" | "needs_upgrade",
     "upgrade_type": "code" | "skill" | "config",  # optional
     "content": "..."  # upgrade description
   }
   ```

3. **Upgrader 直接修改主仓**
   - 4 个基础工具：bash, read, write, edit
   - 不使用 worktree
   - 修改后 `git add -A && git commit`

4. **恢复机制**
   ```python
   # When evolver done:
   scheduler.set_state(parent_tid, "running")
   # Resume solver with upgraded code
   ```

## 为什么不用 Worktree？

| 方案 | 优点 | 缺点 |
|------|------|------|
| Worktree | 隔离，安全 | 1. Git ref 权限问题<br>2. 拷贝开销大<br>3. 符号链接处理复杂 |
| Upgrader | 1. 直接修改主仓<br>2. 无额外开销<br>3. 简单 | 需要重启机制 |

当前 llm-server 已经足够稳定，直接修改主仓的风险可控。

## 后续优化

1. **并行升级**：多个 evolver 并行（优先级控制）
2. **升级验证**：evolver 运行 smoke check
3. **回滚机制**：升级失败自动回滚
