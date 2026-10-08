---
name: fix-bug
description: Reproduce, diagnose, and fix a bug with a regression test. Use when the user reports an error, failing test, or unexpected behavior.
---

1. Reproduce first: get the exact error or a failing test. If you can't reproduce, say so.
2. Find the root cause (not just the symptom); cite file:line.
3. Write a failing regression test that captures the bug.
4. Make the minimal fix; confirm the new test and the existing suite pass.
5. Report the cause, the fix, and the test added.
