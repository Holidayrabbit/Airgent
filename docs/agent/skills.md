# Skills

## 原理

Airgent 里的 skill 是“本地工作流文档”，不是独立 agent，也不是默认的 Python 插件系统。

每个 skill 本质上就是一个目录，加一个 `SKILL.md`：

```text
.agents/skills/<skill_key>/SKILL.md
```

agent 通过两个工具使用 skill：

- `list_skills`
- `load_skill`

执行逻辑：

1. agent 先用自己的 prompt 和 tools 处理任务
2. 如果觉得某类工作流可能有现成套路，就调用 `list_skills`
3. 系统扫描 `skills_root/*/SKILL.md`
4. 返回 skill 名称和简短描述
5. agent 选择一个 skill，再调用 `load_skill`
6. 系统读取完整的 `SKILL.md`
7. agent 按这个工作流继续执行

所以 skill 的作用是：

- 按需加载额外流程说明
- 避免把所有领域规则都塞进基础 prompt
- 让项目级规范通过文件沉淀，而不是写死在代码里

默认 skills 目录来自 `skills_root`，未显式配置时等于：

```text
<project_root>/.agents/skills
```

## 新建 Skill

新建目录：

```text
.agents/skills/release-checklist/
```

再创建文件：

```text
.agents/skills/release-checklist/SKILL.md
```

建议格式：

```md
---
name: release-checklist
description: Use this when preparing a release or validating release readiness.
---

# Release Checklist

## When to Activate

Describe when the skill should be used.

## Workflow

1. Check changed files.
2. Verify version references.
3. Verify changelog updates.
4. Summarize release risks.
```

注意两点：

- `list_skills` 的描述来自 `SKILL.md` 顶部内容，所以前几行要写清楚
- skill 只是文档，系统不会自动执行其中步骤，仍然由 agent 自己理解并调用工具

## 让 Agent 能使用 Skill

目标 agent 的 YAML 必须启用这两个工具：

```yaml
tools:
  - list_skills
  - load_skill
```

没有这两个 tool，agent 看不到也加载不了任何 skill。

## 适合放进 Skill 的内容

适合：

- 可复用工作流
- 项目约定
- 检查清单
- 某类任务的操作步骤

不适合：

- 永久生效的 agent 身份设定
- 秘钥、账号等敏感信息
- 需要结构化输入输出的动作能力

判断方法：

- 一直都要生效的，放 `instructions`
- 只在特定任务需要的流程，放 skill
- 要真的执行动作的，做成 tool

## 排查

skill 不显示时检查：

1. 路径是否为 `.agents/skills/<skill_key>/SKILL.md`
2. agent 是否启用了 `list_skills`
3. 当前运行目录对应的 `project_root` 是否正确

skill 加载失败时检查：

1. `skill_key` 和目录名是否一致
2. agent 是否启用了 `load_skill`
3. `SKILL.md` 是否存在

## 限制

- skill 是 markdown 文档，不是可执行模块
- skill 发现依赖文件系统扫描
- 当前没有独立的 skill router，是否加载 skill 由 agent 自己判断
