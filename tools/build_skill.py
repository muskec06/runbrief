#!/usr/bin/env python3
"""Build dist/runbrief-skill.zip for claude.ai (Customize > Skills > Upload a skill)."""
import os, shutil, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.join(ROOT, "dist", "runbrief-skill.zip")
os.makedirs(os.path.dirname(out), exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(os.path.join(ROOT, "skill", "runbrief", "SKILL.md"), "runbrief/SKILL.md")
    z.write(os.path.join(ROOT, "runbrief.py"), "runbrief/scripts/runbrief.py")
print(out)
