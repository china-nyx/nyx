# NYX 升级流程 (Upgrade Flow)

## 概述

NYX 的升级流程采用简化的直接修改模式：
- Solver/Hotfixer 直接修改主仓代码
- Commit 后，Executor 检测 HEAD 变化 → 重启
- 没有复杂的子任务机制

## 为什么简化？

**之前的 upgrader 子任务设计问题：**
1. 增加复杂度：需要管理父子任务、状态流转
2. 多数场景不需要区分"求解"和"升级"
3. Solver 有能力修改代码，不需要强制限制

**简化后的优势：**
1. 更简单：直接修改 + 提交
2. 更灵活：Solver 可以做任何事情
3. 更可靠：没有状态同步问题

## 当前流程

```
1. Solver 运行
   - 读取 skills
   - 执行任务（可能修改代码）
   - 提交 changes

2. Executor 检测
   - 记录 HEAD
   - 运行 solver
   - 检测 HEAD 是否变化

3. 重启
   - 如果 HEAD 变化 → os.execv 重启
   - 如果没变化 → 正常结束
```

## 文件结构

```
app/
├── solver.py      ← 求解任务，可修改代码
├── hotfixer.py    ← 热修复，修改代码
├── executor.py    ← 执行 + 重启
├── scheduler.py   ← 任务管理
└── main.py        ← 主入口
```

## Agent 通信

**无复杂通信**：
- Solver 不需要返回特殊状态
- Executor 只检测 HEAD 变化
- 没有状态同步、子任务、父任务恢复

**直接提交**：
```bash
git add -A && git commit -m 'fix: brief description'
```

Executor 检测到 HEAD 变化 → 重启。
