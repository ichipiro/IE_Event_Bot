# AGENTS.md

## Purpose

- This file is the first-run guide for AI assistants working in this repository.
- Optimize for safe, minimal, verifiable changes.
- Prefer repository-local context over assumptions.

## Workspace Boundary

- Do not access files outside the WSL workspace unless the user gives a clear, explicit instruction.
- Even when the user explicitly asks to access files outside WSL, stop and ask for confirmation before doing so.
- Treat Windows-side paths, mounted drives, home directories outside the current Linux workspace, cloud-sync folders, and GUI-opened files as out of bounds unless the user has both requested them and confirmed access.
- If the task can be completed fully inside `/home/products/Git_Products/IE/IE_Event_Bot_fork/IE_Event_Bot_fork`, stay inside it.

## Runtime

- Primary runtime is Linux / WSL.
- Preferred Python environment is `.venv` at the repository root.
- Use the Linux virtual environment for Python work in this repository.
- Do not replace Linux commands with Windows-only instructions unless the user explicitly asks for Windows steps.
- Main application target is Cloudflare Python Workers under `workers/`.

## Setup

- Activate the Linux virtual environment:

```bash
source .venv/bin/activate
```

- Create it if needed:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip install -r workers/requirements.txt
```

## Project Structure

- `workers/src/entry.py`: Worker entrypoint and route handling.
- `workers/src/google_calendar_sync.py`: Google Calendar delta fetch.
- `workers/src/google_apply_sync.py`: apply Google changes to Notion and Discord.
- `workers/src/discord_notion_sync.py`: Discord to Notion sync and Discord to Google path.
- `workers/src/google_auth.py`: Google token resolution and caching.
- `workers/src/google_watch.py`: Google watch lifecycle.
- `workers/src/jobs.py`: periodic jobs.
- `workers/src/state.py`: KV-backed state handling.
- `workers/wrangler.jsonc`: Cloudflare Workers config.
- `README_ENV.md`: Python packages expected in the local venv.

## Rules

- Prefer small, targeted edits.
- Preserve existing behavior unless the task explicitly changes it.
- Do not delete user files, KV-related docs, or generated artifacts unless the user asks.
- Keep environment and setup instructions aligned with verified Linux / WSL behavior.
- When changing dependencies, update `README_ENV.md` if the venv contents should change too.
- When touching Worker behavior, verify the effect against `workers/wrangler.jsonc` and the relevant route or job entrypoint.

## Verification

- Basic dependency check:

```bash
source .venv/bin/activate
python - <<'PY'
import rsa
import google.auth
print("imports ok")
PY
```

- Lint:

```bash
source .venv/bin/activate
ruff check .
```

- Type check:

```bash
source .venv/bin/activate
pyright
```

- Tests:

```bash
source .venv/bin/activate
pytest -q
```

## Known Pitfalls

- This repository should be handled as a Linux / WSL workspace, not a Windows-native one.
- Paths and shell commands should be written for WSL unless the user explicitly wants Windows guidance.
- Cloudflare runtime behavior depends on bindings in `workers/wrangler.jsonc`, so code-only changes may still require config awareness.
- `README_ENV.md` is intentionally limited to Python packages that belong in the repo venv.
