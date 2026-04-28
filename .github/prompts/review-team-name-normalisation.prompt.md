---
description: "Review Jira team-name normalisation quality, propose minimal alias/override updates, and optionally apply approved config edits."
---
Review team-name normalisation for this workspace.

Scope:
- Analyze current Jira requesting team raw values from cache data.
- Compare against:
  - config/jira-team-normalization.json
  - config/jira-team-overrides.json
  - config/jira-org-structure.json
- Keep deterministic workflow behavior and precedence unchanged unless explicitly requested.

Goal:
- Critically evaluate mapping quality.
- Propose the smallest safe config changes to improve normalisation.
- Separate confident recommendations from ambiguous cases.

Process:
1. List unmapped or weakly mapped raw team values with counts.
2. Propose updates as either:
   - overrides (for exceptions/one-offs)
   - aliases (for reusable normalisation patterns)
3. Explain why each proposal belongs in overrides vs aliases.
4. Highlight ambiguous items and ask for confirmation instead of guessing.
5. If user confirms, apply only approved config edits.

Output format:
- Confident updates
- Ambiguous updates needing confirmation
- Exact patch preview by file
- Validation command to run next
