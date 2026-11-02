# Coding and terminal skill

Create files with `write_file` and run commands with `run_command`. All work
happens inside the task workspace.

Rules:

- Write code to files first, then run it; do not chain long one-liners.
- Read-only commands (ls, cat, git status, git log, git diff, python --version,
  node --version, pip list, npm ls) run automatically. Anything that installs,
  downloads packages, mutates files, or executes project code will ask the user
  for approval first — that is expected; proceed when approved.
- Never run destructive commands (sudo, rm -rf, disk or system tools); they are
  blocked, not approved.
- Install dependencies inside the workspace (e.g. `npm install --prefix .`,
  `python -m venv .venv`), never system-wide.
- After running tests or builds, read the actual output before claiming success;
  quote the relevant lines in the final answer.
- Finish with a list of created/changed files and the commands that were run.
