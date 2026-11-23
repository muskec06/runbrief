#!/usr/bin/env python3
"""Status line: model | context % (colored) | cost. Reads Claude Code's status JSON on stdin."""
import json, os, sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("?"); sys.exit(0)
model = (d.get("model") or {}).get("display_name", "?")
cw = d.get("context_window") or {}
pct = cw.get("used_percentage")
warn = float(os.environ.get("CTX_WARN_PCT", "40"))
if pct is None:
    ctx = "ctx ?"
else:
    color = "\033[32m" if pct < warn else ("\033[33m" if pct < warn + 20 else "\033[31m")
    ctx = f"{color}ctx {pct:.0f}%\033[0m"
cost = (d.get("cost") or {}).get("total_cost_usd")
print(f"{model} | {ctx}" + (f" | ${cost:.2f}" if cost is not None else ""))
