# AI Support Stats Rebuild

This rebuild is focused on deterministic, reusable script-based workflows with minimal LLM logic.

## Setup
1. Dependency checks
2. GitHub auth validation via `gh` (keychain-backed)
3. Jira auth validation using credentials stored via Python `keyring`
4. Team owner configuration prompt
5. Deterministic discovery of owner-tagged repositories from `catalog-info.yaml`

## Script

- `scripts/setup.py`

## Run

```bash
python3 scripts/setup.py
```

Optional non-interactive arguments:

```bash
python3 scripts/setup.py --org trainline-private --owner ecommerce
```

## Requirements

- Python 3.9+
- `gh` CLI installed and authenticated
- Python `keyring` package

If `gh` is missing and Homebrew is installed:

```bash
brew install gh
```

If Homebrew is not installed:

- Install Homebrew: https://brew.sh
- Or install GitHub CLI directly: https://cli.github.com

If `keyring` is missing:

```bash
python3 -m pip install keyring
```

`keyring` uses the native credential backend for your OS when available, such as macOS Keychain or Windows Credential Manager.

## Jira Credential Storage

The script stores Jira credentials under the `keyring` service:

- `ai-support-stats.jira`

Using these usernames:

- `ai-support-stats.jira.site` (example value: `trainline.atlassian.net`)
- `ai-support-stats.jira.email`
- `ai-support-stats.jira.api-token`

On first run, `scripts/setup.py` will prompt for any missing Jira credentials and store them automatically via `keyring`.

Create Jira API token at:

- https://id.atlassian.com/manage-profile/security/api-tokens

## Outputs

- `data/github/owned-repositories.json`

If the discovered list is wrong:

1. Edit `data/github/owned-repositories.json` directly (local override)
2. Or fix the `spec.owner` value in repository `catalog-info.yaml` files and re-run setup
