#!/usr/bin/env python3
"""Remove generated outputs to restore a clean first-run state."""

import argparse
import os
import shutil
import sys
from typing import List, Tuple

CACHE_TARGETS: List[str] = [
    "cache/github",
    "cache/jira",
]

# Only auto-generated config files are cleaned.
# Hand-maintained files (jira-team-normalization.json, jira-team-overrides.json)
# are intentionally excluded.
CONFIG_TARGETS: List[str] = [
    "config/capabilities.json",
    "config/owned-repositories.json",
    "config/github-minimal.json",
    "config/github-internal-team.json",
    "config/jira-minimal.json",
    "config/jira-org-structure.json",
    "config/jira-service-normalization.json",
    "data/config",
]


def collect_paths(targets: List[str]) -> List[Tuple[str, bool]]:
    return [(t, os.path.exists(t)) for t in targets]


def remove_path(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.isfile(path):
        os.remove(path)


def remove_parent_if_empty(path: str) -> None:
    parent = os.path.dirname(path)
    if parent and os.path.isdir(parent) and not os.listdir(parent):
        os.rmdir(parent)


def run_clean(targets: List[str], yes: bool) -> None:
    paths = collect_paths(targets)
    present = [(p, e) for p, e in paths if e]

    if not present:
        print("Nothing to clean.")
        return

    print("Will remove:")
    for path, _ in present:
        print(f"  {path}/")

    if not yes:
        answer = input("\nProceed? [y/N]: ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(0)

    for path, _ in present:
        remove_path(path)
        remove_parent_if_empty(path)
        print(f"  removed  {path}/")

    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove generated outputs.")
    parser.add_argument("--cache", action="store_true", help="Remove cache/ outputs")
    parser.add_argument("--config", action="store_true", help="Remove config/ outputs")
    parser.add_argument("--all", action="store_true", help="Remove all generated outputs")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if not any([args.cache, args.config, args.all]):
        parser.print_help()
        sys.exit(0)

    targets: List[str] = []
    if args.cache or args.all:
        targets.extend(CACHE_TARGETS)
    if args.config or args.all:
        targets.extend(CONFIG_TARGETS)

    run_clean(targets, yes=args.yes)


if __name__ == "__main__":
    main()
