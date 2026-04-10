# Cron 定时任务

## 概述

Airgent 内置了极轻量的定时任务调度系统，核心是一个纯 asyncio 后台轮询调度器，无需额外依赖（无 APScheduler、无 Celery）。任务以你已有的 Agent 为执行单元，每次触发时生成一个独立的 session 来承载运行日志。

## 核心概念

### 调度类型（ScheduleKind）

| 类型 | `schedule_kind` 值 | `schedule_value` 示例 | 说明 |
|------|-------------------|---------------------|------|
| 标准 Cron | `cron` | `0 * * * *` | 5 段式 cron 表达式（分 时 日 月 周） |
| 单次执行 | `once` | `once` | 固定值，执行后自动禁用 |
| 间隔执行 | `interval` | `300` | 秒数，每次执行后从"现在"重新计时 |

### Cron 表达式格式

```
分   时   日   月   周
*    *    *    *    *   — 每分钟
*/5  *    *    *    *   — 每 5 分钟
0    *    *    *    *   — 每整点
0    9    *    *    *   — 每天 9:00
0    9    *    *    1   — 每周一 9:00（周一=1）
*/15 14   *    *    *   — 每天 14:00 每 15 分钟一次
0    0    *    *    *   — 每天午夜
```

**支持**：通配符 `*`、步进 `*/n`、固定值。
**不支持**：范围 `1-5`、列表 `1,3,5`、秒级字段。

### Job 数据模型

```python
@dataclass(frozen=True)
class ScheduledJob:
    id: str                     # 12 位十六进制 ID
    name: str                   # 人类可读的 job 名称
    agent_key: str               # 使用的 agent 配置 key
    input: str                   # 发给 agent 的 prompt
    schedule_kind: ScheduleKind  # cron | once | interval
    schedule_value: str          # cron 表达式或秒数
    enabled: bool                # 是否接受调度
    one_shot: bool              # 是否一次性（执行完自动 disable）
    last_run_at: str | None      # ISO8601 上次执行时间
    next_run_at: str | None      # ISO8601 下次执行时间
    created_at: str              # 创建时间
    metadata: dict[str, Any]     # 自定义附加数据
```

## 技术原理

### 架构

```
┌──────────────────────────────────────────────────────────────┐
│  FastAPI app (airgent serve)                                │
│                                                              │
│  lifespan startup                                            │
│    └─ CronService.start()  →  后台 _poll_loop() task         │
│         └─ 每 30 秒：list_due_cron_jobs() → 按时触发的 job    │
│              └─ asyncio.create_task(_execute_job(job_id))     │
│                  └─ AgentRunnerService.run(request)          │
│                      └─ 结果写入 session_id = cron:{job_id}:x │
│                                                              │
│  lifespan shutdown                                            │
│    └─ CronService.stop()  →  取消所有运行中 task              │
└──────────────────────────────────────────────────────────────┘

数据存储（SQLite，同 airgent.db）：
  scheduled_jobs 表 ← LocalStore 管理
```

### 调度循环

`CronService` 内部是一个 `asyncio.Task`，每 30 秒醒来一次：

1. 调用 `store.list_due_cron_jobs(now_iso)` 查询 `enabled=1 AND next_run_at <= now` 的 job。
2. 对每个 due 的 job，检查是否已有运行中 task（防重入）。
3. 用 `asyncio.create_task(_execute_job(job_id))` 异步执行，不阻塞轮询循环。
4. `_execute_job` 完成后：
   - 计算下一次 `next_run_at`（cron 表达式重新计算，或 interval 从"现在"累加）
   - 更新 `last_run_at` 和 `next_run_at`
   - `one_shot` 的 job 自动置 `enabled=False`

### 数据存储

使用已有的 SQLite（`~/.airgent/airgent.db`），新增 `scheduled_jobs` 表：

```sql
CREATE TABLE scheduled_jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agent_key TEXT NOT NULL,
    input TEXT NOT NULL,
    schedule_kind TEXT NOT NULL,   -- 'cron' | 'once' | 'interval'
    schedule_value TEXT NOT NULL,  -- cron expr or seconds
    enabled INTEGER NOT NULL DEFAULT 1,
    one_shot INTEGER NOT NULL DEFAULT 0,
    last_run_at TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_scheduled_jobs_next_run
ON scheduled_jobs(next_run_at) WHERE enabled = 1;
```

### Session 隔离

每次 job 执行都会生成独立的 session_id：`cron:{job_id}:{8位随机hex}`。执行结果（成功或失败）作为 `role=system` 的消息写入该 session，方便事后回溯。

```
airgent sessions list   →  能看到所有 cron job 的执行 session
airgent sessions show cron:abc123:def456   →  查看该次执行的 transcript
```

## API

所有接口前缀：`/api/v1/cron`

### 创建 Job

```
POST /api/v1/cron
Content-Type: application/json

{
  "name": "daily-news",
  "agent_key": "root_assistant",
  "input": "请摘要今天最重要的三条科技新闻",
  "schedule_kind": "cron",
  "schedule_value": "0 9 * * *",
  "enabled": true,
  "one_shot": false,
  "metadata": {}
}
```

### 列出所有 Job

```
GET /api/v1/cron
```

### 获取单个 Job

```
GET /api/v1/cron/{job_id}
```

### 更新 Job

```
PATCH /api/v1/cron/{job_id}
Content-Type: application/json

{
  "schedule_value": "0 10 * * *",   -- 修改调度时间
  "enabled": false                   -- 暂停
}
```

### 删除 Job

```
DELETE /api/v1/cron/{job_id}
```

### 暂停 / 恢复

```
POST /api/v1/cron/{job_id}/pause
POST /api/v1/cron/{job_id}/resume   -- resume 会从"现在"重新计算 next_run_at
```

### 手动触发

```
POST /api/v1/cron/{job_id}/trigger
```

立即执行，跳过 schedule 校验，返回 202 Accepted。执行结果在对应的 cron session 中可查。

## CLI

```
airgent cron list
airgent cron create <name> --input <prompt> --schedule-kind cron --schedule-value "0 * * * *"
airgent cron delete <job_id>
airgent cron pause <job_id>
airgent cron resume <job_id>
airgent cron trigger <job_id>
```

交互式创建（省略 `--input` 或 `--schedule-value` 会触发 prompt）：

```bash
# 每小时摘要新闻
airgent cron create hourly-news \
  --input "摘要过去一小时的科技动态" \
  --schedule-kind cron \
  --schedule-value "0 * * * *"

# 每 5 分钟检查一次
airgent cron create frequent-check \
  --input "检查并汇报系统状态" \
  --schedule-kind interval \
  --schedule-value "300"

# 一次性任务
airgent cron create one-time \
  --input "只跑一次的任务" \
  --schedule-kind once \
  --schedule-value "once"

# 每天早上 9 点
airgent cron create morning \
  --input "今天日期是什么？" \
  --schedule-kind cron \
  --schedule-value "0 9 * * *"
```

## 常见问题

### serve 和 chat 模式都支持 cron 吗？

只有 `airgent serve` 模式（FastAPI 服务）会在后台运行调度循环。`airgent chat` 是单次交互，不启动服务，因此不支持定时调度。

### job 执行失败会怎样？

异常被捕获后写入对应 session（`role=system`，`kind=cron_error`），job 本身保持 `enabled=True`，下次仍会按调度时间再次尝试。

### 可以同时运行多个 airgent serve 吗？

可以，但每个进程的调度器都会独立轮询和执行 job，没有分布式锁。预期外的重复执行。

### 如何调试 cron job？

查看对应 session 的 transcript：
```bash
airgent sessions list | grep "^cron:"
airgent sessions show <session_id>
```

### cron 表达式不合法会报错吗？

`create_job` 和 `update_job` 会在内部尝试验证，非法表达式会导致 `next_run_at = None`，job 不会执行但也不会报错。建议先用 CLI 创建验证格式。

## 文件索引

- 核心实现：`app/cron/service.py`
- 数据存储：`app/memory/store.py`（`scheduled_jobs` 表操作）
- 依赖注入：`app/bootstrap.py`
- HTTP 路由：`app/api/routes/cron.py`
- 请求/响应模型：`app/api/schemas/cron.py`
- CLI 命令：`app/cli.py`（`@cron_app`）
