# Copy-paste prompts

All prompts pin a released version (`v1.1.0`), so a later change to the repository can never
change what runs in your session. Update the tag when you want a newer release.

## A. New chat - Claude in the browser / app (code execution), repo on GitHub

```text
Before anything else: git clone --depth 1 --branch v1.1.0 https://github.com/muskec06/runbrief /tmp/runbrief
and follow /tmp/runbrief/AGENTS.md for this whole conversation (run its setup step).
If cloning fails, tell me and I will attach runbrief.py.
Task:
```

## B. A conversation that is already running

```text
From now on: git clone --depth 1 --branch v1.1.0 https://github.com/muskec06/runbrief /tmp/runbrief
(skip if /tmp/runbrief exists) and follow /tmp/runbrief/AGENTS.md for the rest of this
conversation. Start by writing HANDOFF.md for where we are now.
```
It saves tokens from this point on. What is already in the conversation stays until the chat
is compacted or you move to a new chat with HANDOFF.md (prompt A + "Continue from the attached
HANDOFF.md").

## C. Without GitHub (attach the files)

Attach `runbrief.py` and `AGENTS.md`, then:

```text
Use the attached runbrief.py and follow AGENTS.md for this whole conversation
(run its setup step first). Task:
```

## D. Claude skill (no prompt needed afterwards)

Upload `dist/runbrief-skill.zip` once: Customize > Skills > + > Create skill > Upload a skill
(code execution must be enabled). Then start chats with:

```text
Use the runbrief skill. Task:
```
A skill is re-loaded by its description whenever builds or tests come up, so it survives
long conversations better than a one-time prompt.

## E. Claude Code (CLI, desktop, web) - permanent per project

```bash
python3 integrations/claude-code/install.py /path/to/project
```
Commit the generated `.claude/` folder, `CLAUDE.md` additions and `.runbrief/patterns.txt`.
Hooks then enforce the rules in every session, also after `/compact`.

## F. Other agents (Cursor, Codex, Aider, Copilot agent, ...)

Copy `runbrief.py` into the repo (e.g. `tools/runbrief.py`) and append `AGENTS.md`
to the agent's rules file (`AGENTS.md`, `.cursorrules`, `CONVENTIONS.md`, ...).

## Moving a long conversation to a new one

Send this in the old conversation, then paste the result into the new one after prompt A or D:

```text
Write HANDOFF.md (max 40 lines): first line "Session uses runbrief", then goal, decisions made,
files changed, current error or state, next step. No code or logs. Send it as a file.
```
