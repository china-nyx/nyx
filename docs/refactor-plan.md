# NYX 架构重构规划

## 目标架构

```
evolver.evolve(agent_fn):
    pre_head = git.head()
    result = agent_fn()            # 实际业务 LLM session
    post_head = git.head()
    if post_head != pre_head:      restart              # 已 commit → 直接重启
    elif git.dirty():               commit + restart     # 未提交 → 提交后重启
    else:                           return result        # 无变更 → 正常返回

调用：
    evolver.evolve(lambda: solver.solve(llm, execute, req))
    evolver.evolve(lambda: hotfixer.fix(llm, execute, req))

system prompt 告诉模型两个路径：
    Working directory: {cwd}       # runtime data (sandbox, skills, task, mailbox)
    Repo: {repo_path}              # git repo source code (read/write .py files)
```

---

## 改动清单

### Step 1: config.py 清理

- 删 `SRC_LINK`, `WORKTREES`
- `CODE` → `REPO`

### Step 2: boot.py 简化

- 删 `_mount_source_ro()`, `_umount_source()`
- 删 SRC_LINK symlink 创建
- self-heal 改为 `evolver.evolve(lambda: hotfixer.fix(llm, execute, req))`

### Step 3: evolver.py 重写

```python
def evolve(agent_fn):
    pre_head = git.head()
    result = agent_fn()
    post_head = git.head()
    if post_head != pre_head or git.dirty():
        commit + restart
    return result
```

### Step 4: 新建 hotfixer.py (原 editor.py)

```python
def fix(llm, executor, requirement):
    # 4 tools only LLM session, 改 REPO 里的代码
```

### Step 5: solver.py 改为函数

```python
def solve(llm, executor, requirement):
    # 4 tools + skills LLM session
```

- system prompt 用 `repo={config.REPO}` 和 `cwd={config.HOME}`
- 删 "bind-mounted read-only"

### Step 6: agent.py 简化

```python
evolver.evolve(lambda: solver.solve(llm, execute, req))
# needs_upgrade 时:
evolver.evolve(lambda: hotfixer.fix(llm, execute, content))
```

### Step 7: 删除旧文件

- `app/editor.py`

### Step 8: git.py 清理

worktree 方法暂不删。

### Step 9: 文档更新

README.md, AGENTS.md — 删 bind mount、worktree，更新架构。

### Step 10: skill 路径更新

SKILL.md 里不写绝对路径。system prompt 告诉模型 cwd 和 repo 的实际位置，
SKILL.md 用相对描述（如 "the repo directory"），模型从 system prompt 获取真实路径。

---

## 不改的部分

sdk/llm.py, sdk/tools.py, sdk/skills.py, sdk/atomic_io.py, core/log.py, app/scheduler.py