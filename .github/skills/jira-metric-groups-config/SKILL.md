---
name: jira-metric-groups-config
description: "Use when adding, editing, validating, or explaining Jira metric group policy in config/jira-metric-groups.json, including group labels, include/exclude/force filters, rolling window rules, and cycle-time validity constraints."
---

# Jira Metric Groups Config Skill

## Goal
Safely update `config/jira-metric-groups.json` so reporting behavior stays deterministic and auditable.

## Scope
Use this skill for:
- Adding or renaming metric groups
- Editing include/exclude/force filters by issue type and labels
- Adjusting rolling-vs-month window policy
- Adjusting cycle-time validity constraints

Do not use this skill for:
- Fetch pipeline changes
- Jira auth/setup troubleshooting
- Chart rendering logic

## Required Structure
The config must include:
- `version`
- `defaults.event_anchor` (currently `resolved_at`)
- `defaults.window_policy`
- `defaults.validity`
- `groups[]` with `id`, `label`, and `filter`

## Deterministic Rules
- Normalize comparisons case-insensitively in code.
- Group selection precedence:
  1. include_any (if present)
  2. exclude_any
  3. force_include_any
- Cycle time is computed as `resolutiondate - created` for tickets anchored in the active time window.
- Invalid tickets (missing timestamps or negative duration) are excluded when validity flags require it.

## Safe Edit Workflow
1. Read `config/jira-metric-groups.json`.
2. Apply only requested edits.
3. Preserve unrelated groups and defaults.
4. Validate JSON syntax and required keys.
5. Summarize the change in plain language.

## Example Group
```json
{
  "id": "vertical_support",
  "label": "Vertical Support",
  "filter": {
    "include_any": {
      "issue_types": ["PR Request", "ExternalRequest"],
      "labels": ["vertical support"]
    }
  }
}
```
