#!/usr/bin/env python3
"""Unified fetch dispatcher for all data sources."""

import argparse
import sys

from fetch.github_prs import main as github_main
from fetch.jira_tickets import main as jira_main


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Fetch data from supported sources.",
        usage="python3 scripts/fetch-data.py {github|jira} [source options]",
    )
    parser.add_argument(
        "source",
        choices=["github", "jira"],
        help="Which data source to fetch",
    )
    parser.add_argument(
        "source_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed through to the selected source fetcher",
    )
    args = parser.parse_args(argv)

    forwarded = args.source_args
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    if args.source == "github":
        github_main(forwarded)
        return

    if args.source == "jira":
        jira_main(forwarded)
        return

    parser.error(f"Unsupported source: {args.source}")


if __name__ == "__main__":
    main(sys.argv[1:])
