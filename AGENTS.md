# AGENTS.md

## 先看这里

- 项目总览与启动方式：`README.md`
- 系统架构与模块分层：`docs/ARCHITECTURE.md`
- Agent 内核（pi）协议文档：pi 仓库 `packages/coding-agent/docs/rpc.md`（本机全局安装的 pi 包内也带 `docs/`）

## 按主题找文档

### Agent 内核（pi）

- 内核进程与 RPC 协议：`app/kernel/process.py`、`app/kernel/events.py`
- 会话级进程管理与恢复：`app/kernel/manager.py`
- pi 隔离环境装配（models.json / 系统提示 / 扩展）：`app/kernel/setup.py`
- Runner（事件翻译、转录落库、记忆注入）：`app/agents/runner.py`

### Tools / Skills

- 工具由 pi 内建（read/write/edit/bash/grep/…），自定义工具走 pi 扩展
- Memory 扩展（search_memory / remember_note）：`pi/airgent-memory.ts`（部署时渲染到 pi agent 目录）
- Skill 机制与编写方式：`docs/agent/skills.md`（pi 原生发现 `.agents/skills/`）

### API / Web / CLI

- API 服务入口：`app/main.py`
- API 路由：`app/api/routes/`
- API 数据结构：`app/api/schemas/`
- CLI 入口：`app/cli.py`（也支持 `python -m app`，memory 扩展依赖它）
- TUI 入口：`app/tui.py`
- Web 静态资源：`app/web/static/`

### Memory / 存储

- Memory Store（transcripts + memories + kernel_sessions）：`app/memory/store.py`
- Memory 上下文构建（提示注入）：`app/memory/context_builder.py`

### 配置与启动

- 启动装配：`app/bootstrap.py`
- 应用配置：`app/core/config.py`
- 日志：`app/core/logging.py`

### 测试

- 测试目录：`tests/`
- 内核协议与装配测试：`tests/test_kernel.py`
- Store 测试：`tests/test_store.py`
- 配置测试：`tests/test_settings_env.py`
- Agent progress / TUI 测试：`tests/test_agent_progress.py`

## 按问题找入口

- 想知道项目怎么跑起来：先看 `README.md`
- 想理解整体结构：先看 `docs/ARCHITECTURE.md`
- 想改模型接入/代理：`app/kernel/setup.py` 和 `app/core/config.py`
- 想给模型加工具：写一个 pi 扩展（参考 `pi/airgent-memory.ts`），在 `app/kernel/setup.py` 里部署
- 想新增 skill：放到工作区 `.agents/skills/<name>/SKILL.md`
- 想调整 API：先看 `app/main.py` 和 `app/api/routes/`
- 想排查会话或记忆：先看 `app/kernel/manager.py` 和 `app/memory/`

## 使用约定

- 优先通过文档定位，再进入代码。
- 优先修改已有文档，不重复新增相同主题说明。
- 如果新增模块或重要文档，更新本文件中的对应导航项。

## Cursor Cloud specific instructions

- **包管理器**: 项目使用 `uv`。依赖安装命令为 `uv sync`，运行命令前缀为 `uv run`。
- **测试**: `uv run pytest` 即可运行全部测试（22 个），无需外部服务。
- **启动开发服务器**: `uv run airgent serve --reload`，默认监听 `127.0.0.1:10304`。
- **环境变量**: 需要 `OPENAI_API_KEY`（必须）和 `OPENAI_BASE_URL`（可选，代理地址）。项目会自动加载 `.env` 文件。模型可用 `AIRGENT_MODEL` 覆盖（如 `anthropic/claude-sonnet-4.5`）。
- **外部依赖**: 需要 `pi` 内核在 PATH（`curl -fsSL https://pi.dev/install.sh | sh` 或 npm 全局安装）；pi 状态隔离在 `~/.airgent/pi/`。
- **无 lint 工具**: 项目当前未配置独立的 linter（无 ruff/flake8/mypy 配置），`pytest` 是唯一的自动化质量检查。
- **数据库**: 嵌入式 SQLite，自动创建于 `~/.airgent/airgent.db`，无需额外配置。
- **无 Docker 依赖**: Python 进程 + pi 子进程，无需 Docker；WebUI 为纯静态资源。
