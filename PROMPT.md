# Copy-paste prompts

## A. Claude in the browser / app (cloud chat with code execution), repo on GitHub

Paste as the first message of a new chat, then describe the task on the next line:

```text
Before anything else: git clone --depth 1 https://github.com/muskec06/runbrief /tmp/runbrief
and follow /tmp/runbrief/AGENTS.md for this whole conversation (run its setup step).
If cloning fails, tell me and I will attach runbrief.py.
Task:
```

## B. Same, without GitHub (attach the file)

Attach `runbrief.py` and `AGENTS.md` (or the whole repo zip), then:

```text
Use the attached runbrief.py and follow AGENTS.md for this whole conversation
(run its setup step first). Task:
```

## C. Claude skill (no prompt needed afterwards)

Upload `dist/runbrief-skill.zip` once: Customize > Skills > + > Create skill > Upload a skill
(code execution must be enabled). In each chat you can then just say:

```text
Use the runbrief skill. Task:
```

## D. Claude Code (CLI, desktop, web) - permanent per project

```bash
python3 integrations/claude-code/install.py /path/to/project
```
Commit the generated `.claude/` folder, `CLAUDE.md` additions and `.runbrief/patterns.txt`.
Hooks then enforce the rules; no prompt needed.

## E. Other agents (Cursor, Codex, Aider, Copilot agent, ...)

Copy `runbrief.py` into the repo (e.g. `tools/runbrief.py`) and append `AGENTS.md`
to the agent's rules file (`AGENTS.md`, `.cursorrules`, `CONVENTIONS.md`, ...).

## Moving a long conversation to a new one

Send this in the old conversation, then paste the result into the new one after prompt A/B/C:

```text
Write HANDOFF.md (max 40 lines): goal, decisions made, files changed, current error or
state, next step. No code or logs. Send it as a file.
```
