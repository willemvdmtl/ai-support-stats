#!/usr/bin/env python3
"""Shared helpers for setup, fetch, and generate scripts."""

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

JIRA_CREDENTIAL_SERVICE = "ai-support-stats.jira"
DEFAULT_GITHUB_OUTPUT = "config/owned-repositories.json"
DEFAULT_CAPABILITIES_OUTPUT = "config/capabilities.json"


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


def check_dependencies(require_gh: bool = False, require_keyring: bool = False) -> None:
    missing: List[str] = []
    if require_gh and not command_exists("gh"):
        missing.append("gh")
    if require_keyring and keyring is None:
        missing.append("keyring")

    if missing:
        print("ERROR: Missing required dependencies:")
        for item in missing:
            print(f"  - {item}")
        print_dependency_guidance(missing)
        sys.exit(1)


def prompt_if_missing(value: str, prompt_text: str, default: str = "") -> str:
    if value:
        return value
    entered = input(prompt_text).strip()
    if entered:
        return entered
    return default


def prompt_for_secret(prompt_text: str) -> str:
    return getpass.getpass(prompt_text).strip()


def read_json(path: str) -> Dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def check_github_auth() -> Tuple[bool, str, str]:
    token_proc = run(["gh", "auth", "token"], check=False)
    if token_proc.returncode != 0 or not token_proc.stdout.strip():
        return False, "", "Unable to retrieve token via `gh auth token`."

    user_proc = run(["gh", "api", "user", "--jq", ".login"], check=False)
    if user_proc.returncode != 0 or not user_proc.stdout.strip():
        return False, "", "Unable to call `gh api user` with current auth state."

    return True, token_proc.stdout.strip(), user_proc.stdout.strip()


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


def credential_get(service: str, username: str) -> str:
    if keyring is None:
        return ""
    try:
        value = keyring.get_password(service, username)
    except KeyringError:
        return ""
    return (value or "").strip()


def credential_set(service: str, username: str, value: str) -> bool:
    if keyring is None:
        return False
    try:
        keyring.set_password(service, username, value)
        return True
    except KeyringError:
        return False


def credential_delete(service: str, username: str) -> None:
    if keyring is None:
        return
    try:
        keyring.delete_password(service, username)
    except KeyringError:
        pass


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
        label
        for label, value in [
            ("site", jira_site),
            ("email", jira_email),
            ("api token", jira_token),
        ]
        if not value
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


def update_capability_status(
    capability_name: str,
    configured: bool,
    details: Dict = None,
    output_path: str = DEFAULT_CAPABILITIES_OUTPUT,
) -> None:
    payload = read_json(output_path)
    if not payload:
        payload = {"capabilities": {}}

    capabilities = payload.setdefault("capabilities", {})
    entry = {
        "configured": configured,
        "updated_at": dt.datetime.utcnow().isoformat() + "Z",
    }
    if details:
        entry["details"] = details
    capabilities[capability_name] = entry

    write_json(output_path, payload)
