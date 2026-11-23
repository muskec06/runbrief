# Changelog

## 1.1.0 - 2026-11-23
- `runbrief stats` is now a dashboard: would-have-read vs actually-read, net saving bar,
  per-command share of savings, recent runs; ASCII fallback (`RUNBRIEF_ASCII=1`).
- `runbrief stats --svg FILE` writes a shareable SVG card; `--since HOURS` filters.
- Honest counting: each run's raw output is counted up to 25,000 tokens (an agent's tool-output
  limit; `RUNBRIEF_STATS_CAP_TOKENS`), uncapped total still shown. Follow-up reads are charged
  to the command they inspected.
- Docs: "How it works" diagrams (log flow, rule precedence) in both READMEs; a real, step-by-step
  skill session with every command and output in `docs/skill-example.md` (+ Turkish version).
- README: "Using the skill" section (install, when it loads, costs) with a table of stats requests
  in a skill session and a real stats card; skill and AGENTS.md handle stats requests (`--svg`
  card sent as a file, `--since`, offer to save totals in HANDOFF.md).
- AGENTS.md: project rules come first; contributor note; logs survive context compaction;
  HANDOFF.md records the current error as `name Lline`.
- Includes all 1.0.1 changes below (1.0.1 was not released separately).

## 1.0.1 - 2026-11-23 (unreleased, folded into 1.1.0)
- Fix: long file paths (e.g. macOS temp folders) no longer cut off the `file:line in function`
  part of an error; paths inside the working directory are shown relative, long outside paths
  as `.../last/parts` (also makes briefs shorter: CMake example 163 -> 149 tokens).
- Linux tools: apt/dpkg, npm, git `fatal:`, `configure: error`, `make: *** No rule to make target`
  (root cause, not cascade), shell errors (`command not found`, `Permission denied`, ...),
  valgrind, sanitizers, kernel `BUG:`/`Oops:`/`error -NN`, systemd `Failed to start`.
- `FATAL`/`PANIC`/`FAULT`/`Aborted` are matched case-sensitively and not as file names.
- New `-H N`: print the first N lines of a big output that is itself the answer.
- Success briefs say how to read the hidden output.
- `.runbrief/` writes its own `.gitignore` so logs and stats are never committed.
- Claude Code guard: apt/dnf/conda installs, `./configure`, `docker build`, kernel module builds.
- AGENTS.md / skill: which commands to wrap and which to limit natively, joining a long
  conversation, recovery after a workspace reset or context summary.
- Prompts pin a release tag.
- CI: Python 3.10 / 3.12 / 3.13 (3.9 is no longer available on the latest runners).

## 1.0.0 - 2026-11-23
- First release: `run`, `grep`, `show`, `stats`, `clean`, `install`.
- Root-cause-first briefs: dedup with repeat counts, cascade lines separated, C++ template errors
  mapped to the user source line, Python tracebacks reduced to the user frame, test totals.
- Rare-line hint for unknown error formats; project patterns in `.runbrief/patterns.txt`.
- Log policy: 100 MB per-log cap with head/tail trimming, 1 ok + 5 failed logs per name,
  gzip for older logs, 300 MB folder cap.
- Timeouts kill the whole process tree (Linux, macOS, Windows).
- Integrations: AGENTS.md, claude.ai skill, Claude Code hooks, copy-paste prompts.
