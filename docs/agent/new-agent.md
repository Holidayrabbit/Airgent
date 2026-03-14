# New Agent

## 原理

Airgent 现在的 agent 是“配置驱动”的，不是代码里硬编码注册，也不是多 agent 编排图。

一次请求的执行链路：

1. CLI / API / TUI / WebUI 传入 `agent_key`
2. `AgentRunnerService` 构建运行时上下文
3. `AgentRegistry` 读取 `app/agents/configs/<agent_key>.yaml`
4. 按配置解析工具列表
5. 用 YAML 里的 `instructions` 作为基础 prompt
6. 系统自动附加运行时上下文：
   - `project_root`
   - `skills_root`
   - `session_id`
   - memory hits
7. OpenAI Agents SDK 执行该 agent

所以当前“agent 编排”的本质只有三件事：

- 按 `agent_key` 选择一个 agent 配置
- 给它挂载允许的 tools
- 给它附加统一的运行时上下文

当前不是 multi-agent handoff graph，没有 supervisor/sub-agent 路由层。

## 新建 Agent

在下面目录新增一个 YAML 文件：

```text
app/agents/configs/
```

文件名通常与 `key` 一致，例如：

```text
app/agents/configs/code_reviewer.yaml
```

最小示例：

```yaml
key: code_reviewer
version: v1
model: gpt-4o
max_turns: 8
instructions: |
  You are a strict code review agent.
  Focus on bugs, regressions, and missing tests.
allow_high_risk_tools: false
tools:
  - read_file
  - search_memory
  - list_skills
  - load_skill
```

字段说明：

- `key`: agent 唯一标识
- `version`: 展示给 API / UI 的版本号
- `model`: 模型名，或 `default`
- `max_turns`: 最大回合数
- `instructions`: 基础系统提示词
- `allow_high_risk_tools`: 是否允许高风险工具
- `tools`: 该 agent 启用的工具 key

## 操作步骤

1. 复制一个现有配置，例如 `root_assistant.yaml`
2. 修改 `key`
3. 直接改 `instructions`
4. 只保留需要的 `tools`
5. 除非确实需要 shell，保持 `allow_high_risk_tools: false`
6. 启动后通过 API / WebUI / CLI 选择这个 agent

## 生效方式

新增 YAML 后，不需要再写额外注册代码：

- `/api/v1/agent/available` 会自动列出它
- WebUI 下拉框会自动显示它
- CLI 可直接用 `--agent <key>`

CLI 示例：

```bash
airgent chat --agent code_reviewer "review the current repository"
```

API 示例：

```bash
curl -X POST http://127.0.0.1:10304/api/v1/agent/run \
  -H "Content-Type: application/json" \
  -d '{
    "input": "review the current repository",
    "agent_key": "code_reviewer"
  }'
```

## 什么时候还需要写代码

只改 prompt 和 tools，不需要写 Python。

只有这两种情况需要改代码：

- 你要新增 tool：去 `app/tools/` 实现并在 `app/tools/registry.py` 注册
- 你要保留旧式动态 prompt：继续用兼容字段 `instructions_builder`

新 agent 默认应使用 `instructions`，不要再为普通场景写 prompt builder 函数。

## 限制

- agent 选择是“单次请求选一个”，不是动态 agent graph
- tools 仍然必须先在 Python 中注册
- prompt 可以写在 YAML，但 tools 不能写在 YAML 里动态定义
