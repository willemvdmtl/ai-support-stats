#!/usr/bin/env python3
"""Configure minimal GitHub setup for basic PR statistics."""

import argparse
import datetime as dt
import sys
from typing import Dict, List

from common.setup_utils import (
    DEFAULT_GITHUB_OUTPUT,
    check_dependencies,
    check_github_auth,
    github_search_code,
    prompt_if_missing,
    update_capability_status,
    write_json,
)


def parse_owner_values(raw: str) -> List[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def discover_repositories(token: str, org: str, owners: List[str]) -> List[str]:
    by_repo: Dict[str, List[str]] = {}

    for owner in owners:
        query = f'org:{org} filename:catalog-info.yaml "owner: {owner}"'
        print(f"Running GitHub code search: {query}")
        try:
            items = github_search_code(token, query)
        except Exception as exc:
            print(f"ERROR: GitHub discovery call failed for owner '{owner}': {exc}")
            sys.exit(1)

        for item in items:
            repo = (item.get("repository") or {}).get("full_name")
            path = item.get("path") or "catalog-info.yaml"
            if not repo:
                continue
            by_repo.setdefault(repo, [])
            if path not in by_repo[repo]:
                by_repo[repo].append(path)

    return sorted(by_repo.keys())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", default="")
    parser.add_argument("--owner", default="")
    parser.add_argument("--output", default=DEFAULT_GITHUB_OUTPUT)
    parser.add_argument("--config-output", default="config/github-minimal.json")
    args = parser.parse_args()

    print("Step 1/3: Checking dependencies...")
    check_dependencies(require_gh=True)
    print("OK: dependencies available.")

    print("\nStep 2/3: Validating GitHub auth via keychain-backed gh CLI...")
    github_ok, github_token, github_login_or_error = check_github_auth()
    if not github_ok:
        print("ERROR: GitHub auth check failed.")
        print(f"Reason: {github_login_or_error}")
        print("\nTo fix:")
        print("  gh auth login")
        print("  gh auth status")
        sys.exit(1)
    print(f"OK: GitHub auth works as user: {github_login_or_error}")

    print("\nStep 3/3: Team configuration and repository discovery...")
    org = prompt_if_missing(
        args.org.strip(),
        "Enter GitHub organization (e.g. trainline-private) [trainline-private]: ",
        default="trainline-private",
    )
    owner_input = prompt_if_missing(
        args.owner.strip(),
        "Enter catalog-info owner value(s), comma-separated (e.g. ecommerce,checkout): ",
    )
    owners = parse_owner_values(owner_input)

    if not org or not owners:
        print("ERROR: org and at least one owner value are required.")
        sys.exit(1)

    repositories = discover_repositories(github_token, org, owners)
    search_queries = [f'org:{org} filename:catalog-info.yaml "owner: {owner}"' for owner in owners]

    output_payload = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "organization": org,
        "owner_values": owners,
        "owner_value": ",".join(owners),
        "search_queries": search_queries,
        "search_query": " OR ".join(search_queries),
        "repositories": repositories,
    }
    write_json(args.output, output_payload)

    config_payload = {
        "configured_at": dt.datetime.utcnow().isoformat() + "Z",
        "organization": org,
        "owner_values": owners,
        "owner_value": ",".join(owners),
        "owned_repositories_file": args.output,
    }
    write_json(args.config_output, config_payload)

    update_capability_status(
        "github_minimal",
        True,
        {
            "organization": org,
            "owner_values": owners,
            "repositories_found": len(repositories),
            "config_file": args.config_output,
        },
    )

    print("\nDiscovery complete.")
    print(f"Owned repositories found: {len(repositories)}")
    print(f"Output file: {args.output}")
    print(f"Capability config: {args.config_output}")


if __name__ == "__main__":
    main()
