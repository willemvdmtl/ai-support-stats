# Support Stats Workspace Instructions

## Safety Guardrails

- Never run destructive cleanup commands without explicit user confirmation in the current chat.
- Treat these commands as destructive and confirm first:
  - `python3 scripts/clean.py --all`
  - `python3 scripts/clean.py --config`
  - `python3 scripts/clean.py --cache`
  - Any command that deletes or recursively removes project data/config (for example `rm`, `rmtree`).
- Before running any of the above, clearly state what will be removed and ask for confirmation.
- If not explicitly confirmed, do not execute the command.

## Workflow Preference

- Prefer guiding users to run the workflow scripts directly:
  - `python3 scripts/run-workflow.py`
- Use script-first operation over ad-hoc/manual actions whenever possible.
- Do not create new ad-hoc scripts unless the user explicitly requests it or explicitly approves it.
- When adding new functionality, follow existing workflow patterns and principles:
  - configurable
  - reusable
  - deterministic
  - clear step separation (setup, fetch, generate, report)
