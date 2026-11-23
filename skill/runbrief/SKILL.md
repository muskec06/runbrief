---
name: runbrief
description: Use when building, compiling, testing, linting, flashing or running emulators/simulators/long scripts (C/C++, C#, Python, Rust, JS...) in the code environment, so long command output goes to a log and only a root-cause brief enters the conversation.
---

# runbrief

Run noisy commands through the bundled `scripts/runbrief.py`; the full output is
logged under `./.runbrief/logs/` and only a compact brief comes back.
This file is all you need: run the script, do not read its source (~8k tokens).

## Setup (once per conversation)
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/runbrief.py install
```
If `${CLAUDE_SKILL_DIR}` is empty, locate the folder containing this SKILL.md and use
its `scripts/runbrief.py`. If install finds no writable PATH dir, call the script with
`python3 <path>/runbrief.py` instead of `runbrief`.

## Rules
1. Builds, tests, linters, installs, flashing, emulators, simulators, long scripts:
   `runbrief -n <build|test|sim|emu|drv|lint> -- "<command>"`; never-ending programs get `-T <seconds>`.
   Do not wrap commands whose output is the answer (cat, git diff, ls, a result-printing script).
2. Fix from the brief: root-cause errors in first-seen order, `[xN]` repeats, user source
   line for C++ template errors, user frame for Python tracebacks, test totals.
   Fix the FIRST error first; `build-system:` lines are consequences.
3. Drill down only when needed: `runbrief grep 'REGEX' -n <name> -C 3`,
   `runbrief show -n <name> -L <line> -C 20`. Never read whole logs or build/, out/, obj/.
4. "no known error pattern" and the rare-lines hint is not enough: add a regex line to
   `.runbrief/patterns.txt` (e.g. `\[EMU\] FAULT`) and mention it once.
5. Rebuild narrowly; read source with offset/limit around the reported line.
6. Keep `HANDOFF.md` (<=40 lines: goal, decisions, files changed, current error, next step).
   When the conversation is long or a task is done, say in one line it is a good point for a
   new chat and send HANDOFF.md; in a new chat read it first.
7. Report briefly; cite file:line instead of pasting logs or code.

`runbrief stats` reports tokens saved, only when the user asks.
