## runbrief (token economy for build / test / simulation output)

- Run builds, tests, linters, installs, flashing, emulators and simulators through runbrief:
  `python3 .claude/runbrief/runbrief.py -n <build|test|sim|emu|drv> -- "<command>"` (`-T <sec>` for never-ending programs).
  Do not wrap commands whose output is the answer (cat, git diff, ls, result-printing scripts).
- Fix from the brief, FIRST error first; `build-system:` lines are consequences.
- Drill down only when needed: `... runbrief.py grep 'REGEX' -n <name> -C 3` / `... show -n <name> -L <line> -C 20`.
  Never read whole logs or build/, out/, obj/, .runbrief/logs/.
- Unknown error format: add a regex line to `.runbrief/patterns.txt` and mention it once.
- Rebuild narrowly; read source with offset/limit around the reported line.
- Keep HANDOFF.md (<=40 lines: goal, decisions, files changed, current error, next step) current at milestones;
  after /clear or /compact read it first.
