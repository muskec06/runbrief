#!/usr/bin/env python3
"""Install runbrief into a Claude Code project.

  python3 integrations/claude-code/install.py /path/to/project [--windows]

Copies runbrief.py + hooks to <project>/.claude/runbrief/, merges hooks, permissions,
env and statusLine into <project>/.claude/settings.json (existing settings kept, .bak made),
appends the rules to CLAUDE.md once, creates .runbrief/patterns.txt, updates .gitignore.
"""
import json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MARK = "## runbrief (token economy"

def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict):
            merge(dst.setdefault(k, {}), v)
        elif isinstance(v, list):
            cur = dst.setdefault(k, [])
            cur.extend(x for x in v if x not in cur)
        else:
            dst.setdefault(k, v)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    proj = os.path.abspath(args[0])
    py = "python" if "--windows" in sys.argv else "python3"
    dst = os.path.join(proj, ".claude", "runbrief")
    os.makedirs(dst, exist_ok=True)
    shutil.copy2(os.path.join(ROOT, "runbrief.py"), dst)
    for f in ("guard.py", "context_watch.py", "statusline.py"):
        shutil.copy2(os.path.join(HERE, f), dst)

    tpl = json.loads(open(os.path.join(HERE, "settings.template.json"), encoding="utf-8").read()
                     .replace("python3 ", py + " "))
    sp = os.path.join(proj, ".claude", "settings.json")
    cur = {}
    if os.path.exists(sp):
        shutil.copy2(sp, sp + ".bak")
        cur = json.load(open(sp, encoding="utf-8"))
    merge(cur, tpl)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2)

    md = os.path.join(proj, "CLAUDE.md")
    rules = open(os.path.join(HERE, "CLAUDE.snippet.md"), encoding="utf-8").read().replace("python3 ", py + " ")
    old = open(md, encoding="utf-8").read() if os.path.exists(md) else ""
    if MARK not in old:
        with open(md, "a", encoding="utf-8") as f:
            f.write(("\n\n" if old else "") + rules)

    pat = os.path.join(proj, ".runbrief", "patterns.txt")
    if not os.path.exists(pat):
        os.makedirs(os.path.dirname(pat), exist_ok=True)
        with open(pat, "w") as f:
            f.write("# Project-specific error patterns for runbrief, one regex per line.\n"
                    "# Prefix with W: for warnings. Examples:\n# \\[EMU\\] FAULT\n# W: \\[SIM\\] slow path\n")
    gi = os.path.join(proj, ".gitignore")
    g = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    if ".runbrief/logs" not in g:
        with open(gi, "a", encoding="utf-8") as f:
            f.write("\n.runbrief/logs/\n.runbrief/stats.jsonl\n")
    print(f"runbrief installed in {proj}. Commit .claude/, CLAUDE.md, .runbrief/patterns.txt, .gitignore;"
          " restart Claude Code.")

if __name__ == "__main__":
    main()
