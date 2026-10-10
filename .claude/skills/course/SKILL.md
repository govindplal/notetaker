---
name: course
description: Show where the course notes stand (worker, each course and lecture) and what to do next. Use at the start of a session or when the owner asks about progress.
---

# /course: where the course stands

Run from the notetaker project root. Gather facts, then answer. Do not start the worker and do not
record anything.

1. Read `.claude/STATUS.md` for decisions and open items.
2. Run `uv run python -m notetaker status` for the live state: worker running or stopped, what it
   is doing, each lecture's status, and any failure or wait reason.
3. Check the notes are readable: `data/notes/course-<id>/index.html` exists for each course.

Reply in this order, short:
- Worker: running, stopping, or stopped, and why it last stopped (finished, stopped, or Claude usage
  limit with the reset time if known).
- Progress: lectures done of total, and the lecture in progress with how far it got.
- Problems: any failed lecture (with the error), any course paused for sign-in, any wait.
- Next step: the one action the owner should take, for example "press Start in the control page"
  (`uv run python -m notetaker ui`, then http://127.0.0.1:8765), or "sign in in the recorder browser".

Never run `run`, `redo` or `retry` from this skill. Those change state, and the owner decides.
