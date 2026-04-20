#!/usr/bin/env python3
"""
Setup phase:
1. Check dependencies
2. Validate GitHub and Jira auth using OS-agnostic credential storage
3. Prompt for team owner/org configuration
4. Discover repositories via catalog-info.yaml owner value
5. Write reusable config output
"""

import argparse
import base64
import datetime as dt
import getpass
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from typing import Dict, List, Tuple

try:
    import keyring
    from keyring.errors import KeyringError
except ImportError:
    keyring = None
    KeyringError = Exception


DEFAULT_OUTPUT = "data/github/owned-repositories.json"
JIRA_CREDENTIAL_SERVICE = "ai-support-stats.jira"


def run(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def print_dependency_guidance(missing: List[str]) -> None:
    print("\nInstall guidance:")

    if "gh" in missing:
        if command_exists("brew"):
            print("  GitHub CLI:")
            print("    brew install gh")
        else:
            print("  GitHub CLI:")
            print("    Install Homebrew first: https://brew.sh")
            print("    Or install GitHub CLI directly: https://cli.github.com")

    if "keyring" in missing:
        print("  Python keyring package:")
        print("    python3 -m pip install keyring")
        print("  Docs:")
        print("    https://pypi.org/project/keyring/")


def check_dependencies() -> None:
    required = ["gh"]
    missing = [cmd for cmd in required if not command_exists(cmd)]
    if keyring is None:
        missing.append("keyring")
    if missing:
        print("ERROR: Missing required dependencies:")
        for item in missing:
            print(f"  - {item}")
        print_dependency_guidance(missing)
        sys.exit(1)


def credential_get(service: str, username: str) -> str:
    try:
        value = keyring.get_password(service, username)
    except KeyringError:
        return ""
    return (value or "").strip()


def credential_set(service: str, username: str, value: str) -> bool:
    try:
        keyring.set_password(service, username, value)
        return True
    except KeyringError:
        return False


def check_github_auth() -> Tuple[bool, str, str]:
    token_proc = run(["gh", "auth", "token"], check=False)
    if token_proc.returncode != 0 or not token_proc.stdout.strip():
        return False, "", "Unable to retrieve token via `gh auth token`."

    user_proc = run(["gh", "api", "user", "--jq", ".login"], check=False)
    if user_proc.returncode != 0 or not user_proc.stdout.strip():
        return False, "", "Unable to call `gh api user` with current auth state."

    return True, token_proc.stdout.strip(), user_proc.stdout.strip()


def check_jira_auth(jira_site: str, jira_email: str, jira_api_token: str) -> Tuple[bool, Dict, str]:
    if not jira_site.startswith("http://") and not jira_site.startswith("https://"):
        jira_site = f"https://{jira_site}"

    url = jira_site.rstrip("/") + "/rest/api/3/myself"
    basic_auth = base64.b64encode(f"{jira_email}:{jira_api_token}".encode("utf-8")).decode("utf-8")

    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("Authorization", f"Basic {basic_auth}")

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return True, payload, ""
    except Exception as exc:
        return False, {}, str(exc)


def prompt_for_secret(prompt_text: str) -> str:
    return getpass.getpass(prompt_text).strip()


def ensure_jira_credentials(site_name: str, email_name: str, token_name: str) -> Tuple[str, str, str]:
    jira_site = credential_get(JIRA_CREDENTIAL_SERVICE, site_name)
    jira_email = credential_get(JIRA_CREDENTIAL_SERVICE, email_name)
    jira_token = credential_get(JIRA_CREDENTIAL_SERVICE, token_name)

    if jira_site and jira_email and jira_token:
        return jira_site, jira_email, jira_token

    print("\nJira credentials are missing from the system credential store.")
    print("They will be stored using Python keyring, which uses the native credential backend for your OS.")
    print("Create Jira API token at:")
    print("  https://id.atlassian.com/manage-profile/security/api-tokens")

    if not jira_site:
        jira_site = input("Jira site (e.g. trainline.atlassian.net): ").strip()
    if not jira_email:
        jira_email = input("Jira email: ").strip()
    if not jira_token:
        jira_token = prompt_for_secret("Jira API token: ")

    missing = [
        label for label, value in [
            ("site", jira_site),
            ("email", jira_email),
            ("api token", jira_token),
        ] if not value
    ]
    if missing:
        print("ERROR: Missing Jira credential values: " + ", ".join(missing))
        sys.exit(1)

    writes = [
        credential_set(JIRA_CREDENTIAL_SERVICE, site_name, jira_site),
        credential_set(JIRA_CREDENTIAL_SERVICE, email_name, jira_email),
        credential_set(JIRA_CREDENTIAL_SERVICE, token_name, jira_token),
    ]
    if not all(writes):
        print("ERROR: Unable to write Jira credentials to the system credential store via keyring.")
        print("Check your OS credential backend and try again.")
        sys.exit(1)

    return jira_site, jira_email, jira_token


def prompt_if_missing(value: str, prompt_text: str) -> str:
    if value:
        return value
    return input(prompt_text).strip()


def github_search_code(token: str, query: str) -> List[Dict]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    all_items: List[Dict] = []
    page = 1
    while True:
        params = urllib.parse.urlencode({"q": query, "per_page": 100, "page": page})
        url = f"https://api.github.com/search/code?{params}"
        req = urllib.request.Request(url, headers=headers)

        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        items = payload.get("items", [])
        if not items:
            break

        all_items.extend(items)
        if len(items) < 100:
            break
        page += 1

    return all_items


def write_json(path: str, data: Dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", default="")
    parser.add_argument("--owner", default="")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--jira-site-service", default="ai-support-stats.jira.site")
    parser.add_argument("--jira-email-service", default="ai-support-stats.jira.email")
    parser.add_argument("--jira-token-service", default="ai-support-stats.jira.api-token")
    args = parser.parse_args()

    print("Step 1/4: Checking dependencies...")
    check_dependencies()
    print("OK: dependencies available.")

    print("\nStep 2/4: Validating GitHub auth via keychain-backed gh CLI...")
    github_ok, github_token, github_login_or_error = check_github_auth()
    if not github_ok:
        print("ERROR: GitHub auth check failed.")
        print(f"Reason: {github_login_or_error}")
        print("\nTo fix:")
        print("  gh auth login")
        print("  gh auth status")
        sys.exit(1)
    print(f"OK: GitHub auth works as user: {github_login_or_error}")

    print("\nStep 3/4: Validating Jira auth via the system credential store...")
    jira_site, jira_email, jira_token = ensure_jira_credentials(
        args.jira_site_service,
        args.jira_email_service,
        args.jira_token_service,
    )

    jira_ok, jira_payload, jira_error = check_jira_auth(jira_site, jira_email, jira_token)
    if not jira_ok:
        print("ERROR: Jira auth check failed.")
        print(f"Reason: {jira_error}")
        print("\nCheck that these stored values are valid and match the same Atlassian account:")
        print(f"  service: {JIRA_CREDENTIAL_SERVICE}, username: {args.jira_site_service}")
        print(f"  service: {JIRA_CREDENTIAL_SERVICE}, username: {args.jira_email_service}")
        print(f"  service: {JIRA_CREDENTIAL_SERVICE}, username: {args.jira_token_service}")
        sys.exit(1)
    print(f"OK: Jira auth works as user: {jira_payload.get('displayName', 'unknown')}")

    print("\nStep 4/4: Team configuration and repository discovery...")
    org = prompt_if_missing(args.org.strip(), "Enter GitHub organization (e.g. trainline-private): ")
    owner = prompt_if_missing(args.owner.strip(), "Enter exact catalog-info owner value (e.g. ecommerce): ")

    if not org or not owner:
        print("ERROR: org and owner are required.")
        sys.exit(1)

    query = f'org:{org} filename:catalog-info.yaml "owner: {owner}"'
    print(f"Running GitHub code search: {query}")
    try:
        items = github_search_code(github_token, query)
    except Exception as exc:
        print(f"ERROR: GitHub discovery call failed: {exc}")
        sys.exit(1)

    by_repo: Dict[str, List[str]] = {}
    for item in items:
        repo = (item.get("repository") or {}).get("full_name")
        path = item.get("path") or "catalog-info.yaml"
        if not repo:
            continue
        by_repo.setdefault(repo, [])
        if path not in by_repo[repo]:
            by_repo[repo].append(path)

    repositories = sorted(by_repo.keys())

    output_payload = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "organization": org,
        "owner_value": owner,
        "search_query": query,
        "discovered_repository_count": len(repositories),
        "repositories": repositories,
        "repository_catalog_matches": [
            {"repository": repo, "paths": sorted(by_repo[repo])}
            for repo in repositories
        ],
    }

    write_json(args.output, output_payload)

    print("\nDiscovery complete.")
    print(f"Owned repositories found: {len(repositories)}")
    print(f"Output file: {args.output}")

    if repositories:
        print("\nRepositories:")
        for repo in repositories:
            print(f"  - {repo}")

    print("\nIf anything is wrong:")
    print(f"  1) Edit {args.output} directly for a local override")
    print("  2) Or fix `spec.owner` in the relevant repository catalog-info.yaml files and re-run")
    print(f"  3) Re-run with: python3 scripts/setup.py --org {org} --owner {owner}")


if __name__ == "__main__":
    main()
