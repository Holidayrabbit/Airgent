# AGENTS.md

## 先看这里

- 项目总览与启动方式：`README.md`
- 系统架构与模块分层：`docs/ARCHITECTURE.md`
- 使用 OpenAI Agents SDK 的设计原则：`docs/openai_agents_sdk_best_practices.md`

## 按主题找文档

### Agent

- 新建 Agent：`docs/agent/new-agent.md`
- Agent 注册与运行代码：`app/agents/registry.py`、`app/agents/runner.py`
- Agent 上下文与 prompt：`app/agents/context.py`、`app/agents/prompts.py`
- Agent 配置示例：`app/agents/configs/root_assistant.yaml`

### Tools

- Tool 设计与接入：`docs/agent/tools.md`
- Tool 注册：`app/tools/registry.py`
- 文件类工具：`app/tools/file_tools.py`
- Bash 工具：`app/tools/bash_tools.py`
- Memory 工具：`app/tools/memory_tools.py`
- Skill 工具：`app/tools/skill_tools.py`

### Skills

- Skill 机制与编写方式：`docs/agent/skills.md`
- 项目内 skills 目录：`.agents/skills/`

### API / Web / CLI

- API 服务入口：`app/main.py`
- API 路由：`app/api/routes/`
- API 数据结构：`app/api/schemas/`
- CLI 入口：`app/cli.py`
- TUI 入口：`app/tui.py`
- Web 静态资源：`app/web/static/`

### Session / Memory / 存储

- Session 实现：`app/sessions/session.py`
- Session 装配：`app/sessions/factory.py`
- Memory Store：`app/memory/store.py`
- Memory 上下文构建：`app/memory/context_builder.py`

### 配置与启动

- 启动装配：`app/bootstrap.py`
- 应用配置：`app/core/config.py`
- OpenAI 配置：`app/core/openai_config.py`
- 日志：`app/core/logging.py`

### 测试

- 测试目录：`tests/`
- Agent 注册测试：`tests/test_agent_registry.py`
- File tools 测试：`tests/test_file_tools.py`
- Bash tools 测试：`tests/test_bash_tools.py`
- Store 测试：`tests/test_store.py`
- 配置测试：`tests/test_settings_env.py`
- Agent progress 测试：`tests/test_agent_progress.py`

## 按问题找入口

- 想知道项目怎么跑起来：先看 `README.md`
- 想理解整体结构：先看 `docs/ARCHITECTURE.md`
- 想新增一个 agent：先看 `docs/agent/new-agent.md`
- 想新增或修改 tool：先看 `docs/agent/tools.md`
- 想新增 skill：先看 `docs/agent/skills.md`
- 想调整 API：先看 `app/main.py` 和 `app/api/routes/`
- 想排查会话或记忆：先看 `app/sessions/` 和 `app/memory/`
- 想理解 SDK 设计取舍：先看 `docs/openai_agents_sdk_best_practices.md`

## 使用约定

- 优先通过文档定位，再进入代码。
- 优先修改已有文档，不重复新增相同主题说明。
- 如果新增模块或重要文档，更新本文件中的对应导航项。
