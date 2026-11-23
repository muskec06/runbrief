# Changelog

## 1.0.0 - 2026-11-23
- First release: `run`, `grep`, `show`, `stats`, `clean`, `install`.
- Root-cause-first briefs: dedup with repeat counts, cascade lines separated, C++ template errors
  mapped to the user source line, Python tracebacks reduced to the user frame, test totals.
- Rare-line hint for unknown error formats; project patterns in `.runbrief/patterns.txt`.
- Log policy: 100 MB per-log cap with head/tail trimming, 1 ok + 5 failed logs per name,
  gzip for older logs, 300 MB folder cap.
- Timeouts kill the whole process tree (Linux, macOS, Windows).
- Integrations: AGENTS.md, claude.ai skill, Claude Code hooks, copy-paste prompts.
