# Skills

Skills are discovered natively by the pi kernel:

- Project skills: `.agents/skills/` in the workspace (scanned recursively for `SKILL.md`, from the working directory up to the repository root)
- User skills: `~/.agents/skills/`

Only the name and description of each skill enter the system prompt; the model reads `SKILL.md` on demand when a task matches. See the [Agent Skills spec](https://agentskills.io) for the SKILL.md format (frontmatter `name`/`description`, optional `scripts/`, `references/`, `assets/`).

## Skill registry (opt-in)

By default Airgent stays out of the way: pi's native scan decides everything.
Set `AIRGENT_SKILLS_REGISTRY=1` to add per-skill kill switches on top:

```bash
airgent skills list            # what was discovered (project / user / extra)
airgent skills disable brave-search
airgent skills enable brave-search
airgent skills mode deny_all   # default-deny; enable only what you list
airgent skills add-dir ~/more-skills
```

- State lives in `~/.airgent/skills.json` (`mode` / `enabled` / `disabled`).
- With the registry enabled and something actually filtered, Airgent writes a
  `skills` whitelist into the isolated pi agent dir's `settings.json`
  (`~/.airgent/pi/agent/settings.json`); your own `~/.pi` is never touched.
- The same control is available over HTTP: `GET /api/v1/skills`,
  `POST /api/v1/skills/{name}/enable|disable`.

The registry only controls *which skills load*. It is not a security
boundary — treat it as prompt hygiene, and use the sandbox/guardrails for
hard isolation.

## Installing skill packages

```bash
airgent skills install https://github.com/badlogic/pi-skills
airgent skills install https://github.com/anthropics/skills --name anthropic
airgent skills list
airgent skills remove pi-skills
```

- Shallow clone (`git clone --depth 1`) into `~/.airgent/skills/<name>`;
  name defaults to the repo stem.
- Both single-skill repos (a `SKILL.md` at the root) and collections
  (skill subdirectories) work — the install dir is scanned recursively.
- Installed skills load through pi's repeatable `--skill <path>` flag, so
  they work regardless of the registry; review what you install, since a
  skill can instruct the model to do anything.

