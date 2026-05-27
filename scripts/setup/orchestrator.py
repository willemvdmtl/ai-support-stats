#!/usr/bin/env python3
"""Capability orchestrator for setup workflows."""

import argparse
import os
import subprocess
import sys
from typing import Dict, List

from common.setup_utils import check_python_requirements


CAPABILITIES: List[Dict[str, str]] = [
    {
        "id": "1",
        "name": "GitHub minimal (repo discovery for PR stats)",
        "script": os.path.join("setup", "github_minimal.py"),
    },
    {
        "id": "2",
        "name": "GitHub internal team (internal vs external PR split)",
        "script": os.path.join("setup", "github_internal_team.py"),
    },
    {
        "id": "3",
        "name": "Jira minimal (all-issue fetch + Vertical Support filters)",
        "script": os.path.join("setup", "jira_minimal.py"),
    },
    {
        "id": "4",
        "name": "Jira org structure (areas/teams for team heatmap)",
        "script": os.path.join("setup", "jira_org_structure.py"),
    },
    {
        "id": "5",
        "name": "Jira service normalization (service name aliases seeded from owned repos)",
        "script": os.path.join("setup", "jira_service_normalization.py"),
    },
]


def parse_selection(raw: str) -> List[Dict[str, str]]:
    normalized = raw.strip().lower()
    if not normalized or normalized == "all":
        return CAPABILITIES

    tokens = [item.strip() for item in raw.split(",") if item.strip()]
    selected: List[Dict[str, str]] = []
    seen = set()
    by_id = {cap["id"]: cap for cap in CAPABILITIES}

    for token in tokens:
        if token not in by_id:
            print(f"ERROR: Unknown capability number '{token}'.")
            print("Valid options: " + ", ".join(cap["id"] for cap in CAPABILITIES))
            sys.exit(1)
        if token in seen:
            continue
        selected.append(by_id[token])
        seen.add(token)

    if not selected:
        print("ERROR: No capabilities selected.")
        sys.exit(1)

    return selected


def build_capability_args(capability_id: str, args: argparse.Namespace) -> List[str]:
    forwarded: List[str] = []

    if capability_id == "1":
        if args.org.strip():
            forwarded.extend(["--org", args.org.strip()])
        if args.owner.strip():
            forwarded.extend(["--owner", args.owner.strip()])
        if args.github_output.strip():
            forwarded.extend(["--output", args.github_output.strip()])
        if args.github_config_output.strip():
            forwarded.extend(["--config-output", args.github_config_output.strip()])

    if capability_id == "2":
        if args.internal_teams.strip():
            forwarded.extend(["--teams", args.internal_teams.strip()])
        if args.internal_users.strip():
            forwarded.extend(["--users", args.internal_users.strip()])
        if args.internal_team_output.strip():
            forwarded.extend(["--output", args.internal_team_output.strip()])

    if capability_id == "3":
        if args.jira_output.strip():
            forwarded.extend(["--output", args.jira_output.strip()])
        if args.jira_site_service.strip():
            forwarded.extend(["--jira-site-service", args.jira_site_service.strip()])
        if args.jira_email_service.strip():
            forwarded.extend(["--jira-email-service", args.jira_email_service.strip()])
        if args.jira_token_service.strip():
            forwarded.extend(["--jira-token-service", args.jira_token_service.strip()])
        if args.jira_project.strip():
            forwarded.extend(["--project", args.jira_project.strip()])
        if args.jira_vertical_issue_types.strip():
            forwarded.extend(["--vertical-support-issue-types", args.jira_vertical_issue_types.strip()])
        elif args.jira_issue_types.strip():
            forwarded.extend(["--issue-types", args.jira_issue_types.strip()])
        if args.jira_vertical_tags.strip():
            forwarded.extend(["--vertical-support-tags", args.jira_vertical_tags.strip()])
        if args.jira_metric_groups_output.strip():
            forwarded.extend(["--metric-groups-output", args.jira_metric_groups_output.strip()])

    if capability_id == "4":
        if args.org_repo_owner.strip():
            forwarded.extend(["--repo-owner", args.org_repo_owner.strip()])
        if args.org_repo_name.strip():
            forwarded.extend(["--repo-name", args.org_repo_name.strip()])
        if args.org_repo_path.strip():
            forwarded.extend(["--repo-path", args.org_repo_path.strip()])
        if args.org_repo_ref.strip():
            forwarded.extend(["--repo-ref", args.org_repo_ref.strip()])
        if args.jira_org_output.strip():
            forwarded.extend(["--output", args.jira_org_output.strip()])
        if args.jira_team_norm_output.strip():
            forwarded.extend(["--team-normalization-output", args.jira_team_norm_output.strip()])
        if args.jira_requesting_team_fields.strip():
            forwarded.extend(["--requesting-team-fields", args.jira_requesting_team_fields.strip()])

    if capability_id == "5":
        if args.service_norm_repos_input.strip():
            forwarded.extend(["--repos-input", args.service_norm_repos_input.strip()])
        if args.service_norm_output.strip():
            forwarded.extend(["--output", args.service_norm_output.strip()])

    return forwarded


def run_capability(script_dir: str, capability: Dict[str, str], args: argparse.Namespace) -> None:
    script_path = os.path.join(script_dir, capability["script"])
    cmd = [sys.executable, script_path] + build_capability_args(capability["id"], args)
    env = os.environ.copy()
    pythonpath_parts = [script_dir]
    if env.get("PYTHONPATH"):
        pythonpath_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    print(f"\n=== Running {capability['id']}: {capability['name']} ===")

    result = subprocess.run(cmd, check=False, env=env)
    if result.returncode != 0:
        print(f"ERROR: Capability {capability['id']} failed with exit code {result.returncode}.")
        sys.exit(result.returncode)

    print(f"OK: Capability {capability['id']} completed.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Run all capability setup scripts")
    parser.add_argument(
        "--capabilities",
        default="",
        help="Comma-separated capability numbers (e.g. 1,3). Use 'all' for all capabilities.",
    )
    parser.add_argument("--list", action="store_true", help="List available setup capabilities")

    parser.add_argument("--org", default="", help="GitHub organization for capability 1")
    parser.add_argument(
        "--owner",
        default="",
        help="catalog-info owner value(s) for capability 1 (comma-separated)",
    )
    parser.add_argument("--github-output", default="", help="Override output file for capability 1")
    parser.add_argument(
        "--github-config-output",
        default="",
        help="Override capability config output file for capability 1",
    )

    parser.add_argument(
        "--internal-teams",
        default="",
        help="Team mapping for capability 2: Team A:user1,user2;Team B:user3",
    )
    parser.add_argument(
        "--internal-users",
        default="",
        help="Comma-separated internal GitHub usernames for capability 2",
    )
    parser.add_argument(
        "--internal-team-output",
        default="",
        help="Override output file for capability 2",
    )

    parser.add_argument("--jira-output", default="", help="Override output file for capability 3")
    parser.add_argument(
        "--jira-site-service",
        default="",
        help="Credential username key for Jira site value (capability 3)",
    )
    parser.add_argument(
        "--jira-email-service",
        default="",
        help="Credential username key for Jira email value (capability 3)",
    )
    parser.add_argument(
        "--jira-token-service",
        default="",
        help="Credential username key for Jira API token value (capability 3)",
    )
    parser.add_argument("--jira-project", default="", help="Jira project key for capability 3")
    parser.add_argument(
        "--jira-vertical-issue-types",
        default="",
        help="Comma-separated Jira issue types that define Vertical Support for capability 3",
    )
    parser.add_argument(
        "--jira-vertical-tags",
        default="",
        help="Comma-separated Jira labels/tags that define Vertical Support for capability 3",
    )
    parser.add_argument(
        "--jira-issue-types",
        default="",
        help="Deprecated alias for --jira-vertical-issue-types",
    )
    parser.add_argument(
        "--jira-metric-groups-output",
        default="",
        help="Override Jira metric group config output file for capability 3",
    )

    parser.add_argument(
        "--org-repo-owner",
        default="",
        help="GitHub repo owner containing org structure JSON for capability 4",
    )
    parser.add_argument(
        "--org-repo-name",
        default="",
        help="GitHub repo name containing org structure JSON for capability 4",
    )
    parser.add_argument(
        "--org-repo-path",
        default="",
        help="Path to org structure JSON file in source repo for capability 4",
    )
    parser.add_argument(
        "--org-repo-ref",
        default="",
        help="Git reference (branch/tag/SHA) for source repo file in capability 4",
    )
    parser.add_argument(
        "--jira-org-output",
        default="",
        help="Override Jira org structure output file for capability 4",
    )
    parser.add_argument(
        "--jira-team-norm-output",
        default="",
        help="Override Jira team normalization output file for capability 4",
    )
    parser.add_argument(
        "--jira-requesting-team-fields",
        default="",
        help="Comma-separated Jira field IDs used to read requesting team values for capability 4",
    )
    parser.add_argument(
        "--service-norm-repos-input",
        default="",
        help="Override owned repositories input file for capability 5",
    )
    parser.add_argument(
        "--service-norm-output",
        default="",
        help="Override service normalization output file for capability 5",
    )
    args = parser.parse_args()

    if args.list:
        print("Available setup capabilities:")
        for capability in CAPABILITIES:
            print(f"  {capability['id']}) {capability['name']}")
        return

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    requirements_path = os.path.join(os.path.dirname(script_dir), "requirements.txt")
    check_python_requirements(requirements_path)

    if args.all:
        selected = CAPABILITIES
    elif args.capabilities.strip():
        selected = parse_selection(args.capabilities)
    else:
        print("Select setup capabilities to run:")
        for capability in CAPABILITIES:
            print(f"  {capability['id']}) {capability['name']}")
        entered = input("Enter comma-separated numbers, or 'all' [all]: ").strip()
        selected = parse_selection(entered or "all")

    print("\nSelected capabilities:")
    for capability in selected:
        print(f"  {capability['id']}) {capability['name']}")

    for capability in selected:
        run_capability(script_dir, capability, args)

    print("\nSetup orchestration complete.")


if __name__ == "__main__":
    main()
