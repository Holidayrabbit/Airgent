# 使用 OpenAI Agents SDK 设计 Agent 的最佳实践

## 1. 目标与适用场景

本文总结了使用 OpenAI Agents SDK 设计生产级 agent 系统的最佳实践，重点适用于以下场景：

- 以服务调用为主，请求进入后由 agent 自动执行并返回结果
- 后端服务采用 Python + FastAPI
- 需要 tools、skills、multi-agent、session、tracing 等能力
- 希望前期快速上线，后续再逐步增强编排与治理能力

本文默认你使用 OpenAI Agents SDK 作为运行时底座，并将其作为一个独立服务来部署。

---

## 2. 先理解 SDK 的最小抽象

OpenAI Agents SDK 的核心抽象很少，主要是：

- `Agent`
- `Tools`
- `Handoffs`
- `Guardrails`
- `Session`
- `Tracing`
- `Runner`

最佳实践的第一条不是“多用高级特性”，而是**尽量围绕这些原生抽象设计系统**，不要过早构造一套和 SDK 不兼容的自定义框架。

### 建议
- 把 `agent` 视为运行时单元
- 把 `tool` 视为能力暴露单元
- 把 `handoff` 视为会话级任务委派
- 把 `agent-as-tool` 视为一次性 skill 执行
- 把 `session` 视为短期上下文，不要天然等同于长期记忆
- 把 `context` 视为本地依赖注入，不要放进 prompt

---

## 3. Agent 设计最佳实践

## 3.1 一个 agent 只负责一类职责

不要把一个 agent 设计成“万能总控”。  
更好的模式是：

- 一个 `root agent` 负责理解意图、选择能力、整合输出
- 多个 `specialist agents` 负责各自领域
- 少量 `skill agents` 负责复杂单点任务

### 推荐做法
- `root agent`：做路由、整合、少量常规回答
- `specialist agent`：按业务域拆分，例如订单、知识库、报表、文档处理
- `skill agent`：按任务拆分，例如 SQL 查询、文档抽取、代码生成、网页检索

### 不推荐
- 一个 agent 同时管理所有 tool、所有流程、所有输出格式
- 把所有业务规则都塞进超长 prompt

---

## 3.2 instructions 永远要明确、具体、稳定

SDK 官方明确建议传 `instructions`，也就是系统提示。  
因此在生产中：

- 每个 agent 都应有清晰的系统职责
- 明确它能做什么、不能做什么
- 明确什么时候自己回答，什么时候调用 tool，什么时候 handoff

### 推荐的 instructions 结构
1. 身份与职责
2. 任务边界
3. tool 使用原则
4. handoff 原则
5. 输出格式与风格
6. 失败与不确定时的处理方式

### 推荐示例结构
```text
你是订单支持 Agent。
你的职责是：
1. 查询订单状态
2. 判断是否需要转交退款 Agent
3. 向用户解释订单处理结果

你不能：
1. 执行退款
2. 编造订单信息
3. 暴露内部系统字段

当需要退款、退货、赔付时，优先 handoff 给 Refund Agent。
当工具调用失败时，说明失败原因并给出下一步建议。
```

### 不推荐
- 把产品需求文档原封不动塞进 instructions
- 同时放太多例外规则
- 把用户上下文、权限、租户信息混在 instructions 中

---

## 3.3 把动态运行时数据放进 context，不要放进 prompt

SDK 的 `context` 不会发给模型，而是本地运行对象，适合传：

- 用户 ID
- 租户 ID
- 权限快照
- 数据库连接
- Redis 客户端
- 内部服务客户端
- feature flags
- request_id / trace tags

### 推荐
- prompt 只描述行为
- context 提供依赖和运行时数据
- tools / guardrails / lifecycle hooks 只从 context 读内部依赖

### 不推荐
- 在 prompt 里写一大段隐藏式业务状态
- 把敏感权限字段直接暴露给模型

---

## 4. Tool 设计最佳实践

## 4.1 Tool 要小、稳、可描述、可审计

好的 tool 应具备以下特征：

- 输入 schema 清晰
- 名称和描述直观
- 单次调用职责明确
- 结果尽量结构化
- 失败模式可预期
- 可做超时、重试、熔断、审计

### 推荐
- 一个 tool 做一件事
- 返回结构化 JSON / dataclass 兼容对象
- docstring 写清输入含义和适用场景
- 对外部依赖加 timeout

### 不推荐
- 一个 tool 里混合搜索、写入、审批、通知多个动作
- 返回过长、过脏、不可解析的原始文本

---

## 4.2 优先使用函数式工具，不要过早复杂化

SDK 对 Python function tool 支持很好，会自动生成工具名、描述和输入 schema。  
V1 阶段优先采用函数工具最合适。

### 推荐
- 用普通 Python 函数封装业务动作
- 用明确的参数类型与 docstring
- 在函数里做输入校验和异常归一化

### 示例模式
```python
def get_order_status(order_id: str) -> dict:
    """查询订单状态。仅在已知订单号时调用。"""
    ...
```

### 不推荐
- 首发版本就大量引入动态工具注册、复杂插件机制、远程代码执行
- 把工具 schema 存在数据库里并在运行时动态执行任意逻辑

---

## 4.3 高风险 tool 必须加策略包装

有些 tool 不是“调用成功就好”，而是需要业务约束：

- 写数据库
- 发邮件/消息
- 下单/退款
- 执行命令
- 访问高敏数据
- 调用第三方付费接口

### 推荐
统一加一层 policy wrapper：
- 权限检查
- 限频
- 幂等
- 参数白名单
- 审计日志
- 超时与重试

### 最佳实践
即使 V1 不做人审，也应至少做：
- 调用前鉴权
- 调用后审计
- 风险动作默认关闭，按 agent 显式启用

---

## 5. Handoff 与 Agent-as-Tool 的使用边界

这是多 agent 设计里最容易混乱的地方。

## 5.1 什么时候用 handoff

适合“把当前会话交给另一个 agent 继续处理”的场景：

- 当前任务已经进入另一个领域
- 后续对话应由另一个 agent 主导
- 对方 agent 有不同 instructions、不同 model、不同 tool 集

### 典型场景
- root agent -> refund agent
- root agent -> report analysis agent
- support agent -> billing agent

---

## 5.2 什么时候用 agent-as-tool

适合“调用另一个 agent 完成一次子任务并返回结果”的场景：

- 一个复杂能力可以独立封装
- 不需要切换会话主导权
- 上层 agent 只需要子任务结果

### 典型场景
- 调用一个 SQL 生成 agent
- 调用一个合同抽取 agent
- 调用一个文档总结 agent

---

## 5.3 经验规则

- **会接管后续对话** -> 用 `handoff`
- **只做一次性任务** -> 用 `agent-as-tool`
- **纯系统动作** -> 用 `function tool`

### 不推荐
- handoff 图太深、太自由
- 所有 agent 相互 handoff
- skill 与 specialist 混用却没有明确规则

---

## 6. 编排最佳实践

## 6.1 混合编排比全自治更稳

SDK 支持模型驱动的工具选择与 handoff，也支持你用 Python 自己编排。  
生产上最推荐的是混合方式：

### 代码编排负责
- 入口选择哪个 root agent
- 哪些工具/能力对当前请求可见
- 是否允许高风险操作
- 超时、重试、降级
- 异步任务派发

### Agent 自治负责
- 当前上下文下是自己回答还是用 tool
- 在允许范围内选择 handoff
- 对工具结果做整合与表达

### 不推荐
- 把所有流程都交给 LLM 自行探索
- 用数据库配置完全替代代码层的控制边界

---

## 6.2 root agent 要做“路由+整合”，不要做“全能执行器”

最稳的 root agent 职责是：

1. 判断用户意图
2. 决定自己答、调用工具或 handoff
3. 收敛子能力结果
4. 统一最终输出风格

### 推荐
- root agent 工具尽量少
- specialist agent 自己持有领域工具
- root agent 主要暴露 handoff 和少量低风险工具

---

## 6.3 给 agent 设置合理的 turn 上限

SDK 运行时会在超过 `max_turns` 时抛出异常。  
因此最佳实践是：

- root agent 设较低 turn 上限
- specialist agent 根据任务复杂度设上限
- 对高成本/高风险任务使用更低上限

### 经验建议
- root agent：6 ~ 10
- specialist：8 ~ 15
- 长链复杂任务：单独走异步工作流，不要无限 turn

---

## 7. Session / Memory 最佳实践

## 7.1 把 session 当作短期会话记忆

SDK session 会自动维护多轮历史。  
在你的系统里，Redis session 非常适合作为短期 memory cache。

### 推荐
- session 只保存短期上下文
- 设定 TTL
- 长期知识与业务事实从数据库或业务系统读取
- 不把 session 当永久记忆库

### 不推荐
- 把关键业务状态只存在 session 中
- 不设 TTL
- 把所有历史原样无限累积

---

## 7.2 保留最近原始历史，更早内容改为摘要

随着轮次增加，历史会不断推高成本。  
最佳实践不是“保留全部”，而是：

- 最近几轮保留原始 items
- 更早内容压缩成摘要
- 大型工具结果只保留摘要或关键字段

### 推荐
- 最近 10~20 个 items 直接保留
- 更早历史做 summary item
- 大对象存业务存储，只在 session 中保留引用或摘要

---

## 7.3 非交互式任务不要过度依赖 session

你当前的核心场景是“请求进入 -> agent 自动执行 -> 返回结果”，这意味着很多任务本质上是短生命周期任务。  
因此：

- 对单次任务型请求，可以弱化 session
- 只在需要跨请求续跑时使用 session
- 异步任务状态更适合存数据库/任务表，而不是只放 session

---

## 8. Guardrails 最佳实践

SDK 提供 input guardrails、output guardrails，以及 function-tool 调用前后的 tool guardrails。

## 8.1 输入 guardrails 用来做早期拒绝

适合：
- 恶意输入识别
- 越权请求识别
- 高成本任务拦截
- 不支持的任务类型拦截

### 推荐
在昂贵模型调用前做轻量检查，尽早失败。

---

## 8.2 输出 guardrails 用来做最终质量与合规校验

适合：
- 输出格式校验
- 结构化结果完整性
- 敏感字段过滤
- 品质阈值检查

### 推荐
- 对结构化输出做 schema 校验
- 对对外文案做基础合规检查

---

## 8.3 tool guardrails 用来管危险动作

适合：
- 参数白名单
- 输出脱敏
- 外部动作审批
- 命令执行限制

### 推荐
把高风险动作的约束放在 tool 层，而不是只依赖 prompt。

---

## 9. 输出设计最佳实践

## 9.1 尽可能定义结构化输出

对于服务到服务的调用，最好的输出往往不是自然语言，而是：

- JSON
- Pydantic model
- 统一 response envelope

### 推荐输出格式
- `status`
- `result`
- `reason`
- `warnings`
- `trace_id`
- `tool_summary`

### 好处
- 前后端契约清晰
- 便于调试
- 便于写测试
- 便于接异步任务回查

---

## 9.2 区分“用户可见文本”和“系统消费结果”

一个成熟的 agent 不应只返回一句自然语言。  
更推荐双层输出：

- `display_text`：给用户/调用方展示
- `machine_result`：供程序后续处理
- `meta`：trace、tool、timing、warnings

---

## 10. 非交互式自动执行场景的最佳实践

结合你的业务场景，这部分非常关键。

## 10.1 将长耗时任务改成异步工作模式

如果任务可能涉及：
- 多次工具调用
- 外部系统 I/O
- 文件处理
- 代码执行
- 长文本分析

就不要都堵在同步 HTTP 请求里。

### 推荐模式
- FastAPI 接收请求
- 生成 task_id
- 通过 Celery 派发任务
- worker 内调用 Agents SDK
- 结果写回数据库或缓存
- 调用方轮询或订阅结果

### 同步只适合
- 简短问答
- 轻量查询
- 延迟敏感且工具调用少的任务

---

## 10.2 异步任务要记录“业务状态”而不是只记录 session

推荐至少有这些状态：
- `queued`
- `running`
- `waiting_external`
- `succeeded`
- `failed`
- `timed_out`

### 不推荐
- 只靠 Redis session 判断任务进度
- 只在 trace 里看执行过程，不在业务侧记录状态

---

## 10.3 避免在 worker 中构造过度动态的 agent 图

Celery worker 里更适合：
- 读取固定版本 agent 配置
- 构造稳定 agent
- 执行一次任务
- 记录 trace 与结果

不推荐在 worker 里临时生成大量不受控的 handoff 图和动态工具。

---

## 11. Tracing 与可观测性最佳实践

## 11.1 首期直接使用内建 tracing

SDK tracing 默认开启，适合你当前阶段。  
V1 不要过早自建复杂 tracing 平台。

### 推荐
- 在每次运行中附带 request_id / tenant_id / session_id
- 记录当前 agent 版本、prompt 版本、tool 版本
- 把 trace id 关联到业务任务记录

---

## 11.2 不要只看“最终答案”，要看执行链

Agent 调试最常见的误区是只看最终输出。  
实际上最重要的是看：

- 为什么选择了这个 tool
- 为什么触发了 handoff
- 哪一步失败
- 哪一步成本最高
- 哪个 agent 在反复兜圈

### 推荐追踪维度
- agent name / version
- model
- latency
- token usage
- tool calls
- handoff path
- final output
- exception
- max_turns exceed

---

## 11.3 为后续接 Langfuse 预留统一埋点字段

即使 V1 先只用 OpenAI tracing，也建议统一这些字段：

- `request_id`
- `session_id`
- `tenant_id`
- `user_id_hash`
- `agent_key`
- `agent_version`
- `task_id`
- `environment`
- `feature_flags`

这样后续切到 Langfuse 或并行接入时，关联成本最低。

---

## 12. Prompt 管理最佳实践

## 12.1 Prompt 要版本化，不要在线直接覆盖

即使首版本地硬编码 prompt，也要在代码层做版本标识。  
后续迁移到数据库或 prompt 平台时，才能顺滑过渡。

### 推荐
- 每个 agent 的 prompt 都有 `version`
- 改 prompt 先走测试，再发布
- trace 里记录 prompt version

---

## 12.2 拆分 prompt 槽位，而不是维护一大段文本

推荐把 prompt 拆成：
- identity
- scope
- tool usage
- handoff policy
- output style
- examples
- tenant override

这样更容易治理、A/B 测试和局部替换。

---

## 12.3 Prompt 负责行为描述，不负责系统真相

不要指望 prompt 成为“唯一可信数据源”。  
订单、权限、库存、配额、用户状态等真实信息应来自工具或业务系统。

---

## 13. 模型选择最佳实践

## 13.1 不同 agent 可以使用不同模型

这是多 agent 的一个重要优势：

- root agent 用更快、更便宜的模型做路由
- specialist agent 用更强模型处理复杂任务
- skill agent 根据任务选择合适模型

### 推荐
从成本和延迟出发分层，而不是所有 agent 都用同一个模型。

---

## 13.2 保留模型降级策略

当出现：
- 成本异常
- 配额问题
- 延迟超标
- 高峰期负载

应支持降级：

- 更便宜模型
- 更短上下文
- 降低工具使用频率
- 直接返回部分结果

---

## 14. E2B / 代码执行类能力的最佳实践

如果后续使用 E2B 或其他 sandbox 作为工具后端，建议遵循下面原则：

## 14.1 首发版本不要让其成为核心依赖

原因：
- 成本高
- 调试复杂
- 安全策略复杂
- 对超时、网络、文件生命周期要求更高

### 推荐
- 作为可选 skill/tool 挂载
- 仅对明确需要代码执行的 agent 开启
- 与普通 API 工具隔离

---

## 14.2 明确沙箱边界

必须限制：
- 可用镜像
- 运行时间
- 网络访问
- 文件体积
- 命令白名单
- 输出大小

---

## 14.3 所有代码执行结果都要摘要化后再回给上层 agent

不要把大段日志和原始终端输出直接塞回模型。  
应先做：
- 摘要
- 关键文件清单
- 退出码
- 标准输出截断
- 错误摘要

---

## 15. FastAPI + Celery + ECS 的工程最佳实践

## 15.1 API 层与 Agent 执行层职责分离

### FastAPI 层负责
- 请求接入
- 参数校验
- 鉴权
- task_id / session_id 生成
- 路由同步或异步执行

### Worker 层负责
- 构造 context
- 构造 agent
- 执行 run
- 写回结果
- 上报 trace / 日志 / metrics

---

## 15.2 Redis 职责要隔离

虽然都用 Redis，但不建议混在一起：

- session cache
- celery broker
- celery result backend
- distributed lock

### 推荐
至少按 logical DB / key prefix / 集群角色隔离，避免互相干扰。

---

## 15.3 分布式锁只用于协调，不用于保存业务状态

Redis 锁适合：
- 避免同一个任务重复执行
- 避免同一个 session 被并发污染
- 保护幂等动作

不适合：
- 保存长期执行状态
- 替代任务状态表

---

## 16. 测试最佳实践

## 16.1 把 agent 测试分成三层

### 第一层：tool 单元测试
- 输入输出
- 异常
- 超时
- 权限

### 第二层：agent 组合测试
- prompt + tool + handoff
- 固定输入的预期行为
- 是否触发正确 tool/handoff

### 第三层：端到端任务测试
- 从 API 到 worker 到最终结果
- 覆盖主业务场景
- 覆盖失败与降级路径

---

## 16.2 测试目标不要只盯“答案像不像”

还要检查：
- 是否调用了不该调用的 tool
- 是否发生了不必要的 handoff
- 是否超出 turn 上限
- 是否成本异常
- 是否返回了规定结构

---

## 17. 版本演进建议

## V1：轻量上线
- agent 本地硬编码
- prompt 本地版本化
- Redis session
- OpenAI 内建 tracing
- 少量 function tools
- 仅少量 handoff
- 不做复杂 skill 平台

## V2：配置化增强
- agent/tool/prompt 配置入库
- admin 可迭代 prompt
- agent release / rollback
- 更细粒度 tracing 标签
- skill registry

## V3：生产治理增强
- Langfuse
- A/B 实验
- 自动评测
- tool policy 平台化
- 审批流
- 沙箱执行治理
- 多租户配置隔离

---

## 18. 一份简明的落地 checklist

### 设计上
- [ ] 每个 agent 只有单一职责
- [ ] root / specialist / skill 分工清晰
- [ ] handoff 和 agent-as-tool 使用边界明确

### 工程上
- [ ] tool 输入输出 schema 清晰
- [ ] context 不进 prompt
- [ ] session 有 TTL
- [ ] 异步任务有状态表
- [ ] trace 绑定 request_id / agent_version

### 风险控制上
- [ ] 高风险 tool 有 policy wrapper
- [ ] 有 max_turns
- [ ] 有 timeout / retry / fallback
- [ ] Redis 职责隔离
- [ ] E2B 首发非强依赖

### 演进上
- [ ] prompt 版本化
- [ ] agent 配置可发布
- [ ] 为 Langfuse 预留统一 tracing tags

---

## 19. 总结

使用 OpenAI Agents SDK 设计 agent，最重要的最佳实践不是“把 agent 做得更聪明”，而是：

- **让职责更清楚**
- **让能力边界更明确**
- **让运行时更可控**
- **让 tracing 与版本更可追溯**
- **让系统先简单上线，再逐步增强**

对于你的场景，最推荐的路线是：

- V1 用少量硬编码 agent 快速上线
- 以 function tools 为主，少量 specialist handoff
- Redis 做短期 session
- Celery 承担长任务
- OpenAI 内建 tracing 先跑通
- 后续再把 prompt、tools、skills、handoff 逐步配置化并接 Langfuse

这条路线最稳，也最符合 Agents SDK 的设计哲学。
