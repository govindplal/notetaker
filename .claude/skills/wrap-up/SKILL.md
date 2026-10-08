---
name: wrap-up
description: End-of-session checklist - verify, review, record status for the next session, and summarize before committing. Use when the user says they're done or asks to wrap up.
disable-model-invocation: true
---

1. Run the `test-runner` agent; report real results.
2. Run the `code-reviewer` agent on the current diff.
3. List files changed and any TODOs left behind.
4. Rewrite `.claude/STATUS.md` so a fresh session can start without re-exploring: Now (what works, with the latest commit hash), Next (concrete next step first), Open questions, Learned. Keep it under ~40 lines and drop stale lines.
5. Save lasting facts that aren't in the repo (user preferences, decisions and their reasons) to auto memory. Skip anything code, git or CLAUDE.md already records.
6. If a lesson belongs permanently in CLAUDE.md (a command, a gotcha), propose the one-line addition.
7. Suggest a commit message (imperative, under 72 chars) but do not commit unless asked.
