---
name: start-feature
description: Plan and implement a new feature step by step. Use when the user asks to add or build a feature.
---

1. Restate the goal and acceptance criteria in 2-4 bullets; ask only if something blocks you.
2. Use the `explorer` agent to find the relevant code and existing patterns.
3. Propose a short plan (files to touch, tests to add). Wait for approval if the change is large or touches public APIs.
4. Implement in small steps, writing tests alongside.
5. Run the `test-runner` agent, then the `code-reviewer` agent. Fix real findings.
6. Summarize what changed and anything left undone.
