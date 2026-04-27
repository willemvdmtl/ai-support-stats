#!/usr/bin/env python3
"""Generate Jira team/area config from the tech org source-of-truth repository."""

import argparse
import base64
import datetime as dt
import json
import sys
import urllib.request
from collections import defaultdict
from typing import Dict, List, Tuple

from common.setup_utils import (
    DEFAULT_CAPABILITIES_OUTPUT,
    check_dependencies,
    check_github_auth,
    prompt_if_missing,
    read_json,
    update_capability_status,
    write_json,
)

DEFAULT_REPO_OWNER = "trainline-private"
DEFAULT_REPO_NAME = "github-tech-org-teams"
DEFAULT_REPO_PATH = "data/org-structure-2027.json"
DEFAULT_REPO_REF = "main"
DEFAULT_ORG_OUTPUT = "config/jira-org-structure.json"
DEFAULT_TEAM_NORM_OUTPUT = "config/jira-team-normalization.json"


def fetch_repo_file(owner: str, repo: str, path: str, ref: str, token: str) -> Tuple[Dict, Dict]:
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    req = urllib.request.Request(api_url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    with urllib.request.urlopen(req, timeout=30) as response:
        metadata = json.loads(response.read().decode("utf-8"))

    encoded = metadata.get("content", "")
    if not encoded:
        print("ERROR: Source file content is empty or unavailable.")
        sys.exit(1)

    decoded = base64.b64decode(encoded).decode("utf-8")
    return json.loads(decoded), metadata


def parse_entries(org_structure: Dict) -> Tuple[List[Dict], Dict[str, Dict], Dict[str, str]]:
    pillars = org_structure.get("pillars") or []
    areas_dataset: List[Dict] = []
    teams_map: Dict[str, Dict] = {}
    aliases: Dict[str, str] = {}

    for pillar_wrapper in pillars:
        pillar = pillar_wrapper.get("pillar") or {}
        pillar_name = str(pillar.get("name", "")).strip()
        pillar_slug = str(pillar.get("slug", "")).strip()

        for area_wrapper in pillar_wrapper.get("areas") or []:
            area = area_wrapper.get("area") or {}
            area_name = str(area.get("name", "")).strip()
            area_slug = str(area.get("slug", "")).strip()

            area_teams: List[Dict] = []
            teams_direct = area_wrapper.get("teams") or []
            sub_areas = area_wrapper.get("subAreas") or []

            for team in teams_direct:
                team_name = str(team.get("name", "")).strip()
                team_slug = str(team.get("slug", "")).strip()
                if not team_name or not team_slug:
                    continue
                entry = {
                    "team_name": team_name,
                    "team_slug": team_slug,
                    "sub_area_name": "",
                    "sub_area_slug": "",
                    "retired": bool(team.get("retired", False)),
                    "old_org_team": bool(team.get("old-org-team", False)),
                }
                area_teams.append(entry)
                teams_map[team_name] = {
                    "display_name": team_name,
                    "slug": team_slug,
                    "area": area_name,
                    "area_slug": area_slug,
                    "pillar": pillar_name,
                    "pillar_slug": pillar_slug,
                    "sub_area": "",
                    "sub_area_slug": "",
                    "retired": entry["retired"],
                    "old_org_team": entry["old_org_team"],
                }

            for sub_area_wrapper in sub_areas:
                sub_area = sub_area_wrapper.get("subArea") or {}
                sub_area_name = str(sub_area.get("name", "")).strip()
                sub_area_slug = str(sub_area.get("slug", "")).strip()
                for team in sub_area_wrapper.get("teams") or []:
                    team_name = str(team.get("name", "")).strip()
                    team_slug = str(team.get("slug", "")).strip()
                    if not team_name or not team_slug:
                        continue
                    entry = {
                        "team_name": team_name,
                        "team_slug": team_slug,
                        "sub_area_name": sub_area_name,
                        "sub_area_slug": sub_area_slug,
                        "retired": bool(team.get("retired", False)),
                        "old_org_team": bool(team.get("old-org-team", False)),
                    }
                    area_teams.append(entry)
                    teams_map[team_name] = {
                        "display_name": team_name,
                        "slug": team_slug,
                        "area": area_name,
                        "area_slug": area_slug,
                        "pillar": pillar_name,
                        "pillar_slug": pillar_slug,
                        "sub_area": sub_area_name,
                        "sub_area_slug": sub_area_slug,
                        "retired": entry["retired"],
                        "old_org_team": entry["old_org_team"],
                    }

            areas_dataset.append(
                {
                    "pillar_name": pillar_name,
                    "pillar_slug": pillar_slug,
                    "area_name": area_name,
                    "area_slug": area_slug,
                    "teams": sorted(area_teams, key=lambda item: item["team_name"].lower()),
                }
            )

    for canonical_team, details in teams_map.items():
        slug = details.get("slug", "")
        aliases[canonical_team.lower()] = canonical_team
        aliases[slug.lower()] = canonical_team
        aliases[canonical_team.lower().replace("&", "and")] = canonical_team
        aliases[canonical_team.lower().replace(" ", "-")] = canonical_team
        aliases[canonical_team.lower().replace(" ", "")] = canonical_team
        aliases[slug.lower().replace("-", "")] = canonical_team

    aliases = {k: v for k, v in aliases.items() if k}

    return sorted(areas_dataset, key=lambda item: item["area_name"].lower()), teams_map, aliases


def parse_fields(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Jira org/team config from source repo JSON.")
    parser.add_argument("--repo-owner", default=DEFAULT_REPO_OWNER, help="Source repo owner")
    parser.add_argument("--repo-name", default=DEFAULT_REPO_NAME, help="Source repo name")
    parser.add_argument("--repo-path", default=DEFAULT_REPO_PATH, help="Path to org structure JSON in source repo")
    parser.add_argument("--repo-ref", default=DEFAULT_REPO_REF, help="Source branch/tag/SHA")
    parser.add_argument("--output", default=DEFAULT_ORG_OUTPUT, help="Output path for Jira org structure config")
    parser.add_argument(
        "--team-normalization-output",
        default=DEFAULT_TEAM_NORM_OUTPUT,
        help="Output path for Jira team normalization config",
    )
    parser.add_argument(
        "--requesting-team-fields",
        default="",
        help="Comma-separated Jira field IDs used to read requesting team values",
    )
    parser.add_argument(
        "--capabilities-output",
        default=DEFAULT_CAPABILITIES_OUTPUT,
        help="Capabilities status file path",
    )
    args = parser.parse_args()

    print("Step 1/4: Checking dependencies...")
    check_dependencies(require_gh=True)
    print("OK: dependencies available.")

    print("\nStep 2/4: Validating GitHub auth...")
    ok, token, login_or_err = check_github_auth()
    if not ok:
        print("ERROR: GitHub auth is not ready.")
        print(f"Reason: {login_or_err}")
        print("Fix: run `gh auth login` and try again.")
        sys.exit(1)
    print(f"OK: GitHub auth works as user: {login_or_err}")

    owner = prompt_if_missing(args.repo_owner.strip(), f"Source repo owner [{DEFAULT_REPO_OWNER}]: ", DEFAULT_REPO_OWNER)
    repo = prompt_if_missing(args.repo_name.strip(), f"Source repo name [{DEFAULT_REPO_NAME}]: ", DEFAULT_REPO_NAME)
    path = prompt_if_missing(args.repo_path.strip(), f"Source file path [{DEFAULT_REPO_PATH}]: ", DEFAULT_REPO_PATH)
    ref = prompt_if_missing(args.repo_ref.strip(), f"Source ref [{DEFAULT_REPO_REF}]: ", DEFAULT_REPO_REF)

    print("\nStep 3/4: Fetching and deriving org dataset...")
    try:
        source_json, metadata = fetch_repo_file(owner, repo, path, ref, token)
    except Exception as exc:
        print("ERROR: Failed to fetch source org structure JSON.")
        print(f"Reason: {exc}")
        print("Fix: verify repo/path/ref and your GitHub access, then re-run capability 4.")
        sys.exit(1)

    areas_dataset, teams_map, aliases = parse_entries(source_json)

    org_payload = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "source": {
            "repo": f"{owner}/{repo}",
            "path": path,
            "ref": ref,
            "sha": metadata.get("sha", ""),
            "html_url": metadata.get("html_url", ""),
        },
        "summary": {
            "area_count": len(areas_dataset),
            "team_count": len(teams_map),
            "retired_team_count": sum(1 for _, details in teams_map.items() if details.get("retired")),
        },
        "areas": areas_dataset,
        "teams": dict(sorted(teams_map.items(), key=lambda item: item[0].lower())),
    }
    write_json(args.output, org_payload)

    existing_norm = read_json(args.team_normalization_output)
    requesting_fields = parse_fields(args.requesting_team_fields)
    if not requesting_fields:
        existing_fields = existing_norm.get("requesting_team_fields") if isinstance(existing_norm, dict) else []
        if isinstance(existing_fields, list):
            requesting_fields = [str(item).strip() for item in existing_fields if str(item).strip()]

    team_norm_payload = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "source": {
            "repo": f"{owner}/{repo}",
            "path": path,
            "ref": ref,
            "sha": metadata.get("sha", ""),
        },
        "requesting_team_fields": requesting_fields,
        "team_aliases": dict(sorted(aliases.items(), key=lambda item: item[0])),
    }
    write_json(args.team_normalization_output, team_norm_payload)

    update_capability_status(
        "jira_org_structure",
        True,
        {
            "source_repo": f"{owner}/{repo}",
            "source_path": path,
            "source_ref": ref,
            "source_sha": metadata.get("sha", ""),
            "output": args.output,
            "team_normalization_output": args.team_normalization_output,
            "area_count": len(areas_dataset),
            "team_count": len(teams_map),
        },
        output_path=args.capabilities_output,
    )

    print(f"OK: derived org structure written to {args.output}")
    print(f"OK: team normalization scaffold written to {args.team_normalization_output}")
    print(f"Areas: {len(areas_dataset)} | Teams: {len(teams_map)}")

    print("\nStep 4/4: Next actions")
    if not requesting_fields:
        print("- Add Jira requesting team field IDs to config/jira-team-normalization.json > requesting_team_fields")
    print("- Run: python3 scripts/generate-charts.py jira --month YYYY-MM")
    print("- If needed, refine aliases under config/jira-team-normalization.json > team_aliases")


if __name__ == "__main__":
    main()
