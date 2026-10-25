# Skills

Skills are discovered natively by the pi kernel:

- Project skills: `.agents/skills/` in the workspace (scanned recursively for `SKILL.md`, from the working directory up to the repository root)
- User skills: `~/.agents/skills/`

Only the name and description of each skill enter the system prompt; the model reads `SKILL.md` on demand when a task matches. See the [Agent Skills spec](https://agentskills.io) for the SKILL.md format (frontmatter `name`/`description`, optional `scripts/`, `references/`, `assets/`).
