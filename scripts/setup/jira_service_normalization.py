#!/usr/bin/env python3
"""Seed Jira service name normalization from owned repositories."""

import argparse
import datetime as dt
import json
import os
import sys
from typing import Dict, List

from common.setup_utils import (
    DEFAULT_CAPABILITIES_OUTPUT,
    update_capability_status,
    write_json,
)

DEFAULT_OWNED_REPOS_PATH = "config/owned-repositories.json"
DEFAULT_SERVICE_NORM_OUTPUT = "config/jira-service-normalization.json"


def read_json(path: str) -> Dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def repo_name(full_name: str) -> str:
    """Strip org prefix from 'org/RepoName' -> 'RepoName'."""
    return full_name.split("/", 1)[-1]


def seed_aliases(canonical_names: List[str]) -> Dict[str, str]:
    """
    Build a service_aliases dict seeded from canonical repo names.

    Each canonical name (PascalCase) is pre-registered under common
    variant keys so that minor differences in Jira field values
    (case, spacing, abbreviation) resolve to the canonical form.

    Only mechanical variants are added here (lowercase, no-spaces).
    Human-curated corrections go in jira-service-overrides.json.
    """
    aliases: Dict[str, str] = {}
    for name in canonical_names:
        lower = name.lower()
        nospace = lower.replace(" ", "").replace("-", "").replace("_", "")
        # Register the lowercase variant (covers exact-case-insensitive matches
        # that aren't already caught by case-insensitive lookup in the chart code)
        if lower != name:
            aliases[lower] = name
        # Register the no-punctuation/no-space squashed variant
        if nospace != lower:
            aliases[nospace] = name
    return aliases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos-input", default=DEFAULT_OWNED_REPOS_PATH)
    parser.add_argument("--output", default=DEFAULT_SERVICE_NORM_OUTPUT)
    parser.add_argument(
        "--capabilities-output", default=DEFAULT_CAPABILITIES_OUTPUT
    )
    args = parser.parse_args()

    print("Step 1/3: Reading owned repositories...")
    repos_data = read_json(args.repos_input)
    if not repos_data.get("repositories"):
        print(f"ERROR: No repositories found in {args.repos_input}.")
        print("Fix: Run capability 1 (GitHub minimal) first.")
        sys.exit(1)

    canonical_names = sorted(
        {repo_name(r) for r in repos_data["repositories"]},
        key=str.lower,
    )
    print(f"OK: {len(canonical_names)} canonical service names from owned repos.")

    print("\nStep 2/3: Seeding service aliases...")
    # Preserve any existing human overrides (service_aliases key) if the file exists
    existing = read_json(args.output)
    existing_aliases = existing.get("service_aliases") or {}

    seeded = seed_aliases(canonical_names)
    # Merge: existing (curated) aliases take precedence over seeded ones
    merged_aliases = {**seeded, **existing_aliases}

    payload = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "source": args.repos_input,
        "canonical_services": canonical_names,
        "service_aliases": merged_aliases,
    }
    write_json(args.output, payload)
    print(f"OK: Wrote {args.output} ({len(canonical_names)} canonical, {len(merged_aliases)} aliases).")

    print("\nStep 3/3: Updating capability status...")
    update_capability_status(
        capability_name="jira_service_normalization",
        configured=True,
        details={
            "canonical_service_count": len(canonical_names),
            "alias_count": len(merged_aliases),
            "source": args.repos_input,
            "output": args.output,
        },
        output_path=args.capabilities_output,
    )
    print("OK: Capability status updated.")
    print("\nDone. Service normalization is ready.")


if __name__ == "__main__":
    main()
