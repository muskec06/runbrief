# runbrief - instructions for AI agents

You were pointed here to save context tokens while building, testing or running
simulators. Follow these rules for the rest of the session. This file is all you need:
do not read runbrief.py, README or other repo files (that would cost more tokens than it saves).

## Setup (once per session, ~1 second)

```bash
python3 <path-to>/runbrief.py install     # puts `runbrief` on PATH; prints where
```
If install reports no writable PATH dir, use `python3 <path-to>/runbrief.py` wherever this file says `runbrief`.
On Windows use `python` instead of `python3`.

## Rules

1. **Noisy commands go through runbrief.** Builds, tests, linters, package installs,
   flashing, emulators, simulators, long scripts:
   `runbrief -n <build|test|sim|emu|drv|lint> -- "<command>"`
   Never-ending programs (emulators, servers): add `-T <seconds>`.
   Do NOT wrap commands whose output *is* the answer you need (`cat`, `git diff`,
   `ls`, a script printing a result) - tiny outputs are passed through anyway.
2. **Fix from the brief.** It lists root-cause errors in first-seen order, with
   `[xN]` repeat counts, the user source line for C++ template errors, the user
   frame for Python tracebacks, and test-runner totals. Fix the FIRST error first;
   `build-system:` lines are consequences, not causes.
3. **Drill down only when needed, only where needed:**
   `runbrief grep 'REGEX' -n <name> -C 3` or `runbrief show -n <name> -L <line> -C 20`.
   Never `cat`/Read a whole log or files under build/, out/, obj/, .runbrief/.
4. **Unknown error format?** If the brief says "no known error pattern" and the
   rare-lines hint is not enough, add one regex line to `.runbrief/patterns.txt`
   (e.g. `\[EMU\] FAULT`) and tell the user in one line.
5. **Rebuild narrowly** (one target, one test file) and read source with
   offset/limit around the reported line.
6. **Keep the conversation small.** Maintain `HANDOFF.md` (<= 40 lines: goal,
   decisions, files changed, current error, next step). When the session is long
   or a task is done, say in one line that this is a good point for a new
   session / `/compact`, and share HANDOFF.md. New session: read it first.
7. **Report briefly.** Do not paste logs or whole files back to the user; cite `file:line`.

`runbrief stats` shows the tokens saved so far (on request; don't run it routinely).
