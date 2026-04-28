---
name: support-stats-workflow
description: "Use when working on Support Stats setup, run-workflow usage, debugging workflow failures, managing GitHub internal team usernames, Jira team normalization/overrides, or extending workflow functionality. Keywords: run workflow, setup, fetch, generate, report, team members, GitHub usernames, jira overrides, normalization, deterministic caching."
---

# Support Stats Workflow Skill

## Goal
Help users operate and evolve this workflow safely and predictably.

The workflow is script-first. Prefer asking users to run scripts directly themselves whenever possible, and use agent edits only when code/config changes are required.

## Core Principles
- Deterministic: same inputs should produce same outputs.
- Configurable: user/account-specific values belong in config and prompts, not hardcoded logic.
- Reusable: shared helpers in scripts/common and modular entrypoints.
- Separation of concerns:
  - Setup/configuration
  - Data fetch/caching
  - Visualizations
  - Reporting
- Cache discipline: raw + manifest + derived consolidation with no manual interpretation.

## Preferred Entry Point
Default to the full workflow command:

```bash
python3 scripts/run-workflow.py
```

Use month-specific runs when needed:

```bash
python3 scripts/run-workflow.py --month YYYY-MM
```

## Operator-First Behavior
When assisting users:
1. Prefer guiding the user to run existing scripts/flags before proposing code changes.
2. If troubleshooting, reproduce with the smallest script scope first:
   - setup only
   - fetch only
   - generate only
   - report only
3. Only patch code when behavior is incorrect, unclear, or missing.
4. After code changes, validate with a real command and confirm outputs.
5. Do not create ad-hoc scripts unless the user explicitly asks for one or explicitly approves creating one.

## Output Discipline
Avoid generating files or artefacts unless they are actively consumed by another step in the workflow.
- Do not write intermediate files if the data can be passed in-memory between functions.
- Do not write debug/diagnostic output files by default; print summaries to stdout instead.
- Do not add new cache files, derived files, or report artefacts without a clear downstream consumer.
- When removing a feature or step, also remove any files it was writing — do not leave orphaned outputs.
- If a file exists only for human inspection (e.g. debugging), it should be opt-in (a flag), not default behaviour.
- Apply this principle to code changes too: do not add helpers, constants, or imports that are not used.

## Capability Map (Setup)
`scripts/setup.py` orchestrates capabilities.

1. GitHub minimal
- Discovers repositories using org + owner slug from catalog-info.yaml.

2. GitHub internal team
- Stores comma-separated internal GitHub usernames for split chart.

3. Jira minimal
- Validates Jira auth and writes Jira minimal config.
- Credentials are stored via keyring.
- If stored Jira credentials fail auth, clear and re-prompt.

4. Jira org structure
- Builds Jira org structure and team normalization scaffold.

5. Jira service normalization
- Seeds service aliases from configured owned repositories.
- Depends on GitHub minimal / owned-repositories being configured first.
- Writes user/account-specific generated config that should be cleaned by the clean workflow.
- Treat as non-deterministic setup output, not hand-maintained shared config.

## Team Name Normalization and Overrides (Authoritative Behavior)
Normalization is handled in `scripts/generate/jira_charts.py`.

Order of matching for requesting team values:
1. Overrides (`config/jira-team-overrides.json`) exact match, case-insensitive.
2. Alias map (`config/jira-team-normalization.json` -> team_aliases).
3. Exact case-insensitive match against known org team names.
4. Structural parsing (for values like `Area - Team`) using org structure.
5. Raw fallback (unmapped value retained).

Implications:
- Use overrides for curated corrections and exceptions.
- Use aliases for reusable/standardized mappings.
- Keep overrides small and intentional.
- Preserve deterministic precedence (do not reorder matching tiers unless explicitly required).
- Keep normalization inline within the current flow (no extra normalization stage), but format mapped team labels as `Parent - Team` (sub-area preferred, else area) via existing derived fields.

## Service Name Normalization
Service normalization is separate from team normalization because canonical services depend on the configured owned repository set.

Behavior:
1. Generated aliases come from `config/jira-service-normalization.json`.
2. Curated exceptions live in `config/jira-service-overrides.json`.
3. Generation is gated behind a setup capability and should only run once owned repositories exist.
4. Generated service normalization output is cleanable via the clean workflow.

Implications:
- Do not fold service aliases into the team normalization file.
- Do not treat generated service aliases as globally shared deterministic config.
- Keep the same precedence shape as team normalization: overrides, generated aliases, exact canonical match, raw fallback.

## GitHub Username Assistance
For "find GitHub usernames from engineer names":
- If GitHub MCP/tools are available, search users/org members first.
- Prefer exact profile matches and confirm ambiguity before updating team config.
- If tools are unavailable, ask user for a source list and produce a candidate mapping for review.
- Do not silently change `config/github-internal-team.json` without clear confirmation.

## Extending Functionality Safely
When adding features:
1. Place code in existing module structure:
   - setup/*
   - fetch/*
   - generate/*
   - workflow/*
   - common/*
2. Keep top-level entrypoints thin:
   - scripts/setup.py
   - scripts/fetch-data.py
   - scripts/generate-charts.py
   - scripts/generate-report.py
   - scripts/run-workflow.py
3. Reuse shared helpers from `scripts/common/setup_utils.py` where possible.
4. Keep CLI behavior explicit with argparse flags and clear defaults.
5. Update README command examples and requirements when behavior changes.

## Troubleshooting Playbook
- Auth failures:
  - GitHub: verify `gh auth status`.
  - Jira: rerun capability 3 and re-enter site/email/token when prompted.
- Missing data:
  - Check month-specific consolidated files in cache.
  - Re-run fetch for target month.
- Chart/report mismatches:
  - Regenerate charts first, then report.
- Cleaning concerns:
  - Ensure clean only removes generated outputs, not curated normalization/override files.

## Expected Response Style
- Give concrete script commands first.
- Keep recommendations deterministic and reversible.
- State what is user-provided vs workflow-managed.
- Call out assumptions and validation steps explicitly.
