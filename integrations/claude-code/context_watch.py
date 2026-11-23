#!/usr/bin/env python3
"""UserPromptSubmit hook: context-size watchdog.

Reads the session transcript, takes the token usage of the latest assistant
turn (input + cache read + cache creation = current context size), and when it
crosses CTX_WARN_PCT it adds a short note to Claude's context asking it to
write a handoff note and suggest /compact (or /clear) to the user.

Fires once per 10% bucket per session, so it does not repeat every turn.
Fallback for CLAUDE_AUTOCOMPACT_PCT_OVERRIDE, which is unreliable
in several Claude Code versions.

Env: CTX_WINDOW (default 200000), CTX_WARN_PCT (default 40)
"""
import json, os, sys, tempfile

def context_tokens(path):
    last = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    msg = json.loads(line).get("message") or {}
                except Exception:
                    continue
                u = msg.get("usage")
                if u:
                    last = u
    except OSError:
        return None
    if not last:
        return None
    return (last.get("input_tokens", 0) + last.get("cache_read_input_tokens", 0)
            + last.get("cache_creation_input_tokens", 0))

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    tokens = context_tokens(data.get("transcript_path", ""))
    if not tokens:
        return 0
    window = int(os.environ.get("CTX_WINDOW", "200000"))
    warn = int(os.environ.get("CTX_WARN_PCT", "40"))
    pct = 100 * tokens / window
    if pct < warn:
        return 0
    bucket = int(pct // 10)
    state = os.path.join(tempfile.gettempdir(), f"ctxwatch-{data.get('session_id', 'x')}")
    try:
        if int(open(state).read() or -1) >= bucket:
            return 0
    except (OSError, ValueError):
        pass
    try:
        open(state, "w").write(str(bucket))
    except OSError:
        pass
    print(f"[context-watch] Context is ~{pct:.0f}% ({tokens:,}/{window:,} tokens). "
          "After answering this message: update HANDOFF.md (goal, decisions, "
          "files touched, current error, next step; <=40 lines) and tell the user in one "
          "line that this is a good moment for /compact (same task) or /clear (new task).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
