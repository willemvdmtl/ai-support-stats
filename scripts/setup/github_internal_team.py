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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", default="")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    existing = read_json(args.output)
    existing_users = existing.get("internal_github_users") or []
    existing_raw = ", ".join(existing_users)

    print("Configure internal GitHub users for PR author classification.")
    if existing_users:
        print(f"Current users ({len(existing_users)}): {existing_raw}")

    if args.users.strip():
        users = parse_users(args.users)
    else:
        prompt_suffix = ""
        if existing_users:
            prompt_suffix = " (leave blank to keep current list)"
        entered = prompt_if_missing("", f"Enter internal GitHub usernames as comma-separated values{prompt_suffix}: ")
        if not entered and existing_users:
            users = existing_users
        else:
            users = parse_users(entered)

    if not users:
        print("ERROR: At least one internal GitHub username is required.")
        print("You can re-run with: --users user1,user2")
        sys.exit(1)

    payload = {
        "configured_at": dt.datetime.utcnow().isoformat() + "Z",
        "internal_github_users": users,
    }
    write_json(args.output, payload)

    update_capability_status(
        "github_internal_team",
        True,
        {
            "user_count": len(users),
            "config_file": args.output,
        },
    )

    print("\nInternal GitHub team configuration saved.")
    print(f"Users configured: {len(users)}")
    print(f"Output file: {args.output}")


if __name__ == "__main__":
    main()
