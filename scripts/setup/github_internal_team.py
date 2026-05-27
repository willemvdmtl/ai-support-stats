#!/usr/bin/env python3
"""Configure internal GitHub users for internal vs external PR breakdowns."""

import argparse
import datetime as dt
import sys

from common.setup_utils import prompt_if_missing, read_json, update_capability_status, write_json


DEFAULT_OUTPUT = "config/github-internal-team.json"


def parse_users(raw: str):
    users = []
    seen = set()
    for part in raw.split(","):
        login = part.strip()
        if not login:
            continue
        key = login.lower()
        if key in seen:
            continue
        users.append(login)
        seen.add(key)
    return users


def parse_teams(raw: str):
    teams = {}
    for block in raw.split(";"):
        part = block.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        team_name, users_raw = part.split(":", 1)
        team = team_name.strip()
        users = parse_users(users_raw)
        if team and users:
            teams[team] = users
    return teams


def flatten_team_users(teams):
    users = []
    seen = set()
    for team_users in teams.values():
        for login in team_users:
            key = login.lower()
            if key in seen:
                continue
            users.append(login)
            seen.add(key)
    return users


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", default="")
    parser.add_argument(
        "--teams",
        default="",
        help="Semicolon-separated team mappings: Team A:user1,user2;Team B:user3",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    existing = read_json(args.output)
    existing_users = existing.get("internal_github_users") or []
    existing_teams = existing.get("internal_teams") or {}
    existing_raw = ", ".join(existing_users)

    print("Configure internal GitHub users for PR author classification.")
    if existing_teams:
        print(f"Current teams ({len(existing_teams)}):")
        for team_name, team_users in existing_teams.items():
            print(f"  - {team_name}: {', '.join(team_users)}")
    if existing_users:
        print(f"Current users ({len(existing_users)}): {existing_raw}")

    teams = {}
    users = []

    if args.teams.strip():
        teams = parse_teams(args.teams)
        users = flatten_team_users(teams)
    elif args.users.strip():
        users = parse_users(args.users)
    else:
        teams_prompt = (
            "Optional team mapping (Team A:user1,user2;Team B:user3). "
            "Leave blank to use single-list mode"
        )
        entered_teams = prompt_if_missing("", f"{teams_prompt}: ")
        if entered_teams.strip():
            teams = parse_teams(entered_teams)
            users = flatten_team_users(teams)
        else:
            prompt_suffix = ""
            if existing_users:
                prompt_suffix = " (leave blank to keep current list)"
            entered = prompt_if_missing("", f"Enter internal GitHub usernames as comma-separated values{prompt_suffix}: ")
            if not entered and existing_users:
                users = existing_users
                teams = existing_teams if isinstance(existing_teams, dict) else {}
            else:
                users = parse_users(entered)

    if not users:
        print("ERROR: At least one internal GitHub username is required.")
        print("You can re-run with: --users user1,user2")
        print("Or with teams: --teams 'Team A:user1,user2;Team B:user3'")
        sys.exit(1)

    payload = {
        "configured_at": dt.datetime.utcnow().isoformat() + "Z",
        "internal_github_users": users,
    }
    if teams:
        payload["internal_teams"] = teams
    write_json(args.output, payload)

    update_capability_status(
        "github_internal_team",
        True,
        {
            "user_count": len(users),
            "team_count": len(teams),
            "config_file": args.output,
        },
    )

    print("\nInternal GitHub team configuration saved.")
    if teams:
        print(f"Teams configured: {len(teams)}")
    print(f"Users configured: {len(users)}")
    print(f"Output file: {args.output}")


if __name__ == "__main__":
    main()
