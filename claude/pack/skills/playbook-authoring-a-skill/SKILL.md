---
name: playbook-authoring-a-skill
description: "The Authoring or modifying a skill playbook of poteto-mode, loaded through the Skill tool so its steps survive compaction."
---

The Authoring or modifying a skill playbook, from .claude/skills/poteto-mode/playbooks/authoring-a-skill.md. Relative paths below resolve under .claude/skills/poteto-mode/.

### Authoring or modifying a skill

**You own the skill's voice.**

1. Use the **skill-creator** skill (Anthropic's skill for authoring SKILL.md files, from the `anthropics/skills` repository, installed as a plugin or a synced skill) when it is installed. Otherwise follow the Claude Code skills reference (https://code.claude.com/docs/en/skills) and check the result with `claude plugin validate .claude/skills`.
2. Validate the skill: frontmatter has `name` and `description`, referenced files exist, cross-skill links resolve.
3. Test cases if structural. Skip if subjective.
4. Run **Opening a PR**.

When in doubt, delete. Keep only prose that changes a decision. Tell it to do the thing and skip the reason. Explain only when the rule is confusing without one. Match tone to scope. Point at structural sources (types, READMEs, config) per the **encode-lessons-in-structure** principle skill. Delegate to other skills by path. Don't restate. A workflow you keep hitting but isn't captured → propose a new skill.

**Reply:** summary of the skill, key design decisions, validation notes.
