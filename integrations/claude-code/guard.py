#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash).

Blocks known-noisy commands (builds, tests, flashing, simulators) when they
are run raw, and tells Claude to rerun them through runbrief.py. Blocking with a
reason is reliable across Claude Code versions (unlike rewriting the command).

Allowed through:
  * commands that already use runbrief
  * commands whose output is already limited (| tail, | head, | grep -m, > file)
  * commands prefixed with RAW=1  (explicit opt-out, e.g. interactive tools)
Extend NOISY via the env var RUNBRIEF_NOISY_EXTRA (a regex).
"""
import json, os, re, sys

NOISY = [
    r"\bmake\b", r"\bninja\b", r"\bcmake\s+--build\b", r"\bcmake\s+(-S|-B|\.)",
    r"\bmsbuild\b", r"\bdotnet\s+(build|test|publish|run)\b", r"\bnuget\s+restore\b",
    r"\b(gcc|g\+\+|clang\+?\+?|cc|c\+\+|cl(\.exe)?|arm-none-eabi-\w+)\s", r"\bscons\b",
    r"\bwest\s+(build|flash)\b", r"\bidf\.py\b", r"\bplatformio\b|\bpio\s+run\b",
    r"\bpytest\b", r"\bpython3?\s+-m\s+(pytest|unittest)\b", r"\bctest\b", r"\btox\b",
    r"\bpip3?\s+install\b", r"\bnpm\s+(install|ci|run\s+build)\b",
    r"\bcargo\s+(build|test)\b", r"\bqemu-system-\w+", r"\brenode\b",
    r"\bmeson\s+(compile|test)\b", r"\bbazel\s+(build|test)\b", r"\bgradlew?\b",
]
extra = os.environ.get("RUNBRIEF_NOISY_EXTRA")
if extra:
    NOISY.append(extra)
NOISY_RE = re.compile("|".join(NOISY))
LIMITED_RE = re.compile(r"\|\s*(tail|head)\b|\|\s*grep\b.*-m\s*\d|\|\s*wc\b|>\s*\S+\.(log|txt)\b|&>\s*\S")

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    cmd = (data.get("tool_input") or {}).get("command", "")
    # ignore quoted text (commit messages, echo strings) and git commands
    bare = re.sub(r"'[^']*'|\"[^\"]*\"", "''", cmd)
    if re.match(r"\s*git\b", bare):
        return 0
    if (not cmd or "runbrief" in cmd or cmd.lstrip().startswith("RAW=1")
            or LIMITED_RE.search(bare) or not NOISY_RE.search(bare)):
        return 0
    rb = os.environ.get("RUNBRIEF_CMD", "python3 .claude/runbrief/runbrief.py")
    reason = (
        "runbrief: this command produces long output. Rerun it through runbrief so the full "
        f"output goes to a log file and only a brief comes back:\n  {rb} -n <name> -- \"{cmd}\"\n"
        f"Then dig into the log only if needed: {rb} grep 'REGEX' -n <name>. "
        "If you truly need raw output (interactive/short), prefix the command with RAW=1."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
