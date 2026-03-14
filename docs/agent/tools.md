# Tools

## 原理
Airgent 的 tool 是暴露给 agent 的运行时函数，先在 Python 中实现并注册，再由 agent YAML 按需启用。

可用条件：
- 已在 `app/tools/registry.py` 注册
- 已写进该 agent 的 YAML `tools`

执行链路：
1. agent YAML 写入 `tools`
2. `AgentRegistry` 读取这些 key
3. `ToolRegistry` 映射到 Python 实现
4. 高风险工具在 `allow_high_risk_tools != true` 时被过滤
5. 解析后的工具挂到 agent 上
6. 运行时由模型决定是否调用

所以 tool 编排的本质是：
- 工具集中注册
- agent 按需启用
- 高风险工具单独门控

## 当前内置 Tools
- `read_file`
- `create_file`
- `edit_file`
- `run_bash_command`
- `search_memory`
- `remember_note`
- `list_skills`
- `load_skill`

## 分类和边界
文件工具：
- `read_file`
- `create_file`
- `edit_file`

边界：
- 只能操作 `project_root` 内文件
- 路径越界会被拒绝
- `edit_file` 要求 `old_text` 必须存在

Shell 工具：
- `run_bash_command`

边界：
- 在 `project_root` 下执行
- 有默认超时
- 会拦截高风险命令
- 会拦截 `bash -c` 这类 inline shell
- 属于高风险工具

Memory 工具：
- `search_memory`
- `remember_note`

Skill 工具：
- `list_skills`
- `load_skill`

## 给 Agent 启用 Tool
直接在 agent YAML 里写：

```yaml
key: code_reviewer
version: v1
model: gpt-4o
max_turns: 8
instructions: |
  You are a code review agent.
allow_high_risk_tools: false
tools:
  - read_file
  - search_memory
  - list_skills
  - load_skill
```

如果不在 `tools` 列表里，这个 agent 就不能用该工具。
如果要开 shell，还要额外加：

```yaml
allow_high_risk_tools: true
tools:
  - run_bash_command
```

## 新增 Tool
步骤：
1. 在 `app/tools/` 新增实现
2. 在 `app/tools/registry.py` 注册 key
3. 在目标 agent YAML 的 `tools` 中引用
4. 如果有风险，注册时设为 `high_risk=True`
5. 只有需要它的 agent 才开启 `allow_high_risk_tools`

最小示例：

```python
@function_tool
async def summarize_repo(
    wrapper: RunContextWrapper[AgentRunContext],
) -> dict[str, str]:
    return {"summary": "repo summary"}
```

注册：

```python
"summarize_repo": ToolDefinition("summarize_repo", summarize_repo),
```

启用：

```yaml
tools:
  - read_file
  - summarize_repo
```

## Prompt / Skill / Tool 怎么分
- 一直生效的身份和规则，放 `instructions`
- 条件触发的流程说明，放 skill
- 需要执行动作、读写状态、返回结构化结果的，做成 tool

## 排查
tool 不可用时检查：
1. 是否已在 `app/tools/registry.py` 注册
2. 是否已写入 agent YAML 的 `tools`
3. 是否被高风险过滤掉
4. 改 Python 后是否已重启服务

`run_bash_command` 被拒绝时检查：
1. 是否启用了 `allow_high_risk_tools: true`
2. 是否命中了 blocked command
3. 是否用了 `bash -c` 这类 inline shell
4. 是否执行超时

文件工具失败时检查：
1. 路径是否越出 `project_root`
2. 目标文件是否存在
3. `edit_file` 的 `old_text` 是否真的存在

## 限制
- tool 不能在 YAML 里动态定义，必须先写 Python
- tool 可见性是按 agent 控制的，不是全局开放
- 即使开启 shell，也仍然受命令和超时限制
