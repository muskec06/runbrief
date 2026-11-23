---
name: runbrief
description: Use when building, compiling, testing, linting, installing packages, flashing or running emulators/simulators/long scripts (C/C++, C#, Python, Rust, JS, Linux tools) in the code environment, so long command output goes to a log and only a root-cause brief enters the conversation.
---

# runbrief

Run noisy commands through the bundled `scripts/runbrief.py`; the full output is
logged under `./.runbrief/logs/` and only a compact brief comes back.
This file is all you need: run the script, do not read its source (~8k tokens).

## Setup (once per conversation; again if `runbrief` is "not found")
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/runbrief.py install
```
If `${CLAUDE_SKILL_DIR}` is empty, locate the folder containing this SKILL.md and use
its `scripts/runbrief.py`. If install finds no writable PATH dir, call the script with
`python3 <path>/runbrief.py` instead of `runbrief`.

## What to wrap
- Wrap: builds (make, cmake --build, ninja, msbuild, dotnet, gcc/clang, cargo, kernel modules),
  tests, package installs (apt, dnf, pip, npm, conda, ./configure), docker build, valgrind and
  sanitizer runs, emulators/simulators/flashing (add `-T <seconds>` if they never exit):
  `runbrief -n <build|test|sim|emu|drv|lint> -- "<command>"`
- Do not wrap output that is the answer (cat, ls, git diff/log, env, ps): limit it natively
  (`| head -100`, `grep -m 50`, `find -maxdepth 3`, `journalctl -p err -n 100 --no-pager`,
  `dmesg --level=err,warn | tail -100`). Big output you must read: `runbrief -n x -H 40 -- "<cmd>"`.

## Rules
1. Fix from the brief: root-cause errors in first-seen order, `[xN]` repeats, user source
   line for C++ template errors, user frame for Python tracebacks, test totals.
   Fix the FIRST error first; `build-system:` lines are consequences.
2. Drill down only when needed: `runbrief grep 'REGEX' -n <name> -C 3`,
   `runbrief show -n <name> -L <line> -C 20`. Never read whole logs or build/, out/, obj/.
3. "no known error pattern" and the rare-lines hint is not enough: add a regex line to
   `.runbrief/patterns.txt` (e.g. `\[EMU\] FAULT`) and mention it once.
4. Rebuild narrowly; read source with offset/limit around the reported line.
5. Keep `HANDOFF.md` (<=40 lines; first line "Session uses runbrief"; then goal, decisions,
   files changed, current error, next step). When the conversation is long or a task is done,
   say in one line it is a good point for a new chat and send HANDOFF.md; in a new chat read it first.
6. Joining a long conversation: savings start now; write HANDOFF.md and mention once that a new
   chat would reclaim the space.
7. Report briefly; cite file:line instead of pasting logs or code.

## Statistics (only when the user asks)
- "How much did runbrief save?" -> `runbrief stats` (about 320 tokens of output); add `--since <hours>`
  for "this task" / "today"; `--json` for raw numbers; `--reset` to start over.
- "Make a stats card / picture" -> `runbrief stats --svg runbrief-stats.svg`, then send that file to
  the user. Do not read the SVG back into the conversation.
- Before the user leaves a long chat, offer once to put the `runbrief stats` totals into HANDOFF.md;
  statistics live in this workspace's `.runbrief/` and are lost when it is reset.
