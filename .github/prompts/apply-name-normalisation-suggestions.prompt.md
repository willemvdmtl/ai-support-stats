---
description: "Apply user-approved team/service name normalisation suggestions to config files only, with a minimal and auditable diff."
---
Apply approved name-normalisation suggestions in this workspace.

Guardrails:
- Apply only suggestions explicitly approved in this chat.
- Keep edits minimal and limited to config files unless user asked for logic changes.
- Do not alter precedence in scripts/generate/jira_charts.py unless explicitly approved.
- Preserve deterministic workflow behavior.

Files you may update:
- config/jira-team-overrides.json
- config/jira-team-normalization.json
- config/jira-service-overrides.json
- config/jira-service-normalization.json

Process:
1. Restate the approved suggestions as a checklist.
2. Apply only those items.
3. Show an exact diff summary per file.
4. Run relevant validation command(s) and report outcome.

Output format:
- Applied changes
- Validation results
- Any skipped suggestions and reason
