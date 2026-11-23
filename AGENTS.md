# runbrief - instructions for AI agents

You were pointed here to save context tokens while building, testing or running
simulators. Follow these rules for the rest of the session. This file is all you need:
do not read runbrief.py, README or other repo files (that would cost more tokens than it saves).

> Working **on** the runbrief repository itself (asked to change or fix it)? Then this file does not
> apply: read the code and tests normally. It is meant for agents that *use* runbrief in another project.

## Setup (once per session, ~1 second)

```bash
python3 <path-to>/runbrief.py install     # puts `runbrief` on PATH; prints where
```
If install reports no writable PATH dir, use `python3 <path-to>/runbrief.py` wherever this file says `runbrief`.
On Windows use `python` instead of `python3`.
If `runbrief` is later "not found" (workspace reset), repeat the clone/setup step silently.

## Which commands to wrap

| Wrap with `runbrief -n <name> -- "<cmd>"` | Do NOT wrap: limit them natively |
|---|---|
| builds: make, cmake --build, ninja, msbuild, dotnet, gcc/clang, cargo, kernel `make -C /lib/modules/...` | output that *is* the answer: cat, ls, tree, git diff/log/show, env, ps |
| tests: pytest, ctest, dotnet test, npm test | searches: `grep -rn -m 50`, `rg -m 50`, `find -maxdepth 3 ... \| head -100` |
| installs: apt/apt-get, dnf, pip, npm, conda, ./configure | system logs: `journalctl -p err -n 100 --no-pager`, `dmesg --level=err,warn \| tail -100` |
| emulators/simulators/flashing: qemu, renode, west, idf.py, your sim (`-T <sec>`) | a small script printing a result |
| docker build, long scripts, valgrind/sanitizer runs | |

Big output you must actually read: `runbrief -n x -H 40 -- "<cmd>"` (shows the first 40 lines,
keeps the rest in the log). Tiny outputs (< 40 lines) are always shown in full.

## Project rules come first

If the project has its own `AGENTS.md` / `CLAUDE.md`, follow it; these rules only add *how* to
run noisy commands. If a project rule demands full output (e.g. "paste the whole build log"),
the project rule wins for that command.

## Rules

1. **Fix from the brief.** It lists root-cause errors in first-seen order, with `[xN]`
   repeat counts, the user source line for C++ template errors, the user frame for Python
   tracebacks, and test totals. Fix the FIRST error first; `build-system:` lines are consequences.
2. **Drill down only when needed, only where needed:**
   `runbrief grep 'REGEX' -n <name> -C 3` or `runbrief show -n <name> -L <line> -C 20`.
   Never `cat`/Read a whole log or files under build/, out/, obj/, .runbrief/.
3. **Unknown error format?** If the brief says "no known error pattern" and the rare-lines
   hint is not enough, add one regex line to `.runbrief/patterns.txt` (e.g. `\[EMU\] FAULT`)
   and tell the user in one line.
4. **Rebuild narrowly** (one target, one test file) and read source with offset/limit
   around the reported line.
5. **Keep the conversation small.** Maintain `HANDOFF.md` (<= 40 lines). Its first line is
   always: `Session uses runbrief: follow <path-to>/AGENTS.md`. Then: goal, decisions, files
   changed, current error with its runbrief name and line (e.g. `build L18`), next step. When the session is long or a task is done, say in one
   line that this is a good point for a new session / `/compact`, and share HANDOFF.md.
6. **After a context summary/compaction**, if you are unsure of these rules, re-read this
   file (it is short). Logs live on disk, not in the context, so they survive compaction:
   `runbrief show -n <name> -L <line>` still works. In a new session read HANDOFF.md first.
7. **Report briefly.** Do not paste logs or whole files back to the user; cite `file:line`.

## Joining a conversation that is already long

Savings start from now; earlier output stays in the context until it is compacted or the
user starts a new chat. Write HANDOFF.md right away and tell the user in one line that a
new chat (with HANDOFF.md) would reclaim the space.

## Statistics (only when the user asks)
- "How much did runbrief save?" -> `runbrief stats` (about 320 tokens of output); add `--since <hours>`
  for "this task" / "today"; `--json` for raw numbers; `--reset` to start over.
- "Make a stats card / picture" -> `runbrief stats --svg runbrief-stats.svg`, then send that file to
  the user. Do not read the SVG back into the conversation.
- Before the user leaves a long chat, offer once to put the `runbrief stats` totals into HANDOFF.md;
  statistics live in this workspace's `.runbrief/` and are lost when it is reset.
