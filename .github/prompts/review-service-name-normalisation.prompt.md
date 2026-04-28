---
description: "Review Jira service-name normalisation quality, propose minimal alias/override updates, and optionally apply approved config edits."
---
Review service-name normalisation for this workspace.

Scope:
- Analyze service values used by the Jira Service Heatmap from cache data.
- Compare against:
  - config/jira-service-normalization.json
  - config/jira-service-overrides.json
  - config/owned-repositories.json
- Preserve precedence and behavior in scripts/generate/jira_charts.py unless explicitly requested.

Goal:
- Critically evaluate mapping quality and identify variant collisions.
- Propose minimal updates to service aliases/overrides.
- Avoid broad or risky remaps without confirmation.

Process:
1. List raw service values with counts and current resolved canonical value.
2. Identify unresolved or weakly resolved variants.
3. Propose updates as either:
   - overrides (abbreviations, local exceptions)
   - generated alias amendments (if justified and safe)
4. Keep deterministic seeded output intact where possible.
5. Ask confirmation for ambiguous cases before editing.
6. If user confirms, apply only approved updates.

Output format:
- Confident updates
- Ambiguous updates needing confirmation
- Exact patch preview by file
- Validation command to run next
